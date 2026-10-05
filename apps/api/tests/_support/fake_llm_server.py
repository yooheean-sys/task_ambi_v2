"""偽 LLM サーバ（OpenAI 互換・ループバック HTTP・台本駆動）。FR-027〜029・SC-006。

標準ライブラリの `http.server.ThreadingHTTPServer` を pytest プロセス内のデーモンスレッドで
起動し、`("127.0.0.1", 0)` に bind して**ポートを OS に採番させる**（待受ポートの衝突が原理的に
起きない）。追加の実行時依存を 1 つも増やさない（憲章 §8）。

httpx の `MockTransport` で代替しない: monitor の `LLMHttpHook`（IF-40〜43）は httpx の
**transport 層**の差し込み点であり、MockTransport はそこを置き換えてしまう。実 HTTP 往復で
なければ観測系の検証が成立しない（FR-028・D14-b）。

契約は specs/000-workspace-foundation/contracts/fake-llm-server.md。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

CHAT_COMPLETIONS_PATH = "/chat/completions"


class FakeLlmServer:
    """台本どおりに OpenAI 互換の応答を返すループバック HTTP サーバ。

    Args:
        script: 1 要素＝1 往復。使い切ったら最終要素を繰り返す。各要素のキーは
            `finish_reason`（必須）・`usage`（必須）・`content`・`tool_calls`・`status`。

    Attributes:
        base_url: `http://127.0.0.1:<OS 採番ポート>/v1`。
        requests: 受信した要求本文の一覧（検証用）。
        call_count: 応答した往復の回数。
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        if not script:
            raise ValueError("台本は 1 要素以上必要（1 要素＝1 往復）")
        self._script = script
        self.requests: list[dict[str, Any]] = []
        self.call_count = 0

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        host, port = self._server.server_address[0], self._server.server_address[1]
        self.base_url = f"http://{host!s}:{port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        """待受を止めてスレッドを回収する。"""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeLlmServer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.shutdown()

    def _next_turn(self) -> dict[str, Any]:
        turn = self._script[min(self.call_count, len(self._script) - 1)]
        self.call_count += 1
        return turn

    def _response_body(self, turn: dict[str, Any], model: str) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": turn.get("content")}
        if "tool_calls" in turn:
            message["tool_calls"] = turn["tool_calls"]
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {"index": 0, "message": message, "finish_reason": turn["finish_reason"]},
            ],
            "usage": turn["usage"],
        }

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802  BaseHTTPRequestHandler が定める名前
                if not self.path.endswith(CHAT_COMPLETIONS_PATH):
                    self.send_error(404, f"unsupported path: {self.path}")
                    return

                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                try:
                    payload: dict[str, Any] = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {}
                owner.requests.append(payload)

                turn = owner._next_turn()
                model = str(payload.get("model", "fake-model"))
                body = json.dumps(owner._response_body(turn, model)).encode("utf-8")
                status = int(turn.get("status", 200))

                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  基底の引数名
                """テスト出力を汚さないため既定のアクセスログを捨てる。"""

        return _Handler
