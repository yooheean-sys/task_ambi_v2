"""完了ゲートとレビュー（F09・FR-051〜FR-057・SC-023・SC-023a・SC-024）。"""

from __future__ import annotations

import pytest
from conftest import (
    Stubs,
    make_issue,
    make_issue_run,
    make_member,
    make_outcome,
    make_ports,
    make_review,
    make_stage_spec,
    project_label,
)

from common.interfaces.executor import CrewSpec, ReviewVerdict
from common.interfaces.settings import Member, MemberNotFound
from common.interfaces.stages import ContextRefs, PromptPurpose
from common.interfaces.vault import VaultNotFound, VaultPathViolation
from common.kernel.enums import PersonaKey
from orchestrator.models import (
    OUTCOME_PREFIX,
    CloseIntent,
    IssueDisposition,
    IssueRun,
    Retry,
    Settled,
)
from orchestrator.service import execute

TWO = "outcome/a.md と outcome/b.md"


def _run(
    stubs: Stubs,
    *,
    acceptance: str = TWO,
    completed: bool = True,
    review: ReviewVerdict | None = None,
    parent: str | None = None,
    member: Member | None = None,
) -> IssueRun:
    issue = make_issue("bd-1", labels=[project_label()], acceptance=acceptance, parent=parent)
    stubs.state.add(issue)
    return make_issue_run(
        issue=issue,
        spec=make_stage_spec(mode="execute"),
        member=member,
        outcome=make_outcome(completed=completed, review=review),
    )


def _gate_tokens(stubs: Stubs, acceptance: str, tokens: list[str]) -> None:
    stubs.proposal_gate.tokens[acceptance] = tokens


# --------------------------------------------------------------------------------------
# 実在確認（SC-023・SC-023a）
# --------------------------------------------------------------------------------------


def test_all_outcomes_exist_then_review(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"])
    stubs.vault_reader.existing = {"a.md", "b.md"}
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert stubs.log.count("VaultReader.outcome_exists") == 2
    assert stubs.log.count("CrewExecutor.run") == 1
    assert isinstance(step, CloseIntent)


def test_one_missing_outcome_skips_review(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [f"{OUTCOME_PREFIX}a.md", f"{OUTCOME_PREFIX}b.md"])
    stubs.vault_reader.existing = {"a.md"}
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert stubs.log.count("CrewExecutor.run") == 0
    assert isinstance(step, Retry)
    assert "b.md" in step.feedback


def test_token_without_the_prefix_is_rejected_without_asking_the_vault(stubs: Stubs) -> None:
    """接頭辞で始まらないトークンは**実在確認を呼ばずに**不合格（SC-023a・FR-052）。"""
    _gate_tokens(stubs, TWO, ["../outside.md"])
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert stubs.log.count("VaultReader.outcome_exists") == 0
    assert isinstance(step, Retry)
    assert "../outside.md" in step.feedback


def test_the_value_passed_to_the_vault_has_the_prefix_stripped(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [f"{OUTCOME_PREFIX}sub/a.md"])
    stubs.vault_reader.existing = {"sub/a.md"}
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs))

    assert stubs.log.of("VaultReader.outcome_exists")[0].kwargs["filename"] == "sub/a.md"


def test_path_violation_becomes_feedback_not_an_exception(stubs: Stubs) -> None:
    """規則違反は**呼び手へ例外を漏らさず**差し戻しの指摘に載せる（FR-052）。"""
    _gate_tokens(stubs, TWO, [f"{OUTCOME_PREFIX}a.md"])
    stubs.vault_reader.failures.fail(
        "VaultReader.outcome_exists",
        1,
        VaultPathViolation("規則違反", kind="forbidden_name", detail="`:` は使えない"),
    )
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert isinstance(step, Retry)
    assert "forbidden_name" in step.feedback
    assert stubs.log.count("CrewExecutor.run") == 0


def test_vault_not_found_becomes_feedback_not_an_exception(stubs: Stubs) -> None:
    """保管庫が「無い」で答えても**呼び手へ例外を漏らさず**差し戻しの指摘に載せる（FR-052）。

    `outcome_exists` の `VaultNotFound` は意味としても「成果物が無い」＝ `not exists` と
    同じ帰結なので、`VaultPathViolation` と**同じ 2 例外の組**（前処理の
    `prepare._prepare` と同形）で受ける。
    """
    _gate_tokens(stubs, TWO, [f"{OUTCOME_PREFIX}a.md"])
    stubs.vault_reader.failures.fail(
        "VaultReader.outcome_exists",
        1,
        VaultNotFound("成果物が無い", rel_path="pj/alpha/outcome/a.md", role=None),
    )
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert isinstance(step, Retry)
    assert "a.md" in step.feedback
    assert stubs.log.count("CrewExecutor.run") == 0


