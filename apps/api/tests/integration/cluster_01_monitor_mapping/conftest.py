"""クラスタ 01（上限停止と等級写像）の準備手段。plan (i)・(ii)・(iii)。

**実 orchestrator × 実 executor × 実 monitor × 実 board**（8 スタブ）。見るのは**鎖**である ——
Hook が `RunHalted` を上げ、litellm／crewai のリトライを素通りし、`CrewOutcome.halted` になり、
`finish` が等級へ写し、盤面（実 `bd`）へ封筒・ラベル・release が入り、確認読取まで届く。

override は **8 件**（`registry.py` の `requires`／`provides` から機械的に決まる）——
`SettingsReader`・`MemberResolver`・`Authorizer`・`CommandRegistry`・`StageCatalog`・
`ProposalGate`・`VaultReader`・`OutcomeWriter`。過不足は起動時に落ちる。

**判定に効く設定値を直書きしない**（R-05）—— `grade_map`・`cost_field_path`・`envelope_marker`
は `MonitorConfig()` から引く。台本の `usage` には必ず `cost` を載せる（落とすと
`usage_missing` で等級が I に化け、「正常＝ I」と区別できなくなる）。

fixture は**すべて関数スコープ**（FR-042）。`from conftest import ...` を使わない（FR-041a）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from _support.cluster_env import assert_env_path_under, isolate_env
from _support.cluster_logs import CorrelationRecorder, recording_correlation
from _support.cluster_stores import (
    ClusterStores,
    init_beads,
    make_temp_area_a,
    make_temp_vault,
    remove_beads,
    require_bd_or_skip,
    seed_issue,
)
from _support.cluster_wiring import ClusterRuntime, build_cluster_wiring
from _support.contract_stubs import (
    CallLog,
    StubAuthorizer,
    StubCommandRegistry,
    StubMemberResolver,
    StubOutcomeWriter,
    StubProposalGate,
    StubRepoReader,
    StubRepoWriter,
    StubSettingsReader,
    StubStageCatalog,
    StubVaultReader,
)

from board import BoardEnv
from common.interfaces.board import ClaimFilter
from common.interfaces.orchestrator import ExecutionContext, LaunchKind
from common.interfaces.repo import RepoReader, RepoWriter
from common.interfaces.settings import (
    Authorizer,
    CommandRegistry,
    HooksConfig,
    LimitPolicy,
    LLMProfile,
    Member,
    MemberResolver,
    Project,
    ProjectFolders,
    ProviderKind,
    Rules,
    SettingsReader,
    TaskTemplate,
    TaskTemplates,
)
from common.interfaces.stages import (
    PromptBundle,
    PromptPurpose,
    ProposalGate,
    StageCatalog,
    StageSpec,
)
from common.interfaces.vault import OutcomeWriter, VaultReader
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName, ToolName

if TYPE_CHECKING:
    from pathlib import Path

    from _support.fake_llm_server import FakeLlmServer

    from api.wiring import ProtocolOverrides

BEADS_PROJECT_KEY = "cl01"
"""`bd init -p` に渡す接頭辞。"""

PROJECT_KEY = "cluster01pj"
"""スタブ settings が知っている唯一のプロジェクト（盤面のラベルにも使う）。"""

PERSONA = PersonaKey.programmer
"""スタブ settings が返す唯一のメンバのペルソナ。"""

API_KEY_REF = "LLM_API_KEY_CLUSTER01"
API_KEY_VALUE = "sk-cluster01-dummy"

THRESHOLD_T = 1_000_000
"""`LLMProfile.threshold_t` の**既定**。**十分大きく**して `ctx_exceeded` を誘発しない
（01 の大半が見るのは回数・費用・`finish_reason`・API 失敗である）。

送信前トークン推定（`tokens_before_send > T`）を踏むテストだけが `cluster(..., threshold_t=小)`
を明示的に渡す（値はテスト側が持つ＝`from conftest import` を使わない＝ FR-041a）。上限 3 系統の
うち①（長さ）だけがリポジトリのどこでも踏まれて
いなかったので、**値をテスト側から決められる形にした**（R-05・T084）。
"""

MAX_REQUESTS = 100
"""スタブ settings が返す回数の**既定**の上限。

