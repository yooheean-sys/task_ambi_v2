"""SC-003: 6 次元すべてで組立→分解が往復し、`parse` が例外を送出しない。

期待値の正本は data-model.md §1 の `parse` 確定仕様表（FR-014・DIFF-01）。
"""

from __future__ import annotations

import pytest

from common.kernel.enums import KindLabel, LabelDim, Labels, PersonaKey, StateLabel

# --- (a) 6 次元の往復 -------------------------------------------------------

ROUND_TRIPS: list[tuple[str, LabelDim, str]] = [
    (Labels.kind(KindLabel.question), LabelDim.kind, str(KindLabel.question)),
    (Labels.project("demo"), LabelDim.project, "demo"),
    (Labels.persona(PersonaKey.programmer), LabelDim.persona, str(PersonaKey.programmer)),
    (Labels.role_executor(), LabelDim.role, "executor"),
    (Labels.state(StateLabel.working), LabelDim.state, str(StateLabel.working)),
    (Labels.note("single-child"), LabelDim.note, "single-child"),
]


@pytest.mark.parametrize(("label", "dimension", "value"), ROUND_TRIPS)
def test_round_trip(label: str, dimension: LabelDim, value: str) -> None:
    assert Labels.parse(label) == (dimension, value)


def test_round_trip_covers_all_six_dimensions() -> None:
    assert {dimension for _, dimension, _ in ROUND_TRIPS} == set(LabelDim)


def test_assembled_strings() -> None:
    assert Labels.kind(KindLabel.child) == "kind:child"
    assert Labels.project("demo") == "project:demo"
    assert Labels.persona(PersonaKey.writer) == "persona:writer"
    assert Labels.role_executor() == "role:executor"
    assert Labels.state(StateLabel.failing) == "state:failing"
    assert Labels.note("single-child") == "note:single-child"


# --- (b) エッジケース -------------------------------------------------------

EDGE_CASES: list[tuple[str, tuple[LabelDim, str] | None]] = [
    ("kind:question", (LabelDim.kind, "question")),
    ("note:a:b", (LabelDim.note, "a:b")),
    ("kind:", (LabelDim.kind, "")),
    ("kinds:x", None),
    ("foo", None),
    ("", None),
    ("project:project", (LabelDim.project, "project")),
    ("state", None),
    (":x", None),
]


@pytest.mark.parametrize(("label", "expected"), EDGE_CASES)
def test_parse_edge_cases(label: str, expected: tuple[LabelDim, str] | None) -> None:
    assert Labels.parse(label) == expected


# --- (c) 例外を送出しない ---------------------------------------------------

NEVER_RAISES = [
    "",
    ":",
    "::",
    "kind",
    "kind:" + "x" * 10_000,
    "ＫＩＮＤ:全角",
    "  kind:question",
    "kind: question",
    "KIND:x",
    "Kind:x",
    "\x00\x01",
    "note:" + "\n",
]


@pytest.mark.parametrize("label", NEVER_RAISES)
def test_parse_never_raises(label: str) -> None:
    Labels.parse(label)


def test_parse_is_case_sensitive_on_the_dimension() -> None:
    """次元名の照合は大文字小文字を区別する（既知 6 次元は小文字のみ）。"""
    assert Labels.parse("KIND:x") is None
    assert Labels.parse("Kind:x") is None


def test_parse_does_not_trim_whitespace() -> None:
    """前後・区切り前後の空白は値の一部として扱う（妥当性を判断しないため）。"""
    assert Labels.parse(" kind:question") is None
    assert Labels.parse("kind: question") == (LabelDim.kind, " question")


# --- (d) 妥当性判断をしない -------------------------------------------------


def test_assembly_does_not_validate_its_input() -> None:
    assert Labels.project("") == "project:"
    assert Labels.note("a:b") == "note:a:b"


def test_kernel_holds_no_judgement_helpers() -> None:
    """未知と判別したあとの扱いは呼び側の責務（P5D-002・FR-009・FR-015）。"""
    offenders = [
        name for name in vars(Labels) if name.startswith(("is_", "filter_", "validate_", "check_"))
    ]
    assert offenders == []
