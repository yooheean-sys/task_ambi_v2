"""クラスタ 04 のエスカレーションと人間の回答（IF-05・IF-11・IF-12・IF-14・IF-28・IF-07）。

§6 #12 ×4・#13 ×2・#14。

`mode="execute"` の段は実配線で到達できるので、エスカレーションの経路は**鎖として**見られる ——
スタブ `CrewExecutor` が `escalation` を載せた `CrewOutcome` を返すと、orchestrator は未回答の
質問を探し（IF-05）、無ければ質問を起票し（IF-11）、依存で待たせて（IF-12）解放する（IF-14）。

**質問は `--parent` ではなく `add_dependency` の辺で張る**（P5D-059 (2)）—— `--parent` の辺は
子側にしか記録されず `open_question_of` が拾えない。実 `bd` の依存辺を読み戻して確かめる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_stores import seed_comment

from api.wiring import require
from common.interfaces.board import BoardReader, IssueStatus
from common.interfaces.executor import Escalation
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.settings import MemberResolver
from common.kernel.enums import KindLabel, Labels
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_stores import ClusterStores
    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewOutcome
    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

QUESTION_TITLE_PREFIX = "[要判断]"
"""質問課題の題名の頭（FR-063）。"""

HUMAN_ANSWER = "人間からの回答: この方針で進めてよい"
"""人間が盤面へ書く回答の本文。"""


def _escalation() -> Escalation:
    """`escalate_to_human` の申告（3 項目）。"""
    return Escalation(
        what_i_tried="試したこと", why_stuck="行き詰まった理由", decision_needed="決めてほしいこと"
    )


@pytest.mark.contract_if("IF-11")
@pytest.mark.cluster_case("04#12")
def test_cluster_04_escalation_creates_a_question_issue(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #12 / IF-11: 未回答の質問が無ければ `kind:question` を**起票**する。

    期待（§6 #12）: 無ければ question 起票 → `add_dependency` → release。

    起票されたことは実 `bd` から読み戻して確かめる —— 題名が `[要判断]` で始まり、
    `kind:question` が付いていること。**親は持たせない**（質問は依存で張る＝ P5D-059 (2)）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="エスカレーションする課題")
    runtime = cluster([make_crew_outcome(escalation=_escalation())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].result == "escalated", (
        f"エスカレーションとして扱われていない: {result.issues[0].result}"
    )
    question = board_reader.open_question_of(issue_id)
    assert question is not None, "質問課題が起票されていない"
    assert question.title.startswith(QUESTION_TITLE_PREFIX), question.title
    assert Labels.kind(KindLabel.question) in question.labels, question.labels
    assert question.parent is None, (
        f"質問に親が付いている（依存で張るはず＝ P5D-059 (2)）: {question.parent}"
    )


@pytest.mark.contract_if("IF-12")
@pytest.mark.cluster_case("04#12")
def test_cluster_04_escalation_links_the_question_with_a_dependency(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #12 / IF-12: 質問は `add_dependency` の辺で課題に紐づく。

    期待（§6 #12）: → `add_dependency` → release。

    実 `bd` の依存辺を読み戻し、**課題が質問に blocked_by されている**ことを確かめる。
    `--parent` の辺では `open_question_of` が拾えないので、この形が要る。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="依存で待つ課題")
    runtime = cluster([make_crew_outcome(escalation=_escalation())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    issue = board_reader.show(issue_id)
    question = board_reader.open_question_of(issue_id)
    assert question is not None, "質問課題が起票されていない"
    assert question.id in [ref.id for ref in issue.dependencies], (
        f"質問への依存が張られていない: {[ref.id for ref in issue.dependencies]}"
    )


@pytest.mark.contract_if("IF-14")
@pytest.mark.cluster_case("04#12")
def test_cluster_04_escalation_releases_the_issue(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #12 / IF-14: 質問を立てたら課題を **release** する（抱えたままにしない）。

    期待（§6 #12）: → release。

    release は `system_actor` で打たれる（質問の起票はメンバの `bd_actor`）。課題が close されて
    いないこと・assignee が戻っていることを実 `bd` から読み戻して確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="解放される課題")
    runtime = cluster([make_crew_outcome(escalation=_escalation())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    issue = board_reader.show(issue_id)
    assert not issue.assignee, f"確保したままになっている: {issue.assignee}"
    assert issue.status is not IssueStatus.closed, f"close されている: {issue.status}"


@pytest.mark.contract_if("IF-05")
@pytest.mark.cluster_case("04#12")
def test_cluster_04_escalation_reuses_an_open_question_without_creating_another(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    link: Callable[[str, str], None],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #12 / IF-05: 未回答の質問が既にあれば**再利用**し、起票しない（FN-08）。

    期待（§6 #12）: 未回答の質問が既にある → 再利用（起票しない）。

    ⚠ 未回答の質問がある課題は**起動時の拾い直しでは触られない**（`state:working` でないので
    そもそも列挙されない）。ここでは claim されて実行まで進み、エスカレーション時に
    `open_question_of` が既存を見つける形を作る。

    起票 0 回であることは、盤面の `kind:question` の総数が**増えていない**ことで見る。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="既に質問がある課題")
    existing = seed(KindLabel.question, ready=False, title=f"{QUESTION_TITLE_PREFIX} 既存の質問")
    link(issue_id, existing)
    runtime = cluster([make_crew_outcome(escalation=_escalation())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    question = board_reader.open_question_of(issue_id)
    assert question is not None, "質問が見つからない"
    assert question.id == existing, (
        f"既存の質問を再利用せず新しく起票している: 実際={question.id} 既存={existing}"
    )


@pytest.mark.contract_if("IF-28")
@pytest.mark.cluster_case("04#13")
def test_cluster_04_human_actor_is_recognised_from_the_real_settings(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #13 / IF-28: 台帳外の actor が `is_human` で真になる。

    期待（§6 #13）: 人間が bd comment で回答（台帳外 actor＝`is_human` True）。

    実 settings の `humans.yaml` に宣言した行為者と、宣言していない行為者（`system_actor`・
    AI メンバの `bd_actor`）の**両方**を渡して差を見る —— 「何でも真」でないことまで確かめる。
    """
    runtime = cluster([make_crew_outcome()])
    member_resolver: MemberResolver = require(runtime.wiring.provided, MemberResolver)
    human = stores.area_a.human_actor
    member = member_resolver.resolve(stores.project_key, stores.persona)

    assert member_resolver.is_human(human) is True, f"人間の宣言が効いていない: {human}"
    assert member_resolver.is_human(member.bd_actor) is False, (
        f"AI メンバが人間と判定された: {member.bd_actor}"
    )
    assert member_resolver.is_human(OrchestratorConfig().system_actor) is False, (
        "system_actor が人間と判定された"
    )


