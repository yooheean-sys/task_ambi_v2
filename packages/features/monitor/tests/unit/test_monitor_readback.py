"""US3: 確認読取と停止（monitor-F11・contracts/verdict-mapping.md §6）。

**外部ツールの終了コードを成功の証拠にしない**（FR-055）。見るのは読み戻した値だけである。
"""

from __future__ import annotations

import logging

import pytest
from conftest import (
    ENVELOPE_MARKER,
    StubBoardReader,
    StubBoardWriter,
    completion_payload,
    make_comment,
    make_config,
    make_hook,
    make_issue,
    make_linked_board,
    make_outcome,
    make_response,
    make_service,
    make_state,
)

from common.interfaces.board import BoardUnavailable, Comment, IssueNotFound, IssueStatus
from common.interfaces.monitor import MappingUnverified, RunVerdict
from common.kernel.enums import Labels, StateLabel

FAILING = Labels.state(StateLabel.failing)
WORKING = Labels.state(StateLabel.working)


def _envelope(kickoff_id: str = "k-1", *, comment_id: str = "c-9", actor: str = "bot") -> Comment:
    """写像が残したはずの封筒。"""
    body = f"{ENVELOPE_MARKER} grade=F kinds=no_output\nkickoff_id={kickoff_id}\ncorrelation_id=-\n"
    return make_comment(body, comment_id=comment_id, actor=actor)


def _finish_failure(reader: StubBoardReader, writer: StubBoardWriter) -> RunVerdict:
    """F 級（`no_output`）の 1 回を回す。"""
    config = make_config()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    return service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))


# --------------------------------------------------------------------------------------
# 一致
# --------------------------------------------------------------------------------------


def test_failure_readback_matches() -> None:
    """F 級は `state:failing` があり `assignee` が空、封筒の `kickoff_id` も一致する。"""
    reader, writer = make_linked_board(labels=(WORKING,))
    verdict = _finish_failure(reader, writer)
    assert verdict.grade is not None
    assert [call[0] for call in reader.calls] == ["show", "comments"]


def test_warning_readback_matches_on_closed_status() -> None:
    """W 級は `status is IssueStatus.closed` を見る。"""
    config = make_config()
    hook = make_hook(config)
    reader, writer = make_linked_board()
    service = make_service(hook=hook, reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_inbound(make_response(completion_payload(finish_reason="length")))
    verdict = service.finish("k-1", "i-1", "bot", make_outcome())
    assert verdict.closed is True


@pytest.mark.parametrize("assignee", [None, "", "  "])
def test_empty_assignee_is_accepted_in_three_forms(assignee: str | None) -> None:
    """未設定・空文字・空白のみをいずれも「空」として扱う（FR-054c）。"""
    reader = StubBoardReader(
        issue=make_issue(labels=[FAILING], assignee=assignee), comment_list=[_envelope()]
    )
    writer = StubBoardWriter()
    verdict = _finish_failure(reader, writer)
    assert verdict.grade is not None


def test_envelope_is_found_by_scanning_backwards() -> None:
    """封筒のあとに別の書き手のコメントがあっても成功する（SC-011a）。"""
    reader = StubBoardReader(
        issue=make_issue(labels=[FAILING], assignee=None),
        comment_list=[
            make_comment("先行するやり取り", comment_id="c-1", actor="human"),
            _envelope(),
            make_comment("あとから来た人のコメント", comment_id="c-10", actor="human"),
        ],
    )
    verdict = _finish_failure(reader, StubBoardWriter())
    assert verdict.grade is not None


# --------------------------------------------------------------------------------------
# 不一致の 4 通り
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "assignee", "comments", "label"),
    [
        ([], None, [_envelope()], "ラベルが無い"),
        ([FAILING], "someone", [_envelope()], "担当が空でない"),
        ([FAILING], None, [], "封筒が無い"),
        ([FAILING], None, [_envelope("k-other")], "封筒の kickoff_id が違う"),
    ],
)
def test_mismatch_raises_mapping_unverified(
    labels: list[str], assignee: str | None, comments: list[Comment], label: str
) -> None:
    """不一致は 4 通りとも `MappingUnverified`（SC-011）。"""
    reader = StubBoardReader(
        issue=make_issue(labels=labels, assignee=assignee), comment_list=comments
    )
    with pytest.raises(MappingUnverified) as caught:
        _finish_failure(reader, StubBoardWriter())
    assert caught.value.issue_id == "i-1", label
    assert "state:failing" in caught.value.expected


