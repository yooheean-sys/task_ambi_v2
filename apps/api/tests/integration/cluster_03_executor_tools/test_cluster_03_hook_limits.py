"""クラスタ 03 の Hook（IF-40〜43）。§6 #10・#11 ×2・#14 ×2。

**実 monitor の Hook × 実 executor の LLM 往復**。Hook は httpx の**transport 層**の差し込み点
なので、`MockTransport` では代替できない（規約 §4）—— 偽 LLM サーバとの**実 HTTP 往復**が要る。

`begin` は**テストが呼ぶ**（§6 #11 が明記。03 の有効 MOD に orchestrator は無い）。上限は
実 settings の `get_limits` が返す実値を使い、テストへ直書きしない。

`RunHalted` は `BaseException` 直系なので、litellm／crewai の `except Exception` を**素通り**
する（Z-6）。executor の出口までが 03 の鎖である（盤面の確認読取まで伸びるのは 01＝ §0-3a）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn
from _support.cluster_stores import seed_issue

from api.wiring import require
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import FailureKind, LLMHttpHook, RunMonitor
from common.interfaces.settings import LimitPolicy, SettingsReader
from common.kernel.enums import KindLabel, Labels, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewSpec

pytestmark = pytest.mark.requires_bd

KICKOFF_ID = "cluster03-kickoff"
"""`begin` に渡す識別子（テストが発番する）。"""

THRESHOLD_T = 1_000_000
"""長さの閾値。**十分大きく**して `ctx_exceeded` を誘発しない（03 が見るのは回数と費用）。"""

TURN_COST = 0.25
"""1 往復あたりの `usage.cost`（費用の累積を作る）。"""


def _two_turn_script() -> list[dict[str, object]]:
    """**2 往復**する台本（1 往復目で道具を呼ばせ、2 往復目で確定する）。

    ⚠ 正常応答を並べただけでは往復は増えない —— 既定の本文は `Final Answer:` 形なので実 CrewAI
    が **1 往復で確定する**（2026-09-01 に実測）。回数・費用の上限は「2 往復目の**送信前**」に
    効くので、道具呼び出しを 1 つ挟んで往復を作る（副作用の無い `read_issue` を使う）。
    """
    first = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    first["tool_calls"] = [make_tool_call(1, ToolName.read_issue, {})]
    return [first, make_turn(cost=TURN_COST)]


def _seed(runtime: ClusterRuntime) -> str:
    """実 `bd` に課題を 1 件仕込み、その issue_id を返す。"""
    return seed_issue(
        runtime.stores.beads,
        title="クラスタ 03 の課題",
        labels=[
            Labels.project(runtime.stores.project_key),
            Labels.kind(KindLabel.child),
            Labels.persona(runtime.stores.persona),
        ],
    )


def _limits(runtime: ClusterRuntime) -> LimitPolicy:
    """実 settings が解決した上限（`global/limits.yaml` の ⑤ 段）。"""
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    return settings_reader.get_limits(runtime.stores.project_key)


@pytest.mark.contract_if("IF-40")
@pytest.mark.cluster_case("03#10")
def test_cluster_03_outbound_hook_halts_the_run_when_the_request_limit_is_exceeded(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #10 / IF-40: 上限超過は `on_outbound` が `RunHalted` で止める（送信しない）。

    期待（§6 #10）: `on_outbound` で `RunHalted`（送信しない）→ litellm／crewai のリトライを
    **素通り**（Z-6）→ `CrewOutcome.halted=True`・`events`。

    台本 T4 は道具呼び出しを 1 つ挟んで **2 往復**する（正常応答を並べただけでは実 CrewAI が
    1 往復で確定してしまい、上限に達しない＝ 2026-09-01 の実測）。
    **上限は実 settings の値**を使い、テストへ直書きしない。`RunHalted` は `BaseException`
    直系なので、途中のライブラリが握り潰さずに executor の出口まで届く。
    """
    script = _two_turn_script()
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)
    limits = _limits(runtime)
    # 回数の上限を **1** に絞った写しで計測を始める（`model_copy` で新しい値を作る＝憲章 §2）。
    run_monitor.begin(
        KICKOFF_ID,
        limits.model_copy(update={"max_requests_per_kickoff": 1}),
        THRESHOLD_T,
    )

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.halted is True, "上限超過なのに停止していない"
    assert outcome.halted_reason is FailureKind.limit_requests, outcome.halted_reason
    assert runtime.llm.call_count == 1, (
        "上限に達した往復は**送信されない**（送ってから判定していないか）:"
        f" 実際={runtime.llm.call_count}"
    )


