"""組 apps/api ⇄ executor: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=(BoardReader, BoardWriter, VaultReader, OutcomeWriter,
Authorizer, CommandRegistry, LLMHttpHook)`（**7 件**）／`provides=(CrewExecutor,)`／
`has_router=False`／`has_ui_page=False`。**外部ストアは不要**。

依存 7 件のアダプタは **一度も実行されていなかった**（GAP-4）。本ファイルが `require` の取り出し
7 回と Handle 属性から `WiredModule.provides` への写しを初めて通す。

⚠ **能動検査の形が他の 4 MOD と違う**（2026-09-01 裁定＝ WD-39・research R-05）。executor の `build`
は `.env` を読まず（docstring が「読む時点は実行ごと」と明言）`ExecutorEnv` に Path フィールドが
無いため `assert_env_path_under` を適用できない。代わりに **`assert_env_file_under`** で
**差し替え点 `executor.models.env_file_path()` の戻り**が一時 `.env` であることを `build` の**前**に
確かめる。executor は `LLM_API_KEY_*` の**唯一の経路**であり、この検査は FR-030（実 LLM API を
呼ばない・実鍵を読まない）の証跡を兼ねる。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import (
    CallLog,
    StubAuthorizer,
    StubBoardReader,
    StubBoardWriter,
    StubCommandRegistry,
    StubKickoffRunner,
    StubLLMHttpHook,
    StubOutcomeWriter,
    StubRepoReader,
    StubRepoWriter,
    StubVaultReader,
)
from conftest import (
    WiringCase,
    assemble,
    assert_env_file_under,
    expected_lifespans,
    make_case,
    resolve_deps_handle,
)

import executor.models
from api.exceptions import ExtraneousOverrideError, UnsatisfiedContractError
from api.wiring import require
from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import LLMHttpHook
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.repo import RepoReader, RepoWriter
from common.interfaces.settings import Authorizer, CommandRegistry
from common.interfaces.vault import OutcomeWriter, VaultReader

if TYPE_CHECKING:
    from pathlib import Path

WIRING_COVERS = ("executor",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case(tmp_path_factory: pytest.TempPathFactory, isolated_env_file: Path) -> WiringCase:
    """executor の組の記述（依存 7 件をスタブで埋め、`build` の**前**に差し替え点を確かめる）。

    ⚠ 境界に `tmp_path` ではなく pytest の一時ルートを渡す——`isolated_env_file` は
    `tmp_path_factory.mktemp()` が作るので `tmp_path` の**兄弟**に置かれる（**B-13**）。

    Returns:
        実 registry の executor エントリ 1 件を組み立てる `WiringCase`。
    """
    resolved = assert_env_file_under(
        executor.models.env_file_path(), tmp_path_factory.getbasetemp()
    )
    assert resolved == isolated_env_file.resolve()

    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)
    board_writer: BoardWriter = StubBoardWriter(log)
    vault_reader: VaultReader = StubVaultReader(log)
    outcome_writer: OutcomeWriter = StubOutcomeWriter(log)
    authorizer: Authorizer = StubAuthorizer(log)
    command_registry: CommandRegistry = StubCommandRegistry(log)
    llm_http_hook: LLMHttpHook = StubLLMHttpHook(log)
    repo_reader: RepoReader = StubRepoReader(log)
    repo_writer: RepoWriter = StubRepoWriter(log)
    overrides: dict[type[object], object] = {
        BoardReader: board_reader,
        BoardWriter: board_writer,
        VaultReader: vault_reader,
        OutcomeWriter: outcome_writer,
        Authorizer: authorizer,
        CommandRegistry: command_registry,
        LLMHttpHook: llm_http_hook,
        RepoReader: repo_reader,
        RepoWriter: repo_writer,
    }
    return make_case(MODULE, overrides, log)


def test_executor_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: 組み上がり、宣言 `provides` の契約が取り出せる（FR-008・FR-010）。

    見る欄: `registration.provides`（1 件）。依存 7 件の `require` 取り出しがアダプタ内で初めて
    実行される（GAP-4）。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    crew_executor: CrewExecutor = require(wiring.provided, CrewExecutor)
    assert crew_executor is resolved[CrewExecutor]


def test_executor_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: `Wiring.provided` の鍵集合が `requires ∪ provides` に一致（FR-011
    (a)）。

    見る欄: `registration.requires`（7 件）・`registration.provides`（1 件）。鍵は 8 件になる。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_executor_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（7 件）。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_executor_t3b_missing_override_fails_the_assembly(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: 差し替え写像を 1 件欠くと組み立てが失敗する（FR-011 (c)）。

    見る欄: `registration.requires`（7 件）。**落とす契約は宣言から導く**。
    """
    dropped = case.registration.requires[0]
    partial = {
        contract: value for contract, value in case.overrides.items() if contract is not dropped
    }

    with pytest.raises(UnsatisfiedContractError):
        assemble(case, overrides=partial)


def test_executor_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: `has_router` の宣言と実 `Wiring.routers`
    の件数が一致（FR-018・GAP-1）。

    見る欄: `registration.has_router`（False）。**期待値は宣言から導く**（SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_executor_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `ExecutorHandle` の `lifespan` 欄（不在）。正は `<Mod>Handle` の宣言属性（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_executor_t0_no_real_llm_api_key_is_reachable(case: WiringCase) -> None:
    """組 apps/api ⇄ executor: 実 `LLM_API_KEY_*` が読める状態でないまま組み上がる（FR-030）。

    見る欄: —（外部前提）。executor は `LLM_API_KEY_*` の**唯一の経路**である。止め木は (b) 差し替え
    点 5 件と (c) OS 環境変数の削除の**両方**を倒すので、`.env` 側にも環境変数側にも鍵が残らない。
    """
    assert not [name for name in os.environ if name.startswith("LLM_API_KEY")]
    assert executor.models.env_file_path().read_text(encoding="utf-8") == ""

    assemble(case)

    assert not [name for name in os.environ if name.startswith("LLM_API_KEY")]
