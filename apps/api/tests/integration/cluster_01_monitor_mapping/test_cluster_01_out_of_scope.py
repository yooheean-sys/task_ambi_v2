"""クラスタ 01 の構造化ログ・非同期経路・非実行（**IF に紐づかない観点行**）。§6 #9・#10・#11。

`CONTRACT_META = True` の理由（plan (v)・B-11）: #9 はログ、#10 は**通過 IF**（IF-42/43 の
主検証クラスタは 03 なので宣言しない＝ FR-010b）、#11 は**非実行**である。いずれも 01 の主検証
IF に紐づかない。**このファイルに `contract_if` を書かない**。

FR-032（配線の過不足が起動時に落ちること）の検査もここに置く —— 対応する §6 の行が存在しない
ため `01#1` に対応させた（判断は design-sync の B 表に記録する）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn
from _support.cluster_wiring import build_cluster_wiring
from _support.contract_stubs import CallLog, StubRunMonitor

from api.correlation import correlation_scope
from api.exceptions import DuplicateProviderError, UnsatisfiedContractError
from api.wiring import require
from common.interfaces.monitor import FailureKind, LLMHttpHook, RunHalted, RunMonitor
from common.interfaces.orchestrator import KickoffRunner, KickoffStatus
from common.interfaces.settings import LimitPolicy, SettingsReader
from common.kernel.enums import ToolName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_logs import CorrelationRecorder
    from _support.cluster_wiring import ClusterRuntime

    from api.wiring import ProtocolOverrides
    from common.interfaces.orchestrator import ExecutionContext

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

pytestmark = pytest.mark.requires_bd

TURN_COST = 0.01

HALTING_CALLS = 1
"""`_halting_script()` ＋ `max_requests=1` で**実際に送られる**往復数（2 往復目は送信前に停止）。"""

THRESHOLD_T = 1_000_000
"""#10 で `begin` へ渡す長さの閾値（**十分大きく**して回数だけで判定させる）。

値はテスト側が持つ（`from conftest import ...` を使わない＝ FR-041a・R-02）。
"""

ASYNC_KICKOFF_ID = "cluster01-async"
"""#10 がテスト側から打つ `begin` の識別子（orchestrator は走らせない）。"""

ASYNC_MAX_REQUESTS = 2
"""#10 の上限。**同期 1 回＋非同期 1 回で使い切り、3 回目で超える**ように選ぶ（T086）。

同期と非同期が別々の `RunState` に積む実装なら、3 回目（非同期の 2 回目）は上限に届かず
`RunHalted` が上がらない —— この値が「同じ器に積まれる」ことの観測点そのものである。
"""

ASYNC_MAX_COST = 1000.0
"""#10 では費用で止めない（回数だけで判定させる）。"""


def _halting_script() -> list[dict[str, object]]:
    """上限超過を誘発する 2 往復の台本（F 級の写像を起こし、monitor がログを出す）。"""
    first = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    first["tool_calls"] = [make_tool_call(1, ToolName.read_issue, {})]
    return [first, make_turn(cost=TURN_COST)]


def _completing_script() -> list[dict[str, object]]:
    """差し戻しを起こさずに 1 周で終わる 4 往復の台本。"""
    work_call = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    work_call["tool_calls"] = [
        make_tool_call(1, ToolName.complete_issue, {"reason": "クラスタ 01 の完了"})
    ]
    review_call = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    review_call["tool_calls"] = [
        make_tool_call(
            2, ToolName.submit_review, {"approved": True, "feedback": "承認", "files_read": []}
        )
    ]
    return [work_call, make_turn(cost=TURN_COST), review_call, make_turn(cost=TURN_COST)]


