"""monitor 提供の Hook 4 IF（IF-40〜43）の適合検証。US5。

**実 Hook**（monitor の `build()` が返す `LLMHttpHook`）を、**偽 LLM サーバへの実 HTTP 往復**に
通して確かめる（FR-023）。`httpx.MockTransport` は使わない —— Hook は httpx の transport 層の
差し込み点であり、MockTransport はそこを置き換えてしまう（FR-009）。外部の実 LLM API へは
1 度も通信しない（FR-010。宛先はループバックだけ）。

非同期 2 件（IF-42・IF-43）は**非同期として呼ぶ**（FR-019）。同期テストの中の
`asyncio.run(...)` で回す —— `pytest-asyncio` は未導入で、ルート `pyproject.toml` は変更禁止
（FR-030）だからである（R-07）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from _support.contract_stubs import StubBoardReader, StubBoardWriter
from conftest import (
    MONITOR_ACTOR,
    MONITOR_ISSUE_ID,
    MONITOR_KICKOFF_ID,
    MONITOR_THRESHOLD_T,
    make_llm_script,
    make_monitor_envelope_comment,
    make_monitor_issue,
)

from common.interfaces.board import IssueStatus
from common.interfaces.executor import CrewOutcome
from common.interfaces.monitor import FailureKind, Grade, RunHalted
from common.interfaces.settings import LimitPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.contract_stubs import CallLog
    from _support.fake_llm_server import FakeLlmServer

    from common.interfaces.monitor import LLMHttpHook, RunMonitor
    from monitor import MonitorHandle

GENEROUS_LIMITS = LimitPolicy(
    scope="global", max_requests_per_kickoff=10, max_cost_per_kickoff=10.0
)
"""上限に当たらない設定（正常系用）。"""

ONE_REQUEST_LIMITS = LimitPolicy(
    scope="global", max_requests_per_kickoff=1, max_cost_per_kickoff=10.0
)
"""1 往復だけ許す設定（2 往復目の `on_outbound` で `RunHalted` を踏む）。"""

CHEAP_LIMITS = LimitPolicy(scope="global", max_requests_per_kickoff=10, max_cost_per_kickoff=0.5)
"""費用だけを絞った設定。**回数の上限には当たらない**ので、`limit_requests` の分岐に落ちずに
`limit_cost` の分岐だけを踏み分けられる（判定順は 長さ → 回数 → 費用）。"""

TIGHT_THRESHOLD_T = 1
"""1 往復目の推定トークン（`chars` 方式＝ `ceil(len(content)/4)`）で必ず超える threshold_T。"""

ACCUMULATED_COST = 1.0
"""1 往復で積む費用（`CHEAP_LIMITS.max_cost_per_kickoff` を超える）。"""

BENIGN_OUTCOME = CrewOutcome(
    proposal=None,
    task_output="契約テストの出力",
    completed=True,
    completion_reason="done",
    review=None,
    escalation=None,
    written=[],
    events=[],
    halted=False,
    halted_reason=None,
    progress_notes=0,
)
"""失敗を 1 件も生まない実行結果（`finish` を計測の読み口としてだけ使うため）。"""


def _payload() -> dict[str, Any]:
    """OpenAI 互換の要求本文（偽 LLM サーバが受け取れる最小形）。"""
    return {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "契約テストの問い合わせ"}],
    }


def _round_trip(server: FakeLlmServer, hook: LLMHttpHook) -> httpx.Response:
    """`on_outbound` → 実 HTTP 送信 → `on_inbound` の 1 往復を回す。"""
    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        sent = hook.on_outbound(request)
        response = client.send(sent)
        return hook.on_inbound(response)


@pytest.mark.contract_if("IF-40")
def test_contract_monitor_on_outbound_passes_the_request_through(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-40 正常系: 上限内なら受け取った `httpx.Request` を**書き換えずに**返す。"""
    server = fake_llm_server(make_llm_script())
    run_monitor.begin(MONITOR_KICKOFF_ID, GENEROUS_LIMITS, MONITOR_THRESHOLD_T)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        returned = llm_http_hook.on_outbound(request)

        assert isinstance(returned, httpx.Request)
        assert returned is request
        client.send(returned)

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )
    assert verdict.measurement.requests == 1


