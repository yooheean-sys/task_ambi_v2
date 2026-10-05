"""クラスタ 01 の W／I／api_exhausted（IF-20・IF-39・IF-48）。§6 #2・#3・#4。

台本は 3 形。

- **(B)** `finish_reason="length"` → **W** → close ＋ 封筒（IF-20）
- **(C)** 正常 → **I** → 盤面は不変（IF-39）。**`usage.cost` を必ず載せる** —— 落とすと
  `usage_missing` 由来の I と区別できず、テストが誤って緑になる（R-05）
- **(D)** 5xx 連発 → crewai 内リトライ後 `ExecutorFailure` → **F（api_exhausted）**（IF-48）

等級写像の期待値は `MonitorConfig().grade_map` から引く（直書きしない）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn, script_repeated

from api.wiring import require
from common.interfaces.board import BoardReader, IssueStatus
from common.interfaces.monitor import FailureKind, Grade
from common.interfaces.orchestrator import KickoffRunner
from common.kernel.enums import Labels, StateLabel, ToolName
from monitor import MonitorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

TURN_COST = 0.01
SERVER_ERROR = 503
"""偽 LLM サーバに返させる異常応答（API 失敗を作る）。"""


def _completing_script() -> list[dict[str, object]]:
    """**差し戻しを起こさずに 1 周で終わる** 4 往復の台本。

    work run（`complete_issue` → 確定）とレビュー run（`submit_review(approved=True)` → 確定）
    でちょうど 4 往復になる。`complete_issue` を呼ばないと `CrewOutcome.completed` が偽のままで
    orchestrator が必ず差し戻し、往復数が `max_returns` に依存して読めなくなる
    （2026-09-01 に実測）。
    """
    work_call = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    work_call["tool_calls"] = [
        make_tool_call(1, ToolName.complete_issue, {"reason": "クラスタ 01 の完了"})
    ]
    review_call = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    review_call["tool_calls"] = [
        make_tool_call(
            2,
            ToolName.submit_review,
            {"approved": True, "feedback": "承認", "files_read": []},
        )
    ]
    return [
        work_call,
        make_turn(cost=TURN_COST),
        review_call,
        make_turn(cost=TURN_COST),
    ]


def _grade_of(kind: FailureKind) -> Grade:
    """`monitor/config.json` の `grade_map` から等級を引く（期待値を直書きしない）。"""
    return Grade(MonitorConfig().grade_map[kind.value])


@pytest.mark.contract_if("IF-20")
@pytest.mark.cluster_case("01#2")
def test_cluster_01_finish_length_maps_to_grade_w_and_closes_the_issue(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #2 / IF-20: `finish_reason=length` は **W** → close ＋ 封筒。

    期待（§6 #2）: **W** → close＋封筒 → 確認読取一致 → `RunVerdict(grade=W, closed=True)`。

    F 級（release して残す）との違いは **close するかどうか**なので、実 `bd` の `status` を
    読み戻して見る。等級は `grade_map` の `finish_length → W` から引く。
    """
    expected_grade = _grade_of(FailureKind.finish_length)
    marker = MonitorConfig().envelope_marker
    # work run の 1 往復目で `length` を返す（`finish_length` が積まれ W になる）。差し戻しは
    # 起きるが、W かつ close 済みなら `_interpret` が `closed_by_monitor` を優先する。
    script = _completing_script()
    script[0] = make_turn(finish_reason="length", cost=TURN_COST)
    runtime = cluster(script)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue_id = seed_leaf(runtime)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].grade is expected_grade, (
        f"等級が `grade_map` と食い違う: 実際={result.issues[0].grade} 期待={expected_grade}"
    )
    issue = board_reader.show(issue_id)
    assert issue.status is IssueStatus.closed, f"W 級なのに close されていない: {issue.status}"
    comments = board_reader.comments(issue_id)
    assert [c for c in comments if c.body.startswith(marker)], (
        f"封筒 comment が盤面に無い: {[c.body[:40] for c in comments]}"
    )
    assert runtime.llm.call_count >= 1, "1 度も送信していない"


