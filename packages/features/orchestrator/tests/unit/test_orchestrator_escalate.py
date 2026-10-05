"""エスカレーション（F10・FR-062〜FR-065・SC-025・SC-026・UC-04）。"""

from __future__ import annotations

import pytest
from conftest import (
    Stubs,
    make_escalation,
    make_issue,
    make_issue_run,
    make_outcome,
    make_ports,
    make_stage_spec,
    project_label,
)

from common.interfaces.board import BoardUnavailable, BoardWriteUnverified, IssueSpec
from common.kernel.enums import KindLabel, Labels, StateLabel
from orchestrator.models import IssueDisposition, IssueRun, Settled
from orchestrator.service import execute


def _run(stubs: Stubs) -> IssueRun:
    issue = make_issue("bd-1", title="設計を決める", labels=[project_label()])
    stubs.state.add(issue)
    return make_issue_run(
        issue=issue,
        spec=make_stage_spec(mode="execute"),
        outcome=make_outcome(escalation=make_escalation()),
    )


def test_existing_open_question_is_reused_without_creating(stubs: Stubs) -> None:
    """未回答の質問があれば**起票 0 回**で再利用する（SC-025・FR-062）。"""
    stubs.state.questions["bd-1"] = make_issue("bd-9", labels=["kind:question"])
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert stubs.log.count("BoardWriter.create") == 0
    assert stubs.log.of("BoardWriter.add_dependency")[0].kwargs["to_id"] == "bd-9"
    assert isinstance(step, Settled)
    assert step.disposition is IssueDisposition.escalated


def test_question_is_created_when_absent(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    assert stubs.log.count("BoardWriter.create") == 1
    spec: IssueSpec = stubs.log.of("BoardWriter.create")[0].kwargs["spec"]
    assert spec.title.startswith("[要判断]")
    assert set(spec.labels) == {Labels.kind(KindLabel.question), project_label()}
    assert spec.parent is None


def test_question_body_has_the_three_items(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    body: str = stubs.log.of("BoardWriter.create")[0].kwargs["spec"].description
    assert "試したこと" in body
    assert "詰まった理由" in body
    assert "決めてほしいこと" in body
    assert "A と B を試した" in body
    assert "C が決まらない" in body
    assert "C を決めてほしい" in body


def test_waiting_is_expressed_with_a_dependency_not_a_parent(stubs: Stubs) -> None:
    """待たせるのは依存の追加。**親子の指定を 0 回**使う（SC-026・P5D-059 (2)）。"""
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    created: IssueSpec = stubs.log.of("BoardWriter.create")[0].kwargs["spec"]
    assert created.parent is None
    dependency = stubs.log.of("BoardWriter.add_dependency")[0]
    assert dependency.kwargs["from_id"] == "bd-1"
    assert dependency.kwargs["to_id"].startswith("bd-")
    assert stubs.log.count("BoardWriter.move_dependents") == 0


def test_order_is_create_then_dependency_then_release(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    assert stubs.log.only(
        "BoardReader.open_question_of",
        "BoardWriter.create",
        "BoardWriter.add_dependency",
        "BoardWriter.release",
    ) == [
        "BoardReader.open_question_of",
        "BoardWriter.create",
        "BoardWriter.add_dependency",
        "BoardWriter.release",
    ]


def test_actors_split_between_ai_and_system(stubs: Stubs) -> None:
    """起票は `member.bd_actor`・解放は `system_actor`（SC-028）。"""
    ports = make_ports(stubs, system_actor="sysbot")

    execute.handle(ports, _run(stubs))

    assert stubs.log.of("BoardWriter.create")[0].kwargs["actor"] == "ai-planner"
    assert stubs.log.of("BoardWriter.release")[0].kwargs["actor"] == "sysbot"


def test_escalation_does_not_close_or_run_the_executor(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    assert stubs.log.count("BoardWriter.close") == 0
    assert stubs.log.count("CrewExecutor.run") == 0


# --------------------------------------------------------------------------------------
# 盤面の契約例外を呼び手へ漏らさない（FR-003・SC-003・T078）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "error", "linked"),
    [
        (
            "BoardWriter.create",
            BoardWriteUnverified("読み直せない", issue_id="bd-101", mismatch="x"),
            False,
        ),
        ("BoardWriter.add_dependency", None, False),
        ("BoardWriter.release", None, True),
    ],
    ids=["create", "add_dependency", "release"],
)
def test_board_failures_hand_off_instead_of_escaping(
    stubs: Stubs, target: str, error: BoardWriteUnverified | None, linked: bool
) -> None:
    """3 手のどこで失敗しても**例外を漏らさず** `unsafe` として人間へ渡す（FR-046・D01-b）。"""
    stubs.board_writer.failures.fail(target, 1, error)
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert isinstance(step, Settled)
    assert step.disposition is IssueDisposition.handed_off_unsafe
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "停止[unsafe]" in body
    assert f"- 依存: {'張れた' if linked else '未実行'}" in body
    labels = stubs.log.of("BoardWriter.set_labels")[0].kwargs
    assert labels["add"] == [Labels.state(StateLabel.failing)]


def test_a_partly_created_question_is_named_in_the_hand_off(stubs: Stubs) -> None:
    """質問だけ起票して依存が張れなかったときは、その ID を本文に残す（FN-49）。"""
    stubs.board_writer.failures.fail(
        "BoardWriter.add_dependency",
        1,
        BoardUnavailable("届かない", argv=["bd"], rc=1, stderr_head="x", attempts=1),
    )
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    created: str = stubs.log.of("BoardWriter.create")[0].kwargs["spec"].title
    assert created.startswith("[要判断]")
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "- 質問課題: bd-101" in body
    assert "BoardUnavailable" in body
