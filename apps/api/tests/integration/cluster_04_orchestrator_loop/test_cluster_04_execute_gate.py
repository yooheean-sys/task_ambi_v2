"""クラスタ 04 の完了ゲートとレビュー（IF-47・IF-36・IF-19）。§6 #10 ×2・#11。

`mode="execute"` の段は実配線で**到達できる**（受け皿の `plain`／親ありの `child`）ので、
ここは**鎖として**見られる —— 受入条文からトークンを抽出し（IF-47）、実 vault で実在を確かめ
（IF-36）、揃っていればレビューへ進み、承認なら実 `bd` を close する（IF-19）。

`CrewExecutor` は `SequencedCrewExecutor` に包んだスタブで、呼ばれる回数ぶんの `CrewOutcome`
を並びで渡す。**使い切ったら失敗する**ので、往復の読み違いが緑にならない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.wiring import require
from common.interfaces.board import BoardReader, IssueStatus
from common.interfaces.executor import ReviewVerdict
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.stages import ProposalGate
from common.kernel.enums import KindLabel
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_stores import ClusterStores
    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewOutcome
    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

OUTCOME_NAME = "report.md"
"""受入条文が名指しする成果物のファイル名。"""

ACCEPTANCE_WITH_OUTCOME = f"- [ ] outcome/{OUTCOME_NAME} を書く"
"""完了ゲートが実在を確かめる成果物を含む受入条文。"""


def _approved() -> ReviewVerdict:
    """レビュー承認の結果。"""
    return ReviewVerdict(approved=True, feedback="承認", files_read=[OUTCOME_NAME])


def _rejected() -> ReviewVerdict:
    """レビュー却下の結果（差し戻しへ倒す）。"""
    return ReviewVerdict(approved=False, feedback="やり直し", files_read=[])


@pytest.mark.contract_if("IF-47")
@pytest.mark.cluster_case("04#10")
def test_cluster_04_outcome_tokens_are_extracted_from_the_acceptance(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """クラスタ 04 #10 / IF-47: `extract_outcome_tokens` が受入条文から成果物を取り出す。

    期待（§6 #10）: 完了ゲート（`extract_outcome_tokens` → `outcome_exists`）。

    実 `ProposalGate` を配線から取り出して直接呼ぶ。**正準形の接頭辞（`outcome/`）で始まる
    トークンだけ**が拾われることを、拾われる形と拾われない形の対で確かめる。
    """
    runtime = cluster([make_crew_outcome()])
    proposal_gate: ProposalGate = require(runtime.wiring.provided, ProposalGate)

    tokens = proposal_gate.extract_outcome_tokens(ACCEPTANCE_WITH_OUTCOME)
    empty = proposal_gate.extract_outcome_tokens("- [ ] 特に成果物は無い")

    assert tokens, f"受入条文から成果物を拾えていない: {ACCEPTANCE_WITH_OUTCOME!r}"
    assert any(OUTCOME_NAME in token for token in tokens), tokens
    assert not empty, f"成果物の書かれていない条文から拾っている: {empty}"


@pytest.mark.contract_if("IF-36")
@pytest.mark.cluster_case("04#10")
def test_cluster_04_missing_outcome_sends_the_issue_back(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #10 / IF-36: 成果物が実 vault に無ければ**差し戻す**。

    期待（§6 #10）: 欠け／不正トークン → 差し戻し。揃っていればレビュー run（スタブ）へ。

    受入条文に `outcome/report.md` を書いた課題を仕込み、**実 vault には置かない**。
    `outcome_exists` が偽を返すので完了ゲートが通らず、`max_returns` まで差し戻してから人間へ
    渡る。差し戻しが起きたことは `returns` と盤面の記録で見る。
    """
    marker = OrchestratorConfig().comment_marker
    max_returns = OrchestratorConfig().max_returns
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, acceptance=ACCEPTANCE_WITH_OUTCOME, title="成果物が無い"
    )
    assert not (stores.vault.outcome_dir / OUTCOME_NAME).exists(), "前提: 成果物は置かれていない"
    runtime = cluster([make_crew_outcome() for _ in range(max_returns + 1)])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].returns >= 1, (
        f"完了ゲートが通ってしまっている: returns={result.issues[0].returns}"
    )
    comments = board_reader.comments(issue_id)
    reasons = [c.body for c in comments if c.body.startswith(marker)]
    assert reasons, f"差し戻しの記録が無い: {[c.body[:40] for c in comments]}"
    assert any(OUTCOME_NAME in reason for reason in reasons), (
        f"どの成果物が足りないかが記録されていない: {reasons}"
    )


@pytest.mark.contract_if("IF-19")
@pytest.mark.cluster_case("04#11")
def test_cluster_04_approved_review_closes_the_issue_on_the_real_board(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #11 / IF-19: レビュー承認なら実 `bd` の課題を close する。

    期待（§6 #11）: approved → close／却下 → 差し戻し（`max_returns` 超過は人間へ）。

    成果物を**実 vault に置いてから**走らせる（完了ゲートを通す）。work run とレビュー run の
    2 回ぶんの `CrewOutcome` を並びで渡し、レビュー側を承認にする。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, acceptance=ACCEPTANCE_WITH_OUTCOME, title="承認される課題"
    )
    (stores.vault.outcome_dir / OUTCOME_NAME).write_text("成果物\n", encoding="utf-8")
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].result == "closed", (
        f"close されていない: {result.issues[0].result} / returns={result.issues[0].returns}"
    )
    assert board_reader.show(issue_id).status is IssueStatus.closed


@pytest.mark.contract_if("IF-19")
@pytest.mark.cluster_case("04#11")
def test_cluster_04_rejected_review_sends_the_issue_back_instead_of_closing(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #11 / IF-19: レビュー却下なら close せず**差し戻す**。

    期待（§6 #11）: 却下 → 差し戻し（`max_returns` 超過は人間へ）。

    承認側（上のテスト）と**同じ土台**で結果だけを変える —— 差が「close するかどうか」に
    現れることを見るには、他の条件が揃っている必要がある。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, acceptance=ACCEPTANCE_WITH_OUTCOME, title="却下される課題"
    )
    (stores.vault.outcome_dir / OUTCOME_NAME).write_text("成果物\n", encoding="utf-8")
    max_returns = OrchestratorConfig().max_returns
    outcomes: list[CrewOutcome] = []
    for _ in range(max_returns + 1):
        outcomes.append(make_crew_outcome())
        outcomes.append(make_crew_outcome(review=_rejected()))
    runtime = cluster(outcomes)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].result != "closed", (
        f"却下されたのに close されている: {result.issues[0].result}"
    )
    assert result.issues[0].returns >= 1, f"差し戻しが起きていない: {result.issues[0].returns}"
    assert board_reader.show(issue_id).status is not IssueStatus.closed
