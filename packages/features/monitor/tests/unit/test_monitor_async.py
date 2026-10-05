"""US2: 非同期の 2 メソッドが同期側と**同内容**であること（monitor-F04・SC-006・Z-4）。

**`asyncio.run` で回す**（非同期のテスト用プラグインを増やさない。現物の前例＝
`packages/common/tests/kernel/test_log_correlation.py:56,89`）。
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from conftest import (
    TRUNCATION_PHRASE,
    chat_payload,
    completion_payload,
    make_config,
    make_hook,
    make_linked_board,
    make_outcome,
    make_request,
    make_response,
    make_service,
    make_state,
    make_stream_response,
)

from common.interfaces.monitor import FailureKind, Grade, Measurement, RunHalted, RunVerdict
from common.interfaces.settings import LimitPolicy
from monitor.models import RawFailure
from monitor.repository import HttpHook

_LIMITS = LimitPolicy(scope="global", max_requests_per_kickoff=1000, max_cost_per_kickoff=1000.0)
"""どの上限にも掛からない広い値（`make_state` の既定と同じ）。"""

_THRESHOLD_T = 1_000_000
"""長さの閾値（掛からない広い値）。"""


def _sync_calls(hook: HttpHook, *, payload: dict[str, object]) -> None:
    """同期側の 3 呼び出し（`detach` はしない）。"""
    hook.on_outbound(
        make_request(chat_payload(messages=[{"role": "assistant", "content": "問い"}]))
    )
    hook.on_inbound(make_response(payload))
    hook.on_outbound(
        make_request(chat_payload(messages=[{"role": "assistant", "content": TRUNCATION_PHRASE}]))
    )


async def _async_calls(hook: HttpHook, *, payload: dict[str, object]) -> None:
    """非同期側の 3 呼び出し（同期側と**同じ入力・同じ順**）。"""
    await hook.aon_outbound(
        make_request(chat_payload(messages=[{"role": "assistant", "content": "問い"}]))
    )
    await hook.aon_inbound(make_response(payload))
    await hook.aon_outbound(
        make_request(chat_payload(messages=[{"role": "assistant", "content": TRUNCATION_PHRASE}]))
    )


def _sync_run(
    hook: HttpHook, *, payload: dict[str, object]
) -> tuple[Measurement, list[RawFailure]]:
    _sync_calls(hook, payload=payload)
    return hook.detach("k-1")


async def _async_run(
    hook: HttpHook, *, payload: dict[str, object]
) -> tuple[Measurement, list[RawFailure]]:
    await _async_calls(hook, payload=payload)
    return hook.detach("k-1")


def _comparable(measurement: Measurement, failures: list[RawFailure]) -> str:
    """時刻を除いた計測と、積まれた種類・証拠を文字列に畳む（1 バイト違わないことを見る）。"""
    fields = measurement.model_dump(exclude={"started_at", "finished_at"})
    kinds = [(failure.kind.value, sorted(failure.evidence.items())) for failure in failures]
    return f"{sorted(fields.items())}|{kinds}"


def test_sync_and_async_produce_identical_measurements() -> None:
    """同じ入力を両経路に通し、計測と積まれた失敗が 1 バイト違わない。"""
    payload = completion_payload(prompt_tokens=7, completion_tokens=3, finish_reason="length")

    sync_hook = make_hook()
    sync_hook.attach(make_state())
    sync_result = _comparable(*_sync_run(sync_hook, payload=payload))

    async_hook = make_hook()
    async_hook.attach(make_state())
    async_result = _comparable(*asyncio.run(_async_run(async_hook, payload=payload)))

    assert sync_result == async_result


def _verdict_of(*, run_async: bool) -> RunVerdict:
    """`begin` → 3 呼び出し → `finish` を 1 経路ぶん通す。

    **盤面はスタブ**（`make_linked_board`）で、`httpx.Client`／`AsyncClient` を 1 度も作らない
    （SC-019）。応答の `finish_reason=length` と 2 本目の要求の強制最終回答文言で W 級が 2 種類
    積まれ、写像（comment → close）と確認読取まで通る経路になる。
    """
    reader, writer = make_linked_board()
    config = make_config()
    hook = make_hook(config)
    service = make_service(reader=reader, writer=writer, config=config, hook=hook)
    service.begin("k-1", _LIMITS, _THRESHOLD_T)
    payload = completion_payload(prompt_tokens=7, completion_tokens=3, finish_reason="length")
    if run_async:
        asyncio.run(_async_calls(hook, payload=payload))
    else:
        _sync_calls(hook, payload=payload)
    return service.finish("k-1", "i-1", "bot", make_outcome())


def _comparable_verdict(verdict: RunVerdict) -> tuple[object, ...]:
    """判定のうち**時刻に依らない 5 欄**（`Envelope.render()` は時刻を 1 つも載せない）。"""
    return (verdict.grade, tuple(verdict.kinds), verdict.halted, verdict.closed, verdict.envelope)


def test_sync_and_async_produce_identical_verdicts() -> None:
    """同じ入力を両経路で `finish` まで通し、`RunVerdict` が一致する（T027・SC-006）。"""
    sync_verdict = _verdict_of(run_async=False)
    async_verdict = _verdict_of(run_async=True)

    assert _comparable_verdict(sync_verdict) == _comparable_verdict(async_verdict)
    # 写像と確認読取まで通る経路を通っていることを確かめる（空の判定を比べて緑にしない）
    assert sync_verdict.grade is Grade.W
    assert sync_verdict.kinds == [FailureKind.truncated_answer, FailureKind.finish_length]
    assert sync_verdict.closed is True


def test_async_outbound_raises_run_halted_with_the_same_attributes() -> None:
    """`RunHalted` は非同期側でも同じ属性で飛ぶ。"""

    async def _run() -> RunHalted:
        hook = make_hook()
        hook.attach(make_state(max_requests=0))
        try:
            await hook.aon_outbound(make_request())
        except RunHalted as halted:
            return halted
        pytest.fail("aon_outbound が RunHalted を送出しなかった")

    sync_hook = make_hook()
    sync_hook.attach(make_state(max_requests=0))
    with pytest.raises(RunHalted) as caught:
        sync_hook.on_outbound(make_request())
    expected = caught.value

    actual = asyncio.run(_run())
    assert (actual.kind, actual.measured, actual.limit, actual.request_no) == (
        expected.kind,
        expected.measured,
        expected.limit,
        expected.request_no,
    )
    assert actual.kind is FailureKind.limit_requests


def test_async_inbound_does_not_read_the_stream() -> None:
    """非同期側でも SSE の本文を読まない。"""

    async def _run() -> bool:
        hook = make_hook()
        hook.attach(make_state())
        response = make_stream_response()
        assert await hook.aon_inbound(response) is response
        return response.is_stream_consumed

    assert asyncio.run(_run()) is False


def test_async_inbound_fills_elapsed() -> None:
    """非同期側でも読み取りの副作用を打ち消す。"""

    async def _run() -> float:
        hook = make_hook()
        hook.attach(make_state())
        response = make_response()
        await hook.aon_inbound(response)
        return response.elapsed.total_seconds()

    assert asyncio.run(_run()) >= 0.0


def test_async_inbound_swallows_internal_failures() -> None:
    """非同期側でも内部例外を呼び手へ飛ばさない。"""

    async def _run() -> bool:
        hook = make_hook()
        hook.attach(make_state())
        response = make_response(content=b"not json")
        return await hook.aon_inbound(response) is response

    assert asyncio.run(_run()) is True


def test_async_inbound_without_state_warns_but_is_not_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """帰属不明の応答は非同期側でも**警告のみ**で素通しする（FR-026・SC-006）。

    同期側の `test_monitor_attribution.py` の ③ と**同一の判定・観測**であることを見る。
    応答側は `unattributed_requests` に数えない（要求側で数え済み＝ hook-contract.md §5）。
    """
    hook = make_hook()
    response = make_response()

    async def _run() -> bool:
        return await hook.aon_inbound(response) is response

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(_run()) is True
    measurement, _ = hook.detach("k-1")
    assert measurement.unattributed_requests == 0
    assert "monitor.hook.unattributed" in caplog.text
