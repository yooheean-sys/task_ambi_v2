"""盤面の 2 道具（executor-F07。IF-02／IF-17）。

**`close`・起票・依存張り・ラベル操作の道具は作らない**（FR-043・SC-016）。

`BoardUnavailable`・`IssueNotFound` は**どちらも**理由の文言に翻訳し、「これ以上の作業はせず
`escalate_to_human` で人に相談してください」と**人へ相談する道具を名指しする**（FR-044。AI が
別経路を取れないため）。**文面は 2 つで分ける**（C3 裁定）。
"""

from typing import Any

from pydantic import BaseModel, Field

from common.interfaces.board import BoardUnavailable, IssueNotFound
from common.kernel.enums import ToolName
from executor.tools.base import GuardedTool, ToolContext

ESCALATE_SUFFIX = "これ以上の作業はせず escalate_to_human で人に相談してください。"
"""盤面に届かない・課題が無いときの結び（命令形で人へ相談する道具を名指しする）。"""


class _NoArgs(BaseModel):
    """引数を持たない道具の入力。"""


class _NoteArg(BaseModel):
    """進捗コメントの入力。"""

    note: str = Field(description="盤面に残す進捗の一言")


class ReadIssueTool(GuardedTool):
    """担当の課題を読む（**`issue_id` は指示書が束縛する**。AI に選ばせない）。"""

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        try:
            issue = self.ctx.deps.board_reader.show(self.ctx.issue_id)
        except IssueNotFound:
            return f"ERROR: 課題 '{self.ctx.issue_id}' が盤面に無い。{ESCALATE_SUFFIX}"
        except BoardUnavailable:
            return f"ERROR: 盤面に接続できない。{ESCALATE_SUFFIX}"
        labels = ", ".join(issue.labels) or "(なし)"
        return (
            f"# {issue.title}\n\n{issue.description}\n\n"
            f"## 受け入れ条件\n{issue.acceptance}\n\n"
            f"## ラベル\n{labels}"
        )


class RecordProgressTool(GuardedTool):
    """進捗を盤面に 1 行残す（**成功時のみ** `progress_notes` を加算＝ FR-044a）。"""

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        note = str(kwargs.get("note", ""))
        try:
            self.ctx.deps.board_writer.comment(
                self.ctx.issue_id, note, actor=self.ctx.member.bd_actor
            )
        except IssueNotFound:
            return f"ERROR: 課題 '{self.ctx.issue_id}' が盤面に無い。{ESCALATE_SUFFIX}"
        except BoardUnavailable:
            return f"ERROR: 盤面に接続できない。{ESCALATE_SUFFIX}"
        self.ctx.session.count_progress_note()
        return "進捗を盤面に記録した。"


def build_read_issue(ctx: ToolContext) -> GuardedTool:
    """`read_issue` を組む。"""
    return ReadIssueTool(
        name=ToolName.read_issue.value,
        description="担当している課題の本文・受け入れ条件・ラベルを読む。引数は無い。",
        args_schema=_NoArgs,
        ctx=ctx,
        tool_name=ToolName.read_issue,
    )


def build_record_progress(ctx: ToolContext) -> GuardedTool:
    """`record_progress` を組む。"""
    return RecordProgressTool(
        name=ToolName.record_progress.value,
        description="進捗を盤面に 1 行残す。完了の申告には使わない（complete_issue を使う）。",
        args_schema=_NoteArg,
        ctx=ctx,
        tool_name=ToolName.record_progress,
    )
