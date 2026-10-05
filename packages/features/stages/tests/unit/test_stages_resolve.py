"""段の解決（`resolve_stage`。IF-44・stages-F01・US1）。

観測点は `specs/006-stages/contracts/stage-table.md` §1 の 15 行。
"""

from __future__ import annotations

import inspect

import pytest
from conftest import make_service, stage_spec

from common.interfaces.stages import UnknownStage
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName, StateLabel
from stages.models import KIND_TO_STAGE

PROGRAM = Labels.kind(KindLabel.program)
REQUEST = Labels.kind(KindLabel.request)
SUBREQUEST = Labels.kind(KindLabel.subrequest)
CHILD = Labels.kind(KindLabel.child)
QUESTION = Labels.kind(KindLabel.question)

RESOLVED: tuple[tuple[set[str], bool, StageName], ...] = (
    ({PROGRAM}, False, StageName.program),
    ({REQUEST}, False, StageName.project),
    ({REQUEST}, True, StageName.project),
    ({SUBREQUEST}, True, StageName.subrequest),
    ({SUBREQUEST}, False, StageName.subrequest),
    ({CHILD}, True, StageName.child),
    (set(), True, StageName.child),
    (set(), False, StageName.plain),
    ({Labels.persona(PersonaKey.writer), Labels.state(StateLabel.working)}, True, StageName.child),
    ({Labels.persona(PersonaKey.writer), Labels.state(StateLabel.working)}, False, StageName.plain),
    ({"壊れたラベル"}, False, StageName.plain),
    ({Labels.note("single-child")}, True, StageName.child),
)

RESOLVED_IDS = (
    "US1-AC1-program",
    "US1-AC2-project-no-parent",
    "US1-AC2-project-with-parent",
    "US1-AC3-subrequest",
    "US1-AC3-subrequest-no-parent",
    "US1-AC4-child-by-label",
    "US1-AC4-child-by-parent",
    "US1-AC5-plain",
    "EDGE-other-dims-with-parent",
    "EDGE-other-dims-no-parent",
    "EDGE-unparsable-label",
    "EDGE-note-dim",
)

REJECTED: tuple[tuple[set[str], bool], ...] = (
    ({PROGRAM}, True),
    ({CHILD}, False),
    ({QUESTION}, False),
    ({QUESTION}, True),
    ({PROGRAM, SUBREQUEST}, False),
    ({PROGRAM, SUBREQUEST}, True),
    ({"kind:foo"}, False),
    ({"kind:foo"}, True),
    ({"kind:"}, False),
)

REJECTED_IDS = (
    "US1-AC6-program-with-parent",
    "FR019c-child-without-parent",
    "FR019b-question-no-parent",
    "FR019b-question-with-parent",
    "FR019a-two-kinds-no-parent",
    "FR019a-two-kinds-with-parent",
    "FR019b-unknown-value-no-parent",
    "FR019b-unknown-value-with-parent",
    "FR019b-empty-kind-value",
)


@pytest.mark.parametrize(("kind_labels", "has_parent", "expected"), RESOLVED, ids=RESOLVED_IDS)
def test_resolve_stage_returns_expected_stage(
    kind_labels: set[str], has_parent: bool, expected: StageName
) -> None:
    assert make_service().resolve_stage(kind_labels, has_parent).name == expected


@pytest.mark.parametrize(("kind_labels", "has_parent"), REJECTED, ids=REJECTED_IDS)
def test_resolve_stage_rejects_contradictions(kind_labels: set[str], has_parent: bool) -> None:
    with pytest.raises(UnknownStage):
        make_service().resolve_stage(kind_labels, has_parent)


def test_subrequest_requires_outcome_path() -> None:
    spec = make_service().resolve_stage({SUBREQUEST}, has_parent=True)
    assert spec.require_outcome_path is True


def test_child_is_execute_mode() -> None:
    assert make_service().resolve_stage({CHILD}, has_parent=True).mode == "execute"


def test_plain_is_execute_mode() -> None:
    assert make_service().resolve_stage(set(), has_parent=False).mode == "execute"


