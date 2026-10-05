"""`CommandRegistry.resolve_command`（IF-30・settings-F10）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import make_service, write, write_minimal_area_a, write_policy

from common.interfaces.settings import UnknownCommand

if TYPE_CHECKING:
    from pathlib import Path

COMMANDS = """\
commands:
  pytest:
    argv:
      - uv
      - run
      - pytest
"""


def _command_area(root: Path) -> None:
    write_minimal_area_a(root)
    write(root, "global/commands.yaml", COMMANDS)
    write_policy(
        root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.programmer, cmd:pytest, execute",
        ],
    )


def test_resolve_command_returns_the_argv_as_declared(settings_root: Path) -> None:
    """US2 AC-7: 定義に名前が在れば**引数列そのもの**が返る（組み立てない＝ D07-e）。"""
    _command_area(settings_root)
    assert make_service(settings_root).resolve_command("pytest") == ["uv", "run", "pytest"]


def test_resolve_command_raises_for_unknown_name(settings_root: Path) -> None:
    """US2 AC-8: 定義に無い名前は契約例外（**空の一覧を返さない**）。"""
    _command_area(settings_root)
    with pytest.raises(UnknownCommand):
        make_service(settings_root).resolve_command("rm")


def test_caller_mutation_does_not_break_the_snapshot(settings_root: Path) -> None:
    """返り値を呼び手が書き換えてもスナップショットは壊れない（その都度新しい一覧を作る）。"""
    _command_area(settings_root)
    service = make_service(settings_root)
    first = service.resolve_command("pytest")
    first.append("--ruined")
    first[0] = "rm"
    assert service.resolve_command("pytest") == ["uv", "run", "pytest"]


def test_empty_command_registry_is_valid(settings_root: Path) -> None:
    """Edge Cases: コマンド定義が空でも有効（`cmd:` 行が無ければ突合も通る）。"""
    write_minimal_area_a(settings_root)
    service = make_service(settings_root)
    with pytest.raises(UnknownCommand):
        service.resolve_command("pytest")
