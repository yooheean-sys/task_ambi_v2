"""書込 8 メソッド（US2・board-F08〜F15）。argv の期待値は plan (ii) の表が正本。

**起票だけが読み戻す**（他は確認読取 0 回＝ SC-003・P5D-007）。
"""

from __future__ import annotations

import pytest
from board_testing import StubRunner, bd_ok, issue_payload, issue_ref_payload

from board.exceptions import BdCommandError, BdErrorKind
from board.service import BoardService
from common.interfaces.board import (
    BoardUnavailable,
    BoardWriteUnverified,
    ClaimFilter,
    IssueNotFound,
    IssueSpec,
)

ACTOR = "demo_pj.programmer"


def _failure(kind: BdErrorKind) -> BdCommandError:
    return BdCommandError("bd が失敗した", kind=kind, argv=("bd",), rc=1, stderr="boom")


def _spec(**overrides: object) -> IssueSpec:
    values: dict[str, object] = {
        "title": "子課題の表題",
        "description": "説明",
        "labels": ["kind:child", "project:pj"],
        "parent": "pj-1",
        "acceptance": "- [ ] done",
    }
    values.update(overrides)
    return IssueSpec(**values)  # pyright: ignore[reportArgumentType]  # テストの可変引数を pydantic に渡す


class TestClaimNext:
    """board-F08: `ready --claim` ＋ `update --add-label state:working` の 2 手。"""

    def test_two_calls_and_argv(self) -> None:
        runner = StubRunner(bd_ok([issue_payload(id="pj-2")]), bd_ok([issue_payload(id="pj-2")]))
        claim_filter = ClaimFilter(labels=frozenset({"role:executor"}), project_key="pj")
        issue = BoardService(runner).claim_next(claim_filter, ACTOR)
        assert runner.argvs == [
            (
                "ready",
                "--claim",
                "--limit",
                "1",
                "-l",
                "role:executor",
                "-l",
                "project:pj",
                "--json",
            ),
            ("update", "pj-2", "--add-label", "state:working", "--json"),
        ]
        assert issue is not None
        assert issue.id == "pj-2"

    def test_both_calls_are_writes_carrying_the_actor(self) -> None:
        runner = StubRunner(bd_ok([issue_payload(id="pj-2")]), bd_ok([issue_payload(id="pj-2")]))
        BoardService(runner).claim_next(ClaimFilter(project_key=None), ACTOR)
        assert [c.write for c in runner.commands] == [True, True]
        assert [c.actor for c in runner.commands] == [ACTOR, ACTOR]

    def test_actor_never_appears_in_argv(self) -> None:
        """`actor` は env `BEADS_ACTOR` に載る（FR-012・D19-c）。"""
        runner = StubRunner(bd_ok([issue_payload(id="pj-2")]), bd_ok([issue_payload(id="pj-2")]))
        BoardService(runner).claim_next(ClaimFilter(project_key=None), ACTOR)
        assert all(ACTOR not in argv for argv in runner.argvs)

    def test_nothing_ready_returns_none_with_one_call(self) -> None:
        runner = StubRunner(bd_ok([]))
        assert BoardService(runner).claim_next(ClaimFilter(project_key=None), ACTOR) is None
        assert runner.calls == 1

    def test_no_re_read_after_claiming(self) -> None:
        """M-23: ①の応答は `Issue` を組むのに十分。`show` を打たない。"""
        runner = StubRunner(bd_ok([issue_payload(id="pj-2")]), bd_ok([issue_payload(id="pj-2")]))
        BoardService(runner).claim_next(ClaimFilter(project_key=None), ACTOR)
        assert all(argv[0] != "show" for argv in runner.argvs)

    def test_label_step_failure_does_not_release(self) -> None:
        """P5D-007: 確保を保ったまま `BoardUnavailable`（解放しない）。"""
        runner = StubRunner(bd_ok([issue_payload(id="pj-2")]), _failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).claim_next(ClaimFilter(project_key=None), ACTOR)
        assert runner.calls == 2

    def test_fr026_no_post_filter_and_no_release_flags(self) -> None:
        """FR-026 は**否定の要求**として生きている（DIFF-07・quickstart g3）。

        bd の版が上がって `-l` の AND が壊れたとき、「消えたはずの分岐の復活」を気付かせる番人。
        """
        runner = StubRunner(bd_ok([issue_payload(id="pj-2")]), bd_ok([issue_payload(id="pj-2")]))
        BoardService(runner).claim_next(ClaimFilter(project_key=None), ACTOR)
        flat = [token for argv in runner.argvs for token in argv]
        assert "--status" not in flat
        assert "--assignee" not in flat
        assert "--remove-label" not in flat


