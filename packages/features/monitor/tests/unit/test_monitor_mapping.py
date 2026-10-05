"""US3: 盤面への写像（monitor-F10・contracts/verdict-mapping.md §5）。

**証拠を先に残してから状態を変える**（P5D-024）。不達は打ち切って確認読取へ進み、
**その場では送出しない**（FR-053）。
"""

from __future__ import annotations

import logging

import pytest
from conftest import (
    StubBoardReader,
    StubBoardWriter,
    completion_payload,
    make_config,
    make_event,
    make_hook,
    make_linked_board,
    make_outcome,
    make_response,
    make_service,
    make_state,
)

from common.interfaces.board import BoardUnavailable, IssueNotFound
from common.interfaces.executor import CrewEventKind
from common.interfaces.monitor import Grade, MappingUnverified
from common.kernel.enums import KindLabel, Labels, StateLabel

FAILING = Labels.state(StateLabel.failing)
WORKING = Labels.state(StateLabel.working)


def _unavailable() -> BoardUnavailable:
    return BoardUnavailable("bd に届かない", argv=["bd"], rc=1, stderr_head="", attempts=1)


def _not_found() -> IssueNotFound:
    return IssueNotFound("課題が無い", issue_id="i-1")


def _run_failure() -> tuple[StubBoardWriter, StubBoardReader]:
    """F 級（`no_output`）の 1 回を回す（写像が最後まで通る形）。"""
    config = make_config()
    reader, writer = make_linked_board(labels=(WORKING,))
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))
    return writer, reader


# --------------------------------------------------------------------------------------
# F 級 / W 級 / I 級 の写像
# --------------------------------------------------------------------------------------


def test_failure_grade_order_is_comment_labels_release() -> None:
    """F 級は `comment` → `set_labels` → `release` の順。`close` は呼ばない（SC-008）。"""
    writer, _ = _run_failure()
    assert writer.names == ["comment", "set_labels", "release"]
    assert "close" not in writer.names


def test_set_labels_is_one_call_with_add_and_remove() -> None:
    """`set_labels` は **1 回**の呼び出しで add 1・remove 1（2 回に分けない）。"""
    writer, _ = _run_failure()
    labels_calls = [call for call in writer.calls if call[0] == "set_labels"]
    assert len(labels_calls) == 1
    assert labels_calls[0][2] == f"add={FAILING}"
    assert labels_calls[0][3] == f"remove={WORKING}"


def test_question_label_is_never_used() -> None:
    """質問ラベルは失敗の写像に使わない（FR-052・D06-i）。"""
    writer, _ = _run_failure()
    assert Labels.kind(KindLabel.question) not in "".join(
        part for call in writer.calls for part in call
    )


