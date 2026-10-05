"""stages が提供する契約（IF-44〜47）。

段の解決・プロンプト組立・分解提案の検査・完了トークン抽出。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/stages/p5_stages_data_model.mmd` の `namespace common_interfaces_stages_py`。

`Member`・`Rules`・`TaskTemplates` は Protocol の注釈にしか使わないため
`TYPE_CHECKING` ブロックで import する（R-06 の 2 分規則。実行時グラフは stages → kernel のみ）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from common.kernel.enums import PersonaKey, StageName, ToolName
from common.kernel.errors import NotFoundError

if TYPE_CHECKING:
    from common.interfaces.settings import Member, Rules, TaskTemplates


class PromptPurpose(StrEnum):
    """プロンプトの用途。"""

    work = "work"
    review = "review"


class StageSpec(BaseModel):
    """段の構造属性（DM-08）。

    `require_labels` は**接頭辞込み**のラベル文字列（`Labels.kind(...)` で組む形）。
    """

    model_config = ConfigDict(frozen=True)

    name: StageName
    mode: Literal["propose", "execute"]
    require_labels: set[str]
    require_no_parent: bool | None
    child_labels: list[str]
    allowed_personas: set[PersonaKey]
    min_items: int = 1
    max_acceptance: int | None
    allow_depends_on: bool
    require_outcome_path: bool


class ContextRefs(BaseModel):
    """`build_prompt` の材料。"""

    model_config = ConfigDict(frozen=True)

    issue_id: str
    title: str
    description: str
    acceptance: str
    parent_title: str | None
    parent_acceptance: str | None
    human_guidance: list[str]
    input_refs: list[str]
    existing_children: list[str]
    purpose: PromptPurpose
    is_final_review: bool
    repository_remote_url: str | None = None
    """プロジェクトのリモートリポジトリ URL（CFG-74。未登録なら None）。"""
    repository_local_path: str | None = None
    """プロジェクトのローカルリポジトリの OS 絶対パス（CFG-74。未登録なら None）。"""


class PromptBundle(BaseModel):
    """Agent ＋ Task の材料（DM-08/27）。"""

    model_config = ConfigDict(frozen=True)

    persona_key: PersonaKey
    role: str
    goal: str
    backstory: str
    task_description: str
    expected_output: str
    task_tools: list[ToolName] | None
    purpose: PromptPurpose
    proposal_schema: str | None = None


class ProposalItem(BaseModel):
    """分解提案の 1 件。`depends_on` は提案内 index（前方のみ）。"""

    model_config = ConfigDict(frozen=True)

    title: str
    summary: str
    description: str
    acceptance: list[str]
    depends_on: list[int]
    persona: PersonaKey | None


class Proposal(BaseModel):
    """分解提案一式。"""

    model_config = ConfigDict(frozen=True)

    items: list[ProposalItem]


class GateResult(BaseModel):
    """提案検査の結果。"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    problems: list[str]
    observations: list[str]
    order: list[int]


class UnknownStage(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_stages_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """`kind` ラベルの組から段を決められない。"""


class UnknownPersona(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_stages_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """段が許すペルソナに当てはまらない。"""


class StageCatalog(Protocol):
    """段の解決とプロンプト組立（IF-44・IF-45）。"""

    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec:
        """`kind` ラベルの組と親の有無から段を決める。

        Args:
            kind_labels: issue のラベル文字列を**接頭辞込み**（`"kind:child"` の形）で
                渡す。値だけ（`"child"`）を渡すと段表の `require_labels` 照合（部分集合）
                と宣言 kind の事前検査（SD-41）の両方が沈黙で素通りする（B-12）。
            has_parent: 親 issue があるか。

        Raises:
            UnknownStage: 段を決められない。
        """
        ...

    def build_prompt(
        self,
        spec: StageSpec,
        member: Member,
        rules: Rules,
        templates: TaskTemplates,
        ctx: ContextRefs,
        feedback: str | None,
    ) -> PromptBundle:
        """段テンプレートとメンバ本文からプロンプトを組む。

        Raises:
            UnknownPersona: 段が許すペルソナに当てはまらない。
        """
        ...


class ProposalGate(Protocol):
    """分解提案の検査と完了トークン抽出（IF-46・IF-47）。"""

    def validate_proposal(
        self, proposal: Proposal, spec: StageSpec, existing_children: list[str]
    ) -> GateResult:
        """提案を段の規則に照らして検査する。"""
        ...

    def extract_outcome_tokens(self, acceptance: str) -> list[str]:
        """受入条文から完了ゲート鎖の入力となるトークンを抜く。"""
        ...

    def extract_repo_tokens(self, acceptance: str) -> list[str]:
        """受入条文からリポジトリの成果物（`repo/<パス>`）のトークンを抜く（D25-b）。"""
        ...
