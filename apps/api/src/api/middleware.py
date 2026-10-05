"""相関 ID の純 ASGI ミドルウェア（contracts/correlation-id.md §3・research R-07）。

`BaseHTTPMiddleware` は使わない（`call_next` の内側を別タスクで動かす癖を持ち込まない）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.correlation import normalize_correlation_id
from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTTP_SCOPE_TYPE = "http"
RESPONSE_START = "http.response.start"


class CorrelationIdMiddleware:
    """1 要求につき相関 ID を 1 つ決め、束縛し、同名ヘッダで応答に返す（FR-019〜023）。"""

    def __init__(self, app: ASGIApp, *, header_name: str) -> None:
        """ヘッダ名は呼び手（`create_app`）が CFG-18 から渡す。**直書きしない**（FR-022）。

        Args:
            app: 内側の ASGI アプリ。
            header_name: 読み書きする相関 ID ヘッダの名前（`RootConfig.log.correlation_header`）。
        """
        self._app = app
        self.header_name = header_name
        self._raw_name = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """HTTP 要求のみを対象に相関 ID を束縛し、応答ヘッダへ同じ値を足す。

        Args:
            scope: ASGI スコープ。`scope["type"] != "http"` は素通しする。
            receive: ASGI receive 呼び出し可能。
            send: ASGI send 呼び出し可能。
        """
        if scope["type"] != HTTP_SCOPE_TYPE:
            await self._app(scope, receive, send)
            return

        correlation_id = normalize_correlation_id(self._incoming(scope))
        # 正規化を通した値は制御文字を含まず 128 文字以下なので、必ず latin-1 で再符号化できる
        # （Starlette は受信ヘッダを latin-1 で復号する。research R-07）。
        encoded = correlation_id.encode("latin-1")

        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == RESPONSE_START:
                headers: list[tuple[bytes, bytes]] = message.setdefault("headers", [])
                headers.append((self._raw_name, encoded))
            await send(message)

        token = CorrelationContext.bind(correlation_id)
        try:
            await self._app(scope, receive, send_with_correlation_id)
        finally:
            CorrelationContext.reset(token)

    def _incoming(self, scope: Scope) -> str | None:
        """受信ヘッダから相関 ID の生値を読む。

        Args:
            scope: ASGI スコープ。

        Returns:
            該当ヘッダの値（latin-1 復号済み）。無ければ `None`。
        """
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        for key, value in raw_headers:
            if key.lower() == self._raw_name:
                return value.decode("latin-1")
        return None
