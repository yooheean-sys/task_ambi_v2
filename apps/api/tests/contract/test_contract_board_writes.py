"""board 提供の書込 IF（IF-10〜22）の適合検証。US2。

実 `bd` CLI 越しに、`BoardWriter` の 8 メソッドが台帳どおりに振る舞うことを確かめる。
台帳の重複行（`release`＝ IF-14・15／`comment`＝ IF-16・17・18／`close`＝ IF-19・20／
`set_labels`＝ IF-21・22）は**消費者ごとの観点で書き分ける**（FR-002a・conventions §4-1）。

`BoardUnavailable`（22 行すべてが宣言）は `test_contract_board_unavailable.py`、
`BoardWriteUnverified`（IF-11）は `test_contract_board_write_unverified.py` で踏む。

台帳の**出力欄が `None`** の行（IF-12〜22）は、戻り値が `None` であることまで主張する
（FR-015「各テストは台帳の出力欄が宣言する型のインスタンスが返ることを確かめる」）——
呼び捨てにすると、コルーチンを返す実装（＝台帳の `同期/非同期` 欄違反）でも緑になる
（`design-sync.md` B-29）。同じメソッドを 2 回呼ぶ行は**両方**受ける。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import BOARD_ACTOR, BOARD_PROJECT_KEY

from common.interfaces.board import ClaimFilter, Issue, IssueSpec, IssueStatus
from common.kernel.enums import KindLabel, Labels, StateLabel

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter

pytestmark = pytest.mark.requires_bd

PROJECT_LABEL = Labels.project(BOARD_PROJECT_KEY)
EXECUTOR_FILTER = ClaimFilter(
    labels=frozenset({Labels.role_executor()}), project_key=BOARD_PROJECT_KEY
)
OTHER_PROJECT_KEY = "otherpj"
"""一時盤面に同居させるもう 1 つの PJ キー（`project_key` の絞り込みの観測点）。"""

OTHER_PROJECT_LABEL = Labels.project(OTHER_PROJECT_KEY)
ANY_PROJECT_FILTER = ClaimFilter(labels=frozenset({Labels.role_executor()}), project_key=None)
"""台帳 IF-10 の入力欄が言う「**任意** project_key」の `None` 側（PJ で絞らない）。"""

ACCEPTANCE = "- [ ] 受け入れ条件"


def _spec(title: str, labels: list[str], parent: str | None = None) -> IssueSpec:
    """契約テストが使う `IssueSpec`。"""
    return IssueSpec(
        title=title,
        description="説明",
        labels=labels,
        parent=parent,
        acceptance=ACCEPTANCE,
    )


@pytest.fixture
def parent_id(board_writer: BoardWriter) -> str:
    """親 1 件（ラベルを持たせて `create` の非継承を観測できるようにする）。"""
    return board_writer.create(
        _spec("親", [Labels.kind(KindLabel.program), PROJECT_LABEL, Labels.note("parent")]),
        BOARD_ACTOR,
    )


@pytest.fixture
def task_id(board_writer: BoardWriter, parent_id: str) -> str:
    """確保できる子 1 件（ブロックする質問を持たせない）。"""
    return board_writer.create(
        _spec(
            "実行対象",
            [Labels.kind(KindLabel.child), PROJECT_LABEL, Labels.role_executor()],
            parent_id,
        ),
        BOARD_ACTOR,
    )


@pytest.mark.contract_if("IF-10")
def test_contract_board_claim_next_returns_the_claimed_issue(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-10: `claim_next` が `Issue | None` を返し、`state=working` を付与する。

    台帳の出力欄が名指しする 2 点（`bd ready --claim` 相当・`state=working` の付与）を
    確かめる。対象が無ければ `None`（例外に倒さない）。
    """
    claimed = board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR)

    assert isinstance(claimed, Issue)
    assert claimed.id == task_id
    readback = board_reader.show(task_id)
    assert Labels.state(StateLabel.working) in readback.labels
    assert readback.status is IssueStatus.in_progress
    assert readback.assignee == BOARD_ACTOR

    assert board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR) is None