**十分大きく**する —— 1 周回は差し戻しで `CrewExecutor.run` を複数回呼ぶので、小さいと
W・I を見るテストまで `limit_requests` で F に化ける（2026-09-01 に実測）。上限を踏ませる
テストだけが `max_requests=1` を明示的に渡す。"""

MAX_COST = 100.0
"""同上の費用の上限（回数で止める台本では効かせない）。"""

TURN_COST = 0.01
"""1 往復あたりの `usage.cost`（**必ず載せる**＝ R-05）。"""


def _make_project() -> Project:
    """スタブ `SettingsReader` が返すプロジェクト（vault はスタブなので実在確認は素通り）。"""
    return Project(
        project_key=PROJECT_KEY,
        name="クラスタ 01",
        folders=ProjectFolders(
            pj_input_dir="pj",
            role_dirs={PERSONA: f"pj/roles/{PERSONA}"},
            outcome_dir="pj/outcome",
        ),
        is_reserved=False,
    )


def _make_member(base_url: str, threshold_t: int) -> Member:
    """スタブ `MemberResolver` が返すメンバ（**LLM は偽サーバへ向ける**）。

    `threshold_t` は `KickoffRunner` が `RunMonitor.begin` へ渡す長さの閾値である
    （`orchestrator/service/kickoff.py` が `member.llm_profile.threshold_t` を読む）。
    """
    return Member(
        member_id=f"{PROJECT_KEY}.{PERSONA}",
        project_key=PROJECT_KEY,
        persona_key=PERSONA,
        role="クラスタ 01 の役割",
        goal="クラスタ 01 の目標",
        backstory="クラスタ 01 の背景",
        llm_profile=LLMProfile(
            name="default",
            provider_kind=ProviderKind.openai_compatible,
            base_url=base_url,
            model_id="demo-model",
            context_window=32768,
            api_key_ref=API_KEY_REF,
            threshold_t=threshold_t,
        ),
        tools=[ToolName.read_issue, ToolName.complete_issue, ToolName.submit_review],
        skills=[],
        mcps=[],
        bd_actor=f"{PROJECT_KEY}.{PERSONA}",
        casbin_subject=f"member:{PROJECT_KEY}.{PERSONA}",
    )


def _make_stage_spec() -> StageSpec:
    """`child` 段（`mode="execute"`。提案を伴わない最短経路）。"""
    return StageSpec(
        name=StageName.child,
        mode="execute",
        require_labels={Labels.kind(KindLabel.child)},
        require_no_parent=None,
        child_labels=[],
        allowed_personas={PERSONA},
        max_acceptance=None,
        allow_depends_on=False,
        require_outcome_path=False,
    )


def _make_bundle() -> PromptBundle:
    """スタブ `StageCatalog` が返すプロンプト。

    道具は 3 件だけ装備する（2026-09-01 の実測にもとづく）。

    - `read_issue`: 道具が 0 件だと実 CrewAI が 1 往復目の `Final Answer` で確定してしまい、
      **2 往復目の送信前**に効く上限判定へ到達できない。盤面は実物なのでそのまま実 `bd` を読む。
    - `complete_issue`・`submit_review`: これが無いと `CrewOutcome.completed` が偽のままになり、
      orchestrator が「完了の申告も失敗の申告も無い」として**必ず差し戻す**。差し戻しは
      `CrewExecutor.run` を `max_returns` 回まで繰り返すので、往復数が読めなくなる
      （R-04 の `call_count` の assert が立たない）。
    """
    return PromptBundle(
        persona_key=PERSONA,
        role="クラスタ 01 の役割",
        goal="クラスタ 01 の目標",
        backstory="クラスタ 01 の背景",
        task_description="クラスタ 01 の作業指示",
        expected_output="クラスタ 01 の期待成果",
        task_tools=[
            ToolName.read_issue,
            ToolName.complete_issue,
            ToolName.submit_review,
        ],
        purpose=PromptPurpose.work,
        proposal_schema=None,
    )


def _make_limits(*, max_requests: int = MAX_REQUESTS, max_cost: float = MAX_COST) -> LimitPolicy:
    """スタブ `SettingsReader` が返す上限（**判定に効く値なので値そのものは test が決める**）。"""
    return LimitPolicy(
        scope=PROJECT_KEY, max_requests_per_kickoff=max_requests, max_cost_per_kickoff=max_cost
    )


def _make_templates() -> TaskTemplates:
    """スタブ `SettingsReader` が返す段テンプレート（`child` 段の 1 件）。"""
    return TaskTemplates(
        generation=1,
        templates={
            str(StageName.child): TaskTemplate(
                description="クラスタ 01 の作業指示: {title}",
                expected_output="クラスタ 01 の期待成果: {acceptance}",
                agent=None,
                tools=None,
            )
        },
    )


def _make_overrides(
    log: CallLog, base_url: str, limits: LimitPolicy, threshold_t: int = THRESHOLD_T
) -> ProtocolOverrides:
    """10 件の未充足契約を埋める（**Protocol 型を必ず注釈する**＝憲章 §3。9・10 件目は D25）。"""
    settings_reader: SettingsReader = StubSettingsReader(
        log,
        project=_make_project(),
        limits=limits,
        rules=Rules(project_rules="クラスタ 01 の共通ルール"),
        hooks=HooksConfig(hooks=[]),
        tasks=_make_templates(),
    )
    member_resolver: MemberResolver = StubMemberResolver(
        log, member=_make_member(base_url, threshold_t)
    )
    authorizer: Authorizer = StubAuthorizer(log, allow=True)
    command_registry: CommandRegistry = StubCommandRegistry(log)
    stage_catalog: StageCatalog = StubStageCatalog(
        log, spec=_make_stage_spec(), bundle=_make_bundle()
    )
    proposal_gate: ProposalGate = StubProposalGate(log)
    vault_reader: VaultReader = StubVaultReader(log)
    outcome_writer: OutcomeWriter = StubOutcomeWriter(log)
    repo_reader: RepoReader = StubRepoReader(log)
    repo_writer: RepoWriter = StubRepoWriter(log)
    return {
        SettingsReader: settings_reader,
        MemberResolver: member_resolver,
        Authorizer: authorizer,
        CommandRegistry: command_registry,
        StageCatalog: stage_catalog,
        ProposalGate: proposal_gate,
        VaultReader: vault_reader,
        OutcomeWriter: outcome_writer,
        RepoReader: repo_reader,
        RepoWriter: repo_writer,
    }


def _make_context() -> ExecutionContext:
    """`KickoffRunner.run` に渡す実行文脈（手動起動・PJ 指定）。"""
    return ExecutionContext(
        kind=LaunchKind.manual,
        trigger_snapshot=None,
        dropped_path=None,
        claim_filter=ClaimFilter(labels=frozenset({Labels.role_executor()}), project_key=None),
        requested_at=datetime.now(UTC),
    )


@pytest.fixture
def bd_bin() -> str:
    """`bd` の絶対パス（無ければ **skip**）。"""
    return require_bd_or_skip()


@pytest.fixture(autouse=True)
def cluster_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse）。`.env` には鍵 1 件だけを書く。"""
    return isolate_env(monkeypatch, tmp_path, entries={API_KEY_REF: API_KEY_VALUE})


