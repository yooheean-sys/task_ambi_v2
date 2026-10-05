"""F08b 完了検知（`repository/settle.py`）。

**番号（`S-1`〜`S-7`）の正本は
[contracts/fire-pipeline.md](../../../../specs/011-trigger/contracts/fire-pipeline.md) §5** である。
期待値は契約からの写しで、ここで発明しない。

完了検知は**監視から独立した部品**（SD-07）—— `watcher_enabled=false` でも退避経路
（`POST /hooks/file`）の完了検知を担う（S-4）。

⚠ **実運用の DL フォルダ（Windows 側の共有）に 1 バイトも触れない**（P-2）。
`tmp_path`（ext4）だけを使う。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from trigger.repository.settle import SETTLE_POLL_SECONDS, SettleWorker

if TYPE_CHECKING:
    from pathlib import Path

STABLE_SECONDS = 2
TTL_FACTOR = 10
"""重複抑止の保持は `stable_seconds × 10`（＝20 秒）＝ S-3。"""

SLOW_CALLBACK_SECONDS = 6.0
"""T092 の観測点で使う callback の所要（秒）。

取り除いた `join` の timeout は `poll_seconds * 10 + 5` で、`poll_seconds` をいくら小さく
しても **5 秒**を下回らない。**これを超える callback でなければ変異を捕まえられない**。
"""


class _Clock:
    """テストが進める単調時計（`time.monotonic` の差し替え）。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Fires:
    """完了時の callback（`SettleWorker` は `fire` を直接呼ばない＝ SD-07）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, trigger_id: str, path: str) -> None:
        self.calls.append((trigger_id, path))


def _worker(fires: _Fires, clock: _Clock) -> SettleWorker:
    return SettleWorker(fires, stable_seconds=STABLE_SECONDS, clock=clock)


def _tick(worker: SettleWorker, clock: _Clock, times: int = 1) -> None:
    """`poll_once()` を `times` 回、1 秒刻みで回す（スレッドを起こさずに走査だけする）。"""
    for _ in range(times):
        clock.advance(SETTLE_POLL_SECONDS)
        worker.poll_once()


# -- S-1・S-2: 安定判定 --------------------------------------------------------------------


def test_the_poll_interval_is_one_second_and_separate_from_the_observer_timeout() -> None:
    """S-1: `stat` は `poll_interval_sec`（5.0）とは**別に約 1 秒間隔**（M-03・M-08・C-6）。"""
    assert SETTLE_POLL_SECONDS == 1.0
    fires, clock = _Fires(), _Clock()

    assert _worker(fires, clock).poll_seconds == SETTLE_POLL_SECONDS


def test_nothing_fires_while_the_size_and_mtime_keep_changing(tmp_path: Path) -> None:
    """S-2・SC-006: `(st_size, st_mtime_ns)` が変わり続けている間は発火 **0 回**。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "big.bin"
    target.write_bytes(b"0")
    worker.submit("t1", str(target), "watchdog")

    for chunk in range(6):
        with target.open("ab") as stream:
            stream.write(b"x" * 1024)
        stamp = (1_700_000_000 + chunk) * 1_000_000_000
        os.utime(target, ns=(stamp, stamp))
        _tick(worker, clock)

    assert fires.calls == []


def test_it_fires_exactly_once_after_stable_seconds_of_no_change(tmp_path: Path) -> None:
    """S-2・SC-006: 変わらなくなってから `stable_seconds` 経過で **1 回**。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "done.bin"
    target.write_bytes(b"payload")
    worker.submit("t1", str(target), "watchdog")

    _tick(worker, clock)  # 1 周目: 初回の stat（stable_since を置く）
    assert fires.calls == []
    _tick(worker, clock, times=STABLE_SECONDS)

    assert fires.calls == [("t1", os.path.realpath(target))]

    _tick(worker, clock, times=3)
    assert len(fires.calls) == 1, "同じ完了で 2 回発火した"


def test_a_change_of_mtime_alone_resets_the_stability_window(tmp_path: Path) -> None:
    """S-2・DIFF-14: **サイズと mtime の両方**を見る（mtime だけ動いても「変わった」）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "same_size.bin"
    target.write_bytes(b"1234")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock)

    os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_500_000_000_000))
    _tick(worker, clock)  # ここで stable_since が更新される
    assert fires.calls == []

    _tick(worker, clock, times=STABLE_SECONDS)
    assert len(fires.calls) == 1


