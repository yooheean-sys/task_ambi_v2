"""領域 A のファイル写像・語彙の定数・スナップショット・台帳（data-model.md §2〜§4）。

**検査を通ってから写像する**（R-03）。写像モデルは `extra="forbid"`・`frozen=True` だが、
そこで初めて落ちることは無い（落ちたら内部整合の破れ＝バグ）。

`PolicyEnforcer` は casbin の `Enforcer` を settings の内側に閉じるための構造的部分型である。
casbin は型スタブを持たないため、実体の import と `cast` は `service/loader.py` の 1 箇所に
閉じ、スナップショットの型面には本 Protocol しか現れない（FR-010・SC-012）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from common.interfaces.settings import (
    HookRef,
    LimitPolicy,
    LLMProfile,
    McpSpec,
    Member,
    Project,
    ProviderKind,
    Rules,
)
from common.kernel.enums import PersonaKey, ToolName

if TYPE_CHECKING:
    from collections.abc import Mapping

    from common.interfaces.settings import HooksConfig

# --------------------------------------------------------------------------------------
# §3 語彙の定数（C-4。`config.json` に出さない＝ x3_config_catalog.csv は凍結対象）
# --------------------------------------------------------------------------------------

AGENT_FIELDS: frozenset[str] = frozenset(
    {
        "role",
        "goal",
        "backstory",
        "llm",
        "tools",
        "skills",
        "mcps",
        "max_iter",
        "max_rpm",
        "max_execution_time",
    }
)
"""`agents.yaml` の許可フィールド 10 種（D16-d）。"""

AGENT_GLOBAL_REQUIRED: frozenset[str] = frozenset({"role", "goal", "backstory"})
"""`global/agents.yaml` の必須フィールド 3 種（D16-d。PJ 側は全任意）。"""

FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "allow_delegation",
        "function_calling_llm",
        "step_callback",
        "cache_handler",
        "knowledge_sources",
    }
)
"""`agents.yaml` の禁止キー 5 種（D16-d）。"""

SECRET_KEY_WORDS: frozenset[str] = frozenset({"api_key", "token", "secret", "password"})
"""秘密語 4 種（D15-i (5)）。**キー名の完全一致**で判定する。

部分一致にしない理由: `LLMProfile.api_key_ref`（FR-049 が定める参照名）と
`settings.context_reserve_tokens`（CFG-37）はいずれも秘密ではないが、部分一致だと
両方が違反になり、規則そのものが成立しない。FR-050 の文言も
「`api_key`／`token`／`secret`／`password` **という名前のキー**」である。
"""

TASK_FIELDS: frozenset[str] = frozenset({"description", "expected_output", "agent", "tools"})
"""`tasks.yaml` の許可フィールド 4 種（D16-f）。"""

TASK_REQUIRED_FIELDS: tuple[str, ...] = ("description", "expected_output")
"""`tasks.yaml` の必須フィールド 2 種（D16-f）。"""

TASK_KEYS: tuple[str, ...] = (
    "program",
    "project",
    "subrequest",
    "child",
    "plain",
    "review_normal",
    "review_final",
)
"""段テンプレートの 7 キー（D16-a）。

**`StageName` を使わない**。`StageName` は 5 種で `review_normal`・`review_final` を持たず、
契約側も `TaskTemplates.templates: dict[str, TaskTemplate]` と `str` キーである。
"""

ALLOWED_VARS: frozenset[str] = frozenset(
    {
        "issue_id",
        "title",
        "description",
        "acceptance",
        "parent_title",
        "parent_acceptance",
        "persona_guide",
        "human_guidance",
        "feedback",
        "input_rules",
        "input_refs",
        "existing_children",
        "project_key",
        "persona_key",
        "outcome_dir",
        "repository_remote_url",
        "repository_local_path",
    }
)
"""段テンプレート本文の `{変数}` の許可 17 種（D16-c ＋ D24 の 2 種）。