@pytest.mark.contract_if("IF-39")
@pytest.mark.cluster_case("01#3")
def test_cluster_01_normal_run_maps_to_grade_i_and_leaves_the_board_untouched(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #3 / IF-39: 正常終了は **I** で、盤面には封筒を書かない。

    期待（§6 #3）: **I** → board 不変（封筒は書かない）・履歴のみ。`RunVerdict(grade=I)`。

    ⚠ **現物の等級は `None` であって `I` ではない**（2026-09-01 に実測。design-sync の B 表に
    記録する）—— `Grade.I` は `grade_map` が `retry_succeeded`・`usage_missing`・`summarization`
    を写す先であり、**失敗が 1 件も積まれなければ等級は付かない**。orchestrator の `_interpret`
    も `grade is None or grade is Grade.I` を同じ扱い（盤面に書かない）にしている。したがって
    ここは「**封筒を書かない等級**（`None` または `I`）であること」を見る。

    ⚠ **`usage.cost` を必ず載せる**（R-05）—— 落とすと `usage_missing` が積まれて等級が `I` に
    化け、「正常だから書かない」と「usage を読めなかったから I」を区別できなくなる。台本の
    `usage` に `cost` を載せた上で、**`I` ではなく `None`** であることまで確かめれば
    `usage_missing` が積まれていないことの証拠になる。
    """
    marker = MonitorConfig().envelope_marker
    script = _completing_script()
    runtime = cluster(script)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue_id = seed_leaf(runtime)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    summary = result.issues[0]
    assert summary.grade is None, (
        "正常終了で等級が付いている（`usage_missing` などが積まれた疑い）:"
        f" {summary.grade} / 期待=None（`Grade.I` は失敗が積まれたときの写像先）"
    )
    assert summary.grade is not Grade.I, "usage を読めずに I へ落ちている（`cost` の載せ忘れ）"
    assert summary.returns == 0, f"差し戻しが起きている: {summary.returns}"
    comments = board_reader.comments(issue_id)
    assert not [c for c in comments if c.body.startswith(marker)], (
        f"I 級なのに封筒が書かれている（board 不変のはず）: {[c.body[:40] for c in comments]}"
    )
    issue = board_reader.show(issue_id)
    assert Labels.state(StateLabel.failing) not in issue.labels, (
        f"I 級なのに `state:failing` が付いている: {issue.labels}"
    )
    assert runtime.llm.call_count == len(script), (
        "往復数が台本と食い違う（差し戻しが起きた疑い）:"
        f" 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.contract_if("IF-48")
@pytest.mark.cluster_case("01#4")
def test_cluster_01_api_failure_maps_to_grade_f_without_retrying_on_our_side(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #4 / IF-48: 5xx 連発は `ExecutorFailure` → **F（api_exhausted）**。

    期待（§6 #4）: crewai 内リトライ後 `ExecutorFailure` → **F（api_exhausted）**。
    **自前の再試行・迂回が無いこと**（憲章 §10）。

    「自前の再試行が無い」ことは、偽サーバへの往復数が **crewai のリトライぶんで頭打ちになり、
    周回が 1 課題で終わる**ことで見る（自前で回すなら周回が続くか往復が青天井になる）。
    等級は `grade_map` の `api_exhausted → F` から引く。
    """
    expected_grade = _grade_of(FailureKind.api_exhausted)
    runtime = cluster(script_repeated(make_turn(status=SERVER_ERROR), turns=3))
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    seed_leaf(runtime)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    summary = result.issues[0]
    assert summary.grade is expected_grade, (
        f"等級が `grade_map` と食い違う: 実際={summary.grade} 期待={expected_grade}"
    )
    assert len(result.issues) == 1, (
        f"1 課題で終わっていない（自前の迂回で別の課題へ移った疑い）: {result.issues}"
    )
    assert runtime.llm.call_count >= 1, "1 度も送信していない"
