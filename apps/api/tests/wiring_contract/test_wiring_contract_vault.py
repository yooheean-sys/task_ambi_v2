"""組 apps/api ⇄ vault: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=()`／`provides=(VaultReader, OutcomeWriter)`／
`has_router=False`／`has_ui_page=False`。外部前提は **`VAULT_ROOT` の一時化**である。

本ファイルが確立するのは **`build` の前の能動検査**（data-model §4 の段 2 → 3 → 4 → 5）である——
止め木は `build` の**前**に効いていなければならず、事後確認では手遅れになる。vault の `build` は
`VAULT_ROOT` の実在確認しかしないが、settings（`_scaffold`）と trigger（`ensure_template`）は
**雛形を書き込む**ため、同じ型を 4 MOD で使う（FR-029）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import CallLog, StubKickoffRunner
from conftest import (
    WiringCase,
    assemble,
    assert_env_path_under,
    expected_lifespans,
    make_case,
    resolve_deps_handle,
)

from api.exceptions import ExtraneousOverrideError
from api.wiring import require
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.vault import OutcomeWriter, VaultReader
from vault import VaultEnv

if TYPE_CHECKING:
    from pathlib import Path

WIRING_COVERS = ("vault",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temp_vault_root: Path,
    isolated_env_file: Path,
) -> WiringCase:
    """vault の組の記述（保管庫ルートを一時化し、`build` の**前**に解決先を確かめる）。

    `isolated_env_file` は autouse だが**名前でも要求する**——`vault.env_file_path` を import 済みの
    名前で呼ぶと差し替え**前**の関数を掴むので、差し替えた `.env` の所在は fixture 経由で受け取る。

    Returns:
        実 registry の vault エントリ 1 件を組み立てる `WiringCase`。
    """
    monkeypatch.setenv("VAULT_ROOT", str(temp_vault_root))
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=isolated_env_file)

    log = CallLog()
    overrides: dict[type[object], object] = {}
    return make_case(MODULE, overrides, log)


def test_vault_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ vault: 組み上がり、宣言 `provides` の 2
    契約が全部取り出せる（FR-008・FR-010）。

    見る欄: `registration.provides`（2 件）。期待する契約は**登録の `provides` から導く**。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    vault_reader: VaultReader = require(wiring.provided, VaultReader)
    outcome_writer: OutcomeWriter = require(wiring.provided, OutcomeWriter)
    assert vault_reader is resolved[VaultReader]
    assert outcome_writer is resolved[OutcomeWriter]


def test_vault_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ vault: `Wiring.provided` の鍵集合が `requires ∪ provides`
    に一致（FR-011 (a)）。

    見る欄: `registration.requires`（空）・`registration.provides`（2 件）。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_vault_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ vault: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（空）。⚠ **T3b（1 件欠く）は置かない**——`requires=()` なので
    「欠く」対象が構造上存在しない（規約 §3-3）。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_vault_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ vault: `has_router` の宣言と実 `Wiring.routers` の件数が一致（FR-018・GAP-1）。

    見る欄: `registration.has_router`（False）。**期待値は宣言から導く**（SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_vault_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ vault: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `VaultHandle` の `lifespan` 欄（不在）。正は `<Mod>Handle`
    の宣言属性である（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_vault_t0_stub_out_resolves_under_the_temporary_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temp_vault_root: Path,
    isolated_env_file: Path,
) -> None:
    """組 apps/api ⇄ vault: 止め木の解決先が一時ディレクトリ配下である（FR-029・SC-012）。

    見る欄: —（外部前提。`VaultEnv.VAULT_ROOT`）。`case` fixture が `build` の**前**に通している
    能動検査を、独立したテストとしても置く——⚠ 止め木が効くのは `<Mod>Env` の明示 `__init__` と
    `build()` 側の `_env_file` 指定が**対で**在るときだけであり、片方が失われるとテストは緑のまま
    実運用ストアを掴む（`013` の実測）。
    """
    monkeypatch.setenv("VAULT_ROOT", str(temp_vault_root))

    resolved = assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=isolated_env_file)

    assert resolved == temp_vault_root.resolve()
