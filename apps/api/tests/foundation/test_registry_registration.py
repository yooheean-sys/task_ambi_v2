"""apps/api の登録点（土台の 4 つ目）の整合検査 REG-8a〜h。

判定規則は `specs/002-api-composition-root/contracts/module-registration.md` §3。集合の定義は
`000` の REG-1〜7（[test_module_registration.py](./test_module_registration.py)）と共有する
（`S0` = 実在 feature パッケージ、`S3` = ルート `config.json` の `enabled_modules`）。

本 feature 完了時点は `S0 = S3 = name 集合 = ∅` であり、**空集合どうしの一致で通ること自体は
保護の証明にならない**。負の検証（意図的な違反を置くと赤くなること）は本ファイル末尾の
`test_reg8*_detects_*` が偽の登録で担う（CHK003）。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from _support.fake_modules import (
    BoardContract,
    CallRecorder,
    SettingsContract,
    VaultContract,
    make_registration,
)

from api.exceptions import DuplicateProviderError, UnsatisfiedContractError
from api.registry import MODULE_REGISTRATIONS
from api.wiring import ModuleRegistration, check_registration_graph
from ui.pages import UI_PAGE_REGISTRATIONS


def _repo_root() -> Path:
    """`[tool.uv.workspace]` を持つ `pyproject.toml` を上方向に探す（cwd 非依存）。"""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            data: dict[str, Any] = tomllib.loads(candidate.read_text(encoding="utf-8"))
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return parent
    raise AssertionError(
        "workspace ルート（[tool.uv.workspace] を持つ pyproject.toml）が見つからない"
    )


@pytest.fixture(scope="module")
def root() -> Path:
    """リポジトリルート。"""
    return _repo_root()


@pytest.fixture(scope="module")
def s0_existing(root: Path) -> set[str]:
    """S0: 実在 feature パッケージ（`packages/features/*/src/<name>/`）。"""
    return {
        src.name
        for src in (root / "packages" / "features").glob("*/src/*")
        if src.is_dir() and src.name != "__pycache__"
    }


@pytest.fixture(scope="module")
def s3_enabled(root: Path) -> list[str]:
    """S3: ルート `config.json` の `enabled_modules`（重複検査のため list のまま返す）。"""
    path = root / "config.json"
    assert path.is_file(), f"ルート config.json が実在しない: {path}"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw: Any = data["enabled_modules"]
    return [str(name) for name in raw]


@pytest.fixture(scope="module")
def registration_names() -> list[str]:
    """`MODULE_REGISTRATIONS` の `name`（重複検査のため list のまま返す）。"""
    return [registration.name for registration in MODULE_REGISTRATIONS]


def test_reg8a_registration_names_match_existing_features(
    s0_existing: set[str], registration_names: list[str]
) -> None:
    """REG-8a: `name` 集合 == S0（実在するのに未登録／登録があるのに実在しない を落とす）。"""
    registered = set(registration_names)
    missing = sorted(s0_existing - registered)
    orphans = sorted(registered - s0_existing)
    assert not missing and not orphans, (
        "apps/api/src/api/registry.py の登録が実在 feature と一致しない（登録点 4/4）。"
        f" 実在するのに登録が無い: {missing} → registry.py に 1 エントリ足すこと /"
        f" 登録があるのに実在しない: {orphans} → packages/features/<mod>/ を作るか登録を削ること"
    )


def test_reg8b_registration_names_are_unique(registration_names: list[str]) -> None:
    """REG-8b: `name` に重複が無い。"""
    duplicates = sorted({name for name in registration_names if registration_names.count(name) > 1})
    assert not duplicates, f"registry.py に同じモジュールの登録が 2 回ある: {duplicates}"


def test_reg8c_enabled_modules_are_registered(
    s3_enabled: list[str], registration_names: list[str]
) -> None:
    """REG-8c: S3 ⊆ `name` 集合（起動時に `UnknownModuleError` になる状態を先に落とす）。"""
    unregistered = sorted(set(s3_enabled) - set(registration_names))
    assert not unregistered, (
        "ルート config.json の enabled_modules に registry.py の登録が無い名前がある"
        f"（起動時に UnknownModuleError になる）: {unregistered}"
    )


def test_reg8d_requires_and_provides_are_disjoint() -> None:
    """REG-8d: 各登録の `requires` ∩ `provides` == ∅（自分の提供物を自分で要求しない）。"""
    overlaps = {
        registration.name: sorted(
            str(contract) for contract in set(registration.requires) & set(registration.provides)
        )
        for registration in MODULE_REGISTRATIONS
        if set(registration.requires) & set(registration.provides)
    }
    assert not overlaps, f"自分の提供物を自分で要求している登録がある: {overlaps}"


def test_reg8e_provides_are_unique_across_registrations() -> None:
    """REG-8e: `provides` が登録集合全体で一意（2 つのモジュールが同じ契約を提供しない）。"""
    seen: dict[type[object], str] = {}
    conflicts: list[str] = []
    for registration in MODULE_REGISTRATIONS:
        for contract in registration.provides:
            owner = seen.get(contract)
            if owner is not None:
                conflicts.append(f"{contract} を {owner} と {registration.name} が提供")
            else:
                seen[contract] = registration.name
    assert not conflicts, f"同じ契約を 2 つ以上のモジュールが提供している: {conflicts}"


def test_reg8f_all_registrations_enabled_form_a_closed_graph() -> None:
    """REG-8f: 全登録を有効化した検査が未充足 0・輪 0 で成立する（組み立ては行わない）。"""
    order = check_registration_graph(MODULE_REGISTRATIONS)

    assert set(order) == {registration.name for registration in MODULE_REGISTRATIONS}


def test_reg8g_enabled_modules_have_no_duplicates(s3_enabled: list[str]) -> None:
    """REG-8g・FR-049: `enabled_modules` に重複が無い。

    起動時に `DuplicateModuleError` になる状態を、整合検査の側で先に落とす。
    """
    duplicates = sorted({name for name in s3_enabled if s3_enabled.count(name) > 1})
    assert not duplicates, (
        f"ルート config.json の enabled_modules に重複がある（起動時に DuplicateModuleError に"
        f" なる）: {duplicates}"
    )


def test_reg8h_has_ui_page_matches_ui_page_registrations() -> None:
    """REG-8h: `has_ui_page=True` の登録名の集合 == `UI_PAGE_REGISTRATIONS` の `module` 集合。"""
    declared = {
        registration.name for registration in MODULE_REGISTRATIONS if registration.has_ui_page
    }
    registered = {page.module for page in UI_PAGE_REGISTRATIONS}
    assert declared == registered, (
        "UI ページの有無の宣言が 2 箇所で食い違っている。"
        f" apps/api の has_ui_page=True: {sorted(declared)} /"
        f" apps/ui の UI_PAGE_REGISTRATIONS: {sorted(registered)}"
    )


def _fake_pair() -> tuple[ModuleRegistration, ModuleRegistration]:
    """負の検証で使う偽の登録 2 件（board 相当を提供する側と、それを要求する側）。"""
    recorder = CallRecorder()
    provider = make_registration(name="fake-a", recorder=recorder, provides=(BoardContract,))
    consumer = make_registration(
        name="fake-b", recorder=recorder, requires=(BoardContract,), provides=(SettingsContract,)
    )
    return provider, consumer


def test_reg8f_detects_an_unsatisfied_contract() -> None:
    """負の検証（CHK003）: 供給元の無い契約を置くと REG-8f の検査が赤くなる。"""
    _, consumer = _fake_pair()

    with pytest.raises(UnsatisfiedContractError):
        check_registration_graph((consumer,))


def test_reg8f_detects_a_duplicate_provider() -> None:
    """負の検証（CHK003）: 同じ契約を 2 つが提供すると REG-8f の検査が赤くなる。"""
    recorder = CallRecorder()
    first = make_registration(name="fake-a", recorder=recorder, provides=(VaultContract,))
    second = make_registration(name="fake-b", recorder=recorder, provides=(VaultContract,))

    with pytest.raises(DuplicateProviderError):
        check_registration_graph((first, second))


def test_reg8f_accepts_a_closed_graph() -> None:
    """負の検証の対（CHK003）: 閉じた依存関係なら REG-8f の検査は通り、順序を返す。"""
    provider, consumer = _fake_pair()

    assert check_registration_graph((consumer, provider)) == ("fake-a", "fake-b")
