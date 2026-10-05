"""クラスタ 04 の前処理の正常経路（IF-01・IF-24・IF-25・IF-26・IF-50）。§6 #5 ×5。

確保した課題を読み直し（`show`）、段・主体・設定・フォルダを揃えて 1 課題ぶんの処理状態を組む
までを、**実 settings の読取 5 面**を通して確かめる。

観測は `CallLog` ではなく**盤面と領域 A の実物**で行う —— settings は実物なので、返る値は
`make_temp_area_a` が置いたファイルの中身と一致するはずである（スタブの記録を見るのは A 層の
やり方であり、ここは「実物同士が噛み合う」ことを見る）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.wiring import require
from common.interfaces.board import BoardReader
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.settings import SettingsReader
from common.kernel.enums import KindLabel, Labels, StageName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_stores import ClusterStores
    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewOutcome, ReviewVerdict
    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd


def _approved() -> ReviewVerdict:
    """レビュー承認の結果（1 周回を最後まで通す）。"""
    from common.interfaces.executor import ReviewVerdict as Verdict  # noqa: PLC0415

    return Verdict(approved=True, feedback="承認", files_read=[])


@pytest.mark.contract_if("IF-01")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_preflight_reads_the_claimed_issue_again(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #5 / IF-01: 確保した課題を `show` で**読み直す**。

    期待（§6 #5）: claim 後の `show`。

    `claim_next` の応答をそのまま使わず読み直していることは、**確保のあとに盤面へ付いたラベルが
    処理に反映される**ことで見る —— `claim_next` は `state:working` を**付けたあと**の姿を
    返さないので、読み直していれば `state:working` が付いた姿が使われる。

    ここでは 1 周回が最後まで通ることと、処理対象が仕込んだ課題であることを確かめる。

    ⚠ `kind:child` の段は **親が在ること**を要求する（`require_no_parent=False`）。親を付けない
    と `UnknownStage` で人間へ渡される（B-12 修正で SD-41 の事前検査が発火するようになった）
    ので、親を 1 件仕込んでから子を張る。親は `role:executor` を持たせない（確保の対象にしない）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="クラスタ 04 の親")
    issue_id = seed(KindLabel.child, parent=parent)
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].issue_id == issue_id, result.issues[0].issue_id
    assert result.issues[0].stage is StageName.child, (
        f"段の解決が食い違う: {result.issues[0].stage}"
    )
    assert board_reader.show(issue_id).id == issue_id


@pytest.mark.contract_if("IF-24")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_preflight_reads_the_real_limits(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #5 / IF-24: `get_limits` が**領域 A の実ファイル**から解決した上限を返す。

    期待（§6 #5）: 前処理 5 読取の正常経路（`get_limits`）。

    実 settings が `global/limits.yaml` の ⑤ 段（PJ 上書き）を解決していることを、
    `SettingsReader` を直接呼んで確かめる —— スタブ `RunMonitor` は `begin` に渡された上限を
    記録するが、04 の主眼は「実物が読めていること」なので領域 A の値と突き合わせる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent)
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    limits = settings_reader.get_limits(stores.project_key)
    assert limits.max_requests_per_kickoff > 0, limits
    assert limits.max_cost_per_kickoff > 0, limits
    assert limits.scope, f"解決の段が記録されていない: {limits}"


@pytest.mark.contract_if("IF-25")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_preflight_reads_the_real_rules(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #5 / IF-25: `get_rules` が `projects/<pj>/rules.md` の本文を返す。

    期待（§6 #5）: 前処理 5 読取の正常経路（`get_rules`）。

    期待値は**領域 A の実ファイル**から取る（テストへ直書きしない）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent)
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    expected = (stores.area_a.root / f"projects/{stores.project_key}/rules.md").read_text(
        encoding="utf-8"
    )

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    rules = settings_reader.get_rules(stores.project_key)
    assert rules.project_rules.strip() == expected.strip(), (
        f"`rules.md` の本文と食い違う: {rules.project_rules!r} / 期待={expected!r}"
    )


@pytest.mark.contract_if("IF-26")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_preflight_reads_the_real_hooks(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #5 / IF-26: `get_hooks` が `projects/<pj>/hooks.yaml` の宣言を返す。

    期待（§6 #5）: 前処理 5 読取の正常経路（`get_hooks`）。

    04 の領域 A は**宣言 0 件**で組んである（`hook_catalog` との照合を踏まないため）。したがって
    ここで見るのは「防御的に空へ落ちた」のではなく「**宣言 0 件を読み取った**」ことである ——
    区別のため、`hooks.yaml` の実ファイルが在って中身が空の一覧であることも確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent)
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    hooks_file = stores.area_a.root / f"projects/{stores.project_key}/hooks.yaml"

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert hooks_file.is_file(), "前提: `hooks.yaml` が領域 A に在る"
    assert "hooks: []" in hooks_file.read_text(encoding="utf-8"), "前提: 宣言は 0 件である"
    hooks = settings_reader.get_hooks(stores.project_key)
    assert hooks.hooks == [], f"宣言 0 件のはず: {hooks.hooks}"


@pytest.mark.contract_if("IF-50")
@pytest.mark.cluster_case("04#5")
def test_cluster_04_preflight_reads_the_real_task_templates(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #5 / IF-50: `get_tasks` が段テンプレートを返し `build_prompt` へ渡る。

    期待（§6 #5）: `get_tasks` は `build_prompt` へ引数で渡る（D16-h）。

    領域 A の `projects/<pj>/tasks.yaml` は `child` 段を**丸ごと上書き**している（キー単位の
    上書き＝ D16-a）。実 settings がその上書きを解決していることを、返るテンプレートの本文が
    PJ 側の値であることで確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    seed(KindLabel.child, parent=parent)
    runtime = cluster([make_crew_outcome(), make_crew_outcome(review=_approved())])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    pj_tasks = (stores.area_a.root / f"projects/{stores.project_key}/tasks.yaml").read_text(
        encoding="utf-8"
    )

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    templates = settings_reader.get_tasks(stores.project_key)
    child = templates.templates.get(str(StageName.child))
    assert child is not None, f"`child` 段のテンプレートが無い: {sorted(templates.templates)}"
    assert child.description.strip() in pj_tasks, (
        f"PJ 側の上書きが効いていない: {child.description!r}"
    )
    assert Labels.kind(KindLabel.child), "前提: `kind` ラベルの語彙が読める"
