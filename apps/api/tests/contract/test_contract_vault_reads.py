"""vault 提供の読取 IF（IF-31〜36）の適合検証。US3。

一時 vault の**実物**（ファイル木）に対して、`VaultReader` の 5 メソッドが台帳どおりの型を
返し台帳どおりの例外を投げることを確かめる。台帳の重複 2 行（`check_project_folders`＝
IF-31・32）は**消費者ごとの観点で書き分ける**（FR-002a・conventions §4-1）。

台帳の出力欄の注記が振る舞いを規定している行（FR-016）を落とさない ——
IF-31/32「不在は例外にせず `missing` に載せる」・IF-33「空の一覧に倒さない」・
IF-35「不在は例外で明示。黙って空を返さない」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from contract_stores import VAULT_INPUT_NAME, VAULT_OUTCOME_NAME, VAULT_ROLE_NAME

from common.interfaces.vault import (
    FileRef,
    FolderCheck,
    VaultArea,
    VaultNotFound,
    VaultPathViolation,
)
from common.kernel.enums import PersonaKey

if TYPE_CHECKING:
    from contract_stores import TempVault

    from common.interfaces.vault import VaultReader

ABSENT_ROLE = PersonaKey.planner
"""一時 vault の `role_dirs` に**宣言が無い**役割（`VaultNotFound` を踏む）。"""

FORBIDDEN_FILENAME = "bad:name.md"
"""Windows 禁止文字を含む名前（名前ゲートが `forbidden_name` で弾く）。"""


def _ref(rel_path: str, area: VaultArea = VaultArea.pj_input) -> FileRef:
    """`read_input` に渡す参照（vault は `FileRef` を信用せず毎回再解決する）。"""
    return FileRef(
        area=area,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        size_bytes=0,
        mtime=datetime.now(tz=UTC),
    )


@pytest.mark.contract_if("IF-31")
def test_contract_vault_check_project_folders_lists_missing_for_orchestrator(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-31（orchestrator 観点）: 不在一覧を返す（**空＝OK**）。

    orchestrator は kickoff の前段でフォルダの実在を確かめる。**宣言の一部が実在しなくても
    例外にならず `FolderCheck.missing` に載る**ことが台帳の出力欄の注記である（FR-016）。
    """
    ok = vault_reader.check_project_folders(vault_store.folders)

    assert isinstance(ok, FolderCheck)
    assert ok.missing == []
    assert ok.ok is True

    with_absent = vault_store.folders.model_copy(
        update={"role_dirs": {PersonaKey.programmer: "pj/roles/absent"}}
    )
    partial = vault_reader.check_project_folders(with_absent)

    assert partial.ok is False
    assert partial.missing == ["pj/roles/absent"]


