"""SC-005: 秘密がどこにも残らない（FR-024・FR-047）。

**ヘッダの値を残さない**のが第一の防御であり、`RedactFilter` は第二の防波堤である。
第二に依存しないことを確かめるため、伏字フィルタを**通さない**ロガ出力を観測する。
"""

from __future__ import annotations

import logging

import pytest
from conftest import (
    ENVELOPE_MARKER,
    StubBoardReader,
    StubBoardWriter,
    chat_payload,
    make_comment,
    make_hook,
    make_issue,
    make_outcome,
    make_request,
    make_response,
    make_service,
    make_state,
)

from common.interfaces.board import Comment, IssueStatus
from common.interfaces.monitor import MappingUnverified
from common.kernel.enums import Labels, StateLabel

SECRET = "Bearer sk-secret-value-0123456789"
"""要求・応答に載せる鍵（**どこにも現れてはならない**）。"""

SECRET_HEADERS = {
    "authorization": SECRET,
    "X-Api-Key": "ak-9999",
    "content-type": "application/json",
}


def test_header_values_never_appear_in_measurement_or_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """計測・積まれた失敗・構造化ログのどこにもヘッダ値が現れない。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    with caplog.at_level(logging.DEBUG):
        hook.on_outbound(make_request(headers=SECRET_HEADERS, content=b"broken json"))
        hook.on_inbound(make_response(headers=SECRET_HEADERS, content=b"broken json"))
    measurement, failures = hook.detach("k-1")

    haystack = repr(measurement) + repr(failures) + caplog.text
    assert SECRET not in haystack
    assert "sk-secret-value-0123456789" not in haystack
    assert "ak-9999" not in haystack


def test_header_names_are_recorded_in_lowercase(caplog: pytest.LogCaptureFixture) -> None:
    """記録されるのは**名前だけ**で、小文字に揃えて並ぶ。"""
    hook = make_hook()
    hook.attach(make_state())
    with caplog.at_level(logging.WARNING):
        hook.on_inbound(make_response(headers=SECRET_HEADERS, content=b"broken json"))
    record = next(r for r in caplog.records if r.getMessage() == "monitor.hook.measure_failed")
    names = record.__dict__["header_names"]
    assert "authorization" in names
    assert "x-api-key" in names
    assert all(name == name.lower() for name in names)


def test_envelope_carries_no_secret() -> None:
    """封筒（`RunVerdict.envelope`）にも秘密が載らない。"""
    hook = make_hook()
    writer = StubBoardWriter()
    reader = StubBoardReader(
        issue=make_issue(labels=[Labels.state(StateLabel.failing)], assignee=None)
    )
    service = make_service(hook=hook, reader=reader, writer=writer)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_outbound(make_request(headers=SECRET_HEADERS, content=b"broken json"))
    hook.on_inbound(make_response(headers=SECRET_HEADERS, content=b"broken json"))
    reader.comment_list = [_envelope_comment("k-1")]
    verdict = service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))
    assert SECRET not in verdict.envelope
    assert "ak-9999" not in verdict.envelope


def test_body_hashes_are_not_carried_over() -> None:
    """PoC の `body_sha256`／`messages_sha256` を移植していない。"""
    hook = make_hook()
    state = make_state()
    hook.attach(state)
    hook.on_outbound(make_request(chat_payload()))
    hook.on_inbound(make_response())
    _, failures = hook.detach("k-1")
    keys = {key for failure in failures for key in failure.evidence}
    assert "body_sha256" not in keys
    assert "messages_sha256" not in keys


def test_mapping_unverified_message_carries_no_secret() -> None:
    """停止痕跡（`MappingUnverified`）にも秘密が載らない。"""
    hook = make_hook()
    writer = StubBoardWriter()
    reader = StubBoardReader(issue=make_issue(status=IssueStatus.open, assignee="bot"))
    service = make_service(hook=hook, reader=reader, writer=writer)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_outbound(make_request(headers=SECRET_HEADERS, content=b"broken json"))
    with pytest.raises(MappingUnverified) as caught:
        service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))
    text = f"{caught.value} {caught.value.expected} {caught.value.actual}"
    assert SECRET not in text
    assert "ak-9999" not in text


def _envelope_comment(kickoff_id: str) -> Comment:
    """写像が残したはずの封筒（確認読取が拾う形）。"""
    return make_comment(f"{ENVELOPE_MARKER} grade=F kinds=no_output\nkickoff_id={kickoff_id}\n")
