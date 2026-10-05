"""ロック待ちの再実行・直列化・タイムアウト（FR-015〜017・contracts/bd-cli.md §5）。

`subprocess.run` は `monkeypatch` の偽物である（実行ファイルを起動しないので
`requires_bd` は付けない）。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

import pytest
from board_testing import BD_BIN, BEADS_DIR, FakeRun, completed, log_extra

from board import BoardConfig
from board.exceptions import BdCommandError, BdErrorKind
from board.repository import RC_NOT_SPAWNED, RC_TIMEOUT, BdCli, BdCommand

LOCK_STDERR = "Error: database is LOCKED by another process"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """`board.repository` が呼ぶ `time.sleep` を記録だけの偽物にする。"""
    recorded: list[float] = []
    monkeypatch.setattr("board.repository.time.sleep", recorded.append)
    return recorded


def _cli(config: BoardConfig) -> BdCli:
    return BdCli(bd_bin=BD_BIN, beads_dir=BEADS_DIR, config=config)


class TestLockRetry:
    """種別が `lock` のときだけ待って同じ呼び出しをやり直す（R5-3〜R5-5）。"""

    def test_succeeds_within_the_limit(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        fake = FakeRun(
            completed("", rc=1, stderr=LOCK_STDERR),
            completed("", rc=1, stderr=LOCK_STDERR),
            completed("[]"),
        )
        monkeypatch.setattr(subprocess, "run", fake)
        result = _cli(default_config).run(BdCommand(argv=("list", "--json")))
        assert result.attempts == 3
        assert len(fake.calls) == 3
        assert len(sleeps) == 2

    def test_exhausting_the_limit_raises_lock_exhausted(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        fake = FakeRun(*[completed("", rc=1, stderr=LOCK_STDERR) for _ in range(3)])
        monkeypatch.setattr(subprocess, "run", fake)
        with pytest.raises(BdCommandError) as exc:
            _cli(default_config).run(BdCommand(argv=("list", "--json")))
        assert exc.value.kind is BdErrorKind.lock_exhausted
        assert exc.value.attempts == default_config.lock_wait_max_attempts
        assert len(fake.calls) == default_config.lock_wait_max_attempts

    def test_lock_hints_match_case_insensitively_and_partially(
        self, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        config = BoardConfig(lock_hints=["BUSY"])
        fake = FakeRun(
            completed("", rc=1, stderr="the resource is busy right now"), completed("[]")
        )
        monkeypatch.setattr(subprocess, "run", fake)
        assert _cli(config).run(BdCommand(argv=("list", "--json"))).attempts == 2

    def test_non_lock_failures_are_never_retried(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        """憲章 §10・SC-008: ロック以外への再実行は 0 回。"""
        fake = FakeRun(completed("", rc=1, stderr="Error: 見たことのない失敗"))
        monkeypatch.setattr(subprocess, "run", fake)
        with pytest.raises(BdCommandError) as exc:
            _cli(default_config).run(BdCommand(argv=("list", "--json")))
        assert exc.value.kind is BdErrorKind.other
        assert exc.value.attempts == 1
        assert len(fake.calls) == 1
        assert sleeps == []

    def test_wait_is_linear_in_the_attempt_number(
        self, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        """`lock_wait_base_sec × 試行番号`（CFG-24）。上限に達したら待たずに失敗する。"""
        config = BoardConfig(lock_wait_max_attempts=4, lock_wait_base_sec=0.25)
        monkeypatch.setattr(
            subprocess, "run", FakeRun(*[completed("", rc=1, stderr=LOCK_STDERR) for _ in range(4)])
        )
        with pytest.raises(BdCommandError):
            _cli(config).run(BdCommand(argv=("list", "--json")))
        assert sleeps == [0.25, 0.5, 0.75]

    def test_lock_retry_also_applies_to_reads(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        """R5-6: P5D-004 の裁定文は対象を書込に限っていない。"""
        fake = FakeRun(completed("", rc=1, stderr=LOCK_STDERR), completed("[]"))
        monkeypatch.setattr(subprocess, "run", fake)
        result = _cli(default_config).run(BdCommand(argv=("show", "pj-1", "--json"), write=False))
        assert result.attempts == 2

    def test_one_log_record_per_attempt(
        self,
        default_config: BoardConfig,
        monkeypatch: pytest.MonkeyPatch,
        sleeps: list[float],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            FakeRun(completed("", rc=1, stderr=LOCK_STDERR), completed("[]")),
        )
        with caplog.at_level(logging.INFO, logger="board.repository"):
            _cli(default_config).run(BdCommand(argv=("list", "--json")))
        records = [r for r in caplog.records if r.name == "board.repository"]
        assert [log_extra(r, "attempt") for r in records] == [1, 2]
        assert [r.levelno for r in records] == [logging.WARNING, logging.INFO]


class TestTimeoutAndSpawnFailure:
    """タイムアウトと spawn 失敗はロック競合ではないので再実行しない（R5-7・R-08）。"""

    def test_timeout_uses_the_sentinel_and_is_not_retried(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        fake = FakeRun(subprocess.TimeoutExpired(cmd=[BD_BIN], timeout=120))
        monkeypatch.setattr(subprocess, "run", fake)
        with pytest.raises(BdCommandError) as exc:
            _cli(default_config).run(BdCommand(argv=("list", "--json")))
        assert exc.value.rc == RC_TIMEOUT
        assert exc.value.kind is BdErrorKind.unavailable
        assert len(fake.calls) == 1

    def test_timeout_value_comes_from_config(
        self, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        config = BoardConfig(timeout_sec=7)
        fake = FakeRun(completed("[]"))
        monkeypatch.setattr(subprocess, "run", fake)
        _cli(config).run(BdCommand(argv=("list", "--json")))
        assert fake.calls[0]["timeout"] == 7

    def test_spawn_failure_uses_the_sentinel(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
    ) -> None:
        fake = FakeRun(OSError("実行ファイルがありません"))
        monkeypatch.setattr(subprocess, "run", fake)
        with pytest.raises(BdCommandError) as exc:
            _cli(default_config).run(BdCommand(argv=("list", "--json")))
        assert exc.value.rc == RC_NOT_SPAWNED
        assert exc.value.kind is BdErrorKind.unavailable
        assert len(fake.calls) == 1


class _Concurrency:
    """偽 `subprocess.run` の同時進入数を測る補助。"""

    def __init__(self, hold_sec: float) -> None:
        self._hold_sec = hold_sec
        self._guard = threading.Lock()
        self._inside = 0
        self.peak = 0

    def __call__(self, argv: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        with self._guard:
            self._inside += 1
            self.peak = max(self.peak, self._inside)
        time.sleep(self._hold_sec)
        with self._guard:
            self._inside -= 1
        return completed("[]")


def _run_in_threads(cli: BdCli, command: BdCommand, count: int) -> None:
    threads = [threading.Thread(target=lambda: cli.run(command)) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)


class TestSerialisation:
    """書込のみプロセス内 `threading.Lock` で直列化する（R5-1・R5-2・R-07）。"""

    def test_writes_are_serialised(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = _Concurrency(hold_sec=0.05)
        monkeypatch.setattr(subprocess, "run", probe)
        _run_in_threads(
            _cli(default_config), BdCommand(argv=("close", "pj-1", "--json"), write=True), 3
        )
        assert probe.peak == 1

    def test_reads_are_not_serialised(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = _Concurrency(hold_sec=0.05)
        monkeypatch.setattr(subprocess, "run", probe)
        _run_in_threads(
            _cli(default_config), BdCommand(argv=("show", "pj-1", "--json"), write=False), 3
        )
        assert probe.peak > 1

    def test_the_lock_is_held_across_retries(
        self, default_config: BoardConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R5-2: 途中で手放すと、待っている間に別スレッドの書込が割り込む。

        ロック待ちで 1 回失敗する書込（pj-1）の**待ち時間中**に別の書込（pj-2）を走らせ、
        bd に届いた順が `pj-1, pj-1, pj-2` になる（＝割り込まれない）ことで確かめる。
        """
        cli = _cli(default_config)
        order: list[str] = []
        guard = threading.Lock()
        intruder: list[threading.Thread] = []

        def scripted_run(argv: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            target = [a for a in list(argv) if isinstance(a, str) and a.startswith("pj-")][0]  # type: ignore[arg-type]
            with guard:
                order.append(target)
                first_attempt_of_pj1 = order.count("pj-1") == 1
            if first_attempt_of_pj1:
                return completed("", rc=1, stderr=LOCK_STDERR)
            return completed("[]")

        def during_sleep(_seconds: float) -> None:
            if not intruder:
                thread = threading.Thread(
                    target=lambda: cli.run(BdCommand(argv=("close", "pj-2", "--json"), write=True))
                )
                intruder.append(thread)
                thread.start()
            # `time.sleep` は差し替え済みなので使わない（自己再帰になる）
            threading.Event().wait(0.05)

        monkeypatch.setattr(subprocess, "run", scripted_run)
        monkeypatch.setattr("board.repository.time.sleep", during_sleep)
        cli.run(BdCommand(argv=("close", "pj-1", "--json"), write=True))
        intruder[0].join(timeout=5)
        assert order == ["pj-1", "pj-1", "pj-2"]
