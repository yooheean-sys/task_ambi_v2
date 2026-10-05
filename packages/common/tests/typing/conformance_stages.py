# pyright: reportUnnecessaryTypeIgnoreComment=true
"""SC-010: `StageCatalog`・`ProposalGate`。

`build_prompt` の 6 引数の**引数名違い**を不適合ケースに含める（P5D-047 の裁定）。
"""

from __future__ import annotations

from common.interfaces.settings import Member, Rules, TaskTemplates
from common.interfaces.stages import (
    ContextRefs,
    GateResult,
    PromptBundle,
    Proposal,
    ProposalGate,
    StageCatalog,
    StageSpec,
)


class _CatalogOk:
    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec: ...
    def build_prompt(
        self,
        spec: StageSpec,
        member: Member,
        rules: Rules,
        templates: TaskTemplates,
        ctx: ContextRefs,
        feedback: str | None,
    ) -> PromptBundle: ...


class _CatalogMissingBuildPrompt:
    """`build_prompt` を欠く。"""

    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec: ...


class _CatalogRenamedArg:
    """`build_prompt` の引数名が違う（`ctx` ではなく `context`）。"""

    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec: ...
    def build_prompt(
        self,
        spec: StageSpec,
        member: Member,
        rules: Rules,
        templates: TaskTemplates,
        context: ContextRefs,
        feedback: str | None,
    ) -> PromptBundle: ...


catalog_ok: StageCatalog = _CatalogOk()
catalog_missing: StageCatalog = _CatalogMissingBuildPrompt()  # type: ignore
catalog_renamed: StageCatalog = _CatalogRenamedArg()  # type: ignore


class _GateOk:
    def validate_proposal(
        self, proposal: Proposal, spec: StageSpec, existing_children: list[str]
    ) -> GateResult: ...
    def extract_outcome_tokens(self, acceptance: str) -> list[str]: ...
    def extract_repo_tokens(self, acceptance: str) -> list[str]: ...


class _GateMissingExtract:
    """`extract_outcome_tokens` を欠く。"""

    def validate_proposal(
        self, proposal: Proposal, spec: StageSpec, existing_children: list[str]
    ) -> GateResult: ...


class _GateRenamedArg:
    """`validate_proposal` の引数名が違う（`spec` ではなく `stage_spec`）。"""

    def validate_proposal(
        self, proposal: Proposal, stage_spec: StageSpec, existing_children: list[str]
    ) -> GateResult: ...
    def extract_outcome_tokens(self, acceptance: str) -> list[str]: ...
    def extract_repo_tokens(self, acceptance: str) -> list[str]: ...


gate_ok: ProposalGate = _GateOk()
gate_missing: ProposalGate = _GateMissingExtract()  # type: ignore
gate_renamed: ProposalGate = _GateRenamedArg()  # type: ignore