@pytest.mark.cluster_case("01#9")
def test_cluster_01_correlation_id_is_issued_at_the_direct_entry_and_reaches_both_logs(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
    correlation_recorder: CorrelationRecorder,
) -> None:
    """クラスタ 01 #9: Hook の計測ログと bd の監査ログに**同じ**相関 ID が載る。

    期待（§6 #9）: Hook の計測ログ・bd 呼び出しの監査ログ（P5D-006）に同じ correlation_id
    （憲章 §6）。**直接呼び出し入口でも発番される**。

    01 は HTTP を通らない（router を持つ MOD が有効 MOD に無い）ので `correlation_scope` で
    束縛する。相関 ID は `LogRecord` に載らず整形器が読むので、レコードが出た瞬間の束縛値を
    控える観測点を使う。
    """
    # 上限停止の経路で走らせる —— 正常終了では monitor も orchestrator も**ログを 1 行も出さない**
    # ので、貫通を観測できない（2026-09-01 に実測）。F 級の写像なら `monitor.hook.halted` と
    # `orchestrator` の記録が出る。
    runtime = cluster(_halting_script(), max_requests=1)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    seed_leaf(runtime)

    with correlation_scope() as correlation_id:
        result = runner.run(context)

    assert result.status is KickoffStatus.ok, result.status
    board_ids = correlation_recorder.correlation_ids("board")
    assert board_ids, "bd の監査ログが 1 行も出ていない"
    assert board_ids == {correlation_id}, f"bd 監査ログの相関 ID が貫通していない: {board_ids}"
    orchestrator_ids = correlation_recorder.correlation_ids("orchestrator")
    monitor_ids = correlation_recorder.correlation_ids("monitor")
    observed = orchestrator_ids | monitor_ids
    assert observed, "orchestrator／monitor のログが 1 行も出ていない"
    assert observed == {correlation_id}, (
        f"計測ログの相関 ID が貫通していない: {observed} / 期待={correlation_id}"
    )
    assert runtime.llm.call_count == HALTING_CALLS, (
        "往復数が台本・上限と食い違う（ログの本数だけ合わせても鎖が違う）:"
        f" 実際={runtime.llm.call_count} 期待={HALTING_CALLS}"
    )


@pytest.mark.cluster_case("01#10")
@pytest.mark.cluster_conditional("CX-01")
def test_cluster_01_async_hook_path_uses_the_same_limits(
    cluster: Callable[..., ClusterRuntime],
) -> None:
    """クラスタ 01 #10: Hook の非同期経路でも**同じ器に積まれ**同じ上限判定が効く。

    期待（§6 #10）: crewai／litellm が async 経路を使う構成でも `aon_outbound` で同じ上限判定が
    効く（装着の両経路）。

    **通過 IF なので宣言しない**（FR-010b）—— IF-42／IF-43 の主検証クラスタは 03 である。

    **条件付き（`CX-01`）**: 非同期の差し込み点が実装に無い構成では成立しない。条件の成否を
    テスト内で判定し、不成立なら `pytest.skip(理由)` で出力に残す（FR-022。人間の目視に
    委ねない）。⚠ 条件の**定義**は §6 #10 の「crewai／litellm が async 経路を使う構成か」では
    なく「Hook が `aon_*` を実装しているか」に狭めてある（差し替えの記録は design-sync の (4)）。

    上限は `max_requests=2` にし、**同期 1 回 → 非同期 1 回 → 非同期 1 回**の順に口を叩く。
    3 回目で `RunHalted` が上がるのは、同期と非同期が**同じ `RunState` に積んでいる**ときだけ
    である（別々に数えるなら 3 回目は非同期の 2 回目にすぎず上限に届かない）。`test_…_03_…` の
    同型テストが非同期だけで見るのに対し、ここは**両経路が 1 つの器を共有する**ところまで見る。

    偽サーバへは 1 度も送らない（口を直接叩く）—— `call_count == 0` がその証拠である。
    """
    runtime = cluster(_completing_script())
    llm_http_hook: LLMHttpHook = require(runtime.wiring.provided, LLMHttpHook)
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)

    if not hasattr(llm_http_hook, "aon_outbound") or not hasattr(llm_http_hook, "aon_inbound"):
        pytest.skip("CX-01: 非同期経路の差し込み点が実装に無い（同期経路のみの構成）")

    import asyncio  # noqa: PLC0415  # 条件が成立したときだけ要る

    import httpx  # noqa: PLC0415

    run_monitor.begin(
        ASYNC_KICKOFF_ID,
        LimitPolicy(
            scope=ASYNC_KICKOFF_ID,
            max_requests_per_kickoff=ASYNC_MAX_REQUESTS,
            max_cost_per_kickoff=ASYNC_MAX_COST,
        ),
        THRESHOLD_T,
    )
    request = httpx.Request("POST", f"{runtime.llm.base_url}/chat/completions", json={})

    assert llm_http_hook.on_outbound(request) is request, "同期の口が要求を書き換えている"
    assert asyncio.run(llm_http_hook.aon_outbound(request)) is request, (
        "非同期の口が要求を書き換えている"
    )
    with pytest.raises(RunHalted) as caught:
        asyncio.run(llm_http_hook.aon_outbound(request))

    assert caught.value.kind is FailureKind.limit_requests, (
        f"止まった理由が回数の上限でない: {caught.value.kind}"
    )
    assert caught.value.request_no == ASYNC_MAX_REQUESTS + 1, (
        "同期と非同期が別々の器に数えている（同じ RunState に積まれていない）:"
        f" 何回目={caught.value.request_no} 期待={ASYNC_MAX_REQUESTS + 1}"
    )
    assert runtime.llm.call_count == 0, (
        f"口を直接叩いたのに偽サーバへ送られている: {runtime.llm.call_count}"
    )


