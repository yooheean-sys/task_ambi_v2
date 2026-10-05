"""クラスタ 04 の HTTP 入口・構造化ログ・非実行（**IF に紐づかない観点行**）。§6 #1 ×3・#16・#17。

`CONTRACT_META = True` の理由（plan (v)・B-11）: #1 は HTTP の口の振る舞い、#16 はログ、#17 は
**非実行**である。いずれも 04 の主検証 IF に紐づかない。**このファイルに `contract_if` を
書かない**。

**HTTP と直接ドライブを 1 つのテストで混ぜない**（R-07）—— `create_app` は `Wiring` を公開
しないので別インスタンスになる。single-flight（#1）は **HTTP だけ**で観測する。

FR-032（配線の過不足）と FR-021a（非実行の根拠を実配線から確認する）もここに置く。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from _support.cluster_doubles import Gate, GatedCrewExecutor
from _support.cluster_wiring import build_cluster_wiring, cluster_modules
from _support.contract_stubs import CallLog, StubCrewExecutor

from api.correlation import correlation_scope
from api.exceptions import DuplicateProviderError, UnsatisfiedContractError
from api.wiring import require
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import RunMonitor
from common.kernel.enums import KindLabel
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_logs import CorrelationRecorder
    from _support.cluster_wiring import ClusterRuntime
    from _support.composition import HttpClient

    from api.wiring import ProtocolOverrides
    from common.interfaces.executor import CrewOutcome
    from common.interfaces.orchestrator import ExecutionContext

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

pytestmark = pytest.mark.requires_bd

KICKOFFS_PATH = "/kickoffs"
CORRELATION_HEADER = "X-Correlation-ID"
HTTP_ACCEPTED = 202
HTTP_CONFLICT = 409
HTTP_NOT_FOUND = 404

COMPLETION_TIMEOUT_SEC = 30.0
"""完了待ちの上限（超えたら待ち直さずに失敗する＝憲章 §10）。"""

COMPLETION_STEP_SEC = 0.05
"""完了待ちポーリングの刻み。"""


def _wait_for_kickoff_completion(http: HttpClient, kickoff_id: str) -> None:
    """`GET /kickoffs/{id}` の上限つきポーリングで完走を待ってから `with` を出る（T095）。

    `POST /kickoffs` のワーカーは daemon スレッドで join の口が無く、orchestrator に lifespan も
    無い（CD-09）。待たずに `with` を出ると、`stores` teardown の `remove_beads`（rmtree）と
    ワーカーの実 `bd` 呼び出しが競合しうる（`_drive_background` が全例外を握るため赤にならず
    露見しない）。ルートロガ観測点（`correlation_recorder`）への残存ログ混入の経路も同じ穴で
    ある。05 が lifespan の `shutdown(wait=True)` で回収しているのと同じ規範（規約 §9
    「スレッドを起こしたテストは必ず回収してから終わる」）を、lifespan の無い 04 では記録の
    `status` の観測で満たす。固定 `sleep` に依存しない（上限つきポーリング）。
    """
    deadline = time.monotonic() + COMPLETION_TIMEOUT_SEC
    while time.monotonic() < deadline:
        record = http.get(f"{KICKOFFS_PATH}/{kickoff_id}")
        if record.status_code == 200 and record.json()["status"] != "running":
            return
        time.sleep(COMPLETION_STEP_SEC)
    msg = f"kickoff {kickoff_id} が {COMPLETION_TIMEOUT_SEC} 秒以内に完了しなかった"
    raise AssertionError(msg)


@pytest.mark.cluster_case("04#1")
def test_cluster_04_second_kickoff_is_rejected_while_one_is_running(
    cluster_client: Callable[..., HttpClient],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    call_log: CallLog,
) -> None:
    """クラスタ 04 #1: 進行中に `POST /kickoffs` を投げると **409** で拒否される。

    期待（§6 #1）: 409（`KickoffBusy`）。**本文の欄名は §6 と違う**（下の実測を参照）。

    `POST /kickoffs` は `start_background` で**待たずに 202** を返すので、1 本目を保持しないと
    2 本目が 409 を踏めない（R-07）。待機ラッパ（`GatedCrewExecutor`）で 1 本目を
    `CrewExecutor.run` の中に留めてから 2 本目を投げる。**待ちには上限がある**（既定 10 秒）。

    HTTP だけで観測する —— `create_app` は `Wiring` を公開しないので、直接ドライブと混ぜると
    別インスタンスの single-flight を見てしまう。

    解放後は 1 本目の**完走を待ってから** `with` を出る（ワーカーは daemon で join の口が
    無い。`_wait_for_kickoff_completion` の docstring を参照＝ T095）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent, title="保持される課題")
    gate = Gate()
    inner: CrewExecutor = StubCrewExecutor(call_log, outcome=make_crew_outcome())
    gated: CrewExecutor = GatedCrewExecutor(inner, gate)

    with cluster_client(executor=gated) as http:
        first = http.post(KICKOFFS_PATH, json={})
        assert first.status_code == HTTP_ACCEPTED, first.text
        gate.wait_entered(what="1 本目の `CrewExecutor.run`")

        second = http.post(KICKOFFS_PATH, json={})

        assert second.status_code == HTTP_CONFLICT, second.text
        body = second.json()
        # ⚠ 本文は `{detail, current_kickoff_id}` である（2026-09-01 に実測）。§6 #1 が書く
        # `{status: rejected, reason: busy}` とは**欄の名前が違う** —— 実装（`KickoffBusyDetail`）
        # を正とし、食い違いは design-sync の B 表に記録する。
        assert body.get("current_kickoff_id"), f"実行中の kickoff_id が載っていない: {body}"
        assert "kickoff" in body.get("detail", ""), f"理由が載っていない: {body}"
        gate.release()
        _wait_for_kickoff_completion(http, first.json()["kickoff_id"])