@pytest.mark.contract_if("IF-10")
def test_contract_board_claim_next_honours_the_optional_project_key(
    board_writer: BoardWriter, task_id: str
) -> None:
    """IF-10: 入力欄の「**任意** project_key」の **`None` 分岐**でも確保できる（FR-002a）。

    台帳 IF-10 の入力欄は `ClaimFilter（ラベル集合＋**任意** project_key）・actor` であり、
    現物は `project_key is not None` のときだけ `project:<key>` を `-l` に足す**分岐**を持つ。
    ところが契約テストの `ClaimFilter` は**すべて `project_key` を与えており**、`None` 側を
    一度も通らない —— **`None` を無視して常に PJ で絞る実装でも、逆に PJ ラベルを一切足さない
    実装でも緑**になる。

    そこで**別の PJ ラベル**を持つ `role:executor` の課題をもう 1 件仕込み、2 通りを並べる ——
    (a) `project_key=<この PJ>` では、自 PJ の 1 件を取り切ったあと `None` が返る（別 PJ の
    課題は拾われない） (b) `project_key=None` では、その別 PJ の課題が拾える。
    2 つを並べて初めて分岐が効いていることを区別できる。
    """
    other = board_writer.create(
        _spec(
            "別 PJ の実行対象",
            [Labels.kind(KindLabel.child), OTHER_PROJECT_LABEL, Labels.role_executor()],
        ),
        BOARD_ACTOR,
    )

    # (a) PJ で絞る側 —— 自 PJ の 1 件だけを確保し、別 PJ の課題は残る。
    assert board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR) is not None
    assert board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR) is None, (
        "別 PJ の課題を拾っている（project_key の絞り込みが効いていない）"
    )

    # (b) 絞らない側 —— 同じラベル集合でも別 PJ の課題に届く。
    claimed = board_writer.claim_next(ANY_PROJECT_FILTER, BOARD_ACTOR)

    assert isinstance(claimed, Issue)
    assert claimed.id == other, f"task_id={task_id} は (a) で確保済みなので other が返るはず"


@pytest.mark.contract_if("IF-11")
def test_contract_board_create_returns_issue_id_after_readback_verification(
    board_reader: BoardReader, board_writer: BoardWriter, parent_id: str
) -> None:
    """IF-11 正常系: `create` が新しい issue_id を返す（読み戻し検証を通る）。

    確認読取は「`set(labels)` の完全一致と `parent` の一致」を見る。**親のラベルを
    継承しない**ことがその一致の前提である。
    """
    labels = [Labels.kind(KindLabel.child), PROJECT_LABEL, Labels.role_executor()]

    new_id = board_writer.create(_spec("起票された子", labels, parent_id), BOARD_ACTOR)

    assert isinstance(new_id, str)
    assert new_id
    readback = board_reader.show(new_id)
    assert set(readback.labels) == set(labels)
    assert readback.parent == parent_id
    assert Labels.note("parent") not in readback.labels


@pytest.mark.contract_if("IF-12")
def test_contract_board_add_dependency_creates_a_blocks_edge(
    board_reader: BoardReader, board_writer: BoardWriter, parent_id: str, task_id: str
) -> None:
    """IF-12: `add_dependency` が兄弟依存（blocks）を張り `None` を返す。"""
    blocker = board_writer.create(_spec("先行", [PROJECT_LABEL], parent_id), BOARD_ACTOR)

    result = board_writer.add_dependency(task_id, blocker, BOARD_ACTOR)

    assert result is None
    assert blocker in {ref.id for ref in board_reader.show(task_id).dependencies}


@pytest.mark.contract_if("IF-13")
def test_contract_board_move_dependents_repoints_edges(
    board_reader: BoardReader, board_writer: BoardWriter, parent_id: str
) -> None:
    """IF-13: `move_dependents` が `new_id` への依存を張り `None` を返す。

    **ここで主張するのは「新が張られた」ことだけ**である。台帳の出力欄が言う「付け替え」
    （＝旧が外れる）は現物が満たしておらず、下の `..._does_not_detach_the_old_edge` が
    その食い違いを `xfail(strict=True)` として保持する（design-sync.md B-16）。
    """
    old = board_writer.create(_spec("旧", [PROJECT_LABEL], parent_id), BOARD_ACTOR)
    new = board_writer.create(_spec("新", [PROJECT_LABEL], parent_id), BOARD_ACTOR)
    dependent = board_writer.create(_spec("後続", [PROJECT_LABEL], parent_id), BOARD_ACTOR)
    board_writer.add_dependency(dependent, old, BOARD_ACTOR)
    assert old in {ref.id for ref in board_reader.show(dependent).dependencies}

    result = board_writer.move_dependents(old, new, BOARD_ACTOR)

    assert result is None
    assert new in {ref.id for ref in board_reader.show(dependent).dependencies}


