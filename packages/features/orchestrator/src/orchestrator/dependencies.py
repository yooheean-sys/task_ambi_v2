"""FastAPI `Depends` 用 provider（`OrchestratorService` の差し替え点を 1 箇所に閉じる）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from orchestrator.service import OrchestratorService


def service_provider(service: OrchestratorService) -> Callable[[], OrchestratorService]:
    """`Depends` に渡す provider を作る。

    Args:
        service: 3 本の口が共有する周回サービス。

    Returns:
        `service` をそのまま返す呼び出し可能（テストは
        `app.dependency_overrides[provider]` で差し替えられる）。
    """

    def _provide() -> OrchestratorService:
        return service

    return _provide
