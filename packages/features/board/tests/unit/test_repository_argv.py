"""`BdCli` の argv 組立・env 注入・cwd 固定・ホワイトリスト（FR-010〜014）。

契約は `specs/003-board/contracts/bd-cli.md` §1〜§3。

`subprocess.run` は `monkeypatch` の偽物である。**実行ファイルを起動しない**ので `requires_bd` は
付けない（plan (viii)）。構造化ログの検査（FR-050〜052）もここに置く。
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import TYPE_CHECKING

import pytest
from board_testing import BD_BIN, BEADS_DIR, FakeRun, completed, log_argv, log_extra

from board.exceptions import BdCommandError, BdErrorKind
from board.repository import (
    ARGV_ELEMENT_CHARS,
    OUTPUT_HEAD_CHARS,
    WHITELIST,
    BdCli,
    BdCommand,
)
from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from board import BoardConfig


@pytest.fixture
def cli(default_config: BoardConfig) -> BdCli:
    """検査対象の `BdCli`（`bd` は絶対パスで解決済み＝ R2-7）。"""
    return BdCli(bd_bin=BD_BIN, beads_dir=BEADS_DIR, config=default_config)


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """`subprocess.run` を偽物に差し替える（既定は空配列の成功）。"""
    fake = FakeRun(completed("[]"))
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


class TestWhitelist:
    """ホワイトリスト 9 種の外は **bd を起動する前に**失敗させる（FR-010・SC-006）。"""

    def test_whitelist_has_exactly_nine_subcommands(self) -> None:
        assert {
            "ready",
            "show",
            "list",
            "comments",
            "create",
            "update",
            "close",
            "comment",
            "dep",
        } == WHITELIST

    @pytest.mark.parametrize("subcommand", ["init", "set-state"])
    def test_dangerous_subcommands_are_excluded(self, subcommand: str) -> None:
        """`init` は git リポジトリを作る（M-10・D19-d）。`set-state` は event bead（M-11）。"""
        assert subcommand not in WHITELIST

    @pytest.mark.parametrize("subcommand", ["init", "set-state", "delete", "version"])
    def test_running_outside_the_whitelist_never_spawns_bd(
        self, cli: BdCli, fake_run: FakeRun, subcommand: str
    ) -> None:
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=(subcommand, "--json")))
        assert exc.value.kind is BdErrorKind.other
        assert fake_run.calls == []


class TestArgv:
    """argv の組立（contracts/bd-cli.md §2）。"""

    def test_binary_is_prepended_as_an_absolute_path(self, cli: BdCli, fake_run: FakeRun) -> None:
        cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert fake_run.calls[0]["argv"] == [BD_BIN, "show", "pj-1", "--json"]

    def test_command_argv_is_passed_through_unchanged(self, cli: BdCli, fake_run: FakeRun) -> None:
        """`--json` と `--` の位置は `service.py` が完成させる（`BdCli` は足すだけ）。"""
        argv = ("comment", "pj-1", "--json", "--", "- [ ] 箇条書きの本文")
        cli.run(BdCommand(argv=argv, actor="demo_pj.programmer", write=True))
        assert fake_run.calls[0]["argv"] == [BD_BIN, *argv]

    def test_body_starting_with_a_dash_survives(self, cli: BdCli, fake_run: FakeRun) -> None:
        """`--` 区切りが無いと `unknown shorthand flag` で落ちる（M-28）。"""
        body = "-r いきなりハイフンで始まる本文"
        cli.run(BdCommand(argv=("comment", "pj-1", "--json", "--", body), write=True))
        assert fake_run.calls[0]["argv"][-1] == body

    def test_shell_is_never_used(self, cli: BdCli, fake_run: FakeRun) -> None:
        """`shell=False`。リストのまま渡し、文字列へ連結しない（FR-011・SC-006）。"""
        cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert fake_run.calls[0].get("shell") in (None, False)
        assert isinstance(fake_run.calls[0]["argv"], list)

    def test_repeated_label_flags_are_not_merged(self, cli: BdCli, fake_run: FakeRun) -> None:
        """ラベルは区切り文字で連結しない（R-04。`BdCli` は並びを触らない）。"""
        argv = ("list", "-l", "a:1", "-l", "b:2", "--json")
        cli.run(BdCommand(argv=argv))
        assert fake_run.calls[0]["argv"] == [BD_BIN, *argv]


class TestEnvironment:
    """実行環境（contracts/bd-cli.md §3・FR-012・FR-013）。"""

    def test_beads_dir_is_injected_through_env(self, cli: BdCli, fake_run: FakeRun) -> None:
        cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert fake_run.calls[0]["env"]["BEADS_DIR"] == str(BEADS_DIR)

    def test_actor_is_injected_only_for_writes(self, cli: BdCli, fake_run: FakeRun) -> None:
        cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert "BEADS_ACTOR" not in fake_run.calls[0]["env"]

    def test_actor_is_injected_when_given(self, cli: BdCli, fake_run: FakeRun) -> None:
        cli.run(BdCommand(argv=("close", "pj-1", "--json"), actor="demo_pj.lead", write=True))
        assert fake_run.calls[0]["env"]["BEADS_ACTOR"] == "demo_pj.lead"

    def test_actor_never_appears_in_argv(self, cli: BdCli, fake_run: FakeRun) -> None:
        cli.run(BdCommand(argv=("close", "pj-1", "--json"), actor="demo_pj.lead", write=True))
        assert "demo_pj.lead" not in fake_run.calls[0]["argv"]

    def test_cwd_is_the_parent_of_beads_dir(self, cli: BdCli, fake_run: FakeRun) -> None:
        """cwd を親に固定すると `beads.role not configured` 警告が消える（M-30・R-01）。"""
        cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert fake_run.calls[0]["cwd"] == str(BEADS_DIR.parent)

    def test_existing_environment_is_inherited(
        self, cli: BdCli, fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
        cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert fake_run.calls[0]["env"]["PATH"] == "/usr/bin"


class TestMachineReadableOutput:
    """`--json` を成功の条件にする（FR-014）。"""

    def test_json_is_parsed(self, cli: BdCli, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRun(completed(json.dumps([{"id": "pj-1"}])))
        monkeypatch.setattr(subprocess, "run", fake)
        result = cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert result.json == [{"id": "pj-1"}]
        assert result.rc == 0
        assert result.attempts == 1

    @pytest.mark.parametrize("stdout", ["", "   ", "not json at all"])
    def test_non_json_output_is_not_a_success(
        self, cli: BdCli, monkeypatch: pytest.MonkeyPatch, stdout: str
    ) -> None:
        """空文字列を含め、機械可読でない出力を成功にしない（FR-014）。"""
        monkeypatch.setattr(subprocess, "run", FakeRun(completed(stdout)))
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert exc.value.kind is BdErrorKind.unavailable

    def test_output_heads_are_truncated(self, cli: BdCli, monkeypatch: pytest.MonkeyPatch) -> None:
        long_stdout = "x" * (OUTPUT_HEAD_CHARS + 100)
        monkeypatch.setattr(subprocess, "run", FakeRun(completed(long_stdout, rc=1)))
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert len(exc.value.stderr) == len("stdout: ") + OUTPUT_HEAD_CHARS


class TestFailureClassification:
    """失敗の判定は**文言**で行う（FR-041a・M-06。rc では分類できない）。"""

    def test_not_found_on_stdout_is_detected(
        self, cli: BdCli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdout = '{"error": "no issues found matching the provided IDs"}'
        monkeypatch.setattr(subprocess, "run", FakeRun(completed(stdout, rc=1)))
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=("show", "pj-9", "--json"), issue_id="pj-9"))
        assert exc.value.kind is BdErrorKind.not_found

    def test_missing_database_is_unavailable(
        self, cli: BdCli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess, "run", FakeRun(completed("", rc=1, stderr="Error: no beads database found"))
        )
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert exc.value.kind is BdErrorKind.unavailable

    def test_unrecognised_failure_falls_back_to_other(
        self, cli: BdCli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判定漏れは `other` に倒れ、再実行されない（安全側）。"""
        monkeypatch.setattr(
            subprocess, "run", FakeRun(completed("", rc=1, stderr="Error: 見たことのない失敗"))
        )
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        assert exc.value.kind is BdErrorKind.other

    def test_exception_argv_is_the_safe_form(
        self, cli: BdCli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """例外の `argv` は自由文字列を伏せた形（R-09）。"""
        body = "秘密 sk-live-0123456789 を含む本文"
        monkeypatch.setattr(subprocess, "run", FakeRun(completed("", rc=1, stderr="Error: 失敗")))
        with pytest.raises(BdCommandError) as exc:
            cli.run(BdCommand(argv=("comment", "pj-1", "--json", "--", body), write=True))
        assert "sk-live-0123456789" not in " ".join(exc.value.argv)
        assert exc.value.argv[-1] == f"<body:{len(body)}chars>"


class TestStructuredLog:
    """試行 1 回につき 1 レコード（P5D-006・FR-050〜052・contracts/bd-cli.md §6）。"""

    def test_one_record_per_attempt(
        self, cli: BdCli, fake_run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="board.repository"):
            cli.run(BdCommand(argv=("show", "pj-1", "--json"), issue_id="pj-1"))
        records = [r for r in caplog.records if r.name == "board.repository"]
        assert len(records) == 1
        assert records[0].getMessage() == "bd"
        assert records[0].levelno == logging.INFO

    def test_record_carries_the_real_argv(
        self, cli: BdCli, fake_run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ログの `argv` は**実値**（伏字は `RedactFilter` が掛ける）。例外の `argv` とは別物。"""
        body = "本文はログには実値で残る"
        with caplog.at_level(logging.INFO, logger="board.repository"):
            cli.run(BdCommand(argv=("comment", "pj-1", "--json", "--", body), write=True))
        record = next(r for r in caplog.records if r.name == "board.repository")
        argv = log_argv(record)
        assert argv[-1] == body
        assert log_extra(record, "write") is True
        assert log_extra(record, "attempt") == 1
        assert log_extra(record, "rc") == 0
        assert isinstance(log_extra(record, "elapsed_sec"), float)

    def test_failure_is_logged_at_warning(
        self, cli: BdCli, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(subprocess, "run", FakeRun(completed("", rc=1, stderr="Error: 失敗")))
        with (
            caplog.at_level(logging.INFO, logger="board.repository"),
            pytest.raises(BdCommandError),
        ):
            cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        record = next(r for r in caplog.records if r.name == "board.repository")
        assert record.levelno == logging.WARNING

    def test_reserved_logging_keys_are_never_passed(
        self, cli: BdCli, fake_run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`extra` に `module`・`name`・`args`・`msg` を渡すと `KeyError` になる。"""
        with caplog.at_level(logging.INFO, logger="board.repository"):
            cli.run(BdCommand(argv=("show", "pj-1", "--json")))
        record = next(r for r in caplog.records if r.name == "board.repository")
        assert record.name == "board.repository"
        assert record.module == "repository"

    def test_issue_id_is_promoted(
        self, cli: BdCli, fake_run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="board.repository"):
            cli.run(BdCommand(argv=("show", "pj-1", "--json"), issue_id="pj-1"))
        record = next(r for r in caplog.records if r.name == "board.repository")
        assert log_extra(record, "issue_id") == "pj-1"

    def test_argv_elements_are_truncated_in_the_log(
        self, cli: BdCli, fake_run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="board.repository"):
            cli.run(BdCommand(argv=("show", "p" * (ARGV_ELEMENT_CHARS + 50), "--json")))
        record = next(r for r in caplog.records if r.name == "board.repository")
        argv = log_argv(record)
        assert len(argv[2]) == ARGV_ELEMENT_CHARS

    def test_correlation_id_is_attached_by_the_formatter(
        self, cli: BdCli, fake_run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        """board は相関 ID を引数で受け取らない（憲章 §6・FR-052）。整形器が読む。"""
        token = CorrelationContext.bind("cid-board-1")
        try:
            with caplog.at_level(logging.INFO, logger="board.repository"):
                cli.run(BdCommand(argv=("show", "pj-1", "--json")))
            assert CorrelationContext.current() == "cid-board-1"
        finally:
            CorrelationContext.reset(token)
