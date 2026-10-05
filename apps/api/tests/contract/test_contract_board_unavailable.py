"""board 提供 22 IF すべての `BoardUnavailable`（IF-01〜22）。US2。

台帳の board 22 行は**すべて**例外欄に `BoardUnavailable` を宣言しているので、
FR-017 に従って 22 件すべてで実際に踏む（**縮退 0 件**＝ SC-003）。

手段は **`BD_BIN` シム (a)「常に失敗」**（contracts/bd-shims.md §2）。board の `build()` は
`BD_BIN` を `shutil.which` ＋ `os.access(X_OK)` だけで確かめ `bd version` を打たないので、
任意の実行可能スクリプトを向けられる。シムは標準エラーへ `no beads database found` を出して
`rc=1` で終わり、board の `classify()` がこれを `unavailable` と判定する。

**`bd init` を打たない**（R-18）—— シムは argv を無視するので盤面の中身を一切使わない。
`config.yaml` を 1 つ置いた軽量 beads で board の起動時検査 (b)(c) を通せば足りる。

⚠ 但し書き（本ファイル末尾の 1 本だけは例外）: 軽量 beads は**実 `bd` に向けても**
`rc=1`・stderr `no beads database found` を返す —— つまり上の 22 本は
`make_always_fails_shim` が壊れていても緑のままで、**故障注入の手段が効いていることを
1 本も実証していない**。末尾の `..._is_caused_by_the_shim_not_the_beads_dir` だけは
**実 `bd init` した健全な盤面**を使い、シムだけが不達の原因である状況を作る。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import BOARD_ACTOR, BOARD_PROJECT_KEY
from contract_shims import UNAVAILABLE_MESSAGE

from common.interfaces.board import BoardUnavailable, ClaimFilter, IssueSpec
from common.kernel.enums import KindLabel, Labels, StateLabel

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter

pytestmark = pytest.mark.requires_bd

ISSUE_ID = f"{BOARD_PROJECT_KEY}-000001"
OTHER_ID = f"{BOARD_PROJECT_KEY}-000002"
CLAIM_FILTER = ClaimFilter(
    labels=frozenset({Labels.role_executor()}), project_key=BOARD_PROJECT_KEY
)
SPEC = IssueSpec(
    title="不達のときに起票を試みる",
    description="説明",
    labels=[Labels.kind(KindLabel.child), Labels.project(BOARD_PROJECT_KEY)],
    parent=None,
    acceptance="- [ ] 受け入れ条件",
)


# ------------------------------------------------------------------ 読取 9 件（IF-01〜09）


@pytest.mark.contract_if("IF-01")
def test_contract_board_show_raises_unavailable_for_orchestrator(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-01: `bd` に届かないとき `BoardUnavailable`（不在に倒さない）。"""
    with pytest.raises(BoardUnavailable) as caught:
        board_unavailable_reader.show(ISSUE_ID)
    assert caught.value.rc == 1
    assert "no beads database found" in caught.value.stderr_head


