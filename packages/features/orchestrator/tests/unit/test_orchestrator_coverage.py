"""親条文の被覆検査（FR-058〜FR-061・SC-021・SC-022・SC-022a・B-25）。

抽出は `ProposalGate.extract_outcome_tokens` に委ね、orchestrator が持つのは
**正準形の接頭辞の定数 1 つ**だけである（FR-061）。
"""

from __future__ import annotations

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

from orchestrator.models import OUTCOME_PREFIX, CloseIntent, IssueRun, Retry
from orchestrator.service import propose

PARENT_TWO = "outcome/a.md と outcome/b.md を作る"
CHILD_A = "outcome/a.md"
CHILD_B = "outcome/b.md"


def _run(stubs: Stubs, parent_acceptance: str, child_acceptance: list[list[str]]) -> IssueRun:
    parent = make_issue("bd-1", labels=[project_label()], acceptance=parent_acceptance)
    stubs.state.add(parent)
    proposal = make_proposal(len(child_acceptance), acceptance=child_acceptance)
    return make_issue_run(
        issue=parent,
        spec=make_stage_spec(mode="propose", child_labels=["kind:child"]),
        outcome=make_outcome(proposal=proposal),
    )


def _tokens(stubs: Stubs, mapping: dict[str, list[str]]) -> None:
    stubs.proposal_gate.tokens.update(mapping)


# --------------------------------------------------------------------------------------
# 欠けの検出（SC-021）
# --------------------------------------------------------------------------------------


def test_missing_coverage_becomes_a_retry(stubs: Stubs) -> None:
    """親 2 件・子 1 件しか覆わない → 欠け 1 件が差し戻しの指摘に載る。"""
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    _tokens(
        stubs,
        {PARENT_TWO: [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"], CHILD_A: [CHILD_A]},
    )
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, PARENT_TWO, [[CHILD_A]]))

    assert isinstance(step, Retry)
    assert "b.md" in step.feedback
    assert "a.md" not in step.feedback
    assert stubs.log.count("BoardWriter.create") == 0


def test_full_coverage_proceeds_to_create(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(order=(0, 1))
    _tokens(
        stubs,
        {
            PARENT_TWO: [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"],
            CHILD_A: [CHILD_A],
            CHILD_B: [CHILD_B],
        },
    )
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, PARENT_TWO, [[CHILD_A], [CHILD_B]]))

    assert isinstance(step, CloseIntent)
    assert stubs.log.count("BoardWriter.create") == 2


# --------------------------------------------------------------------------------------
# 何も言わない場合（SC-022・SC-022a・FR-058・FR-060）
# --------------------------------------------------------------------------------------


def test_parent_without_tokens_says_nothing(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    _tokens(stubs, {"成果物の指定なし": []})
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, "成果物の指定なし", [[]]))

    assert isinstance(step, CloseIntent)


def test_parent_tokens_outside_the_canonical_prefix_are_ignored(stubs: Stubs) -> None:
    """接頭辞で始まらない親のトークンは突合の対象にしない（SC-022a）。

    人間が書いた条文の書式違反を分解した側へ差し戻さない（PoC 継承）。
    """
    parent = "../outside.md と /abs/path.md を作る"
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    _tokens(stubs, {parent: ["../outside.md", "/abs/path.md"]})
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, parent, [[]]))

    assert isinstance(step, CloseIntent)


# --------------------------------------------------------------------------------------
# 突合の鍵と抽出の委譲（FR-058・FR-061）
# --------------------------------------------------------------------------------------


def test_match_key_is_the_name_after_the_prefix(stubs: Stubs) -> None:
    """親と子で**接頭辞を剥がした残り**が一致すれば覆われたと見る。"""
    parent = "outcome/report.md"
    child = "outcome/report.md を書く"
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    _tokens(stubs, {parent: [f"{OUTCOME_PREFIX}report.md"], child: [f"{OUTCOME_PREFIX}report.md"]})
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, parent, [[child]]))

    assert isinstance(step, CloseIntent)


def test_extraction_is_delegated_to_the_gate(stubs: Stubs) -> None:
    """親 1 回 ＋ 子の件数ぶん、`extract_outcome_tokens` に委ねる（独自の抽出規則を持たない）。"""
    stubs.proposal_gate.default_result = make_gate_result(order=(0, 1))
    _tokens(
        stubs,
        {
            PARENT_TWO: [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"],
            CHILD_A: [CHILD_A],
            CHILD_B: [CHILD_B],
        },
    )
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs, PARENT_TWO, [[CHILD_A], [CHILD_B]]))

    calls = stubs.log.of("ProposalGate.extract_outcome_tokens")
    assert [call.kwargs["acceptance"] for call in calls] == [PARENT_TWO, CHILD_A, CHILD_B]


def test_child_acceptance_items_are_joined_by_newline(stubs: Stubs) -> None:
    """子の受入条文は各項を改行で連結して 1 回だけ抽出に掛ける。"""
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    joined = f"{CHILD_A}\n{CHILD_B}"
    _tokens(
        stubs,
        {
            PARENT_TWO: [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"],
            joined: [CHILD_A, CHILD_B],
        },
    )
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, PARENT_TWO, [[CHILD_A, CHILD_B]]))

    assert isinstance(step, CloseIntent)


# --------------------------------------------------------------------------------------
# 実施位置（DIFF-01・B-25）
# --------------------------------------------------------------------------------------


def test_coverage_runs_after_the_gate_and_before_create(stubs: Stubs) -> None:
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    _tokens(
        stubs,
        {PARENT_TWO: [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"], CHILD_A: [CHILD_A]},
    )
    ports = make_ports(stubs)

    propose.handle(ports, _run(stubs, PARENT_TWO, [[CHILD_A]]))

    assert stubs.log.only(
        "ProposalGate.validate_proposal",
        "ProposalGate.extract_outcome_tokens",
        "BoardWriter.create",
    ) == [
        "ProposalGate.validate_proposal",
        "ProposalGate.extract_outcome_tokens",
        "ProposalGate.extract_outcome_tokens",
    ]


def test_coverage_is_skipped_when_the_gate_rejects(stubs: Stubs) -> None:
    """検査に落ちた提案には被覆を掛けない（合格後に行う）。"""
    stubs.proposal_gate.default_result = make_gate_result(ok=False, problems=["形が違う"])
    ports = make_ports(stubs)

    step = propose.handle(ports, _run(stubs, PARENT_TWO, [[CHILD_A]]))

    assert isinstance(step, Retry)
    assert stubs.log.count("ProposalGate.extract_outcome_tokens") == 0
