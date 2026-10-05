"""US1: 上限を超えた要求が外へ出る前に止まる（monitor-F02／F05・IF-40）。

quickstart S2 の 7 行 ＋ SC-002・SC-003・SC-006a。**サーバもネットワークも AI も使わない**。
"""

from __future__ import annotations

import json
import math

import pytest
from conftest import (
    TRUNCATION_PHRASE,
    chat_payload,
    make_config,
    make_hook,
    make_request,
    make_state,
)

from common.interfaces.monitor import FailureKind, RunHalted

# --------------------------------------------------------------------------------------
# ①〜⑤ 上限の判定（長さ → 回数 → 費用。厳密な超過）
# --------------------------------------------------------------------------------------


def test_max_requests_stops_the_fourth_request() -> None:
    """① `max_requests=3` で 4 本目が止まり、要求が返らない。"""
    hook = make_hook()
    hook.attach(make_state(max_requests=3))
    for _ in range(3):
        hook.on_outbound(make_request())
    with pytest.raises(RunHalted) as caught:
        hook.on_outbound(make_request())
    halted = caught.value
    assert halted.kind is FailureKind.limit_requests
    assert (halted.measured, halted.limit, halted.request_no) == (4, 3, 4)


def test_long_messages_stop_even_when_count_and_cost_are_inside() -> None:
    """② 閾値超過の長い `messages` は、回数・費用が上限内でも止まる。"""
    hook = make_hook()
    hook.attach(make_state(threshold_t=10, max_requests=1000, max_cost=1000.0))
    payload = chat_payload(messages=[{"role": "user", "content": "あ" * 400}])
    with pytest.raises(RunHalted) as caught:
        hook.on_outbound(make_request(payload))
    assert caught.value.kind is FailureKind.ctx_exceeded
    assert caught.value.limit == 10


def test_accumulated_cost_stops_before_sending() -> None:
    """③ 費用の累計が上限超過の状態では、次の送信の**前**に止まる。"""
    hook = make_hook()
    state = make_state(max_cost=0.5)
    hook.attach(state)
    state.cost_usd = 0.6  # これまでの応答から積んだ累計
    with pytest.raises(RunHalted) as caught:
        hook.on_outbound(make_request())
    assert caught.value.kind is FailureKind.limit_cost
    assert (caught.value.measured, caught.value.limit) == (0.6, 0.5)


def test_all_three_limits_hit_at_once_always_reports_ctx_exceeded() -> None:
    """④ 3 つ同時に当たっても種類は一意（**常に長さ**）＝ SC-003。"""
    hook = make_hook()
    state = make_state(threshold_t=1, max_requests=0, max_cost=0.0)
    hook.attach(state)
    state.cost_usd = 99.0
    payload = chat_payload(messages=[{"role": "user", "content": "x" * 100}])
    with pytest.raises(RunHalted) as caught:
        hook.on_outbound(make_request(payload))
    assert caught.value.kind is FailureKind.ctx_exceeded


@pytest.mark.parametrize(
    ("threshold_t", "max_requests", "cost_so_far", "max_cost"),
    [
        (25, 1000, 0.0, 1000.0),  # 推定値ちょうど（"x"*100 → 25）
        (1_000_000, 1, 0.0, 1000.0),  # 回数ちょうど（1 本目で requests == 1）
        (1_000_000, 1000, 0.5, 0.5),  # 費用ちょうど
    ],
)
def test_equal_to_the_limit_does_not_stop(
    threshold_t: int, max_requests: int, cost_so_far: float, max_cost: float
) -> None:
    """⑤ 上限とちょうど等しい値では止めない（3 つとも厳密な `>`）。"""
    hook = make_hook()
    state = make_state(threshold_t=threshold_t, max_requests=max_requests, max_cost=max_cost)
    hook.attach(state)
    state.cost_usd = cost_so_far
    payload = chat_payload(messages=[{"role": "user", "content": "x" * 100}])
    assert hook.on_outbound(make_request(payload)) is not None
    assert state.halted_reason is None


def test_request_is_returned_unchanged_when_inside_limits() -> None:
    """⑥ 上限内なら受け取った要求が `is` で同一のまま返る（FR-016）。"""
    hook = make_hook()
    hook.attach(make_state())
    request = make_request()
    assert hook.on_outbound(request) is request


def test_zero_limit_is_not_corrected() -> None:
    """⑦ `max_requests=0` は補正せず 1 本目で止まる（FR-014c）。"""
    hook = make_hook()
    hook.attach(make_state(max_requests=0))
    with pytest.raises(RunHalted) as caught:
        hook.on_outbound(make_request())
    assert caught.value.kind is FailureKind.limit_requests
    assert (caught.value.measured, caught.value.limit, caught.value.request_no) == (1, 0, 1)


def test_numbering_advances_even_when_halted() -> None:
    """⑧ 停止した回も番号を消費する（P-02）。"""
    hook = make_hook()
    state = make_state(max_requests=1)
    hook.attach(state)
    hook.on_outbound(make_request())
    for expected_no in (2, 3):
        with pytest.raises(RunHalted) as caught:
            hook.on_outbound(make_request())
        assert caught.value.request_no == expected_no
    assert state.requests == 3


def test_halt_records_reason_and_raw_failure() -> None:
    """⑨ 停止時は `halted_reason` が入り `RawFailure` が 1 件積まれる。"""
    hook = make_hook()
    state = make_state(max_requests=0)
    hook.attach(state)
    with pytest.raises(RunHalted):
        hook.on_outbound(make_request())
    assert state.halted_reason is FailureKind.limit_requests
    assert [f.kind for f in state.failures] == [FailureKind.limit_requests]
    assert state.failures[0].evidence == {"measured": 1, "limit": 0, "request_no": 1}


