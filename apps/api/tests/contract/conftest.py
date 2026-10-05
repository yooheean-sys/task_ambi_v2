"""契約テスト層（層 4）の fixture。マーカ登録・一括止め木・提供 MOD 別の `build()` 直呼び。

規約の正本は `specs/013-protocol-contracts/contracts/contract-test-conventions.md`
（転記が `apps/api/tests/contract/README.md`）。

★ **止め木**（`isolated_env_file`）が本ファイルの核である。実リポジトリの `.env` は
`BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT`・`DOWNLOADS_DIR` を実値で持ち、`VAULT_ROOT` と
`DOWNLOADS_DIR` は **`/mnt/c` 配下**を指す。autouse で既定を安全側に倒さないと、`build()` を
呼ぶテストが**実運用のストアを掴む**（FR-013・FR-014・SC-009）。**この fixture を消さない・
迂回しない。**

⚠ 止め木が効くのは、各 MOD の `<Mod>Env` の**明示 `__init__(self, **overrides)`** と
`build()` の **`<Mod>Env(_env_file=env_file_path())`** が**対で**在るときだけである。
どちらかを落とすと `model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**に
なる（テストは緑のまま実運用ストアを掴む）。executor だけは差し替え点が
**`executor.models.env_file_path`** である（`executor.env_file_path` は再エクスポートで、
差し替えても効かない＝ `packages/features/executor/tests/conftest.py:718-724`）。
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest
from _support.contract_stubs import (
    CallLog,
    StubAuthorizer,
    StubBoardReader,
    StubBoardWriter,
    StubCommandRegistry,
    StubCrewExecutor,
    StubLLMHttpHook,
    StubMemberResolver,
    StubOutcomeWriter,
    StubProposalGate,
    StubRepoReader,
    StubRepoWriter,
    StubRunMonitor,
    StubSettingsReader,
    StubStageCatalog,
    StubVaultReader,
)
from contract_shims import make_always_fails_shim, make_drops_one_label_shim
from contract_stores import (
    TempAreaA,
    TempBeads,
    TempVault,
    init_beads,
    make_light_beads,
    make_temp_area_a,
    make_temp_vault,
    remove_beads,
)

from board import BoardConfig, BoardDeps, BoardEnv, BoardHandle
from board import build as build_board
from common.interfaces.board import Comment, Issue, IssueStatus
from common.interfaces.monitor import Grade
from common.interfaces.settings import (
    LLMProfile,
    Member,
    ProjectFolders,
    ProjectRepos,
    ProviderKind,
    Rules,
    TaskTemplate,
    TaskTemplates,
)
from common.interfaces.stages import ContextRefs, PromptPurpose
from common.kernel.enums import PersonaKey, StageName, ToolName
from executor import ExecutorDeps, ExecutorHandle
from executor import build as build_executor
from monitor import MonitorConfig, MonitorDeps, MonitorHandle
from monitor import build as build_monitor
from orchestrator import OrchestratorDeps, OrchestratorHandle
from orchestrator import build as build_orchestrator
from repo import RepoDeps, RepoHandle
from repo import build as build_repo
from settings import SettingsDeps, SettingsEnv, SettingsHandle
from settings import build as build_settings
from stages import StagesDeps, StagesHandle
from stages import build as build_stages
from vault import VaultDeps, VaultEnv, VaultHandle
from vault import build as build_vault

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from typing import Any

    from pydantic_settings import BaseSettings

    from common.interfaces.board import BoardReader, BoardWriter
    from common.interfaces.executor import CrewExecutor
    from common.interfaces.monitor import LLMHttpHook, RunMonitor
    from common.interfaces.orchestrator import KickoffRunner
    from common.interfaces.repo import RepoReader, RepoWriter
    from common.interfaces.settings import (
        Authorizer,
        CommandRegistry,
        MemberResolver,
        SettingsReader,
    )
    from common.interfaces.stages import ProposalGate, StageCatalog
    from common.interfaces.vault import OutcomeWriter, VaultReader

ENV_FILE_PATH_TARGETS = (
    "board.env_file_path",
    "vault.env_file_path",
    "settings.env_file_path",
    "executor.models.env_file_path",
)
"""`.env` を読む 4 MOD の差し替え点（executor だけ `models` 側＝再エクスポートでは効かない）。"""

CLEARED_ENV_KEYS = (
    "BEADS_DIR",
    "BD_BIN",
    "VAULT_ROOT",
    "SETTINGS_ROOT",
    "TRIGGER_ROOT",
    "DOWNLOADS_DIR",
)
"""OS 環境変数の側から実運用ストアが漏れないよう消すキー（`.env` より優先されるため）。"""

API_KEY_ENV_PREFIX = "LLM_API_KEY"
"""実 LLM API の鍵（FR-010。前方一致で消す）。"""

BD_COMMAND = "bd"


def pytest_configure(config: pytest.Config) -> None:
    """`contract_if` マーカを登録する（FR-002・plan K-5）。

    ルート pyproject.toml の `markers` は触らない（FR-030）。`requires_bd` はルートに
    登録済みなのでここでは足さない。
    """
    config.addinivalue_line(
        "markers",
        "contract_if(if_id): 検証する契約行 IF-nn を 1 件だけ宣言する",
    )


@pytest.fixture(autouse=True)
def isolated_env_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """実 `.env`・実運用ストア・実 LLM API へ届かない既定に倒す（FR-013・FR-014・R-04）。

    2 段構えの 1 段目である。**先に安全側へ倒し**、提供 MOD 別の fixture が 2 段目として
    一時ディレクトリへ指し直す。指し直しを忘れたテストは `build()` が「キーが無い」で
    落ちるので、実物を掴んだまま緑になることが構造的に起きない。

    **autouse だが名前でも要求できる** —— 提供 MOD 別の fixture は、`build()` の前に
    `assert_env_path_under(..., env_file=isolated_env_file)` を通すためにこの値を要る
    （`board.env_file_path` を import 済みの名前で呼ぶと差し替え前の関数を掴むので、
    差し替えた `.env` の所在は fixture 経由で受け取る）。

    Yields:
        差し替え先の空の `.env` の絶対パス。
    """
    empty_env = tmp_path_factory.mktemp("contract_env") / ".env"
    empty_env.write_text("", encoding="utf-8")
    for target in ENV_FILE_PATH_TARGETS:
        monkeypatch.setattr(target, lambda: empty_env)
    for key in CLEARED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key in [name for name in os.environ if name.startswith(API_KEY_ENV_PREFIX)]:
        monkeypatch.delenv(key, raising=False)
    yield empty_env


def assert_env_path_under(
    env_cls: type[BaseSettings],
    field: str,
    tmp_path: Path,
    *,
    env_file: Path,
) -> Path:
    """`build()` を呼ぶ**前**に、その MOD が読む解決先が `tmp_path` 配下かを確かめる（FR-013a）。

    `build()` そのものを検査に使わない —— settings の `build()` は不在時に雛形を**書き込む**
    ため、事後確認では手遅れになる（R-05）。読み方は `build()` と**同じ経路**
    （`<Mod>Env(_env_file=env_file_path())`）にする。

    Args:
        env_cls: その MOD の `<Mod>Env`（`BoardEnv`・`VaultEnv`・`SettingsEnv`）。
        field: 解決先を持つフィールド名（`BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT`）。
        tmp_path: 配下であるべき一時ディレクトリ。
        env_file: `build()` が読む `.env` の絶対パス（`env_file_path()` の戻り）。

    Returns:
        解決済みの絶対パス（`tmp_path` 配下であることが確かめられたもの）。
    """
    expected = tmp_path.resolve()
    try:
        env = env_cls(_env_file=env_file)
    except Exception as exc:
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__} を build() と同じ経路で読めない: {exc}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    raw: object = getattr(env, field, None)
    if not isinstance(raw, Path):
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__}.{field} が Path でない: {raw!r}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    resolved = raw.expanduser().resolve()
    if not resolved.is_relative_to(expected):
        pytest.fail(
            "止め木の能動検査: 解決先が一時ディレクトリの外にある"
            "（実物に触れる前に止めた＝ FR-013a・SC-009）\n"
            f"  読んだキー名: {field}\n"
            f"  解決値: {resolved}\n"
            f"  期待した接頭辞: {expected}\n"
            f"  読んだ .env: {env_file}"
        )
    return resolved


@pytest.fixture
def bd_bin() -> str:
    """`bd` 実行ファイルの絶対パス（見つからなければ **skip**）。

    ルート `apps/api/tests/conftest.py` の同名 fixture は `pytest.fail` する版だが、
    `contract/` 側は `pytest.skip` で上書きする（R-14）。`bd` を持たない環境で
    `-m "not requires_bd"` を使わずに走らせても止まらないようにするためである。
    ルートの conftest は**変更しない**（FR-030）。
    """
    resolved = shutil.which(BD_COMMAND)
    if resolved is None:
        pytest.skip(
            f"外部ツール '{BD_COMMAND}' が PATH 上に無い"
            '（board 提供分は `uv run pytest -m "not requires_bd"` で外せる）'
        )
    return resolved


# --------------------------------------------------------------------------------------
# 受け渡し型の見本（複数ストーリで共有する。`common.interfaces` の型だけを使う）
# --------------------------------------------------------------------------------------

SAMPLE_PROJECT_KEY = "contractpj"
SAMPLE_BASE_URL = "http://127.0.0.1:8080/v1"
"""偽 LLM サーバ以外へ通信しないことを目視できるよう、見本もループバックにする（FR-010）。"""


def make_llm_profile(*, base_url: str = SAMPLE_BASE_URL, api_key_ref: str = "") -> LLMProfile:
    """契約テストが使う `LLMProfile` の見本（鍵の**実値**は持たない）。"""
    return LLMProfile(
        name="default",
        provider_kind=ProviderKind.openai_compatible,
        base_url=base_url,
        model_id="demo-model",
        context_window=32768,
        api_key_ref=api_key_ref,
        threshold_t=16384,
    )


def make_member(
    persona_key: PersonaKey = PersonaKey.project_lead,
    *,
    project_key: str = SAMPLE_PROJECT_KEY,
    profile: LLMProfile | None = None,
) -> Member:
    """契約テストが使う `Member` の見本。"""
    return Member(
        member_id=f"{project_key}.{persona_key}",
        project_key=project_key,
        persona_key=persona_key,
        role=f"{persona_key} の役割",
        goal=f"{persona_key} の目標",
        backstory=f"{persona_key} の背景",
        llm_profile=profile if profile is not None else make_llm_profile(),
        tools=[],
        skills=[],
        mcps=[],
        bd_actor=f"{project_key}.{persona_key}",
        casbin_subject=f"member:{project_key}.{persona_key}",
    )


def make_task_templates(
    keys: Iterable[str] = (StageName.program,),
    *,
    generation: int = 1,
    tools: list[ToolName] | None = None,
    description: str | None = None,
) -> TaskTemplates:
    """指定したキーだけを持つ `TaskTemplates` の見本（本文に `{title}` を含める）。

    Args:
        keys: 生成するテンプレートキー。
        generation: テンプレート一式の世代。
        tools: 各テンプレートに載せる道具。**既定は `None`**（`tasks.yaml` が道具を
            指定しない形）で、値を渡すのは IF-45 の `task_tools` の**写し**を踏む
            テストだけである。既定を変えると既存テストの前提が動く。
        description: `description` の本文を丸ごと差し替える。**既定は `None`**（`{title}`
            だけを持つ現状どおりの本文）で、値を渡すのは IF-45 の入力欄が名指しする
            `feedback` 等、既定の本文が参照しない変数の充填を踏むテストだけである。
            既定を変えると既存テストの前提が動く。

    Returns:
        `TaskTemplates` の見本。
    """
    return TaskTemplates(
        generation=generation,
        templates={
            str(key): TaskTemplate(
                description=(
                    f"{key} 段の作業指示: {{title}}" if description is None else description
                ),
                expected_output=f"{key} 段の期待成果: {{acceptance}}",
                agent=None,
                tools=tools,
            )
            for key in keys
        },
    )


def make_context_refs(
    *,
    purpose: PromptPurpose = PromptPurpose.work,
    is_final_review: bool = False,
) -> ContextRefs:
    """契約テストが使う `ContextRefs` の見本。"""
    return ContextRefs(
        issue_id="ct-1",
        title="契約テストの題名",
        description="契約テストの説明",
        acceptance="- [ ] 契約どおりに振る舞うことを確認できる",
        parent_title=None,
        parent_acceptance=None,
        human_guidance=[],
        input_refs=[],
        existing_children=[],
        purpose=purpose,
        is_final_review=is_final_review,
    )


def make_rules(text: str = "PJ 共通ルールの本文") -> Rules:
    """契約テストが使う `Rules` の見本。"""
    return Rules(project_rules=text)


def make_project_folders() -> ProjectFolders:
    """契約テストが使う `ProjectFolders` の見本（vault の一時木と同じ綴り）。"""
    return ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.programmer: "pj/roles/programmer"},
        outcome_dir="pj/outcome",
    )


# --------------------------------------------------------------------------------------
# 提供 MOD 別 fixture（`build()` 直呼び。FR-006a・FR-012）
# --------------------------------------------------------------------------------------


@pytest.fixture
def stages_handle() -> StagesHandle:
    """stages の `build()` を直呼びする（`config=` は既定＝ commit 済み `config.json`）。

    stages は `.env` を読まないので止め木の指し直しは不要である。
    """
    return build_stages(StagesDeps())


@pytest.fixture
def stage_catalog(stages_handle: StagesHandle) -> StageCatalog:
    """`StageCatalog` として使う（憲章 §3・FR-012: Protocol 型を必ず注釈する）。"""
    catalog: StageCatalog = stages_handle.stage_catalog
    return catalog


@pytest.fixture
def proposal_gate(stages_handle: StagesHandle) -> ProposalGate:
    """`ProposalGate` として使う（`stage_catalog` と同一インスタンス＝ stages FR-005）。"""
    gate: ProposalGate = stages_handle.proposal_gate
    return gate


# --------------------------------------------------------------------------------------
# board（IF-01〜22）— 実 `bd` と `BD_BIN` シム
# --------------------------------------------------------------------------------------

BOARD_PROJECT_KEY = "contractbd"
"""一時 beads の `bd init -p` に渡すプロジェクトキー。"""

BOARD_ACTOR = "contractpj.programmer"
"""`.` を含む actor 名も無改変で読み戻る（board M-01）。"""

BOARD_TIMEOUT_SEC = 120


@pytest.fixture
def board_beads(tmp_path: Path, bd_bin: str) -> Iterator[TempBeads]:
    """実 `bd init` した一時 beads（テスト後に `.git` と embedded Dolt ごと消す）。"""
    beads = init_beads(tmp_path, bd_bin, project_key=BOARD_PROJECT_KEY)
    yield beads
    remove_beads(beads)


def _build_board(
    beads_dir: Path,
    bd_path: str,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_file: Path,
) -> BoardHandle:
    """`BEADS_DIR`／`BD_BIN` を差し直し、**能動検査を通してから** `build()` を直呼びする。"""
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))
    monkeypatch.setenv("BD_BIN", bd_path)
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=env_file)
    return build_board(BoardDeps(), config=BoardConfig(timeout_sec=BOARD_TIMEOUT_SEC))


@pytest.fixture
def board_handle(
    board_beads: TempBeads,
    bd_bin: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env_file: Path,
) -> BoardHandle:
    """実 `bd` に向けた board の `build()` 直呼び（正常系用）。"""
    return _build_board(
        board_beads.beads_dir,
        bd_bin,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        env_file=isolated_env_file,
    )


@pytest.fixture
def board_reader(board_handle: BoardHandle) -> BoardReader:
    """`BoardReader` として使う（憲章 §3・FR-012）。"""
    reader: BoardReader = board_handle.board_reader
    return reader


@pytest.fixture
def board_writer(board_handle: BoardHandle) -> BoardWriter:
    """`BoardWriter` として使う（`board_reader` と同一インスタンス＝ board FR-005）。"""
    writer: BoardWriter = board_handle.board_writer
    return writer


@pytest.fixture
def board_unavailable_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_env_file: Path
) -> BoardHandle:
    """**シム (a)（常に失敗）** に向けた board（`BoardUnavailable` の 22 本用）。

    `bd init` を打たない軽量 beads で足りる（R-18）—— シムは argv を無視して失敗するので
    盤面の中身を一切使わない。実 `bd init` の回数を抑える。
    """
    return _build_board(
        make_light_beads(tmp_path),
        str(make_always_fails_shim(tmp_path)),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        env_file=isolated_env_file,
    )


@pytest.fixture
def board_unavailable_reader(board_unavailable_handle: BoardHandle) -> BoardReader:
    """不達シムに向けた `BoardReader`。"""
    reader: BoardReader = board_unavailable_handle.board_reader
    return reader


@pytest.fixture
def board_unavailable_writer(board_unavailable_handle: BoardHandle) -> BoardWriter:
    """不達シムに向けた `BoardWriter`。"""
    writer: BoardWriter = board_unavailable_handle.board_writer
    return writer


@pytest.fixture
def board_unavailable_on_healthy_handle(
    board_beads: TempBeads,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env_file: Path,
) -> BoardHandle:
    """**シム (a)** に向けた board（盤面だけは**実 `bd init` した健全なもの**）。

    `board_unavailable_handle` は軽量 beads（`bd init` を打たない＝ R-18）を掴むので、
    **シムが無くても実 `bd` が同じ `BoardUnavailable` を返す** —— つまり 22 本は
    `make_always_fails_shim` が壊れていても緑のままである。この fixture は盤面を健全にして
    **シムだけが不達の原因である**状況を作り、故障注入の手段が効いていることを実証させる
    （FR-021a (a)・SC-003）。実 `bd init` を使うので `requires_bd` が要る（FR-026）。
    """
    return _build_board(
        board_beads.beads_dir,
        str(make_always_fails_shim(tmp_path)),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        env_file=isolated_env_file,
    )


@pytest.fixture
def board_unavailable_on_healthy_reader(
    board_unavailable_on_healthy_handle: BoardHandle,
) -> BoardReader:
    """健全な盤面 ＋ 不達シムに向けた `BoardReader`。"""
    reader: BoardReader = board_unavailable_on_healthy_handle.board_reader
    return reader


@pytest.fixture
def board_unverified_handle(
    board_beads: TempBeads,
    bd_bin: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env_file: Path,
) -> BoardHandle:
    """**シム (b)（委譲＋`-l` 1 組落とし）** に向けた board（`BoardWriteUnverified` 用）。

    実 `bd` へ委譲するので実 `bd init` した beads が要る。ラベルが 1 つ足りない状態で
    読み戻ると `create` の確認読取が spec と食い違う。
    """
    return _build_board(
        board_beads.beads_dir,
        str(make_drops_one_label_shim(tmp_path, bd_bin)),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        env_file=isolated_env_file,
    )


@pytest.fixture
def board_unverified_writer(board_unverified_handle: BoardHandle) -> BoardWriter:
    """1 組落としシムに向けた `BoardWriter`。"""
    writer: BoardWriter = board_unverified_handle.board_writer
    return writer


# --------------------------------------------------------------------------------------
# vault（IF-31〜37）— 一時 vault の実物
# --------------------------------------------------------------------------------------


@pytest.fixture
def vault_store(tmp_path: Path) -> TempVault:
    """PJ 入力・役割・outcome の 3 領域と、ルート外の 1 ファイルを持つ一時 vault。"""
    return make_temp_vault(tmp_path)


@pytest.fixture
def vault_handle(
    vault_store: TempVault,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env_file: Path,
) -> VaultHandle:
    """`VAULT_ROOT` を一時 vault へ指し直し、**能動検査を通してから** `build()` を直呼びする。"""
    monkeypatch.setenv("VAULT_ROOT", str(vault_store.root))
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=isolated_env_file)
    return build_vault(VaultDeps())


@pytest.fixture
def vault_reader(vault_handle: VaultHandle) -> VaultReader:
    """`VaultReader` として使う（憲章 §3・FR-012）。"""
    reader: VaultReader = vault_handle.vault_reader
    return reader


@pytest.fixture
def outcome_writer(vault_handle: VaultHandle) -> OutcomeWriter:
    """`OutcomeWriter` として使う（`vault_reader` と同一インスタンス＝ vault FR-005）。"""
    writer: OutcomeWriter = vault_handle.outcome_writer
    return writer


# --------------------------------------------------------------------------------------
# settings（IF-23〜30・50）— 一時の設定ファイル領域 A ＋ 依存 2 スタブ
# --------------------------------------------------------------------------------------


@pytest.fixture
def area_a(tmp_path: Path) -> TempAreaA:
    """`build()` の整合検査 3 種を通る最小の一時領域 A。"""
    return make_temp_area_a(tmp_path)


@pytest.fixture
def settings_handle(
    area_a: TempAreaA,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env_file: Path,
) -> SettingsHandle:
    """settings の `build()` 直呼び（依存 2 件は記録つきスタブ）。

    ⚠ **能動検査を `build()` の前に必ず通す** —— settings の `build()` は `_scaffold` が
    雛形を**書き込む**ため、事後確認では手遅れになる（FR-013a・R-05）。
    """
    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)  # 憲章 §3・FR-012
    vault_reader: VaultReader = StubVaultReader(log)
    monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=isolated_env_file)
    return build_settings(SettingsDeps(board_reader=board_reader, vault_reader=vault_reader))


@pytest.fixture
def settings_reader(settings_handle: SettingsHandle) -> SettingsReader:
    """`SettingsReader` として使う（憲章 §3・FR-012）。"""
    reader: SettingsReader = settings_handle.settings_reader
    return reader


@pytest.fixture
def member_resolver(settings_handle: SettingsHandle) -> MemberResolver:
    """`MemberResolver` として使う（4 契約は同一インスタンス＝ settings FR-005）。"""
    resolver: MemberResolver = settings_handle.member_resolver
    return resolver


@pytest.fixture
def authorizer(settings_handle: SettingsHandle) -> Authorizer:
    """`Authorizer` として使う。"""
    enforcer: Authorizer = settings_handle.authorizer
    return enforcer


@pytest.fixture
def command_registry(settings_handle: SettingsHandle) -> CommandRegistry:
    """`CommandRegistry` として使う。"""
    registry: CommandRegistry = settings_handle.command_registry
    return registry


# --------------------------------------------------------------------------------------
# monitor（IF-38〜43）— 記録つきスタブ 2 種 ＋ 偽 LLM サーバの台本
# --------------------------------------------------------------------------------------

MONITOR_KICKOFF_ID = "kickoff-contract-1"
MONITOR_ISSUE_ID = "contractbd-000001"
MONITOR_ACTOR = "contractpj.programmer"
MONITOR_THRESHOLD_T = 16384
FINAL_ANSWER_CONTENT = "Thought: I now can give a great answer\nFinal Answer: 契約テストの出力"
"""1 往復で確定する CrewAI 向けの応答本文（R-09 の実測）。"""


def monitor_envelope_marker() -> str:
    """monitor が封筒の 1 行目に付ける印（`config.json` の実値から取る＝直書きしない）。"""
    return MonitorConfig().envelope_marker


def make_llm_script(
    *,
    content: str | None = FINAL_ANSWER_CONTENT,
    finish_reason: str = "stop",
    prompt_tokens: int = 12,
    completion_tokens: int = 8,
    cost: float | None = 0.001,
    turns: int = 1,
) -> list[dict[str, Any]]:
    """偽 LLM サーバの台本（1 要素＝1 往復。data-model §3-5 の 3 系統を 1 つの口で作る）。

    Args:
        content: 応答本文（`Final Answer:` 形にすると実 CrewAI が 1 往復で確定する）。
            道具呼び出しの往復では本文を持たないので `None` を渡す。
        finish_reason: `stop`／`length`（`length` は `finish_length` を積ませる）。
        prompt_tokens: `usage.prompt_tokens`。
        completion_tokens: `usage.completion_tokens`。
        cost: `usage.cost`（`None` にすると `usage_missing` を積ませる）。
        turns: 台本の要素数（使い切ったら最終要素を繰り返す）。

    Returns:
        `FakeLlmServer` に渡す台本。
    """
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cost is not None:
        usage["cost"] = cost
    turn: dict[str, Any] = {"content": content, "finish_reason": finish_reason, "usage": usage}
    return [dict(turn) for _ in range(turns)]


def make_monitor_issue(
    *,
    labels: list[str] | None = None,
    status: IssueStatus = IssueStatus.open,
    assignee: str | None = None,
) -> Issue:
    """確認読取（`_verify_mapping`）が読み戻す課題の姿。

    Args:
        labels: 盤面のラベル（写像後の姿を作るときに `state:failing` を渡す）。
        status: 課題の状態（W 級の一致条件は `closed`）。
        assignee: 担当（F 級の一致条件は**空**）。

    Returns:
        `StubBoardReader(issues=...)` に渡す 1 件。
    """
    now = datetime.now(tz=UTC)
    return Issue(
        id=MONITOR_ISSUE_ID,
        title="契約テストの課題",
        description="説明",
        status=status,
        labels=[] if labels is None else labels,
        parent=None,
        assignee=assignee,
        acceptance="- [ ] 受け入れ条件",
        dependencies=[],
        created_at=now,
        updated_at=now,
    )


def make_monitor_envelope_comment(grade: Grade) -> Comment:
    """`_find_envelope` が封筒とみなす形のコメント（印で始まり `kickoff_id=` を含む）。

    印は `config.json` の実値から取る（直書きしない）。**種類欄は照合に使われない**
    （一致条件は「印で始まる」「`kickoff_id` を含む」の 2 つだけ＝ `service.py` の
    `_find_envelope`）ので `kinds=-` のまま使い回せる。

    Args:
        grade: 封筒の 1 行目に載せる等級。

    Returns:
        `StubBoardReader(comments=...)` に渡す 1 件。
    """
    return Comment(
        id="comment-1",
        issue_id=MONITOR_ISSUE_ID,
        actor=MONITOR_ACTOR,
        body=f"{monitor_envelope_marker()} grade={grade.value} kinds=-\n"
        f"kickoff_id={MONITOR_KICKOFF_ID}",
        created_at=datetime.now(tz=UTC),
    )


@pytest.fixture
def call_log() -> CallLog:
    """スタブ横断で共有する 1 本の順序つき記録（IF-39 の順序照合に要る）。"""
    return CallLog()


@pytest.fixture
def monitor_factory(
    call_log: CallLog,
) -> Callable[[StubBoardReader, StubBoardWriter], MonitorHandle]:
    """記録つきスタブ 2 種を渡して monitor の `build()` を直呼びする口。

    テストごとに「盤面が何を返すか」が違う（写像前／写像後）ので、スタブの組み立ては
    テスト側に置き、ここは `MonitorDeps` へ**注釈つきで**渡す責務だけを持つ。
    monitor は `.env` を読まないので止め木の指し直しは不要である。
    """

    def _build(reader_stub: StubBoardReader, writer_stub: StubBoardWriter) -> MonitorHandle:
        board_reader: BoardReader = reader_stub  # 憲章 §3・FR-012
        board_writer: BoardWriter = writer_stub
        return build_monitor(MonitorDeps(board_reader=board_reader, board_writer=board_writer))

    del call_log  # 記録の実体はテストが `call_log` fixture から直に受け取る
    return _build


@pytest.fixture
def monitor_handle(
    call_log: CallLog, monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle]
) -> MonitorHandle:
    """既定（無害な空のスタブ）で組んだ monitor（Hook 系のテストが使う）。"""
    return monitor_factory(StubBoardReader(call_log), StubBoardWriter(call_log))


@pytest.fixture
def run_monitor(monitor_handle: MonitorHandle) -> RunMonitor:
    """`RunMonitor` として使う（憲章 §3・FR-012）。"""
    monitor: RunMonitor = monitor_handle.run_monitor
    return monitor


@pytest.fixture
def llm_http_hook(monitor_handle: MonitorHandle) -> LLMHttpHook:
    """`LLMHttpHook` として使う（**実 Hook**。`run_monitor` と同じ `build()` の戻り）。"""
    hook: LLMHttpHook = monitor_handle.llm_http_hook
    return hook


# --------------------------------------------------------------------------------------
# executor（IF-48）— スタブ 7 種 ＋ 実 CrewAI
# --------------------------------------------------------------------------------------

EXECUTOR_API_KEY_REF = "LLM_API_KEY_CONTRACT"
"""一時 `.env` に置く鍵の参照名（**実値はダミー**。実 LLM API へは通信しない＝ FR-010）。"""

EXECUTOR_API_KEY_VALUE = "sk-contract-test-dummy"


@pytest.fixture
def executor_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """executor が読む `.env` を一時ファイルへ指し直す（R-04 の 2 段目）。

    差し替え点は **`executor.models.env_file_path`** である（`executor.env_file_path` は
    再エクスポートなので差し替えても効かない）。中身は鍵の**参照名とダミー値**だけで、
    実運用の鍵は 1 つも載らない。
    """
    path = tmp_path / "executor.env"
    path.write_text(f"{EXECUTOR_API_KEY_REF}={EXECUTOR_API_KEY_VALUE}\n", encoding="utf-8")
    monkeypatch.setattr("executor.models.env_file_path", lambda: path)
    return path


class ExecutorFactory(Protocol):
    """スタブ 7 種を差し替えて executor を組む口（`executor_factory` の型）。

    `LLMHttpHook` だけ**位置引数**で受ける（`RunHalted` の伝播を踏むテストが送出口つきの
    スタブを渡すため）。認可判定器と成果物の書き手はキーワードで差し替える —— 申告 4 道具や
    `write_outcome` を**実際に呼ばせる**テストは、既定（すべて拒否・書き手は未設定）のままだと
    道具の前段で止まる。**型を `Callable[..., …]` に緩めない**。
    """

    def __call__(
        self,
        hook_stub: StubLLMHttpHook,
        *,
        authorizer_stub: StubAuthorizer | None = None,
        outcome_writer_stub: StubOutcomeWriter | None = None,
    ) -> ExecutorHandle:
        """7 スタブから executor を組む。"""
        ...


@pytest.fixture
def executor_factory(call_log: CallLog, executor_env_file: Path) -> ExecutorFactory:
    """スタブ 7 種を `ExecutorDeps` へ**注釈つきで**渡して `build()` を直呼びする口。

    **必ず `build()` を通す** —— `build()` が `CREWAI_DISABLE_TELEMETRY`・`OTEL_SDK_DISABLED`
    を立てる（R-09 の実測）。スタブは「呼ばれないはずの面を `NotImplementedError`」に倒して
    あり、契約の外の呼び出しが起きたら赤くなる（K-2）。
    """
    del executor_env_file  # 差し替えの副作用だけが要る（値は使わない）

    def _build(
        hook_stub: StubLLMHttpHook,
        *,
        authorizer_stub: StubAuthorizer | None = None,
        outcome_writer_stub: StubOutcomeWriter | None = None,
    ) -> ExecutorHandle:
        board_reader: BoardReader = StubBoardReader(call_log)  # 憲章 §3・FR-012
        board_writer: BoardWriter = StubBoardWriter(call_log)
        vault_reader: VaultReader = StubVaultReader(call_log)
        outcome_writer: OutcomeWriter = outcome_writer_stub or StubOutcomeWriter(call_log)
        authorizer: Authorizer = authorizer_stub or StubAuthorizer(call_log)
        command_registry: CommandRegistry = StubCommandRegistry(call_log)
        llm_http_hook: LLMHttpHook = hook_stub
        repo_reader: RepoReader = StubRepoReader(call_log)
        repo_writer: RepoWriter = StubRepoWriter(call_log)
        return build_executor(
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

    return _build


@pytest.fixture
def crew_executor(call_log: CallLog, executor_factory: ExecutorFactory) -> CrewExecutor:
    """既定（素通しの Hook スタブ）で組んだ `CrewExecutor`。"""
    executor: CrewExecutor = executor_factory(StubLLMHttpHook(call_log)).crew_executor
    return executor


# --------------------------------------------------------------------------------------
# orchestrator（IF-49）— スタブ 10 種
# --------------------------------------------------------------------------------------


class OrchestratorFactory(Protocol):
    """10 スタブを差し替えて orchestrator を組む口（`orchestrator_factory` の型）。

    `BoardReader` だけ**位置引数**で受ける（`KickoffBusy` のテストが `list_working` を
    `threading.Event` で塞ぐ派生スタブを渡すため）。残る 9 種はキーワードで、省略すると
    「空を返す既定のスタブ」になる。**型を `Callable[..., …]` に緩めない** —— 渡し先を
    間違えたときに pyright strict が捕まえられなくなる。
    """

    def __call__(
        self,
        reader_stub: StubBoardReader,
        *,
        writer_stub: StubBoardWriter | None = None,
        settings_stub: StubSettingsReader | None = None,
        member_stub: StubMemberResolver | None = None,
        vault_stub: StubVaultReader | None = None,
        monitor_stub: StubRunMonitor | None = None,
        catalog_stub: StubStageCatalog | None = None,
        gate_stub: StubProposalGate | None = None,
        executor_stub: StubCrewExecutor | None = None,
        repo_stub: StubRepoReader | None = None,
    ) -> OrchestratorHandle:
        """10 スタブから orchestrator を組む。"""
        ...


@pytest.fixture
def orchestrator_factory(call_log: CallLog) -> OrchestratorFactory:
    """スタブ 10 種を `OrchestratorDeps` へ**注釈つきで**渡して `build()` を直呼びする口。

    orchestrator は `.env` を読まない。既定はどれも「空／`None`／偽」を返すスタブなので、
    何も渡さなければ 1 課題も処理しない（`status=noop`）。1 課題を通す経路を踏むテストは
    必要なスタブだけを差し替える。
    """

    def _build(
        reader_stub: StubBoardReader,
        *,
        writer_stub: StubBoardWriter | None = None,
        settings_stub: StubSettingsReader | None = None,
        member_stub: StubMemberResolver | None = None,
        vault_stub: StubVaultReader | None = None,
        monitor_stub: StubRunMonitor | None = None,
        catalog_stub: StubStageCatalog | None = None,
        gate_stub: StubProposalGate | None = None,
        executor_stub: StubCrewExecutor | None = None,
        repo_stub: StubRepoReader | None = None,
    ) -> OrchestratorHandle:
        board_reader: BoardReader = reader_stub  # 憲章 §3・FR-012
        board_writer: BoardWriter = writer_stub or StubBoardWriter(call_log)
        settings_reader: SettingsReader = settings_stub or StubSettingsReader(call_log)
        member_resolver: MemberResolver = member_stub or StubMemberResolver(call_log)
        vault_reader: VaultReader = vault_stub or StubVaultReader(call_log)
        run_monitor: RunMonitor = monitor_stub or StubRunMonitor(call_log)
        stage_catalog: StageCatalog = catalog_stub or StubStageCatalog(call_log)
        proposal_gate: ProposalGate = gate_stub or StubProposalGate(call_log)
        crew_executor: CrewExecutor = executor_stub or StubCrewExecutor(call_log)
        repo_reader: RepoReader = repo_stub or StubRepoReader(call_log)
        return build_orchestrator(
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

    return _build


@pytest.fixture
def kickoff_runner(call_log: CallLog, orchestrator_factory: OrchestratorFactory) -> KickoffRunner:
    """既定（空を返す 10 スタブ）で組んだ `KickoffRunner`。"""
    runner: KickoffRunner = orchestrator_factory(StubBoardReader(call_log)).kickoff_runner
    return runner


# --------------------------------------------------------------------------------------
# repo（IF-51〜55）— 一時の作業ツリー（D25）
# --------------------------------------------------------------------------------------

REPO_SOURCE_PATH = "src/app.py"
"""一時の作業ツリーに置くソース（読める・在る）。"""

REPO_SOURCE_BODY = "print('hello')\n"
"""`REPO_SOURCE_PATH` の中身。"""


@pytest.fixture
def repo_store(tmp_path: Path) -> ProjectRepos:
    """`.git/`・ソース 1 つ・秘密ファイル 1 つを持つ一時の作業ツリーを指す対応付け。

    repo は git コマンドを呼ばないので `.git` は空ディレクトリで足りる。**外側に実体を作らない**
    （シンボリックリンクの検査はリンク先も `tmp_path` の内側に作る）。
    """
    root = tmp_path / "work"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    (root / REPO_SOURCE_PATH).write_text(REPO_SOURCE_BODY, encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    return ProjectRepos(remote_url="https://example.com/g/r.git", local_path=str(root))


@pytest.fixture
def repo_handle() -> RepoHandle:
    """`build()` を直呼びする（repo は `.env` を読まないので止め木は要らない）。"""
    return build_repo(RepoDeps())


@pytest.fixture
def repo_reader(repo_handle: RepoHandle) -> RepoReader:
    """`RepoReader` として使う（憲章 §3・FR-012）。"""
    reader: RepoReader = repo_handle.repo_reader
    return reader


@pytest.fixture
def repo_writer(repo_handle: RepoHandle) -> RepoWriter:
    """`RepoWriter` として使う（`repo_reader` と同一インスタンス）。"""
    writer: RepoWriter = repo_handle.repo_writer
    return writer
