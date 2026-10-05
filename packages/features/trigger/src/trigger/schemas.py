"""HTTP 境界の DTO（data-model §3）。**正本は `docs/design/phase6/openapi.yaml` の components**。

`openapi.yaml` の trigger 側 components は **8 個**（`TriggerSpec`・`TriggerView`・`TriggerToggle`・
`RunAccepted`・`HistoryRow`・`WatcherView`・`FileHookRequest`・`FileHookAccepted`）で、これに
`LaunchCond` と エラー本文 3 種（`Detail`・`ValidationProblem`・`Problem`）を足した
**12 型**を置く。

⚠ **`settings` から import しない**（feature 間 import は禁止＝ FR-015・SD-15）。`Detail`・
`ValidationProblem`・`Problem` は settings と同型だが、**同型の DTO を trigger 側に自前で持つ**。

⚠ **`kind` は `trigger_spec` の 1 語しか無い**（`openapi.yaml` の `ValidationProblem.kind` の
enum）。したがって「どの規則で拒否したか」は `where` と `reason` の文字列でしか区別できない
（SD-15。`010` SD-03 と同型の非対称。**記録のみ**）。

**振る舞いを持たせない**（憲章 §7-2）。写像は `service/views.py` と `router.py` が行う。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # pydantic のフィールド型なので実行時に要る
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VALIDATION_KIND = "trigger_spec"
"""`ValidationProblem.kind` の唯一の値（`openapi.yaml` の enum に trigger 用は 1 語だけ）。"""


class LaunchCondBody(BaseModel):
    """`LaunchCond`（起動条件）。既定は `["role:executor"]`（D06-h）。"""

    model_config = ConfigDict(frozen=True)

    labels: list[str] = Field(default_factory=lambda: ["role:executor"])
    project_key: str | None = None


class TriggerSpecBody(BaseModel):
    """`TriggerSpec`。**`name`・`type`・`spec`・`condition`・`enabled` が必須**。"""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    type: Literal["cron", "interval", "file"]
    spec: str
    condition: LaunchCondBody
    enabled: bool


class HistoryRowBody(BaseModel):
    """`HistoryRow`（15 欄）。必須は 7 つ、残り 8 つは nullable。"""

    model_config = ConfigDict(frozen=True)

    run_id: str
    kickoff_id: str | None = None
    trigger_id: str
    trigger_name: str
    trigger_type: Literal["cron", "interval", "file"]
    kind: Literal["timer", "file", "manual"]
    started_at: datetime
    finished_at: datetime | None = None
    duration_sec: float | None = None
    status: Literal["started", "ok", "noop", "stopped", "busy"]
    issues_ok: int | None = None
    issues_failing: int | None = None
    notes: str | None = None
    dropped_path: str | None = None
    correlation_id: str


class TriggerViewBody(TriggerSpecBody):
    """`TriggerView`。

    `TriggerSpec` ＋ 4 欄（`trigger_id`・`is_default`・`next_fire_at`・`last_result`）。
    """

    trigger_id: str
    is_default: bool
    next_fire_at: datetime | None = None
    """scheduler の `job.next_run_time`（**インメモリ**）。未登録なら `null`。"""

    last_result: HistoryRowBody | None = None
    """履歴 csv の直近 1 行（**表示のためだけ**に読む＝ FR-058・H-8）。"""


class TriggerToggleBody(BaseModel):
    """`TriggerToggle`（有効／無効の切替）。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool


class RunAcceptedBody(BaseModel):
    """`RunAccepted`（手動実行の受付。`run_id` は trigger が発番＝ SD-14）。"""

    model_config = ConfigDict(frozen=True)

    run_id: str


class WatcherEntryBody(BaseModel):
    """`WatcherView.watchers[]` の 1 件。"""

    model_config = ConfigDict(frozen=True)

    trigger_id: str
    watch_dir: str
    alive: bool


class WatcherViewBody(BaseModel):
    """`WatcherView`（**4 欄**）。必須は `scheduler_running`・`watchers`・`running`。"""

    model_config = ConfigDict(frozen=True)

    scheduler_running: bool
    watchers: list[WatcherEntryBody]
    next_fire_at: dict[str, datetime] = Field(default_factory=dict[str, datetime])
    running: bool


class FileHookRequestBody(BaseModel):
    """`FileHookRequest`（退避経路の投下通知。P5D-042・EIF-12）。"""

    model_config = ConfigDict(frozen=True)

    path: str
    source: Literal["hook"] = "hook"


class FileHookAcceptedBody(BaseModel):
    """`FileHookAccepted`（受付と一致したトリガー）。"""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    matched_trigger_ids: list[str] = Field(default_factory=list[str])


class DetailBody(BaseModel):
    """`Detail`（**404** の本文）。"""

    model_config = ConfigDict(frozen=True)

    detail: str


class ValidationProblemItem(BaseModel):
    """`ValidationProblem`（**422** の 1 件）。`kind` は `trigger_spec` の 1 語だけ（SD-15）。"""

    model_config = ConfigDict(frozen=True)

    where: str
    kind: Literal["trigger_spec"] = VALIDATION_KIND
    reason: str


class ProblemBody(BaseModel):
    """`Problem`（**422** の本文）。**一覧**である（最初の 1 件で打ち切らない）。"""

    model_config = ConfigDict(frozen=True)

    problems: list[ValidationProblemItem]