@pytest.fixture
def call_log() -> CallLog:
    """8 スタブが共有する 1 本の順序つき記録。"""
    return CallLog()


@pytest.fixture
def cluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_env: Path,
    bd_bin: str,
    call_log: CallLog,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> Iterator[Callable[..., ClusterRuntime]]:
    """台本を渡すと**実物一式**を組んで返す工場（偽サーバ → メンバの `base_url` → `build()`）。

    盤面には **`kind:child` の ready リーフを 1 件**仕込む（`claim_next` が取る対象）。

    Yields:
        台本 → 実物一式。
    """
    beads = init_beads(tmp_path, bd_bin, project_key=BEADS_PROJECT_KEY)
    # vault・領域 A は**読まれない**（`VaultReader`・`SettingsReader` はスタブ）。`ClusterStores`
    # の形を 03・04 と揃えるためだけに作る —— 揃えておくと横断検査と補助関数を使い回せる。
    vault = make_temp_vault(tmp_path)
    area_a = make_temp_area_a(tmp_path)
    monkeypatch.setenv("BEADS_DIR", str(beads.beads_dir))
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)

    def _build(
        script: list[dict[str, Any]],
        *,
        max_requests: int = MAX_REQUESTS,
        max_cost: float = MAX_COST,
        threshold_t: int = THRESHOLD_T,
    ) -> ClusterRuntime:
        server = fake_llm_server(script)
        overrides = _make_overrides(
            call_log,
            server.base_url,
            _make_limits(max_requests=max_requests, max_cost=max_cost),
            threshold_t,
        )
        return ClusterRuntime(
            wiring=build_cluster_wiring("01", overrides),
            stores=ClusterStores(beads=beads, vault=vault, area_a=area_a),
            server=server,
        )

    yield _build

    remove_beads(beads)


@pytest.fixture
def seed_leaf(bd_bin: str) -> Callable[[ClusterRuntime], str]:
    """`kind:child` の ready リーフを 1 件仕込む工場を返す。

    `claim_next` は `role:executor` の ready を取るので、その 2 ラベルを付ける。

    Returns:
        実物一式 → 仕込んだ issue_id。
    """
    assert bd_bin, "前提: `bd` が解決できている"

    def _seed(runtime: ClusterRuntime, *, acceptance: str = "- [ ] 完了条件") -> str:
        return seed_issue(
            runtime.stores.beads,
            title="クラスタ 01 のリーフ課題",
            labels=[
                Labels.project(PROJECT_KEY),
                Labels.kind(KindLabel.child),
                Labels.persona(PERSONA),
                Labels.role_executor(),
            ],
            acceptance=acceptance,
        )

    return _seed


@pytest.fixture
def correlation_recorder() -> Iterator[CorrelationRecorder]:
    """ルートロガへ観測点を足し、レコードごとの相関 ID を控える（#9 用）。"""
    yield from recording_correlation()


@pytest.fixture
def context() -> ExecutionContext:
    """`KickoffRunner.run` に渡す実行文脈（手動起動）。

    Returns:
        1 回の kickoff の実行文脈。
    """
    return _make_context()


@pytest.fixture
def stub_overrides(call_log: CallLog) -> ProtocolOverrides:
    """8 件の未充足契約を埋める写像そのもの（配線の過不足を見るテストが使う）。

    `Wiring.provided` は **override ＋ 有効 MOD が提供した契約**を合わせた写像なので、そこから
    override 集合を取り出すことはできない（board・monitor・executor の提供分まで混ざる）。
    過不足の検査は**この写像を足し引きして**行う。

    Returns:
        契約の型 → スタブ実装（8 件）。
    """
    return _make_overrides(call_log, "http://127.0.0.1:1/v1", _make_limits())
