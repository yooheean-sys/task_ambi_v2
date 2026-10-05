"""組 apps/api ⇄ stages: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=()`／`provides=(StageCatalog, ProposalGate)`／
`has_router=False`／`has_ui_page=False`。**外部ストアもスタブも要らない**最軽量の組で、外部前提は
自 `config.json` のみである。

検証単位は規約 §3 の 4 つだけ——実 registry エントリちょうど 1 つ・その MOD の実 `build` ファクトリ・
依存 Protocol の契約準拠スタブ（本組は 0 件）・所有外部ストアの実物（本組は不要）。**偽の登録・
手配線・実装クラスの直接生成を代用にしない**（FR-003）。
"""

from __future__ import annotations

import pytest
from _support.contract_stubs import CallLog, StubKickoffRunner
from conftest import WiringCase, assemble, expected_lifespans, make_case, resolve_deps_handle

from api.exceptions import ExtraneousOverrideError
from api.wiring import require
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.stages import ProposalGate, StageCatalog

WIRING_COVERS = ("stages",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case() -> WiringCase:
    """stages の組の記述（`overrides` は**空**＝ `requires=()`・一時ストア不要）。

    Returns:
        実 registry の stages エントリ 1 件を組み立てる `WiringCase`。
    """
    log = CallLog()
    overrides: dict[type[object], object] = {}
    return make_case(MODULE, overrides, log)


def test_stages_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ stages: 組み上がり、宣言 `provides` の 2
    契約が全部取り出せる（FR-008・FR-010）。

    見る欄: `registration.provides`（2 件）。期待する契約は**登録の `provides` から導く**（契約名を
    期待値として書かない）。取り出しに `api.wiring.require` を使うことで `typing.cast` をテストに
    書かずに済む（規約 §3-2）。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する（pyright strict が構造適合を検査する）
    stage_catalog: StageCatalog = require(wiring.provided, StageCatalog)
    proposal_gate: ProposalGate = require(wiring.provided, ProposalGate)
    assert stage_catalog is resolved[StageCatalog]
    assert proposal_gate is resolved[ProposalGate]


def test_stages_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ stages: `Wiring.provided` の鍵集合が `requires ∪ provides`
    に一致（FR-011 (a)）。

    見る欄: `registration.requires`（空）・`registration.provides`（2 件）。期待集合を**登録の 2 欄
    から導く**。override は `_run_builds` で `resolved` の初期値になり `provided` に**残る**
    （`002` data-model §5）ので、単独組み立てでは宣言の両側を 1 つの実出力で突き合わせられる。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_stages_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ stages: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（空）。
    宣言が実際に絞り込みの根拠になっていることを実出力で示す。

    ⚠ **T3b（1 件欠く）は置かない**——`requires=()` なので「欠く」対象が構造上存在しない。T3b は
    `requires` が非空の 5 MOD（settings・monitor・executor・orchestrator・trigger）だけに置く
    （規約 §3-3）。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires  # 宣言外であることを宣言から確かめる

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_stages_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ stages: `has_router` の宣言と実 `Wiring.routers`
    の件数が一致（FR-018・GAP-1）。

    見る欄: `registration.has_router`（False）。**`True`／`False` をテストに書かず宣言から導く**——
    `registry.py` の当該エントリを反転させると赤くなることが要件である（SC-015 の変異 1）。
    stages は宣言 `False` の 5 件のうちの 1 つ（公開経路が 1 本も現れない側）。

    ⚠ この一致には **`wiring.py` 側の対応規則が無い**（`002` design-sync C-9
    が「実装しない」と裁定）。
    本 feature は**テスト層で見るだけ**とし、実行時に落とす検査は足さない（FR-021・B-02）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_stages_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ stages: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `StagesHandle` の `lifespan` 欄（不在）。⚠ **`ModuleRegistration` に `lifespan`
    の宣言欄が
    無い**（6 欄＝`name`・`requires`・`provides`・`build`・`has_router`・`has_ui_page`）ため、
    期待値の
    正は **`<Mod>Handle` の宣言属性**である（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)
