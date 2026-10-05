"""クラスタ 04 の完了ゲート: リポジトリの成果物（IF-56・IF-53）。§6 #18。D25-b。

受入条文の `repo/<パス>` を**実 stages** が抽出し（IF-56）、**実 repo** が一時の作業ツリーで実在を
確かめる（IF-53）。在ればレビューへ進み承認で close、無ければ差し戻す —— outcome の完了ゲート
（§6 #10・#11）と同じ鎖を、成果物の置き場だけ替えて通す。

作業ツリーは stores が `tmp_path/repo_work` に作り、領域 A の `project.yaml#repos.local_path` に
登録してある。テストは所在を**実 settings から**引く（見本を自前で作らない）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from api.wiring import require
from common.interfaces.board import BoardReader, IssueStatus
from common.interfaces.executor import ReviewVerdict
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.settings import SettingsReader
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

REPO_PATH = "src/app.py"
"""受入条文が名指しするリポジトリの成果物（リポジトリ相対）。"""

ACCEPTANCE_WITH_REPO = f"- [ ] repo/{REPO_PATH} に関数を足す"
"""完了ゲートがリポジトリでの実在を確かめる成果物を含む受入条文。"""


def _work_tree(runtime: ClusterRuntime, stores: ClusterStores) -> Path:
    """実 settings が返した作業ツリーの所在。"""
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    repos = settings_reader.get_project(stores.area_a.project_key).repos
    assert repos is not None
    assert repos.local_path is not None, "前提: 領域 A にリポジトリが登録されている"
    return Path(repos.local_path)


@pytest.mark.contract_if("IF-56")
@pytest.mark.cluster_case("04#18")
def test_cluster_04_repo_tokens_are_extracted_from_the_acceptance(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """クラスタ 04 #18 / IF-56: `extract_repo_tokens` が受入条文からリポジトリの成果物を取り出す。

    期待（§6 #18）: `repo/<パス>` だけが拾われ、outcome のトークンとは取り違えない。
    """
    runtime = cluster([make_crew_outcome()])
    proposal_gate: ProposalGate = require(runtime.wiring.provided, ProposalGate)

    tokens = proposal_gate.extract_repo_tokens(ACCEPTANCE_WITH_REPO)
    plain = proposal_gate.extract_repo_tokens("- [ ] outcome/report.md を書く")

    assert tokens == [f"repo/{REPO_PATH}"], tokens
    assert plain == [], f"outcome の成果物を repo として拾っている: {plain}"
    assert proposal_gate.extract_outcome_tokens(ACCEPTANCE_WITH_REPO) == []


@pytest.mark.contract_if("IF-53")
@pytest.mark.cluster_case("04#18")
def test_cluster_04_repo_deliverable_present_passes_the_gate_and_closes(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #18 / IF-53: 作業ツリーに成果物が在れば完了ゲートを通り、承認で close する。

    成果物を**実の作業ツリーに置いてから**走らせる。work run とレビュー run の 2 回ぶんの
    `CrewOutcome` を並びで渡し、レビュー側を承認にする（outcome の #11 と同じ形）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, acceptance=ACCEPTANCE_WITH_REPO, title="コードを足す課題"
    )
    approved = ReviewVerdict(approved=True, feedback="承認", files_read=[REPO_PATH])
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=approved)])
    work_tree = _work_tree(runtime, stores)
    (work_tree / "src").mkdir(parents=True, exist_ok=True)
    (work_tree / REPO_PATH).write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].result == "closed", (
        f"close されていない: {result.issues[0].result} / returns={result.issues[0].returns}"
    )
    assert board_reader.show(issue_id).status is IssueStatus.closed


@pytest.mark.contract_if("IF-53")
@pytest.mark.cluster_case("04#18")
def test_cluster_04_missing_repo_deliverable_sends_the_issue_back(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #18 / IF-53: 作業ツリーに成果物が無ければ**差し戻す**（欠けを記録する）。"""
    marker = OrchestratorConfig().comment_marker
    max_returns = OrchestratorConfig().max_returns
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, acceptance=ACCEPTANCE_WITH_REPO, title="コードが無い課題"
    )
    runtime = cluster([make_crew_outcome() for _ in range(max_returns + 1)])
    assert not (_work_tree(runtime, stores) / REPO_PATH).exists(), "前提: 成果物は置かれていない"
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].returns >= 1, (
        f"完了ゲートが通ってしまっている: returns={result.issues[0].returns}"
    )
    reasons = [c.body for c in board_reader.comments(issue_id) if c.body.startswith(marker)]
    assert any(f"repo/{REPO_PATH}" in reason for reason in reasons), (
        f"どの成果物が足りないかが記録されていない: {reasons}"
    )
