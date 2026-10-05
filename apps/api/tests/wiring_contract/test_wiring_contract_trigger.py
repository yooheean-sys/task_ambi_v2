"""組 apps/api ⇄ trigger: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=(KickoffRunner,)`／**`provides=()`（空。`011` R-02 の初例）
**／
**`has_router=True`**／**`has_ui_page=True`**。外部前提は **`TRIGGER_ROOT` の一時化**である。

**GAP-2 を埋める組**である —— trigger の `lifespan` が**実 registry 経由で** `Wiring.lifespans` に
載ることを確かめる。⚠ **`ModuleRegistration` に `lifespan` の宣言フィールドが無い**（6 欄）。
`lifespan` は `WiredModule` と `Wiring.lifespans` にしか現れず、**アダプタ関数が渡すかどうかが唯一の
分岐**である。したがって期待値の正は **`TriggerHandle` の宣言属性**になる（**B-03**）。

⚠ **`provides=()` は `_run_builds` の集合一致（`set(()) == set({})`）を素通りする**（`011` R-02）。
「提供が空」と「提供の写し忘れ」が同じ表現になるため、この組の検証は `router` と `lifespan` の側で
成立させる。

⚠ trigger の `build` は `ensure_template` が `triggers.yaml` を**書き込む**ので、止め木は `build` の
**前**に効かせる（FR-029・WD-09）。`DOWNLOADS_DIR` は止め木の 1 段目で消えて `None` に落ちるため、
`file` 種別の常駐は起きない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import CallLog, StubBoardReader, StubKickoffRunner
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
from trigger import TriggerEnv

if TYPE_CHECKING:
    from pathlib import Path

WIRING_COVERS = ("trigger",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temp_area_b: Path,
    isolated_env_file: Path,
) -> WiringCase:
    """trigger の組の記述（領域 B を一時化し、`build` の**前**に解決先を確かめる）。

    差し替え点 `trigger.env_file_path` は `013` の 4 件に足した **5 件目**である（WD-28・B-10）。

    Returns:
        実 registry の trigger エントリ 1 件を組み立てる `WiringCase`。
    """
    monkeypatch.setenv("TRIGGER_ROOT", str(temp_area_b))
    assert_env_path_under(TriggerEnv, "TRIGGER_ROOT", tmp_path, env_file=isolated_env_file)

    log = CallLog()
    kickoff_runner: KickoffRunner = StubKickoffRunner(log)
    overrides: dict[type[object], object] = {KickoffRunner: kickoff_runner}
    return make_case(MODULE, overrides, log)


def test_trigger_t1_declared_provides_is_empty_and_nothing_is_exposed(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: 宣言 `provides` が**空**であり、取り出す契約が 0
    件（FR-008・FR-010）。

    見る欄: `registration.provides`（空）。**リテラルの空タプルを書かず宣言から導く**。

    ⚠ 「提供が空」と「提供の写し忘れ」は `_run_builds` の集合一致では同じ表現になる
    （`set(()) == set({})`＝ `011` R-02）ため、この組の検証は `router`（T4）と `lifespan`（T5・T7）
    の
    側で成立させる。
    """
    wiring = assemble(case)

    # 他 7 組と**同じ形**で走査する。`provides` が空なので内包の本体は 1 度も評価されない
    # ——「取り出す契約が 0 件であること」が宣言から導かれていることが、この形の要点である。
    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }

    assert len(resolved) == len(case.registration.provides)
    assert not set(wiring.provided) & set(case.registration.provides)
    assert wiring.modules == (MODULE,)


def test_trigger_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: `Wiring.provided` の鍵集合が `requires ∪ provides` に一致（FR-011
    (a)）。

    見る欄: `registration.requires`（1 件）・`registration.provides`（空）。`provides` が空なので
    鍵は override の `KickoffRunner` **1 件のみ**になる（override は `provided` に**残る**＝
    `002` data-model §5）。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_trigger_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（1 件）。
    """
    board_reader: BoardReader = StubBoardReader(case.call_log)
    assert BoardReader not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, BoardReader: board_reader}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_trigger_t3b_missing_override_fails_the_assembly(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: 差し替え写像を 1 件欠くと組み立てが失敗する（FR-011 (c)）。

    見る欄: `registration.requires`（1 件）。**落とす契約は宣言から導く**。
    """
    dropped = case.registration.requires[0]
    partial = {
        contract: value for contract, value in case.overrides.items() if contract is not dropped
    }

    with pytest.raises(UnsatisfiedContractError):
        assemble(case, overrides=partial)


def test_trigger_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: `has_router` の宣言と実 `Wiring.routers` の件数が一致（FR-018）。

    見る欄: `registration.has_router`（True）。**宣言 `True` 側の 3 件のうちの 3 件目**である。
    `provides` が空なので、この組で「アダプタが実体を写したか」を示すのは `router` と `lifespan`
    だけである。**期待値は宣言から導く**（SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_trigger_t5_one_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: 後始末が **1 件**積まれる（FR-020・GAP-2・US4 AC-4）。

    見る欄: `TriggerHandle` の `lifespan` 欄（**在り**）。⚠ `ModuleRegistration` に宣言フィールドが
    **無い**（**B-03**）ので、期待値は `TriggerHandle` の宣言属性から導く——`lifespan` を渡すのは
    `_build_trigger` だけであり、アダプタが渡し忘れればここが赤くなる。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert expected_lifespans(handle_cls) == 1
    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_trigger_t7_lifespan_returns_an_async_context_manager(case: WiringCase) -> None:
    """組 apps/api ⇄ trigger: `lifespans[0]()` が非同期コンテキストを返す。
    **開始しない**（FR-020）。

    見る欄: `TriggerHandle.lifespan`。⚠ **`async with` に入れない**（常駐スレッドを起こさない）。

    開始・終了の意味論は `integration/test_smoke_lifespan.py` が、毎回新しいコンテキストを返すことは
    trigger の feature 単体テストが既に担保している（spec §0-6 の除外表）。⚠ **実 trigger の
    `lifespan` を開始する検証はリポジトリ全体で 0 本のまま残る**（`012` の領分＝ WD-18・WD-29）。
    """
    wiring = assemble(case)

    context = wiring.lifespans[0]()

    assert hasattr(context, "__aenter__")
    assert hasattr(context, "__aexit__")


def test_trigger_t0_template_is_written_only_under_the_temporary_area_b(
    case: WiringCase, temp_area_b: Path
) -> None:
    """組 apps/api ⇄ trigger: `build` の雛形が一時領域 B の配下にだけ生成される（FR-028・SC-012）。

    見る欄: —（外部前提。`TriggerEnv.TRIGGER_ROOT`）。`ensure_template` が実際に書き込むことと、
    それが止め木の内側に閉じていることを実出力から示す。
    """
    assert not any(temp_area_b.iterdir())

    assemble(case)

    written = sorted(entry.name for entry in temp_area_b.iterdir())
    assert written, "build が雛形を 1 つも書いていない（`ensure_template` の前提が変わった可能性）"
    for entry in temp_area_b.rglob("*"):
        assert entry.resolve().is_relative_to(temp_area_b.resolve())
