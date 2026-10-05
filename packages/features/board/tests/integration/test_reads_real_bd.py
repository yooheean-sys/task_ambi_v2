"""読取 6 メソッドを**実 `bd`** に対して検証する（US1・SC-002）。

一時ワークスペースに課題を仕込み、6 読取が仕込んだ状態を返すことを確かめる。
`bd init` は `tmp_path` 配下だけで行う（conftest の止め木を通る＝ D19-d・FR-061）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from common.interfaces.board import ClaimFilter, IssueSpec, IssueStatus

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter

pytestmark = pytest.mark.requires_bd

ACTOR = "demo_pj.programmer"
PROJECT = "boardtest"


def _spec(title: str, labels: list[str], parent: str | None = None) -> IssueSpec:
    return IssueSpec(
        title=title,
        description="説明",
        labels=labels,
        parent=parent,
        acceptance="- [ ] 受け入れ条件",
    )


@pytest.fixture
def tree(writer: BoardWriter) -> dict[str, str]:
    """親 1・子 2（うち 1 件は closed）・質問 1 の小さな盤面を仕込む。"""
    parent = writer.create(_spec("親", ["kind:program", f"project:{PROJECT}"]), ACTOR)
    child_open = writer.create(
        _spec("子（未完）", ["kind:child", f"project:{PROJECT}", "role:executor"], parent), ACTOR
    )
    child_closed = writer.create(
        _spec("子（完了）", ["kind:child", f"project:{PROJECT}"], parent), ACTOR
    )
    grandchild = writer.create(_spec("孫", ["kind:child", f"project:{PROJECT}"], child_open), ACTOR)
    # 質問は**ブロックする側**として張る（PoC loop.py:960-969 と同じ形。`--parent` では
    # 依存が子側に付くので、親の `dependencies` には現れない＝ bd 1.1.2 実測）
    question = writer.create(_spec("質問", ["kind:question", f"project:{PROJECT}"]), ACTOR)
    writer.add_dependency(child_open, question, ACTOR)
    writer.close(child_closed, "完了", ACTOR)
    return {
        "parent": parent,
        "child_open": child_open,
        "child_closed": child_closed,
        "grandchild": grandchild,
        "question": question,
    }


class TestShow:
    def test_reads_back_what_was_written(self, reader: BoardReader, tree: dict[str, str]) -> None:
        issue = reader.show(tree["child_open"])
        assert issue.id == tree["child_open"]
        assert issue.title == "子（未完）"
        assert issue.parent == tree["parent"]
        assert set(issue.labels) == {"kind:child", f"project:{PROJECT}", "role:executor"}
        assert issue.acceptance == "- [ ] 受け入れ条件"
        assert issue.status is IssueStatus.open

    def test_missing_issue_raises(self, reader: BoardReader, tree: dict[str, str]) -> None:
        from common.interfaces.board import IssueNotFound

        assert tree
        with pytest.raises(IssueNotFound):
            reader.show("boardtest-zzzzzz")


class TestChildrenOf:
    def test_returns_direct_children_only_including_closed(
        self, reader: BoardReader, tree: dict[str, str]
    ) -> None:
        """孫を含まない（M-31）。closed も返す（FR-019）。"""
        children = {issue.id for issue in reader.children_of(tree["parent"])}
        assert children == {tree["child_open"], tree["child_closed"]}
        assert tree["grandchild"] not in children

    def test_closed_child_keeps_its_status(self, reader: BoardReader, tree: dict[str, str]) -> None:
        statuses = {i.id: i.status for i in reader.children_of(tree["parent"])}
        assert statuses[tree["child_closed"]] is IssueStatus.closed

    def test_no_children_is_an_empty_list(self, reader: BoardReader, tree: dict[str, str]) -> None:
        assert reader.children_of(tree["grandchild"]) == []


class TestOpenQuestionOf:
    def test_finds_the_open_question(self, reader: BoardReader, tree: dict[str, str]) -> None:
        question = reader.open_question_of(tree["child_open"])
        assert question is not None
        assert question.id == tree["question"]
        assert question.title == "質問"

    def test_closed_question_is_not_returned(
        self, reader: BoardReader, writer: BoardWriter, tree: dict[str, str]
    ) -> None:
        writer.close(tree["question"], "回答済み", ACTOR)
        assert reader.open_question_of(tree["child_open"]) is None

    def test_issue_without_question_returns_none(
        self, reader: BoardReader, tree: dict[str, str]
    ) -> None:
        assert reader.open_question_of(tree["child_closed"]) is None


class TestListWorking:
    def test_lists_only_issues_carrying_the_state_label(
        self, reader: BoardReader, writer: BoardWriter, tree: dict[str, str]
    ) -> None:
        claim_filter = ClaimFilter(labels=frozenset({"role:executor"}), project_key=PROJECT)
        assert reader.list_working(claim_filter) == []
        writer.set_labels(tree["child_open"], add=["state:working"], remove=[], actor=ACTOR)
        assert [i.id for i in reader.list_working(claim_filter)] == [tree["child_open"]]

    def test_closed_issues_are_excluded(
        self, reader: BoardReader, writer: BoardWriter, tree: dict[str, str]
    ) -> None:
        """R-18: `--all` で取ってから board 側で closed を除く。"""
        writer.set_labels(tree["child_closed"], add=["state:working"], remove=[], actor=ACTOR)
        claim_filter = ClaimFilter(labels=frozenset(), project_key=PROJECT)
        assert [i.id for i in reader.list_working(claim_filter)] == []


class TestComments:
    def test_reads_comments_in_order_with_the_actor(
        self, reader: BoardReader, writer: BoardWriter, tree: dict[str, str]
    ) -> None:
        writer.comment(tree["child_open"], "- [ ] 1 件目（箇条書き）", ACTOR)
        writer.comment(tree["child_open"], "-r 2 件目（ハイフン始まり）", ACTOR)
        comments = reader.comments(tree["child_open"])
        assert [c.body for c in comments] == [
            "- [ ] 1 件目（箇条書き）",
            "-r 2 件目（ハイフン始まり）",
        ]
        assert {c.actor for c in comments} == {ACTOR}
        assert {c.issue_id for c in comments} == {tree["child_open"]}

    def test_no_comments_is_an_empty_list(self, reader: BoardReader, tree: dict[str, str]) -> None:
        assert reader.comments(tree["grandchild"]) == []


class TestHasOpenIssues:
    def test_true_while_something_is_open(self, reader: BoardReader, tree: dict[str, str]) -> None:
        assert tree
        assert reader.has_open_issues(PROJECT) is True

    def test_false_for_an_unknown_project(self, reader: BoardReader, tree: dict[str, str]) -> None:
        assert tree
        assert reader.has_open_issues("no-such-project") is False

    def test_in_progress_counts_as_open(
        self, reader: BoardReader, writer: BoardWriter, tree: dict[str, str]
    ) -> None:
        """M-25: bd の既定フィルタは closed を除く全状態（`in_progress` を含む）。"""
        # 未回答の質問がブロックしている間は `bd ready` に出ない（beads の公約）。
        # 質問を閉じてから確保する＝「回答したら実行対象に戻る」の実流路。
        writer.close(tree["question"], "回答した", ACTOR)
        claimed = writer.claim_next(
            ClaimFilter(labels=frozenset({"role:executor"}), project_key=PROJECT), ACTOR
        )
        assert claimed is not None
        assert reader.show(claimed.id).status is IssueStatus.in_progress
        assert reader.has_open_issues(PROJECT) is True
