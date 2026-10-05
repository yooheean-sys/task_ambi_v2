"""F11 HTTP の口（**6 パス・10 操作**）。正本は `docs/design/phase6/openapi.yaml`。

**`prefix` を付けない**（正本のパスがそのまま `/triggers`・`/history`・`/watcher`・`/hooks/file`）。
経路の登録は `router.add_api_route(...)` の**明示登録**にする —— デコレータで束ねた局所関数が
参照されないまま残ると pyright strict の `reportUnusedFunction` に触れる（`api/health.py` の先例）。

**例外 → 応答の翻訳をこの 1 箇所に閉じる**（contracts/trigger-http.md §3）:

| 例外 | HTTP | 本文 |
|---|---|---|
| `TriggerNotFound` | **404** | `Detail{detail}` |
| `TriggerValidationError` | **422** | `Problem{problems: [{where, kind, reason}]}` |

`TriggerStartupError` 系は口に届かない（`build()`／`lifespan` の中でしか出ない＝起動中断）。

⚠ **本ファイルに `from __future__ import annotations` を置かない**（`010` F-20 の実測）——
`Annotated[..., Depends(局所 provider)]` の `provider` は `create_trigger_router()` の**局所変数**
なので、注釈を文字列にすると FastAPI が module の globals だけで解決を試みて失敗する。
"""

from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from trigger.dependencies import service_provider
from trigger.exceptions import TriggerNotFound, TriggerValidationError
from trigger.schemas import (
    DetailBody,
    FileHookAcceptedBody,
    FileHookRequestBody,
    HistoryRowBody,
    ProblemBody,
    RunAcceptedBody,
    TriggerSpecBody,
    TriggerToggleBody,
    TriggerViewBody,
    WatcherViewBody,
)
from trigger.service import TriggerService, views

TAGS: list[str | Enum] = ["triggers"]
"""openapi の `tags`（10 操作とも同じ）。"""

MONTH_PATTERN = "^[0-9]{6}$"
"""`GET /history` の `month`（**任意**。省略時は当月）。書式違反は FastAPI が 422 を返す。"""

_NOT_FOUND: dict[int | str, dict[str, Any]] = {status.HTTP_404_NOT_FOUND: {"model": DetailBody}}
_PROBLEM: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemBody}
}