def test_same_input_gives_same_stage() -> None:
    """US1 AC-7・FR-011: 同じ入力に同じ段（走査順は宣言順で固定）。"""
    service = make_service()
    first = service.resolve_stage({SUBREQUEST}, True)
    second = service.resolve_stage({SUBREQUEST}, True)
    assert first == second


def test_signature_has_no_tracker_type() -> None:
    """US1 AC-8・FR-015: 作業管理ツール側の種別は引数に無い（構造的に見ようがない）。"""
    params = list(inspect.signature(make_service().resolve_stage).parameters)
    assert params == ["kind_labels", "has_parent"]


# -- 段表の実値（`child_labels` の連鎖・`allowed_personas`）------------------------------
#
# 正本: contracts/stage-table.md §2・data-model.md §2-1（spec.md P-01）。
# ここまでの表は「入力 → 段名」しか見ておらず、返る `StageSpec` の残りの属性は
# `require_outcome_path`・`mode`・`min_items`・`max_acceptance` しか観測されていない。
# `child_labels` は書かれるだけで誰からも読まれないため、段の連鎖（FR-018）を壊しても
# 気づけない。ここで表として固定する。

STAGE_VALUES: tuple[tuple[StageName, list[str], set[PersonaKey]], ...] = (
    (StageName.program, [REQUEST], {PersonaKey.project_lead}),
    (StageName.project, [SUBREQUEST], {PersonaKey.system_engineer, PersonaKey.doc_lead}),
    (
        StageName.subrequest,
        [CHILD],
        {
            PersonaKey.architect,
            PersonaKey.programmer,
            PersonaKey.analyst,
            PersonaKey.writer,
            PersonaKey.general,
        },
    ),
    (StageName.child, [], set()),
    (StageName.plain, [], set()),
)

STAGE_VALUE_IDS = (
    "US1-AC1-program",
    "US1-AC2-project",
    "US1-AC3-subrequest",
    "FR017a-child-empty",
    "FR017a-plain-empty",
)


@pytest.mark.parametrize(
    ("name", "child_labels", "allowed_personas"), STAGE_VALUES, ids=STAGE_VALUE_IDS
)
def test_stage_row_declares_expected_children_and_personas(
    name: StageName, child_labels: list[str], allowed_personas: set[PersonaKey]
) -> None:
    """FR-018・FR-017a: 段が起票する子のラベルと、その子に許すペルソナの実値。"""
    spec = stage_spec(name)
    assert spec.child_labels == child_labels
    assert spec.allowed_personas == allowed_personas


CHAIN: tuple[tuple[StageName, StageName], ...] = (
    (StageName.program, StageName.project),
    (StageName.project, StageName.subrequest),
    (StageName.subrequest, StageName.child),
)

CHAIN_IDS = ("FR018-program-to-project", "FR018-project-to-subrequest", "FR018-subrequest-to-child")


@pytest.mark.parametrize(("parent", "expected_child"), CHAIN, ids=CHAIN_IDS)
def test_child_labels_resolve_to_the_next_stage(
    parent: StageName, expected_child: StageName
) -> None:
    """FR-018: `child_labels` を持つ子は次の段に落ちる（連鎖が実際につながっている）。"""
    labels = set(stage_spec(parent).child_labels)
    assert make_service().resolve_stage(labels, has_parent=True).name == expected_child


def test_kind_to_stage_has_exactly_four_entries() -> None:
    """SD-41: `kind_key` を持つ 4 行から導出される（`question` はどの段からも参照されない）。

    対になる `PERSONA_GROUP` の 9 種網羅は `test_stages_prompt.py` で固定済み。ここが無いと
    段表に 5 行目の `kind_key` が生えても `REJECTED` 表（`kind:question` が拒まれること）は
    落ちない。
    """
    assert set(KIND_TO_STAGE) == {
        KindLabel.program,
        KindLabel.request,
        KindLabel.subrequest,
        KindLabel.child,
    }
    assert KindLabel.question not in KIND_TO_STAGE
