"""生存確認 `/health`（contracts/health.md）。正本は `docs/design/phase6/openapi.yaml`。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class Health(BaseModel):
    """生存応答（`openapi.yaml` の `components.schemas.Health`）。

    `status` に**既定値を付けない**（付けると生成スキーマの `required` から落ち、設計側の
    `required` 4 件と食い違う。research R-09 の実測）。
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    app: str
    env_label: str
    modules: list[str]


def create_health_router(*, app_name: str, env_label: str, modules: tuple[str, ...]) -> APIRouter:
    """生存確認の経路 1 本だけを持つ router を返す。

    値は組み立て時に確定した 3 つを閉包で捕まえる。**外部ツールに触れない**（FR-016）・
    **設定を読み直さない**（FR-027）・**`app.state` を使わない**（research R-09）。

    Args:
        app_name: ルート設定のアプリ名（CFG-14）。
        env_label: ルート設定の環境ラベル（CFG-15）。
        modules: 実際に組み立てられたモジュール名（依存順。FR-017）。

    Returns:
        `GET /health`（`operation_id="getHealth"`・`tags=["health"]`）を持つ `APIRouter`。
    """
    router = APIRouter()
    response = Health(status="ok", app=app_name, env_label=env_label, modules=list(modules))

    async def get_health() -> Health:
        """組み立て時に確定した値をそのまま返す（外部ツールにも設定にも触れない）。"""
        return response

    # デコレータではなく明示登録にするのは、束ねた局所関数が参照されないまま残ると
    # pyright strict の reportUnusedFunction に触れるため（振る舞いは同じ）。
    router.add_api_route(
        "/health",
        get_health,
        methods=["GET"],
        operation_id="getHealth",
        tags=["health"],
        response_model=Health,
    )
    return router