class TestCreate:
    """board-F09: 起票 ＋ 読み戻し照合（唯一の書込検証。P5D-007）。"""

    def test_argv_uses_title_flag_and_no_inherit_labels(self) -> None:
        created = issue_payload(id="pj-1.1", parent="pj-1", labels=["kind:child", "project:pj"])
        runner = StubRunner(bd_ok(created), bd_ok([created]))
        new_id = BoardService(runner).create(_spec(), ACTOR)
        assert runner.argvs == [
            (
                "create",
                "--title",
                "子課題の表題",
                "-d",
                "説明",
                "-p",
                "2",
                "-t",
                "task",
                "--parent",
                "pj-1",
                "--no-inherit-labels",
                "-l",
                "kind:child",
                "-l",
                "project:pj",
                "--acceptance",
                "- [ ] done",
                "--json",
            ),
            ("show", "pj-1.1", "--json"),
        ]
        assert new_id == "pj-1.1"

    def test_parent_flag_is_omitted_when_absent(self) -> None:
        created = issue_payload(id="pj-9", parent=None, labels=["kind:program"])
        runner = StubRunner(bd_ok(created), bd_ok([created]))
        BoardService(runner).create(_spec(parent=None, labels=["kind:program"]), ACTOR)
        assert "--parent" not in runner.argvs[0]

    def test_create_response_is_a_bare_object(self) -> None:
        """`bd create` の応答は単一オブジェクト（M-15）。"""
        created = issue_payload(id="pj-1.1", parent="pj-1", labels=[])
        runner = StubRunner(bd_ok(created), bd_ok([created]))
        assert BoardService(runner).create(_spec(labels=[]), ACTOR) == "pj-1.1"

    def test_readback_is_a_read_not_a_write(self) -> None:
        created = issue_payload(id="pj-1.1", parent="pj-1", labels=[])
        runner = StubRunner(bd_ok(created), bd_ok([created]))
        BoardService(runner).create(_spec(labels=[]), ACTOR)
        assert [c.write for c in runner.commands] == [True, False]

    def test_label_set_is_compared_regardless_of_order(self) -> None:
        created = issue_payload(id="pj-1.1", parent="pj-1", labels=["project:pj", "kind:child"])
        runner = StubRunner(bd_ok(created), bd_ok([created]))
        assert BoardService(runner).create(_spec(), ACTOR) == "pj-1.1"

    def test_label_mismatch_is_unverified(self) -> None:
        created = issue_payload(id="pj-1.1", parent="pj-1")
        readback = issue_payload(id="pj-1.1", parent="pj-1", labels=["kind:child", "親から継承"])
        runner = StubRunner(bd_ok(created), bd_ok([readback]))
        with pytest.raises(BoardWriteUnverified) as exc:
            BoardService(runner).create(_spec(), ACTOR)
        assert exc.value.issue_id == "pj-1.1"
        assert "labels" in exc.value.mismatch

    def test_parent_mismatch_is_unverified(self) -> None:
        created = issue_payload(id="pj-1.1", parent="pj-1")
        readback = issue_payload(id="pj-1.1", parent="pj-2", labels=["kind:child", "project:pj"])
        runner = StubRunner(bd_ok(created), bd_ok([readback]))
        with pytest.raises(BoardWriteUnverified) as exc:
            BoardService(runner).create(_spec(), ACTOR)
        assert "parent" in exc.value.mismatch

    def test_title_and_acceptance_are_not_compared(self) -> None:
        created = issue_payload(id="pj-1.1", parent="pj-1")
        readback = issue_payload(
            id="pj-1.1",
            parent="pj-1",
            labels=["kind:child", "project:pj"],
            title="bd が整形した別の表題",
            acceptance_criteria="別の受け入れ条件",
        )
        runner = StubRunner(bd_ok(created), bd_ok([readback]))
        assert BoardService(runner).create(_spec(), ACTOR) == "pj-1.1"

    def test_readback_not_found_is_unverified_not_issue_not_found(self) -> None:
        """`create` が id を返した以上その実在は board が保証する（P5D-007）。"""
        created = issue_payload(id="pj-1.1", parent="pj-1")
        runner = StubRunner(bd_ok(created), _failure(BdErrorKind.not_found))
        with pytest.raises(BoardWriteUnverified) as exc:
            BoardService(runner).create(_spec(), ACTOR)
        assert exc.value.issue_id == "pj-1.1"
        assert "readback" in exc.value.mismatch

    def test_create_step_failure_is_board_unavailable(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).create(_spec(), ACTOR)


