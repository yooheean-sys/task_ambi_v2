"""クラスタ 01 の鎖の起点（IF-48・IF-39）。§6 #1 ×3。

台本 (A)：上限超過 → Hook が `RunHalted`（送信しない）→ litellm／crewai のリトライを**素通り**
（`BaseException` 直系＝ Z-6）→ `CrewOutcome.halted=True` → `finish` → **F**。

§6 #1 が挙げる上限は **3 系統**（長さ `tokens_before_send > T`／回数 `requests > max_requests`／
費用 `cost > max_cost`）で、`_reserve_and_judge` の判定順もこの通りである。ここでは①（長さ）と
②（回数）を踏む —— ②は `max_requests=1` で 2 往復目の送信前に、①は `threshold_t` を小さくして
**1 往復目の送信前**に踏む（①は `call_count == 0`＝**1 度も送られない**ことが観測点になる）。
③（費用）は 03 #13 が主検証クラスタとして踏む。

**03 #10 と同じ機序だが鎖の長さが違う**（§0-3a）—— 03 は executor の出口まで、01 は
`finish` の等級写像と盤面の確認読取まで伸ばす。

**通過 IF（IF-40〜43）は宣言しない**（FR-010b）。ここで踏まれてはいるが、主検証クラスタは
03 である。docstring で触れるに留める。

判定に効く値は `MonitorConfig()` から引く（R-05）。台本の `usage` には必ず `cost` を載せる。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn
from _support.contract_stubs import CallLog

from api.wiring import require
from common.interfaces.board import BoardReader
from common.interfaces.monitor import FailureKind, Grade
from common.interfaces.orchestrator import KickoffRunner, KickoffStatus
from common.kernel.enums import ToolName
from monitor import MonitorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

TURN_COST = 0.01
"""1 往復あたりの `usage.cost`（**必ず載せる**＝ R-05）。"""

TINY_THRESHOLD_T = 8
"""①（長さ）を**必ず**踏ませる `LLMProfile.threshold_t`（T084）。

推定は `chars` 方式の `ceil(len(連結 content) / 4)`（`monitor/repository.py` の `CHARS_PER_TOKEN`）
なので、CrewAI が組む系統プロンプトだけで数百トークンになる。**回数・費用の上限は既定のまま**
（十分大きい）にしておくので、1 往復目で上がる `RunHalted` の種類は `ctx_exceeded` にしか
なりえない —— 判定順が長さ → 回数 → 費用だからではなく、**ほかの 2 つが到達しない**からである。

値は**テスト側が持つ**（`from conftest import ...` を使わない＝ FR-041a・R-02）。
"""

_EVIDENCE = re.compile(r"^- (?P<kind>\S+): measured=(?P<measured>\S+) limit=(?P<limit>\S+) ")
"""封筒の失敗行（`Envelope.render` の 5 行目以降）を読む。書式は verdict-mapping.md §4。"""


def halting_script() -> list[dict[str, object]]:
    """台本 (A): 上限超過を誘発する **2 往復**の台本。

    ⚠ 正常応答を並べただけでは往復は増えない —— 既定の本文は `Final Answer:` 形なので実 CrewAI
    が 1 往復で確定する（2026-09-01 に実測）。道具呼び出しを 1 つ挟んで 2 往復目を作り、その
    **送信前**に回数の上限を踏ませる。
    """
    first = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    first["tool_calls"] = [make_tool_call(1, ToolName.read_issue, {})]
    return [first, make_turn(cost=TURN_COST)]


@pytest.mark.contract_if("IF-48")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_run_halted_passes_through_to_the_crew_outcome(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
    call_log: CallLog,
) -> None:
    """クラスタ 01 #1 / IF-48: `RunHalted` が素通りして `CrewOutcome.halted=True` になる。

    期待（§6 #1 の前半）: Hook が `RunHalted`（送信しない）→ litellm／crewai のリトライを
    素通り（`BaseException` 直系 Z-6）→ `CrewOutcome.halted=True`。

    通過するのは IF-40〜43（Hook の 4 面）だが、**主検証クラスタは 03 なので宣言しない**
    （FR-010b）。ここで見るのは executor が返す `CrewOutcome` の `halted` である。

    `KickoffRunner.run` を通して見る —— `begin` は orchestrator が実物として呼ぶ（01 では
    テストが呼ばない。03 との差である）。
    """
    runtime = cluster(halting_script(), max_requests=1)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    issue_id = seed_leaf(runtime)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない（claim_next が取れていない）"
    summary = result.issues[0]
    assert summary.issue_id == issue_id, f"別の課題を処理している: {summary.issue_id}"
    assert summary.result == "halted", f"停止として扱われていない: {summary.result}"
    assert summary.grade is Grade.F, f"等級が F でない: {summary.grade}"
    assert runtime.llm.call_count == 1, (
        "上限に達した往復が送信されている（`on_outbound` は送信前に判定する）:"
        f" 実際={runtime.llm.call_count}"
    )
    assert not call_log.of("VaultReader", "read_input"), (
        "スタブ vault が読まれている（01 の鎖は保管庫を通らない）"
    )


@pytest.mark.contract_if("IF-39")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_finish_maps_the_halted_run_to_grade_f(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #1 / IF-39: 停止した run を `finish` が **F** へ写し、盤面へ書く。

    期待（§6 #1 の後半）: → finish → **F**。board: 封筒 comment → `state:failing` → release の
    順 → 確認読取一致 → `RunVerdict(grade=F, halted=True)`。

    等級写像は `monitor/config.json` の `grade_map` が正本なので、**期待値をそこから引く**
    （R-05。`limit_requests → F` をテストへ直書きしない）。封筒の印も `envelope_marker` から
    引く。写像の内訳（封筒・ラベル・release・確認読取）は
    `test_cluster_01_grade_f_mapping.py` が IF ごとに分けて見る。
    """
    config = MonitorConfig()
    expected_grade = Grade(config.grade_map[FailureKind.limit_requests.value])
    runtime = cluster(halting_script(), max_requests=1)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    seed_leaf(runtime)

    result = runner.run(context)

    assert result.status is KickoffStatus.ok, f"周回が異常終了している: {result.status}"
    summary = result.issues[0]
    assert summary.grade is expected_grade, (
        f"等級写像が `grade_map` と食い違う: 実際={summary.grade} 期待={expected_grade}"
    )
    assert runtime.llm.call_count == 1


