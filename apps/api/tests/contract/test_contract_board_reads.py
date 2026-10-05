"""board 提供の読取 IF（IF-01〜09）の適合検証。US2。

実 `bd` CLI 越しに、`BoardReader` の 6 メソッドが台帳どおりの型を返し台帳どおりの例外を
投げることを確かめる。台帳の重複 3 行（`show`＝ IF-01・02・03）と 2 行（`comments`＝
IF-07・08）は、**消費者ごとの観点で書き分ける**（FR-002a・conventions §4-1）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import BOARD_ACTOR, BOARD_PROJECT_KEY

from common.interfaces.board import (
    ClaimFilter,
    Comment,
    Issue,
    IssueNotFound,
    IssueSpec,
    IssueStatus,
)
from common.kernel.enums import KindLabel, Labels, PersonaKey, StateLabel

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter

pytestmark = pytest.mark.requires_bd

MISSING_ISSUE_ID = f"{BOARD_PROJECT_KEY}-zzzzzz"
"""実 `bd` に存在しない issue_id（`IssueNotFound` を踏む）。"""

ACCEPTANCE = "- [ ] 受け入れ条件の 1 行目\n- [ ] 2 行目"
DESCRIPTION = "課題の説明本文（複数行）\n2 行目"


def _spec(title: str, labels: list[str], parent: str | None = None) -> IssueSpec:
    """契約テストが使う `IssueSpec`（`acceptance` は複数行の箇条書き）。"""
    return IssueSpec(
        title=title,
        description=DESCRIPTION,
        labels=labels,
        parent=parent,
        acceptance=ACCEPTANCE,
    )


@pytest.fixture
def tree(board_writer: BoardWriter) -> dict[str, str]:
    """親 1・子 2（うち 1 件は claim 可能）・孫 1・質問 1 の小さな盤面を仕込む。"""
    project = Labels.project(BOARD_PROJECT_KEY)
    parent = board_writer.create(
        _spec("親", [Labels.kind(KindLabel.program), project]), BOARD_ACTOR
    )
    child = board_writer.create(
        _spec(
            "実行対象の子",
            [
                Labels.kind(KindLabel.child),
                project,
                Labels.role_executor(),
                Labels.persona(PersonaKey.programmer),
            ],
            parent,
        ),
        BOARD_ACTOR,
    )
    other_child = board_writer.create(
        _spec("もう 1 件の子", [Labels.kind(KindLabel.child), project], parent), BOARD_ACTOR
    )
    grandchild = board_writer.create(
        _spec("孫", [Labels.kind(KindLabel.child), project], child), BOARD_ACTOR
    )
    question = board_writer.create(
        _spec("未回答の質問", [Labels.kind(KindLabel.question), project]), BOARD_ACTOR
    )
    board_writer.add_dependency(child, question, BOARD_ACTOR)
    return {
        "parent": parent,
        "child": child,
        "other_child": other_child,
        "grandchild": grandchild,
        "question": question,
    }


@pytest.mark.contract_if("IF-01")
def test_contract_board_show_returns_dm13_fields_for_orchestrator(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-01（orchestrator 観点）: `show` が DM-13 の全項目を持つ `Issue` を返す。

    台帳の出力欄が名指しする 5 項目 —— labels・parent・acceptance・status・claim 状態
    （`assignee`）—— がすべて読めることを確かめる（FR-015・FR-016）。
    """
    issue = board_reader.show(tree["child"])

    assert isinstance(issue, Issue)
    assert issue.id == tree["child"]
    assert set(issue.labels) == {
        Labels.kind(KindLabel.child),
        Labels.project(BOARD_PROJECT_KEY),
        Labels.role_executor(),
        Labels.persona(PersonaKey.programmer),
    }
    assert issue.parent == tree["parent"]
    assert issue.acceptance == ACCEPTANCE
    assert issue.status is IssueStatus.open
    assert issue.assignee is None  # claim 状態（未確保）


