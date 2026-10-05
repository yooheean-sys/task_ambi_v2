"""内部の失敗 → 契約例外の翻訳（FR-042〜044・plan (v) の翻訳表）。"""

from __future__ import annotations

import pytest
from board_testing import FAKE_BD_BIN, bd_ok

from board.exceptions import BdCommandError, BdErrorKind, BdMappingError
from board.repository import (
    ARGV_ELEMENT_CHARS,
    OUTPUT_HEAD_CHARS,
    safe_argv_of,
    stderr_head_of,
)
from board.service import translate_failure
from common.interfaces.board import BoardUnavailable, IssueNotFound

FAILED_ARGV: tuple[str, ...] = (FAKE_BD_BIN, "show", "pj-1", "--json")


def _error(kind: BdErrorKind, *, rc: int = 1, stderr: str = "boom") -> BdCommandError:
    return BdCommandError("bd が失敗した", kind=kind, argv=FAILED_ARGV, rc=rc, stderr=stderr)


class TestNotFound:
    """not-found の翻訳は 14 メソッド一律（FR-042・裁定 2026-08-21。design-sync B-4）。"""

    def test_not_found_becomes_issue_not_found_with_the_target_id(self) -> None:
        error = _error(BdErrorKind.not_found)
        translated = translate_failure(error, issue_id="pj-1")
        assert isinstance(translated, IssueNotFound)
        assert translated.issue_id == "pj-1"
        assert translated.cause is error

    def test_not_found_without_a_target_id_falls_back_to_unavailable(self) -> None:
        """列挙系（`children_of`・`list_working`・`has_open_issues`）は対象 ID を持たない。"""
        translated = translate_failure(_error(BdErrorKind.not_found))
        assert isinstance(translated, BoardUnavailable)


class TestUnavailable:
    """`BoardUnavailable` の 4 属性に実値が載る（FR-043）。"""

    @pytest.mark.parametrize(
        "kind", [BdErrorKind.lock_exhausted, BdErrorKind.unavailable, BdErrorKind.other]
    )
    def test_every_other_kind_becomes_board_unavailable(self, kind: BdErrorKind) -> None:
        error = BdCommandError(
            "bd が失敗した", kind=kind, argv=FAILED_ARGV, rc=1, stderr="boom", attempts=3
        )
        translated = translate_failure(error, issue_id="pj-1")
        assert isinstance(translated, BoardUnavailable)
        assert translated.argv == list(FAILED_ARGV)
        assert translated.rc == 1
        assert translated.stderr_head == "boom"
        assert translated.attempts == 3
        assert translated.cause is error

    def test_attempts_defaults_to_one(self) -> None:
        translated = translate_failure(_error(BdErrorKind.other))
        assert isinstance(translated, BoardUnavailable)
        assert translated.attempts == 1

    def test_argv_is_a_list_not_a_tuple(self) -> None:
        """契約の型は `list[str]`（data-model §5 の ⚠）。"""
        translated = translate_failure(_error(BdErrorKind.other))
        assert isinstance(translated, BoardUnavailable)
        assert isinstance(translated.argv, list)


class TestMappingFailure:
    """写像の失敗は直前の `BdResult` から `argv`・`rc`・`attempts` を埋める（R-11）。"""

    def test_fills_from_the_preceding_result(self) -> None:
        result = bd_ok({"id": "pj-1"}, attempts=2, argv=FAILED_ARGV)
        translated = translate_failure(
            BdMappingError("必須キーが無い: status", key="status"), issue_id="pj-1", result=result
        )
        assert isinstance(translated, BoardUnavailable)
        assert translated.argv == list(result.safe_argv)
        assert translated.rc == result.rc
        assert translated.attempts == 2
        assert "status" in translated.stderr_head

    def test_mapping_failure_is_never_translated_to_issue_not_found(self) -> None:
        translated = translate_failure(BdMappingError("壊れた", key="id"), issue_id="pj-1")
        assert isinstance(translated, BoardUnavailable)


class TestSecretsAreNotCarried:
    """例外の `argv` に秘密・自由文字列の原文を載せない（FR-043・R-09）。"""

    def test_env_names_never_appear_in_argv(self) -> None:
        """`BEADS_DIR`／`BEADS_ACTOR` は env で渡すので argv に現れない（FR-012）。"""
        argv = safe_argv_of(
            (FAKE_BD_BIN, "comment", "pj-1", "--json", "--", "秘密 sk-live-0123456789")
        )
        joined = " ".join(argv)
        assert "BEADS_DIR" not in joined
        assert "BEADS_ACTOR" not in joined

    def test_body_after_the_separator_becomes_a_placeholder(self) -> None:
        body = "- [ ] 受け入れ条件に秘密 sk-live-0123456789 が混ざっている"
        argv = safe_argv_of((FAKE_BD_BIN, "comment", "pj-1", "--json", "--", body))
        assert argv == (FAKE_BD_BIN, "comment", "pj-1", "--json", "--", f"<body:{len(body)}chars>")
        assert "sk-live-0123456789" not in " ".join(argv)

    @pytest.mark.parametrize(
        ("flag", "label"),
        [("--title", "title"), ("-d", "d"), ("--acceptance", "acceptance"), ("-r", "r")],
    )
    def test_free_text_flag_values_become_placeholders(self, flag: str, label: str) -> None:
        value = "x" * 42
        argv = safe_argv_of((FAKE_BD_BIN, "create", flag, value, "--json"))
        assert argv == (FAKE_BD_BIN, "create", flag, f"<{label}:42chars>", "--json")

    def test_flags_labels_and_ids_are_kept(self) -> None:
        argv = safe_argv_of(
            (FAKE_BD_BIN, "update", "pj-1", "--add-label", "state:working", "--json")
        )
        assert argv == (FAKE_BD_BIN, "update", "pj-1", "--add-label", "state:working", "--json")

    def test_other_elements_are_truncated(self) -> None:
        argv = safe_argv_of((FAKE_BD_BIN, "show", "p" * (ARGV_ELEMENT_CHARS + 50), "--json"))
        assert len(argv[2]) == ARGV_ELEMENT_CHARS


class TestStderrHead:
    """`stderr` が空なら stdout を `"stdout: "` 接頭辞で載せる（M-06）。"""

    def test_prefers_stderr(self) -> None:
        assert stderr_head_of("real error", "ignored") == "real error"

    def test_falls_back_to_stdout_with_a_prefix(self) -> None:
        stdout = '{"error": "no issues found matching the provided IDs"}'
        assert stderr_head_of("", stdout) == f"stdout: {stdout}"

    def test_empty_both_is_empty(self) -> None:
        assert stderr_head_of("", "") == ""

    def test_stdout_fallback_is_truncated(self) -> None:
        assert stderr_head_of("", "x" * (OUTPUT_HEAD_CHARS + 10)) == "stdout: " + "x" * (
            OUTPUT_HEAD_CHARS
        )
