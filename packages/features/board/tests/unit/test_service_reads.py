"""読取 6 メソッド（US1・board-F02〜F07）。argv の期待値は plan (ii) の表が正本。"""

from __future__ import annotations

import pytest
from board_testing import StubRunner, bd_ok, comment_payload, issue_payload, issue_ref_payload

from board.exceptions import BdCommandError, BdErrorKind
from board.service import BoardService
from common.interfaces.board import (
    BoardUnavailable,
    ClaimFilter,
    IssueNotFound,
    IssueStatus,
)


def _failure(kind: BdErrorKind) -> BdCommandError:
    return BdCommandError("bd が失敗した", kind=kind, argv=("bd",), rc=1, stderr="boom")


class TestShow:
    """board-F02: `["show", <id>, "--json"]`・bd 1 回。"""

    def test_argv_and_single_call(self) -> None:
        runner = StubRunner(bd_ok([issue_payload(id="pj-1")]))
        issue = BoardService(runner).show("pj-1")
        assert runner.argvs == [("show", "pj-1", "--json")]
        assert runner.calls == 1
        assert issue.id == "pj-1"

    def test_is_a_read(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).show("pj-1")
        assert runner.commands[0].write is False
        assert runner.commands[0].actor is None
        assert runner.commands[0].issue_id == "pj-1"

    def test_array_response_takes_the_head(self) -> None:
        """`bd show` の応答は配列（M-15）。"""
        runner = StubRunner(bd_ok([issue_payload(id="pj-1"), issue_payload(id="pj-2")]))
        assert BoardService(runner).show("pj-1").id == "pj-1"

    def test_not_found_becomes_issue_not_found(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).show("pj-9")
        assert exc.value.issue_id == "pj-9"

    @pytest.mark.parametrize(
        "kind", [BdErrorKind.unavailable, BdErrorKind.lock_exhausted, BdErrorKind.other]
    )
    def test_other_failures_become_board_unavailable(self, kind: BdErrorKind) -> None:
        runner = StubRunner(_failure(kind))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).show("pj-1")

    def test_mapping_failure_becomes_board_unavailable(self) -> None:
        """空や部分 JSON を黙って埋めない（P3・FR-024）。"""
        runner = StubRunner(bd_ok([{"id": "pj-1"}]))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).show("pj-1")

    def test_empty_array_is_not_a_success(self) -> None:
        runner = StubRunner(bd_ok([]))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).show("pj-1")


class TestChildrenOf:
    """board-F03: `["list", "--parent", <id>, "--all", "-n", "0", "--json"]`・bd 1 回。"""

    def test_argv(self) -> None:
        runner = StubRunner(bd_ok([]))
        BoardService(runner).children_of("pj-1")
        assert runner.argvs == [("list", "--parent", "pj-1", "--all", "-n", "0", "--json")]

    def test_returns_direct_children_including_closed(self) -> None:
        """直下の子だけが平坦な配列で返る（M-31。board 側で深さを絞らない）。"""
        runner = StubRunner(
            bd_ok(
                [
                    issue_payload(id="pj-1.1", parent="pj-1"),
                    issue_payload(id="pj-1.2", parent="pj-1", status="closed"),
                ]
            )
        )
        children = BoardService(runner).children_of("pj-1")
        assert [(c.id, c.status) for c in children] == [
            ("pj-1.1", IssueStatus.open),
            ("pj-1.2", IssueStatus.closed),
        ]

    def test_no_children_is_an_empty_list_not_an_exception(self) -> None:
        runner = StubRunner(bd_ok([]))
        assert BoardService(runner).children_of("pj-1") == []

    def test_failure_becomes_board_unavailable(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).children_of("pj-1")

    def test_limit_flag_is_dropped_when_list_limit_none_is_false(self) -> None:
        """CFG-26 を `false` にすると既定 50 件で黙って切れる（M-13。SKILL.md の危険）。"""
        runner = StubRunner(bd_ok([]))
        BoardService(runner, list_limit_none=False).children_of("pj-1")
        assert runner.argvs == [("list", "--parent", "pj-1", "--all", "--json")]


class TestOpenQuestionOf:
    """board-F04: 2 回目の `show` を打つ（R-19・DIFF-05）。"""

    def test_two_calls_when_an_open_question_exists(self) -> None:
        parent = issue_payload(
            id="pj-1",
            dependencies=[
                issue_ref_payload(id="pj-2", labels=["kind:child"]),
                issue_ref_payload(id="pj-3", labels=["kind:question"], status="closed"),
                issue_ref_payload(id="pj-4", labels=["kind:question"], status="open"),
            ],
        )
        runner = StubRunner(bd_ok([parent]), bd_ok([issue_payload(id="pj-4")]))
        question = BoardService(runner).open_question_of("pj-1")
        assert runner.argvs == [("show", "pj-1", "--json"), ("show", "pj-4", "--json")]
        assert question is not None
        assert question.id == "pj-4"

    def test_picks_the_first_match_in_bd_order(self) -> None:
        """board は並べ替えない（bd が返した順の最初の 1 件）。"""
        parent = issue_payload(
            dependencies=[
                issue_ref_payload(id="pj-8", labels=["kind:question"]),
                issue_ref_payload(id="pj-5", labels=["kind:question"]),
            ]
        )
        runner = StubRunner(bd_ok([parent]), bd_ok([issue_payload(id="pj-8")]))
        BoardService(runner).open_question_of("pj-1")
        assert runner.argvs[1] == ("show", "pj-8", "--json")

    def test_dependency_type_is_not_used_to_filter(self) -> None:
        """R-19: 質問子が `parent-child` で張られていても拾う。"""
        parent = issue_payload(
            dependencies=[
                issue_ref_payload(
                    id="pj-6", labels=["kind:question"], dependency_type="parent-child"
                )
            ]
        )
        runner = StubRunner(bd_ok([parent]), bd_ok([issue_payload(id="pj-6")]))
        result = BoardService(runner).open_question_of("pj-1")
        assert result is not None
        assert result.id == "pj-6"

    def test_no_open_question_returns_none_with_a_single_call(self) -> None:
        parent = issue_payload(
            dependencies=[issue_ref_payload(id="pj-3", labels=["kind:question"], status="closed")]
        )
        runner = StubRunner(bd_ok([parent]))
        assert BoardService(runner).open_question_of("pj-1") is None
        assert runner.calls == 1

    def test_second_show_not_found_raises_instead_of_returning_none(self) -> None:
        """P3: 読めないときに「質問なし」に倒さない（→ plan C-3）。"""
        parent = issue_payload(
            dependencies=[issue_ref_payload(id="pj-4", labels=["kind:question"])]
        )
        runner = StubRunner(bd_ok([parent]), _failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).open_question_of("pj-1")
        assert exc.value.issue_id == "pj-4"

    def test_first_show_not_found_raises(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).open_question_of("pj-1")
        assert exc.value.issue_id == "pj-1"


