"""コマンド突合の整合検査（F20）。contracts/validation-rules.md §4 の V-24〜V-26。

**死に定義（V-25）も同じ重さで起動を中断する**（FR-047・clarify 裁定）。違反に 2 種の重さを
持ち込むと `ValidationProblem` に §5 の語彙に無い属性が要り、「起動は通ったが保存できない
設定」という第 3 の状態も残る。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import write, write_minimal_area_a, write_policy

from settings.repository import AreaAReader
from settings.service.loader import read_area
from settings.service.validate import validate_commands

if TYPE_CHECKING:
    from pathlib import Path

BASE_ROW = "p, member:pj.programmer, tool:read_issue, execute"


def _area(root: Path, commands: str, rows: list[str]) -> None:
    write_minimal_area_a(root)
    write(root, "global/commands.yaml", commands)
    write_policy(root, "pj", [BASE_ROW, *rows])


def _commands(root: Path) -> list[str]:
    return [item.kind for item in validate_commands(read_area(AreaAReader(root)))]


PYTEST_DEF = "commands:\n  pytest:\n    argv:\n      - uv\n      - run\n      - pytest\n"


def test_matched_definition_and_reference_pass(settings_root: Path) -> None:
    """定義と参照が対応していれば違反 0 件。"""
    _area(settings_root, PYTEST_DEF, ["p, member:pj.programmer, cmd:pytest, execute"])
    assert _commands(settings_root) == []


def test_secret_key_at_the_top_level_of_commands_is_rejected(settings_root: Path) -> None:
    """V-06 は**ファイルの生トップレベル**まで届く（FR-050。`llm_profiles.yaml` と同型）。"""
    _area(
        settings_root,
        "password: 実値は書かない\n" + PYTEST_DEF,
        ["p, member:pj.programmer, cmd:pytest, execute"],
    )
    problems = validate_commands(read_area(AreaAReader(settings_root)))
    assert [item.where for item in problems if item.kind == "forbidden_key"] == [
        "global/commands.yaml:password"
    ]


def test_v24_detects_a_reference_without_a_definition(settings_root: Path) -> None:
    """V-24: policy の `cmd:<名前>` 対象 ⊆ `commands.yaml` の名前。"""
    _area(settings_root, "commands: {}\n", ["p, member:pj.programmer, cmd:pytest, execute"])
    assert _commands(settings_root) == ["cmd_ref"]


def test_v25_detects_a_dead_definition(settings_root: Path) -> None:
    """V-25: 定義の名前が **1 行以上の policy から参照される**（死に定義の検出）。"""
    _area(settings_root, PYTEST_DEF, [])
    assert _commands(settings_root) == ["cmd_ref"]


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param("commands:\n  pytest:\n    argv: []\n", id="空のリスト"),
        pytest.param("commands:\n  pytest:\n    argv: pytest\n", id="文字列（リストでない）"),
        pytest.param("commands:\n  pytest:\n    argv:\n      - 1\n", id="要素が文字列でない"),
        pytest.param("commands:\n  pytest: {}\n", id="定義が空"),
    ],
)
def test_v26_detects_a_broken_argv(settings_root: Path, argv: str) -> None:
    """V-26: `argv` が**空でない文字列のリスト**である（US2 AC-9。実行時に初めて壊れない）。"""
    _area(settings_root, argv, ["p, member:pj.programmer, cmd:pytest, execute"])
    assert "cmd_ref" in _commands(settings_root)


def test_empty_definitions_and_no_cmd_rows_pass(settings_root: Path) -> None:
    """Edge Cases: コマンド定義が空・policy に `cmd:` 行が無ければ違反 0 件で通る。"""
    _area(settings_root, "commands: {}\n", [])
    assert _commands(settings_root) == []


def test_dead_definition_aborts_the_startup(settings_root: Path) -> None:
    """FR-047: 死に定義は**安全側の不整合だが同じ重さ**で起動を中断させる。"""
    from settings_testing import collect_problems, kinds  # noqa: PLC0415

    _area(settings_root, PYTEST_DEF, [])
    assert "cmd_ref" in kinds(collect_problems(settings_root))