@pytest.mark.contract_if("IF-41")
def test_contract_monitor_on_inbound_observes_usage(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-41: 応答をそのまま返し、**`usage`** を観測する（例外欄が `—`＝ FR-018）。

    もう一方の観測（`finish_reason`）は下の別テストが踏む。既定の台本は `finish_reason` が
    `stop` 固定で、その経路を通らない。
    """
    server = fake_llm_server(make_llm_script(prompt_tokens=31, completion_tokens=7, cost=0.25))
    run_monitor.begin(MONITOR_KICKOFF_ID, GENEROUS_LIMITS, MONITOR_THRESHOLD_T)

    response = _round_trip(server, llm_http_hook)

    assert isinstance(response, httpx.Response)
    assert response.status_code == 200

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )
    assert verdict.measurement.prompt_tokens == 31
    assert verdict.measurement.completion_tokens == 7
    assert verdict.measurement.cost_usd == pytest.approx(0.25)
    assert verdict.measurement.cost_missing_count == 0


@pytest.mark.contract_if("IF-41")
def test_contract_monitor_on_inbound_observes_finish_reason_length(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-41: `on_inbound` が **`finish_reason`** も観測する（FR-016）。

    台帳の出力欄は `httpx.Response（usage・finish_reason の観測）` と **2 つの観測**を
    名指しするが、上のテストが見るのは `usage` の 3 値だけである（既定の台本は
    `finish_reason` が `stop` 固定なので、観測の経路そのものを通らない）。

    現物は `repository.py:479-486` で `choices[*].finish_reason == "length"` を
    `FailureKind.finish_length` として積む。観測は**応答を止めない** —— `on_inbound` は
    受け取った応答をそのまま返し（例外欄は `—`＝ FR-018）、積んだ失敗が表に出るのは
    `finish` の `RunVerdict.kinds` である。

    `finish_length` は `grade_map` で **W** なので `finish` は写像と確認読取まで行く。
    ここは `finish_reason` の観測が目的なので、盤面スタブを**写像後の姿**（`closed` ＋
    封筒コメント）に置いて `MappingUnverified` に落ちないようにしてある。
    """
    server = fake_llm_server(make_llm_script(finish_reason="length"))
    reader = StubBoardReader(
        call_log,
        issues={MONITOR_ISSUE_ID: make_monitor_issue(status=IssueStatus.closed)},
        comments={MONITOR_ISSUE_ID: [make_monitor_envelope_comment(Grade.W)]},
    )
    handle = monitor_factory(reader, StubBoardWriter(call_log))
    run_monitor: RunMonitor = handle.run_monitor
    hook: LLMHttpHook = handle.llm_http_hook
    run_monitor.begin(MONITOR_KICKOFF_ID, GENEROUS_LIMITS, MONITOR_THRESHOLD_T)

    response = _round_trip(server, hook)

    assert response.status_code == 200, "観測は応答を止めない"

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )

    assert FailureKind.finish_length in verdict.kinds
    assert verdict.measurement.cost_missing_count == 0, "`usage` 側の観測は無傷"


@pytest.mark.contract_if("IF-42")
def test_contract_monitor_aon_outbound_passes_the_request_through(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-42 正常系: **非同期として呼ぶ**（台帳の `同期/非同期` 欄が `非同期`＝ FR-019）。"""
    server = fake_llm_server(make_llm_script())
    run_monitor.begin(MONITOR_KICKOFF_ID, GENEROUS_LIMITS, MONITOR_THRESHOLD_T)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        returned = asyncio.run(llm_http_hook.aon_outbound(request))

        assert isinstance(returned, httpx.Request)
        assert returned is request
        client.send(returned)

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )
    assert verdict.measurement.requests == 1


@pytest.mark.contract_if("IF-43")
def test_contract_monitor_aon_inbound_observes_usage(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-43: 非同期の応答観測（例外欄が `—`＝ FR-018）。**非同期として呼ぶ**（FR-019）。"""
    server = fake_llm_server(make_llm_script(prompt_tokens=5, completion_tokens=3, cost=0.5))
    run_monitor.begin(MONITOR_KICKOFF_ID, GENEROUS_LIMITS, MONITOR_THRESHOLD_T)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        sent = asyncio.run(llm_http_hook.aon_outbound(request))
        response = client.send(sent)
        returned = asyncio.run(llm_http_hook.aon_inbound(response))

    assert isinstance(returned, httpx.Response)
    assert returned is response

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )
    assert verdict.measurement.prompt_tokens == 5
    assert verdict.measurement.cost_usd == pytest.approx(0.5)