def test_no_tokens_goes_straight_to_review(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [])
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert stubs.log.count("VaultReader.outcome_exists") == 0
    assert isinstance(step, CloseIntent)


# --------------------------------------------------------------------------------------
# レビュー（SC-024・FR-054・FR-055・FR-057）
# --------------------------------------------------------------------------------------


def test_review_rejection_is_a_retry_without_close(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [])
    stubs.crew_executor.default_outcome = make_outcome(
        review=make_review(approved=False, feedback="節が足りない")
    )
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert isinstance(step, Retry)
    assert "節が足りない" in step.feedback
    assert stubs.log.count("BoardWriter.close") == 0


def test_review_uses_the_configured_persona(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [])
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())
    ports = make_ports(stubs, review_persona="architect")

    execute.handle(ports, _run(stubs))

    resolves = stubs.log.of("MemberResolver.resolve")
    assert resolves[-1].kwargs["persona_key"] is PersonaKey.architect
    spec: CrewSpec = stubs.log.of("CrewExecutor.run")[0].kwargs["spec"]
    assert spec.purpose is PromptPurpose.review
    assert spec.member.persona_key is PersonaKey.architect


@pytest.mark.parametrize(
    ("parent", "expected"), [(None, False), ("bd-0", True)], ids=["親なし", "親あり"]
)
def test_is_final_review_follows_the_parent_acceptance(
    stubs: Stubs, parent: str | None, expected: bool
) -> None:
    """`is_final_review` は**親の受入条文の有無**で決まる（D16-a）。"""
    _gate_tokens(stubs, TWO, [])
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())
    if parent is not None:
        stubs.state.add(make_issue(parent, acceptance="親の条文"))
    ports = make_ports(stubs)

    execute.handle(ports, _run(stubs, parent=parent))

    ctx: ContextRefs = stubs.log.of("StageCatalog.build_prompt")[0].kwargs["ctx"]
    assert ctx.is_final_review is expected
    assert ctx.purpose is PromptPurpose.review
    assert ctx.input_refs == []


def test_reviewer_not_in_the_ledger_hands_off(stubs: Stubs) -> None:
    """レビュー担当が台帳に無ければ人間へ渡す（FR-057）。"""
    _gate_tokens(stubs, TWO, [])
    stubs.member_resolver.failures.fail("MemberResolver.resolve", 1, MemberNotFound("台帳に無い"))
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert isinstance(step, Settled)
    assert step.disposition is IssueDisposition.handed_off
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "停止[config_missing]" in body
    assert "MemberNotFound" in body


def test_missing_review_verdict_is_a_retry(stubs: Stubs) -> None:
    _gate_tokens(stubs, TWO, [])
    stubs.crew_executor.default_outcome = make_outcome()
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs))

    assert isinstance(step, Retry)


@pytest.mark.parametrize(
    ("member", "reviewer", "expected"),
    [
        (None, None, "ai-planner"),
        (
            make_member(PersonaKey.programmer, bd_actor="ai-programmer"),
            make_member(PersonaKey.planner, bd_actor="ai-reviewer"),
            "ai-programmer",
        ),
    ],
    ids=["作業メンバ＝レビュー担当", "作業メンバ≠レビュー担当"],
)
def test_close_intent_uses_the_member_actor(
    stubs: Stubs, member: Member | None, reviewer: Member | None, expected: str
) -> None:
    """`CloseIntent.actor` は**作業メンバ**の `bd_actor`（レビュー担当のものではない）。

    2 行目は両者の `bd_actor` を分ける。既定（`make_member` の `planner`／`ai-planner`）は
    `review_persona="planner"` と一致してしまい、どちらの actor かを判別できない。
    """
    _gate_tokens(stubs, TWO, [])
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())
    if reviewer is not None:
        stubs.member_resolver.members[PersonaKey.planner] = reviewer
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs, member=member))

    assert isinstance(step, CloseIntent)
    assert step.actor == expected
    assert stubs.log.count("BoardWriter.close") == 0


# --------------------------------------------------------------------------------------
# 申告も失敗も無い（FR-056）
# --------------------------------------------------------------------------------------


def test_no_completion_and_no_failure_retries_once(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    step = execute.handle(ports, _run(stubs, completed=False))

    assert isinstance(step, Retry)
    assert stubs.log.count("VaultReader.outcome_exists") == 0
    assert stubs.log.count("CrewExecutor.run") == 0
