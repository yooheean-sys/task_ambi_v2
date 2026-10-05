"""orchestrator が提供する契約（IF-49）。

1 回の kickoff の実行。呼び手は trigger。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/orchestrator/p5_orchestrator_data_model.mmd` の
`namespace common_interfaces_orchestrator_py`。

`ClaimFilter`（board）・`Grade`（monitor）は Pydantic のフィールド型なので実行時 import する。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from common.interfaces.board import ClaimFilter
from common.interfaces.monitor import Grade
from common.kernel.enums import PersonaKey, StageName
from common.kernel.errors import ViolationError


class LaunchKind(StrEnum):
    """起動種別。"""

    human = "human"
    timer = "timer"
    file = "file"
    manual = "manual"


class KickoffStatus(StrEnum):
    """kickoff の結果状態。"""

    ok = "ok"
    noop = "noop"
    stopped = "stopped"
    busy = "busy"


class TriggerSnapshot(BaseModel):
    """起動元トリガの値コピー（DM-22）。"""

    model_config = ConfigDict(frozen=True)

    trigger_id: str
    name: str
    type: str


class ExecutionContext(BaseModel):
    """1 回の kickoff の実行文脈（DM-19）。"""

    model_config = ConfigDict(frozen=True)

    kind: LaunchKind
    trigger_snapshot: TriggerSnapshot | None
    dropped_path: str | None
    claim_filter: ClaimFilter
    requested_at: datetime


class IssueRunSummary(BaseModel):
    """1 課題の処理結果。"""

    model_config = ConfigDict(frozen=True)

    issue_id: str
    stage: StageName
    persona: PersonaKey
    result: Literal["closed", "released", "failing", "escalated", "unsafe", "halted"]
    grade: Grade | None
    returns: int


class KickoffResult(BaseModel):
    """kickoff の要約（DM-18）。"""

    model_config = ConfigDict(frozen=True)

    kickoff_id: str
    status: KickoffStatus
    issues: list[IssueRunSummary]
    notes: list[str]
    started_at: datetime
    finished_at: datetime


class KickoffBusy(ViolationError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_orchestrator_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """別の kickoff が実行中（single-flight）。"""

    current_kickoff_id: str

    def __init__(
        self, message: str, current_kickoff_id: str, cause: BaseException | None = None
    ) -> None:
        """実行中の kickoff_id を保持する。"""
        super().__init__(message, cause)
        self.current_kickoff_id = current_kickoff_id


class KickoffRunner(Protocol):
    """1 回の kickoff（IF-49）。同期（ブロッキング）。スレッド化は呼び側の責務。"""

    def run(self, ctx: ExecutionContext) -> KickoffResult:
        """実行文脈を受けて kickoff を 1 回まわす。

        Raises:
            KickoffBusy: 別の kickoff が実行中。
        """
        ...
