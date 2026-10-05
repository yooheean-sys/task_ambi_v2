"""分解の段（F08・FR-040〜FR-046a・SC-008・SC-016〜SC-018）。

**起票順序の不変条件**（起票 → 兄弟依存 → 付け替え → 確認読取 → 観測印）を表で押さえる。
"""

from __future__ import annotations

import pytest
from conftest import (
    Stubs,
    make_gate_result,
    make_issue,
    make_issue_run,
    make_outcome,
    make_ports,
    make_proposal,
    make_stage_spec,
    project_label,
)

from common.interfaces.board import BoardWriteUnverified, IssueSpec
from common.kernel.enums import Labels, PersonaKey
from orchestrator.models import (
    SINGLE_CHILD_NOTE,
    SINGLE_CHILD_OBSERVATION,
    CloseIntent,
    IssueDisposition,
    IssueRun,
    Retry,
    Settled,
)
from orchestrator.service import propose

ORDERED = (
    "BoardReader.children_of",
    "ProposalGate.validate_proposal",
    "BoardWriter.create",
    "BoardWriter.add_dependency",
    "BoardWriter.move_dependents",
    "BoardWriter.set_labels",
)


def _run(stubs: Stubs, *, children: int = 2, acceptance: str = "") -> IssueRun:
    """親 1 件と、2 件目が 1 件目に依存する提案（兄弟依存が 1 本張られる形）。"""
    parent = make_issue("bd-1", labels=[project_label()], acceptance=acceptance)
    stubs.state.add(parent)
    depends = [[] if i == 0 else [i - 1] for i in range(children)]
    proposal = make_proposal(children, depends=depends)
    return make_issue_run(
        issue=parent,
        spec=make_stage_spec(mode="propose", child_labels=["kind:child"]),
        outcome=make_outcome(proposal=proposal),
    )


# --------------------------------------------------------------------------------------
# 起票順序の不変条件（SC-016）
# --------------------------------------------------------------------------------------


def test_invariant_order_of_the_breakdown(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(
        order=(0, 1), observations=[SINGLE_CHILD_OBSERVATION]
    )
    ports = make_ports(stubs)
    run = _run(stubs)

    step = propose.handle(ports, run)

    assert stubs.log.only(*ORDERED) == [
        "BoardReader.children_of",
        "ProposalGate.validate_proposal",
        "BoardWriter.create",
        "BoardWriter.create",
        "BoardWriter.add_dependency",
        "BoardWriter.move_dependents",
        "BoardWriter.move_dependents",
        "BoardReader.children_of",
        "BoardWriter.set_labels",
    ]
    assert isinstance(step, CloseIntent)


def test_close_is_not_performed_by_propose(stubs: Stubs) -> None:
    """段別処理は `CloseIntent` を返すだけ。`close` は `kickoff.py` が締めの後に行う（C-1）。"""
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs))

    assert stubs.log.count("BoardWriter.close") == 0
    assert isinstance(step, CloseIntent)
    assert step.actor == "ai-planner"


def test_children_are_created_in_gate_order(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(order=(1, 0))
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs))

    titles = [call.kwargs["spec"].title for call in stubs.log.of("BoardWriter.create")]
    assert titles == ["子 1", "子 0"]


def test_child_labels_and_parent(stubs: Stubs) -> None:
    """段の子ラベル ＋ `project:` ＋ `persona:` ＋ `role:executor`・親は確保した課題（FR-044）。"""
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs, children=1))

    spec: IssueSpec = stubs.log.of("BoardWriter.create")[0].kwargs["spec"]
    assert set(spec.labels) == {
        "kind:child",
        project_label(),
        Labels.persona(PersonaKey.planner),
        Labels.role_executor(),
    }
    assert spec.parent == "bd-1"


def test_create_uses_the_member_actor(stubs: Stubs) -> None:
    """子の起票は **AI の行為**（FR-046a・SC-028）。"""
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs, children=1))

    assert stubs.log.of("BoardWriter.create")[0].kwargs["actor"] == "ai-planner"
    assert ports.config.system_actor == "system"


def test_single_child_is_not_rejected_and_gets_the_note(stubs: Stubs) -> None:
    """子が 1 件でも拒否せず観測の印を 1 回付ける（SC-018）。"""
    stubs.proposal_gate.default_result = make_gate_result(
        order=(0,), observations=[SINGLE_CHILD_OBSERVATION]
    )
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, children=1))

    assert isinstance(step, CloseIntent)
    assert stubs.log.count("BoardWriter.set_labels") == 1
    call = stubs.log.of("BoardWriter.set_labels")[0]
    assert call.kwargs["add"] == [SINGLE_CHILD_NOTE]
    assert call.kwargs["remove"] == []