@pytest.mark.contract_if("IF-40")
@pytest.mark.cluster_case("03#11")
def test_cluster_03_outbound_hook_counts_requests_and_cost_against_the_run(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #11 / IF-40: `on_outbound` の回数・コスト計測が進行中の kickoff に帰属する。

    期待（§6 #11）: `on_outbound` の回数・コスト計測が RunState に帰属する（`begin` はテストが
    呼ぶ）。

    `begin` を**呼んだ場合と呼ばない場合**を対にして見る —— 呼ばずに走らせると帰属先が無く
    `monitor.hook.unattributed` になり、上限判定そのものが効かない。したがって「上限 1 で
    止まる／`begin` 無しでは止まらない」の差が、帰属が効いていることの観測になる。

    2 つの土台を別々に組む —— 同じ偽サーバを 2 回使うと**台本を使い切って最終要素が繰り返され**、
    2 回目は 1 往復で確定してしまう（R-04 の罠）。
    """
    unattributed_runtime = cluster(_two_turn_script())
    unattributed_executor: CrewExecutor = require(
        unattributed_runtime.wiring.provided, CrewExecutor
    )
    unattributed = unattributed_executor.run(
        make_spec(unattributed_runtime, issue_id=_seed(unattributed_runtime))
    )
    assert unattributed.halted is False, "帰属していないのに上限が効いている"
    assert unattributed_runtime.llm.call_count == 2, (
        f"帰属なしの往復数が台本と食い違う: 実際={unattributed_runtime.llm.call_count}"
    )

    runtime = cluster(_two_turn_script())
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)
    limits = _limits(runtime)
    run_monitor.begin(
        KICKOFF_ID,
        limits.model_copy(update={"max_requests_per_kickoff": 1}),
        THRESHOLD_T,
    )

    attributed = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert attributed.halted is True, "`begin` の後なのに計測が帰属していない"
    assert attributed.halted_reason is FailureKind.limit_requests, attributed.halted_reason
    assert runtime.llm.call_count == 1, (
        "上限に達した往復が送信されている（`on_outbound` が送信前に判定していない）:"
        f" 実際={runtime.llm.call_count}"
    )


@pytest.mark.contract_if("IF-41")
@pytest.mark.cluster_case("03#11")
def test_cluster_03_inbound_hook_observes_usage_and_finish_reason(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #11 / IF-41: `on_inbound` が応答の `usage`・`finish_reason` を観測する。

    期待（§6 #11）: `on_inbound` の usage・finish_reason 観測が RunState に帰属する。

    観測できていることは**費用の上限**で見る —— 回数の上限を十分大きく取り、費用だけを
    小さくすれば、`usage.cost` を読めた場合にだけ `limit_cost` で止まる。応答を読まない実装は
    ここで止まらない（回数では止まらないため）。
    """
    script = _two_turn_script()
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)
    limits = _limits(runtime)
    run_monitor.begin(
        KICKOFF_ID,
        limits.model_copy(
            update={"max_requests_per_kickoff": 100, "max_cost_per_kickoff": TURN_COST / 2}
        ),
        THRESHOLD_T,
    )

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.halted is True, "費用の上限が効いていない（応答の usage を読んでいない疑い）"
    assert outcome.halted_reason is FailureKind.limit_cost, (
        f"費用ではなく別の理由で止まっている: {outcome.halted_reason}"
    )
    assert runtime.llm.call_count >= 1, "1 往復も送っていない（費用が積まれる前に止まっている）"


