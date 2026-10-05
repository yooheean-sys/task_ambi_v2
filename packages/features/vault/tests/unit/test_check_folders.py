"""`check_project_folders`（US4・SC-004・SC-008・FR-014・FR-035）。

**フォルダを 1 つも作らない**ことと、**不在を失敗にしない／外縁違反を不在に倒さない**ことを見る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vault_testing import dirs_under, make_service, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import VaultPathViolation
from common.kernel.enums import PersonaKey


def test_all_present(vault_root: Path, folders: ProjectFolders) -> None:
    service = make_service(vault_root)
    result = service.check_project_folders(folders)
    assert result.missing == []
    assert result.ok is True


def test_partly_missing_is_not_a_failure(vault_root: Path) -> None:
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/nope"},
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    result = service.check_project_folders(declared)
    assert result.missing == ["pj/roles/nope"]
    assert result.ok is False


def test_a_file_where_a_folder_is_declared_counts_as_missing(vault_root: Path) -> None:
    write_file(vault_root, "pj/asfile", "F")
    declared = ProjectFolders(
        pj_input_dir="pj/asfile",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    assert service.check_project_folders(declared).missing == ["pj/asfile"]


def test_creates_no_folder(vault_root: Path) -> None:
    """SC-004: 実行後にフォルダが**1 つも作られていない**。"""
    declared = ProjectFolders(
        pj_input_dir="pj/nope1",
        role_dirs={PersonaKey.planner: "pj/nope2"},
        outcome_dir="pj/nope3",
    )
    service = make_service(vault_root)
    before = dirs_under(vault_root)
    result = service.check_project_folders(declared)
    assert result.missing == ["pj/nope1", "pj/nope2", "pj/nope3"]
    assert dirs_under(vault_root) == before


def test_declaration_outside_the_root_is_a_violation_not_a_miss(vault_root: Path) -> None:
    """FR-035・DIFF-05: 外縁違反を**不在に倒さない**。"""
    declared = ProjectFolders(
        pj_input_dir="../outside",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.check_project_folders(declared)
    assert exc.value.kind == "outside_root"
    assert "../outside" in exc.value.detail


def test_missing_order_is_stable_and_independent_of_dict_order(vault_root: Path) -> None:
    """SC-008: `pj_input_dir` → `role_dirs`（**`PersonaKey` の列挙宣言順**） → `outcome_dir`。"""
    service = make_service(vault_root)
    expected = ["pj/nope0", "pj/nope_planner", "pj/nope_writer", "pj/nope9"]
    forward = ProjectFolders(
        pj_input_dir="pj/nope0",
        role_dirs={
            PersonaKey.planner: "pj/nope_planner",
            PersonaKey.writer: "pj/nope_writer",
        },
        outcome_dir="pj/nope9",
    )
    reversed_dict = ProjectFolders(
        pj_input_dir="pj/nope0",
        role_dirs={
            PersonaKey.writer: "pj/nope_writer",
            PersonaKey.planner: "pj/nope_planner",
        },
        outcome_dir="pj/nope9",
    )
    assert service.check_project_folders(forward).missing == expected
    assert service.check_project_folders(reversed_dict).missing == expected


def test_project_without_roles_checks_two_kinds_only(vault_root: Path) -> None:
    """役割 0 個のプロジェクトは PJ 入力と成果物の 2 種だけを見る（Edge Cases）。"""
    declared = ProjectFolders(pj_input_dir="pj", role_dirs={}, outcome_dir="pj/nope")
    service = make_service(vault_root)
    result = service.check_project_folders(declared)
    assert result.missing == ["pj/nope"]
    assert result.ok is False


def test_ok_is_derived_from_missing_only(vault_root: Path, folders: ProjectFolders) -> None:
    """`001` FR-039a: `ok = not missing` の導出点は 1 つだけ。"""
    service = make_service(vault_root)
    present = service.check_project_folders(folders)
    assert present.ok is (present.missing == [])
    declared = ProjectFolders(pj_input_dir="pj/nope", role_dirs={}, outcome_dir="pj/outcome")
    absent = service.check_project_folders(declared)
    assert absent.ok is (absent.missing == [])
