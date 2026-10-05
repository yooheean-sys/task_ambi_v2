"""冪等性の回帰テスト（SC-004）。同じ書込を 2 回出しても盤面が壊れない。"""

from __future__ import annotations

from board_testing import StubRunner, bd_ok, issue_payload

from board.service import BoardService

ACTOR = "demo_pj.programmer"


def test_re_adding_an_existing_dependency_succeeds() -> None:
    """M-20: 既存の辺への `dep add` は rc=0。board 側で事前確認しない。"""
    runner = StubRunner(bd_ok({}), bd_ok({}))
    service = BoardService(runner)
    service.add_dependency("pj-2", "pj-1", ACTOR)
    service.add_dependency("pj-2", "pj-1", ACTOR)
    assert runner.argvs == [("dep", "add", "pj-2", "--blocked-by", "pj-1", "--json")] * 2


def test_closing_an_already_closed_issue_writes_nothing() -> None:
    """M-24: bd 任せでは再書込される。board が事前 `show` で止める（SC-004）。"""
    runner = StubRunner(bd_ok([issue_payload(status="closed")]))
    BoardService(runner).close("pj-1", "完了", ACTOR)
    assert [c.write for c in runner.commands] == [False]


def test_closing_twice_writes_only_once() -> None:
    runner = StubRunner(
        bd_ok([issue_payload(status="open")]),
        bd_ok([issue_payload(status="closed")]),
        bd_ok([issue_payload(status="closed")]),
    )
    service = BoardService(runner)
    service.close("pj-1", "完了", ACTOR)
    service.close("pj-1", "完了", ACTOR)
    assert [argv[0] for argv in runner.argvs] == ["show", "close", "show"]


def test_removing_a_label_that_is_not_attached_succeeds() -> None:
    runner = StubRunner(bd_ok([issue_payload()]))
    BoardService(runner).set_labels("pj-1", add=[], remove=["note:never-attached"], actor=ACTOR)
    assert runner.argvs == [("update", "pj-1", "--remove-label", "note:never-attached", "--json")]


def test_adding_a_label_that_is_already_attached_succeeds() -> None:
    runner = StubRunner(bd_ok([issue_payload()]))
    BoardService(runner).set_labels("pj-1", add=["kind:child"], remove=[], actor=ACTOR)
    assert runner.argvs == [("update", "pj-1", "--add-label", "kind:child", "--json")]
