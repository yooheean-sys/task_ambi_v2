"""F08a ファイル監視（`repository/watcher.py`）。**`PollingObserver` の実物**を回す（spec A-06）。

⚠ **実運用の DL フォルダ（Windows 側の共有）に 1 バイトも触れない**（P-2・spec A-05）。監視対象は
`tmp_path`（ext4）だけである。実運用の DL フォルダを監視するテストは作らない。

観測点は contracts/fire-pipeline.md §5 の S-5（読むだけ）と plan (iv)。
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from trigger.repository.settle import SettleWorker
from trigger.repository.watcher import WatcherRepository

if TYPE_CHECKING:
    from pathlib import Path

POLL_INTERVAL = 0.2
"""テストでの `PollingObserver(timeout=…)`。実運用の既定は 5.0（CFG-65）。"""

STABLE_SECONDS = 1
DEADLINE = 15.0


class _Submits:
    """`SettleWorker.submit` の差し替え（observer が「パスを渡すだけ」であることを見る）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def __call__(self, trigger_id: str, path: str) -> None:
        with self._lock:
            self.calls.append((trigger_id, path))

    def snapshot(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self.calls)


def _wait_until(predicate: object, deadline: float = DEADLINE) -> bool:
    """`predicate()` が真になるまで待つ（真になったら `True`）。"""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():  # pyright: ignore[reportCallIssue]  # テスト内の無引数述語
            return True
        time.sleep(0.05)
    return False


def test_a_file_written_in_chunks_fires_once_after_it_is_complete(downloads_dir: Path) -> None:
    """SC-006: 分割して書いている最中は発火 0・**書き終わってから 1 回**。

    observer と完了検知の**両方を実物**で回す（observer が拾い、完了検知が安定を待つ）。
    """
    fires: list[tuple[str, str]] = []
    settle = SettleWorker(
        lambda trigger_id, path: fires.append((trigger_id, path)),
        stable_seconds=STABLE_SECONDS,
    )
    watcher = WatcherRepository(
        lambda trigger_id, path: settle.submit(trigger_id, path, "watchdog"),
        poll_interval_sec=POLL_INTERVAL,
        enabled=True,
    )
    settle.start()
    watcher.start([("t1", str(downloads_dir))])
    try:
        target = downloads_dir / "large.bin"
        for _ in range(5):
            with target.open("ab") as stream:
                stream.write(b"x" * 200_000)
                stream.flush()
            time.sleep(0.4)
        assert fires == [], f"書いている最中に発火した: {fires}"

        assert _wait_until(lambda: len(fires) >= 1), "書き終わっても発火しない"
        time.sleep(STABLE_SECONDS + 1.5)
    finally:
        watcher.stop()
        settle.stop()

    assert len(fires) == 1, f"1 投下で複数回発火した: {fires}"
    assert fires[0][0] == "t1"


def test_the_dropped_file_is_never_consumed_or_moved(downloads_dir: Path) -> None:
    """SC-007・US2 AC3・D06-h: 発火の後も**パス・大きさ・更新時刻が発火前と同一**。"""
    fires: list[tuple[str, str]] = []
    settle = SettleWorker(
        lambda trigger_id, path: fires.append((trigger_id, path)),
        stable_seconds=STABLE_SECONDS,
    )
    watcher = WatcherRepository(
        lambda trigger_id, path: settle.submit(trigger_id, path, "watchdog"),
        poll_interval_sec=POLL_INTERVAL,
        enabled=True,
    )
    settle.start()
    watcher.start([("t1", str(downloads_dir))])
    try:
        target = downloads_dir / "keepme.bin"
        target.write_bytes(b"payload" * 1000)
        before = target.stat()

        assert _wait_until(lambda: len(fires) >= 1), "発火しない"
    finally:
        watcher.stop()
        settle.stop()

    after = target.stat()
    assert target.exists(), "投下ファイルが消えた"
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert [item.name for item in downloads_dir.iterdir()] == ["keepme.bin"]


