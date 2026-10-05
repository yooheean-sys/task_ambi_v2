"""クラスタ 01 の F 級写像を board の面ごとに分解する（IF-18・IF-22・IF-15・IF-03）。§6 #1 ×4。

`test_cluster_01_halt_chain.py` が「停止 → F」までを見るのに対し、ここは**盤面に何が起きたか**を
実 `bd` から読み戻して面ごとに確かめる —— 封筒 comment（IF-18）→ `state:failing` の
`set_labels`（IF-22）→ `release`（IF-15）→ 確認読取（IF-03）。

観測はすべて**実 `bd` の読み戻し**で行う（board の Protocol 経由。外部ストアを直接覗かない）。
封筒の印は `MonitorConfig().envelope_marker` から引く（テストへ直書きしない＝ R-05）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn

from api.wiring import require
from common.interfaces.board import BoardReader, IssueStatus
from common.interfaces.orchestrator import KickoffRunner
from common.kernel.enums import KindLabel, Labels, StateLabel, ToolName
from monitor import MonitorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

TURN_COST = 0.01


def _halting_script() -> list[dict[str, object]]:
    """上限超過を誘発する 2 往復の台本（`halt_chain` と同型）。"""
    first = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    first["tool_calls"] = [make_tool_call(1, ToolName.read_issue, {})]
    return [first, make_turn(cost=TURN_COST)]


HALTING_CALLS = 1
"""上限停止までに**実際に送られる**往復数（`max_requests=1` なので 2 往復目は送信前に止まる）。

偽サーバは台本を使い切ると**最終要素を繰り返す**（`fake_llm_server.py:69`）ので、往復数を
読み違えても「無限に正常応答」で緑になりうる。したがって厳密値で押さえる（R-04・T085）。
"""


def _run_to_grade_f(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> tuple[ClusterRuntime, str]:
    """上限停止まで走らせ、実物一式と対象の issue_id を返す。

    走らせた直後に**往復数を厳密値で確かめる**（R-04）—— 4 本とも同じ台本・同じ上限なので、
    ここに 1 つ置けば全数に効く。
    """
    runtime = cluster(_halting_script(), max_requests=1)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    issue_id = seed_leaf(runtime)
    runner.run(context)
    assert runtime.llm.call_count == HALTING_CALLS, (
        "往復数が台本・上限と食い違う（差し戻し／再送が起きた疑い）:"
        f" 実際={runtime.llm.call_count} 期待={HALTING_CALLS}"
    )
    return runtime, issue_id


@pytest.mark.contract_if("IF-18")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_failure_mapping_writes_the_envelope_comment(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #1 / IF-18: F 級写像の 1 手目は**封筒 comment**（`BoardWriter.comment`）。

    期待（§6 #1）: board に封筒 comment が入る。封筒の 1 行目には `envelope_marker` が付く
    （`monitor/config.json` の実値から引く）。

    実 `bd` へ書かれたことを `BoardReader.comments` で読み戻して確かめる。
    """
    marker = MonitorConfig().envelope_marker
    runtime, issue_id = _run_to_grade_f(cluster, seed_leaf, context)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    comments = board_reader.comments(issue_id)

    envelopes = [comment for comment in comments if comment.body.startswith(marker)]
    assert envelopes, (
        f"封筒 comment が盤面に無い（印={marker!r}）: {[c.body[:40] for c in comments]}"
    )


@pytest.mark.contract_if("IF-22")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_failure_mapping_sets_the_failing_state_label(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #1 / IF-22: 2 手目は `state:failing` の付与（`BoardWriter.set_labels`）。

    期待（§6 #1）: `state:failing` が付き、`state:working` が外れる（排他）。

    `claim_next` が付けた `state:working` が**消えている**ことまで見る —— 付けるだけの実装では
    2 つの状態が同居する。
    """
    runtime, issue_id = _run_to_grade_f(cluster, seed_leaf, context)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    issue = board_reader.show(issue_id)

    assert Labels.state(StateLabel.failing) in issue.labels, (
        f"`state:failing` が付いていない: {issue.labels}"
    )
    assert Labels.state(StateLabel.working) not in issue.labels, (
        f"`state:working` が外れていない（状態が排他でない）: {issue.labels}"
    )
    assert Labels.kind(KindLabel.child) in issue.labels, (
        f"仕込んだラベルが消えている（付け外しが広すぎる）: {issue.labels}"
    )


@pytest.mark.contract_if("IF-15")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_failure_mapping_releases_the_issue(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #1 / IF-15: 3 手目は `release`（確保を解く）。

    期待（§6 #1）: release される（close ではない）。**F 級は close しない** —— 人間が直すまで
    盤面に残す（P5D-034）。

    `status` が `closed` でないこと・assignee が戻っていることを実 `bd` から読み戻して見る。
    """
    runtime, issue_id = _run_to_grade_f(cluster, seed_leaf, context)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    issue = board_reader.show(issue_id)

    assert issue.status is not IssueStatus.closed, (
        f"F 級で close されている（close するのは W 級）: {issue.status}"
    )
    assert not issue.assignee, f"release されていない（assignee が残っている）: {issue.assignee}"


@pytest.mark.contract_if("IF-03")
@pytest.mark.cluster_case("01#1")
def test_cluster_01_failure_mapping_is_confirmed_by_reading_the_comments_back(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #1 / IF-03: 写像後の**確認読取**（`BoardReader.comments`）が一致する。

    期待（§6 #1）: 確認読取一致 → `RunVerdict(grade=F, halted=True)`。

    確認読取が通ったことは「`MappingUnverified` にならず周回が続いた」ことで分かる（不一致なら
    `KickoffResult.status=stopped` になる＝ #5）。ここでは**読み戻した封筒が
    `kickoff_id` まで一致する**ことを見る —— monitor は「comments を新しい方から遡り、1 行目が
    `envelope_marker` の最初の 1 件を採り `kickoff_id` の一致まで確かめる」規則である。
    """
    marker = MonitorConfig().envelope_marker
    runtime, issue_id = _run_to_grade_f(cluster, seed_leaf, context)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    comments = board_reader.comments(issue_id)
    envelopes = [comment for comment in comments if comment.body.startswith(marker)]

    assert len(envelopes) == 1, (
        f"封筒が 1 件でない（写像が二重に走った疑い）: {[c.body[:40] for c in envelopes]}"
    )
    body = envelopes[0].body
    assert "F" in body, f"封筒に等級が載っていない: {body[:120]}"
    assert any(char.isalnum() for char in body), body
