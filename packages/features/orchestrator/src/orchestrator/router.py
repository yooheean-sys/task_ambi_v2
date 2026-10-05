"""HTTP の 3 本の口（orchestrator-F13）。正本は `docs/design/phase6/openapi.yaml`。

**`prefix` を付けない**（openapi のパスがそのまま `/kickoffs` で始まる）。経路の登録は
`router.add_api_route(...)` の**明示登録**にする —— デコレータで束ねた局所関数が参照されない
まま残ると pyright strict の `reportUnusedFunction` に触れる（`api/health.py` の先例）。

**例外の翻訳は口の内側で閉じる**（contracts/kickoff-http.md §6）: `KickoffBusy` → 409、
記録が無い → 404。ほかを素通しさせない。周回の例外は既に 202 を返したあとなのでここへは届かない。

**本ファイルだけ `from __future__ import annotations` を置かない** —— 口の依存注入は
`Annotated[OrchestratorService, Depends(provider)]` で表すが、`provider` は
`create_kickoff_router()` の**局所変数**である。注釈を文字列にすると FastAPI は module の
globals だけで解決を試み、局所名を見つけられずに `runner` を素のクエリ引数と誤読する
（実測: `GET /kickoffs/{id}` が 422 を返す）。
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from common.interfaces.board import ClaimFilter
from common.interfaces.orchestrator import ExecutionContext, KickoffBusy, LaunchKind
from common.kernel.log import CorrelationContext
from orchestrator.dependencies import service_provider
from orchestrator.models import KickoffRecord
from orchestrator.schemas import (
    CurrentKickoffResponse,
    KickoffAccepted,
    KickoffBusyDetail,
    KickoffNotFoundDetail,
    KickoffRequest,
    KickoffStatusResponse,
)
from orchestrator.service import OrchestratorService

TAGS: list[str | Enum] = ["kickoffs"]
"""openapi の `tags`（3 本とも同じ）。"""


def create_kickoff_router(service: OrchestratorService) -> APIRouter:
    """`/kickoffs` の 3 本を持つ `APIRouter` を返す。

    Args:
        service: 3 本の口が共有する周回サービス（`build()` が組んだもの）。

    Returns:
        `POST /kickoffs`・`GET /kickoffs/{kickoff_id}`・`GET /kickoffs/current` を持つ router。
    """
    router = APIRouter()
    provider = service_provider(service)

    def create_kickoff(
        runner: Annotated[OrchestratorService, Depends(provider)],
        body: KickoffRequest | None = None,
    ) -> KickoffAccepted | JSONResponse:
        """Human 起動を受け付け、**待たせずに**識別子を返す（FR-073・SC-004）。"""
        correlation_id = CorrelationContext.current()  # 入口の値を読む（束縛はしない）
        ctx = ExecutionContext(
            kind=LaunchKind.human,
            trigger_snapshot=None,
            dropped_path=None,
            claim_filter=_claim_filter(body),
            requested_at=datetime.now(UTC),
        )
        try:
            kickoff_id = runner.start_background(ctx, correlation_id)
        except KickoffBusy as exc:
            # 409 の本文は `Detail` を**平らに**拡張した形（`HTTPException` だと入れ子になる）
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=KickoffBusyDetail(
                    detail=exc.message, current_kickoff_id=exc.current_kickoff_id
                ).model_dump(),
            )
        return KickoffAccepted(kickoff_id=kickoff_id, status="running")

    def get_kickoff(
        kickoff_id: str, runner: Annotated[OrchestratorService, Depends(provider)]
    ) -> KickoffStatusResponse:
        """直近 N 件の記録から 1 件返す。知らない・こぼれた識別子は 404（FR-075）。"""
        record = runner.record(kickoff_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"kickoff の記録が無い: {kickoff_id}",
            )
        return _view(record)

    def get_current_kickoff(
        runner: Annotated[OrchestratorService, Depends(provider)],
    ) -> CurrentKickoffResponse:
        """走っていなくても **200 ＋ `running=false`** で返す（FR-076・SC-032）。"""
        record = runner.current_record()
        if record is None:
            return CurrentKickoffResponse(running=False, record=None)
        return CurrentKickoffResponse(running=True, record=_view(record))

    router.add_api_route(
        "/kickoffs",
        create_kickoff,
        methods=["POST"],
        operation_id="createKickoff",
        tags=TAGS,
        status_code=status.HTTP_202_ACCEPTED,
        response_model=KickoffAccepted,
        responses={status.HTTP_409_CONFLICT: {"model": KickoffBusyDetail}},
    )
    router.add_api_route(
        "/kickoffs/current",
        get_current_kickoff,
        methods=["GET"],
        operation_id="getCurrentKickoff",
        tags=TAGS,
        response_model=CurrentKickoffResponse,
    )
    router.add_api_route(
        "/kickoffs/{kickoff_id}",
        get_kickoff,
        methods=["GET"],
        operation_id="getKickoff",
        tags=TAGS,
        response_model=KickoffStatusResponse,
        responses={
            status.HTTP_404_NOT_FOUND: {
                "model": KickoffNotFoundDetail,  # 本文スキーマまで宣言する（正本 `Detail`・T086）
                "description": "記録が無い",
            }
        },
    )
    return router


def _claim_filter(body: KickoffRequest | None) -> ClaimFilter:
    """要求の指定、無ければ既定の確保条件を組む（FR-078・R-10）。

    既定は「ラベルは実行担当の 1 つだけ（型の既定のまま）・プロジェクトの限定なし」。
    **`project_key` は共有側の型に既定が無い必須項目なので明示する**。
    """
    if body is not None and body.claim_filter is not None:
        return body.claim_filter.to_filter()
    return ClaimFilter(project_key=None)


def _view(record: KickoffRecord) -> KickoffStatusResponse:
    """記録を応答の形へ写す（共有側の型はそのまま入れ子にする）。"""
    return KickoffStatusResponse(
        kickoff_id=record.kickoff_id,
        status=record.status,
        ctx=record.ctx,
        result=record.result,
    )
