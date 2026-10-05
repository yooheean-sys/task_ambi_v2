"""入口アプリ生成関数 `create_app`（contracts/composition-root.md §5）。

`build_modules` の上に「ログ基盤の設定・FastAPI の生成・相関 ID ミドルウェア・生存確認の
取り込み・lifespan」を積む。**待受は開始しない**（`uvicorn.run` は `main.py` の責務）。
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from api.config import load_root_config
from api.health import create_health_router
from api.middleware import CorrelationIdMiddleware
from api.wiring import build_modules
from common.kernel.log import LoggerFactory

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from api.wiring import LifespanFactory, ModuleRegistration, ProtocolOverrides
    from common.kernel.config import RootConfig


def create_app(
    *,
    enabled_modules: Sequence[str] | None = None,
    overrides: ProtocolOverrides | None = None,
    config: RootConfig | None = None,
    registrations: Sequence[ModuleRegistration] | None = None,
) -> FastAPI:
    """全モジュールを組み立てた FastAPI アプリを返す（contracts/composition-root.md §5）。

    Args:
        enabled_modules: 有効化するモジュール名。`None` ならルート設定の `enabled_modules`。
        overrides: 契約の型 → 差し替える実装。`None` なら空写像。
        config: ルート設定。`None` なら `load_root_config()` で読む（**1 回だけ**。FR-027）。
        registrations: 登録集合。`None` なら `registry.MODULE_REGISTRATIONS`。

    Returns:
        生存確認と各モジュールの経路を持つ `FastAPI` インスタンス。待受は開始していない。

    Raises:
        ConfigurationError: ルート設定の不備（`load_root_config()` 由来）。
        WiringError: 配線の不備（`build_modules()` 由来。詳細は同関数）。
    """
    resolved_config = config if config is not None else load_root_config()
    LoggerFactory.configure(resolved_config)
    wiring = build_modules(
        enabled_modules=enabled_modules,
        overrides=overrides,
        config=resolved_config,
        registrations=registrations,
    )

    app = FastAPI(title=resolved_config.app_name, lifespan=_make_lifespan(wiring.lifespans))
    app.add_middleware(CorrelationIdMiddleware, header_name=resolved_config.log.correlation_header)
    app.include_router(
        create_health_router(
            app_name=resolved_config.app_name,
            env_label=resolved_config.env_label,
            modules=wiring.modules,
        )
    )
    for router in wiring.routers:
        app.include_router(router)
    return app


def _make_lifespan(
    lifespans: tuple[LifespanFactory, ...],
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """組み立てられた後始末だけを `AsyncExitStack` に積む lifespan を作る（contracts §5-7）。

    積む順は組み立て順、終了は積んだ逆順（`AsyncExitStack` の保証）。
    **override 写像の値には触れない**（FR-043。そもそも `Wiring.lifespans` に入らない）。

    Args:
        lifespans: 組み立てられたモジュールが持つ後始末（依存順）。

    Returns:
        FastAPI に渡す lifespan。
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            for factory in lifespans:
                await stack.enter_async_context(factory())
            yield

    return lifespan
