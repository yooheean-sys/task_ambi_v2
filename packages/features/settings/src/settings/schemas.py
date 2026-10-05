"""HTTP 境界の入出力（data-model.md §2）。**正本は `openapi.yaml` の components**。

`common/interfaces/settings.py` の受け渡し型とは**別に定義する**（C-08・FR-079）。同名の
`HookRef`・`HooksConfig`・`CommandDef` が契約側にも在るが、HTTP 境界の型は口の契約であり、
モジュール間の受け渡し型とは寿命も所有者も違う（憲章 §3・CLAUDE.md のファイル構成）。

**入力になる型には `extra="forbid"` を必ず付ける**（FR-079a）。これで起動時検査に受け皿が無い
3 つの壊れ方（**B-16** 未知キー／必須キー欠け・**B-17** `hooks.yaml` のトップレベルの綴り違い・
**DIFF-18** `p`／`g` 以外の権限行）が**口からは作れなくなる**。**起動時検査の規則は 1 つも
足さない**ので、人が直接編集した領域 A に残る同じ穴は開いたままである（010 SD-10）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from common.interfaces.settings import ProviderKind
from common.kernel.enums import PersonaKey, ToolName

# --------------------------------------------------------------------------------------
# §2-1 共通 3 種
# --------------------------------------------------------------------------------------


class ValidationProblemItem(BaseModel):
    """422 の本文の 1 件（`openapi.yaml` の `Problem.problems[]`）。

    **`kind` は実装の 14 語をそのまま返す**（A-09・B-42。14 語目の `format` は D24 で追加）。
    正本の enum は 7 語しか持たないが、正本の側が古い
    （`_archive/design_過去の判断/p5r1/p5r1_notes.md` §5 が語彙の正本）。
    `str` で受けるのは、切り詰めずに全 14 語を通すためである。
    """

    model_config = ConfigDict(frozen=True)

    where: str
    kind: str
    reason: str


class ProblemBody(BaseModel):
    """**422** の本文（`openapi.yaml` の `Problem`）。違反を**全件**載せる（FR-012・SC-005）。"""

    model_config = ConfigDict(frozen=True)

    problems: list[ValidationProblemItem]


class DetailBody(BaseModel):
    """**404**・**409** の本文（同 `NotFound`／`Conflict`。ともに**単一文字列**）。

    解除の拒否は 409 の `detail` 1 本になり、機械可読な違反一覧は返らない（010 SD-03）。
    拡張が要るときは orchestrator の `KickoffBusyDetail` と同じく**平らに**行う。
    """

    model_config = ConfigDict(frozen=True)

    detail: str


# --------------------------------------------------------------------------------------
# §2-2 プロジェクト（F11）
# --------------------------------------------------------------------------------------


class ProjectFoldersSpec(BaseModel):
    """`ProjectSpec.folders`。3 種すべて必須（DM-02）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pj_input_dir: str
    role_dirs: dict[PersonaKey, str]
    outcome_dir: str


class ProjectReposSpec(BaseModel):
    """`ProjectSpec.repos`（CFG-74）。両項目とも任意（未登録可）。

    `remote_url` はリモートリポジトリの URL、`local_path` はローカルリポジトリの
    OS 絶対パス（保管庫の外の実リポジトリを指す）。形式の検査は保存時の規則が担う。
    **空文字・空白のみの値は `None`（未登録）に正規化する**（保存しない値を作らない）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    remote_url: str | None = None
    local_path: str | None = None

    @field_validator("remote_url", "local_path", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """空文字・空白のみの入力を未登録（`None`）と同一視する。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ProjectSpec(BaseModel):
    """`POST`・`PUT /settings/projects` の本文（DM-01）。

    **`PUT` の本文にも `project_key` が含まれる**（正本が `ProjectSpec` を指定しているため）。
    パスの `{project_key}` と食い違ったら **422 `key_rule`** で拒否する。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_key: str
    name: str
    folders: ProjectFoldersSpec
    repos: ProjectReposSpec | None = None


class ProjectView(BaseModel):
    """プロジェクトの応答（`ProjectSpec` ＋ `is_reserved`）。`_program` だけ真（P5D-014）。"""

    model_config = ConfigDict(frozen=True)

    project_key: str
    name: str
    folders: ProjectFoldersSpec
    is_reserved: bool
    repos: ProjectReposSpec | None = None


# --------------------------------------------------------------------------------------
# §2-3 メンバ・段テンプレート（F12・F17）
# --------------------------------------------------------------------------------------


class McpSpecSchema(BaseModel):
    """`AgentSpec.mcps` の要素（宣言のみ。**接続しない**＝ REC-02）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    transport: str
    command: str
    args: list[str]