@pytest.mark.contract_if("IF-13")
@pytest.mark.xfail(
    strict=True,
    reason="現物は新を足すだけで旧を外さない（台帳 IF-13 の『付け替え』と食い違う）"
    " —— design-sync.md B-16。board は FR-030 の変更禁止範囲なので、テストは食い違いを"
    "保持するだけにする。実装が直れば strict=True によりここが赤くなり、裁定へ戻される",
)
def test_contract_board_move_dependents_does_not_detach_the_old_edge(
    board_reader: BoardReader, board_writer: BoardWriter, parent_id: str
) -> None:
    """IF-13 の**未達**: 台帳の「dependents 付け替え」が「追加」に留まっている（FR-016）。

    台帳 IF-13 の出力欄は `None（dependents **付け替え**）`、`common/interfaces/board.py`
    の Protocol docstring も「依存元を**付け替える**」である。しかし現物
    （`packages/features/board/src/board/service.py:299-328`）は `dep list --direction=up
    -t blocks` で依存元を数え上げたあと `dep add <dependent> --blocked-by <new>` を打つだけで、
    `old` への辺を落とす手を 1 つも持たない。

    上のテストのように新参照の混入だけを見ると**旧を残したまま足すだけの実装でも緑**になる。
    ここは「旧が外れる」を素直に主張し、`xfail(strict=True)` で**未達のまま保持**する ——
    埋めずに指摘する（CLAUDE.md）ためであり、修正は FR-030 の変更禁止範囲に入る。
    """
    old = board_writer.create(_spec("旧", [PROJECT_LABEL], parent_id), BOARD_ACTOR)
    new = board_writer.create(_spec("新", [PROJECT_LABEL], parent_id), BOARD_ACTOR)
    dependent = board_writer.create(_spec("後続", [PROJECT_LABEL], parent_id), BOARD_ACTOR)
    board_writer.add_dependency(dependent, old, BOARD_ACTOR)

    board_writer.move_dependents(old, new, BOARD_ACTOR)

    after = {ref.id for ref in board_reader.show(dependent).dependencies}
    assert new in after
    assert old not in after, "旧依存が外れていない（付け替えではなく追加になっている）"