`repository_remote_url`・`repository_local_path` はプロジェクトのリポジトリ対応付け
（CFG-74）を AI に教えるための 2 変数で、D24 で追加された。**`REQUIRED_VARS` には入れない**
（入れると `repos` 未登録のプロジェクトの全 `tasks.yaml` が起動時検査で落ちる）。値は
`ContextRefs` 経由で stages が充填し、未登録のプロジェクトでは空文字になる（FR-013 踏襲）。
"""

REQUIRED_VARS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "program": frozenset({"persona_guide", "acceptance"}),
        "project": frozenset({"persona_guide", "acceptance"}),
        "subrequest": frozenset({"persona_guide", "acceptance"}),
        "child": frozenset({"acceptance"}),
        "plain": frozenset({"acceptance"}),
        "review_normal": frozenset({"acceptance"}),
        "review_final": frozenset({"acceptance"}),
    }
)
"""段別の必須変数（D16-c）。`description` と `expected_output` を**連結した集合**で判定する。"""

SUBJECT_PREFIX = "member:"
"""認可の主体名の接頭辞（FR-037・FN-23）。"""

MEMBER_ID_SEPARATOR = "."
"""メンバ ID の区切り（`<project_key>.<persona_key>`＝ D06-b）。"""

PROBE_TRIPLE: tuple[str, str, str] = ("member:__probe__", "__probe__", "__probe__")
"""健全性検査の空打ちに使う 3 つ組（FR-048b・M-08）。**戻り値は判定に使わない**。"""

SCOPE_GLOBAL = "global"
"""`LimitPolicy.scope` が全体を指すときのリテラル（R-18）。"""

PROBLEM_KINDS: tuple[str, ...] = (
    "unknown_field",
    "forbidden_key",
    "agent_ref",
    "llm_ref",
    "tool_ref",
    "var_unknown",
    "var_required",
    "forward",
    "orphan",
    "cmd_ref",
    "key_rule",
    "folder_missing",
    "unresolvable",
    "format",
)
"""`ValidationProblem.kind` の閉じた 14 語（`p5r1_notes.md` §5 が正本。FR-046 改＝ D24）。

`format` は値の形式違反（`repos` の URL・絶対パスの形式）のために D24 で追加された
14 番目の語。それ以外の語彙の追加は認めない。
"""

type ProblemKind = Literal[
    "unknown_field",
    "forbidden_key",
    "agent_ref",
    "llm_ref",
    "tool_ref",
    "var_unknown",
    "var_required",
    "forward",
    "orphan",
    "cmd_ref",
    "key_rule",
    "folder_missing",
    "unresolvable",
    "format",
]
"""`PROBLEM_KINDS` の型としての姿。語彙の追加は D24 の `format` まで（FR-046 改）。"""


# --------------------------------------------------------------------------------------
# §2 領域 A のファイル写像（検査を通ってから構築する。R-03）
# --------------------------------------------------------------------------------------


class ProjectFoldersRecord(BaseModel):
    """`projects/<key>/project.yaml` の `folders`（`ProjectYaml` の入れ子）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pj_input_dir: str
    role_dirs: dict[PersonaKey, str] = Field(default_factory=dict[PersonaKey, str])
    outcome_dir: str


class ProjectReposRecord(BaseModel):
    """`projects/<key>/project.yaml` の `repos`（`ProjectYaml` の入れ子。CFG-74）。

    両項目とも任意。未登録のプロジェクトは `repos` キー自体を持たない。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    remote_url: str | None = None
    local_path: str | None = None


class ProjectYaml(BaseModel):
    """`projects/<key>/project.yaml` の写像（DM-01・DM-02）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_key: str
    name: str
    is_reserved: bool = False
    folders: ProjectFoldersRecord
    repos: ProjectReposRecord | None = None


class AgentSpecRecord(BaseModel):
    """`agents.yaml` の 1 ペルソナ分（global・PJ の両方を写す）。

    `role`・`goal`・`backstory` を**任意**にするのは、global（3 つ必須）と PJ（全任意）を
    同じ型で写すためである。必須性は型ではなく検査（`validate.py` の V-04）で見る。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    llm: str | None = None
    tools: list[ToolName] | None = None
    skills: list[str] | None = None
    mcps: list[McpSpec] | None = None
    max_iter: int | None = None
    max_rpm: int | None = None
    max_execution_time: int | None = None


class TaskTemplateRecord(BaseModel):
    """`tasks.yaml` の 1 段分（DM-27）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str
    expected_output: str
    agent: PersonaKey | None = None
    tools: list[ToolName] | None = None


class LlmProfileRecord(BaseModel):
    """`global/llm_profiles.yaml` の `profiles` の 1 件（DM-11）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    provider_kind: ProviderKind
    base_url: str
    model_id: str
    context_window: int
    api_key_ref: str
    extra_headers: dict[str, str] = Field(default_factory=dict[str, str])


class LimitsValuesRecord(BaseModel):
    """`global/limits.yaml` の上限値 1 組（`LimitsRecord` の入れ子）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_requests_per_kickoff: int
    max_cost_per_kickoff: float