@pytest.mark.contract_if("IF-02")
def test_contract_board_show_raises_unavailable_for_executor(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-02: `read_issue` Tool の読取が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.show(ISSUE_ID)


@pytest.mark.contract_if("IF-03")
def test_contract_board_show_raises_unavailable_for_monitor(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-03: 写像後の確認読取が不達で `BoardUnavailable`（→ `MappingUnverified` の材料）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.show(ISSUE_ID)


@pytest.mark.contract_if("IF-04")
def test_contract_board_children_of_raises_unavailable(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-04: `children_of` が不達で `BoardUnavailable`（**空の一覧に倒さない**）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.children_of(ISSUE_ID)


@pytest.mark.contract_if("IF-05")
def test_contract_board_open_question_of_raises_unavailable(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-05: `open_question_of` が不達で `BoardUnavailable`（**`None` に倒さない**）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.open_question_of(ISSUE_ID)


@pytest.mark.contract_if("IF-06")
def test_contract_board_list_working_raises_unavailable(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-06: `list_working` が不達で `BoardUnavailable`（**空の一覧に倒さない**）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.list_working(CLAIM_FILTER)


@pytest.mark.contract_if("IF-07")
def test_contract_board_comments_raises_unavailable_for_orchestrator(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-07: `comments` が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.comments(ISSUE_ID)


@pytest.mark.contract_if("IF-08")
def test_contract_board_comments_raises_unavailable_for_monitor(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-08: 封筒の反映確認が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.comments(ISSUE_ID)


@pytest.mark.contract_if("IF-09")
def test_contract_board_has_open_issues_raises_unavailable(
    board_unavailable_reader: BoardReader,
) -> None:
    """IF-09: `has_open_issues` が不達で `BoardUnavailable`（**偽に倒さない**）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_reader.has_open_issues(BOARD_PROJECT_KEY)


# ----------------------------------------------------------------- 書込 13 件（IF-10〜22）


@pytest.mark.contract_if("IF-10")
def test_contract_board_claim_next_raises_unavailable(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-10: `claim_next` が不達で `BoardUnavailable`（**`None` に倒さない**）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.claim_next(CLAIM_FILTER, BOARD_ACTOR)


@pytest.mark.contract_if("IF-11")
def test_contract_board_create_raises_unavailable(board_unavailable_writer: BoardWriter) -> None:
    """IF-11: `create` が不達で `BoardUnavailable`（**成功に倒さない**＝ P3）。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.create(SPEC, BOARD_ACTOR)


@pytest.mark.contract_if("IF-12")
def test_contract_board_add_dependency_raises_unavailable(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-12: `add_dependency` が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.add_dependency(ISSUE_ID, OTHER_ID, BOARD_ACTOR)


@pytest.mark.contract_if("IF-13")
def test_contract_board_move_dependents_raises_unavailable(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-13: `move_dependents` が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.move_dependents(ISSUE_ID, OTHER_ID, BOARD_ACTOR)


@pytest.mark.contract_if("IF-14")
def test_contract_board_release_raises_unavailable_for_orchestrator(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-14: claim 解放が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.release(ISSUE_ID, BOARD_ACTOR)


@pytest.mark.contract_if("IF-15")
def test_contract_board_release_raises_unavailable_for_monitor(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-15: F 級写像の解放が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.release(ISSUE_ID, BOARD_ACTOR)


@pytest.mark.contract_if("IF-16")
def test_contract_board_comment_raises_unavailable_for_orchestrator(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-16: 停止理由の記録が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.comment(ISSUE_ID, "停止理由", BOARD_ACTOR)


@pytest.mark.contract_if("IF-17")
def test_contract_board_comment_raises_unavailable_for_executor(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-17: `record_progress` Tool の書込が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.comment(ISSUE_ID, "進捗", BOARD_ACTOR)


@pytest.mark.contract_if("IF-18")
def test_contract_board_comment_raises_unavailable_for_monitor(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-18: 封筒の書込が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.comment(ISSUE_ID, "等級: F", BOARD_ACTOR)


@pytest.mark.contract_if("IF-19")
def test_contract_board_close_raises_unavailable_for_orchestrator(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-19: 親 close／リーフ承認 close が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.close(ISSUE_ID, "完了", BOARD_ACTOR)


@pytest.mark.contract_if("IF-20")
def test_contract_board_close_raises_unavailable_for_monitor(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-20: W 級写像の close が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.close(ISSUE_ID, "W 級", BOARD_ACTOR)


@pytest.mark.contract_if("IF-21")
def test_contract_board_set_labels_raises_unavailable_for_orchestrator(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-21: 観測印・state 更新が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.set_labels(
            ISSUE_ID, add=[Labels.note("single-child")], remove=[], actor=BOARD_ACTOR
        )


@pytest.mark.contract_if("IF-22")
def test_contract_board_set_labels_raises_unavailable_for_monitor(
    board_unavailable_writer: BoardWriter,
) -> None:
    """IF-22: F 級写像の `state=failing` 付与が不達で `BoardUnavailable`。"""
    with pytest.raises(BoardUnavailable):
        board_unavailable_writer.set_labels(
            ISSUE_ID, add=[Labels.state(StateLabel.failing)], remove=[], actor=BOARD_ACTOR
        )


# ------------------------------------- 故障注入の手段そのものの実証（FR-021a (a)・SC-003）


@pytest.mark.contract_if("IF-01")
def test_contract_board_unavailable_is_caused_by_the_shim_not_the_beads_dir(
    board_unavailable_on_healthy_reader: BoardReader,
) -> None:
    """IF-01（再宣言）: **シム (a) が効いている**こと自体を実証する。

    上の 22 本は軽量 beads（`bd init` を打たない＝ R-18）を掴むので、**シムが無くても実 `bd` が
    同じ `BoardUnavailable` を返す**。したがって `make_always_fails_shim` が壊れても
    （生成されない・実行権が付かない・`rc=0` で終わる）22 本は緑のままで、FR-021a (a) が
    要求する「CLI 境界からの故障注入」は誰にも確かめられていない。本テストだけは
    **実 `bd init` した健全な盤面**を掴み、`BoardUnavailable` の原因がシムであることを言う。

    区別の根拠は**文言**である —— 実 `bd` は `Error: ` の接頭辞と `Hint: ...` の行を出すが、
    シムは `UNAVAILABLE_MESSAGE` をそのまま 1 行出すだけである（`contract_shims.py`）。
    期待値はテストへ直書きせず、シムの定数を import して取る（FR-004）。

    **シム (b) はこの穴を持たない** —— シムが無ければ `create` は成功するので、
    `BoardWriteUnverified` の 1 本は注入が効いていることを自ずと示す。
    """
    with pytest.raises(BoardUnavailable) as caught:
        board_unavailable_on_healthy_reader.show(ISSUE_ID)
    assert caught.value.rc == 1
    assert caught.value.stderr_head.strip() == UNAVAILABLE_MESSAGE
    assert "Hint:" not in caught.value.stderr_head