@pytest.mark.contract_if("IF-14")
def test_contract_board_release_frees_the_claim_for_orchestrator(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-14（orchestrator 観点）: claim 解放 —— `state=working` を外して再確保できる。

    orchestrator は 1 課題を処理し終えたら解放し、**次の kickoff で同じ課題を確保できる**
    状態に戻す。台帳の出力欄が名指しするのはこの「claim 解放」である。
    """
    assert board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR) is not None

    result = board_writer.release(task_id, BOARD_ACTOR)

    assert result is None
    released = board_reader.show(task_id)
    assert Labels.state(StateLabel.working) not in released.labels
    assert released.status is IssueStatus.open
    assert released.assignee is None
    again = board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR)
    assert again is not None
    assert again.id == task_id


@pytest.mark.contract_if("IF-15")
def test_contract_board_release_is_part_of_the_failure_mapping_for_monitor(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-15（monitor 観点）: F 級写像の一部としての解放。

    monitor の F 級写像は「封筒 `comment` → `set_labels(state=failing)` → `release`」であり、
    **解放しても `state=failing` は残る**（`state` 次元は排他だが `working` を外すだけでは
    `failing` を消さない）。人間が失敗を見つけられる状態で手を離すことが観点である。
    """
    assert board_writer.claim_next(EXECUTOR_FILTER, BOARD_ACTOR) is not None
    board_writer.set_labels(
        task_id, add=[Labels.state(StateLabel.failing)], remove=[], actor=BOARD_ACTOR
    )

    result = board_writer.release(task_id, BOARD_ACTOR)

    assert result is None
    after = board_reader.show(task_id)
    assert Labels.state(StateLabel.working) not in after.labels
    assert Labels.state(StateLabel.failing) in after.labels


@pytest.mark.contract_if("IF-16")
def test_contract_board_comment_records_the_stop_reason_for_orchestrator(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-16（orchestrator 観点）: 停止理由・UNSAFE 記録を 1 行残す。

    orchestrator は「なぜ止めたか」を盤面に残してから手を離す。`-` で始まる本文でも
    フラグと誤読されず**無改変で**残ることが観点である。
    """
    body = "- 停止理由: UNSAFE 判定（人間の確認を待つ）"

    result = board_writer.comment(task_id, body, BOARD_ACTOR)

    assert result is None
    assert [item.body for item in board_reader.comments(task_id)] == [body]


@pytest.mark.contract_if("IF-17")
def test_contract_board_comment_records_progress_for_executor(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-17（executor 観点）: `record_progress` Tool の 1 行。

    Tool は AI が書いた自由文をそのまま渡す（`RecordProgressTool`）。**成功したときだけ**
    進捗として数えられるので、書けたことが `comments` から観測できる必要がある。
    """
    assert board_writer.comment(task_id, "進捗: 入力ファイルを読み終えた", BOARD_ACTOR) is None
    assert board_writer.comment(task_id, "進捗: 下書きを書いた", BOARD_ACTOR) is None

    bodies = [item.body for item in board_reader.comments(task_id)]
    assert bodies == ["進捗: 入力ファイルを読み終えた", "進捗: 下書きを書いた"]


@pytest.mark.contract_if("IF-18")
def test_contract_board_comment_writes_the_envelope_for_monitor(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-18（monitor 観点）: **封筒形**の body（等級・種類・実測値）。

    台帳の入力欄が body の形を名指ししている唯一の行である。複数行の封筒が改行ごと
    無改変で残ることを確かめる。
    """
    envelope = "等級: F\n種類: limit_cost\n実測値: 1.25 USD / 上限 1.00 USD"

    result = board_writer.comment(task_id, envelope, BOARD_ACTOR)

    assert result is None
    stored = [item.body for item in board_reader.comments(task_id)]
    assert stored == [envelope]
    assert "等級: F" in stored[0]
    assert "\n" in stored[0]


@pytest.mark.contract_if("IF-19")
def test_contract_board_close_closes_parent_and_leaf_for_orchestrator(
    board_reader: BoardReader, board_writer: BoardWriter, parent_id: str, task_id: str
) -> None:
    """IF-19（orchestrator 観点）: 親 close ／ リーフ承認 close の 2 経路。

    台帳の出力欄が名指しする 2 つの呼ばれ方（子をすべて閉じたあとの親 close と、
    レビュー承認によるリーフ close）がどちらも `None` を返して状態を変えることを確かめる。
    """
    assert board_writer.close(task_id, "レビュー承認", BOARD_ACTOR) is None
    assert board_reader.show(task_id).status is IssueStatus.closed

    assert board_writer.close(parent_id, "子がすべて閉じた", BOARD_ACTOR) is None
    assert board_reader.show(parent_id).status is IssueStatus.closed


@pytest.mark.contract_if("IF-20")
def test_contract_board_close_is_the_warning_mapping_for_monitor(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-20（monitor 観点）: W 級写像 —— close ＋封筒。

    W 級は「close したうえで封筒を残す」。close の**あとでも**コメントが付けられること
    （閉じた課題に封筒を残せること）が観点である。
    """
    assert board_writer.close(task_id, "W 級として閉じた", BOARD_ACTOR) is None
    board_writer.comment(task_id, "等級: W\n種類: retry_succeeded", BOARD_ACTOR)

    assert board_reader.show(task_id).status is IssueStatus.closed
    assert "等級: W" in board_reader.comments(task_id)[0].body


@pytest.mark.contract_if("IF-21")
def test_contract_board_set_labels_marks_observation_for_orchestrator(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-21（orchestrator 観点）: `note=single-child` の観測印と state 更新。

    台帳の出力欄が名指しする 2 用途 —— 観測印（`note:` 次元）の付与と `state` 次元の
    更新 —— を 1 回の `set_labels` で行い、`state` が**排他**であることを確かめる。
    """
    result = board_writer.set_labels(
        task_id,
        add=[Labels.note("single-child"), Labels.state(StateLabel.working)],
        remove=[Labels.role_executor()],
        actor=BOARD_ACTOR,
    )

    assert result is None
    labels = set(board_reader.show(task_id).labels)
    assert Labels.note("single-child") in labels
    assert Labels.state(StateLabel.working) in labels
    assert Labels.role_executor() not in labels


@pytest.mark.contract_if("IF-22")
def test_contract_board_set_labels_adds_state_failing_for_monitor(
    board_reader: BoardReader, board_writer: BoardWriter, task_id: str
) -> None:
    """IF-22（monitor 観点）: `add` が **`state=failing`**。

    台帳の入力欄が `add` の値まで名指ししている唯一の行である。F 級写像は
    `state:working` を `state:failing` に**置き換える**（`state` 次元は排他）。
    """
    prepared = board_writer.set_labels(
        task_id, add=[Labels.state(StateLabel.working)], remove=[], actor=BOARD_ACTOR
    )

    result = board_writer.set_labels(
        task_id, add=[Labels.state(StateLabel.failing)], remove=[], actor=BOARD_ACTOR
    )

    assert prepared is None
    assert result is None
    labels = set(board_reader.show(task_id).labels)
    assert Labels.state(StateLabel.failing) in labels
    assert Labels.state(StateLabel.working) not in labels
