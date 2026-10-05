"""提案の検査（`validate_proposal`。IF-46・stages-F04／F06・US3）。

正本は `specs/006-stages/contracts/proposal-gate.md` の §1・§2。
**例外を 1 つも送出しない**（FR-032）・**途中で打ち切らない**（FR-033）。
"""

from __future__ import annotations

import pytest
from conftest import make_acceptance_rules, make_item, make_proposal, make_service, stage_spec

from common.interfaces.stages import Proposal, ProposalItem, StageSpec
from common.kernel.enums import PersonaKey, StageName

SUBREQUEST = stage_spec(StageName.subrequest)
PROGRAM = stage_spec(StageName.program)
PROJECT = stage_spec(StageName.project)

OK_ACCEPTANCE = ["- [ ] outcome/a.md にまとめる"]


def _run(proposal: Proposal, spec: StageSpec = PROJECT, existing: list[str] | None = None):
    return make_service().validate_proposal(proposal, spec, [] if existing is None else existing)


def test_min_items_shortfall_is_reported() -> None:
    """US3 AC-1: 件数不足を指摘する。"""
    spec = PROJECT.model_copy(update={"min_items": 3})
    result = make_service().validate_proposal(make_proposal([make_item()]), spec, [])
    assert result.ok is False
    assert any("件" in p for p in result.problems)


def test_min_items_shortfall_does_not_short_circuit() -> None:
    """SD-49 (b): 件数不足でも**早期 return しない**（他の違反も同時に返る）。"""
    spec = PROJECT.model_copy(update={"min_items": 3})
    result = make_service().validate_proposal(make_proposal([make_item(title="")]), spec, [])
    assert len(result.problems) >= 2
    assert any("title" in p for p in result.problems)


EMPTY_FIELD_CASES: tuple[tuple[str, str], ...] = (
    ("title", ""),
    ("title", "tbd"),
    ("title", "  なし  "),
    ("summary", ""),
    ("summary", "-"),
    ("description", ""),
    ("description", "未定"),
)


def _item_with(field: str, value: str) -> ProposalItem:
    """指定のフィールドだけを壊した 1 件を作る（動的キーワードを使わない）。"""
    if field == "title":
        return make_item(title=value, acceptance=OK_ACCEPTANCE)
    if field == "summary":
        return make_item(summary=value, acceptance=OK_ACCEPTANCE)
    return make_item(description=value, acceptance=OK_ACCEPTANCE)


@pytest.mark.parametrize(
    ("field", "value"), EMPTY_FIELD_CASES, ids=[f"{f}-{v!r}" for f, v in EMPTY_FIELD_CASES]
)
def test_placeholder_or_empty_fields_are_reported(field: str, value: str) -> None:
    """US3 AC-4・FR-036: 題名・要約・説明が空／穴埋め文言なら指摘する。"""
    item = _item_with(field, value)
    result = _run(make_proposal([item, make_item(title="別", acceptance=OK_ACCEPTANCE)]))
    assert any(f"1件目 の {field}" in p for p in result.problems)


def test_persona_outside_allowed_is_reported_not_raised() -> None:
    """US3 AC-2: 段が許さない担当は**指摘**（例外にしない）。"""
    item = make_item(persona=PersonaKey.planner, acceptance=OK_ACCEPTANCE)
    result = _run(make_proposal([item]), SUBREQUEST)
    assert any("persona" in p for p in result.problems)


def test_persona_unset_is_not_reported() -> None:
    """US3 AC-3・FR-035: 未指定（`None`）は指摘しない。"""
    result = _run(make_proposal([make_item(persona=None, acceptance=OK_ACCEPTANCE)]), SUBREQUEST)
    assert not any("persona" in p for p in result.problems)


def test_persona_inside_allowed_is_not_reported() -> None:
    result = _run(
        make_proposal([make_item(persona=PersonaKey.writer, acceptance=OK_ACCEPTANCE)]), SUBREQUEST
    )
    assert not any("persona" in p for p in result.problems)


def test_depends_on_forbidden_stage_is_reported() -> None:
    """US3 AC-7・FR-040: 依存を許さない段（`program`）で依存が書かれていれば指摘。"""
    items = [
        make_item(title="a", acceptance=OK_ACCEPTANCE),
        make_item(title="b", depends_on=[0], acceptance=OK_ACCEPTANCE),
    ]
    result = _run(make_proposal(items), PROGRAM)
    assert any("2件目 の depends_on" in p for p in result.problems)


