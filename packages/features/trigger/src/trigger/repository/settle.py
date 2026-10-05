"""F08b 完了検知（投下ファイルが**書き終わってから**拾う）。

**監視から独立した部品**（SD-07）—— `PollingObserver` は「見つけたパス」を渡すだけで、安定判定は
本ファイルの約 1 秒間隔の `stat` ループが行う。これにより `watcher_enabled=false`（監視停止）でも
退避経路 `POST /hooks/file` の完了検知が成立する（S-4・FR-039・FR-040・SC-008）。

**`fire` を直接呼ばない** —— 完了時に呼ぶ callback を注入で受ける（`repository` が `service` を
import しない形を型でも保つ）。配線は `service/__init__.py` が行う。

**読むだけ**である。投下ファイルを書き換えない・動かさない・消さない（S-5・FR-038・D06-h）。
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Literal

from common.kernel.log import LoggerFactory
from trigger.models import FileEvent

if TYPE_CHECKING:
    from collections.abc import Callable

SETTLE_POLL_SECONDS = 1.0
"""S-1: 完了検知の `stat` の間隔（秒）。

**`poll_interval_sec`（CFG-65・既定 5.0）とは別**である —— `poll_interval` で回すと
`stable_seconds`（2）が**実質 5 秒へ丸められる**（実測 M-03・M-08）。config キーにしないのは
新しいキーが `x3_config_catalog.csv`（変更禁止）への行新設を要するためで、調整の自由度は
`stable_seconds`（CFG-66）が持つ（plan C-6）。
"""

RECENT_TTL_FACTOR = 10
"""S-3: 重複抑止の保持は `stable_seconds × 10`（既定 2 秒 → **20 秒**）。"""

_logger = LoggerFactory.get_logger("trigger.repository.settle")

_Key = tuple[str, str, int, int]
"""重複抑止の鍵（`trigger_id`・実パス・`st_size`・`st_mtime_ns`）＝ **4 つ組**。"""


class _RecentFires:
    """直近に発火した 4 つ組（**プロセス内メモリだけ**。再起動で消える＝ FR-013）。

    S-7: 保留の除去（SD-25）とは役割が違う —— 保留の除去は「同じイベントを 2 度処理しない」ため、
    こちらは「**同じ投下が 2 つの入口（監視・通知）から別々に入った**」ときに 1 回にするためである。
    """

    def __init__(self, ttl_seconds: float) -> None:
        """保持時間を受け取る。"""
        self._ttl = ttl_seconds
        self._seen: dict[_Key, float] = {}

    def seen_recently(self, key: _Key, now: float) -> bool:
        """`key` が保持時間の内に記録済みか。期限切れはその場で捨てる。"""
        self._prune(now)
        return key in self._seen

    def record(self, key: _Key, now: float) -> None:
        """`key` を記録する。"""
        self._seen[key] = now

    def _prune(self, now: float) -> None:
        expired = [key for key, at in self._seen.items() if now - at > self._ttl]
        for key in expired:
            del self._seen[key]


class SettleWorker:
    """完了検知の専用スレッド 1 本（F08b）。"""

    def __init__(
        self,
        on_complete: Callable[[str, str], None],
        *,
        stable_seconds: int,
        poll_seconds: float = SETTLE_POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """完了時の呼び先と閾値を受け取る。

        Args:
            on_complete: 完了とみなしたときに `(trigger_id, realpath)` で呼ぶ callback。
                `service` が `fire(kind=file, dropped_path=…)` を配線する。
            stable_seconds: `(size, mtime)` が動かなくなってから完了とみなすまでの秒数（CFG-66）。
            poll_seconds: `stat` の間隔。**既定 `SETTLE_POLL_SECONDS`**（S-1。`poll_interval_sec`
                を渡さない）。
            clock: 単調時計（テストが差し替える）。
        """
        self._on_complete = on_complete
        self._stable_seconds = stable_seconds
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._pending: dict[tuple[str, str], FileEvent] = {}
        self._recent = _RecentFires(stable_seconds * RECENT_TTL_FACTOR)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def poll_seconds(self) -> float:
        """`stat` の間隔（S-1 の観測点）。"""
        return self._poll_seconds

    @property
    def pending_count(self) -> int:
        """保留中の `FileEvent` の件数（S-6 の観測点）。"""
        with self._lock:
            return len(self._pending)

    def submit(self, trigger_id: str, path: str, source: Literal["watchdog", "hook"]) -> None:
        """検知したパスを保留に載せる（**安定判定はここではしない**）。

        Args:
            trigger_id: どの `file` トリガーが拾ったか（重複抑止の鍵の一部＝ FR-042a）。
            path: 投下ファイルのパス（`os.path.realpath` で解決してから載せる）。
            source: 入口の別（`watchdog`／`hook`）。**`kind` は両方とも `file`** に畳む。
        """
        real = os.path.realpath(path)
        if not os.path.exists(real):
            return
        with self._lock:
            key = (trigger_id, real)
            if key in self._pending:
                return
            self._pending[key] = FileEvent(trigger_id, real, source, self._clock())

    def start(self) -> None:
        """走査スレッドを 1 本起こす（起動列の段 5）。

        **`watcher_enabled` と独立に起こす**（S-4。`false` でも退避経路の完了検知を担う）。
        """
        if self._thread is not None:
            return
        self._stop.clear()
        thread = threading.Thread(target=self._loop, name="trigger-settle", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """走査スレッドを止めて `join` する（停止列の段 3 の後半）。

        起動していなければ何もしない（起動中断の片付けから呼ばれる＝ C-5）。

        ⚠ **`join` に timeout を置かない**（T092）—— 完了検知スレッドは callback の中で
        `fire()` →`KickoffRunner.run` を**同期で**回すので、timeout を置くと `file` 入口の
        **進行中 kickoff を打ち切って返る**（実運用の kickoff は LLM を回すので分単位であり、
        既定の `SETTLE_POLL_SECONDS=1.0` から導く 15 秒では常に踏む）。lifespan が閉じて
        プロセスが終われば daemon スレッドは実行中のまま消え、履歴の行は `started` のまま
        残る。段 1 の `scheduler.shutdown(wait=True)`・段 2 の `worker.shutdown(wait=True)` と
        **揃えて待ち切る**（`timer`・`manual`・`hooks` は待つのに `file` だけ待たない非対称を
        残さない）＝ FR-033 段 3・SC-017・US1 AC6・N-6・contracts/fire-pipeline.md §6。
        イベントループを塞ぐのは承知のうえである（C-4）。

        `self._thread` は **`join` が終わってから**落とす —— 先に落とすと `is_alive()` が
        スレッドの走行中に `False` を返し、打ち切ったことが口からもログからも読めない。
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join()
        self._thread = None

    def is_alive(self) -> bool:
        """走査スレッドが生きているか。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def poll_once(self) -> None:
        """保留を 1 周走査する（スレッドが `poll_seconds` 間隔で呼ぶ。テストは直接呼ぶ）。"""
        now = self._clock()
        with self._lock:
            events = list(self._pending.values())
        for event in events:
            self._inspect(event, now)

    def _loop(self) -> None:
        """走査スレッドの本体。"""
        while not self._stop.wait(self._poll_seconds):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001  # 走査を止めない（次の投下を拾えなくならない）
                _logger.exception("settle_scan_failed")

    def _inspect(self, event: FileEvent, now: float) -> None:
        """1 件を `stat` して、変化・完了・消失を振り分ける。"""
        try:
            stat = os.stat(event.path)
        except OSError:
            # 置かれてすぐ消されたファイル。**保留から落とす**（S-2）。
            self._drop(event)
            return
        if (stat.st_size, stat.st_mtime_ns) != (event.last_size, event.last_mtime):
            # **サイズと mtime の両方**を見る（mtime だけでは足りない＝ DIFF-14・S-2）。
            event.last_size = stat.st_size
            event.last_mtime = stat.st_mtime_ns
            event.stable_since = now
            return
        if now - event.stable_since < self._stable_seconds:
            return
        self._complete(event, stat.st_size, stat.st_mtime_ns, now)

    def _complete(self, event: FileEvent, size: int, mtime: int, now: float) -> None:
        """完了とみなした 1 件を処理する（S-6・S-3 の順）。"""
        # ① **保留から落とす**（発火する・しないに関わらず必ず除去する＝ SD-25）。落とさないと
        #    `stat` ループが同じエントリを見続け、TTL が切れた瞬間に同じファイルで再発火する。
        self._drop(event)
        key: _Key = (event.trigger_id, event.path, size, mtime)
        with self._lock:
            if self._recent.seen_recently(key, now):
                _logger.info(
                    "file_settle_duplicate",
                    extra={"trigger_id": event.trigger_id, "path": event.path},
                )
                return
            self._recent.record(key, now)
        try:
            self._on_complete(event.trigger_id, event.path)
        except Exception:  # noqa: BLE001  # 完了 callback（fire）が落ちても走査は続ける
            _logger.exception(
                "file_settle_callback_failed",
                extra={"trigger_id": event.trigger_id, "path": event.path},
            )

    def _drop(self, event: FileEvent) -> None:
        """保留から外す。"""
        with self._lock:
            self._pending.pop(event.key, None)
