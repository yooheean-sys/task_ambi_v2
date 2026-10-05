"""偽 LLM サーバの台本（生成側）。data-model §6・plan (iv)。

`apps/api/tests/contract/conftest.py` の `make_llm_script` の**同型移植**（`contract/` 配下は
`integration/` から import できない＝ R-01）に、`tool_calls` を載せる口を足したものである。
**`_support/fake_llm_server.py` は 1 行も変えない**（R-04。4 形とも既存の口で組める）。

台本は `list[dict[str, Any]]`。1 要素＝1 往復で、キーは `finish_reason`（必須）・`usage`
（必須）・`content`・`tool_calls`・`status`。

⚠ **`usage` には必ず `cost` を載せる**（R-05）。`monitor/config.json` の
`cost_field_path = "usage.cost"` が読めないと `usage_missing` が積まれて等級が **I** になり、
「正常＝ I」の検証が `usage_missing` 由来の I と区別できなくなる（テストが誤って緑になる）。

⚠ **偽サーバは台本を使い切ったら最終要素を繰り返す**。往復数を読み違えたテストは「無限に
正常応答が返る」形で緑になりうるので、**すべての台本テストで `server.call_count` を assert
する**（R-04）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from common.kernel.enums import ToolName

FINAL_ANSWER_CONTENT = "Thought: I now can give a great answer\nFinal Answer: クラスタテストの出力"
"""1 往復で確定する CrewAI 向けの応答本文（`013` の実測形）。"""

DEFAULT_PROMPT_TOKENS = 12
DEFAULT_COMPLETION_TOKENS = 8
DEFAULT_COST = 0.001
"""1 往復あたりの `usage.cost`（`limit_cost` を累積で誘発する側が回数を決める）。"""


def make_turn(
    *,
    content: str | None = FINAL_ANSWER_CONTENT,
    finish_reason: str = "stop",
    prompt_tokens: int = DEFAULT_PROMPT_TOKENS,
    completion_tokens: int = DEFAULT_COMPLETION_TOKENS,
    cost: float | None = DEFAULT_COST,
    status: int | None = None,
    tool_calls: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """台本の 1 往復ぶんを組む。

    Args:
        content: 応答本文（`Final Answer:` 形にすると実 CrewAI が 1 往復で確定する）。
            道具呼び出しの往復では本文を持たないので `None` を渡す。
        finish_reason: `stop`／`length`（`finish_length` を積ませる）／`tool_calls`。
        prompt_tokens: `usage.prompt_tokens`。
        completion_tokens: `usage.completion_tokens`。
        cost: `usage.cost`（`None` にすると `usage_missing` を積ませる。**通常は落とさない**）。
        status: HTTP 応答コード（`503` で API 失敗を作る）。`None` なら 200。
        tool_calls: `message.tool_calls`（`make_tool_call` で組む）。

    Returns:
        `FakeLlmServer` の台本の 1 要素。
    """
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cost is not None:
        usage["cost"] = cost
    turn: dict[str, Any] = {"content": content, "finish_reason": finish_reason, "usage": usage}
    if tool_calls is not None:
        turn["tool_calls"] = list(tool_calls)
    if status is not None:
        turn["status"] = status
    return turn


def make_tool_call(index: int, name: ToolName, arguments: object) -> dict[str, Any]:
    """OpenAI 互換の道具呼び出し 1 件（`arguments` は**文字列**で渡す規約＝ R-03）。

    実 CrewAI は native function calling で道具を呼ぶ（要求本文に `tools` と
    `tool_choice: auto` が載る）。`Thought/Action` の本文解析ではない。

    Args:
        index: 呼び出し ID の連番（`call_<index>`）。
        name: 呼ぶ Tool の登録名。
        arguments: Tool へ渡す引数（JSON 文字列へ落として載せる）。

    Returns:
        `message.tool_calls[]` の 1 件。
    """
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": name.value,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def final_answer_turn(**overrides: Any) -> dict[str, Any]:
    """`Final Answer:` 形の本文で 1 往復で確定させる要素。"""
    return make_turn(**overrides)


def script_tool_calls(
    calls: Sequence[dict[str, Any]],
    *,
    closing: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """1 往復目に `tool_calls` を載せ、2 往復目で確定する 2 要素の台本を組む。

    Args:
        calls: 1 往復目で呼ばせる道具（`make_tool_call` の戻りの並び）。
        closing: 2 往復目の要素（`None` なら `final_answer_turn()`）。

    Returns:
        2 要素の台本。
    """
    first = make_turn(content=None, finish_reason="tool_calls", tool_calls=calls)
    return [first, closing if closing is not None else final_answer_turn()]


def script_repeated(turn: dict[str, Any], *, turns: int) -> list[dict[str, Any]]:
    """同じ要素を `turns` 個並べる（上限超過・5xx 連発用）。

    要素は**都度コピーする**（偽サーバは要素を書き換えないが、テスト側が 1 要素を触って
    他の往復まで変わる事故を作らない）。

    Args:
        turn: 並べる 1 往復ぶん。
        turns: 要素数（**1 以上**）。

    Returns:
        `turns` 個の台本。
    """
    if turns < 1:
        msg = f"台本は 1 要素以上必要（turns={turns}）"
        raise ValueError(msg)
    return [dict(turn) for _ in range(turns)]