@pytest.mark.cluster_case("04#1")
def test_cluster_04_current_kickoff_is_visible_while_running(
    cluster_client: Callable[..., HttpClient],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    call_log: CallLog,
) -> None:
    """クラスタ 04 #1: 進行中は `GET /kickoffs/current` が running を見せる。

    期待（§6 #1）: `GET /kickoffs/current` は running。

    同じく待機ラッパで 1 本目を保持したまま問い合わせる。解放後は完走を待ってから `with` を
    出る（T095）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent, title="進行中に見える課題")
    gate = Gate()
    inner: CrewExecutor = StubCrewExecutor(call_log, outcome=make_crew_outcome())
    gated: CrewExecutor = GatedCrewExecutor(inner, gate)

    with cluster_client(executor=gated) as http:
        accepted = http.post(KICKOFFS_PATH, json={})
        assert accepted.status_code == HTTP_ACCEPTED, accepted.text
        gate.wait_entered(what="1 本目の `CrewExecutor.run`")

        current = http.get(f"{KICKOFFS_PATH}/current")

        assert current.status_code == 200, current.text
        assert current.json()["running"] is True, f"進行中の kickoff が見えない: {current.json()}"
        gate.release()
        _wait_for_kickoff_completion(http, accepted.json()["kickoff_id"])


@pytest.mark.cluster_case("04#1")
def test_cluster_04_unknown_kickoff_id_is_not_found(
    cluster_client: Callable[..., HttpClient], make_crew_outcome: Callable[..., CrewOutcome]
) -> None:
    """クラスタ 04 #1: 知らない kickoff_id は **404**（直近 N 件のインメモリ記録＝ P5D-035）。

    期待（§6 #1）: 終了後 `GET /kickoffs/{id}` で結果（不明は 404）。
    """
    with cluster_client([make_crew_outcome()]) as http:
        response = http.get(f"{KICKOFFS_PATH}/no-such-kickoff")

    assert response.status_code == HTTP_NOT_FOUND, response.text


@pytest.mark.cluster_case("04#16")
def test_cluster_04_correlation_id_reaches_board_settings_and_vault_logs(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    correlation_recorder: CorrelationRecorder,
) -> None:
    """クラスタ 04 #16: 直接呼び出し入口で発番した相関 ID が board のログへ貫通する。

    期待（§6 #16）: HTTP 入口（middleware）と直接呼び出し（発番）の両方で correlation_id が
    board・settings・vault の呼び出しログに貫通（憲章 §6）。

    ここは**直接呼び出し**側を見る（HTTP 側は B 層の `http_contract/` と 02 #16 が見ている）。
    settings・vault は正常系でログを出さないので、**board の監査ログ**で貫通を確かめる ——
    実 `bd` を打つたびに 1 行出るので、周回のどの段でも束縛が効いていることが分かる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent, title="相関 ID の確認")
    # 完了を申告してもレビュー結果が無いので差し戻しが `max_returns` 回まで回る。
    # 1 周につき work run とレビュー run の 2 回消費する（件数は実物の設定から導く）。
    runs = (OrchestratorConfig().max_returns + 1) * 2
    runtime = cluster([make_crew_outcome() for _ in range(runs)])
    from common.interfaces.orchestrator import KickoffRunner  # noqa: PLC0415  # 局所的に要るだけ

    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)

    with correlation_scope() as correlation_id:
        runner.run(context)

    board_ids = correlation_recorder.correlation_ids("board")
    assert board_ids, "bd の監査ログが 1 行も出ていない"
    assert board_ids == {correlation_id}, (
        f"bd 監査ログの相関 ID が貫通していない: {board_ids} / 期待={correlation_id}"
    )
    assert len(correlation_recorder.of("board")) > 1, (
        "bd の呼び出しが 1 回しか無い（周回を通していない疑い）"
    )