# --------------------------------------------------------------------------------------
# SC-002: `RunHalted` が間の層の握りつぶしを素通りする
# --------------------------------------------------------------------------------------


def test_run_halted_passes_through_except_exception() -> None:
    """SC-002 (1): `except Exception` に捕まらない。"""
    hook = make_hook()
    hook.attach(make_state(max_requests=0))
    with pytest.raises(RunHalted):
        try:
            hook.on_outbound(make_request())
        except Exception:  # noqa: BLE001  # 素通りすることの検証そのもの
            pytest.fail("RunHalted が except Exception に捕まった")


def test_run_halted_passes_through_specific_excepts() -> None:
    """SC-002 (2): `except (ValueError, RuntimeError)` に捕まらない。"""
    hook = make_hook()
    hook.attach(make_state(max_requests=0))
    with pytest.raises(RunHalted):
        try:
            hook.on_outbound(make_request())
        except (ValueError, RuntimeError):
            pytest.fail("RunHalted が具体例外の except に捕まった")


def test_run_halted_breaks_out_of_a_retry_loop() -> None:
    """SC-002 (3): `except Exception` で 3 回リトライするループを 1 回目で抜ける。"""
    hook = make_hook()
    state = make_state(max_requests=0)
    hook.attach(state)
    attempts = 0
    with pytest.raises(RunHalted):
        for _ in range(3):
            attempts += 1
            try:
                hook.on_outbound(make_request())
            except Exception:  # noqa: BLE001  # リトライ層の模擬
                continue
    assert attempts == 1
    assert state.requests == 1


# --------------------------------------------------------------------------------------
# 推定（F05）と文言照合（FR-040a）
# --------------------------------------------------------------------------------------


def test_chars_estimator_is_ceil_of_length_over_four() -> None:
    """① `chars` 方式は `ceil(len(連結 content) / 4)`。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    body = "あ" * 10 + "b" * 3
    hook.on_outbound(make_request(chat_payload(messages=[{"role": "user", "content": body}])))
    assert state.max_tokens_before_send == math.ceil(len(body) / 4)


def test_multimodal_content_picks_only_text_parts() -> None:
    """② `content` が `list` のとき `str` 要素と `{"type":"text"}` の `text` だけを拾う。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    content = [
        "abcd",
        {"type": "text", "text": "efgh"},
        {"type": "image_url", "image_url": {"url": "x" * 500}},
        12345,
    ]
    hook.on_outbound(make_request(chat_payload(messages=[{"role": "user", "content": content}])))
    assert state.max_tokens_before_send == math.ceil(len("abcdefgh") / 4)


@pytest.mark.parametrize(
    "payload_bytes",
    [
        b"not json at all",
        json.dumps({"model": "m"}).encode(),  # messages が無い
        json.dumps({"messages": "文字列"}).encode(),  # content を取り出せない
    ],
)
def test_unestimatable_body_records_zero_and_does_not_stop(payload_bytes: bytes) -> None:
    """③ 推定不能な body は **0 として記録し送信を止めない**（FR-030）。"""
    hook = make_hook()
    state = make_state(threshold_t=0)  # 0 でも 0 > 0 は偽なので止まらない
    hook.attach(state)
    request = make_request(content=payload_bytes)
    assert hook.on_outbound(request) is request
    assert state.max_tokens_before_send == 0
    assert state.halted_reason is None


def test_max_tokens_before_send_keeps_the_maximum() -> None:
    """④ `max_tokens_before_send` には最大が残る（FR-031）。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    for length in (40, 200, 80):
        hook.on_outbound(
            make_request(chat_payload(messages=[{"role": "user", "content": "x" * length}]))
        )
    assert state.max_tokens_before_send == math.ceil(200 / 4)


def test_truncation_phrase_is_recorded_without_stopping() -> None:
    """⑤ 文言が完全一致すると `truncated_answer` が積まれるが、要求はそのまま返る。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    payload = chat_payload(
        messages=[
            {"role": "user", "content": "問い"},
            {"role": "assistant", "content": f"  {TRUNCATION_PHRASE}  "},
        ]
    )
    request = make_request(payload)
    assert hook.on_outbound(request) is request
    assert [f.kind for f in state.failures] == [FailureKind.truncated_answer]


def test_truncation_phrase_is_not_matched_partially() -> None:
    """⑤' 部分一致では積まない（両辺 `strip()` して**完全一致**）。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    payload = chat_payload(
        messages=[{"role": "assistant", "content": f"前置き {TRUNCATION_PHRASE}"}]
    )
    hook.on_outbound(make_request(payload))
    assert state.failures == []


def test_truncation_match_runs_after_the_limit_judgement() -> None:
    """⑥ 文言照合は上限判定より**後**に走り、判定結果に影響しない（FR-013）。"""
    hook = make_hook()
    state = make_state(max_requests=0)
    hook.attach(state)
    payload = chat_payload(messages=[{"role": "assistant", "content": TRUNCATION_PHRASE}])
    with pytest.raises(RunHalted) as caught:
        hook.on_outbound(make_request(payload))
    assert caught.value.kind is FailureKind.limit_requests
    # 停止したので文言照合（手順 7）には進まない
    assert [f.kind for f in state.failures] == [FailureKind.limit_requests]


def test_empty_truncation_phrases_never_match() -> None:
    """⑤'' 文言の一覧が空なら何も積まない（設定だけで挙動が決まる）。"""
    hook = make_hook(make_config(truncation_phrases=[]))
    state = make_state()
    hook.attach(state)
    payload = chat_payload(messages=[{"role": "assistant", "content": TRUNCATION_PHRASE}])
    hook.on_outbound(make_request(payload))
    assert state.failures == []
