"""F08a ファイル監視（`PollingObserver` の起動停止と生死）。

**唯一の `watchdog` import 点**である（`.importlinter` の `scheduler-scope` が機械検査する）。

ハンドラは**パスと `trigger_id` の組を渡すだけ**で、安定判定はしない（SD-07）—— 完了検知は
`settle.py` の独立部品が担う。この分離により `watcher_enabled=false` でも退避経路
（`POST /hooks/file`）の完了検知が成立する。

生死は `observer.is_alive()` を読むだけで、**自分では復旧しない**（憲章 §10・N-7・FR-042）。
systemd の再起動に委ねる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from common.kernel.log import LoggerFactory
from trigger.models import WatcherEntry

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from watchdog.observers.api import ObservedWatch

_logger = LoggerFactory.get_logger("trigger.repository.watcher")


class _Handler(FileSystemEventHandler):
    """1 件の `file` トリガーに対するハンドラ。**パスを渡すだけ**（SD-07）。"""

    def __init__(self, trigger_id: str, submit: Callable[[str, str], None]) -> None:
        """どのトリガーのために拾うかと、渡し先を受け取る。"""
        super().__init__()
        self._trigger_id = trigger_id
        self._submit = submit

    def on_created(self, event: FileSystemEvent) -> None:
        """新規作成を拾う。"""
        self._offer(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        """更新を拾う（分割書き込みの途中でも来る。安定判定は完了検知の仕事）。"""
        self._offer(event)

    def _offer(self, event: FileSystemEvent) -> None:
        """ディレクトリの事象を捨て、パスを完了検知へ渡す。"""
        if event.is_directory:
            return
        path = event.src_path
        self._submit(self._trigger_id, path if isinstance(path, str) else path.decode())


class WatcherRepository:
    """`PollingObserver` を **1 つ**持ち、`file` トリガーごとに `schedule` する（F08a）。"""

    def __init__(
        self,
        submit: Callable[[str, str], None],
        *,
        poll_interval_sec: float,
        enabled: bool,
    ) -> None:
        """渡し先と設定を受け取る（**まだスレッドを起こさない**）。

        Args:
            submit: 拾ったパスの渡し先（`SettleWorker.submit` を配線する）。
            poll_interval_sec: `PollingObserver(timeout=…)`（CFG-65。実測 M-02・M-08 で 5.0）。
            enabled: `watcher_enabled`（CFG-67）。`False` なら **observer を作らない**
                （縮退＝ P5D-042・FR-039）。
        """
        self._submit = submit
        self._poll_interval_sec = poll_interval_sec
        self._enabled = enabled
        self._observer = PollingObserver(timeout=poll_interval_sec)
        self._watches: dict[str, tuple[str, ObservedWatch, _Handler]] = {}
        # **handler も持つ**（T091）。`remove()` が trigger 単位で外すために要る。
        self._started = False

    def start(self, entries: Sequence[tuple[str, str]]) -> None:
        """監視を起こす（起動列の段 6）。

        Args:
            entries: `(trigger_id, watch_dir)` の並び（`watch_dir` は解決済みの実パス）。
        """
        if not self._enabled:
            # 縮退。`WatcherState.watchers` は空配列になる（FR-039）。完了検知は独立に動く（S-4）。
            _logger.info("watcher_disabled")
            return
        for trigger_id, watch_dir in entries:
            self._schedule(trigger_id, watch_dir)
        self._observer.start()
        self._started = True

    def add(self, trigger_id: str, watch_dir: str) -> None:
        """1 件を監視に足す（保存が常駐へ即時に反映される＝ FR-028）。"""
        if not self._enabled:
            return
        self._schedule(trigger_id, watch_dir)

    def remove(self, trigger_id: str) -> None:
        """1 件の監視を止める（削除・無効化。**次回以降だけを止める**＝ FR-029）。

        ⚠ **`unschedule()` を単独で呼ばない**（T091）—— `ObservedWatch` は
        `(path, recursive, event_filter)` が等しければ**同一物**であり、`unschedule()` は
        `del self._handlers[watch]` で**その watch に紐づく handler を全部**落とす
        （`watchdog 6.0.0` の `BaseObserver.unschedule`）。同じ `watch_dir` を複数の `file`
        トリガーが見ているとき、1 件の削除・無効化・編集（`DELETE`・`PATCH`・`PUT` はいずれも
        `_deactivate()` を通る）で**残りのトリガーが黙って拾わなくなる**（FR-029・FR-042a (c)・
        SC-022）。trigger 単位で handler だけを外し、**その watch を見るトリガーが 0 件に
        なったときだけ** `unschedule()` して emitter を畳む。
        """
        entry = self._watches.pop(trigger_id, None)
        if entry is None:
            return
        _watch_dir, watch, handler = entry
        try:
            self._observer.remove_handler_for_watch(handler, watch)
            if not any(other == watch for _dir, other, _handler in self._watches.values()):
                self._observer.unschedule(watch)
        except (KeyError, RuntimeError) as exc:  # 既に止まっている observer への解除
            _logger.warning(
                "watcher_unschedule_failed", extra={"trigger_id": trigger_id, "error": str(exc)}
            )

    def stop(self) -> None:
        """監視を止める（停止列の段 3 の前半）。まだ起こしていなければ何もしない（C-5）。"""
        self._watches.clear()
        if not self._started:
            return
        self._started = False
        self._observer.stop()
        self._observer.join(timeout=self._poll_interval_sec * 10 + 5)

    def is_alive(self) -> bool:
        """Observer のスレッドが生きているか（**復旧はしない**）。"""
        return self._enabled and self._observer.is_alive()

    def state(self) -> tuple[WatcherEntry, ...]:
        """`WatcherState.watchers` に載せる一覧（`GET /watcher` の表示用）。

        Returns:
            登録順の `WatcherEntry`。`watcher_enabled=false` なら**空**（FR-039）。
            死んでも一覧からは消えない（`alive=False` を**見せる**のが仕事＝ N-7）。
        """
        alive = self.is_alive()
        return tuple(
            WatcherEntry(trigger_id=trigger_id, watch_dir=watch_dir, alive=alive)
            for trigger_id, (watch_dir, _watch, _handler) in self._watches.items()
        )

    def _schedule(self, trigger_id: str, watch_dir: str) -> None:
        """1 件を `schedule` する（**非再帰**＝設計の主図・実測 M-01）。"""
        if trigger_id in self._watches:
            return
        handler = _Handler(trigger_id, self._submit)
        watch = self._observer.schedule(handler, watch_dir, recursive=False)
        self._watches[trigger_id] = (watch_dir, watch, handler)