def test_a_change_of_size_alone_resets_the_stability_window(tmp_path: Path) -> None:
    """S-2: サイズだけ動いても「変わった」（mtime を元へ戻しても捕まえる）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "same_mtime.bin"
    target.write_bytes(b"1234")
    frozen = (1_700_000_000_000_000_000, 1_700_000_000_000_000_000)
    os.utime(target, ns=frozen)
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock)

    with target.open("ab") as stream:
        stream.write(b"5678")
    os.utime(target, ns=frozen)  # mtime を据え置く
    _tick(worker, clock)
    assert fires.calls == []

    _tick(worker, clock, times=STABLE_SECONDS)
    assert len(fires.calls) == 1


def test_a_vanished_file_is_dropped_from_the_pending_set(tmp_path: Path) -> None:
    """S-2: `FileNotFoundError` は**保留から落とす**（置かれてすぐ消されたファイル）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "gone.bin"
    target.write_bytes(b"x")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock)

    target.unlink()
    _tick(worker, clock, times=STABLE_SECONDS + 2)

    assert fires.calls == []
    assert worker.pending_count == 0


def test_the_dropped_file_itself_is_never_modified(tmp_path: Path) -> None:
    """S-5・SC-007: **読むだけ**（発火後もパス・サイズ・mtime が同一）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "untouched.bin"
    target.write_bytes(b"payload" * 100)
    before = target.stat()
    worker.submit("t1", str(target), "hook")

    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 1
    after = target.stat()
    assert target.exists()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


# -- S-3・S-6・S-7: 重複抑止と保留の除去 ---------------------------------------------------


def test_the_same_four_tuple_does_not_fire_twice(tmp_path: Path) -> None:
    """S-3・SC-022: 鍵は **4 つ組**（`trigger_id`・実パス・サイズ・mtime）。同じ組では 1 回。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "twice.bin"
    target.write_bytes(b"same")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock, times=STABLE_SECONDS + 1)
    assert len(fires.calls) == 1

    worker.submit("t1", str(target), "hook")  # 別入口から同じ投下（監視と通知の両方）
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 1, "同じ投下が 2 つの入口から入って 2 回発火した"


def test_replacing_the_contents_fires_again(tmp_path: Path) -> None:
    """S-3・SC-022: サイズか mtime が変われば**別の投下**として発火する（合計 2 回）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "swapped.bin"
    target.write_bytes(b"first")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    target.write_bytes(b"second-and-longer")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 2


def test_two_triggers_watching_the_same_folder_both_fire(tmp_path: Path) -> None:
    """S-3・FR-042a (c): 鍵に `trigger_id` を含むので **1 投下で 2 回**（仕様どおり残す）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "shared.bin"
    target.write_bytes(b"payload")
    worker.submit("t1", str(target), "watchdog")
    worker.submit("t2", str(target), "watchdog")

    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert sorted(fires.calls) == [
        ("t1", os.path.realpath(target)),
        ("t2", os.path.realpath(target)),
    ]


def test_a_completed_event_leaves_the_pending_set_and_never_refires_after_the_ttl(
    tmp_path: Path,
) -> None:
    """S-6・SD-25: **完了したら保留から落とす**。

    ⚠ 落とさないと `stat` ループが同じエントリを見続け、**TTL（`stable_seconds × 10` ＝ 20 秒）が
    切れた瞬間に同じファイルで再発火する**。20 秒以内しか観測しないテストは**緑のまま出荷される**
    ので、観測点は「**`stable_seconds × 10` を超えて**待っても再発火が **0 回**」である。
    """
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "settled.bin"
    target.write_bytes(b"payload")
    worker.submit("t1", str(target), "watchdog")

    _tick(worker, clock, times=STABLE_SECONDS + 1)
    assert len(fires.calls) == 1
    assert worker.pending_count == 0, "完了したのに保留に残っている（TTL 切れで再発火する）"

    _tick(worker, clock, times=STABLE_SECONDS * TTL_FACTOR + 5)

    assert len(fires.calls) == 1, "TTL が切れた瞬間に再発火した（SD-25）"


def test_a_discarded_duplicate_also_leaves_the_pending_set(tmp_path: Path) -> None:
    """S-6: **発火した場合も捨てた場合も**保留から落ちる（役割は S-7 で分けてある）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "dup.bin"
    target.write_bytes(b"payload")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    worker.submit("t1", str(target), "hook")
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 1
    assert worker.pending_count == 0


def test_a_new_event_after_the_ttl_expires_fires_again(tmp_path: Path) -> None:
    """S-6: **再発火の唯一の契機は「新しいイベントが来ること」**（TTL 切れ後の新規投下）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "again.bin"
    target.write_bytes(b"payload")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    _tick(worker, clock, times=STABLE_SECONDS * TTL_FACTOR + 2)  # TTL を過ぎさせる
    worker.submit("t1", str(target), "hook")  # 新しいイベント
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 2


