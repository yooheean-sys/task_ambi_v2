"""クラスタ 04 の前処理の失敗系（IF-23・IF-27・IF-44・IF-31・IF-16・IF-21）。§6 #5 ×6。

前処理で立つ 4 つの例外は、いずれも**人間へ渡す統一経路**へ合流する ——
`[orchestrator]` 理由コメント（IF-16）→ `state:failing`（IF-21）→ release（P5D-034）。

**`UnknownPersona` は踏まない**（R-12・plan (viii)）—— 72 行のどこにも現れず、A 層が提供側
適合で検証済みである。統一経路は残る 4 例外で十分観測できる。

`CrewExecutor` の並びは**空**にしてある —— 前処理で止まるはずなので、実行まで進めば
`SequencedCrewExecutor` が「並びを使い切った」で落ちる（空振りを緑にしない）。
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from api.wiring import require
from common.interfaces.board import BoardReader
from common.interfaces.orchestrator import KickoffRunner, KickoffStatus
from common.kernel.enums import KindLabel, Labels, PersonaKey, StateLabel
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_stores import ClusterStores
    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

MISSING_PROJECT_KEY = "nosuchpj"
"""領域 A に登録の無いプロジェクトキー（`ProjectNotFound` を踏む）。"""

MISSING_PERSONA = PersonaKey.analyst
"""`agents.yaml` に居ないペルソナ（`MemberNotFound` を踏む）。"""

UNKNOWN_KIND_LABEL = "kind:bogus"
"""段表が参照しない `kind` 値（`UnknownStage` を踏む）。"""


def _assert_handed_off(runtime: ClusterRuntime, issue_id: str) -> str:
    """人間へ渡す統一経路（理由コメント → `state:failing` → release）を盤面から確かめる。

    Args:
        runtime: 実物一式。
        issue_id: 対象の課題。

    Returns:
        見つかった理由コメントの本文（呼び手が原因の別を確かめる）。
    """
    marker = OrchestratorConfig().comment_marker
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue = board_reader.show(issue_id)
    assert Labels.state(StateLabel.failing) in issue.labels, (
        f"`state:failing` が付いていない（IF-21）: {issue.labels}"
    )
    assert not issue.assignee, f"release されていない: {issue.assignee}"
    comments = board_reader.comments(issue_id)
    reasons = [comment.body for comment in comments if comment.body.startswith(marker)]
    assert reasons, f"理由コメントが無い（IF-16。印={marker!r}）: {[c.body[:40] for c in comments]}"
    return reasons[0]


@pytest.mark.contract_if("IF-23")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_unknown_project_is_handed_off_to_a_human(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #5 / IF-23: 領域 A に無い `project:` は `ProjectNotFound` → 人間へ。

    期待（§6 #5）: 人間へ渡す統一経路（`[orchestrator]` 理由コメント → `state:failing` →
    release）。

    課題のラベルを未登録のキーに差し替える（`get_project` が実 settings で落ちる）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, project_key=MISSING_PROJECT_KEY, title="未登録 PJ"
    )
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)

    runner.run(context)

    reason = _assert_handed_off(runtime, issue_id)
    assert "ProjectNotFound" in reason or MISSING_PROJECT_KEY in reason, reason


@pytest.mark.contract_if("IF-27")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_unknown_member_is_handed_off_to_a_human(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #5 / IF-27: `agents.yaml` に居ないペルソナは `MemberNotFound` → 人間へ。

    期待（§6 #5）: 同じ統一経路。

    `persona:` ラベルで担当を名指しし、そのペルソナを領域 A に置かないことで踏む。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, persona=MISSING_PERSONA, title="未登録メンバ")
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)

    runner.run(context)

    reason = _assert_handed_off(runtime, issue_id)
    assert "MemberNotFound" in reason or MISSING_PERSONA.value in reason, reason


@pytest.mark.contract_if("IF-44")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_unresolvable_stage_is_handed_off_to_a_human(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #5 / IF-44: 段を決まらない入力は `UnknownStage` → 人間へ渡す統一経路。

    期待（§6 #5）: 統一経路（`[orchestrator]` 理由コメント → `state:failing` → release）。

    B-12 修正（2026-09-21）で、段表が参照しない `kind` 値は**実配線でも** `UnknownStage`
    に倒れる（orchestrator が接頭辞込みのラベルを渡すようになったため。修正前は値だけを
    渡していたため kind 集合が空になり、SD-41 の事前検査が素通りしていた）。`CrewExecutor`
    の並びは**空**のまま —— 前処理で止まるはずなので、実行まで進めば
    `SequencedCrewExecutor` が「並びを使い切った」で落ちる（空振りを緑にしない）。
    """
    issue_id = seed(None, extra_labels=[UNKNOWN_KIND_LABEL], title="段が決まらない課題")
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)

    result = runner.run(context)

    assert result.status is not KickoffStatus.stopped, f"周回が止まっている: {result.status}"
    reason = _assert_handed_off(runtime, issue_id)
    assert "UnknownStage" in reason, reason
    assert "bogus" in reason, reason
    assert any("段・主体が決まらないまま人間へ渡した" in note for note in result.notes), (
        result.notes
    )