class LimitsRecord(BaseModel):
    """`global/limits.yaml` の写像（DM-12）。

    `global_` の末尾アンダースコアは `global` が Python の予約語であるため。YAML のキー名は
    `global` のままで、`alias` で受ける。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    global_: LimitsValuesRecord | None = Field(default=None, alias="global")
    overrides: dict[str, LimitsValuesRecord] = Field(default_factory=dict[str, LimitsValuesRecord])


class CommandDef(BaseModel):
    """`global/commands.yaml` の 1 件（VO。設計 DM-26）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    argv: tuple[str, ...]


class HooksRecord(BaseModel):
    """`projects/<key>/hooks.yaml` の写像（DM-06）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hooks: list[HookRef] = Field(default_factory=list[HookRef])


class HumansRecord(BaseModel):
    """`global/humans.yaml` の写像（P5D-019）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    human_actors: tuple[str, ...] = ()


class PolicySet(BaseModel):
    """`projects/<key>/policy.csv` の行の写し（VO。設計 DM-04）。

    `Enforcer` の構築とは**別経路**である（`Enforcer` はファイルパスから自分で読む）。本型は
    逆方向検査（F19）とコマンド突合（F20）のために行を持つ。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_key: str
    p_rows: tuple[tuple[str, ...], ...] = ()
    g_rows: tuple[tuple[str, ...], ...] = ()


# --------------------------------------------------------------------------------------
# §4 スナップショット・台帳・違反
# --------------------------------------------------------------------------------------


class PolicyEnforcer(Protocol):
    """認可判定器の構造的部分型（casbin `Enforcer` を settings の内側に閉じる）。

    `Enforcer` を**型としても値としてもモジュールの外へ出さない**ため、スナップショットの
    型面にはこの Protocol しか現れない（FR-010・SC-012・contracts/authorizer.md 冒頭）。
    """

    def enforce(self, subject: str, obj: str, act: str) -> bool:
        """3 つ組を判定する。**副作用を持たない**。"""
        ...

    def enable_auto_save(self, auto_save: bool) -> None:
        """自動保存を切り替える（settings は構築直後に `False` を渡す＝ FR-048d）。"""
        ...

    def get_policy(self) -> list[list[str]]:
        """`p` 行を列挙する（列数検査＝ FR-048c と逆方向検査＝ F19 が使う）。"""
        ...

    def get_grouping_policy(self) -> list[list[str]]:
        """`g` 行を列挙する（同上）。"""
        ...


@dataclass(frozen=True, slots=True)
class AreaAData:
    """(d2) が読み切った領域 A の**生の構造**（写像モデルではない入れ物）。

    `validate.py`（検査）と `loader.py`（写像）が同じ値を見るための受け渡しに使う。R-03 の
    「検査 → 構築」の 2 段は、両者がこの 1 つの入れ物を共有することで成立する。

    `profiles_raw`・`commands_raw` は**規則が使う部分木**（`profiles:` / `commands:` の中身）、
    `profiles_file_raw`・`commands_file_raw` は**ファイルの生トップレベル**である。V-06（秘密語）
    は「領域 A に現れたら拒否」（FR-050）なので**後者**を走査する。前者だけを見ると
    `api_key` をファイルのトップレベルに置いた場合に素通りする。他の 11 種は読取時点で
    ファイル全体を運んでいるため、この対はこの 2 ファイルにだけ要る。
    """

    agents_global: Mapping[str, object]
    tasks_global: Mapping[str, object]
    profiles_raw: Mapping[str, object]
    profiles_file_raw: Mapping[str, object]
    default_profile: str | None
    limits_raw: Mapping[str, object] | None
    commands_raw: Mapping[str, object]
    commands_file_raw: Mapping[str, object]
    humans_raw: Mapping[str, object]
    projects_raw: Mapping[str, Mapping[str, object]]
    agents_raw: Mapping[str, Mapping[str, object]]
    tasks_raw: Mapping[str, Mapping[str, object] | None]
    hooks_raw: Mapping[str, Mapping[str, object] | None]
    rules_raw: Mapping[str, str | None]
    policy_rows: Mapping[str, tuple[tuple[str, ...], ...]]


@dataclass(frozen=True, slots=True)
class ValidationProblem:
    """整合検査の違反 1 件（場所・種別・理由）。

    `where` に**絶対パスを載せない**（領域 A ルート相対＋キー）。呼び手＝後半の UI に返る値で
    あるため（vault の FR-060 と同じ理由）。`reason` に**秘密の実値を載せない**（FR-051）。
    """

    where: str
    kind: ProblemKind
    reason: str


@dataclass(frozen=True, slots=True)
class Registry:
    """台帳（DM-07）。**ファイルに保存しない**（FR-037。導出である）。"""

    bd_actors: Mapping[str, str]
    """`bd_actor` → `member_id`。"""

    subjects: Mapping[str, str]
    """`casbin_subject` → `project_key`。**`enforce` の PJ 引き当てはここだけを使う**。"""

    human_actors: frozenset[str]
    """`global/humans.yaml` が宣言した人間の行為者（P5D-019）。"""

    system_actors: frozenset[str]
    """`settings/config.json` の `system_actor`（P5D-037）。"""

    def is_known(self, actor: str) -> bool:
        """3 集合のいずれかに在るか。"""
        return actor in self.bd_actors or actor in self.human_actors or actor in self.system_actors


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """領域 A の全体を畳んだ不変の値（FR-020〜FR-024・R-05）。

    **不変性の範囲は一様でない**（C-3）。`frozen=True` が守るのは属性の再代入だけで、
    `Mapping` は構築時に `MappingProxyType` で包むが、`PolicyEnforcer`（実体は casbin の
    `Enforcer`）は**可変**である（M-12）。担保は `enable_auto_save(False)` と
    「settings が変更系メソッドを呼ばない」ことに依存する（FR-048d）。
    """

    generation: int
    projects: Mapping[str, Project]
    agents_global: Mapping[PersonaKey, AgentSpecRecord]
    agents: Mapping[str, Mapping[PersonaKey, AgentSpecRecord]]
    members: Mapping[str, Mapping[PersonaKey, Member]]
    tasks_global: Mapping[str, TaskTemplateRecord]
    tasks: Mapping[str, Mapping[str, TaskTemplateRecord]]
    registry: Registry
    llm_profiles: Mapping[str, LLMProfile]
    default_profile: str | None
    limits_global: LimitPolicy
    limits_overrides: Mapping[str, LimitPolicy]
    commands: Mapping[str, CommandDef]
    hooks: Mapping[str, HooksConfig]
    rules: Mapping[str, Rules]
    policies: Mapping[str, PolicySet]
    enforcers: Mapping[str, PolicyEnforcer]


# --------------------------------------------------------------------------------------
# §3（後半）保存経路の内部 VO（data-model.md §3）
# --------------------------------------------------------------------------------------


class EffectClass(StrEnum):
    """反映区分（P5D-016・FR-022）。応答ヘッダ `X-Settings-Effect` の値になる。

    ⚠ **`next_kickoff` のアンダースコアは内部表現**である。ヘッダに載る綴りは
    **`next-kickoff`（ハイフン）**で、写像は `router/__init__.py` の**1 箇所にだけ**置く
    （010 SD-18）。画面・テスト・契約のどこにも第 2 の写像を作らない。
    """

    immediate = "immediate"
    next_kickoff = "next_kickoff"


@dataclass(frozen=True, slots=True)
class FileWrite:
    """1 ファイル分の書込（領域 A ルート相対のパスと**直列化済みの本文**）。

    本文の直列化は呼び手（`service/{projects,members,ai_config}.py`）が済ませてある。
    """

    rel: str
    text: str


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """1 回の保存が触るファイルの全体（data-model §3）。

    不変条件（**呼び手が守る**。裁定 2026-08-27 で 2 件を改めた）:

    - `writes` の `rel` は互いに重複しない（同じファイルを 2 度書かない）。
    - **`archive` が非 `None` のとき、`writes` は `global/limits.yaml` の 1 件に限る**
      （plan **D-k**・FR-044）。PJ 解除は退避（rename）に加えて**上限の上書きから当該キーを
      落とす**必要がある —— 上限の上書きだけが**プロジェクトのディレクトリの外**にあるため
      退避では消えず、残すと口からは二度と消せず（`DELETE /settings/limits/{k}` は未登録 PJ に
      404）、起動時検査にも受け皿が無く（**V-27 は値域だけ**）、同じキーで再登録すると上限だけが
      復活する。
    - `removals` の要素は `writes` の `rel` と重複しない。
    - **`new_project` と `archive` は同時に非 `None` にならない**（登録と解除は別の保存）。
    - **`new_project` が非 `None` のとき、`writes` の `rel` はすべて `projects/<new_project>/`
      の下にある**（FR-033 の 3 ファイル）。
    """

    writes: tuple[FileWrite, ...] = ()
    removals: tuple[str, ...] = ()
    archive: str | None = None
    new_project: str | None = None


@dataclass(frozen=True, slots=True)
class SaveOutcome:
    """保存経路の戻り。router が応答ヘッダへ写す（data-model §8）。"""

    generation: int
    effect: EffectClass
