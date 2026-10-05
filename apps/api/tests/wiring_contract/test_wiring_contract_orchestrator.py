"""組 apps/api ⇄ orchestrator: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=(BoardReader, BoardWriter, SettingsReader, MemberResolver,
VaultReader, RunMonitor, StageCatalog, ProposalGate, CrewExecutor)`（**9 件＝最多**）／
`provides=(KickoffRunner,)`／**`has_router=True`**／`has_ui_page=False`。**外部ストアは不要**。

依存 9 件のアダプタは **一度も実行されていなかった**（GAP-4）。

⚠ **build の段 (e) がスタブの応答に依存して成否を決める。** 段 (e) は `MemberResolver.is_human` を
**1 回だけ**叩き、**真を返すと `OrchestratorStartupError`** になる（FR-084a）。したがって
**`StubMemberResolver` の既定（`humans=()`＝偽）を変えない**ことが build 成功の前提である
（checklist CHK017 が要件側の [Gap] として起票済み）。
"""

from __future__ import annotations

import pytest
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubBoardWriter,
    StubCrewExecutor,
    StubMemberResolver,
    StubOutcomeWriter,
    StubProposalGate,
    StubRepoReader,
    StubRunMonitor,
    StubSettingsReader,
    StubStageCatalog,
    StubVaultReader,
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
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import RunMonitor
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.repo import RepoReader
from common.interfaces.settings import MemberResolver, SettingsReader
from common.interfaces.stages import ProposalGate, StageCatalog
from common.interfaces.vault import OutcomeWriter, VaultReader

WIRING_COVERS = ("orchestrator",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case() -> WiringCase:
    """orchestrator の組の記述（依存 9 件をスタブで埋める。一時ストアは**不要**）。

    ⚠ `StubMemberResolver` を**既定のまま**（`humans=()`）にする——`humans` に actor を入れると
    段 (e) の `is_human` が真を返し、`OrchestratorStartupError` で組み立てが落ちる（FR-084a）。

    Returns:
        実 registry の orchestrator エントリ 1 件を組み立てる `WiringCase`。
    """
    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)
    board_writer: BoardWriter = StubBoardWriter(log)
    settings_reader: SettingsReader = StubSettingsReader(log)
    member_resolver: MemberResolver = StubMemberResolver(log)
    vault_reader: VaultReader = StubVaultReader(log)
    run_monitor: RunMonitor = StubRunMonitor(log)
    stage_catalog: StageCatalog = StubStageCatalog(log)
    proposal_gate: ProposalGate = StubProposalGate(log)
    crew_executor: CrewExecutor = StubCrewExecutor(log)
    repo_reader: RepoReader = StubRepoReader(log)
    overrides: dict[type[object], object] = {
        BoardReader: board_reader,
        BoardWriter: board_writer,
        SettingsReader: settings_reader,
        MemberResolver: member_resolver,
        VaultReader: vault_reader,
        RunMonitor: run_monitor,
        StageCatalog: stage_catalog,
        ProposalGate: proposal_gate,
        CrewExecutor: crew_executor,
        RepoReader: repo_reader,
    }
    return make_case(MODULE, overrides, log)


def test_orchestrator_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ orchestrator: 組み上がり、宣言 `provides`
    の契約が取り出せる（FR-008・FR-010）。

    見る欄: `registration.provides`（1 件）。依存 9 件の `require` 取り出しがアダプタ内で初めて
    実行される（GAP-4）。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    kickoff_runner: KickoffRunner = require(wiring.provided, KickoffRunner)
    assert kickoff_runner is resolved[KickoffRunner]


def test_orchestrator_t2_provided_keys_are_exactly_requires_union_provides(
    case: WiringCase,
) -> None:
    """組 apps/api ⇄ orchestrator: `Wiring.provided` の鍵集合が `requires ∪ provides` に一致。

    FR-011 (a)。見る欄: `registration.requires`（9 件）・`registration.provides`（1 件）。
    鍵は 10 件になる。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_orchestrator_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ orchestrator: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（9 件）。足す契約は `requires` にも `provides` にも無いもの
    （`OutcomeWriter`）を選び、宣言外であることを**宣言から確かめて**から入れる。
    """
    outcome_writer: OutcomeWriter = StubOutcomeWriter(case.call_log)
    assert OutcomeWriter not in case.registration.requires
    assert OutcomeWriter not in case.registration.provides

    extraneous: dict[type[object], object] = {**case.overrides, OutcomeWriter: outcome_writer}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_orchestrator_t3b_missing_override_fails_the_assembly(case: WiringCase) -> None:
    """組 apps/api ⇄ orchestrator: 差し替え写像を 1 件欠くと組み立てが失敗する（FR-011 (c)）。

    見る欄: `registration.requires`（9 件）。**落とす契約は宣言から導く**。
    """
    dropped = case.registration.requires[0]
    partial = {
        contract: value for contract, value in case.overrides.items() if contract is not dropped
    }

    with pytest.raises(UnsatisfiedContractError):
        assemble(case, overrides=partial)


def test_orchestrator_t4_router_presence_matches_the_has_router_declaration(
    case: WiringCase,
) -> None:
    """組 apps/api ⇄ orchestrator: `has_router` の宣言と実 `Wiring.routers` の件数が一致。

    FR-018・US4 AC-1。見る欄: `registration.has_router`（True）。**宣言 `True` 側の 3 件のうちの
    2 件目**である。**期待値は宣言から導く**（SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_orchestrator_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ orchestrator: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `OrchestratorHandle` の `lifespan` 欄（不在）。正は `<Mod>Handle`
    の宣言属性（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_orchestrator_build_touches_only_is_human_once(case: WiringCase) -> None:
    """組 apps/api ⇄ orchestrator: build が依存契約を叩くのは段 (e) の 1 回だけ（WD-10）。

    見る欄: `registration.requires` の `MemberResolver`。`CallLog` に載った呼び出しが
    `('MemberResolver', 'is_human')` の **1 件だけ**であること——build が依存契約を余分に叩いて
    いないことを実出力で示す。

    ⚠ **起動時検査の段順・例外型は再実装しない**（層 2＝ orchestrator の feature 単体テストの領分
    ＝ spec §0-6 の除外表）。ここで見るのは「registry 経由で組んだときスタブがどう触られたか」だけ
    である。
    """
    assert not case.call_log.names()

    assemble(case)

    assert case.call_log.names() == [("MemberResolver", "is_human")]