@pytest.mark.contract_if("IF-02")
def test_contract_board_show_returns_body_for_executor_read_issue_tool(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-02（executor 観点）: `read_issue` Tool が本文として組む 4 項目が読める。

    Tool は `title`・`description`・`acceptance`・`labels` だけを本文へ写す
    （`executor/tools/board_tools.py` の `ReadIssueTool`）。改行を含む本文が**無改変で**
    読み戻ることがこの観点の要である。
    """
    issue = board_reader.show(tree["child"])

    assert issue.title == "実行対象の子"
    assert issue.description == DESCRIPTION
    assert issue.acceptance == ACCEPTANCE
    assert issue.labels, "ラベルが空だと Tool の本文が「(なし)」に潰れる"


@pytest.mark.contract_if("IF-03")
def test_contract_board_show_reflects_mapping_for_monitor_verification(
    board_reader: BoardReader, board_writer: BoardWriter, tree: dict[str, str]
) -> None:
    """IF-03（monitor 観点）: **写像後の**確認読取が写像の結果を返す。

    monitor の `finish` は F 級写像（`state:failing` の付与）のあとに `show` を読んで
    写像が効いたことを確かめる。**書いた直後の読取が古い姿を返さない**ことが観点である。
    """
    before = board_reader.show(tree["child"])
    assert Labels.state(StateLabel.failing) not in before.labels

    board_writer.set_labels(
        tree["child"], add=[Labels.state(StateLabel.failing)], remove=[], actor=BOARD_ACTOR
    )

    after = board_reader.show(tree["child"])
    assert Labels.state(StateLabel.failing) in after.labels


@pytest.mark.contract_if("IF-01")
def test_contract_board_show_raises_issue_not_found_for_orchestrator(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-01 例外系: 不在の issue_id で `IssueNotFound`（**空や `None` に倒れない**）。"""
    assert tree
    with pytest.raises(IssueNotFound) as caught:
        board_reader.show(MISSING_ISSUE_ID)
    assert caught.value.issue_id == MISSING_ISSUE_ID


@pytest.mark.contract_if("IF-02")
def test_contract_board_show_raises_issue_not_found_for_executor(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-02 例外系: `read_issue` Tool は不在を `IssueNotFound` で受け取る（握り潰さない）。"""
    assert tree
    with pytest.raises(IssueNotFound):
        board_reader.show(MISSING_ISSUE_ID)


@pytest.mark.contract_if("IF-03")
def test_contract_board_show_raises_issue_not_found_for_monitor(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-03 例外系: 確認読取が不在に当たったら `MappingUnverified` の材料として例外が要る。"""
    assert tree
    with pytest.raises(IssueNotFound):
        board_reader.show(MISSING_ISSUE_ID)


@pytest.mark.contract_if("IF-04")
def test_contract_board_children_of_returns_direct_children(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-04: `children_of` が**盤面から導出した**直接の子だけを返す（孫を含まない）。"""
    children = board_reader.children_of(tree["parent"])

    assert isinstance(children, list)
    assert all(isinstance(issue, Issue) for issue in children)
    assert {issue.id for issue in children} == {tree["child"], tree["other_child"]}
    assert tree["grandchild"] not in {issue.id for issue in children}


@pytest.mark.contract_if("IF-05")
def test_contract_board_open_question_of_returns_the_unanswered_question(
    board_reader: BoardReader, board_writer: BoardWriter, tree: dict[str, str]
) -> None:
    """IF-05: `open_question_of` が未回答の `kind:question` 子を 1 件返し、閉じると `None`。"""
    question = board_reader.open_question_of(tree["child"])

    assert isinstance(question, Issue)
    assert question.id == tree["question"]

    board_writer.close(tree["question"], "回答した", BOARD_ACTOR)
    assert board_reader.open_question_of(tree["child"]) is None


@pytest.mark.contract_if("IF-06")
def test_contract_board_list_working_returns_claimed_leftovers(
    board_reader: BoardReader, board_writer: BoardWriter, tree: dict[str, str]
) -> None:
    """IF-06: `claim_next` で確保した課題が `state=working` の残骸として返る。"""
    claim_filter = ClaimFilter(
        labels=frozenset({Labels.role_executor()}), project_key=BOARD_PROJECT_KEY
    )
    assert board_reader.list_working(claim_filter) == []

    board_writer.close(tree["question"], "回答した", BOARD_ACTOR)
    claimed = board_writer.claim_next(claim_filter, BOARD_ACTOR)
    assert claimed is not None

    working = board_reader.list_working(claim_filter)
    assert [issue.id for issue in working] == [tree["child"]]
    assert Labels.state(StateLabel.working) in working[0].labels


@pytest.mark.contract_if("IF-06")
def test_contract_board_list_working_applies_the_claim_filter_labels(
    board_reader: BoardReader, board_writer: BoardWriter, tree: dict[str, str]
) -> None:
    """IF-06: 入力欄の**起動条件のラベルフィルタ**で実際に絞り込む（FR-002a・FR-016）。

    台帳 IF-06 の入力欄は `起動条件のラベルフィルタ（ClaimFilter）` だが、上のテストは盤面に
    `state:working` の課題が **1 件しか無い**状態で全件と突き合わせるため、**フィルタを無視して
    `state:working` を全件返す実装でも緑**になる。現物は `-l state:working` に `ClaimFilter`
    由来の `-l` を重ねて絞る。

    そこで `role:executor` を**持たない**もう 1 件の子（`tree["other_child"]`）にも
    `state:working` を付け、**盤面に残骸が 2 件ある**状態を作る。フィルタに合う 1 件だけが
    返ることで、`-l` を重ねていることと `state:working` だけで列挙していることを区別できる。
    """
    claim_filter = ClaimFilter(
        labels=frozenset({Labels.role_executor()}), project_key=BOARD_PROJECT_KEY
    )
    working_label = Labels.state(StateLabel.working)

    board_writer.close(tree["question"], "回答した", BOARD_ACTOR)
    assert board_writer.claim_next(claim_filter, BOARD_ACTOR) is not None
    board_writer.set_labels(tree["other_child"], [working_label], [], BOARD_ACTOR)

    assert working_label in board_reader.show(tree["other_child"]).labels, (
        "前提が崩れている（残骸が 2 件ある状態を作れていない）"
    )

    working = board_reader.list_working(claim_filter)

    assert [issue.id for issue in working] == [tree["child"]], (
        "ラベルフィルタを無視して state:working を全件返している"
    )


@pytest.mark.contract_if("IF-07")
def test_contract_board_comments_returns_dm16_for_orchestrator(
    board_reader: BoardReader, board_writer: BoardWriter, tree: dict[str, str]
) -> None:
    """IF-07（orchestrator 観点）: DM-16 —— `actor` と本文が時刻順に読める。

    orchestrator は「人間指示／回答／封筒」の**種別を本文と actor から自分で判断する**
    （`Comment` は種別を持たない＝ P5D-005）。その材料が揃うことがこの観点である。
    """
    human_actor = "human_owner"
    board_writer.comment(tree["child"], "人間からの指示です", human_actor)
    board_writer.comment(tree["child"], "- [ ] AI からの回答です", BOARD_ACTOR)

    comments = board_reader.comments(tree["child"])

    assert isinstance(comments, list)
    assert all(isinstance(item, Comment) for item in comments)
    assert [item.body for item in comments] == ["人間からの指示です", "- [ ] AI からの回答です"]
    assert [item.actor for item in comments] == [human_actor, BOARD_ACTOR]
    assert {item.issue_id for item in comments} == {tree["child"]}


@pytest.mark.contract_if("IF-08")
def test_contract_board_comments_confirms_the_envelope_for_monitor(
    board_reader: BoardReader, board_writer: BoardWriter, tree: dict[str, str]
) -> None:
    """IF-08（monitor 観点）: 封筒コメントの**反映確認**として読み戻せる。

    monitor は `finish` の中で封筒を書いたあとに `comments` を読み、**自分が書いた封筒が
    そこに在ること**で写像が効いたと判定する（読めなければ `MappingUnverified`）。
    """
    envelope = "等級: F\n種類: limit_requests\n実測値: 12"
    assert board_reader.comments(tree["child"]) == []

    board_writer.comment(tree["child"], envelope, BOARD_ACTOR)

    bodies = [item.body for item in board_reader.comments(tree["child"])]
    assert envelope in bodies


@pytest.mark.contract_if("IF-09")
def test_contract_board_has_open_issues_guards_project_deletion(
    board_reader: BoardReader, tree: dict[str, str]
) -> None:
    """IF-09: `project:<key>` の open な課題の有無を `bool` で返す（削除ガード）。"""
    assert tree
    result = board_reader.has_open_issues(BOARD_PROJECT_KEY)

    assert isinstance(result, bool)
    assert result is True
    assert board_reader.has_open_issues("no-such-project") is False
