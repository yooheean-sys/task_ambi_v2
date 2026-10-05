"""monitor の内部 VO と検出規則の表（fn を持たない）。

正本は `docs/design/phase5/monitor/p5_monitor_data_model.mmd` の `namespace monitor_internal`。
`RunState`（進行中の可変器）・`RawFailure`（等級を持たない失敗）・`Envelope`（封筒）・
`DetectionRule`／`EVENT_RULES`（`events` 観測点の表）を置く。

**`FailureEvent` をここで組み立てない**。等級を付ける箇所は `service.py` の `_grade_of()` 1 つだけで
（FR-043・SC-007）、`RawFailure` はそこへ渡すまでの器である。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from common.interfaces.monitor import FailureEvent, FailureKind, Grade, Measurement

if TYPE_CHECKING:
    from common.interfaces.settings import LimitPolicy

__all__ = [
    "EVENT_RULES",
    "DetectionRule",
    "DetectionSource",
    "Envelope",
    "RawFailure",
    "RunState",
]


@dataclass(frozen=True, slots=True)
class RawFailure:
    """等級を持たない失敗の 1 件（本 feature で新設。SD-18）。

    `FailureEvent.grade` は省略不可（F-03）なので、Hook が `FailureEvent` を組む形にすると
    その場で等級を決めざるを得ない。`RawFailure` を挟むことで `FailureEvent` を組む時点が
    `finish` の 1 箇所に絞られる（FR-043・SC-007）。

    `evidence` の値型は `FailureEvent.evidence` と同一にする（等級付けでそのまま移せる）。
    """

    kind: FailureKind
    evidence: dict[str, str | int | float]
    at: datetime


@dataclass(slots=True)
class RunState:
    """進行中の 1 kickoff（**可変**）。

    `begin` から `finish` まで生き、同時に 1 つだけ存在する（P5D-021）。
    **`lock` を持たない** —— 差し替え（`attach`／`detach`）と中身の更新は同じ不変条件を守るので、
    `HttpHook._lock` の 1 本で守る（SD-19・plan (iii) 5）。
    **`unattributed` も持たない** —— 帰属先が無いときの数なので `HttpHook` 側が持つ。
    """

    kickoff_id: str
    limits: LimitPolicy
    threshold_t: int
    correlation_id: str | None
    started_at: datetime
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_missing_count: int = 0
    max_tokens_before_send: int = 0
    halted_reason: FailureKind | None = None
    failures: list[RawFailure] = field(default_factory=list[RawFailure])


@dataclass(frozen=True, slots=True)
class Envelope:
    """盤面へ載せる封筒（P5D-023）。書式は contracts/verdict-mapping.md §4。"""

    marker: str
    grade: Grade
    kinds: tuple[FailureKind, ...]
    kickoff_id: str
    correlation_id: str
    measurement: Measurement
    failures: tuple[FailureEvent, ...]

    def render(self) -> str:
        """封筒の全文（複数行）を組む（contracts/verdict-mapping.md §4）。

        Returns:
            1 行目＝印と等級と種類／2 行目＝`kickoff_id`／3 行目＝`correlation_id`／
            4 行目＝計測の数値／5 行目以降＝失敗 1 件につき 1 行。
        """
        measurement = self.measurement
        lines = [
            self.summary(),
            f"kickoff_id={self.kickoff_id}",
            f"correlation_id={self.correlation_id}",
            " ".join(
                (
                    f"requests={measurement.requests}",
                    f"prompt_tokens={measurement.prompt_tokens}",
                    f"completion_tokens={measurement.completion_tokens}",
                    f"cost_usd={measurement.cost_usd}",
                    f"cost_missing={measurement.cost_missing_count}",
                    f"max_tokens_before_send={measurement.max_tokens_before_send}",
                    f"unattributed={measurement.unattributed_requests}",
                )
            ),
        ]
        lines.extend(
            f"- {failure.kind.value}: "
            + " ".join(f"{key}={value}" for key, value in failure.evidence.items())
            for failure in self.failures
        )
        return "\n".join(lines)

    def summary(self) -> str:
        """封筒の 1 行目だけを返す（W 級の `close(reason=...)` に渡す）。

        Returns:
            `<印> grade=<G> kinds=<k1,k2,...>`（種類が空なら `kinds=-`）。
        """
        kinds = ",".join(kind.value for kind in self.kinds) or "-"
        return f"{self.marker} grade={self.grade.value} kinds={kinds}"


class DetectionSource(StrEnum):
    """検出の観測点（data-model.md §2.4）。"""

    outbound = "outbound"
    inbound = "inbound"
    events = "events"
    proposal = "proposal"
    task_output = "task_output"
    halted = "halted"


@dataclass(frozen=True, slots=True)
class DetectionRule:
    """検出規則の 1 行（`events` 観測点の表として使う）。"""

    source: DetectionSource
    match: str
    kind: FailureKind
    requires_incomplete: bool


EVENT_RULES: tuple[DetectionRule, ...] = (
    DetectionRule(DetectionSource.events, "agent_retry", FailureKind.api_exhausted, True),
    DetectionRule(DetectionSource.events, "llm_call_failed", FailureKind.api_exhausted, True),
    DetectionRule(DetectionSource.events, "guardrail_failed", FailureKind.guardrail_limit, True),
    DetectionRule(DetectionSource.events, "agent_retry", FailureKind.retry_succeeded, False),
    DetectionRule(DetectionSource.events, "summarization", FailureKind.summarization, False),
)
"""`CrewEventKind` → `FailureKind` の表（contracts/verdict-mapping.md §2.2）。

**1 つの出来事に当たる最初の 1 行だけ**を採る。`requires_incomplete=True` の行を先に並べる
ことで、`agent_retry` が「完了していない → `api_exhausted`／完了している → `retry_succeeded`」に
分かれる（`requires_incomplete=False` は「条件なし」の意味なので、先に絞り込みの行を置かないと
両方が当たってしまう）。

**ここに載らない `CrewEventKind` は積まない**（受け皿が無いので種類を捏造しない＝ FR-038b）:
`llm_call_started`・`llm_call_completed`・`tool_called`・`tool_denied`・`tool_failed`・
`outcome_overwritten`・`crew_completed`・`crew_failed`（`crew_failed` は「完了していない」の
判定にだけ使う）。
"""
