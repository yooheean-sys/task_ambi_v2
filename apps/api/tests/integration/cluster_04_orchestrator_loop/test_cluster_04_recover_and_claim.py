"""クラスタ 04 の残骸回収と確保（IF-06・IF-05・IF-04・IF-14・IF-10）。§6 #2 ×4・#3・#4。

**実 `bd` でしか見られない面**である —— `list_working` の絞り込み・`open_question_of` の
未回答判定・`children_of` の実在確認・`release` の効き目は、いずれも盤面の実際の状態に依存する。

D01 の二重ガード（起動時の拾い直しと分解層の再入）のうち、ここは**起動時の拾い直し**側を見る。
3 分岐は「未回答 question あり → 触らない」「子が既にいる → failing ＋人間へ」「純粋な残骸 →
release して ready へ」（D04-c）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.wiring import require
from common.interfaces.board import BoardReader
from common.interfaces.executor import CrewOutcome, ReviewVerdict
from common.interfaces.orchestrator import KickoffRunner, KickoffStatus
from common.kernel.enums import KindLabel, Labels, StateLabel
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd


def _approved() -> ReviewVerdict:
    """レビュー承認の結果（`CloseIntent` へ進ませる）。"""
    return ReviewVerdict(approved=True, feedback="承認", files_read=[])


@pytest.mark.contract_if("IF-06")
@pytest.mark.cluster_case("04#2")
def test_cluster_04_recover_lists_the_working_leftovers(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    working_label: str,
) -> None:
    """クラスタ 04 #2 / IF-06: `list_working` が `state:working` の残骸を実 `bd` から列挙する。

    期待（§6 #2）: `state=working` の残骸を列挙して 3 分岐へ渡す。

    列挙できていることは「**回収の記録が盤面に残る**」ことで見る —— 列挙されなければどの分岐にも
    入らず、`[orchestrator]` の理由コメントが 1 件も入らない。

    ⚠ 残骸には **`role:executor` を付ける**（`list_working` は `ClaimFilter.labels` で絞るので、
    付けないと列挙対象から外れ、テストが空振りする＝ 2026-09-01 に実測）。
    """
    marker = OrchestratorConfig().comment_marker
    leftover = seed(KindLabel.child, extra_labels=[working_label])
    # release された残骸は**同じ周回で確保される**（`ClaimFilter` は `state:working` を見ない）。
    # 実行まで進むので work run とレビュー run のぶんを用意する（2026-09-01 に実測）。
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.status is not KickoffStatus.stopped, f"周回が止まっている: {result.status}"
    comments = board_reader.comments(leftover)
    assert [comment for comment in comments if comment.body.startswith(marker)], (
        f"残骸が列挙されていない（回収の記録が無い。印={marker!r}）:"
        f" {[c.body[:40] for c in comments]}"
    )


@pytest.mark.contract_if("IF-05")
@pytest.mark.cluster_case("04#2")
def test_cluster_04_recover_leaves_an_issue_with_an_open_question_untouched(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    link: Callable[[str, str], None],
    context: ExecutionContext,
    working_label: str,
) -> None:
    """クラスタ 04 #2 / IF-05: 未回答の質問がある残骸には**1 回も書き込まない**。

    期待（§6 #2）: 未回答 question あり → 触らない（crash window D04-c）。

    質問は **`add_dependency` の辺**で張る（`open_question_of` は課題の `dependencies` から
    `kind:question` を探すので、`--parent` の親子関係では拾われない＝ P5D-059 (2) と同じ理由。
    2026-09-01 に実測）。回収が「触らない」ことは **comment が 1 件も増えていない**ことで見る
    —— release だけを見ると「もともと assignee が無い」形と区別できない。
    """
    parent = seed(KindLabel.child, extra_labels=[working_label])
    question = seed(KindLabel.question, ready=False, title="[要判断] 未回答の質問")
    link(parent, question)
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    before = len(board_reader.comments(parent))

    runner.run(context)

    after = board_reader.comments(parent)
    assert len(after) == before, (
        f"未回答の質問がある残骸へ書き込んでいる: {[c.body[:40] for c in after]}"
    )
    issue = board_reader.show(parent)
    assert Labels.state(StateLabel.working) in issue.labels, (
        f"`state:working` が外れている（触らないはず）: {issue.labels}"
    )


@pytest.mark.contract_if("IF-04")
@pytest.mark.cluster_case("04#2")
def test_cluster_04_recover_hands_off_a_leftover_that_already_has_children(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
    working_label: str,
) -> None:
    """クラスタ 04 #2 / IF-04: 子が既にいる残骸は `state:failing` にして人間へ渡す。

    期待（§6 #2）: 子が既にいる → failing＋人間へ。

    `children_of` が実 `bd` から子を読めていることが前提の分岐である。人間へ渡す統一経路
    （`[orchestrator]` 理由コメント → `state:failing` → release）を通るので、その 3 つを
    盤面から読み戻して確かめる。理由コメントの印は `OrchestratorConfig().comment_marker` から
    引く（テストへ直書きしない）。
    """
    marker = OrchestratorConfig().comment_marker
    parent = seed(KindLabel.subrequest, extra_labels=[working_label])
    seed(KindLabel.child, parent=parent, ready=False, title="既にいる子")
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    issue = board_reader.show(parent)
    assert Labels.state(StateLabel.failing) in issue.labels, (
        f"人間へ渡す印（`state:failing`）が付いていない: {issue.labels}"
    )
    comments = board_reader.comments(parent)
    assert [comment for comment in comments if comment.body.startswith(marker)], (
        f"理由コメントが無い（印={marker!r}）: {[c.body[:40] for c in comments]}"
    )
    assert not issue.assignee, f"release されていない: {issue.assignee}"


@pytest.mark.contract_if("IF-14")
@pytest.mark.cluster_case("04#2")
def test_cluster_04_recover_releases_a_pure_leftover(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    working_label: str,
) -> None:
    """クラスタ 04 #2 / IF-14: 純粋な残骸（質問も子も無い）は release して ready へ戻す。

    期待（§6 #2）: 純粋な残骸 → release して ready へ。

    「ready へ戻る」ことは、**同じ周回のうちにその課題が確保されて処理される**ことで見る ——
    release だけを見ると assignee が消えたことしか分からない。回収の直後に `claim_next` が
    走るので、残骸が 1 件だけなら処理対象になる。
    """
    marker = OrchestratorConfig().comment_marker
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    leftover = seed(KindLabel.child, parent=parent, extra_labels=[working_label])
    # 「ready へ戻る」ことは**同じ周回で確保されて実行まで進む**ことで見る。したがって
    # work run とレビュー run のぶんの `CrewOutcome` を用意する。
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "release された残骸が確保されていない（ready へ戻っていない）"
    assert result.issues[0].issue_id == leftover, (
        f"別の課題を処理している: {result.issues[0].issue_id}"
    )
    comments = board_reader.comments(leftover)
    assert [comment for comment in comments if comment.body.startswith(marker)], (
        f"回収の記録が残っていない（印={marker!r}）: {[c.body[:40] for c in comments]}"
    )


@pytest.mark.contract_if("IF-10")
@pytest.mark.cluster_case("04#3")
def test_cluster_04_claim_returns_none_when_nothing_is_ready(
    cluster: Callable[..., ClusterRuntime], context: ExecutionContext
) -> None:
    """クラスタ 04 #3 / IF-10: ready が無ければ `claim_next` は `None` → `no_work` で終わる。

    期待（§6 #3）: `claim_next` None → 周回終了 `KickoffResult(no_work)`。

    盤面には**何も仕込まない**（`bd init` 直後）。`CrewExecutor` の並びも空にしてあるので、
    もし周回が課題を掴んで実行まで進めば `SequencedCrewExecutor` が「並びを使い切った」で
    落ちる —— 空振りを緑にしない仕掛けである。
    """
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)

    result = runner.run(context)

    assert result.status is KickoffStatus.noop, f"`no_work` で終わっていない: {result.status}"
    assert not result.issues, f"1 件も処理しないはず: {result.issues}"


@pytest.mark.contract_if("IF-14")
@pytest.mark.cluster_case("04#4")
def test_cluster_04_claimed_failing_issue_is_released_immediately(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #4 / IF-14: `state:failing` が付いた課題を掴んだら**即 release** する。

    期待（§6 #4）: 即 release（人間が直すまで触らない P5D-034）。

    実行まで進んでいないことは、`CrewExecutor` の並びを**空**にしておくことで担保する ——
    進めば `SequencedCrewExecutor` が「並びを使い切った」で落ちる。
    """
    failing = seed(KindLabel.child, extra_labels=[Labels.state(StateLabel.failing)])
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    issue = board_reader.show(failing)
    assert Labels.state(StateLabel.failing) in issue.labels, (
        f"`state:failing` が外れている（人間が直すまで触らないはず）: {issue.labels}"
    )
    assert not issue.assignee, f"確保したまま放置している: {issue.assignee}"
