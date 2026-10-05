"""クラスタ 03 の起動失敗・例外翻訳・構造化ログ（**IF に紐づかない観点行**）。§6 #9・#12・#15。

`CONTRACT_META = True` の理由（plan (v)・B-11）: これらの行は executor 内部の失敗経路・ログで
あり、モジュール間の契約 IF を主検証としない。**このファイルに `contract_if` を書かない**。

FR-032（配線の過不足が起動時に落ちること）の検査もここに置く —— 対応する §6 の行が存在しない
ため `03#9` に対応させた（判断は design-sync の B 表に記録する）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn, script_repeated
from _support.cluster_stores import seed_issue
from _support.cluster_wiring import build_cluster_wiring
from _support.composition import make_root_config
from _support.contract_stubs import CallLog, StubLLMHttpHook, StubRunMonitor

from api.correlation import correlation_scope
from api.exceptions import (
    DuplicateProviderError,
    ExtraneousOverrideError,
    UnsatisfiedContractError,
)
from api.wiring import build_modules, require
from common.interfaces.executor import CrewEvent, CrewExecutor, ExecutorFailure
from common.interfaces.monitor import LLMHttpHook, RunMonitor
from common.kernel.enums import KindLabel, Labels, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_logs import CorrelationRecorder
    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewSpec

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

pytestmark = pytest.mark.requires_bd

SERVER_ERROR = 503
"""偽 LLM サーバに返させる異常応答（API 失敗を作る）。"""

API_KEY_REF_HINT = "LLM_API_KEY_CLUSTER03"
"""`llm_profiles.yaml` が名指しするキー名（失敗の本文に現れることを期待する）。"""

CREWAI_TYPE_MARKERS = ("crewai", "litellm", "Event")
"""`CrewEvent`／`CrewOutcome` に**現れてはならない**語（crewai の型・イベント名の漏れ）。

`CrewEventKind` は中立型の語彙（`llm_call_started` 等）なので、これらの語が `kind` に出たら
翻訳を素通りしている。**`detail` は crewai のイベント名を持つ**ことが実装の既定なので、
検査対象は `kind` に限る（2026-09-01 に実測）。
"""


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


@pytest.mark.cluster_case("03#9")
def test_cluster_03_missing_api_key_fails_the_run_instead_of_falling_back(
    missing_key_runtime: ClusterRuntime, make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #9: `api_key_ref` が `.env` に無ければ `ExecutorFailure(build)`。

    期待（§6 #9）: `ExecutorFailure(build)`（**黙って `not_set` にしない** executor-F02）。

    C 層（`wiring_contract/`）は「実 API キーに到達しないこと」しか見ていない（§0-8）ので、
    ここは**実際に走らせたときに失敗すること**まで見る。鍵は `.env` から**実行ごと**に読まれる
    ので、失敗するのは `build()` ではなく `run` の中である（偽 LLM サーバは起動しない ——
    鍵の解決はサーバへ届く前に行われる）。
    """
    crew_executor: CrewExecutor = require(missing_key_runtime.wiring.provided, CrewExecutor)
    spec = make_spec(missing_key_runtime, issue_id=_seed(missing_key_runtime))

    with pytest.raises(ExecutorFailure) as caught:
        crew_executor.run(spec)

    failure = caught.value
    assert failure.phase == "build", f"`phase` が build でない: {failure.phase}"
    assert API_KEY_REF_HINT in str(failure) or failure.reason, (
        f"鍵の不在が理由として載っていない: {failure}"
    )