def ctx_exceeded_script() -> list[dict[str, object]]:
    """①（長さ）用の台本。**1 通も消費されない** —— 送信前に止まるからである。

    それでも 1 通置くのは、偽サーバが空の台本を受け付けないためと、「台本が在るのに
    `call_count == 0`」という形そのものが「送っていない」ことの証拠になるためである。
    """
    return [make_turn(cost=TURN_COST)]


@pytest.mark.contract_if("IF-39")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_context_estimate_halts_before_the_first_request_is_sent(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #1 / IF-39: `tokens_before_send > T` で**送信前に**止まり **F** へ写る。

    期待（§6 #1 の上限①）: Hook が `RunHalted`（**送信しない**）→ 素通り →
    `CrewOutcome.halted=True` → finish → **F**。board: 封筒 → `state:failing` → release →
    確認読取一致。

    ほかの 2 本（`limit_requests`）と違うのは **1 往復も送られない**ことである
    （`call_count == 0`）。応答を見てから止める実装ではこの値が 1 以上になる —— つまりこの
    assert が「送信**前**の推定で止めた」ことの観測点である。

    種類が `ctx_exceeded` であることは**盤面の封筒から読み戻して**確かめる
    （`Envelope.render` の失敗行）。等級は 3 系統とも F なので、等級だけでは①②③を区別
    できない。`limit=` が `begin` へ渡した `threshold_t` と一致し `measured` がそれを超えて
    いることまで見るので、閾値を大きくすれば赤くなる。

    ⚠ 封筒の `kinds` には **`no_output` も併記される**（2026-09-01 の実測）—— 1 往復も送って
    いない以上 `task_output` が無いのは当然で、①を 1 往復目で踏む形では必ず同伴する。したがって
    `kinds` は**含有**で見て、`measured`／`limit` の突合は `ctx_exceeded` の行に絞る。

    通過 IF（IF-40〜43）は宣言しない（FR-010b。主検証クラスタは 03）。
    """
    config = MonitorConfig()
    expected_grade = Grade(config.grade_map[FailureKind.ctx_exceeded.value])
    runtime = cluster(ctx_exceeded_script(), threshold_t=TINY_THRESHOLD_T)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue_id = seed_leaf(runtime)

    result = runner.run(context)

    assert runtime.llm.call_count == 0, (
        f"送信前に止まっていない（応答を見てから判定している）: 実際={runtime.llm.call_count}"
    )
    assert result.status is KickoffStatus.ok, f"周回が異常終了している: {result.status}"
    assert result.issues, "1 件も処理していない"
    summary = result.issues[0]
    assert summary.result == "halted", f"停止として扱われていない: {summary.result}"
    assert summary.grade is expected_grade, (
        f"等級写像が `grade_map` と食い違う: 実際={summary.grade} 期待={expected_grade}"
    )

    envelopes = [
        comment
        for comment in board_reader.comments(issue_id)
        if comment.body.startswith(config.envelope_marker)
    ]
    assert len(envelopes) == 1, f"封筒が 1 件でない: {[c.body[:40] for c in envelopes]}"
    body = envelopes[0].body
    matched = [match for match in map(_EVIDENCE.match, body.splitlines()) if match is not None]
    kinds = [match["kind"] for match in matched]
    assert FailureKind.ctx_exceeded.value in kinds, (
        f"封筒に①（長さ）の失敗行が無い（別の上限で止まった）: {kinds} / 封筒={body[:200]}"
    )
    for other in (FailureKind.limit_requests, FailureKind.limit_cost):
        assert other.value not in kinds, f"②③でも止まっている（①の単独検証にならない）: {kinds}"
    hit = next(match for match in matched if match["kind"] == FailureKind.ctx_exceeded.value)
    assert float(hit["limit"]) == TINY_THRESHOLD_T, (
        f"`begin` へ渡した閾値が判定に使われていない: {hit['limit']}"
    )
    assert float(hit["measured"]) > TINY_THRESHOLD_T, (
        f"推定値が閾値を超えていないのに止まっている: {hit['measured']}"
    )
    assert f"max_tokens_before_send={hit['measured']}" in body, (
        f"計測の最大値が判定に使った推定値と食い違う: {body[:300]}"
    )
