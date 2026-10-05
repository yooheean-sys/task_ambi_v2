"""組 apps/api ⇄ settings: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=(BoardReader, VaultReader)`／
`provides=(SettingsReader, MemberResolver, Authorizer, CommandRegistry)`／**`has_router=True`**／
**`has_ui_page=True`**。外部前提は **領域 A の一時化**である。

⚠ **settings の `build` は不在時に雛形を書き込む**（段 (d1) の `_scaffold`）。したがって止め木は
`build` の**前**に効いていなければならず、事後確認では手遅れになる（FR-029・WD-09）。本ファイルは
`build` の前に `assert_env_path_under` を通し、`build` の後に**雛形が一時領域 A の配下にだけ**
生成されたことを確かめる。

依存契約は `_support/contract_stubs.py` の**契約準拠スタブ**で埋める（**新しいスタブを書かない**＝
FR-009）。スタブを受ける変数には該当 Protocol 型を**必ず注釈する**（憲章 §3・規約 §3-2）——注釈が
無いと pyright strict の構造的部分型の適合検査が発火しない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubKickoffRunner,
    StubVaultReader,
)
from conftest import (
    WiringCase,
    assemble,
    assert_env_path_under,
    expected_lifespans,
    make_case,
    resolve_deps_handle,
)

from api.exceptions import ExtraneousOverrideError, UnsatisfiedContractError
from api.wiring import require
from common.interfaces.board import BoardReader
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.settings import Authorizer, CommandRegistry, MemberResolver, SettingsReader
from common.interfaces.vault import VaultReader
from settings import SettingsEnv

if TYPE_CHECKING:
    from pathlib import Path

WIRING_COVERS = ("settings",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temp_area_a: Path,
    isolated_env_file: Path,
) -> WiringCase:
    """settings の組の記述（領域 A を一時化し、`build` の**前**に解決先を確かめる）。

    `overrides` は宣言 `requires` の 2 件とちょうど同じ鍵を持つ。スタブは**先に Protocol 型で注釈
    した変数**を参照して入れる（規約 §3-2）。

    Returns:
        実 registry の settings エントリ 1 件を組み立てる `WiringCase`。
    """
    monkeypatch.setenv("SETTINGS_ROOT", str(temp_area_a))
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=isolated_env_file)

    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)
    vault_reader: VaultReader = StubVaultReader(log)
    overrides: dict[type[object], object] = {
        BoardReader: board_reader,
        VaultReader: vault_reader,
    }
    return make_case(MODULE, overrides, log)


def test_settings_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ settings: 組み上がり、宣言 `provides` の 4
    契約が全部取り出せる（FR-008・FR-010）。

    見る欄: `registration.provides`（4 件）。⚠ 4
    契約は**同一インスタンス**であるため「宣言の鍵と値の
    対応を取り違えた」誤りは実体では区別できない（Edge Case）——その区別は
    `test_wiring_contract_declarations.py` の型突合（US3）が担う。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    settings_reader: SettingsReader = require(wiring.provided, SettingsReader)
    member_resolver: MemberResolver = require(wiring.provided, MemberResolver)
    authorizer: Authorizer = require(wiring.provided, Authorizer)
    command_registry: CommandRegistry = require(wiring.provided, CommandRegistry)
    assert settings_reader is resolved[SettingsReader]
    assert member_resolver is resolved[MemberResolver]
    assert authorizer is resolved[Authorizer]
    assert command_registry is resolved[CommandRegistry]


def test_settings_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ settings: `Wiring.provided` の鍵集合が `requires ∪ provides` に一致（FR-011
    (a)）。

    見る欄: `registration.requires`（2 件）・`registration.provides`（4 件）。override は
    `_run_builds` で `resolved` の初期値になり `provided` に**残る**（`002` data-model §5）ので、
    鍵は 6 件になる。期待集合は**登録の 2 欄から導く**。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_settings_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ settings: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（2 件）。宣言が実際に絞り込みの根拠になっていることを示す。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_settings_t3b_missing_override_fails_the_assembly(case: WiringCase) -> None:
    """組 apps/api ⇄ settings: 差し替え写像を 1 件欠くと組み立てが失敗する（FR-011 (c)）。

    見る欄: `registration.requires`（2 件）。**落とす契約は宣言から導く**（契約名を書かない）。
    `requires` が非空の 5 MOD にだけ置く主張である（規約 §3-3）。
    """
    dropped = case.registration.requires[0]
    partial = {
        contract: value for contract, value in case.overrides.items() if contract is not dropped
    }

    with pytest.raises(UnsatisfiedContractError):
        assemble(case, overrides=partial)


def test_settings_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ settings: `has_router` の宣言と実 `Wiring.routers` の件数が一致（FR-018・US4
    AC-1）。

    見る欄: `registration.has_router`（True）。**宣言 `True` 側の 3 件のうちの 1 つ**で、公開経路が
    実際に 1 本現れる側である。**期待値は宣言から導く**（`registry.py` の反転で赤くなることが要件
    ＝ SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_settings_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ settings: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `SettingsHandle` の `lifespan` 欄（不在）。正は `<Mod>Handle` の宣言属性（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_settings_t0_scaffold_is_written_only_under_the_temporary_area_a(
    case: WiringCase, temp_area_a: Path
) -> None:
    """組 apps/api ⇄ settings: `build` の雛形が一時領域 A の配下にだけ生成される（FR-028・SC-012）。

    見る欄: —（外部前提。`SettingsEnv.SETTINGS_ROOT`）。段 (d1) の `_scaffold` が実際に書き込むこと
    と、それが止め木の内側に閉じていることを、同じテストで実出力から示す（WD-34）。
    """
    assert not any(temp_area_a.iterdir())

    assemble(case)

    written = sorted(entry.name for entry in temp_area_a.iterdir())
    assert written, "build が雛形を 1 つも書いていない（`_scaffold` の前提が変わった可能性）"
    for entry in temp_area_a.rglob("*"):
        assert entry.resolve().is_relative_to(temp_area_a.resolve())
