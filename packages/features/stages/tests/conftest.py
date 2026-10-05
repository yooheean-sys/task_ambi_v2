"""stages のテスト共通ヘルパ（組み立てだけ。スタブを 1 つも作らない）。

stages は `StagesDeps` が空の純粋 MOD であり、偽装すべき依存が存在しない（FR-014）。
本ファイルに置くのは「契約の受け渡し型を最小の形で組む」ヘルパと「`StagesService` を
既定値で組む」ヘルパだけである。

**`build()` に依存させない**（US6 が未完でも US1〜US4 のテストが回る）。
**他 feature を import しない**・**`packages/features/settings/defaults/` を読まない**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.settings import (
    LLMProfile,
    Member,
    ProviderKind,
    Rules,
    TaskTemplate,
    TaskTemplates,
)
from common.interfaces.stages import (
    ContextRefs,
    PromptPurpose,
    Proposal,
    ProposalItem,
    StageSpec,
)
from common.kernel.enums import PersonaKey, StageName, ToolName
from stages import models
from stages.models import AcceptanceRules
from stages.service import StagesService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def stage_spec(name: StageName) -> StageSpec:
    """段表から 1 段を取り出す（`models.STAGE_ROWS` が唯一の出所）。"""
    for row in models.STAGE_ROWS:
        if row.spec.name == name:
            return row.spec
    msg = f"段表に {name} が無い"
    raise LookupError(msg)


def make_acceptance_rules(
    *,
    placeholders: frozenset[str] | None = None,
    min_len: int = 10,
    max_acceptance_default: int | None = None,
) -> AcceptanceRules:
    """受入条文の閾値を組む（既定は `models` のコード既定に合わせる）。"""
    return AcceptanceRules(
        placeholders=models.DEFAULT_PLACEHOLDERS if placeholders is None else placeholders,
        min_len=min_len,
        max_acceptance_default=max_acceptance_default,
    )


def make_service(
    *,
    rows: tuple[models.StageRow, ...] | None = None,
    rules: AcceptanceRules | None = None,
) -> StagesService:
    """`StagesService` を既定値で組む（`build()` を通さない）。"""
    return StagesService(
        rows=models.STAGE_ROWS if rows is None else rows,
        rules=make_acceptance_rules() if rules is None else rules,
    )


def make_llm_profile() -> LLMProfile:
    """最小の LLM プロファイル（stages は中身を見ない）。"""
    return LLMProfile(
        name="p",
        provider_kind=ProviderKind.openai_compatible,
        base_url="http://localhost:1",
        model_id="m",
        context_window=1000,
        api_key_ref="K",
        threshold_t=1,
    )


def make_member(
    *,
    persona_key: PersonaKey = PersonaKey.planner,
    role: str = "役割",
    goal: str = "目標",
    backstory: str = "背景",
    project_key: str = "pj",
) -> Member:
    """最小のメンバ 1 件。"""
    return Member(
        member_id=f"{project_key}.{persona_key}",
        project_key=project_key,
        persona_key=persona_key,
        role=role,
        goal=goal,
        backstory=backstory,
        llm_profile=make_llm_profile(),
        tools=[],
        skills=[],
        mcps=[],
        bd_actor="actor",
        casbin_subject=f"member:{project_key}.{persona_key}",
    )


def make_project_rules(project_rules: str = "共通ルール") -> Rules:
    """プロジェクト共通ルール（`build_prompt` の `rules` 引数）。"""
    return Rules(project_rules=project_rules)


def make_templates(
    templates: Mapping[str, TaskTemplate] | None = None,
    *,
    generation: int = 1,
) -> TaskTemplates:
    """段テンプレート一式。既定は 7 キーすべてを最小の本文で置く。"""
    if templates is None:
        templates = {
            key: make_template(description=f"{key} の本文", expected_output=f"{key} の成果")
            for key in (
                "program",
                "project",
                "subrequest",
                "child",
                "plain",
                "review_normal",
                "review_final",
            )
        }
    return TaskTemplates(generation=generation, templates=dict(templates))


def make_template(
    *,
    description: str = "本文",
    expected_output: str = "成果",
    tools: list[ToolName] | None = None,
) -> TaskTemplate:
    """段テンプレート 1 段。"""
    return TaskTemplate(description=description, expected_output=expected_output, tools=tools)


def make_ctx(
    *,
    issue_id: str = "i-1",
    title: str = "題名",
    description: str = "説明",
    acceptance: str = "受入",
    parent_title: str | None = None,
    parent_acceptance: str | None = None,
    human_guidance: Sequence[str] = (),
    input_refs: Sequence[str] = (),
    existing_children: Sequence[str] = (),
    purpose: PromptPurpose = PromptPurpose.work,
    is_final_review: bool = False,
    repository_remote_url: str | None = None,
    repository_local_path: str | None = None,
) -> ContextRefs:
    """`build_prompt` の材料。"""
    return ContextRefs(
        issue_id=issue_id,
        title=title,
        description=description,
        acceptance=acceptance,
        parent_title=parent_title,
        parent_acceptance=parent_acceptance,
        human_guidance=list(human_guidance),
        input_refs=list(input_refs),
        existing_children=list(existing_children),
        purpose=purpose,
        is_final_review=is_final_review,
        repository_remote_url=repository_remote_url,
        repository_local_path=repository_local_path,
    )


def make_item(
    *,
    title: str = "子の題名",
    summary: str = "子の要約です",
    description: str = "子の説明です",
    acceptance: Sequence[str] | None = None,
    depends_on: Sequence[int] = (),
    persona: PersonaKey | None = None,
) -> ProposalItem:
    """分解提案の 1 件（既定はどの検査にも掛からない健全な値）。"""
    return ProposalItem(
        title=title,
        summary=summary,
        description=description,
        acceptance=["- [ ] outcome/child.md に結果を書く"]
        if acceptance is None
        else list(acceptance),
        depends_on=list(depends_on),
        persona=persona,
    )


def make_proposal(items: Sequence[ProposalItem]) -> Proposal:
    """分解提案一式。"""
    return Proposal(items=list(items))