@pytest.mark.contract_if("IF-31")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_missing_project_folder_is_handed_off_to_a_human(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #5 / IF-31: 宣言したフォルダが実 vault に無ければ人間へ。

    期待（§6 #5）: フォルダ不在（IF-31）→ 同じ統一経路。

    フォルダ検証は**課題ごとに毎回**行われる（FN-26）。実 vault から `outcome/` を消してから
    走らせ、`check_project_folders` が「不在」を返す形を作る。不在のパスが理由コメントに
    載ることまで見る。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="フォルダが無い課題")
    shutil.rmtree(stores.vault.outcome_dir)
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)

    runner.run(context)

    reason = _assert_handed_off(runtime, issue_id)
    assert "outcome" in reason, f"不在のパスが理由に載っていない: {reason}"


@pytest.mark.contract_if("IF-16")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_hand_off_writes_the_reason_comment(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #5 / IF-16: 人間へ渡すときは `[orchestrator]` の理由コメントを**書く**。

    期待（§6 #5）: `[orchestrator]` 理由コメント。

    印は `OrchestratorConfig().comment_marker` から引く（直書きしない）。**原因の別が本文に
    載る**ことまで見る —— 印だけの空コメントでは人間が何をすればよいか分からない。
    """
    marker = OrchestratorConfig().comment_marker
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, project_key=MISSING_PROJECT_KEY, title="理由の確認"
    )
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    comments = board_reader.comments(issue_id)
    reasons = [comment.body for comment in comments if comment.body.startswith(marker)]
    assert reasons, f"理由コメントが無い: {[c.body[:40] for c in comments]}"
    assert len(reasons[0]) > len(marker) + 1, f"印だけで理由が無い: {reasons[0]!r}"
    assert all(comment.actor for comment in comments), "actor の無いコメントがある"


@pytest.mark.contract_if("IF-21")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_hand_off_sets_the_failing_label_exclusively(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #5 / IF-21: 人間へ渡すときは `state:failing` を付け `state:working` を外す。

    期待（§6 #5）: `state:failing`（P5D-034）。

    状態は**排他**なので、`claim_next` が付けた `state:working` が消えていることまで見る ——
    付けるだけの実装では 2 つの状態が同居する。仕込んだ他のラベル（`kind`・`project`・
    `persona`）が消えていないことも確かめる（付け外しが広すぎないこと）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(
        KindLabel.child, parent=parent, project_key=MISSING_PROJECT_KEY, title="ラベルの確認"
    )
    runtime = cluster()
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    runner.run(context)

    issue = board_reader.show(issue_id)
    assert Labels.state(StateLabel.failing) in issue.labels, issue.labels
    assert Labels.state(StateLabel.working) not in issue.labels, (
        f"`state:working` が外れていない（状態が排他でない）: {issue.labels}"
    )
    assert Labels.kind(KindLabel.child) in issue.labels, (
        f"仕込んだラベルが消えている（付け外しが広すぎる）: {issue.labels}"
    )
