"""申告の 4 道具（F08・T024・US5）。**表駆動**で書く。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from conftest import make_bundle, make_config, make_folders, make_member, make_stubs

from common.kernel.enums import PersonaKey, ToolName
from executor.models import RunSession
from executor.tools import TOOL_BUILDERS
from executor.tools.base import GuardedTool, ToolContext

DECLARE_TOOLS = (
    ToolName.propose_breakdown,
    ToolName.complete_issue,
    ToolName.escalate_to_human,
    ToolName.submit_review,
)

GOOD_ITEM = {
    "title": "子課題",
    "summary": "要約",
    "description": "本文",
    "acceptance": ["条件1"],
    "depends_on": [],
    "persona": PersonaKey.programmer.value,
}

VALID_ARGS: dict[ToolName, dict[str, Any]] = {
    ToolName.propose_breakdown: {"items": [GOOD_ITEM]},
    ToolName.complete_issue: {"reason": "できた"},
    ToolName.escalate_to_human: {
        "what_i_tried": "a",
        "why_stuck": "b",
        "decision_needed": "c",
    },
    ToolName.submit_review: {"approved": True, "feedback": "良い", "files_read": ["x.md"]},
}


def build_tool(tool_name: ToolName) -> tuple[GuardedTool, Any]:
    """申告の道具 1 つとスタブ束を組む。"""
    stubs = make_stubs()
    member = make_member()
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders())
    context = ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(proposal_schema="items: [{title, summary, ...}]"),
    )
    return TOOL_BUILDERS[tool_name](context), stubs


@pytest.mark.parametrize("tool_name", DECLARE_TOOLS)
def test_declaration_calls_no_contract(tool_name: ToolName) -> None:
    """4 道具とも**契約を 1 つも呼ばない**（判定器の前段を除く）。"""
    tool, stubs = build_tool(tool_name)
    tool.run(**VALID_ARGS[tool_name])
    called = {call.target for call in stubs.log.calls}
    assert called == {"Authorizer.enforce"}


@pytest.mark.parametrize("tool_name", DECLARE_TOOLS)
def test_declaration_writes_nothing_to_the_board(tool_name: ToolName) -> None:
    """盤面に 1 行も書かない（FR-049・SC-016）。"""
    tool, stubs = build_tool(tool_name)
    tool.run(**VALID_ARGS[tool_name])
    assert stubs.log.count("BoardWriter.comment") == 0
    assert stubs.log.count("BoardWriter.create") == 0
    assert stubs.log.count("BoardWriter.close") == 0


def test_board_writes_equal_the_number_of_progress_notes() -> None:
    """4 申告をすべて呼ぶ実行で、盤面への書込は `record_progress` の回数と等しい（SC-016）。"""
    stubs = make_stubs()
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
    for tool_name in DECLARE_TOOLS:
        TOOL_BUILDERS[tool_name](context).run(**VALID_ARGS[tool_name])
    progress = TOOL_BUILDERS[ToolName.record_progress](context)
    progress.run(note="1 回目")
    progress.run(note="2 回目")
    assert stubs.log.count("BoardWriter.comment") == 2
    assert session.progress_notes == 2


def test_propose_breakdown_stores_the_proposal() -> None:
    """`propose_breakdown` は `RunSession.proposal` に積むだけ。"""
    tool, _ = build_tool(ToolName.propose_breakdown)
    tool.run(**VALID_ARGS[ToolName.propose_breakdown])
    proposal = tool.ctx.session.proposal
    assert proposal is not None
    assert [item.title for item in proposal.items] == ["子課題"]


BROKEN_ITEMS: list[list[Any]] = [
    [{"title": "欠けている"}],
    [{**GOOD_ITEM, "acceptance": "文字列は list ではない"}],
    ["これは辞書ではない"],
]


@pytest.mark.parametrize(
    "items", BROKEN_ITEMS, ids=["missing-field", "wrong-type", "not-a-mapping"]
)
def test_propose_breakdown_returns_guidance_for_broken_shapes(items: list[Any]) -> None:
    """壊れた形は**例外にせず**指摘の文言にする（FR-050）。"""
    tool, _ = build_tool(ToolName.propose_breakdown)
    message = tool.run(items=items)
    assert message.startswith("検証に落ちました。")
    assert "propose_breakdown" in message
    assert tool.ctx.session.proposal is None


def test_propose_breakdown_does_not_judge_content() -> None:
    """**形の合否を判定しない**（件数 0 件でも中身が薄くても受理する＝ FR-050）。"""
    tool, _ = build_tool(ToolName.propose_breakdown)
    tool.run(items=[])
    proposal = tool.ctx.session.proposal
    assert proposal is not None
    assert proposal.items == []


def test_complete_issue_does_not_close() -> None:
    """`complete_issue` は完了させず、申告を積むだけ（FR-051）。"""
    tool, stubs = build_tool(ToolName.complete_issue)
    tool.run(reason="できた")
    assert tool.ctx.session.completed is True
    assert tool.ctx.session.completion_reason == "できた"
    assert stubs.log.count("BoardWriter.close") == 0


def test_escalate_to_human_does_not_create_an_issue() -> None:
    """`escalate_to_human` は起票しない（FR-052）。"""
    tool, stubs = build_tool(ToolName.escalate_to_human)
    tool.run(**VALID_ARGS[ToolName.escalate_to_human])
    escalation = tool.ctx.session.escalation
    assert escalation is not None
    assert escalation.what_i_tried == "a"
    assert stubs.log.count("BoardWriter.create") == 0


def test_submit_review_stores_the_verdict() -> None:
    """`submit_review` は判定を積むだけ（FR-053）。"""
    tool, _ = build_tool(ToolName.submit_review)
    tool.run(**VALID_ARGS[ToolName.submit_review])
    review = tool.ctx.session.review
    assert review is not None
    assert review.approved is True
    assert review.files_read == ["x.md"]


LastWinsCase = tuple[ToolName, dict[str, Any], dict[str, Any], str, Callable[[Any], bool]]

LAST_WINS_CASES: list[LastWinsCase] = [
    (
        ToolName.propose_breakdown,
        {"items": [GOOD_ITEM]},
        {"items": []},
        "proposal",
        lambda value: bool(value.items == []),
    ),
    (
        ToolName.complete_issue,
        {"reason": "一度目"},
        {"reason": "二度目"},
        "completion_reason",
        lambda value: bool(value == "二度目"),
    ),
    (
        ToolName.escalate_to_human,
        {"what_i_tried": "1", "why_stuck": "1", "decision_needed": "1"},
        {"what_i_tried": "2", "why_stuck": "2", "decision_needed": "2"},
        "escalation",
        lambda value: bool(value.what_i_tried == "2"),
    ),
    (
        ToolName.submit_review,
        {"approved": False, "feedback": "1", "files_read": []},
        {"approved": True, "feedback": "2", "files_read": []},
        "review",
        lambda value: bool(value.approved is True),
    ),
]


@pytest.mark.parametrize(("tool_name", "first", "second", "reader", "check"), LAST_WINS_CASES)
def test_calling_twice_lets_the_last_call_win(
    tool_name: ToolName,
    first: dict[str, Any],
    second: dict[str, Any],
    reader: str,
    check: Callable[[Any], bool],
) -> None:
    """同じ申告を 2 回呼ぶと**最後が勝つ**（追記ではなく代入＝ FR-053a）。"""
    tool, _ = build_tool(tool_name)
    tool.run(**first)
    tool.run(**second)
    assert check(getattr(tool.ctx.session, reader)) is True
