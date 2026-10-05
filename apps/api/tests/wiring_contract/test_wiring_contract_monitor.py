"""組 apps/api ⇄ monitor: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=(BoardReader, BoardWriter)`／
`provides=(RunMonitor, LLMHttpHook)`／`has_router=False`／`has_ui_page=False`。
**外部ストアは不要**。

**提供 2 契約が別インスタンスである唯一の組**である（board・vault・stages・settings
は同一インスタンス
で複数契約を提供するため、「宣言の鍵と値の対応を取り違えた」誤りを実体では区別できない＝
Edge Case）。

⚠ `integration/test_wiring_real_registry.py` が monitor の別インスタンスを既に見ているが、あちらは
**`enabled_modules=["board","monitor"]` の 2 件同時組み立て**で board の実 `build`
を引き込む形であり、
本組は **`enabled_modules=["monitor"]` の単独組み立て＋契約準拠スタブ**という**別条件**である。
**実行条件が異なる置き直しは重複に当たらない**（FR-002 の一般則＝ 2026-09-01 裁定・WD-40）。
"""

from __future__ import annotations

import pytest
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubBoardWriter,
    StubKickoffRunner,
)
from conftest import (
    WiringCase,
    assemble,
    expected_lifespans,
    make_case,
    resolve_deps_handle,
)

from api.exceptions import ExtraneousOverrideError, UnsatisfiedContractError
from api.wiring import require
from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.monitor import LLMHttpHook, RunMonitor
from common.interfaces.orchestrator import KickoffRunner

WIRING_COVERS = ("monitor",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case() -> WiringCase:
    """monitor の組の記述（一時ストアは**不要**。依存 2 件をスタブで埋める）。

    Returns:
        実 registry の monitor エントリ 1 件を組み立てる `WiringCase`。
    """
    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)
    board_writer: BoardWriter = StubBoardWriter(log)
    overrides: dict[type[object], object] = {
        BoardReader: board_reader,
        BoardWriter: board_writer,
    }
    return make_case(MODULE, overrides, log)


def test_monitor_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: 組み上がり、宣言 `provides` の 2
    契約が全部取り出せる（FR-008・FR-010）。

    見る欄: `registration.provides`（2 件）。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    run_monitor: RunMonitor = require(wiring.provided, RunMonitor)
    llm_http_hook: LLMHttpHook = require(wiring.provided, LLMHttpHook)
    assert run_monitor is resolved[RunMonitor]
    assert llm_http_hook is resolved[LLMHttpHook]


def test_monitor_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: `Wiring.provided` の鍵集合が `requires ∪ provides` に一致（FR-011
    (a)）。

    見る欄: `registration.requires`（2 件）・`registration.provides`（2 件）。鍵は 4 件になる。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_monitor_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（2 件）。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_monitor_t3b_missing_override_fails_the_assembly(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: 差し替え写像を 1 件欠くと組み立てが失敗する（FR-011 (c)）。

    見る欄: `registration.requires`（2 件）。**落とす契約は宣言から導く**。
    """
    dropped = case.registration.requires[0]
    partial = {
        contract: value for contract, value in case.overrides.items() if contract is not dropped
    }

    with pytest.raises(UnsatisfiedContractError):
        assemble(case, overrides=partial)


def test_monitor_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: `has_router` の宣言と実 `Wiring.routers`
    の件数が一致（FR-018・GAP-1）。

    見る欄: `registration.has_router`（False）。**期待値は宣言から導く**（SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_monitor_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `MonitorHandle` の `lifespan` 欄（不在）。正は `<Mod>Handle` の宣言属性（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_monitor_t6_provides_two_distinct_instances(case: WiringCase) -> None:
    """組 apps/api ⇄ monitor: 宣言 `provides` の 2
    契約が**別インスタンス**として取り出せる（FR-012）。

    見る欄: `registration.provides`（2 件・`MonitorHandle.run_monitor`／`llm_http_hook`）。

    ⚠ 他 4 MOD（board・vault・stages・settings）は複数契約を**同一インスタンス**で提供するので、この
    区別が実体で付くのは monitor だけである（Edge Case）。

    ⚠ `integration/test_wiring_real_registry.py::test_monitor_provides_two_distinct_instances` と
    **主張は同じだが実行条件が違う**（あちらは 2 件同時組み立て・こちらは単独組み立て＋スタブ）。
    **実行条件が異なる置き直しは重複に当たらない**という FR-002 の一般則（WD-40）に依る。
    """
    wiring = assemble(case)

    run_monitor: RunMonitor = require(wiring.provided, RunMonitor)
    llm_http_hook: LLMHttpHook = require(wiring.provided, LLMHttpHook)

    assert run_monitor is not llm_http_hook
