"""FastAPI `Depends` 用 provider（`SettingsCrudService` の差し替え点を 1 箇所に閉じる）。

**口は起動時に組み立て済みのサービスを共有する**（FR-082。要求ごとに設定を読み直さない）。
テストは `app.dependency_overrides[provider]` で差し替える（FR-080・D-5）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from settings.service.crud import SettingsCrudService


def service_provider(service: SettingsCrudService) -> Callable[[], SettingsCrudService]:
    """`Depends` に渡す provider を作る。

    Args:
        service: 38 の口が共有する CRUD サービス。

    Returns:
        `service` をそのまま返す呼び出し可能。
    """

    def _provide() -> SettingsCrudService:
        return service

    return _provide
