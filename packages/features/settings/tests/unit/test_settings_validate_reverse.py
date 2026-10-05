"""逆方向の整合検査（F19。孤児ポリシーの検出）。contracts/validation-rules.md §3 の V-21〜V-23。

**V-19（メンバ ⊆ policy 主体）と V-21（policy 主体 ⊆ 台帳）は別の検査**であり、片方だけでは
乖離が残る（PoC `validate.py` の `collect_problems` と `_check_orphan_subjects` の対応）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    collect_problems,
    kinds,
    write,
    write_minimal_area_a,
    write_policy,
    write_project,
)

from settings.repository import AreaAReader
from settings.service.loader import read_area
from settings.service.validate import validate_reverse

if TYPE_CHECKING:
    from pathlib import Path


def _area(root: Path, rows: list[str]) -> None:
    write_minimal_area_a(root, personas=("programmer",))
    write_project(root, "pj", ("programmer", "writer"))
    write(root, "projects/pj/agents.yaml", "programmer: {}\nwriter: {}\n")
    write_policy(root, "pj", rows)


def _reverse(root: Path) -> list[str]:
    return [item.kind for item in validate_reverse(read_area(AreaAReader(root)))]


HEALTHY = [
    "p, member:pj.programmer, tool:read_issue, execute",
    "p, member:pj.writer, tool:read_issue, execute",
    "g, member:pj.writer, role_writer",
    "p, role_writer, tool:write_outcome, execute",
]


def test_healthy_policy_has_no_orphans(settings_root: Path) -> None:
    """役割経由の許可を含む健全な policy は違反 0 件（V-21・V-22 が誤検出しない）。"""
    _area(settings_root, HEALTHY)
    assert _reverse(settings_root) == []


def test_v21_detects_a_subject_absent_from_the_registry(settings_root: Path) -> None:
    """V-21: `p`／`g` 行の**主体**が台帳に居ない（孤児ポリシー）。"""
    _area(settings_root, [*HEALTHY, "p, member:pj.ghost, tool:read_issue, execute"])
    assert "orphan" in _reverse(settings_root)


def test_v21_detects_a_grouping_row_subject(settings_root: Path) -> None:
    """V-21 は `g` 行の主体も見る。"""
    _area(settings_root, [*HEALTHY, "g, member:pj.ghost, role_writer"])
    assert "orphan" in _reverse(settings_root)


def test_v21_catches_the_quoted_subject_trap(settings_root: Path) -> None:
    """M-09 の罠: casbin は**引用符を剥がさない**ので台帳のどれにも一致せず孤児になる。

    `p, "member:x", tool:y, execute` の主体は `'"member:x"'` という文字どおりの値になる。
    「`policy.csv` に引用符を書かせない」規約は `SKILL.md` が持つ（FR-048c）。
    """
    _area(settings_root, ['p, "member:pj.programmer", tool:read_issue, execute'])
    assert "orphan" in _reverse(settings_root)


def test_v22_detects_a_role_without_any_grant(settings_root: Path) -> None:
    """V-22: `g` 行の役割側（`role_*`）が `p` 行の主体として 1 行以上定義されていない。"""
    _area(settings_root, [row for row in HEALTHY if not row.startswith("p, role_")])
    assert "orphan" in _reverse(settings_root)


def test_v23_detects_an_empty_vault_area(settings_root: Path) -> None:
    """V-23: `vault:` 対象が形式として読める（続く文字列が空でない）。"""
    _area(settings_root, ["p, member:pj.programmer, vault:, read", *HEALTHY])
    assert "orphan" in _reverse(settings_root)


def test_empty_policy_file_is_valid(settings_root: Path) -> None:
    """Edge Cases: 権限ファイルが空でも有効な組み立てである（すべて拒否になる）。

    孤児ポリシーの検査は 0 行に対して何も言わない。ただし**メンバが居れば V-19 が拾う**
    （メンバ ⊆ policy 主体は順方向の検査であり、こちらとは別である）。
    """
    write_minimal_area_a(settings_root)
    write(settings_root, "projects/pj/agents.yaml", "{}\n")
    write_policy(settings_root, "pj", [])
    assert _reverse(settings_root) == []
    assert collect_problems(settings_root) == []


def test_v19_and_v21_are_different_checks(settings_root: Path) -> None:
    """V-19 は「メンバ ⊆ policy 主体」、V-21 はその逆向きである。"""
    _area(settings_root, ["p, member:pj.programmer, tool:read_issue, execute"])
    problems = collect_problems(settings_root)
    forward = [item for item in problems if item.kind == "forward"]
    assert forward, "policy に行が無い writer を V-19 が拾えていない"
    assert "writer" in forward[0].where
    assert "orphan" not in kinds(problems)


@pytest.mark.parametrize(
    "row",
    [
        pytest.param("# コメント行", id="コメント"),
        pytest.param("", id="空行"),
    ],
)
def test_comments_and_blank_lines_are_skipped(settings_root: Path, row: str) -> None:
    """M-09: `#` 始まりと空行は casbin が読み飛ばす（自前で構文解析しない＝ FR-048）。"""
    _area(settings_root, [row, *HEALTHY])
    assert _reverse(settings_root) == []
