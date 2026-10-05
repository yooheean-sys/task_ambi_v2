"""monitor が提供する契約（IF-38〜43）。

1 kickoff の計測・等級付け・盤面への写像と、LLM HTTP の前後フック。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/monitor/p5_monitor_data_model.mmd` の
`namespace common_interfaces_monitor_py`。

**`httpx` と `CrewOutcome` は `TYPE_CHECKING` でのみ import する**（FR-036・DIFF-08・R-04・R-06）。
`LimitPolicy`（settings）は実行時 import（data-model.md §12 の実行時グラフ）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from common.interfaces.settings import LimitPolicy
from common.kernel.errors import UnverifiedError

if TYPE_CHECKING:
    import httpx

    from common.interfaces.executor import CrewOutcome


class Grade(StrEnum):
    """等級（DM-21）。"""

    F = "F"
    W = "W"
    I = "I"  # noqa: E741  # 等級 I は DM-21／monitor 設計の正本語彙


class FailureKind(StrEnum):
    """失敗の種類。"""

    api_exhausted = "api_exhausted"
    no_output = "no_output"
    guardrail_limit = "guardrail_limit"
    ctx_exceeded = "ctx_exceeded"
    limit_requests = "limit_requests"
    limit_cost = "limit_cost"
    truncated_answer = "truncated_answer"
    finish_length = "finish_length"
    structured_conversion = "structured_conversion"
    retry_succeeded = "retry_succeeded"
    usage_missing = "usage_missing"
    summarization = "summarization"


class FailureEvent(BaseModel):
    """失敗の 1 件（DM-21）。"""

    model_config = ConfigDict(frozen=True)

    grade: Grade
    kind: FailureKind
    evidence: dict[str, str | int | float]
    at: datetime


class Measurement(BaseModel):
    """1 kickoff の計測（DM-20）。"""

    model_config = ConfigDict(frozen=True)

    kickoff_id: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cost_missing_count: int
    max_tokens_before_send: int
    halted_reason: FailureKind | None
    started_at: datetime
    finished_at: datetime
    unattributed_requests: int


class RunVerdict(BaseModel):
    """`finish` の戻り。"""

    model_config = ConfigDict(frozen=True)

    grade: Grade | None
    kinds: list[FailureKind]
    halted: bool
    closed: bool
    envelope: str
    measurement: Measurement
    failures: list[FailureEvent]


class RunHalted(BaseException):
    """上限超過による強制停止（`BaseException` 直系＝階層外。Z-6）。

    `AppError` を継承しないため `message`・`cause` を持たない。送出側と捕捉側の
    間にあるライブラリの `except Exception` を素通りする。
    """

    kind: FailureKind
    measured: float
    limit: float
    request_no: int

    def __init__(self, kind: FailureKind, measured: float, limit: float, request_no: int) -> None:
        """超過した種類・実測値・上限・何回目の要求かを保持する。"""
        super().__init__(kind, measured, limit, request_no)
        self.kind = kind
        self.measured = measured
        self.limit = limit
        self.request_no = request_no


class MappingUnverified(UnverifiedError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_monitor_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """盤面への写像を確認読取で確かめられない（P3）。"""

    issue_id: str
    expected: str
    actual: str

    def __init__(
        self,
        message: str,
        issue_id: str,
        expected: str,
        actual: str,
        cause: BaseException | None = None,
    ) -> None:
        """期待した状態と読めた状態を保持する（R-16）。"""
        super().__init__(message, cause)
        self.issue_id = issue_id
        self.expected = expected
        self.actual = actual


class RunMonitor(Protocol):
    """1 kickoff の計測と写像（IF-38・IF-39）。"""

    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold_t: int) -> None:
        """計測を開始し、以後の Hook 呼び出しをこの kickoff に帰属させる。"""
        ...

    def finish(
        self, kickoff_id: str, issue_id: str, actor: str, outcome: CrewOutcome
    ) -> RunVerdict:
        """計測を締め、等級を付け、F／W は盤面へ写像して確認読取まで行う。

        Raises:
            MappingUnverified: 写像が読めない／`bd` に届かない。
        """
        ...


class LLMHttpHook(Protocol):
    """LLM HTTP の前後フック（IF-40〜43）。非同期は後ろ 2 件のみ。"""

    def on_outbound(self, request: httpx.Request) -> httpx.Request:
        """送信前にトークン・回数・コストを判定する。

        Raises:
            RunHalted: 上限を超過した。
        """
        ...

    def on_inbound(self, response: httpx.Response) -> httpx.Response:
        """応答の usage・finish_reason を観測する。"""
        ...

    async def aon_outbound(self, request: httpx.Request) -> httpx.Request:
        """`on_outbound` の非同期版。

        Raises:
            RunHalted: 上限を超過した。
        """
        ...

    async def aon_inbound(self, response: httpx.Response) -> httpx.Response:
        """`on_inbound` の非同期版。"""
        ...
