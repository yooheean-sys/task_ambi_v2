"""HTTP 境界の DTO（`/kickoffs` の 3 本）。正本は `docs/design/phase6/openapi.yaml`。

**応答は共有側の型（`ExecutionContext`・`KickoffResult`）をそのまま入れ子にする**
（写し直すと `openapi.yaml` の必須項目との二重管理になる）。

**要求だけは別の DTO を置く**（`ClaimFilterRequest`）—— 二重管理を避ける理屈は応答側の話で
あり、要求側は「正本どおりの本文が通るか」が判定基準だからである。共有側の `ClaimFilter` を
そのまま入れ子にすると生成スキーマの必須項目が正本と**逆転する**（B-34・T080 の実測）。

**`status`・`running` に既定値を付けない** —— 付けると生成スキーマの `required` から落ち、
設計側の `required` と食い違う（`apps/api/src/api/health.py` が明記する実測の罠）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from common.interfaces.board import ClaimFilter
from common.interfaces.orchestrator import ExecutionContext, KickoffResult
from common.kernel.enums import Labels
from orchestrator.models import RecordStatus


class ClaimFilterRequest(BaseModel):
    """`POST /kickoffs` の `claim_filter`（**要求側だけの DTO**。正本の `ClaimFilter`）。

    正本は `required: [labels]`・`project_key: { type: [string, "null"] }`（＝任意）である。
    共有側の `ClaimFilter` は `labels` に既定を持ち `project_key` に既定を持たないため、その
    まま入れ子にすると生成スキーマの `required` が `['project_key']` になり、**正本どおりの
    本文が 422 になる**（B-34・T080 の実測）。`packages/common/` は FR-002 で凍結、
    `docs/design/phase6/` は plan (b) で変更禁止なので、**要求側だけ**ここで既定を合わせる。
    """

    model_config = ConfigDict(frozen=True)

    labels: frozenset[str] = frozenset({Labels.role_executor()})
    project_key: str | None = None

    def to_filter(self) -> ClaimFilter:
        """共有側の `ClaimFilter`（モジュール間の受け渡し型）へ写す。"""
        return ClaimFilter(labels=self.labels, project_key=self.project_key)


class KickoffRequest(BaseModel):
    """`POST /kickoffs` の本文（**省略可**）。"""

    model_config = ConfigDict(frozen=True)

    claim_filter: ClaimFilterRequest | None = None


class KickoffAccepted(BaseModel):
    """202（受付。結果は `GET` で参照する）。"""

    model_config = ConfigDict(frozen=True)

    kickoff_id: str
    status: Literal["running"]


class KickoffNotFoundDetail(BaseModel):
    """404（正本の `Detail`。`required: [detail]`）。

    `KickoffBusyDetail` は `current_kickoff_id` を持つので流用できない。**名前をモジュールで
    修飾する**のは、土台が全モジュールの router を 1 つの `FastAPI` へ取り込むためである。
    """

    model_config = ConfigDict(frozen=True)

    detail: str


class KickoffBusyDetail(BaseModel):
    """409（`Detail` ＋ 進行中の識別子）。"""

    model_config = ConfigDict(frozen=True)

    detail: str
    current_kickoff_id: str


class KickoffStatusResponse(BaseModel):
    """`GET /kickoffs/{kickoff_id}`（openapi の `KickoffRecordView`）。"""

    model_config = ConfigDict(frozen=True)

    kickoff_id: str
    status: RecordStatus
    ctx: ExecutionContext
    result: KickoffResult | None = None


class CurrentKickoffResponse(BaseModel):
    """`GET /kickoffs/current`（openapi の `CurrentKickoff`）。"""

    model_config = ConfigDict(frozen=True)

    running: bool
    record: KickoffStatusResponse | None = None
