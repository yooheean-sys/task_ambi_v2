"""偽 LLM サーバの自己検証（FR-027・FR-029・SC-006・US5 AC1／AC2）。

fixture が返す `base_url` に対して OpenAI 互換の要求を **2 往復**送り、台本どおりに応答が
返ることを確かめる。クライアントは標準ライブラリ（`urllib.request`）で組む — plan (v) の
「追加の実行時依存を増やさない」方針に従い、openai／httpx を土台の依存に持ち込まないため。
検証しているのは応答本文が OpenAI 互換であることであり、クライアント実装ではない。
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from _support.fake_llm_server import FakeLlmServer

SCRIPT: list[dict[str, Any]] = [
    {
        "finish_reason": "tool_calls",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_issue", "arguments": '{"issue_id":"bd-1"}'},
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0001},
    },
    {
        "finish_reason": "stop",
        "content": "done",
        "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23, "cost": 0.0002},
    },
]

FakeServerFactory = Callable[[list[dict[str, Any]]], FakeLlmServer]


def _post_chat_completion(base_url: str, message: str) -> dict[str, Any]:
    payload = json.dumps(
        {"model": "fake-model", "messages": [{"role": "user", "content": message}]}
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310  ループバック固定の http のみ
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310  同上
        body: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    return body


def test_two_turns_follow_the_script(fake_llm_server: FakeServerFactory) -> None:
    server = fake_llm_server(SCRIPT)

    first = _post_chat_completion(server.base_url, "1 往復目")
    choice_1: dict[str, Any] = first["choices"][0]
    assert choice_1["finish_reason"] == "tool_calls"
    assert choice_1["message"]["tool_calls"][0]["function"]["name"] == "read_issue"
    assert first["usage"]["cost"] == 0.0001
    assert first["model"] == "fake-model", "要求の model をそのまま返す（OpenAI 互換）"

    second = _post_chat_completion(server.base_url, "2 往復目")
    choice_2: dict[str, Any] = second["choices"][0]
    assert choice_2["finish_reason"] == "stop"
    assert choice_2["message"]["content"] == "done"
    assert "tool_calls" not in choice_2["message"], "台本が指定した往復にのみ tool_calls を載せる"
    assert second["usage"]["cost"] == 0.0002

    assert server.call_count == 2
    assert len(server.requests) == 2
    assert server.requests[0]["messages"][0]["content"] == "1 往復目"


def test_base_url_is_loopback_only(fake_llm_server: FakeServerFactory) -> None:
    """外部通信が 0 回であること（FR-024・SC-006）。待受は 127.0.0.1 のみ。"""
    server = fake_llm_server(SCRIPT)
    parsed = urlparse(server.base_url)
    assert parsed.hostname == "127.0.0.1", f"ループバック以外に bind している: {server.base_url}"
    assert parsed.scheme == "http"
    assert parsed.path == "/v1"


def test_port_is_assigned_by_os(fake_llm_server: FakeServerFactory) -> None:
    """ポートを OS に採番させる（FR-029）。同時に 2 台立てても衝突しない。"""
    first = fake_llm_server(SCRIPT)
    second = fake_llm_server(SCRIPT)
    port_1 = urlparse(first.base_url).port
    port_2 = urlparse(second.base_url).port
    assert port_1 and port_2, "ポートが採番されていない"
    assert port_1 != port_2, "固定ポートを使っている（待受ポートの衝突が起きる）"


def test_script_repeats_last_turn(fake_llm_server: FakeServerFactory) -> None:
    """台本を使い切ったら最終要素を繰り返す（contracts/fake-llm-server.md §3）。"""
    server = fake_llm_server(SCRIPT)
    for _ in range(3):
        _post_chat_completion(server.base_url, "往復")
    last = _post_chat_completion(server.base_url, "使い切った後")
    assert last["choices"][0]["finish_reason"] == "stop"
    assert server.call_count == 4