def test_marker_must_be_at_the_start_of_the_first_line() -> None:
    """印は**1 行目の先頭**にある必要がある（本文中にあるだけでは封筒と見なさない）。"""
    reader = StubBoardReader(
        issue=make_issue(labels=[FAILING], assignee=None),
        comment_list=[make_comment(f"参考: {ENVELOPE_MARKER} grade=F\nkickoff_id=k-1\n")],
    )
    with pytest.raises(MappingUnverified):
        _finish_failure(reader, StubBoardWriter())


# --------------------------------------------------------------------------------------
# 確認読取そのものが失敗する場合（CHK072 の穴を塞ぐ）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("failing_call", ["show", "comments"])
def test_readback_failure_is_translated(failing_call: str) -> None:
    """確認読取が落ちても呼び手へ飛ぶのは `MappingUnverified` だけ（FR-057・SC-012）。"""
    exc = BoardUnavailable("bd に届かない", argv=["bd"], rc=1, stderr_head="", attempts=1)
    reader = StubBoardReader(
        issue=make_issue(labels=[FAILING], assignee=None),
        comment_list=[_envelope()],
        fail_on={failing_call: exc},
    )
    with pytest.raises(MappingUnverified) as caught:
        _finish_failure(reader, StubBoardWriter())
    assert caught.value.actual == "readback: BoardUnavailable"
    assert caught.value.cause is exc


def test_readback_issue_not_found_is_translated() -> None:
    """`IssueNotFound` も同じく翻訳する。"""
    exc = IssueNotFound("課題が無い", issue_id="i-1")
    reader = StubBoardReader(fail_on={"show": exc})
    with pytest.raises(MappingUnverified) as caught:
        _finish_failure(reader, StubBoardWriter())
    assert caught.value.actual == "readback: IssueNotFound"


def test_readback_after_abort_reports_the_exception_name() -> None:
    """打ち切りがあり読み直しも失敗したら、受けた例外の名前を `actual` に載せる（FR-053a）。"""
    exc = BoardUnavailable("bd に届かない", argv=["bd"], rc=1, stderr_head="", attempts=1)
    reader = StubBoardReader(fail_on={"show": exc})
    writer = StubBoardWriter(fail_on={"comment": exc})
    with pytest.raises(MappingUnverified) as caught:
        _finish_failure(reader, writer)
    assert caught.value.actual == "readback: BoardUnavailable"
    assert writer.names == ["comment"]  # 打ち切ったので以後の手は打たない
    assert [call[0] for call in reader.calls] == ["show"]  # 読み直しは試みる


# --------------------------------------------------------------------------------------
# 停止痕跡（§6.5）
# --------------------------------------------------------------------------------------


def test_stop_trace_is_logged_at_error(caplog: pytest.LogCaptureFixture) -> None:
    """`MappingUnverified` の**前**に `ERROR` で痕跡が残る（FR-056）。"""
    reader = StubBoardReader(issue=make_issue(status=IssueStatus.open, assignee="someone"))
    with caplog.at_level(logging.ERROR), pytest.raises(MappingUnverified):
        _finish_failure(reader, StubBoardWriter())
    record = next(r for r in caplog.records if r.getMessage() == "monitor.mapping.unverified")
    assert record.levelno == logging.ERROR
    for key in ("kickoff_id", "issue_id", "envelope", "reason", "expected", "actual"):
        assert hasattr(record, key), key
    assert not hasattr(record, "correlation_id")  # 自分では入れない（整形器が載せる）


def test_mapping_unverified_message_names_the_issue() -> None:
    """`message` は `写像を確認読取で確かめられない: <issue_id>`。"""
    reader = StubBoardReader(issue=make_issue(assignee="someone"))
    with pytest.raises(MappingUnverified) as caught:
        _finish_failure(reader, StubBoardWriter())
    assert str(caught.value) == "写像を確認読取で確かめられない: i-1"
