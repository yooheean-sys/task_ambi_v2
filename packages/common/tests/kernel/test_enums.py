"""SC-002: 共有語彙 6 種のメンバ名と値が設計と過不足 0、かつ全メンバで 名 == 値。"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest
from _support.design_docs import load_kernel_enums

from common.kernel import enums as kernel_enums

KERNEL_ENUMS: dict[str, type[StrEnum]] = {
    "PersonaKey": kernel_enums.PersonaKey,
    "StageName": kernel_enums.StageName,
    "ToolName": kernel_enums.ToolName,
    "KindLabel": kernel_enums.KindLabel,
    "StateLabel": kernel_enums.StateLabel,
    "LabelDim": kernel_enums.LabelDim,
}

EXPECTED_SIZES = {
    "PersonaKey": 9,
    "StageName": 5,
    "ToolName": 16,
    "KindLabel": 5,
    "StateLabel": 2,
    "LabelDim": 6,
}


def test_the_shared_vocabulary_has_six_enums(design_root: Path) -> None:
    """語彙は 6 種。`Labels` は列挙ではなく名前空間クラスなので数えない。"""
    design = load_kernel_enums(design_root)
    assert set(design) == set(KERNEL_ENUMS)
    assert len(design) == 6


@pytest.mark.parametrize("name", sorted(KERNEL_ENUMS))
def test_values_match_the_design(name: str, design_root: Path) -> None:
    design = load_kernel_enums(design_root)
    assert sorted(m.value for m in KERNEL_ENUMS[name]) == sorted(design[name].values)


@pytest.mark.parametrize("name", sorted(KERNEL_ENUMS))
def test_member_names_match_the_design(name: str, design_root: Path) -> None:
    """突合対象は値集合だけでなく**列挙メンバ名**も含む（SC-002）。"""
    design = load_kernel_enums(design_root)
    assert sorted(m.name for m in KERNEL_ENUMS[name]) == sorted(design[name].values)


@pytest.mark.parametrize("name", sorted(KERNEL_ENUMS))
def test_member_name_equals_value(name: str) -> None:
    offenders = [m.name for m in KERNEL_ENUMS[name] if m.name != m.value]
    assert offenders == []


@pytest.mark.parametrize("name", sorted(KERNEL_ENUMS))
def test_sizes(name: str) -> None:
    assert len(KERNEL_ENUMS[name]) == EXPECTED_SIZES[name]


@pytest.mark.parametrize("name", sorted(KERNEL_ENUMS))
def test_every_enum_is_a_str_enum(name: str) -> None:
    """`str(v)` が値そのものになること（`Labels` の組立と `parse` の往復が前提にする）。"""
    for member in KERNEL_ENUMS[name]:
        assert str(member) == member.value


def test_tool_name_has_twelve_values(design_root: Path) -> None:
    """P5D-045 の反映。`p5_common_notes.md` の欠落（DIFF-03）は T054 で解消する。"""
    assert len(kernel_enums.ToolName) == 16
    assert len(load_kernel_enums(design_root)["ToolName"].values) == 16