@pytest.mark.cluster_case("01#11")
@pytest.mark.cluster_not_exercised("NX-01")
def test_cluster_01_openrouter_path_is_not_exercised() -> None:
    """クラスタ 01 #11: openrouter 経路は**踏まない**（`NX-01`）。

    期待（§6 #11）: 偽サーバでは模擬しない。M1 実測（P5D-013/030）に送る（出典「D14 §6-5」）。

    **行を消さずに非実行として宣言する**（規約 §7。非実行は 01 #11 と 04 #17 の 2 件のみで、
    増やしてはならない）。ここで確かめるのは「01 の土台が openrouter を使っていない」ことだけ
    である —— 偽 LLM サーバは OpenAI 互換の 1 形しか話さないので、`provider_kind` を
    `openrouter` にしても実物の差（追加ヘッダ・課金計算）は再現できない。
    """
    from common.interfaces.settings import ProviderKind  # noqa: PLC0415  # 非実行の宣言だけに使う

    assert ProviderKind.openrouter != ProviderKind.openai_compatible, (
        "前提: 2 つの提供元種別は別物である（片方しか偽サーバで模擬できない）"
    )


@pytest.mark.cluster_case("01#1")
def test_cluster_01_wiring_rejects_missing_and_duplicated_overrides(
    cluster: Callable[..., ClusterRuntime],
    stub_overrides: ProtocolOverrides,
) -> None:
    """FR-032: 8 件の override は 1 件でも欠けても余っても**起動時に**落ちる（SC-004）。

    対応する §6 の行が無いので `01#1` に対応させた（判断は design-sync に記録）。

    3 通りを踏む。

    - **正しい 8 件**なら組める（`build_cluster_wiring` が `Wiring` を返す）。override 集合は
      `stub_overrides` fixture から取る —— `Wiring.provided` は override と有効 MOD の提供分を
      **合わせた**写像なので、そこから override だけを取り出すことはできない。
    - **1 件抜く**（`SettingsReader`）→ `UnsatisfiedContractError`（要求元は orchestrator）。
    - **有効 MOD が提供する契約を足す**（`RunMonitor` —— 01 では monitor が実物として提供）→
      `DuplicateProviderError`。
    """
    runtime = cluster([make_turn()])
    assert set(runtime.wiring.modules) == {
        "board",
        "monitor",
        "executor",
        "orchestrator",
    }, runtime.wiring.modules

    reduced = {key: value for key, value in stub_overrides.items() if key is not SettingsReader}
    with pytest.raises(UnsatisfiedContractError) as unsatisfied:
        build_cluster_wiring("01", reduced)
    assert "SettingsReader" in str(unsatisfied.value), unsatisfied.value
    assert "orchestrator" in str(unsatisfied.value), unsatisfied.value

    with pytest.raises(DuplicateProviderError) as duplicated:
        build_cluster_wiring("01", {**stub_overrides, RunMonitor: StubRunMonitor(CallLog())})
    assert "RunMonitor" in str(duplicated.value), duplicated.value
    assert "monitor" in str(duplicated.value), duplicated.value
