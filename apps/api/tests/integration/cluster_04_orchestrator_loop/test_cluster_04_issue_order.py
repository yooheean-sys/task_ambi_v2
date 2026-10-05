"""クラスタ 04 の起票と盤面書込（IF-11・IF-12・IF-13・IF-19・IF-21・IF-16）。§6 #7 ×6・#15・#9 ×2。

B-12 は 2026-09-21 に修正済みで propose 段は実配線で到達可能になったが、propose 段の
鎖 E2E への格上げは**ユーザー裁定で行わない**（`test_cluster_04_propose_gate.py` の
module docstring 参照）。したがって「`create`×n → `add_dependency` →
`move_dependents` → 親 close」という**順序の不変条件そのもの**は鎖としては踏まない。

そこで**契約は実物同士で**確かめる —— 実 `BoardWriter`・実 `BoardReader` を実配線から取り出し、
実 `bd` に対して同じ順序で打って、各手が盤面に及ぼす効果を読み戻す。**本層固有の面**（実 `bd`
でしか確かめられない冪等性・確認読取・ラベルの排他）はここで見られる。

故障注入は BD_BIN シム **(b)**（`create` の `-l` を 1 組落とす → `BoardWriteUnverified`）と
**(c)**（N 回目から失敗 → 周回の途中での `BoardUnavailable`）を使う（規約 §6）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_shims import make_drops_one_label_shim, make_fails_after_n_shim
from _support.cluster_wiring import build_cluster_wiring

from api.wiring import require
from common.interfaces.board import (
    BoardReader,
    BoardUnavailable,
    BoardWriter,
    BoardWriteUnverified,
    IssueSpec,
    IssueStatus,
)
from common.kernel.enums import KindLabel, Labels, StateLabel
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _support.cluster_stores import ClusterStores
    from _support.cluster_wiring import ClusterRuntime

    from api.wiring import ProtocolOverrides
    from common.interfaces.executor import CrewOutcome

pytestmark = pytest.mark.requires_bd

SYSTEM_ACTOR = OrchestratorConfig().system_actor
"""起票の行為者（**実物の設定から引く**＝直書きしない。AI メンバの `bd_actor` とは分離）。"""

SINGLE_CHILD_NOTE = Labels.note("single-child")
"""子が 1 件だけのときに親へ付く印。"""


def _child_spec(stores: ClusterStores, parent: str, title: str) -> IssueSpec:
    """起票する子の仕様（ラベルは呼び手が全部載せる＝ FN-05）。"""
    return IssueSpec(
        title=title,
        description="クラスタ 04 が起票した子",
        labels=[
            Labels.project(stores.project_key),
            Labels.kind(KindLabel.child),
            Labels.persona(stores.persona),
            Labels.role_executor(),
        ],
        parent=parent,
        acceptance="- [ ] outcome/report.md を書く",
    )


@pytest.mark.contract_if("IF-11")
@pytest.mark.cluster_case("04#7")
def test_cluster_04_create_writes_the_child_and_verifies_it_by_reading_back(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #7 / IF-11: `create` は実 `bd` へ起票し、**読み戻して**確かめてから id を返す。

    期待（§6 #7）: 起票順序不変条件の 1 手目（`create`×n）。

    読み戻し検証（`set(labels)` の完全一致と `parent` の一致）が効いていることは、**返った id で
    読み直したときに spec どおりのラベルと親が付いている**ことで見る。`--no-inherit-labels` が
    効いていること（親のラベルが混ざらない）も対にして確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False, extra_labels=[Labels.note("parent-only")])
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    spec = _child_spec(stores, parent, "起票された子")

    created = board_writer.create(spec, SYSTEM_ACTOR)

    issue = board_reader.show(created)
    assert issue.parent == parent, f"親が付いていない: {issue.parent}"
    assert set(issue.labels) == set(spec.labels), (
        f"ラベルが spec と一致しない（継承が混ざった疑い）: {sorted(issue.labels)}"
    )
    assert Labels.note("parent-only") not in issue.labels, "親のラベルを継承している"


@pytest.mark.contract_if("IF-12")
@pytest.mark.cluster_case("04#7")
def test_cluster_04_add_dependency_is_idempotent_on_the_real_board(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #7 / IF-12: `add_dependency` が兄弟依存を張り、**冪等**である。

    期待（§6 #7）: `create`×n → `add_dependency`（B1 blocks）。

    冪等性（同じ辺への再実行も成功する）は**実 `bd` でしか確かめられない**面である —— board 側で
    事前確認をしていないので、bd が重複を弾く実装なら 2 回目で落ちる。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    first = board_writer.create(_child_spec(stores, parent, "先行の子"), SYSTEM_ACTOR)
    second = board_writer.create(_child_spec(stores, parent, "後続の子"), SYSTEM_ACTOR)

    board_writer.add_dependency(second, first, SYSTEM_ACTOR)
    board_writer.add_dependency(second, first, SYSTEM_ACTOR)  # 冪等（M-20）

    blocked = board_reader.show(second)
    depends_on = [ref.id for ref in blocked.dependencies]
    assert first in depends_on, f"兄弟依存が張られていない: {depends_on}"
    assert depends_on.count(first) == 1, f"同じ辺が二重に張られている（冪等でない）: {depends_on}"


@pytest.mark.contract_if("IF-13")
@pytest.mark.cluster_case("04#7")
def test_cluster_04_move_dependents_repoints_the_edges(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #7 / IF-13: `move_dependents` が依存元を新しい課題へ付け替える。

    期待（§6 #7）: → `move_dependents`。

    親に依存していた課題が、分解後は**子の 1 件**にも依存するようになる。実 `bd` の依存辺を
    読み戻して確かめる。

    ⚠ **古い辺は消えない**（2026-09-01 に実測）—— 実装（`board/service.py` の `move_dependents`）は
    `dep list --direction=up -t blocks` で依存元を集め、そこへ `dep add --blocked-by <new>` を
    打つだけで、`old` への辺を削除しない。§6 #7 の「付け替え」という語と実装の「付け足し」は
    食い違うので design-sync の B 表に記録する。ここでは**実装の振る舞い**を期待に採る。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    waiting = seed(KindLabel.child, ready=False, title="親を待っていた課題")
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    board_writer.add_dependency(waiting, parent, SYSTEM_ACTOR)
    child = board_writer.create(_child_spec(stores, parent, "付け替え先の子"), SYSTEM_ACTOR)

    board_writer.move_dependents(parent, child, SYSTEM_ACTOR)

    moved = board_reader.show(waiting)
    depends_on = [ref.id for ref in moved.dependencies]
    assert child in depends_on, f"新しい依存が張られていない: {depends_on}"
    assert parent in depends_on, (
        f"古い依存が消えている（実装は `dep add` だけを行い削除しないはず）: {depends_on}"
    )


@pytest.mark.contract_if("IF-19")
@pytest.mark.cluster_case("04#7")
def test_cluster_04_close_marks_the_parent_done_on_the_real_board(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """クラスタ 04 #7 / IF-19: `close` が実 `bd` の課題を閉じる。

    期待（§6 #7）: → 親 close／release。

    閉じたことは `status` の読み戻しで見る。閉じても**ラベルは消えない**ことまで確かめる
    （close が破壊的に働く実装との差）。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    board_writer.close(parent, "分解完了", SYSTEM_ACTOR)

    issue = board_reader.show(parent)
    assert issue.status is IssueStatus.closed, f"閉じていない: {issue.status}"
    assert Labels.kind(KindLabel.subrequest) in issue.labels, (
        f"close でラベルが消えている: {issue.labels}"
    )