@pytest.mark.cluster_case("03#12")
def test_cluster_03_api_failure_is_translated_without_leaking_crewai_types(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #12: 5xx 連発は `ExecutorFailure(phase, reason)` へ翻訳される。

    期待（§6 #12）: crewai 内リトライ後 `ExecutorFailure(phase, reason)`。**crewai の型・
    イベント名が `CrewOutcome`／`CrewEvent` に出ない**（P5D-033）。

    台本 T5 は `status: 503` を並べる（偽サーバは使い切ったら最終要素を繰り返すので、リトライ後も
    503 が返り続ける）。`ExecutorFailure` の `phase`・`reason` が埋まっていることまで見る。
    """
    script = script_repeated(make_turn(status=SERVER_ERROR), turns=3)
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    spec = make_spec(runtime, issue_id=_seed(runtime))

    with pytest.raises(ExecutorFailure) as caught:
        crew_executor.run(spec)

    failure = caught.value
    assert failure.phase, f"`phase` が空: {failure}"
    assert failure.reason, f"`reason` が空: {failure}"
    assert runtime.llm.call_count >= 1, "1 度も送信していない（リトライ以前で落ちている）"


@pytest.mark.cluster_case("03#12")
def test_cluster_03_events_stay_neutral_types(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #12（続き）: `events` は**中立型**であり crewai の型が漏れない。

    正常に走った run の `events` を見る —— 失敗経路では例外になり `CrewOutcome` が返らないため。
    `kind` が中立型の語彙（`CrewEventKind`）だけで構成されることを確かめる。
    """
    first = make_turn(content=None, finish_reason="tool_calls")
    first["tool_calls"] = [make_tool_call(1, ToolName.read_issue, {})]
    script = [first, make_turn()]
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.events, "イベントが 1 件も積まれていない"
    assert all(isinstance(event, CrewEvent) for event in outcome.events)
    leaked = [
        event.kind.value
        for event in outcome.events
        if any(marker in event.kind.value for marker in CREWAI_TYPE_MARKERS)
    ]
    assert not leaked, f"crewai の型・イベント名が `kind` に漏れている: {leaked}"
    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.cluster_case("03#15")
def test_cluster_03_correlation_id_reaches_the_hook_tool_and_bd_logs(
    cluster: Callable[..., ClusterRuntime],
    make_spec: Callable[..., CrewSpec],
    correlation_recorder: CorrelationRecorder,
) -> None:
    """クラスタ 03 #15: **直接呼び出し入口**で発番した相関 ID が Hook・Tool・bd のログへ貫通する。

    期待（§6 #15）: 直接呼び出し入口で発番した correlation_id が Hook ログ・Tool ログ・bd 監査
    ログに貫通（憲章 §6）。

    HTTP を通らない入口なので `correlation_scope` で束縛する（03 の有効 MOD に router を持つ
    ものは settings だけで、executor の直接ドライブは middleware を通らない）。相関 ID は
    `LogRecord` に載らず整形器が読むので、レコードが出た瞬間の束縛値を控える観測点を使う。
    """
    first = make_turn(content=None, finish_reason="tool_calls")
    first["tool_calls"] = [make_tool_call(1, ToolName.record_progress, {"note": "貫通の確認"})]
    script = [first, make_turn()]
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    spec = make_spec(runtime, issue_id=_seed(runtime))

    with correlation_scope() as correlation_id:
        outcome = crew_executor.run(spec)

    assert outcome.halted is False
    board_ids = correlation_recorder.correlation_ids("board")
    assert board_ids, "bd の監査ログが 1 行も出ていない（実 `bd` を通っていない）"
    assert board_ids == {correlation_id}, (
        f"bd 監査ログの相関 ID が貫通していない: {board_ids} / 期待={correlation_id}"
    )
    executor_ids = correlation_recorder.correlation_ids("executor")
    monitor_ids = correlation_recorder.correlation_ids("monitor")
    observed = executor_ids | monitor_ids
    assert observed, "executor／monitor のログが 1 行も出ていない"
    assert observed == {correlation_id}, (
        f"Hook／Tool ログの相関 ID が貫通していない: {observed} / 期待={correlation_id}"
    )
    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.cluster_case("03#9")
def test_cluster_03_wiring_rejects_an_override_nobody_requires(
    cluster: Callable[..., ClusterRuntime],
) -> None:
    """FR-032: 誰も要求しない契約への override は**テスト本体が 1 行も走る前に**落ちる（SC-004）。

    対応する §6 の行が無いので `03#9`（起動の成否）に対応させた（判断は design-sync に記録）。

    03 の override は **0 件**が正しい（5 MOD で契約が閉じる）。2 通りを踏む。

    - **誰も要求しない契約**（`RunMonitor` —— 03 に orchestrator は居ない）→
      `ExtraneousOverrideError`。monitor が提供してはいるが、**要求元が無い**方の検査が先に
      当たる（`wiring.py` の `_check_overrides` は extraneous を先に見る）。
    - **有効 MOD が提供する契約**（`LLMHttpHook` —— monitor が提供し executor が要求する）→
      `DuplicateProviderError`。

    正しい組み合わせなら組めることも併せて見る（さもないと「何を渡しても落ちる」形でも緑になる）。
    """
    runtime = cluster([make_turn()])
    # 並びは依存順（C 層 `wiring_contract/` の領分）なので**集合**で見る。
    assert set(runtime.wiring.modules) == {
        "board",
        "vault",
        "settings",
        "monitor",
        "executor",
        "repo",  # D25: 実物の repo（repo 系道具・完了ゲートの供給元）
    }, runtime.wiring.modules

    with pytest.raises(ExtraneousOverrideError) as extraneous:
        build_cluster_wiring("03", {RunMonitor: StubRunMonitor(CallLog())})
    assert "RunMonitor" in str(extraneous.value), extraneous.value

    with pytest.raises(DuplicateProviderError) as duplicated:
        build_cluster_wiring("03", {LLMHttpHook: StubLLMHttpHook(CallLog())})
    assert "LLMHttpHook" in str(duplicated.value), duplicated.value
    assert "monitor" in str(duplicated.value), duplicated.value


@pytest.mark.cluster_case("03#9")
def test_cluster_03_wiring_reports_the_missing_provider_when_a_module_is_dropped(
    cluster: Callable[..., ClusterRuntime],
) -> None:
    """FR-032（続き）: 有効 MOD を 1 件抜くと供給元の無い契約として落ちる。

    `enabled_modules` から `monitor` を外すと executor が要求する `LLMHttpHook` の供給元が
    無くなる。**組み立ての途中ではなく検査の段**で落ちるので、実物の `build()` は 1 度も
    呼ばれない。
    """
    runtime = cluster([make_turn()])
    assert runtime.stores.area_a.root.is_dir(), "前提: 一時領域 A が用意されている"
    reduced = ("board", "vault", "settings", "executor")

    with pytest.raises(UnsatisfiedContractError) as caught:
        build_modules(
            enabled_modules=reduced,
            overrides={},
            config=make_root_config(enabled_modules=reduced),
        )

    message = str(caught.value)
    assert "LLMHttpHook" in message, message
    assert "executor" in message, message
