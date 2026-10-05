"""US2: 応答の観測（monitor-F03・IF-41）。quickstart S3。

**上限判定以外では何も送出しない**。SSE は読まず、読み取りの副作用は打ち消す。
"""

from __future__ import annotations

import json

import pytest
from conftest import (
    completion_payload,
    make_config,
    make_hook,
    make_response,
    make_state,
    make_stream_response,
)

from common.interfaces.monitor import FailureKind


def test_non_json_body_does_not_escape_and_returns_the_response() -> None:
    """① 本文が JSON でなくても例外が呼び手へ飛ばず、応答がそのまま返る。"""
    hook = make_hook()
    hook.attach(make_state())
    response = make_response(content=b"<html>error</html>")
    assert hook.on_inbound(response) is response


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        ({"choices": [{"finish_reason": "stop"}]}, "usage が無い"),
        (completion_payload(usage={"prompt_tokens": 1, "completion_tokens": 1}), "cost が無い"),
        (completion_payload(cost="0.001"), "cost が文字列"),
        (completion_payload(cost=True), "cost が bool"),
    ],
)
def test_missing_usage_is_counted_once(payload: dict[str, object], label: str) -> None:
    """②③④⑤ 欠測は 1 応答につき最大 1 件（`bool` と文字列は数値と認めない）。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    hook.on_inbound(make_response(payload))
    assert state.cost_missing_count == 1, label
    assert [f.kind for f in state.failures] == [FailureKind.usage_missing], label


def test_stream_response_body_is_not_read() -> None:
    """⑥ `text/event-stream` は本文を読まない（下流のストリームを食い潰さない）。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    response = make_stream_response()
    assert hook.on_inbound(response) is response
    assert response.is_stream_consumed is False
    assert state.failures == []


def test_finish_reason_length_is_recorded() -> None:
    """⑦ `finish_reason: "length"` は `finish_length` として積まれる。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    hook.on_inbound(make_response(completion_payload(finish_reason="length")))
    assert [f.kind for f in state.failures] == [FailureKind.finish_length]


def test_three_healthy_responses_accumulate_the_measurement() -> None:
    """⑧ 正常な応答 3 本で計数が揃う。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    for _ in range(3):
        hook.on_inbound(make_response(completion_payload(prompt_tokens=10, completion_tokens=5)))
    assert (state.prompt_tokens, state.completion_tokens) == (30, 15)
    assert state.cost_usd == pytest.approx(0.003)
    assert state.cost_missing_count == 0
    assert state.failures == []


def test_elapsed_is_readable_after_the_hook_read_the_body() -> None:
    """⑨ 本文を読んだあとでも `response.elapsed` が `RuntimeError` にならない（FR-025）。"""
    hook = make_hook()
    hook.attach(make_state())
    response = make_response()
    hook.on_inbound(response)
    assert response.elapsed.total_seconds() >= 0.0


def test_existing_elapsed_is_not_overwritten() -> None:
    """⑨' 既に設定済みの `elapsed` は上書きしない（httpx が埋めた値を尊重する）。"""
    from datetime import timedelta

    hook = make_hook()
    hook.attach(make_state())
    response = make_response()
    response.elapsed = timedelta(seconds=42)
    hook.on_inbound(response)
    assert response.elapsed == timedelta(seconds=42)


def test_cost_field_path_reads_a_different_route() -> None:
    """⑩ `cost_field_path` を変えると別の経路から費用を読む。"""
    hook = make_hook(make_config(cost_field_path="billing.total"))
    state = make_state()
    hook.attach(state)
    payload = {
        "choices": [{"finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "billing": {"total": 0.25},
    }
    hook.on_inbound(make_response(payload))
    assert state.cost_usd == pytest.approx(0.25)
    assert state.cost_missing_count == 0


def test_hook_does_not_assign_a_grade() -> None:
    """⑪ Hook は等級を決めない（積まれるのは種類と証拠だけ）。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    hook.on_inbound(make_response(completion_payload(usage={}, finish_reason="length")))
    for failure in state.failures:
        assert not hasattr(failure, "grade")


def test_response_is_returned_unchanged() -> None:
    """応答は書き換えずにそのまま返す（FR-022）。"""
    hook = make_hook()
    hook.attach(make_state())
    payload = completion_payload()
    response = make_response(payload)
    assert hook.on_inbound(response) is response
    assert json.loads(response.content) == payload