def test_empty_acceptance_is_reported_and_stops_that_item_only() -> None:
    """§2 a: 条文が空なら指摘し、**その 1 件の条文検査だけ**を打ち切る。"""
    items = [make_item(title="a", acceptance=[]), make_item(title="b", acceptance=["短い"])]
    result = _run(make_proposal(items))
    first = [p for p in result.problems if p.startswith("1件目")]
    second = [p for p in result.problems if p.startswith("2件目")]
    assert len(first) == 1
    assert len(second) >= 1


def test_max_acceptance_is_checked_only_when_set() -> None:
    """§2 b: `max_acceptance` が `None` なら件数を検査しない。"""
    acceptance = [f"- [ ] outcome/f{i}.md にまとめる" for i in range(4)]
    unlimited = _run(make_proposal([make_item(acceptance=acceptance)]))
    limited = make_service().validate_proposal(
        make_proposal([make_item(acceptance=acceptance)]),
        PROJECT.model_copy(update={"max_acceptance": 2}),
        [],
    )
    assert unlimited.ok is True
    assert any("acceptance" in p for p in limited.problems)


ACCEPTANCE_CASES: tuple[tuple[list[str], str], ...] = (
    (["- [ ] tbd"], "プレースホルダ"),
    (["- [ ] 　なし　"], "プレースホルダ"),
    (["- [ ] 短い"], "短"),
    (["outcome/a.md にまとめる"], "1 行 1 条件"),
    (["* [ ] outcome/a.md にまとめる"], "1 行 1 条件"),
)

ACCEPTANCE_IDS = (
    "c-placeholder",
    "c-placeholder-bulleted",
    "d-too-short",
    "e-not-checkbox",
    "e-wrong-bullet",
)


@pytest.mark.parametrize(("acceptance", "keyword"), ACCEPTANCE_CASES, ids=ACCEPTANCE_IDS)
def test_acceptance_form_problems(acceptance: list[str], keyword: str) -> None:
    """§2 c・d・e: 穴埋め／最小長／`- [ ]` 1 行 1 条件の形。"""
    result = _run(make_proposal([make_item(acceptance=acceptance)]))
    assert any(keyword in p for p in result.problems)


def test_placeholder_and_min_len_are_exclusive() -> None:
    """§2 d: 穴埋めで指摘した条文は最小長を見ない（**c と排他**）。"""
    result = _run(make_proposal([make_item(acceptance=["- [ ] tbd"])]))
    assert sum(1 for p in result.problems if "プレースホルダ" in p) == 1
    assert not any("短" in p for p in result.problems)


def test_outcome_path_required_stage_without_token() -> None:
    """US3 AC-6・FR-039: `require_outcome_path` の段でトークン 0 件なら指摘。"""
    result = _run(
        make_proposal([make_item(acceptance=["- [ ] 結果をまとめて報告する"])]), SUBREQUEST
    )
    assert any("成果物" in p for p in result.problems)


def test_non_canonical_token_counts_as_present() -> None:
    """US3 AC-6: **正準形でないトークンも「有る」側**に数える（形式違反は別に指摘済み）。"""
    result = _run(
        make_proposal([make_item(acceptance=["- [ ] ../outcome/x.md にまとめる"])]), SUBREQUEST
    )
    assert any("形ではありません" in p for p in result.problems)
    assert not any("1 つも" in p for p in result.problems)


def test_single_child_observation_does_not_fail() -> None:
    """US3 AC-13・FR-046: 1 件かつ最小件数 1 なら **`ok` を落とさず**観測を積む。"""
    result = _run(make_proposal([make_item(acceptance=OK_ACCEPTANCE)]))
    assert result.ok is True
    assert result.observations == ["single_child"]


def test_no_observation_when_multiple_items() -> None:
    items = [
        make_item(title="a", acceptance=OK_ACCEPTANCE),
        make_item(title="b", acceptance=OK_ACCEPTANCE),
    ]
    assert _run(make_proposal(items)).observations == []