@pytest.mark.cluster_case("04#17")
@pytest.mark.cluster_not_exercised("NX-02")
def test_cluster_04_mapping_unverified_is_not_exercised_here(
    cluster: Callable[..., ClusterRuntime], make_crew_outcome: Callable[..., CrewOutcome]
) -> None:
    """クラスタ 04 #17: `MappingUnverified` → stopped は**このクラスタでは見ない**（`NX-02`）。

    期待（§6 #17）: 本クラスタでは `RunMonitor` がスタブなので**見ない**（クラスタ 01）。

    **行を消さずに非実行として宣言する**（規約 §7。非実行は 01 #11 と 04 #17 の 2 件のみ）。
    非実行の**根拠そのもの**（`RunMonitor` が override されている）は下のテストが実配線から
    確認する（FR-021a）。
    """
    runtime = cluster([make_crew_outcome()])
    assert runtime.wiring.modules, "配線が組めている（非実行の宣言はここで閉じる）"


@pytest.mark.cluster_case("04#17")
def test_cluster_04_run_monitor_is_actually_overridden_in_this_wiring(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
    stub_overrides_keys: frozenset[type[object]],
) -> None:
    """FR-021a: 非実行の根拠（`RunMonitor` がスタブ）を**実配線から**確認する。

    「本クラスタでは `RunMonitor` がスタブなので見ない」という非実行の理由は、配線が変わったら
    成り立たなくなる。**理由そのものを機械で確かめる** —— 04 の有効 MOD に `monitor` が
    含まれず、`RunMonitor` が override 集合に在ることを見る。

    これが赤くなったら、`NX-02` の非実行宣言を見直さなければならない。
    """
    runtime = cluster([make_crew_outcome()])
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)

    assert "monitor" not in runtime.wiring.modules, (
        f"monitor が有効 MOD に入っている（非実行の根拠が崩れている）: {runtime.wiring.modules}"
    )
    assert RunMonitor in stub_overrides_keys, (
        f"`RunMonitor` が override 集合に無い: {sorted(str(key) for key in stub_overrides_keys)}"
    )
    assert type(run_monitor).__name__.startswith("Stub"), (
        f"`RunMonitor` が実物になっている: {type(run_monitor).__name__}"
    )


@pytest.mark.cluster_case("04#17")
def test_cluster_04_wiring_rejects_missing_and_duplicated_overrides(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
    make_overrides: Callable[..., ProtocolOverrides],
) -> None:
    """FR-032: 2 件の override は 1 件でも欠けても余っても**起動時に**落ちる（SC-004）。

    対応する §6 の行が無いので `04#17` に対応させた（判断は design-sync に記録）。

    - **1 件抜く**（`CrewExecutor`）→ `UnsatisfiedContractError`（要求元は orchestrator）。
    - **有効 MOD が提供する契約を足す**（`BoardReader` —— board が実物として提供）→
      `DuplicateProviderError`。
    """
    runtime = cluster([make_crew_outcome()])
    assert set(runtime.wiring.modules) == {
        "board",
        "vault",
        "settings",
        "stages",
        "orchestrator",
        "repo",  # D25: 実物の repo（完了ゲートの供給元）
    }, runtime.wiring.modules
    overrides = dict(make_overrides([make_crew_outcome()]))

    with pytest.raises(UnsatisfiedContractError) as unsatisfied:
        build_cluster_wiring(
            "04", {key: value for key, value in overrides.items() if key is not CrewExecutor}
        )
    assert "CrewExecutor" in str(unsatisfied.value), unsatisfied.value

    from common.interfaces.board import BoardReader  # noqa: PLC0415  # 局所的に要るだけ

    with pytest.raises(DuplicateProviderError) as duplicated:
        build_cluster_wiring("04", {**overrides, BoardReader: object()})
    assert "BoardReader" in str(duplicated.value), duplicated.value


@pytest.mark.cluster_case("04#17")
def test_cluster_04_health_reports_the_cluster_modules(
    cluster_client: Callable[..., HttpClient],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """US3/AC1: 組み上げ結果を `GET /health` の `modules` で観測する（T094）。

    受け入れシナリオ 1 の観測面は「`/health` の `modules` にそのクラスタの MOD だけが並ぶ」で
    ある。直接ドライブ側の `wiring.modules` 等値検査（上の FR-032 のテスト）とは
    **別インスタンス**なので、HTTP の口からも同じ MOD 集合が見えることを確かめる。対応する
    §6 の行が無いので `04#17`（FR-032 と同じ寄せ方）に対応させた（判断は design-sync に記録）。

    kickoff は投げない（ワーカーを起こさないので完了待ちは要らない）。期待値は
    `cluster_modules("04")` から引き、**整列して**比べる（重複も検知するため）。
    """
    with cluster_client([make_crew_outcome()]) as http:
        response = http.get("/health")

    assert response.status_code == 200, response.text
    modules = response.json()["modules"]
    assert sorted(modules) == sorted(cluster_modules("04")), modules