@pytest.mark.contract_if("IF-32")
def test_contract_vault_check_project_folders_confirms_existence_for_settings(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-32（settings 観点）: 登録・編集時の存在確認。

    settings は PJ の登録・編集で「宣言したフォルダが本当に在るか」を確かめる。**フォルダを
    1 つも作らない**（確認だけである）ことがこの観点の要である。
    """
    absent = vault_store.folders.model_copy(update={"outcome_dir": "pj/absent_outcome"})

    result = vault_reader.check_project_folders(absent)

    assert result.ok is False
    assert result.missing == ["pj/absent_outcome"]
    assert not (vault_store.root / "pj/absent_outcome").exists(), "確認でフォルダを作らない"


@pytest.mark.contract_if("IF-31")
def test_contract_vault_check_project_folders_raises_violation_for_orchestrator(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-31 例外系: 宣言が vault ルートの外を指すと `VaultPathViolation`（不在に倒さない）。"""
    with pytest.raises(VaultPathViolation) as caught:
        vault_reader.check_project_folders(vault_store.outside_folders)
    assert caught.value.kind == "outside_root"


@pytest.mark.contract_if("IF-32")
def test_contract_vault_check_project_folders_raises_violation_for_settings(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-32 例外系: 登録・編集で外を指す宣言が来たら `VaultPathViolation`。"""
    with pytest.raises(VaultPathViolation):
        vault_reader.check_project_folders(vault_store.outside_folders)


@pytest.mark.contract_if("IF-33")
def test_contract_vault_list_inputs_returns_file_refs(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-33 正常系: PJ 入力・役割フォルダ・兄弟成果物を `list[FileRef]` で返す。"""
    refs = vault_reader.list_inputs(vault_store.folders, PersonaKey.programmer)

    assert isinstance(refs, list)
    assert all(isinstance(ref, FileRef) for ref in refs)
    assert [ref.rel_path for ref in refs] == [
        f"pj/{VAULT_INPUT_NAME}",
        f"pj/roles/programmer/{VAULT_ROLE_NAME}",
        f"pj/outcome/{VAULT_OUTCOME_NAME}",
    ]
    assert [ref.area for ref in refs] == [VaultArea.pj_input, VaultArea.role, VaultArea.outcome]


@pytest.mark.contract_if("IF-33")
def test_contract_vault_list_inputs_raises_not_found_without_falling_back_to_empty(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-33 例外系: 役割が宣言に無ければ `VaultNotFound`（**空の一覧に倒さない**＝ FR-016）。"""
    with pytest.raises(VaultNotFound) as caught:
        vault_reader.list_inputs(vault_store.folders, ABSENT_ROLE)
    assert caught.value.role is ABSENT_ROLE


@pytest.mark.contract_if("IF-33")
def test_contract_vault_list_inputs_raises_not_found_when_area_folder_is_absent(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-33 例外系: **領域フォルダが実在しない**ときも `VaultNotFound`（FR-016）。

    台帳の例外欄は `VaultNotFound（役割が宣言に無い／**領域フォルダが実在しない**）` と
    2 条件を名指しする。現物はこの 2 つを**別分岐**で送出しており、載る値も異なる ——
    役割不在は `role=<その役割>`・`rel_path=""`（解決すべきパスが無いので値を捏造しない）、
    領域フォルダ不在は `role=None`・`rel_path=<解決値>`。ここでは後者を踏み、**前者と
    取り違えていない**ことを両フィールドで区別する。
    """
    absent_area = vault_store.folders.model_copy(
        update={"role_dirs": {PersonaKey.programmer: "pj/roles/absent"}}
    )

    with pytest.raises(VaultNotFound) as caught:
        vault_reader.list_inputs(absent_area, PersonaKey.programmer)

    assert caught.value.role is None, "役割不在の分岐と取り違えている"
    assert caught.value.rel_path == "pj/roles/absent"


@pytest.mark.contract_if("IF-33")
def test_contract_vault_list_inputs_raises_violation(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-33 例外系: 宣言が vault ルートの外を指すと `VaultPathViolation`。"""
    with pytest.raises(VaultPathViolation):
        vault_reader.list_inputs(vault_store.outside_folders, PersonaKey.programmer)


@pytest.mark.contract_if("IF-34")
def test_contract_vault_read_input_returns_text(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-34 正常系: 入力ファイルの本文が `str` で返る。"""
    refs = vault_reader.list_inputs(vault_store.folders, PersonaKey.programmer)

    body = vault_reader.read_input(vault_store.folders, refs[0])

    assert isinstance(body, str)
    assert body == (vault_store.root / f"pj/{VAULT_INPUT_NAME}").read_text(encoding="utf-8")


@pytest.mark.contract_if("IF-34")
def test_contract_vault_read_input_raises_not_found(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-34 例外系: 指定のファイルが無ければ `VaultNotFound`。"""
    with pytest.raises(VaultNotFound):
        vault_reader.read_input(vault_store.folders, _ref("pj/absent.md"))


@pytest.mark.contract_if("IF-34")
def test_contract_vault_read_input_raises_violation(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-34 例外系: `FileRef` が改竄されていても領域の外へは出られない（`VaultPathViolation`）。"""
    with pytest.raises(VaultPathViolation) as caught:
        vault_reader.read_input(vault_store.folders, _ref("../outside/secret.md"))
    assert caught.value.kind == "outside_root"


@pytest.mark.contract_if("IF-35")
def test_contract_vault_read_outcome_file_returns_text(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-35 正常系: 成果物ファイルの本文が `str` で返る。"""
    body = vault_reader.read_outcome_file(vault_store.folders, VAULT_OUTCOME_NAME)

    assert isinstance(body, str)
    assert body == (vault_store.root / f"pj/outcome/{VAULT_OUTCOME_NAME}").read_text(
        encoding="utf-8"
    )


@pytest.mark.contract_if("IF-35")
def test_contract_vault_read_outcome_file_raises_not_found_instead_of_empty_text(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-35 例外系: 不在は例外で明示する（**黙って空を返さない**＝ FR-016・FN-13）。"""
    with pytest.raises(VaultNotFound):
        vault_reader.read_outcome_file(vault_store.folders, "absent.md")


@pytest.mark.contract_if("IF-35")
def test_contract_vault_read_outcome_file_raises_violation(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-35 例外系: 宣言が vault ルートの外を指すと `VaultPathViolation`。"""
    with pytest.raises(VaultPathViolation):
        vault_reader.read_outcome_file(vault_store.outside_folders, VAULT_OUTCOME_NAME)


@pytest.mark.contract_if("IF-36")
def test_contract_vault_outcome_exists_returns_bool(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-36 正常系: 完了ゲートの存在確認が `bool` を返す。"""
    present = vault_reader.outcome_exists(vault_store.folders, VAULT_OUTCOME_NAME)
    absent = vault_reader.outcome_exists(vault_store.folders, "absent.md")

    assert isinstance(present, bool)
    assert present is True
    assert absent is False


@pytest.mark.contract_if("IF-36")
def test_contract_vault_outcome_exists_raises_violation_without_falling_back_to_false(
    vault_reader: VaultReader, vault_store: TempVault
) -> None:
    """IF-36 例外系: 名前の違反を **`False` に倒さない**（FR-044）。"""
    with pytest.raises(VaultPathViolation) as caught:
        vault_reader.outcome_exists(vault_store.folders, FORBIDDEN_FILENAME)
    assert caught.value.kind == "forbidden_name"
