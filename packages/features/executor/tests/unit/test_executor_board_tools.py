"""盤面の 2 道具（F07・T019・US4）。"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import make_bundle, make_config, make_folders, make_member, make_stubs

from common.interfaces.board import BoardUnavailable, IssueNotFound
from common.kernel.enums import ToolName
from executor.models import RunSession
from executor.tools import TOOL_BUILDERS
from executor.tools.base import GuardedTool, ToolContext

BOARD_ERRORS: dict[str, BaseException] = {
    "unavailable": BoardUnavailable(
        "bd に届かない", argv=["bd"], rc=1, stderr_head="x", attempts=3
    ),
    "not-found": IssueNotFound("その課題は無い", issue_id="bd-9"),
}


def build_tool(
    tool_name: ToolName,
    *,
    show_raises: BaseException | None = None,
    comment_raises: BaseException | None = None,
    authorizer_mode: str = "allow",
) -> tuple[GuardedTool, Any]:
    """道具 1 つとスタブ束を組む。"""
    stubs = make_stubs(authorizer_mode=authorizer_mode)  # pyright: ignore[reportArgumentType]  # テスト内で Literal を跨いで渡すため
    stubs.board_reader.raises = show_raises
    stubs.board_writer.raises = comment_raises
    member = make_member()
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders())
    context = ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(),
    )
    return TOOL_BUILDERS[tool_name](context), stubs


def test_read_issue_binds_the_issue_id() -> None:
    """`read_issue` は `show(issue_id)` を呼び、**AI に `issue_id` を渡させない**。"""
    tool, stubs = build_tool(ToolName.read_issue)
    result = tool.run()
    assert "題" in result
    (call,) = stubs.log.of("BoardReader.show")
    assert call.kwargs == {"issue_id": "bd-1"}
    assert set(tool.args_schema.model_fields) == set()


def test_record_progress_passes_actor() -> None:
    """`record_progress` は `comment(issue_id, note, actor=member.bd_actor)` を呼ぶ。"""
    tool, stubs = build_tool(ToolName.record_progress)
    tool.run(note="進みました")
    (call,) = stubs.log.of("BoardWriter.comment")
    assert call.kwargs == {
        "issue_id": "bd-1",
        "body": "進みました",
        "actor": make_member().bd_actor,
    }
    assert set(tool.args_schema.model_fields) == {"note"}


@pytest.mark.parametrize("error_key", list(BOARD_ERRORS), ids=list(BOARD_ERRORS))
def test_read_issue_translates_both_contract_errors(error_key: str) -> None:
    """`BoardUnavailable`・`IssueNotFound` の**両方**が例外 0 件で文言になる（C3）。"""
    tool, _ = build_tool(ToolName.read_issue, show_raises=BOARD_ERRORS[error_key])
    message = tool.run()
    assert isinstance(message, str)
    assert message.startswith("ERROR:")
    assert "escalate_to_human" in message


@pytest.mark.parametrize("error_key", list(BOARD_ERRORS), ids=list(BOARD_ERRORS))
def test_record_progress_translates_both_contract_errors(error_key: str) -> None:
    """書込側も両方の例外を文言にする（C3）。"""
    tool, _ = build_tool(ToolName.record_progress, comment_raises=BOARD_ERRORS[error_key])
    message = tool.run(note="x")
    assert message.startswith("ERROR:")
    assert "escalate_to_human" in message


def test_the_two_board_errors_have_distinct_wording() -> None:
    """**文面は分ける**（届かない／その課題が無い＝ C3）。"""
    unavailable, _ = build_tool(ToolName.read_issue, show_raises=BOARD_ERRORS["unavailable"])
    not_found, _ = build_tool(ToolName.read_issue, show_raises=BOARD_ERRORS["not-found"])
    assert unavailable.run() != not_found.run()


def test_progress_notes_count_only_successes() -> None:
    """`progress_notes` は**成功時のみ**加算（拒否・失敗は数えない＝ FR-044a）。"""
    tool, _ = build_tool(ToolName.record_progress)
    for _ in range(3):
        tool.run(note="x")
    assert tool.ctx.session.progress_notes == 3


def test_progress_notes_not_counted_on_failure() -> None:
    """失敗した書込は数えない。"""
    tool, _ = build_tool(ToolName.record_progress, comment_raises=BOARD_ERRORS["unavailable"])
    tool.run(note="x")
    assert tool.ctx.session.progress_notes == 0


def test_progress_notes_not_counted_on_denial() -> None:
    """拒否された書込は数えない。"""
    tool, stubs = build_tool(ToolName.record_progress, authorizer_mode="deny")
    tool.run(note="x")
    assert tool.ctx.session.progress_notes == 0
    assert stubs.log.count("BoardWriter.comment") == 0


def test_no_close_or_create_tools_exist() -> None:
    """完了・起票・依存・ラベルの道具は **0 件**（FR-043・US4 AC-8）。"""
    forbidden = {"close_issue", "create_issue", "add_dependency", "set_labels", "claim_next"}
    assert forbidden & {name.value for name in ToolName} == set()
    assert forbidden & set(TOOL_BUILDERS) == set()
