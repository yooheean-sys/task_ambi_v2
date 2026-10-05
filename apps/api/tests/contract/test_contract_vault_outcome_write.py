"""vault 提供の書込 IF（IF-37 `write_outcome`）の適合検証。US3。

台帳の出力欄の注記が振る舞いを規定している（FR-016）—— **返すのは vault ルート相対**。
例外欄も 2 種を宣言している —— `VaultPathViolation`（規則違反）と
`VaultNotFound`（`outcome_dir` が実在しない。**フォルダを作らない**＝ VD-19）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from common.interfaces.vault import VaultNotFound, VaultPathViolation, WrittenPath

if TYPE_CHECKING:
    from contract_stores import TempVault

    from common.interfaces.vault import OutcomeWriter

BODY = "契約テストが書く成果物の本文\n"
NEW_NAME = "written.md"
FORBIDDEN_NAME = "bad:name.md"
"""Windows 禁止文字（`:`）を含む名前。**Linux では正当な綴り**なので、名前ゲートが無ければ
実際にファイルが作られる —— 「書込が起きていない」ことを主張する意味がここにある。"""


@pytest.mark.contract_if("IF-37")
def test_contract_vault_write_outcome_returns_vault_root_relative_path(
    outcome_writer: OutcomeWriter, vault_store: TempVault
) -> None:
    """IF-37 正常系: 通過した実パスを **vault ルート相対**で返す（FR-016・OB-6）。"""
    written = outcome_writer.write_outcome(vault_store.folders, NEW_NAME, BODY)

    assert isinstance(written, WrittenPath)
    assert written.rel_path == f"{vault_store.folders.outcome_dir}/{NEW_NAME}"
    assert not written.rel_path.startswith("/"), "絶対パスを返さない（vault ルート相対）"
    assert written.overwritten is False
    assert written.archived_to is None
    assert (vault_store.root / written.rel_path).read_text(encoding="utf-8") == BODY


@pytest.mark.contract_if("IF-37")
def test_contract_vault_write_outcome_raises_violation(
    outcome_writer: OutcomeWriter, vault_store: TempVault
) -> None:
    """IF-37 例外系: 宣言が vault ルートの外を指すと `VaultPathViolation`。"""
    with pytest.raises(VaultPathViolation) as caught:
        outcome_writer.write_outcome(vault_store.outside_folders, NEW_NAME, BODY)

    assert caught.value.kind == "outside_root"
    assert not (vault_store.outside / NEW_NAME).exists(), "ルート外へ書き出さない"


@pytest.mark.contract_if("IF-37")
def test_contract_vault_write_outcome_raises_violation_on_forbidden_name(
    outcome_writer: OutcomeWriter, vault_store: TempVault
) -> None:
    """IF-37 例外系: **Windows 禁止文字**の名前を名前ゲート（鎖の (1) 段）が弾く（FR-016）。

    台帳の出力欄は `WrittenPath（外縁ゲート・Windows 禁止文字ゲートを通過した実パス…）` と
    **2 つのゲート**を名指しするが、上の 2 本が踏むのは外縁ゲート（`outside_root`）と
    `outcome_dir` 不在だけで、名前ゲートは 1 度も通らない。

    名前ゲートはゲート鎖の**最初**（内容ゲートより前・resolve より前）に在るので、
    止まったときは**後段が 1 つも走っていない**はずである。成果物フォルダの中身が
    ゲート前後で 1 バイトも動かないことまで主張する（`write_atomic` の一時ファイルが
    残っていないことも同時に見える）。
    """
    outcome_root = vault_store.root / vault_store.folders.outcome_dir
    before = sorted(path.name for path in outcome_root.iterdir())

    with pytest.raises(VaultPathViolation) as caught:
        outcome_writer.write_outcome(vault_store.folders, FORBIDDEN_NAME, BODY)

    assert caught.value.kind == "forbidden_name"
    assert sorted(path.name for path in outcome_root.iterdir()) == before, (
        "名前ゲートで止まったのに後段（退避・原子的書込）が走っている"
    )
    assert not (outcome_root / FORBIDDEN_NAME).exists()


@pytest.mark.contract_if("IF-37")
def test_contract_vault_write_outcome_raises_not_found_without_creating_the_folder(
    outcome_writer: OutcomeWriter, vault_store: TempVault
) -> None:
    """IF-37 例外系: `outcome_dir` が実在しなければ `VaultNotFound`（**フォルダを作らない**）。"""
    absent = vault_store.folders.model_copy(update={"outcome_dir": "pj/absent_outcome"})

    with pytest.raises(VaultNotFound):
        outcome_writer.write_outcome(absent, NEW_NAME, BODY)

    assert not (vault_store.root / "pj/absent_outcome").exists(), "VD-19: フォルダを作らない"
