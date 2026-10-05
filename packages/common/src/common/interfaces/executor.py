"""executor が提供する契約（IF-48）。

CrewAI での 1 回の実行。crewai の型は一切持たない（`CrewEvent.detail` は `dict[str, str]`）。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/executor/p5_executor_data_model.mmd` の
`namespace common_interfaces_executor_py`。

参照する 4 モジュールの型はいずれも **Pydantic のフィールド型**なので実行時 import する
（R-06 の 2 分規則）。本ファイルは型検査時だけの import ブロックを持たない
（循環は monitor 側が `CrewOutcome` を型検査時参照に回すことで断つ＝DIFF-08）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from common.interfaces.monitor import FailureKind
from common.interfaces.settings import HooksConfig, Member, ProjectFolders, ProjectRepos
from common.interfaces.stages import PromptBundle, PromptPurpose, Proposal
from common.interfaces.vault import WrittenPath
from common.kernel.errors import ViolationError


class CrewEventKind(StrEnum):
    """中立型イベントの種類。"""

    llm_call_started = "llm_call_started"
    llm_call_completed = "llm_call_completed"
    llm_call_failed = "llm_call_failed"
    tool_called = "tool_called"
    tool_denied = "tool_denied"
    tool_failed = "tool_failed"
    guardrail_failed = "guardrail_failed"
    agent_retry = "agent_retry"
    summarization = "summarization"
    outcome_overwritten = "outcome_overwritten"
    crew_completed = "crew_completed"
    crew_failed = "crew_failed"


class CrewEvent(BaseModel):
    """中立型イベント 1 件。crewai の型を持たない。"""

    model_config = ConfigDict(frozen=True)

    kind: CrewEventKind
    at: datetime
    detail: dict[str, str]


class Escalation(BaseModel):
    """`escalate_to_human` の申告。"""

    model_config = ConfigDict(frozen=True)

    what_i_tried: str
    why_stuck: str
    decision_needed: str


class ReviewVerdict(BaseModel):
    """`submit_review` の申告。"""

    model_config = ConfigDict(frozen=True)

    approved: bool
    feedback: str
    files_read: list[str]


class CrewSpec(BaseModel):
    """`run` の入力。呼び手が詰める。"""

    model_config = ConfigDict(frozen=True)

    issue_id: str
    mode: Literal["propose", "execute"]
    purpose: PromptPurpose
    bundle: PromptBundle
    member: Member
    hooks: HooksConfig
    folders: ProjectFolders
    parent_acceptance: str | None
    repos: ProjectRepos | None = None


class CrewOutcome(BaseModel):
    """`run` の戻り。"""

    model_config = ConfigDict(frozen=True)

    proposal: Proposal | None
    task_output: str | None
    completed: bool
    completion_reason: str
    review: ReviewVerdict | None
    escalation: Escalation | None
    written: list[WrittenPath]
    events: list[CrewEvent]
    halted: bool
    halted_reason: FailureKind | None
    progress_notes: int


class ExecutorFailure(ViolationError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_executor_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """CrewAI の組立／実行が失敗した（crewai 例外の翻訳）。"""

    phase: Literal["build", "kickoff"]
    reason: str

    def __init__(
        self,
        message: str,
        phase: Literal["build", "kickoff"],
        reason: str,
        cause: BaseException | None = None,
    ) -> None:
        """どの段で失敗したかと、crewai 例外の文字列化を保持する。"""
        super().__init__(message, cause)
        self.phase = phase
        self.reason = reason


class CrewExecutor(Protocol):
    """CrewAI での 1 回の実行（IF-48）。"""

    def run(self, spec: CrewSpec) -> CrewOutcome:
        """Crew を組み立てて実行し、中立型の結果を返す。

        Raises:
            ExecutorFailure: 組立／実行が失敗した。
        """
        ...