@pytest.mark.contract_if("IF-04")
@pytest.mark.cluster_case("04#7")
def test_cluster_04_children_are_confirmed_by_reading_them_back(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #7 / IF-04: 起票した子を `children_of` で**確認読取**する。

    期待（§6 #7）: `children_of` で確認読取。

    「起票した id の集合 ⊆ 読み戻した子の集合」が分解層の確認読取の条件である（外れると
    UNSAFE で人間へ渡る）。実 `bd` で 2 件起票して読み戻し、順序に依存せず集合として一致する
    ことを見る。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    created = {
        board_writer.create(_child_spec(stores, parent, f"子 {index}"), SYSTEM_ACTOR)
        for index in range(2)
    }

    confirmed = {issue.id for issue in board_reader.children_of(parent)}

    assert created <= confirmed, f"起票した子を読み戻せない: 起票={created} 読戻={confirmed}"


@pytest.mark.contract_if("IF-21")
@pytest.mark.cluster_case("04#7")
def test_cluster_04_single_child_note_is_added_without_touching_other_labels(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """クラスタ 04 #7 / IF-21: single-child の観測は `note:single-child` の**付与だけ**を行う。

    期待（§6 #7）: single-child 観測 → `set_labels(note:single-child)`。

    付け外しが広すぎないこと（仕込んだラベルが消えない）を実 `bd` から読み戻して確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    before = set(board_reader.show(parent).labels)

    board_writer.set_labels(parent, add=[SINGLE_CHILD_NOTE], remove=[], actor=SYSTEM_ACTOR)

    after = set(board_reader.show(parent).labels)
    assert SINGLE_CHILD_NOTE in after, f"印が付いていない: {sorted(after)}"
    assert before <= after, f"既存のラベルが消えている: 前={sorted(before)} 後={sorted(after)}"


@pytest.mark.contract_if("IF-11")
@pytest.mark.cluster_case("04#15")
def test_cluster_04_create_uses_the_system_actor_not_the_member_actor(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #15 / IF-11: 起票の actor は **`system_actor`**（AI メンバとは分離）。

    期待（§6 #15）: `create` の actor が system_actor（AI メンバの bd_actor とは分離 P5D-037）。

    期待値は `OrchestratorConfig().system_actor` から引く（直書きしない）。実 `bd` の comment を
    使って行為者を観測する —— `create` 自体の actor は課題の作成者として bd に載るので、
    **メンバの `bd_actor` と異なる**ことを確かめる。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    runtime = cluster([make_crew_outcome()])
    board_writer: BoardWriter = require(runtime.wiring.provided, BoardWriter)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    created = board_writer.create(_child_spec(stores, parent, "actor の確認"), SYSTEM_ACTOR)

    board_writer.comment(created, "起票の記録", SYSTEM_ACTOR)

    comments = board_reader.comments(created)
    assert comments, "comment が入っていない"
    assert comments[-1].actor == SYSTEM_ACTOR, (
        f"actor が `system_actor` でない: {comments[-1].actor} / 期待={SYSTEM_ACTOR}"
    )
    assert f"{stores.project_key}.{stores.persona}" != SYSTEM_ACTOR, (
        "前提: `system_actor` と AI メンバの `bd_actor` は別物である"
    )


@pytest.mark.contract_if("IF-11")
@pytest.mark.cluster_case("04#9")
def test_cluster_04_create_reports_board_unavailable_partway_through(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_overrides: Callable[..., ProtocolOverrides],
) -> None:
    """クラスタ 04 #9 / IF-11: 起票の**途中**で盤面へ届かなくなったら `BoardUnavailable`。

    期待（§6 #9）: UNSAFE: 済み処理の一覧を添えて人間へ。**自動再試行しない**（FN-49・D01-b）。

    注入は BD_BIN シム **(c)**（**N 回目の `create` から**失敗）。(a)（常に失敗）では 1 件目から
    落ちて「途中」にならない。**1 件目は実 `bd` に本当に書かれる**ので、済み処理の一覧が
    空でないことまで確かめられる。

    board は例外を**握らない**（自動再試行しない）ことが要点なので、`pytest.raises` で
    そのまま上がることを見る。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    shim = make_fails_after_n_shim(tmp_path, threshold=2, only_subcommands=("create",))
    monkeypatch.setenv("BD_BIN", str(shim))
    wiring = build_cluster_wiring("04", make_overrides([make_crew_outcome()]))
    board_writer: BoardWriter = require(wiring.provided, BoardWriter)

    first = board_writer.create(_child_spec(stores, parent, "1 件目"), SYSTEM_ACTOR)
    with pytest.raises(BoardUnavailable):
        board_writer.create(_child_spec(stores, parent, "2 件目"), SYSTEM_ACTOR)

    monkeypatch.delenv("BD_BIN", raising=False)
    healthy = build_cluster_wiring("04", make_overrides([make_crew_outcome()]))
    healthy_reader: BoardReader = require(healthy.provided, BoardReader)
    assert healthy_reader.show(first).id == first, "1 件目が盤面に残っていない（途中失敗でない）"


@pytest.mark.contract_if("IF-16")
@pytest.mark.cluster_case("04#9")
def test_cluster_04_create_reports_write_unverified_when_a_label_is_dropped(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_overrides: Callable[..., ProtocolOverrides],
) -> None:
    """クラスタ 04 #9 / IF-16: 読み戻しが spec と食い違えば `BoardWriteUnverified`。

    期待（§6 #9）: `BoardWriteUnverified` は注入点が出来たら（`013` のシム (b) で成立済み）。

    注入は BD_BIN シム **(b)**（実 `bd` へ委譲しつつ `create` の `-l` を 1 組落とす）。課題は
    **実際に作られる**が、読み戻すとラベルが 1 つ足りないので board が「書けたことを確かめ
    られない」と判断する。**外部ツールの終了コードを成功の証拠にしない**（FR-055）ことの
    観測点である。
    """
    parent = seed(KindLabel.subrequest, ready=False)
    shim = make_drops_one_label_shim(tmp_path, stores.beads.bd_bin)
    monkeypatch.setenv("BD_BIN", str(shim))
    wiring = build_cluster_wiring("04", make_overrides([make_crew_outcome()]))
    board_writer: BoardWriter = require(wiring.provided, BoardWriter)

    with pytest.raises(BoardWriteUnverified):
        board_writer.create(_child_spec(stores, parent, "ラベルが落ちる子"), SYSTEM_ACTOR)

    # シムの効力は当該テストに閉じる（`monkeypatch.setenv` は fixture の終了で戻る）。
    assert Labels.state(StateLabel.failing).startswith("state:"), "前提: 状態ラベルの語彙が引ける"