class TestAddDependency:
    """board-F10: `["dep", "add", <from>, "--blocked-by", <to>, "--json"]`・bd 1 回・冪等。"""

    def test_argv(self) -> None:
        runner = StubRunner(bd_ok({}))
        BoardService(runner).add_dependency("pj-2", "pj-1", ACTOR)
        assert runner.argvs == [("dep", "add", "pj-2", "--blocked-by", "pj-1", "--json")]
        assert runner.commands[0].write is True

    def test_no_pre_check(self) -> None:
        """M-20: 既存の辺への再実行も rc=0。board 側で事前確認しない。"""
        runner = StubRunner(bd_ok({}))
        BoardService(runner).add_dependency("pj-2", "pj-1", ACTOR)
        assert runner.calls == 1

    def test_not_found_becomes_issue_not_found(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).add_dependency("pj-2", "pj-1", ACTOR)
        assert exc.value.issue_id == "pj-2"


class TestMoveDependents:
    """board-F11: `dep list --direction=up -t blocks` ＋ 各 dependent への `dep add`。"""

    def test_argv_and_call_count(self) -> None:
        runner = StubRunner(
            bd_ok([issue_ref_payload(id="pj-3"), issue_ref_payload(id="pj-4")]),
            bd_ok({}),
            bd_ok({}),
        )
        BoardService(runner).move_dependents("pj-2", "pj-9", ACTOR)
        assert runner.argvs == [
            ("dep", "list", "pj-2", "--direction=up", "-t", "blocks", "--json"),
            ("dep", "add", "pj-3", "--blocked-by", "pj-9", "--json"),
            ("dep", "add", "pj-4", "--blocked-by", "pj-9", "--json"),
        ]

    def test_listing_is_a_read_and_edges_are_writes(self) -> None:
        runner = StubRunner(bd_ok([issue_ref_payload(id="pj-3")]), bd_ok({}))
        BoardService(runner).move_dependents("pj-2", "pj-9", ACTOR)
        assert [c.write for c in runner.commands] == [False, True]

    def test_self_loop_is_excluded(self) -> None:
        """R-20: `dep_id == new_id` は除いてから張る（設計 board-F11 に無い追加）。"""
        runner = StubRunner(
            bd_ok([issue_ref_payload(id="pj-9"), issue_ref_payload(id="pj-3")]), bd_ok({})
        )
        BoardService(runner).move_dependents("pj-2", "pj-9", ACTOR)
        assert runner.argvs[1:] == [("dep", "add", "pj-3", "--blocked-by", "pj-9", "--json")]

    def test_no_dependents_means_one_call(self) -> None:
        runner = StubRunner(bd_ok([]))
        BoardService(runner).move_dependents("pj-2", "pj-9", ACTOR)
        assert runner.calls == 1

    def test_listing_failure_adds_no_edge(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).move_dependents("pj-2", "pj-9", ACTOR)
        assert runner.calls == 1

    def test_partial_failure_keeps_the_edges_already_added(self) -> None:
        """FR-035: board は巻き戻さない。"""
        runner = StubRunner(
            bd_ok([issue_ref_payload(id="pj-3"), issue_ref_payload(id="pj-4")]),
            bd_ok({}),
            _failure(BdErrorKind.unavailable),
        )
        with pytest.raises(BoardUnavailable):
            BoardService(runner).move_dependents("pj-2", "pj-9", ACTOR)
        assert runner.calls == 3


class TestRelease:
    """board-F12: 2 手のまま畳まない（R-22）。"""

    def test_two_calls_and_argv(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]), bd_ok([issue_payload()]))
        BoardService(runner).release("pj-2", ACTOR)
        assert runner.argvs == [
            ("update", "pj-2", "--status", "open", "--assignee", "", "--json"),
            ("update", "pj-2", "--remove-label", "state:working", "--json"),
        ]
        assert [c.write for c in runner.commands] == [True, True]

    def test_first_step_failure_stops(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).release("pj-2", ACTOR)
        assert runner.calls == 1

    def test_second_step_failure_is_reported(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]), _failure(BdErrorKind.unavailable))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).release("pj-2", ACTOR)
        assert runner.calls == 2

    def test_not_found_becomes_issue_not_found(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).release("pj-2", ACTOR)
        assert exc.value.issue_id == "pj-2"