@pytest.mark.contract_if("IF-40")
def test_contract_monitor_on_outbound_raises_run_halted_over_the_limit(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-40 例外系: 上限を超過したら `RunHalted`（**要求は送られない**）。

    `max_requests_per_kickoff=1` を張って 1 往復させ、**2 往復目の `on_outbound`** で送出させる
    （R-10）。判定は `RunHalted` の中身（種類・実測値・上限・何回目）まで確かめる。
    """
    server = fake_llm_server(make_llm_script(turns=2))
    run_monitor.begin(MONITOR_KICKOFF_ID, ONE_REQUEST_LIMITS, MONITOR_THRESHOLD_T)
    _round_trip(server, llm_http_hook)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        with pytest.raises(RunHalted) as caught:
            llm_http_hook.on_outbound(request)

    assert caught.value.kind is FailureKind.limit_requests
    assert caught.value.limit == ONE_REQUEST_LIMITS.max_requests_per_kickoff
    assert caught.value.request_no == 2
    assert server.call_count == 1, "2 往復目の要求は送られない"


@pytest.mark.contract_if("IF-40")
def test_contract_monitor_on_outbound_raises_run_halted_over_the_token_threshold(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-40 例外系: **送信前トークン**の超過でも `RunHalted`（`ctx_exceeded`）。

    台帳の出力欄は `httpx.Request（送信前トークン・回数・コストの判定。超過は RunHalted を
    送出）` と **3 次元**を名指しするが、上の `RunHalted` 3 本はいずれも `limit_requests`
    （回数）だけを踏む。現物は `repository.py:421-427`（`_reserve_and_judge`）で
    **長さ → 回数 → 費用**の順に判定する 3 分岐であり、最初の分岐が未検証だった。

    threshold_T を 1 に張ると **1 往復目**で超える（`chars` 方式の推定は
    `ceil(len(連結 content)/4)`）。回数の上限（`GENEROUS_LIMITS` の 10）には当たらないので、
    `kind` が `limit_requests` でないことまで見て**分岐を取り違えていない**ことを確かめる。
    """
    server = fake_llm_server(make_llm_script())
    run_monitor.begin(MONITOR_KICKOFF_ID, GENEROUS_LIMITS, TIGHT_THRESHOLD_T)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        with pytest.raises(RunHalted) as caught:
            llm_http_hook.on_outbound(request)

    assert caught.value.kind is FailureKind.ctx_exceeded
    assert caught.value.kind is not FailureKind.limit_requests, "回数の分岐に落ちていない"
    assert caught.value.limit == TIGHT_THRESHOLD_T
    assert caught.value.measured > TIGHT_THRESHOLD_T
    assert caught.value.request_no == 1, "1 往復目で踏む（回数の超過より前）"
    assert server.call_count == 0, "1 往復目の要求も送られない"


@pytest.mark.contract_if("IF-40")
def test_contract_monitor_on_outbound_raises_run_halted_over_the_cost_limit(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-40 例外系: **積み上がった費用**の超過でも `RunHalted`（`limit_cost`）。

    3 次元のうち最後の分岐。費用は応答（`on_inbound`）で積むので、超過が判るのは
    **次の送信の直前**である —— 1 往復させて上限を超える費用を積み、2 往復目の
    `on_outbound` で踏む。

    回数の上限を 10 に緩めてあるので `request_no == 2` は回数の分岐に当たらない。
    `kind` が `limit_requests` でないことを見て分岐を区別する。
    """
    server = fake_llm_server(make_llm_script(cost=ACCUMULATED_COST, turns=2))
    run_monitor.begin(MONITOR_KICKOFF_ID, CHEAP_LIMITS, MONITOR_THRESHOLD_T)
    _round_trip(server, llm_http_hook)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        with pytest.raises(RunHalted) as caught:
            llm_http_hook.on_outbound(request)

    assert caught.value.kind is FailureKind.limit_cost
    assert caught.value.kind is not FailureKind.limit_requests, "回数の分岐に落ちていない"
    assert caught.value.limit == CHEAP_LIMITS.max_cost_per_kickoff
    assert caught.value.measured == pytest.approx(ACCUMULATED_COST)
    assert caught.value.request_no == 2
    assert server.call_count == 1, "2 往復目の要求は送られない"


@pytest.mark.contract_if("IF-42")
def test_contract_monitor_aon_outbound_raises_run_halted_over_the_limit(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-42 例外系: 非同期版も同じ状態で `RunHalted` を送出する（FR-019）。"""
    server = fake_llm_server(make_llm_script(turns=2))
    run_monitor.begin(MONITOR_KICKOFF_ID, ONE_REQUEST_LIMITS, MONITOR_THRESHOLD_T)
    _round_trip(server, llm_http_hook)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        with pytest.raises(RunHalted) as caught:
            asyncio.run(llm_http_hook.aon_outbound(request))

    assert caught.value.kind is FailureKind.limit_requests
    assert server.call_count == 1


@pytest.mark.contract_if("IF-40")
def test_contract_monitor_run_halted_is_not_caught_by_except_exception(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-40: `RunHalted` は **`except Exception` では捕まらない**（`BaseException` 直系＝ Z-6）。

    送出側と捕捉側の間にあるライブラリの `except Exception` を素通りすることが Z-6 の要である。
    型の階層（静的な事実）と、実際に素通りすること（動的な事実）の**両方**を主張する。
    """
    assert issubclass(RunHalted, Exception) is False
    assert issubclass(RunHalted, BaseException) is True

    server = fake_llm_server(make_llm_script(turns=2))
    run_monitor.begin(MONITOR_KICKOFF_ID, ONE_REQUEST_LIMITS, MONITOR_THRESHOLD_T)
    _round_trip(server, llm_http_hook)

    with httpx.Client() as client:
        request = client.build_request(
            "POST", f"{server.base_url}/chat/completions", json=_payload()
        )
        with pytest.raises(RunHalted):
            try:
                llm_http_hook.on_outbound(request)
            except Exception:
                pytest.fail("RunHalted が中間の except Exception に捕まった（Z-6 が壊れている）")