def test_the_recent_fires_table_lives_only_in_memory(tmp_path: Path) -> None:
    """S-3・FR-013: 置き場は**プロセス内メモリだけ**（再起動で消える）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / "mem.bin"
    target.write_bytes(b"payload")
    worker.submit("t1", str(target), "watchdog")
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert sorted(item.name for item in tmp_path.iterdir()) == ["mem.bin"], (
        "重複抑止の記録がファイルに書かれている"
    )

    reborn = _worker(fires, clock)  # 「再起動」＝新しいインスタンス
    reborn.submit("t1", str(target), "watchdog")
    _tick(reborn, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 2, "新しいプロセスなのに抑止が効いている（永続化している）"


# -- スレッドの起動停止 --------------------------------------------------------------------


def test_start_and_stop_are_symmetric(tmp_path: Path) -> None:
    """`start()` で 1 本起こし、`stop()` で `join` する（起動列の段 5・停止列の段 3）。"""
    import threading

    fires, clock = _Fires(), _Clock()
    worker = SettleWorker(fires, stable_seconds=STABLE_SECONDS, poll_seconds=0.01, clock=clock)
    before = threading.active_count()

    worker.start()
    assert threading.active_count() == before + 1

    worker.stop()
    assert threading.active_count() == before
    assert not worker.is_alive()


def test_stop_waits_for_a_callback_that_outlasts_the_old_join_timeout(tmp_path: Path) -> None:
    """T092・FR-033 段 3・SC-017・N-6: `stop()` の `join` に **timeout を置かない**。

    完了検知スレッドは callback の中で `fire()` →`KickoffRunner.run` を**同期で**回すので、
    timeout を置くと `file` 入口の**進行中 kickoff を打ち切って返る**。lifespan が閉じて
    プロセスが終われば daemon スレッドは実行中のまま消え、履歴の行は `started` のまま残る。
    実運用の kickoff は LLM を回すので分単位であり、既定（`poll_seconds=1.0` → **15 秒**）
    では常に踏む。段 1・段 2 の `wait=True` と揃えて待ち切る。

    ⚠ **本テストが `SLOW_CALLBACK_SECONDS` 秒かかるのは意図的**である —— 取り除いた timeout は
    `poll_seconds * 10 + 5` で、`poll_seconds` をいくら小さくしても **5 秒**を下回らない。
    callback がそれを超えなければ打ち切りが起きず、**変異を捕まえられない**。
    """
    import threading
    import time

    entered = threading.Event()
    done: list[str] = []

    def _slow(trigger_id: str, _path: str) -> None:
        entered.set()
        time.sleep(SLOW_CALLBACK_SECONDS)
        done.append(trigger_id)

    worker = SettleWorker(_slow, stable_seconds=0, poll_seconds=0.01)
    target = tmp_path / "in_flight.bin"
    target.write_bytes(b"payload")
    worker.start()
    try:
        worker.submit("t1", str(target), "watchdog")
        assert entered.wait(timeout=10.0), "完了検知が callback へ入らない"

        worker.stop()

        assert done == ["t1"], "進行中の kickoff を打ち切って stop() が返った"
        assert not worker.is_alive()
    finally:
        worker.stop()


def test_stop_without_start_is_harmless() -> None:
    """起動中断の片付けは「まだ起こしていない部品」にも呼ばれる（C-5）。"""
    fires, clock = _Fires(), _Clock()

    _worker(fires, clock).stop()


def test_submit_of_a_missing_path_is_ignored(tmp_path: Path) -> None:
    """存在しないパスは保留に載せない（`realpath` は解決できても `stat` で落ちる）。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)

    worker.submit("t1", str(tmp_path / "never"), "hook")
    _tick(worker, clock, times=STABLE_SECONDS + 2)

    assert fires.calls == []


def test_a_failing_callback_does_not_kill_the_worker(tmp_path: Path) -> None:
    """完了 callback（`fire`）が落ちても走査は続く（次の投下を拾えなくならない）。"""
    clock = _Clock()
    seen: list[str] = []

    def _sometimes_boom(trigger_id: str, path: str) -> None:
        seen.append(trigger_id)
        if trigger_id == "bad":
            msg = "callback が落ちた"
            raise RuntimeError(msg)

    worker = SettleWorker(_sometimes_boom, stable_seconds=STABLE_SECONDS, clock=clock)
    target = tmp_path / "cb.bin"
    target.write_bytes(b"payload")
    worker.submit("bad", str(target), "watchdog")
    worker.submit("good", str(target), "watchdog")

    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert sorted(seen) == ["bad", "good"]


@pytest.mark.parametrize("source", ["watchdog", "hook"])
def test_both_sources_go_through_the_same_completion_path(tmp_path: Path, source: str) -> None:
    """S-4・FR-040: 監視と退避経路は**同じ完了検知**を経て同じ発火へ合流する。"""
    fires, clock = _Fires(), _Clock()
    worker = _worker(fires, clock)
    target = tmp_path / f"{source}.bin"
    target.write_bytes(b"payload")

    worker.submit("t1", str(target), source)  # pyright: ignore[reportArgumentType]  # parametrize の str を Literal へ絞る手間を避ける
    _tick(worker, clock, times=STABLE_SECONDS + 1)

    assert len(fires.calls) == 1