def create_trigger_router(service: TriggerService) -> APIRouter:  # noqa: C901, PLR0915  # 10 操作を 1 箇所に束ねる（分けると翻訳が散る）
    """6 パス・10 操作を持つ `APIRouter` を返す。

    Args:
        service: 10 の口が共有する常駐サービス（`build()` が組んだもの）。

    Returns:
        `/triggers`（5 操作）・`/triggers/{id}/run`・`/history`・`/watcher`・`/hooks/file` を
        持つ router。
    """
    router = APIRouter()
    provider = service_provider(service)

    def list_triggers(
        runner: Annotated[TriggerService, Depends(provider)],
    ) -> list[TriggerViewBody]:
        """一覧（宣言 ＋ `next_fire_at` ＋ `last_result`）＝ FR-063。"""
        return runner.list_triggers()

    def create_trigger(
        body: TriggerSpecBody, runner: Annotated[TriggerService, Depends(provider)]
    ) -> TriggerViewBody | JSONResponse:
        """追加（`validate → 領域 B 書込 → add_job → watcher 開始`＝ FR-028）。"""
        try:
            return runner.create_trigger(body)
        except TriggerValidationError as exc:
            return _problem(exc)

    def get_trigger(
        trigger_id: str, runner: Annotated[TriggerService, Depends(provider)]
    ) -> TriggerViewBody:
        """1 件（不明 id は 404）。"""
        try:
            return runner.get_trigger(trigger_id)
        except TriggerNotFound as exc:
            raise _not_found(exc) from exc

    def update_trigger(
        trigger_id: str,
        body: TriggerSpecBody,
        runner: Annotated[TriggerService, Depends(provider)],
    ) -> TriggerViewBody | JSONResponse:
        """全量の編集（既定トリガーの照合＝ SD-12）。"""
        try:
            return runner.update_trigger(trigger_id, body)
        except TriggerNotFound as exc:
            raise _not_found(exc) from exc
        except TriggerValidationError as exc:
            return _problem(exc)

    def toggle_trigger(
        trigger_id: str,
        body: TriggerToggleBody,
        runner: Annotated[TriggerService, Depends(provider)],
    ) -> TriggerViewBody:
        """有効／無効の切替（**既定トリガーもトグルできる**。422 を持たない）。"""
        try:
            return runner.toggle_trigger(trigger_id, enabled=body.enabled)
        except TriggerNotFound as exc:
            raise _not_found(exc) from exc

    def delete_trigger(
        trigger_id: str, runner: Annotated[TriggerService, Depends(provider)]
    ) -> Response:
        """削除（**204・本文なし**。既定トリガーは 422＝ V-7）。"""
        try:
            runner.delete_trigger(trigger_id)
        except TriggerNotFound as exc:
            raise _not_found(exc) from exc
        except TriggerValidationError as exc:
            return _problem(exc)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def run_trigger(
        trigger_id: str, runner: Annotated[TriggerService, Depends(provider)]
    ) -> RunAcceptedBody:
        """手動実行（**待たせずに** `run_id` を返す＝ FR-049）。"""
        try:
            return RunAcceptedBody(run_id=runner.run_manual(trigger_id))
        except TriggerNotFound as exc:
            raise _not_found(exc) from exc

    def list_history(
        runner: Annotated[TriggerService, Depends(provider)],
        month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    ) -> list[HistoryRowBody]:
        """履歴（**表示のためだけ**に読む＝ FR-058）。無い月は**空配列で 200**（SD-13）。

        ⚠ **404 は返さない**（`responses=` に宣言だけする＝ SD-22）。`TriggerNotFound` を
        この経路から投げる分岐は作らない。
        """
        return [views.to_history_body(row) for row in runner.read_history(month)]

    def get_watcher(
        runner: Annotated[TriggerService, Depends(provider)],
    ) -> WatcherViewBody:
        """インメモリ状態の **4 欄**（この口以外から読めない＝ FR-064）。"""
        return runner.watcher_state()

    def post_file_hook(
        body: FileHookRequestBody, runner: Annotated[TriggerService, Depends(provider)]
    ) -> FileHookAcceptedBody | JSONResponse:
        """退避経路（監視対象の外は 422＝ FR-041）。"""
        try:
            matched = runner.accept_file_hook(body.path)
        except TriggerValidationError as exc:
            return _problem(exc)
        return FileHookAcceptedBody(accepted=True, matched_trigger_ids=matched)

    router.add_api_route(
        "/triggers",
        list_triggers,
        methods=["GET"],
        operation_id="listTriggers",
        tags=TAGS,
        response_model=list[TriggerViewBody],
    )
    router.add_api_route(
        "/triggers",
        create_trigger,
        methods=["POST"],
        operation_id="createTrigger",
        tags=TAGS,
        status_code=status.HTTP_201_CREATED,
        response_model=TriggerViewBody,
        responses=_PROBLEM,
    )
    router.add_api_route(
        "/triggers/{trigger_id}",
        get_trigger,
        methods=["GET"],
        operation_id="getTrigger",
        tags=TAGS,
        response_model=TriggerViewBody,
        responses=_NOT_FOUND,
    )
    router.add_api_route(
        "/triggers/{trigger_id}",
        update_trigger,
        methods=["PUT"],
        operation_id="updateTrigger",
        tags=TAGS,
        response_model=TriggerViewBody,
        responses={**_NOT_FOUND, **_PROBLEM},
    )
    router.add_api_route(
        "/triggers/{trigger_id}",
        toggle_trigger,
        methods=["PATCH"],
        operation_id="toggleTrigger",
        tags=TAGS,
        response_model=TriggerViewBody,
        responses=_NOT_FOUND,
    )
    router.add_api_route(
        "/triggers/{trigger_id}",
        delete_trigger,
        methods=["DELETE"],
        operation_id="deleteTrigger",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        responses={**_NOT_FOUND, **_PROBLEM},
    )
    router.add_api_route(
        "/triggers/{trigger_id}/run",
        run_trigger,
        methods=["POST"],
        operation_id="runTrigger",
        tags=TAGS,
        status_code=status.HTTP_202_ACCEPTED,
        response_model=RunAcceptedBody,
        responses=_NOT_FOUND,
    )
    router.add_api_route(
        "/history",
        list_history,
        methods=["GET"],
        operation_id="listHistory",
        tags=TAGS,
        response_model=list[HistoryRowBody],
        # SD-22: **宣言だけの応答コードはここ 1 つに限る**。部分集合の照合は逆向きに効かない
        # ので、正本の `{200, 404}` に対して生成側が 404 を持たないと SC-012 が落ちる。
        responses=_NOT_FOUND,
    )
    router.add_api_route(
        "/watcher",
        get_watcher,
        methods=["GET"],
        operation_id="getWatcher",
        tags=TAGS,
        response_model=WatcherViewBody,
    )
    router.add_api_route(
        "/hooks/file",
        post_file_hook,
        methods=["POST"],
        operation_id="postFileHook",
        tags=TAGS,
        status_code=status.HTTP_202_ACCEPTED,
        response_model=FileHookAcceptedBody,
        responses=_PROBLEM,
    )
    return router


def _not_found(exc: TriggerNotFound) -> HTTPException:
    """`TriggerNotFound` → **404** ＋ `Detail`（翻訳はこの 1 箇所だけ）。"""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _problem(exc: TriggerValidationError) -> JSONResponse:
    """`TriggerValidationError` → **422** ＋ `Problem`（**一覧**をそのまま載せる）。"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ProblemBody(problems=exc.problems).model_dump(),
    )