def test_warning_grade_order_is_comment_close() -> None:
    """W 級は `comment` → `close` の順。"""
    config = make_config()
    hook = make_hook(config)
    reader, writer = make_linked_board()
    service = make_service(hook=hook, reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_inbound(make_response(completion_payload(finish_reason="length")))
    verdict = service.finish("k-1", "i-1", "bot", make_outcome())
    assert writer.names == ["comment", "close"]
    assert writer.calls[1][2] == verdict.envelope.splitlines()[0]


def test_info_grade_writes_nothing() -> None:
    """失敗が 1 件も無い run は**書込 0 回**で確認読取も行わない（SC-009）。"""
    config = make_config()
    reader, writer = make_linked_board()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    service.finish("k-1", "i-1", "bot", make_outcome())
    assert writer.calls == []
    assert reader.calls == []


def test_info_only_run_writes_nothing_but_records_the_kind() -> None:
    """**I 級だけ**の run も書込 0 回。付記は**判定の封筒にだけ**載る（SC-009・US3 AC-4）。

    失敗 0 件の run（上のテスト）と区別する —— こちらは失敗を 1 件検出しており、
    最重の等級が `I` である。盤面は 1 度も触らない。
    """
    config = make_config()
    reader, writer = make_linked_board()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    outcome = make_outcome(events=[make_event(CrewEventKind.summarization)])
    verdict = service.finish("k-1", "i-1", "bot", outcome)
    assert verdict.grade is Grade.I
    assert writer.calls == []
    assert reader.calls == []
    headline = verdict.envelope.splitlines()[0]
    assert "grade=I" in headline
    assert CrewEventKind.summarization.value in headline


# --------------------------------------------------------------------------------------
# 打ち切り（各段で不達を注入する）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("failing_step", ["comment", "set_labels", "release"])
@pytest.mark.parametrize("failure", ["BoardUnavailable", "IssueNotFound"])
def test_abort_stops_the_remaining_steps_and_still_raises(
    failing_step: str, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    """各段の不達で以後の手を打ち切り、確認読取へ進んで `MappingUnverified` になる。"""
    exc = _unavailable() if failure == "BoardUnavailable" else _not_found()
    reader, writer = make_linked_board(labels=(WORKING,))
    writer.fail_on = {failing_step: exc}
    config = make_config()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    with caplog.at_level(logging.ERROR), pytest.raises(MappingUnverified):
        service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))

    order = ["comment", "set_labels", "release"]
    assert writer.names == order[: order.index(failing_step) + 1]
    assert reader.calls  # 打ち切ったあとも確認読取は試みる（FR-053a）
    trace = next(r for r in caplog.records if r.getMessage() == "monitor.mapping.unverified")
    assert trace.__dict__["reason"] == f"{failing_step}: {failure}"  # 不達理由は停止痕跡に載る


def test_abort_on_close_is_reported() -> None:
    """W 級の `close` の不達も打ち切りとして扱う（`close` も `IssueNotFound` を送出しうる）。"""
    config = make_config()
    hook = make_hook(config)
    reader, writer = make_linked_board()
    writer.fail_on = {"close": _not_found()}
    service = make_service(hook=hook, reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_inbound(make_response(completion_payload(finish_reason="length")))
    with pytest.raises(MappingUnverified):
        service.finish("k-1", "i-1", "bot", make_outcome())
    assert writer.names == ["comment", "close"]


def test_abort_raises_even_when_the_readback_matches(caplog: pytest.LogCaptureFixture) -> None:
    """打ち切りがあれば確認読取が一致しても必ず停止する（US3 AC-8・SC-010）。"""
    reader, writer = make_linked_board(labels=(FAILING,), assignee=None)
    # 盤面は既に「一致する」状態だが、`release` が落ちるので打ち切りが記録される
    writer.fail_on = {"release": _unavailable()}
    config = make_config()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    with caplog.at_level(logging.ERROR), pytest.raises(MappingUnverified) as caught:
        service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))
    assert caught.value.actual.endswith("envelope=ok")  # 読み戻した状態は「一致」
    trace = next(r for r in caplog.records if r.getMessage() == "monitor.mapping.unverified")
    assert trace.__dict__["reason"] == "release: BoardUnavailable"


def test_board_exceptions_never_reach_the_caller() -> None:
    """呼び手へ飛ぶのは `MappingUnverified` だけ（FR-057・SC-012）。"""
    reader, writer = make_linked_board(labels=(WORKING,))
    writer.fail_on = {"comment": _unavailable()}
    config = make_config()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    try:
        service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))
    except MappingUnverified:
        pass
    except (BoardUnavailable, IssueNotFound) as exc:  # pragma: no cover - 失敗時のみ
        pytest.fail(f"盤面の例外が呼び手へ飛んだ: {type(exc).__name__}")


def test_label_strings_are_not_written_literally() -> None:
    """ラベルは `Labels.state(...)` 由来の値になる（直書きしていない）。"""
    writer, _ = _run_failure()
    labels_call = next(call for call in writer.calls if call[0] == "set_labels")
    assert labels_call[2] == f"add={Labels.state(StateLabel.failing)}"
    assert labels_call[3] == f"remove={Labels.state(StateLabel.working)}"
