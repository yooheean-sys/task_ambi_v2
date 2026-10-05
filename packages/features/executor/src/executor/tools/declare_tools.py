"""申告の 4 道具（executor-F08）。**契約を 1 つも呼ばない**。

4 つとも `RunSession` に**積むだけ**で、盤面に 1 行も書かない（FR-049・SC-016）。
起票・close・依存張りは呼び手（orchestrator）の責務である。

**同じ申告を 2 回呼んだら最後が勝つ**（FR-053a）。追記ではなく代入である。
"""

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from common.interfaces.executor import Escalation, ReviewVerdict
from common.interfaces.stages import Proposal
from common.kernel.enums import ToolName
from executor.tools.base import GuardedTool, ToolContext

GUIDANCE_TEMPLATE = "検証に落ちました。次の点を直して {tool} を呼び直してください: {problems}"
"""形が壊れているときの文言（**例外にしない**＝ FR-050）。"""


class _ProposalArgs(BaseModel):
    """分解提案の入力（手引きは `bundle.proposal_schema`）。"""

    items: list[Any] = Field(description="分解した子課題の一覧")


class _ReasonArg(BaseModel):
    """完了申告の入力。"""

    reason: str = Field(description="完了と判断した理由")


class _EscalationArgs(BaseModel):
    """人への相談の入力。"""

    what_i_tried: str = Field(description="試したこと")
    why_stuck: str = Field(description="行き詰まった理由")
    decision_needed: str = Field(description="人に決めてほしいこと")


class _ReviewArgs(BaseModel):
    """レビュー判定の入力。"""

    approved: bool = Field(description="承認するか")
    feedback: str = Field(description="所見")
    files_read: list[str] = Field(description="読んだファイル名")


class ProposeBreakdownTool(GuardedTool):
    """分解提案を持ち帰る。

    **形の合否を判定しない**（FR-050）。受理できる形かどうかだけを見て、壊れていれば
    指摘の文言を返す（提案の良し悪しは stages の `ProposalGate` が判定する）。
    """

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        items = kwargs.get("items", [])
        try:
            proposal = Proposal.model_validate({"items": items})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            return GUIDANCE_TEMPLATE.format(
                tool=ToolName.propose_breakdown.value, problems=problems
            )
        self.ctx.session.set_proposal(proposal)
        return f"分解提案を {len(proposal.items)} 件で受け取った。"


class CompleteIssueTool(GuardedTool):
    """完了を申告する（**完了させない**＝ FR-051。close は呼び手）。"""

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        self.ctx.session.set_completion(str(kwargs.get("reason", "")))
        return "完了の申告を受け取った。close は呼び手が判断する。"


class EscalateToHumanTool(GuardedTool):
    """人へ相談する（**起票しない**＝ FR-052）。"""

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        self.ctx.session.set_escalation(
            Escalation(
                what_i_tried=str(kwargs.get("what_i_tried", "")),
                why_stuck=str(kwargs.get("why_stuck", "")),
                decision_needed=str(kwargs.get("decision_needed", "")),
            )
        )
        return "人への相談を受け取った。"


class SubmitReviewTool(GuardedTool):
    """レビュー判定を持ち帰る（FR-053）。"""

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        files_read = kwargs.get("files_read", [])
        self.ctx.session.set_review(
            ReviewVerdict(
                approved=bool(kwargs.get("approved", False)),
                feedback=str(kwargs.get("feedback", "")),
                files_read=[str(name) for name in files_read],
            )
        )
        return "レビュー判定を受け取った。"


def build_propose_breakdown(ctx: ToolContext) -> GuardedTool:
    """`propose_breakdown` を組む。"""
    schema = ctx.bundle.proposal_schema or "（手引きの指定なし）"
    return ProposeBreakdownTool(
        name=ToolName.propose_breakdown.value,
        description=f"課題の分解案を申告する。形の手引き: {schema}",
        args_schema=_ProposalArgs,
        ctx=ctx,
        tool_name=ToolName.propose_breakdown,
    )


def build_complete_issue(ctx: ToolContext) -> GuardedTool:
    """`complete_issue` を組む。"""
    return CompleteIssueTool(
        name=ToolName.complete_issue.value,
        description="やり切ったと判断したときに申告する。close はこの道具では起きない。",
        args_schema=_ReasonArg,
        ctx=ctx,
        tool_name=ToolName.complete_issue,
    )


def build_escalate_to_human(ctx: ToolContext) -> GuardedTool:
    """`escalate_to_human` を組む。"""
    return EscalateToHumanTool(
        name=ToolName.escalate_to_human.value,
        description="行き詰まったときに人へ相談する。起票はこの道具では起きない。",
        args_schema=_EscalationArgs,
        ctx=ctx,
        tool_name=ToolName.escalate_to_human,
    )


def build_submit_review(ctx: ToolContext) -> GuardedTool:
    """`submit_review` を組む。"""
    return SubmitReviewTool(
        name=ToolName.submit_review.value,
        description="レビューの判定を申告する。",
        args_schema=_ReviewArgs,
        ctx=ctx,
        tool_name=ToolName.submit_review,
    )
