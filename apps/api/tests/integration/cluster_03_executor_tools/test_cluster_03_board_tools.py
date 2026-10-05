"""クラスタ 03 の盤面 2 道具（IF-02＝`show`・IF-17＝`comment`）。§6 #13 ×2。

**実 executor の Tool × 実 `bd`**。`record_progress` は盤面に comment を**書く**ので、道具越しの
観測がそのまま成立する（`BoardReader.comments` で読み戻せる）。`read_issue` の `IssueNotFound`
は道具が文言へ翻訳するので、その 1 点だけ `BoardReader` の直接呼び出しで確かめる。

**`BEADS_ACTOR` を `setenv` しない**（R-09）—— board の `repository.py` が**書込のたびに**
`actor` 引数から注入する。テストが制御するのは `Member.bd_actor`（実 settings が返す値）である。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, script_tool_calls
from _support.cluster_stores import seed_issue

from api.wiring import require
from common.interfaces.board import BoardReader, IssueNotFound
from common.interfaces.executor import CrewEventKind, CrewExecutor
from common.interfaces.settings import MemberResolver
from common.kernel.enums import KindLabel, Labels, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewSpec

pytestmark = pytest.mark.requires_bd

PROGRESS_NOTE = "クラスタ 03 の進捗メモ"
"""`record_progress` に書かせる本文（読み戻して一致を見る）。"""

MISSING_ISSUE_ID = "cl03-999999"
"""実 `bd` に無い課題（`IssueNotFound` を踏む）。"""


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


@pytest.mark.contract_if("IF-02")
@pytest.mark.cluster_case("03#13")
def test_cluster_03_read_issue_reaches_the_real_board_and_reports_a_missing_issue(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #13 / IF-02: `read_issue` が実 `bd` の課題まで届き、不在は `IssueNotFound`。

    期待（§6 #13）: `IssueNotFound`（不在の課題）。

    道具は `issue_id` を**指示書から束縛**する（AI に選ばせない）ので、不在の課題を道具越しに
    読ませることはできない。したがって観測を 2 段にする —— 鎖は「実在する課題で道具が拒否されず
    通ること」、契約例外は `BoardReader.show` を直接呼んで確かめる。
    """
    script = script_tool_calls([make_tool_call(1, ToolName.read_issue, {})])
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue_id = _seed(runtime)

    outcome = crew_executor.run(make_spec(runtime, issue_id=issue_id))

    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, f"policy の前段で拒否されている: {[event.detail for event in denied]}"
    called = [event for event in outcome.events if event.kind is CrewEventKind.tool_called]
    assert any(event.detail.get("tool") == ToolName.read_issue.value for event in called), (
        f"`read_issue` が実 `bd` まで届いていない: {[e.detail for e in called]}"
    )

    issue = board_reader.show(issue_id)
    assert issue.id == issue_id
    assert Labels.kind(KindLabel.child) in issue.labels, issue.labels

    with pytest.raises(IssueNotFound) as caught:
        board_reader.show(MISSING_ISSUE_ID)
    assert caught.value.issue_id == MISSING_ISSUE_ID, caught.value.issue_id

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.contract_if("IF-17")
@pytest.mark.cluster_case("03#13")
def test_cluster_03_record_progress_writes_a_comment_as_the_member_actor(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #13 / IF-17: `record_progress` の comment が**メンバの `bd_actor`** で入る。

    期待（§6 #13）: bd に comment が actor＝メンバの `bd_actor` で入る。

    行為者は環境変数ではなく **`actor` 引数**で決まる（R-09。board が書込のたびに
    `BEADS_ACTOR` へ注入する）。渡っているのは実 settings が解決した `Member.bd_actor` なので、
    期待値もそこから取る（テストへ直書きしない）。
    """
    script = script_tool_calls(
        [make_tool_call(1, ToolName.record_progress, {"note": PROGRESS_NOTE})]
    )
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    member_resolver: MemberResolver = require(runtime.wiring.provided, MemberResolver)
    expected_actor = member_resolver.resolve(
        runtime.stores.project_key, runtime.stores.persona
    ).bd_actor
    issue_id = _seed(runtime)

    outcome = crew_executor.run(make_spec(runtime, issue_id=issue_id))

    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, f"policy の前段で拒否されている: {[event.detail for event in denied]}"
    assert outcome.progress_notes == 1, (
        f"進捗の計上が合わない（成功時のみ加算される）: {outcome.progress_notes}"
    )

    comments = board_reader.comments(issue_id)
    written = [comment for comment in comments if PROGRESS_NOTE in comment.body]
    assert written, f"実 `bd` に comment が入っていない: {[c.body for c in comments]}"
    assert written[0].actor == expected_actor, (
        f"actor がメンバの `bd_actor` でない: 実際={written[0].actor} 期待={expected_actor}"
    )

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )
