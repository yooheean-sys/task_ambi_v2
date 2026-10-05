"""settings が提供する契約（IF-23〜30・IF-50）。

プロジェクト・メンバ・上限・ルール・段テンプレート・Hook・認可・コマンド台帳の読み口。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/settings/p5_settings_data_model.mmd` の
`namespace common_interfaces_settings_py`。

`api_key` の**実値は載せない**（`api_key_ref` は .env のキー名。x3_notes §4 (1)）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from common.kernel.enums import PersonaKey, ToolName
from common.kernel.errors import NotFoundError


class ProviderKind(StrEnum):
    """LLM プロバイダの種別（P5D-013）。"""

    openai_compatible = "openai_compatible"
    openrouter = "openrouter"


class ProjectFolders(BaseModel):
    """プロジェクトのフォルダ対応付け（DM-02）。いずれも vault ルート相対。"""

    model_config = ConfigDict(frozen=True)

    pj_input_dir: str
    role_dirs: dict[PersonaKey, str]
    outcome_dir: str


class ProjectRepos(BaseModel):
    """プロジェクトのリポジトリ対応付け（CFG-74）。両項目とも任意（未登録可）。

    `remote_url` はリモートリポジトリの URL、`local_path` はローカルリポジトリの
    OS 絶対パス（保管庫の外の実リポジトリを指す）。コード編纂の実行・レビュー時に
    AI へのプロンプトへ知らせるための情報であり、git 操作の手段は持たない。
    """

    model_config = ConfigDict(frozen=True)

    remote_url: str | None = None
    local_path: str | None = None


class Project(BaseModel):
    """プロジェクト 1 件（DM-01）。"""

    model_config = ConfigDict(frozen=True)

    project_key: str
    name: str
    folders: ProjectFolders
    is_reserved: bool
    repos: ProjectRepos | None = None


class McpSpec(BaseModel):
    """MCP サーバの指定（`Agent.mcps`）。"""

    model_config = ConfigDict(frozen=True)

    name: str
    transport: str
    command: str
    args: list[str]


class LLMProfile(BaseModel):
    """LLM プロファイル（DM-11。PJ→global→default 解決済み）。"""

    model_config = ConfigDict(frozen=True)

    name: str
    provider_kind: ProviderKind
    base_url: str
    model_id: str
    context_window: int
    api_key_ref: str
    extra_headers: dict[str, str] = {}
    threshold_t: int


class Member(BaseModel):
    """メンバ 1 件（DM-03＋DM-07 導出。agents.yaml ④→⑤ merge 済み D16-d/h）。"""

    model_config = ConfigDict(frozen=True)

    member_id: str
    project_key: str
    persona_key: PersonaKey
    role: str
    goal: str
    backstory: str
    llm_profile: LLMProfile
    tools: list[ToolName]
    skills: list[str]
    mcps: list[McpSpec]
    max_iter: int | None = None
    max_rpm: int | None = None
    max_execution_time: int | None = None
    bd_actor: str
    casbin_subject: str


class LimitPolicy(BaseModel):
    """1 kickoff あたりの上限（DM-12。global＋PJ 上書き解決済み）。"""

    model_config = ConfigDict(frozen=True)

    scope: str
    max_requests_per_kickoff: int
    max_cost_per_kickoff: float


class Rules(BaseModel):
    """プロジェクト共通ルール（DM-05。D16-h で `project_rules` のみに縮小）。"""

    model_config = ConfigDict(frozen=True)

    project_rules: str


class TaskTemplate(BaseModel):
    """段テンプレート 1 段（DM-27）。"""

    model_config = ConfigDict(frozen=True)

    description: str
    expected_output: str
    agent: PersonaKey | None = None
    tools: list[ToolName] | None


class TaskTemplates(BaseModel):
    """段テンプレート一式（DM-27。global→PJ 上書き適用済み）。"""

    model_config = ConfigDict(frozen=True)

    generation: int
    templates: dict[str, TaskTemplate]


class HookRef(BaseModel):
    """Hook の登録参照。"""

    model_config = ConfigDict(frozen=True)

    name: str
    points: Literal["PRE_TOOL_CALL", "POST_TOOL_CALL"]
    agents: list[str] = []


class HooksConfig(BaseModel):
    """Hook の設定（DM-06）。"""

    model_config = ConfigDict(frozen=True)

    hooks: list[HookRef]


class ProjectNotFound(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_settings_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """指定のプロジェクトが台帳に無い。"""


class MemberNotFound(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_settings_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """指定のメンバが台帳に無い。"""


class UnknownCommand(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_settings_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """指定の名前がコマンド台帳に無い。"""


class SettingsReader(Protocol):
    """設定の読取（IF-23〜26・IF-50）。"""

    def get_project(self, project_key: str) -> Project:
        """プロジェクトを読む。

        Raises:
            ProjectNotFound: 指定のプロジェクトが無い。
        """
        ...

    def get_limits(self, project_key: str) -> LimitPolicy:
        """上限を読む（global＋PJ 上書き解決済み）。"""
        ...

    def get_rules(self, project_key: str) -> Rules:
        """プロジェクト共通ルールを読む。

        Raises:
            ProjectNotFound: 指定のプロジェクトが無い。
        """
        ...

    def get_hooks(self, project_key: str) -> HooksConfig:
        """Hook 設定を読む。

        Raises:
            ProjectNotFound: 指定のプロジェクトが無い。
        """
        ...

    def get_tasks(self, project_key: str) -> TaskTemplates:
        """段テンプレートを読む（DM-27 の 7 キー）。

        Raises:
            ProjectNotFound: 指定のプロジェクトが無い。
        """
        ...


class MemberResolver(Protocol):
    """メンバの解決（IF-27・IF-28）。"""

    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member:
        """プロジェクトとペルソナからメンバを解決する。

        Raises:
            MemberNotFound: 指定のメンバが無い。
        """
        ...

    def is_human(self, actor: str) -> bool:
        """指定の actor が人間か（台帳内＝AI）。"""
        ...


class Authorizer(Protocol):
    """認可判定（IF-29）。"""

    def enforce(self, subject: str, obj: str, act: str) -> bool:
        """認可を判定する（casbin enforce）。Tool の前段判定に使う。"""
        ...


class CommandRegistry(Protocol):
    """コマンド台帳（IF-30）。"""

    def resolve_command(self, name: str) -> list[str]:
        """登録名から argv を解決する（`shell=False`・引数は AI に渡させない）。

        Raises:
            UnknownCommand: 指定の名前が台帳に無い。
        """
        ...