@pytest.mark.contract_if("IF-42")
@pytest.mark.cluster_case("03#14")
@pytest.mark.cluster_conditional("CX-01")
def test_cluster_03_async_outbound_hook_applies_the_same_limits(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #14 / IF-42: 非同期経路 `aon_outbound` でも同じ上限判定が効く。

    期待（§6 #14）: crewai／litellm が async 経路を使う構成でも同じ判定が効く（装着の両経路）。

    **条件付き（`CX-01`）** —— 実 CrewAI が同期経路を使う構成では、非同期の差し込み点は
    kickoff からは踏まれない。条件の成否を**テスト内で判定**し、不成立なら `pytest.skip` で
    理由を出力に残す（FR-022。人間の目視に委ねない）。

    判定は「1 度 kickoff を走らせたあと、非同期の口が RunState に帰属したまま同じ判定を返すか」
    で行う。`aon_outbound` は要求側に非同期 I/O が無いので同期版をそのまま呼ぶ実装だが、
    **装着されているか**は構成しだいなので、ここでは口が同じ判定を返すことまでを見る。
    """
    script = _two_turn_script()
    runtime = cluster(script)
    llm_http_hook: LLMHttpHook = require(runtime.wiring.provided, LLMHttpHook)
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)
    limits = _limits(runtime)
    run_monitor.begin(
        KICKOFF_ID, limits.model_copy(update={"max_requests_per_kickoff": 1}), THRESHOLD_T
    )

    if not hasattr(llm_http_hook, "aon_outbound"):
        pytest.skip("CX-01: 非同期経路の差し込み点が実装に無い（同期経路のみの構成）")

    import asyncio  # noqa: PLC0415  # 条件が成立したときだけ要る

    import httpx  # noqa: PLC0415

    request = httpx.Request("POST", f"{runtime.llm.base_url}/chat/completions", json={})
    first = asyncio.run(llm_http_hook.aon_outbound(request))
    assert first is request, "`aon_outbound` は要求を書き換えない"

    from common.interfaces.monitor import RunHalted  # noqa: PLC0415

    with pytest.raises(RunHalted) as caught:
        asyncio.run(llm_http_hook.aon_outbound(request))
    assert caught.value.kind is FailureKind.limit_requests, caught.value.kind


@pytest.mark.contract_if("IF-43")
@pytest.mark.cluster_case("03#14")
@pytest.mark.cluster_conditional("CX-01")
def test_cluster_03_async_inbound_hook_observes_the_same_usage(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #14 / IF-43: 非同期経路 `aon_inbound` でも同じ観測が行われる。

    期待（§6 #14）: 同上（装着を両経路で確認）。

    **条件付き（`CX-01`）**。同期版と違うのは本文の取り出し方だけで、観測は同じヘルパを通る
    実装である。ここでは「非同期の口を通した費用が同じ RunState に積まれ、次の送信判定で
    `limit_cost` として現れる」ことで観測の同一性を見る。
    """
    script = _two_turn_script()
    runtime = cluster(script)
    llm_http_hook: LLMHttpHook = require(runtime.wiring.provided, LLMHttpHook)
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)
    limits = _limits(runtime)
    run_monitor.begin(
        KICKOFF_ID,
        limits.model_copy(
            update={"max_requests_per_kickoff": 100, "max_cost_per_kickoff": TURN_COST / 2}
        ),
        THRESHOLD_T,
    )

    if not hasattr(llm_http_hook, "aon_inbound"):
        pytest.skip("CX-01: 非同期経路の差し込み点が実装に無い（同期経路のみの構成）")

    import asyncio  # noqa: PLC0415  # 条件が成立したときだけ要る
    import json  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from common.interfaces.monitor import RunHalted  # noqa: PLC0415

    body = json.dumps(
        {
            "choices": [{"index": 0, "message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": make_turn(cost=TURN_COST)["usage"],
        }
    ).encode("utf-8")
    response = httpx.Response(200, content=body, request=httpx.Request("POST", "http://x/y"))
    observed = asyncio.run(llm_http_hook.aon_inbound(response))
    assert observed is response, "`aon_inbound` は応答を書き換えない"

    request = httpx.Request("POST", f"{runtime.llm.base_url}/chat/completions", json={})
    with pytest.raises(RunHalted) as caught:
        llm_http_hook.on_outbound(request)
    assert caught.value.kind is FailureKind.limit_cost, (
        f"非同期経路で観測した費用が同じ RunState に積まれていない: {caught.value.kind}"
    )