def test_the_watch_is_not_recursive(downloads_dir: Path) -> None:
    """`schedule(..., recursive=False)`（設計の主図が非再帰・実測 M-01 も非再帰）。"""
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=True)
    nested = downloads_dir / "sub"
    nested.mkdir()
    watcher.start([("t1", str(downloads_dir))])
    try:
        (nested / "deep.bin").write_bytes(b"payload")
        time.sleep(POLL_INTERVAL * 6)
        deep = [call for call in submits.snapshot() if "deep.bin" in call[1]]
        assert deep == [], f"再帰的に拾っている: {deep}"

        (downloads_dir / "flat.bin").write_bytes(b"payload")
        assert _wait_until(lambda: any("flat.bin" in call[1] for call in submits.snapshot())), (
            "直下のファイルを拾えていない"
        )
    finally:
        watcher.stop()


def test_two_triggers_on_the_same_folder_each_get_one_submit(downloads_dir: Path) -> None:
    """FR-042a (c): 同じ `watch_dir` を 2 件の `file` トリガーが見ると**件数だけ**渡る。"""
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=True)
    watcher.start([("t1", str(downloads_dir)), ("t2", str(downloads_dir))])
    try:
        (downloads_dir / "shared.bin").write_bytes(b"payload")

        assert _wait_until(
            lambda: (
                {call[0] for call in submits.snapshot() if "shared.bin" in call[1]} == {"t1", "t2"}
            )
        ), f"両方のトリガーに渡っていない: {submits.snapshot()}"
    finally:
        watcher.stop()

    assert {entry.trigger_id for entry in watcher.state()} == set()


def test_removing_one_of_two_triggers_on_the_same_folder_keeps_the_other_alive(
    downloads_dir: Path,
) -> None:
    """T091・FR-029・FR-042a (c)・SC-022: 1 件を外しても**残る 1 件は拾い続ける**。

    ⚠ `ObservedWatch` は `(path, recursive, event_filter)` が等しければ**同一物**で、
    `unschedule()` は `del self._handlers[watch]` で**その watch の handler を全部**落とす
    （`watchdog 6.0.0`）。trigger 単位（`remove_handler_for_watch`）で外さないと、`t1` の
    削除・無効化・編集で `t2` が**黙って**拾わなくなる。

    ⚠ **`test_two_triggers_on_the_same_folder_each_get_one_submit` は削除を見ない**ので、
    どちらの実装でも緑になる（本テストが唯一の観測点である）。
    """
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=True)
    watcher.start([("t1", str(downloads_dir)), ("t2", str(downloads_dir))])
    try:
        (downloads_dir / "before.bin").write_bytes(b"payload")
        assert _wait_until(
            lambda: (
                {call[0] for call in submits.snapshot() if "before.bin" in call[1]} == {"t1", "t2"}
            )
        ), f"2 件とも拾えていない: {submits.snapshot()}"

        watcher.remove("t1")

        (downloads_dir / "after.bin").write_bytes(b"payload")
        assert _wait_until(
            lambda: any(call[0] == "t2" and "after.bin" in call[1] for call in submits.snapshot())
        ), f"残った t2 が拾わなくなった（unschedule が巻き添えにしている）: {submits.snapshot()}"
        assert [
            call for call in submits.snapshot() if call[0] == "t1" and "after.bin" in call[1]
        ] == [], "外した t1 がまだ拾っている"

        # 生死が実態と一致する（拾えている t2 だけが残り、`alive=True` を見せる）
        assert [(entry.trigger_id, entry.alive) for entry in watcher.state()] == [("t2", True)]

        # 最後の 1 件を外したら emitter ごと畳む（誰も見ていないフォルダを走査し続けない）
        watcher.remove("t2")
        assert watcher.state() == ()
        (downloads_dir / "nobody.bin").write_bytes(b"payload")
        time.sleep(POLL_INTERVAL * 6)
        assert [call for call in submits.snapshot() if "nobody.bin" in call[1]] == [], (
            "全件を外したのにまだ拾っている"
        )
    finally:
        watcher.stop()


