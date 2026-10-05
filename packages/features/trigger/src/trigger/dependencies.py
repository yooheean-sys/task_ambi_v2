"""FastAPI `Depends` 用 provider（`TriggerService` の差し替え点を **1 箇所**に閉じる）。

**口は起動時に組み立て済みのサービスを共有する**（要求ごとに設定を読み直さない）。
テストは `app.dependency_overrides[provider]` で差し替える（orchestrator・settings の先例）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from trigger.service import TriggerService


def service_provider(service: TriggerService) -> Callable[[], TriggerService]:
    """`Depends` に渡す provider を作る。

    Args:
        service: 10 の口が共有する常駐サービス。

    Returns:
        `service` をそのまま返す呼び出し可能。
    """

    def _provide() -> TriggerService:
        return service

    return _provide