class AgentSpec(BaseModel):
    """`PUT /settings/agents/...` の本文（許可 10 種。**全部任意**）。

    `global` の必須 3（`role`・`goal`・`backstory`）は**候補の検査 V-04** が見る（口では見ない）
    —— PJ 側は全任意なので同じ型を使う。`extra="forbid"` が**許可 10 の外**（V-03）と
    **禁止キー**（V-05）を口から締め出す。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    llm: str | None = None
    tools: list[ToolName] | None = None
    skills: list[str] | None = None
    mcps: list[McpSpecSchema] | None = None
    max_iter: int | None = None
    max_rpm: int | None = None
    max_execution_time: int | None = None


class AgentView(BaseModel):
    """メンバの応答（`AgentSpec` ＋ 台帳の導出値）。

    **`project_key` が `null` なら全体の既定行**である（レベル付き一覧の判別点＝ R-11・D-4）。
    """

    model_config = ConfigDict(frozen=True)

    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    llm: str | None = None
    tools: list[ToolName] | None = None
    skills: list[str] | None = None
    mcps: list[McpSpecSchema] | None = None
    max_iter: int | None = None
    max_rpm: int | None = None
    max_execution_time: int | None = None
    member_id: str
    project_key: str | None
    persona_key: PersonaKey
    bd_actor: str
    casbin_subject: str
    resolved_llm_profile: str


class TaskTemplateSpec(BaseModel):
    """段テンプレート 1 段（DM-27）。前 2 つは必須（**V-10a** が候補側でも見る）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str
    expected_output: str
    agent: PersonaKey | None = None
    tools: list[ToolName] | None = None


class TaskTemplatesView(RootModel[dict[str, TaskTemplateSpec]]):
    """段キー → テンプレート（`PUT` の本文でもある）。

    全体は **7 キー必須**だが、それを見るのは**候補の検査 V-08** である（口では見ない）
    —— PJ 側はキー単位の丸ごと置換なので同じ型を使う。
    """

    root: dict[str, TaskTemplateSpec]


# -- 役割ごとの実効設定（D26。閲覧のみ） ------------------------------------------------


class RoleToolView(BaseModel):
    """Tool 1 種の装備と許可の対照（どちらか一方だけ真なら食い違い）。"""

    model_config = ConfigDict(frozen=True)

    tool: ToolName
    equipped: bool
    allowed: bool


class RoleStageView(BaseModel):
    """この役割が `agent:` で既定担当になっている段 1 つ。

    `replaces_tools` が真なら段の `tools:` が装備を置き換え（D16-a）、`tools` にその段での
    対照が入る。偽なら `tools` は空（メンバの装備がそのまま使われる）。
    """

    model_config = ConfigDict(frozen=True)

    stage: str
    replaces_tools: bool
    tools: list[RoleToolView]


class PolicyGrant(BaseModel):
    """権限表の `p` 行のうち `tool:` 以外の 1 件（2 段目の許可）。"""

    model_config = ConfigDict(frozen=True)

    obj: str
    act: str


class RoleView(BaseModel):
    """メンバ 1 人の実効設定（merge 済みの装備・解決済みの LLM・権限表）。"""

    model_config = ConfigDict(frozen=True)

    persona_key: PersonaKey
    member_id: str
    casbin_subject: str
    role: str
    goal: str
    llm_profile: str
    stages: list[RoleStageView]
    tools: list[RoleToolView]
    grants: list[PolicyGrant]


class StageToolsView(BaseModel):
    """`agent:` を持たずに `tools:` を持つ段 1 つ。

    担当は orchestrator の設定（`review_persona`）が決めるので settings からは見えない。
    """

    model_config = ConfigDict(frozen=True)

    stage: str
    tools: list[ToolName]


class RolesView(BaseModel):
    """`GET /settings/projects/{project_key}/roles` の応答（D26）。"""

    model_config = ConfigDict(frozen=True)

    project_key: str
    roles: list[RoleView]
    stage_tools: list[StageToolsView]


# --------------------------------------------------------------------------------------
# §2-4 LLM プロファイル・上限・コマンド定義（F13・F14・F15）
# --------------------------------------------------------------------------------------