class TestComment:
    """board-F13: `["comment", <id>, "--json", "--", <body>]`（M-28）。"""

    def test_argv_puts_json_before_the_separator(self) -> None:
        runner = StubRunner(bd_ok({}))
        BoardService(runner).comment("pj-1", "- [ ] 箇条書き", ACTOR)
        assert runner.argvs == [("comment", "pj-1", "--json", "--", "- [ ] 箇条書き")]

    def test_body_starting_with_a_dash_is_the_normal_path(self) -> None:
        runner = StubRunner(bd_ok({}))
        BoardService(runner).comment("pj-1", "-r 停止理由", ACTOR)
        assert runner.argvs[0][-1] == "-r 停止理由"

    def test_no_verification_read(self) -> None:
        """反映確認は呼び手が `comments` で行う（D04-b・SC-003）。"""
        runner = StubRunner(bd_ok({}))
        BoardService(runner).comment("pj-1", "本文", ACTOR)
        assert runner.calls == 1

    def test_not_found_becomes_issue_not_found(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).comment("pj-9", "本文", ACTOR)
        assert exc.value.issue_id == "pj-9"


class TestClose:
    """board-F14: 事前 `show` で closed なら書込 0 回（M-24・SC-004）。"""

    def test_open_issue_is_closed_with_two_calls(self) -> None:
        runner = StubRunner(bd_ok([issue_payload(status="open")]), bd_ok([issue_payload()]))
        BoardService(runner).close("pj-1", "完了", ACTOR)
        assert runner.argvs == [
            ("show", "pj-1", "--json"),
            ("close", "pj-1", "-r", "完了", "--json"),
        ]
        assert [c.write for c in runner.commands] == [False, True]

    def test_already_closed_issues_no_write(self) -> None:
        runner = StubRunner(bd_ok([issue_payload(status="closed")]))
        BoardService(runner).close("pj-1", "完了", ACTOR)
        assert runner.calls == 1
        assert all(c.write is False for c in runner.commands)

    def test_missing_issue_is_not_treated_as_success(self) -> None:
        """「既に無い」を成功に倒さない（P3）。"""
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).close("pj-9", "完了", ACTOR)
        assert exc.value.issue_id == "pj-9"

    def test_close_step_failure_is_board_unavailable(self) -> None:
        runner = StubRunner(bd_ok([issue_payload(status="open")]), _failure(BdErrorKind.other))
        with pytest.raises(BoardUnavailable):
            BoardService(runner).close("pj-1", "完了", ACTOR)


class TestSetLabels:
    """board-F15: 1 回の `bd update` に畳む（FR-033・DIFF-10）。"""

    def test_single_call_with_removes_before_adds(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=["note:b"], remove=["note:a"], actor=ACTOR)
        assert runner.argvs == [
            ("update", "pj-1", "--remove-label", "note:a", "--add-label", "note:b", "--json")
        ]
        assert runner.commands[0].write is True

    def test_multiple_labels_are_repeated_flags_in_sorted_order(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels(
            "pj-1", add=["note:z", "note:a"], remove=["note:y", "note:b"], actor=ACTOR
        )
        assert runner.argvs == [
            (
                "update",
                "pj-1",
                "--remove-label",
                "note:b",
                "--remove-label",
                "note:y",
                "--add-label",
                "note:a",
                "--add-label",
                "note:z",
                "--json",
            )
        ]

    def test_nothing_to_do_never_touches_bd(self) -> None:
        runner = StubRunner()
        BoardService(runner).set_labels("pj-1", add=[], remove=[], actor=ACTOR)
        assert runner.calls == 0

    def test_not_found_becomes_issue_not_found(self) -> None:
        runner = StubRunner(_failure(BdErrorKind.not_found))
        with pytest.raises(IssueNotFound) as exc:
            BoardService(runner).set_labels("pj-9", add=["note:a"], remove=[], actor=ACTOR)
        assert exc.value.issue_id == "pj-9"