class TestListWorking:
    """board-F05: ラベルは `sorted(claim_filter.labels)` ＋ `project:<key>`（R-04・R-18）。"""

    def test_argv_with_sorted_labels_and_project(self) -> None:
        runner = StubRunner(bd_ok([]))
        claim_filter = ClaimFilter(
            labels=frozenset({"role:executor", "kind:child"}), project_key="pj"
        )
        BoardService(runner).list_working(claim_filter)
        assert runner.argvs == [
            (
                "list",
                "-l",
                "state:working",
                "-l",
                "kind:child",
                "-l",
                "role:executor",
                "-l",
                "project:pj",
                "--all",
                "-n",
                "0",
                "--json",
            )
        ]

    def test_argv_without_project_key(self) -> None:
        runner = StubRunner(bd_ok([]))
        BoardService(runner).list_working(ClaimFilter(project_key=None))
        assert runner.argvs == [
            ("list", "-l", "state:working", "-l", "role:executor", "--all", "-n", "0", "--json")
        ]

    def test_closed_is_removed_after_mapping(self) -> None:
        """R-18: `--all` で取ってから board 側で closed を除く。"""
        runner = StubRunner(
            bd_ok(
                [
                    issue_payload(id="pj-1"),
                    issue_payload(id="pj-2", status="closed"),
                    issue_payload(id="pj-3", status="in_progress"),
                ]
            )
        )
        working = BoardService(runner).list_working(ClaimFilter(project_key=None))
        assert [issue.id for issue in working] == ["pj-1", "pj-3"]

    def test_failure_becomes_board_unavailable(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).list_working(ClaimFilter(project_key=None))

    def test_limit_flag_is_dropped_when_list_limit_none_is_false(self) -> None:
        runner = StubRunner(bd_ok([]))
        BoardService(runner, list_limit_none=False).list_working(ClaimFilter(project_key=None))
        assert runner.argvs == [
            ("list", "-l", "state:working", "-l", "role:executor", "--all", "--json")
        ]


class TestComments:
    """board-F06: `["comments", <id>, "--json"]`。並べ替えない（M-21）・種別なし（P5D-005）。"""

    def test_argv_and_single_call(self) -> None:
        runner = StubRunner(bd_ok([comment_payload()]))
        BoardService(runner).comments("pj-1")
        assert runner.argvs == [("comments", "pj-1", "--json")]

    def test_order_is_preserved(self) -> None:
        runner = StubRunner(
            bd_ok(
                [
                    comment_payload(id="c2", created_at="2026-08-21T02:00:00Z"),
                    comment_payload(id="c1", created_at="2026-08-21T01:00:00Z"),
                ]
            )
        )
        assert [c.id for c in BoardService(runner).comments("pj-1")] == ["c2", "c1"]

    def test_no_kind_is_attached(self) -> None:
        comment = BoardService(StubRunner(bd_ok([comment_payload()]))).comments("pj-1")[0]
        assert not hasattr(comment, "kind")

    def test_not_found_becomes_issue_not_found(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).comments("pj-9")
        assert exc.value.issue_id == "pj-9"

    def test_no_comments_is_an_empty_list(self) -> None:
        assert BoardService(StubRunner(bd_ok([]))).comments("pj-1") == []


class TestHasOpenIssues:
    """board-F07: `["list", "-l", "project:<key>", "--json"]`。`--all` も `-n 0` も無し（R-17）。"""

    def test_argv_has_no_all_and_no_limit(self) -> None:
        runner = StubRunner(bd_ok([]))
        BoardService(runner).has_open_issues("pj")
        assert runner.argvs == [("list", "-l", "project:pj", "--json")]

    def test_limit_flag_is_never_added_even_when_configured(self) -> None:
        """R-17: CFG-26 は `children_of`／`list_working` の 2 経路にだけ効かせる。"""
        runner = StubRunner(bd_ok([]))
        BoardService(runner, list_limit_none=True).has_open_issues("pj")
        assert "-n" not in runner.argvs[0]

    def test_true_when_at_least_one(self) -> None:
        assert BoardService(StubRunner(bd_ok([issue_payload()]))).has_open_issues("pj") is True

    def test_false_when_none(self) -> None:
        assert BoardService(StubRunner(bd_ok([]))).has_open_issues("pj") is False

    def test_failure_does_not_fall_back_to_false(self) -> None:
        """P3・FR-024: 読めないときに `False` に倒さない（呼び手は削除を拒否側に倒す）。"""
        runner = StubRunner(_failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).has_open_issues("pj")
