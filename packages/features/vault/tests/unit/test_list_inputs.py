"""`list_inputs` の列挙・除外・枝刈り・並び順（US1・SC-008・FR-036〜038・FR-037a）。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from vault_testing import link, log_extra, make_adapter, make_service, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import FileRef, VaultArea, VaultNotFound
from common.kernel.enums import PersonaKey
from vault import VaultConfig
from vault.exceptions import FsIoError


def _rels(refs: list[FileRef]) -> list[str]:
    return [ref.rel_path for ref in refs]


def test_lists_files_recursively_and_only_files(vault_root: Path, folders: ProjectFolders) -> None:
    write_file(vault_root, "pj/a.md", "A")
    write_file(vault_root, "pj/sub/b.md", "B")
    write_file(vault_root, "pj/roles/planner/p.md", "P")
    write_file(vault_root, "pj/outcome/o.md", "O")
    service = make_service(vault_root)
    refs = service.list_inputs(folders, PersonaKey.planner)
    assert _rels(refs) == ["pj/a.md", "pj/sub/b.md", "pj/roles/planner/p.md", "pj/outcome/o.md"]
    assert all(ref.name == ref.rel_path.rsplit("/", 1)[-1] for ref in refs)


def test_excludes_archive_dot_prefixed_and_temp_files(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """除外 4 種のうち 3 種（`_archive`／ドット始まり／一時ファイル）。"""
    write_file(vault_root, "pj/a.md", "A")
    write_file(vault_root, "pj/outcome/_archive/old.md", "OLD")
    write_file(vault_root, "pj/.obsidian/workspace.json", "{}")
    write_file(vault_root, "pj/.hidden.md", "H")
    write_file(vault_root, "pj/outcome/.vaulttmp-deadbeef", "TMP")
    service = make_service(vault_root)
    assert _rels(service.list_inputs(folders, PersonaKey.planner)) == ["pj/a.md"]


def test_prunes_other_declared_area_roots_so_no_file_is_returned_twice(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """除外 4 種の 4 つ目（**宣言された他の領域の根**）＝ FR-037a・R-24。

    既定構成では `outcome_dir` が `pj_input_dir` の内側にあるため、枝刈りが無いと同じファイルが
    `pj_input` と `outcome` の 2 件で返る。
    """
    write_file(vault_root, "pj/outcome/o.md", "O")
    write_file(vault_root, "pj/roles/planner/p.md", "P")
    service = make_service(vault_root)
    refs = service.list_inputs(folders, PersonaKey.planner)
    assert len(_rels(refs)) == len(set(_rels(refs)))
    areas = {ref.rel_path: ref.area for ref in refs}
    assert areas["pj/outcome/o.md"] == VaultArea.outcome
    assert areas["pj/roles/planner/p.md"] == VaultArea.role


def test_unrequested_role_folder_yields_nothing(vault_root: Path, folders: ProjectFolders) -> None:
    """**指定されていない役割のフォルダが 0 件**（同じ枝刈りで塞がる）。"""
    write_file(vault_root, "pj/roles/planner/p.md", "P")
    write_file(vault_root, "pj/roles/writer/w.md", "W")
    service = make_service(vault_root)
    rels = _rels(service.list_inputs(folders, PersonaKey.planner))
    assert "pj/roles/planner/p.md" in rels
    assert "pj/roles/writer/w.md" not in rels


def test_prune_uses_identity_not_spelling(vault_root: Path, tmp_path: Path) -> None:
    """枝刈りの照合は**実体の同一性**で行う（綴りが違っても漏れない＝ R-24）。"""
    (vault_root / "pj" / "linked_outcome").mkdir()
    write_file(vault_root, "pj/linked_outcome/o.md", "O")
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        # 同じ実体を別の綴りで宣言する
        outcome_dir="pj/./linked_outcome",
    )
    service = make_service(vault_root)
    refs = service.list_inputs(declared, PersonaKey.planner)
    rels = _rels(refs)
    assert len(rels) == len(set(rels))
    assert sum(1 for r in rels if r.endswith("o.md")) == 1


def test_symlinked_directories_are_pruned(vault_root: Path, folders: ProjectFolders) -> None:
    """ディレクトリの symlink は刈る（領域外の木の露出と循環を同時に塞ぐ）。"""
    write_file(vault_root, "other/deep/x.md", "X")
    link(vault_root, "pj/loop", vault_root / "other")
    service = make_service(vault_root)
    assert _rels(service.list_inputs(folders, PersonaKey.planner)) == []


def test_ordering_is_area_then_codepoint_and_independent_of_dict_order(
    vault_root: Path,
) -> None:
    """FR-038: 領域（`pj_input`→`role`→`outcome`）→ `rel_path` のコードポイント順。"""
    write_file(vault_root, "pj/z.md", "Z")
    write_file(vault_root, "pj/a.md", "A")
    write_file(vault_root, "pj/roles/planner/p.md", "P")
    write_file(vault_root, "pj/outcome/o.md", "O")
    expected = ["pj/a.md", "pj/z.md", "pj/roles/planner/p.md", "pj/outcome/o.md"]
    service = make_service(vault_root)
    forward = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner", PersonaKey.writer: "pj/roles/writer"},
        outcome_dir="pj/outcome",
    )
    reversed_dict = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.writer: "pj/roles/writer", PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/outcome",
    )
    assert _rels(service.list_inputs(forward, PersonaKey.planner)) == expected
    assert _rels(service.list_inputs(reversed_dict, PersonaKey.planner)) == expected


def test_unregistered_role_raises_vault_not_found_with_empty_rel_path(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """役割未登録 → `VaultNotFound(rel_path="", role=role)`（**値を捏造しない**）。"""
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.list_inputs(folders, PersonaKey.architect)
    assert exc.value.rel_path == ""
    assert exc.value.role is PersonaKey.architect


def test_undeclared_role_is_named_in_the_log(
    vault_root: Path, folders: ProjectFolders, caplog: pytest.LogCaptureFixture
) -> None:
    """`rel_path` が空でも「どの役割が無かったか」が journal から読める（T092・VD-42）。

    `rel_path=""` は plan (v) の「値を捏造しない」の帰結で正しいので、役割名は `result` に
    畳む（`contracts/fs-adapter.md` §7 の 12 種を増やさない）。違反の行と見分ける印
    `"not_found"` は**前方一致**で残る。
    """
    service = make_service(vault_root)
    with caplog.at_level(logging.INFO, logger="vault.service"), pytest.raises(VaultNotFound):
        service.list_inputs(folders, PersonaKey.analyst)
    record = next(r for r in caplog.records if r.name == "vault.service")
    assert record.levelno == logging.WARNING
    assert log_extra(record, "rel_path") == ""
    assert log_extra(record, "result") == "not_found:analyst"
    assert str(log_extra(record, "result")).startswith("not_found")


def test_missing_area_folder_raises_instead_of_empty_list(vault_root: Path) -> None:
    """領域フォルダ不在 → `VaultNotFound(rel_path=<領域の rel>)`（**空に倒さない**＝ FR-040）。"""
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/nope",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.list_inputs(declared, PersonaKey.planner)
    assert exc.value.rel_path == "pj/nope"
    assert exc.value.role is None


def test_area_folder_that_is_a_file_is_not_found(vault_root: Path) -> None:
    write_file(vault_root, "pj/asfile", "F")
    declared = ProjectFolders(
        pj_input_dir="pj/asfile",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.list_inputs(declared, PersonaKey.planner)
    assert exc.value.rel_path == "pj/asfile"


def test_walk_raises_when_a_folder_becomes_unreadable(vault_root: Path) -> None:
    """列挙の途中で読めなくなったら `FsIoError`（短い一覧を静かに返さない＝ SC-007）。

    root 権限で走らせると `chmod(0o000)` が効かないため、**実際に読めなくなったときだけ**
    送出を検査する（`pytest.skip` を使わない＝ FR-071・US8 AC3 の「skip 0 件」）。
    """
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(rel="pj", area=VaultArea.pj_input, area_rels=("pj",))
    blocked = vault_root / "pj" / "blocked"
    blocked.mkdir()
    blocked.chmod(0o000)
    try:
        try:
            list(blocked.iterdir())
        except PermissionError:
            with pytest.raises(FsIoError):
                adapter.walk(resolved, prune_rels=())
        else:
            assert adapter.walk(resolved, prune_rels=()) == []
    finally:
        blocked.chmod(0o755)


def test_exclude_prefixes_come_from_config(vault_root: Path, folders: ProjectFolders) -> None:
    """除外接頭辞は設定から読む（`_archive` は `archive_dir` からの導出＝ R-10）。"""
    write_file(vault_root, "pj/a.md", "A")
    write_file(vault_root, "pj/~draft.md", "D")
    config = VaultConfig(list_exclude_prefixes=[".", "~"])
    service = make_service(vault_root, config)
    assert _rels(service.list_inputs(folders, PersonaKey.planner)) == ["pj/a.md"]