class LlmProfileSpec(BaseModel):
    """LLM プロファイルの入力（DM-11）。**鍵の実値を持たない**（`api_key_ref` は参照名）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    provider_kind: ProviderKind
    base_url: str
    model_id: str
    context_window: int = Field(ge=1)
    api_key_ref: str
    extra_headers: dict[str, str] = Field(default_factory=dict[str, str])
    is_default: bool = False


class LlmProfileView(BaseModel):
    """LLM プロファイルの応答（`LlmProfileSpec` ＋ 導出値）。

    **`threshold_t` は小文字**で返す（A-10・B-43）。正本は `threshold_T` だが、現物の受け渡し型
    `common.interfaces.settings.LLMProfile.threshold_t` が小文字であり、そちらに揃える。
    """

    model_config = ConfigDict(frozen=True)

    name: str
    provider_kind: ProviderKind
    base_url: str
    model_id: str
    context_window: int
    api_key_ref: str
    extra_headers: dict[str, str] = Field(default_factory=dict[str, str])
    is_default: bool = False
    threshold_t: int


class LlmProfilesView(BaseModel):
    """`GET /settings/llm-profiles` の応答。"""

    model_config = ConfigDict(frozen=True)

    profiles: list[LlmProfileView]
    default: str | None


class LimitSpec(BaseModel):
    """上限 1 組の入力（DM-12）。**0・負値は口でも候補の検査（V-27）でも落ちる**（二重）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_requests_per_kickoff: int = Field(ge=1)
    max_cost_per_kickoff: float = Field(ge=0)


class LimitsView(BaseModel):
    """`GET /settings/limits` の応答。

    ⚠ **`global_` は解決値**である（`global/limits.yaml` の `global:` が未設定なら **CFG-39 の
    ③ 段**＝ `config.json` の `limits_defaults`）。**ファイルに `global:` が在ることを意味しない**
    （010 SD-16）。したがって**そのまま書き戻さない** —— PJ 上書きの保存・解除（#24・#25・#5）が
    組む候補は `overrides` だけを差し替え、`LimitsRecord.global_` は**未設定のまま残す**。
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    global_: LimitSpec = Field(alias="global")
    overrides: dict[str, LimitSpec] = Field(default_factory=dict[str, LimitSpec])


class CommandDefSchema(BaseModel):
    """コマンド定義 1 件（DM-26。`POST`・`PUT` の本文でもある）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    argv: list[str] = Field(min_length=1)


# --------------------------------------------------------------------------------------
# §2-5 権限・ルール・Hook・人間（F16・F17）
# --------------------------------------------------------------------------------------


class PolicyRow(BaseModel):
    """権限表の 1 行。

    **`ptype` の enum が DIFF-18 を口から締め出す**（`q, …` のような行を作れない）。列数
    （`p`=3・`g`=2）は**候補の判定器**（`build_enforcer` の担保 2）が見る。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ptype: Literal["p", "g"]
    fields: list[str]


class PolicySpec(BaseModel):
    """`PUT /settings/policies/{project_key}` の本文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[PolicyRow]


class PolicyView(BaseModel):
    """権限表の応答（`PolicySpec` ＋ 参照用の `model_conf`）。

    `model_conf` は**参照用の本文**であり CRUD 対象外である（FR-064。`g = _, _` を matcher から
    落とさないため、権限モデルは口から編集できない）。
    """

    model_config = ConfigDict(frozen=True)

    rows: list[PolicyRow]
    project_key: str
    model_conf: str


class TextBody(BaseModel):
    """共通ルールの本文（`rules.md`）。**空文字も可**（FR-065）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: str


class HookRefSchema(BaseModel):
    """Hook の登録参照（`name`・`points` は必須）。

    **`points` は一覧**である（正本 `openapi.yaml` の `HookRef.points: array`）。領域 A の
    `hooks.yaml` は 1 宣言 1 地点の形なので（**V-18** が `points` を単一文字列で見る）、
    `service/ai_config.py` が**地点ごとに 1 宣言へ展開して**書き、読取時に 1 要素の一覧へ畳む。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    points: list[Literal["PRE_TOOL_CALL", "POST_TOOL_CALL"]] = Field(min_length=1)
    agents: list[str] | None = None


class HooksConfigSchema(BaseModel):
    """`PUT /settings/hooks/{project_key}` の本文。

    **トップレベルキーが `hooks` であることを型が強制する**（B-17 を口から締め出す）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hooks: list[HookRefSchema]


class HumansView(BaseModel):
    """人間の行為者の一覧（`PUT /settings/humans` の本文でもある）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    human_actors: list[str]
