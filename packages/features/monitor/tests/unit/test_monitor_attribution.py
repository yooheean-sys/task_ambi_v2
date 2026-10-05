"""US2・US3: 帰属とスレッド安全（monitor-F01・F06）。quickstart S4。

**帰属不明・相関の食い違い・二重 begin のいずれでも止めない**（FR-034・FR-035・FR-033）。
"""

from __future__ import annotations

import logging
import threading

import pytest
from conftest import (
    completion_payload,
    make_hook,
    make_request,
    make_response,
    make_service,
    make_state,
)

from common.interfaces.monitor import FailureKind
from common.kernel.log import CorrelationContext

# --------------------------------------------------------------------------------------
# 帰属（`attach`／`detach` を直接使う。`begin` を要さない）
# --------------------------------------------------------------------------------------


def test_outbound_before_attach_is_counted_and_warned(caplog: pytest.LogCaptureFixture) -> None:
    """① `attach` 前の送信は `unattributed_requests` に数えられ、止まらない。"""
    hook = make_hook()
    request = make_request()
    with caplog.at_level(logging.WARNING):
        assert hook.on_outbound(request) is request
    measurement, _ = hook.detach("k-1")
    assert measurement.unattributed_requests == 1
    assert measurement.requests == 0
    assert "monitor.hook.unattributed" in caplog.text


def test_outbound_after_detach_is_counted() -> None:
    """② `detach` 後の送信も同じ扱い（帰属先が無い）。"""
    hook = make_hook()
    hook.attach(make_state())
    hook.detach("k-1")
    hook.on_outbound(make_request())
    measurement, _ = hook.detach("k-1")
    assert measurement.unattributed_requests == 1


def test_inbound_without_state_warns_but_is_not_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """③ 応答側の帰属不明は**警告のみ**（要求側で数え済み＝ hook-contract.md §5）。"""
    hook = make_hook()
    response = make_response()
    with caplog.at_level(logging.WARNING):
        assert hook.on_inbound(response) is response
    measurement, _ = hook.detach("k-1")
    assert measurement.unattributed_requests == 0
    assert "monitor.hook.unattributed" in caplog.text


def test_correlation_mismatch_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    """④ 相関の識別子が食い違っても**止めない**（警告のみ）。"""
    hook = make_hook()
    hook.attach(make_state(correlation_id="c-begun"))
    token = CorrelationContext.bind("c-other")
    try:
        with caplog.at_level(logging.WARNING):
            request = make_request()
            assert hook.on_outbound(request) is request
    finally:
        CorrelationContext.reset(token)
    assert "monitor.hook.correlation_mismatch" in caplog.text


def test_sixteen_threads_count_exactly(caplog: pytest.LogCaptureFixture) -> None:
    """⑤ 16 スレッド × 200 回で `requests` が正確に 3,200（FR-028）。"""
    hook = make_hook()
    hook.attach(make_state(max_requests=1_000_000, max_cost=1_000_000.0))
    request = make_request()

    def worker() -> None:
        for _ in range(200):
            hook.on_outbound(request)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    with caplog.at_level(logging.ERROR):  # 警告の整形コストを避ける
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    measurement, _ = hook.detach("k-1")
    assert measurement.requests == 3200


def test_detach_with_a_different_kickoff_id_warns_and_takes_the_argument(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """⑥ `begin` と `finish` で識別子が食い違えば**引数を正**とし、警告だけを出す。

    取り違えると 1 回の実行の計測・封筒・`RunVerdict` が丸ごと**別の kickoff に帰属する**。
    帰属不明・相関の食い違い・二重 begin と同じく**止めない**側であり、積まれた失敗も
    失わない（spec Edge Cases「開始と終了で実行の識別子が食い違ったらどうなるか」）。
    """
    hook = make_hook()
    hook.attach(make_state(kickoff_id="k-1"))
    hook.on_inbound(make_response(completion_payload(finish_reason="length")))
    with caplog.at_level(logging.WARNING):
        measurement, failures = hook.detach("k-2")
    assert measurement.kickoff_id == "k-2"  # 引数の側が正
    assert [failure.kind for failure in failures] == [FailureKind.finish_length]
    record = next(r for r in caplog.records if r.getMessage() == "monitor.hook.kickoff_mismatch")
    assert record.__dict__["kickoff_id"] == "k-2"
    assert record.__dict__["begun_kickoff_id"] == "k-1"


# --------------------------------------------------------------------------------------
# `begin`（monitor-F06）
# --------------------------------------------------------------------------------------


def test_double_begin_supersedes_without_raising(caplog: pytest.LogCaptureFixture) -> None:
    """① 二重 begin は**例外なし**。旧い器の打切りが `WARNING` に残り差し替わる（FR-034）。"""
    hook = make_hook()
    service = make_service(hook=hook)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_outbound(make_request())
    with caplog.at_level(logging.WARNING):
        service.begin("k-2", make_state().limits, 1000)
    assert "monitor.begin.superseded" in caplog.text
    record = next(r for r in caplog.records if r.getMessage() == "monitor.begin.superseded")
    assert record.__dict__["superseded_kickoff_id"] == "k-1"
    assert record.__dict__["superseded_requests"] == 1
    assert record.__dict__["kickoff_id"] == "k-2"
    measurement, _ = hook.detach("k-2")
    assert measurement.requests == 0  # 新しい器に差し替わっている


def test_begin_without_bound_correlation_does_not_fail() -> None:
    """② `begin` は相関が未束縛でも落ちない（控える値は `None`）。"""
    hook = make_hook()
    service = make_service(hook=hook)
    service.begin("k-1", make_state().limits, 1000)
    request = make_request()
    assert hook.on_outbound(request) is request


def test_begin_captures_the_bound_correlation() -> None:
    """② ' `begin` 時の相関が控えられ、同じ値なら食い違い警告が出ない。"""
    hook = make_hook()
    service = make_service(hook=hook)
    token = CorrelationContext.bind("c-same")
    try:
        service.begin("k-1", make_state().limits, 1000)
        hook.on_outbound(make_request())
    finally:
        CorrelationContext.reset(token)