def test_no_single_child_observation_means_no_label(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(order=(0, 1), observations=[])
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs))

    assert stubs.log.count("BoardWriter.set_labels") == 0


# --------------------------------------------------------------------------------------
# ガード（FR-040・SC-008）
# --------------------------------------------------------------------------------------


def test_existing_children_hand_off_without_any_create(stubs: Stubs) -> None:
    stubs.state.children["bd-1"] = [make_issue("bd-2"), make_issue("bd-3")]
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs))

    assert stubs.log.count("BoardWriter.create") == 0
    assert stubs.log.count("ProposalGate.validate_proposal") == 0
    assert isinstance(step, Settled)
    assert step.disposition is IssueDisposition.handed_off
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "停止[guard_children]" in body


# --------------------------------------------------------------------------------------
# 4 つの失敗点（SC-017・FR-046）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "nth"),
    [
        ("BoardWriter.create", 2),
        ("BoardWriter.add_dependency", 1),
        ("BoardWriter.move_dependents", 1),
        ("BoardReader.children_of", 2),
    ],
)
def test_four_failure_points_hand_off_unsafe_without_retry(
    stubs: Stubs, target: str, nth: int
) -> None:
    stubs.proposal_gate.default_result = make_gate_result(order=(0, 1))
    if target.startswith("BoardReader"):
        stubs.board_reader.failures.fail(target, nth)
    else:
        stubs.board_writer.failures.fail(target, nth)
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs))

    assert isinstance(step, Settled)
    assert step.disposition is IssueDisposition.handed_off_unsafe
    assert stubs.log.count(target) == nth  # やり直しが 0 回
    assert stubs.log.count("BoardWriter.close") == 0
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert body.startswith("[orchestrator] 停止[unsafe]")


def test_unsafe_body_lists_the_finished_work(stubs: Stubs) -> None:
    stubs.board_writer.failures.fail("BoardWriter.create", 2)
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs))

    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "作成済みの子: bd-101" in body
    assert "失敗した処理" in body


@pytest.mark.parametrize(
    ("nth", "expected"),
    [(1, "付け替え: 未実行"), (2, "付け替え: 一部のみ: bd-101")],
    ids=["1 件目で失敗", "2 件目で失敗"],
)
def test_partly_moved_dependents_are_named_in_the_unsafe_body(
    stubs: Stubs, nth: int, expected: str
) -> None:
    """付け替えの**部分成功**を本文に残す（contracts §3・T092）。

    真偽 1 つで表していたときは 2 件目の失敗でも `未実行` と出て、「1 件目は済んでいる」
    情報が引き継ぐ人間に届かなかった。
    """
    stubs.proposal_gate.default_result = make_gate_result(order=(0, 1))
    stubs.board_writer.failures.fail("BoardWriter.move_dependents", nth)
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs))

    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert expected in body


def test_write_unverified_is_also_unsafe(stubs: Stubs) -> None:
    stubs.board_writer.failures.fail(
        "BoardWriter.create", 1, BoardWriteUnverified("読み直せない", issue_id="bd-1", mismatch="x")
    )
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs))

    assert isinstance(step, Settled)
    assert step.disposition is IssueDisposition.handed_off_unsafe


# --------------------------------------------------------------------------------------
# 検査の指摘と循環（FR-041・FR-043）
# --------------------------------------------------------------------------------------


def test_gate_problems_become_a_retry(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(ok=False, problems=["受入条文が空"])
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs))

    assert isinstance(step, Retry)
    assert "受入条文が空" in step.feedback
    assert stubs.log.count("BoardWriter.create") == 0


def test_empty_order_creates_nothing_and_retries(stubs: Stubs) -> None:
    """順が返らなければ（循環など）起票しない（FR-043）。"""
    stubs.proposal_gate.default_result = make_gate_result(order=())
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs))

    assert isinstance(step, Retry)
    assert stubs.log.count("BoardWriter.create") == 0


def test_missing_proposal_retries_once(stubs: Stubs) -> None:
    ports = make_ports(stubs)
    run = make_issue_run(
        issue=make_issue("bd-1", labels=[project_label()]),
        spec=make_stage_spec(mode="propose"),
        outcome=make_outcome(),
    )
    stubs.state.add(run.issue)

    step = propose.handle(ports, run)

    assert isinstance(step, Retry)
    assert stubs.log.count("ProposalGate.validate_proposal") == 0