@pytest.mark.contract_if("IF-07")
@pytest.mark.cluster_case("04#13")
def test_cluster_04_human_answer_is_read_from_the_board_comments(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #13 / IF-07: 人間が書いた回答を `comments` から読み入力へ注入する。

    期待（§6 #13）: 次の claim で `open_question_of` None → 回答を `comments` から読み入力に
    注入して続きから kickoff。

    実 `bd` に**人間の actor**でコメントを書いてから走らせる。人間の本文だけが材料に載る
    （システム actor・AI メンバの書き込みは混ぜない＝ FR-029）ことを、`comments` の読み戻しで
    確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="人間の回答がある課題")
    seed_comment(stores.beads, issue_id, HUMAN_ANSWER, actor=stores.area_a.human_actor)
    # 完了を申告してもレビュー結果を返さないので差し戻しが `max_returns` 回まで回る。
    # 1 周につき work run とレビュー run の 2 回消費する（件数は実物の設定から導く）。
    runs = (OrchestratorConfig().max_returns + 1) * 2
    runtime = cluster([make_crew_outcome() for _ in range(runs)])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    comments = board_reader.comments(issue_id)
    human_bodies = [c.body for c in comments if c.actor == stores.area_a.human_actor]
    assert HUMAN_ANSWER in human_bodies, (
        f"人間の回答が盤面から読めない: {[(c.actor, c.body[:30]) for c in comments]}"
    )


@pytest.mark.contract_if("IF-07")
@pytest.mark.cluster_case("04#14")
def test_cluster_04_human_guidance_excludes_system_and_member_comments(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #14 / IF-07: 人間のコメントだけを材料に載せる（UC-03 の通過）。

    期待（§6 #14）: `comments`＋`is_human` で拾い ContextRefs／feedback に載る（FN-07/09/10）。

    **システム actor と AI メンバの書き込みを混ぜない**（FR-029。いずれも台帳内＝人間ではない）
    ことが要点なので、3 者（人間・`system_actor`・メンバ）のコメントを仕込んでから走らせ、
    `is_human` の判定と `comments` の actor が対応することを確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="3 者のコメントがある課題")
    member_resolver: MemberResolver = require(
        cluster([make_crew_outcome()]).wiring.provided, MemberResolver
    )
    member = member_resolver.resolve(stores.project_key, stores.persona)
    for actor, body in (
        (stores.area_a.human_actor, HUMAN_ANSWER),
        (OrchestratorConfig().system_actor, "システムの記録"),
        (member.bd_actor, "AI メンバの進捗"),
    ):
        seed_comment(stores.beads, issue_id, body, actor=actor)
    runs = (OrchestratorConfig().max_returns + 1) * 2
    runtime = cluster([make_crew_outcome() for _ in range(runs)])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    comments = board_reader.comments(issue_id)
    by_actor = {c.actor for c in comments}
    assert stores.area_a.human_actor in by_actor, f"人間のコメントが無い: {by_actor}"
    human_only = [c.body for c in comments if member_resolver.is_human(c.actor)]
    assert HUMAN_ANSWER in human_only, f"人間の本文が拾えない: {human_only}"
    assert "システムの記録" not in human_only, "system_actor を人間として拾っている"
    assert "AI メンバの進捗" not in human_only, "AI メンバを人間として拾っている"
