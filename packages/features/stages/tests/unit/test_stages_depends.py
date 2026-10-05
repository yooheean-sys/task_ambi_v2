"""依存の検査と起票順（`validate_proposal` の §4・`order`。US3 AC-8／AC-9・FR-041／FR-042）。

正本は [contracts/proposal-gate.md](../../../../specs/006-stages/contracts/proposal-gate.md) §4。
**循環時も例外を送出せず `order = []` で返す**（R-09。PoC の `ValueError` は継承しない）。
"""

from __future__ import annotations

import pytest
from conftest import make_item, make_proposal, make_service, stage_spec

from common.kernel.enums import StageName

PROJECT = stage_spec(StageName.project)
ACCEPT = ["- [ ] outcome/x.md にまとめる"]


def _problems(depends: list[list[int]]) -> list[str]:
    items = [
        make_item(title=f"t{i}", acceptance=ACCEPT, depends_on=d) for i, d in enumerate(depends)
    ]
    return make_service().validate_proposal(make_proposal(items), PROJECT, []).problems


def _order(depends: list[list[int]]) -> list[int]:
    items = [
        make_item(title=f"t{i}", acceptance=ACCEPT, depends_on=d) for i, d in enumerate(depends)
    ]
    return make_service().validate_proposal(make_proposal(items), PROJECT, []).order


DEPENDS_CASES: tuple[tuple[list[list[int]], str], ...] = (
    ([[], [0, 0]], "重複"),
    ([[], [1]], "自己"),
    ([[], [9]], "範囲"),
    ([[], [-1]], "範囲"),
    ([[1], []], "後"),
)

DEPENDS_IDS = ("duplicate", "self-reference", "out-of-range-high", "out-of-range-low", "backward")


@pytest.mark.parametrize(("depends", "keyword"), DEPENDS_CASES, ids=DEPENDS_IDS)
def test_depends_problems(depends: list[list[int]], keyword: str) -> None:
    """US3 AC-8: 重複 → 自己参照 → 範囲外 → 後方参照 の順に**排他で 1 つだけ**指摘。"""
    problems = [p for p in _problems(depends) if "depends_on" in p]
    assert any(keyword in p for p in problems)


def test_exclusive_one_problem_per_dependency() -> None:
    """§4: 1 つの依存に対して指摘は 1 つだけ（自己参照かつ後方参照でも 1 件）。"""
    problems = [p for p in _problems([[], [1]]) if "depends_on" in p]
    assert len(problems) == 1


def test_duplicate_reports_once_and_continues() -> None:
    """§4: 重複は指摘して**次の依存へ**進む（後続の壊れ方も拾う）。"""
    problems = [p for p in _problems([[], [], [0, 0, 9]]) if "depends_on" in p]
    assert any("重複" in p for p in problems)
    assert any("範囲" in p for p in problems)


def test_order_is_empty_when_depends_problems_exist() -> None:
    """R-09・FR-042: 依存に指摘が 1 つでもあれば `order` を返さない（例外にしない）。"""
    assert _order([[], [9]]) == []


def test_order_is_returned_despite_other_problems() -> None:
    """§4: 依存以外の指摘（題名が空等）があっても `order` は返す。"""
    items = [
        make_item(title="", acceptance=ACCEPT),
        make_item(title="b", acceptance=ACCEPT, depends_on=[0]),
    ]
    result = make_service().validate_proposal(make_proposal(items), PROJECT, [])
    assert result.ok is False
    assert result.order == [0, 1]


ORDER_CASES: tuple[tuple[list[list[int]], list[int]], ...] = (
    ([[], [], []], [0, 1, 2]),
    ([[], [0], [1]], [0, 1, 2]),
    ([[], [], [0, 1]], [0, 1, 2]),
    ([[], [0], [0]], [0, 1, 2]),
)

ORDER_IDS = ("no-deps", "chain", "fan-in", "fan-out")


@pytest.mark.parametrize(("depends", "expected"), ORDER_CASES, ids=ORDER_IDS)
def test_layered_topo_order(depends: list[list[int]], expected: list[int]) -> None:
    """US3 AC-9・FR-042: 層状の topo 順（同順位は元の添字順で安定）。"""
    assert _order(depends) == expected


def test_order_covers_every_item() -> None:
    """健全な提案では全件が 1 度ずつ並ぶ。"""
    order = _order([[], [0], [0], [1, 2]])
    assert sorted(order) == [0, 1, 2, 3]


def test_cycle_is_structurally_impossible_but_reported() -> None:
    """§4: 前方参照だけを許すため循環は起こらないが、後方参照として指摘は残る。"""
    problems = [p for p in _problems([[1], [0]]) if "depends_on" in p]
    assert problems != []
    assert _order([[1], [0]]) == []