def test_all_problems_returned_at_once() -> None:
    """US3 AC-14・SC-006: 複数の違反を含む 1 提案で、すべての指摘が 1 度に返る。"""
    spec = SUBREQUEST.model_copy(update={"min_items": 3})
    items = [
        make_item(title="", persona=PersonaKey.planner, acceptance=["- [ ] tbd"], depends_on=[5]),
        make_item(title="b", summary="", acceptance=[]),
    ]
    result = make_service().validate_proposal(make_proposal(items), spec, [])
    joined = "\n".join(result.problems)
    assert "件" in joined
    assert "1件目 の title" in joined
    assert "1件目 の persona" in joined
    assert "1件目 の acceptance" in joined
    assert "1件目 の depends_on" in joined
    assert "2件目 の summary" in joined
    assert "2件目 の acceptance" in joined


BROKEN_PROPOSALS: tuple[Proposal, ...] = (
    make_proposal([]),
    make_proposal([make_item(depends_on=[999])]),
    make_proposal([make_item(acceptance=[])]),
    make_proposal([make_item(title="")]),
    make_proposal(
        [make_item(title="", summary="", description="", acceptance=[""], depends_on=[0])]
    ),
)

BROKEN_IDS = (
    "zero-items",
    "out-of-range-dep",
    "empty-acceptance",
    "empty-title",
    "everything-broken",
)


@pytest.mark.parametrize("proposal", BROKEN_PROPOSALS, ids=BROKEN_IDS)
def test_broken_proposals_never_raise(proposal: Proposal) -> None:
    """US3 AC-15・SC-007: 壊れた提案でも**例外 0 件**。"""
    for spec in (PROGRAM, PROJECT, SUBREQUEST):
        result = make_service().validate_proposal(proposal, spec, [])
        assert isinstance(result.problems, list)


def test_ok_is_derived_from_problems() -> None:
    """§0: `ok = not problems`。`observations` は `ok` に影響しない。"""
    good = _run(make_proposal([make_item(acceptance=OK_ACCEPTANCE)]))
    bad = _run(make_proposal([make_item(title="")]))
    assert good.ok is True
    assert good.observations != []
    assert bad.ok is False


def test_problem_order_is_fixed() -> None:
    """§0・§1: 指摘の並び順は検査の順（件数 → 件ごと → 依存 → 衝突）で固定する。"""
    spec = PROJECT.model_copy(update={"min_items": 3})
    items = [
        make_item(title="", acceptance=["- [ ] outcome/a.md にまとめる"]),
        make_item(title="", acceptance=["- [ ] outcome/a.md にまとめる"], depends_on=[9]),
    ]
    problems = make_service().validate_proposal(make_proposal(items), spec, []).problems
    kinds = [
        next(i for i, p in enumerate(problems) if "件以上" in p),
        next(i for i, p in enumerate(problems) if p.startswith("1件目 の title")),
        next(i for i, p in enumerate(problems) if p.startswith("2件目 の title")),
        next(i for i, p in enumerate(problems) if "範囲" in p),
        next(i for i, p in enumerate(problems) if "重複" in p),
    ]
    assert kinds == sorted(kinds)


def test_same_input_gives_same_result() -> None:
    """FR-011・SC-003: 同じ入力に同じ結果（指摘の並び順も含めて）。"""
    proposal = make_proposal([make_item(title=""), make_item(title="")])
    assert _run(proposal) == _run(proposal)


def test_placeholder_set_comes_from_rules() -> None:
    """US6 AC-4: 穴埋め文言の集合は設定由来（`AcceptanceRules`）。"""
    service = make_service(
        rules=make_acceptance_rules(placeholders=frozenset({"独自語"}), min_len=1)
    )
    result = service.validate_proposal(make_proposal([make_item(title="独自語")]), PROJECT, [])
    assert any("1件目 の title" in p for p in result.problems)


def test_min_len_comes_from_rules() -> None:
    """US6 AC-4: 最小長は設定由来。"""
    long_enough = make_service(rules=make_acceptance_rules(min_len=1))
    strict = make_service(rules=make_acceptance_rules(min_len=50))
    proposal = make_proposal([make_item(acceptance=["- [ ] outcome/a.md にまとめる"])])
    assert not any("短" in p for p in long_enough.validate_proposal(proposal, PROJECT, []).problems)
    assert any("短" in p for p in strict.validate_proposal(proposal, PROJECT, []).problems)