def test_directory_events_are_dropped(downloads_dir: Path) -> None:
    """`event.is_directory` は捨てる（ディレクトリの作成では発火しない）。"""
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=True)
    watcher.start([("t1", str(downloads_dir))])
    try:
        (downloads_dir / "newdir").mkdir()
        time.sleep(POLL_INTERVAL * 6)

        assert [call for call in submits.snapshot() if "newdir" in call[1]] == []
    finally:
        watcher.stop()


def test_watcher_disabled_creates_no_observer_but_the_escape_route_still_fires(
    downloads_dir: Path,
) -> None:
    """FR-039・SC-008: `watcher_enabled=false` で observer を作らない。

    ただし**退避経路だけで発火する** —— 完了検知は `watcher_enabled` と独立に動く（S-4）。
    """
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=False)
    watcher.start([("t1", str(downloads_dir))])
    try:
        (downloads_dir / "ignored.bin").write_bytes(b"payload")
        time.sleep(POLL_INTERVAL * 6)

        assert submits.snapshot() == [], "監視が止まっているのに拾っている"
        assert watcher.state() == (), "WatcherState.watchers が空配列にならない"
        assert not watcher.is_alive()
    finally:
        watcher.stop()

    fires: list[tuple[str, str]] = []
    settle = SettleWorker(
        lambda trigger_id, path: fires.append((trigger_id, path)),
        stable_seconds=STABLE_SECONDS,
    )
    settle.start()
    try:
        settle.submit("t1", str(downloads_dir / "ignored.bin"), "hook")  # POST /hooks/file 相当

        assert _wait_until(lambda: len(fires) == 1), "退避経路でも発火しない（SC-008）"
    finally:
        settle.stop()


def test_a_dead_observer_thread_is_reported_and_never_restarted(downloads_dir: Path) -> None:
    """FR-042・US2 AC6・憲章 §10: スレッドが死んだら `alive=False` を見せるだけ。"""
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=True)
    watcher.start([("t1", str(downloads_dir))])
    try:
        assert all(entry.alive for entry in watcher.state())

        # スレッド死亡を模す（外から殺されうる状況を作る）
        observer = watcher._observer  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]  # 死亡を模す以外に手段が無い
        observer.stop()
        observer.join(timeout=5)

        assert _wait_until(lambda: not any(entry.alive for entry in watcher.state())), (
            "死んだのに alive=True のまま"
        )
        state = watcher.state()
        assert len(state) == 1, "死んでも一覧からは消えない（見せるのが仕事）"
        assert state[0].trigger_id == "t1"
        assert state[0].watch_dir == str(downloads_dir)

        (downloads_dir / "after_death.bin").write_bytes(b"payload")
        time.sleep(POLL_INTERVAL * 6)
        assert submits.snapshot() == [], "自動復旧している（憲章 §10 違反）"
        assert not watcher.is_alive()
    finally:
        watcher.stop()


def test_add_and_remove_reflect_on_a_running_observer(downloads_dir: Path) -> None:
    """FR-028・FR-029: 保存・削除が監視の開始・停止に反映される（次の起動を待たない）。"""
    submits = _Submits()
    watcher = WatcherRepository(submits, poll_interval_sec=POLL_INTERVAL, enabled=True)
    watcher.start([])
    try:
        assert watcher.state() == ()

        watcher.add("t1", str(downloads_dir))
        assert {entry.trigger_id for entry in watcher.state()} == {"t1"}
        (downloads_dir / "one.bin").write_bytes(b"payload")
        assert _wait_until(lambda: any("one.bin" in call[1] for call in submits.snapshot()))

        watcher.remove("t1")
        assert watcher.state() == ()
        (downloads_dir / "two.bin").write_bytes(b"payload")
        time.sleep(POLL_INTERVAL * 6)
        assert [call for call in submits.snapshot() if "two.bin" in call[1]] == []
    finally:
        watcher.stop()


def test_stop_without_start_is_harmless() -> None:
    """起動中断の片付けは「まだ起こしていない部品」にも呼ばれる（C-5）。"""
    WatcherRepository(_Submits(), poll_interval_sec=POLL_INTERVAL, enabled=True).stop()
