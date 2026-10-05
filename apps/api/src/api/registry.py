"""モジュール登録（土台側の 4 つ目の登録点。contracts/module-registration.md）。

モジュールを 1 つ足すときに **apps/api 側で編集する唯一のファイル**（FR-007・SC-005）。
`wiring.py`・`app.py`・`main.py`・`middleware.py`・`correlation.py`・`health.py`・`config.py`・
`exceptions.py` は 1 行も変わらない。エントリの形は contracts/module-registration.md §2。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.wiring import ModuleRegistration, WiredModule, require
from board import BoardDeps
from board import build as build_board
from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import LLMHttpHook, RunMonitor
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.repo import RepoReader, RepoWriter
from common.interfaces.settings import Authorizer, CommandRegistry, MemberResolver, SettingsReader
from common.interfaces.stages import ProposalGate, StageCatalog
from common.interfaces.vault import OutcomeWriter, VaultReader
from executor import ExecutorDeps
from executor import build as build_executor
from monitor import MonitorDeps
from monitor import build as build_monitor
from orchestrator import OrchestratorDeps
from orchestrator import build as build_orchestrator
from repo import RepoDeps
from repo import build as build_repo
from settings import SettingsDeps
from settings import build as build_settings
from stages import StagesDeps
from stages import build as build_stages
from trigger import TriggerDeps
from trigger import build as build_trigger
from vault import VaultDeps
from vault import build as build_vault

if TYPE_CHECKING:
    from collections.abc import Mapping


def _build_board(deps: Mapping[type[object], object]) -> WiredModule:
    """Board を組み立て、両契約を `WiredModule` へ写す（`requires=()` なので `deps` は空）。"""
    handle = build_board(BoardDeps())
    board_reader: BoardReader = handle.board_reader  # 憲章 §3: Protocol 型を必ず注釈する
    board_writer: BoardWriter = handle.board_writer
    return WiredModule(provides={BoardReader: board_reader, BoardWriter: board_writer})


def _build_vault(deps: Mapping[type[object], object]) -> WiredModule:
    """Vault を組み立て、両契約を `WiredModule` へ写す（`requires=()` なので `deps` は空）。"""
    handle = build_vault(VaultDeps())
    vault_reader: VaultReader = handle.vault_reader  # 憲章 §3: Protocol 型を必ず注釈する
    outcome_writer: OutcomeWriter = handle.outcome_writer
    return WiredModule(provides={VaultReader: vault_reader, OutcomeWriter: outcome_writer})


def _build_repo(deps: Mapping[type[object], object]) -> WiredModule:
    """Repo を組み立て、両契約を `WiredModule` へ写す（`requires=()` なので `deps` は空。D25）。"""
    handle = build_repo(RepoDeps())
    repo_reader: RepoReader = handle.repo_reader  # 憲章 §3: Protocol 型を必ず注釈する
    repo_writer: RepoWriter = handle.repo_writer
    return WiredModule(provides={RepoReader: repo_reader, RepoWriter: repo_writer})


def _build_settings(deps: Mapping[type[object], object]) -> WiredModule:
    """Settings を組み立て、4 契約と router を写す（依存 2 件・UI ページを持つ初の登録）。"""
    board_reader: BoardReader = require(deps, BoardReader)  # 憲章 §3: Protocol 型を必ず注釈する
    vault_reader: VaultReader = require(deps, VaultReader)
    handle = build_settings(SettingsDeps(board_reader=board_reader, vault_reader=vault_reader))
    settings_reader: SettingsReader = handle.settings_reader
    member_resolver: MemberResolver = handle.member_resolver
    authorizer: Authorizer = handle.authorizer
    command_registry: CommandRegistry = handle.command_registry
    return WiredModule(
        provides={
            SettingsReader: settings_reader,
            MemberResolver: member_resolver,
            Authorizer: authorizer,
            CommandRegistry: command_registry,
        },
        router=handle.router,
    )


def _build_stages(deps: Mapping[type[object], object]) -> WiredModule:
    """Stages を組み立て、両契約を `WiredModule` へ写す（`requires=()` なので `deps` は空）。"""
    handle = build_stages(StagesDeps())
    stage_catalog: StageCatalog = handle.stage_catalog  # 憲章 §3: Protocol 型を必ず注釈する
    proposal_gate: ProposalGate = handle.proposal_gate
    return WiredModule(provides={StageCatalog: stage_catalog, ProposalGate: proposal_gate})


def _build_monitor(deps: Mapping[type[object], object]) -> WiredModule:
    """Monitor を組み立て、両契約を `WiredModule` へ写す（`requires` が非空の初の登録）。"""
    board_reader: BoardReader = require(deps, BoardReader)  # 憲章 §3: Protocol 型を必ず注釈する
    board_writer: BoardWriter = require(deps, BoardWriter)
    handle = build_monitor(MonitorDeps(board_reader=board_reader, board_writer=board_writer))
    run_monitor: RunMonitor = handle.run_monitor
    llm_http_hook: LLMHttpHook = handle.llm_http_hook
    return WiredModule(provides={RunMonitor: run_monitor, LLMHttpHook: llm_http_hook})


def _build_executor(deps: Mapping[type[object], object]) -> WiredModule:
    """Executor を組み立て、`CrewExecutor` を `WiredModule` へ写す（依存 9 件。2 件は D25）。"""
    board_reader: BoardReader = require(deps, BoardReader)  # 憲章 §3: Protocol 型を必ず注釈する
    board_writer: BoardWriter = require(deps, BoardWriter)
    vault_reader: VaultReader = require(deps, VaultReader)
    outcome_writer: OutcomeWriter = require(deps, OutcomeWriter)
    authorizer: Authorizer = require(deps, Authorizer)
    command_registry: CommandRegistry = require(deps, CommandRegistry)
    llm_http_hook: LLMHttpHook = require(deps, LLMHttpHook)
    repo_reader: RepoReader = require(deps, RepoReader)
    repo_writer: RepoWriter = require(deps, RepoWriter)
    handle = build_executor(
        ExecutorDeps(
            board_reader=board_reader,
            board_writer=board_writer,
            vault_reader=vault_reader,
            outcome_writer=outcome_writer,
            authorizer=authorizer,
            command_registry=command_registry,
            llm_http_hook=llm_http_hook,
            repo_reader=repo_reader,
            repo_writer=repo_writer,
        )
    )
    crew_executor: CrewExecutor = handle.crew_executor
    return WiredModule(provides={CrewExecutor: crew_executor})


def _build_orchestrator(deps: Mapping[type[object], object]) -> WiredModule:
    """Orchestrator を組み立て、契約と router を写す（依存 10 件・router を持つ初の登録）。"""
    board_reader: BoardReader = require(deps, BoardReader)  # 憲章 §3: Protocol 型を必ず注釈する
    board_writer: BoardWriter = require(deps, BoardWriter)
    settings_reader: SettingsReader = require(deps, SettingsReader)
    member_resolver: MemberResolver = require(deps, MemberResolver)
    vault_reader: VaultReader = require(deps, VaultReader)
    run_monitor: RunMonitor = require(deps, RunMonitor)
    stage_catalog: StageCatalog = require(deps, StageCatalog)
    proposal_gate: ProposalGate = require(deps, ProposalGate)
    crew_executor: CrewExecutor = require(deps, CrewExecutor)
    repo_reader: RepoReader = require(deps, RepoReader)
    handle = build_orchestrator(
        OrchestratorDeps(
            board_reader=board_reader,
            board_writer=board_writer,
            settings_reader=settings_reader,
            member_resolver=member_resolver,
            vault_reader=vault_reader,
            run_monitor=run_monitor,
            stage_catalog=stage_catalog,
            proposal_gate=proposal_gate,
            crew_executor=crew_executor,
            repo_reader=repo_reader,
        )
    )
    kickoff_runner: KickoffRunner = handle.kickoff_runner
    return WiredModule(provides={KickoffRunner: kickoff_runner}, router=handle.router)


def _build_trigger(deps: Mapping[type[object], object]) -> WiredModule:
    """Trigger を組み立て、口と常駐を写す（`provides` が空・`lifespan` を持つ初の登録）。"""
    # 憲章 §3: Protocol 型を必ず注釈する
    kickoff_runner: KickoffRunner = require(deps, KickoffRunner)
    handle = build_trigger(TriggerDeps(kickoff_runner=kickoff_runner))
    return WiredModule(provides={}, router=handle.router, lifespan=handle.lifespan)


MODULE_REGISTRATIONS: tuple[ModuleRegistration, ...] = (
    ModuleRegistration(
        name="board",
        requires=(),
        provides=(BoardReader, BoardWriter),
        build=_build_board,
    ),
    ModuleRegistration(
        name="vault",
        requires=(),
        provides=(VaultReader, OutcomeWriter),
        build=_build_vault,
    ),
    ModuleRegistration(
        name="repo",
        requires=(),
        provides=(RepoReader, RepoWriter),
        build=_build_repo,
    ),
    ModuleRegistration(
        name="settings",
        requires=(BoardReader, VaultReader),
        provides=(SettingsReader, MemberResolver, Authorizer, CommandRegistry),
        build=_build_settings,
        has_router=True,
        has_ui_page=True,
    ),
    ModuleRegistration(
        name="stages",
        requires=(),
        provides=(StageCatalog, ProposalGate),
        build=_build_stages,
    ),
    ModuleRegistration(
        name="monitor",
        requires=(BoardReader, BoardWriter),
        provides=(RunMonitor, LLMHttpHook),
        build=_build_monitor,
    ),
    ModuleRegistration(
        name="executor",
        requires=(
            BoardReader,
            BoardWriter,
            VaultReader,
            OutcomeWriter,
            Authorizer,
            CommandRegistry,
            LLMHttpHook,
            RepoReader,
            RepoWriter,
        ),
        provides=(CrewExecutor,),
        build=_build_executor,
    ),
    ModuleRegistration(
        name="orchestrator",
        requires=(
            BoardReader,
            BoardWriter,
            SettingsReader,
            MemberResolver,
            VaultReader,
            RunMonitor,
            StageCatalog,
            ProposalGate,
            CrewExecutor,
            RepoReader,
        ),
        provides=(KickoffRunner,),
        build=_build_orchestrator,
        has_router=True,
    ),
    ModuleRegistration(
        name="trigger",
        requires=(KickoffRunner,),
        # **`provides=()` は初例**（`_run_builds` は `set(()) == set({})` で通る＝ 011 R-02）。
        # trigger は Protocol を 1 本も提供しない（`p4_module_contract.csv` に提供行が無い）。
        provides=(),
        build=_build_trigger,
        has_router=True,
        has_ui_page=True,
    ),
)
"""実在 feature は board・vault・settings・stages・monitor・executor・orchestrator・
trigger の 8 件。

整合検査は REG-8a〜h。
"""
