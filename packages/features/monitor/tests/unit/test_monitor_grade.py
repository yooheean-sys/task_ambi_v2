"""US3: 等級付けと封筒（monitor-F09・contracts/verdict-mapping.md §3・§4）。

**等級を決める箇所はコード中に 1 つだけ**（FR-043・SC-007）。期待値は**テスト側に直書き**する。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from conftest import (
    ENVELOPE_MARKER,
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

from common.interfaces.executor import CrewEvent, CrewEventKind
from common.interfaces.monitor import FailureKind, Grade, RunVerdict
from common.kernel.log import UNBOUND_CORRELATION_ID, CorrelationContext

if TYPE_CHECKING:
    from collections.abc import Sequence

# --------------------------------------------------------------------------------------
# 等級と最重判定
# --------------------------------------------------------------------------------------


def _finish(
    *,
    grade_map: dict[str, str] | None = None,
    task_output: str | None = "成果物",
    events: Sequence[CrewEvent] | None = None,
    completed: bool = True,
    labels: tuple[str, ...] = (),
) -> tuple[RunVerdict, StubBoardWriter]:
    config = make_config(grade_map=grade_map)
    reader, writer = make_linked_board(labels=labels)
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    verdict = service.finish(
        "k-1",
        "i-1",
        "bot",
        make_outcome(task_output=task_output, events=events or (), completed=completed),
    )
    return verdict, writer


def test_no_failures_leaves_the_grade_none() -> None:
    """失敗が 1 件も無ければ `RunVerdict.grade` は `None`（FR-044）。"""
    verdict, writer = _finish()
    assert verdict.grade is None
    assert verdict.kinds == []
    assert writer.calls == []


def test_worst_grade_wins() -> None:
    """`F > W > I`。F と I が混ざれば最重は F。"""
    events = [make_event(CrewEventKind.summarization)]  # I
    verdict, _ = _finish(task_output=None, events=events)  # no_output は F
    assert verdict.grade is Grade.F
    assert {event.grade for event in verdict.failures} == {Grade.F, Grade.I}


def test_warning_wins_over_info() -> None:
    """W と I が混ざれば最重は W。"""
    events = [make_event(CrewEventKind.summarization)]  # I
    config_map = dict.fromkeys((kind.value for kind in FailureKind), "I")
    config_map["structured_conversion"] = "W"
    from common.interfaces.stages import Proposal, ProposalItem

    reader, writer = make_linked_board()
    config = make_config(grade_map=config_map)
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    verdict = service.finish(
        "k-1",
        "i-1",
        "bot",
        make_outcome(
            events=events,
            proposal=Proposal(
                items=[
                    ProposalItem(
                        title="子",
                        summary="要約です",
                        description="説明です",
                        acceptance=["- [ ] 何かする"],
                        depends_on=[],
                        persona=None,
                    )
                ]
            ),
        ),
    )
    assert verdict.grade is Grade.W


def test_unknown_kind_falls_to_f_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """`grade_map` に無い種類は **I ではなく F** に倒し警告する（FR-045）。"""
    partial = {kind.value: "I" for kind in FailureKind if kind is not FailureKind.no_output}
    with caplog.at_level(logging.WARNING):
        verdict, writer = _finish(grade_map=partial, task_output=None)
    assert verdict.grade is Grade.F
    assert "monitor.grade.unknown_kind" in caplog.text
    assert writer.names == ["comment", "set_labels", "release"]


# --------------------------------------------------------------------------------------
# 封筒の書式（§4）
# --------------------------------------------------------------------------------------


def test_envelope_layout() -> None:
    """1 行目が印・2 行目 `kickoff_id`・3 行目 `correlation_id`・4 行目が計測の数値。"""
    verdict, _ = _finish(task_output=None)
    lines = verdict.envelope.splitlines()
    assert lines[0].startswith(f"{ENVELOPE_MARKER} grade=F kinds=")
    assert lines[1] == "kickoff_id=k-1"
    assert lines[2] == f"correlation_id={UNBOUND_CORRELATION_ID}"
    assert lines[3].startswith("requests=")
    assert lines[4] == "- no_output: task_output=none"


def test_envelope_kinds_follow_declaration_order() -> None:
    """`kinds` は `FailureKind` の宣言順で `,` 区切り。詳細行の順も 1 行目と同じ。"""
    events = [make_event(CrewEventKind.summarization)]
    verdict, _ = _finish(task_output=None, events=events)
    lines = verdict.envelope.splitlines()
    assert lines[0].endswith("kinds=no_output,summarization")
    assert lines[4].startswith("- no_output:")
    assert lines[5].startswith("- summarization:")


def test_envelope_with_no_failures_uses_a_dash() -> None:
    """失敗が 0 件でも封筒は組む。`kinds` は `-`（FR-050・§7）。"""
    verdict, _ = _finish()
    lines = verdict.envelope.splitlines()
    assert lines[0] == f"{ENVELOPE_MARKER} grade=I kinds=-"
    assert len(lines) == 4  # 詳細行は無い


def test_envelope_carries_the_bound_correlation_id() -> None:
    """相関が束縛されていれば `begin` 時に控えた値が 3 行目に載る。"""
    config = make_config()
    reader, writer = make_linked_board()
    service = make_service(hook=make_hook(config), reader=reader, writer=writer, config=config)
    token = CorrelationContext.bind("c-7f01ce68")
    try:
        service.begin("k-1", make_state().limits, 1000)
    finally:
        CorrelationContext.reset(token)
    verdict = service.finish("k-1", "i-1", "bot", make_outcome(task_output=None))
    assert verdict.envelope.splitlines()[2] == "correlation_id=c-7f01ce68"


def test_envelope_fourth_line_has_only_numbers() -> None:
    """4 行目は計測の数値だけ（文字列は載せない）。"""
    verdict, _ = _finish(task_output=None)
    fourth = verdict.envelope.splitlines()[3]
    keys = [pair.split("=")[0] for pair in fourth.split(" ")]
    assert keys == [
        "requests",
        "prompt_tokens",
        "completion_tokens",
        "cost_usd",
        "cost_missing",
        "max_tokens_before_send",
        "unattributed",
    ]
    for pair in fourth.split(" "):
        float(pair.split("=")[1])


def _finish_warning() -> tuple[RunVerdict, StubBoardWriter]:
    """W 級（`finish_length`）の 1 回を回す。"""
    config = make_config()
    hook = make_hook(config)
    reader, writer = make_linked_board()
    service = make_service(hook=hook, reader=reader, writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    hook.on_inbound(make_response(completion_payload(finish_reason="length")))
    verdict = service.finish("k-1", "i-1", "bot", make_outcome())
    return verdict, writer


def test_summary_is_the_first_line_only() -> None:
    """`summary()` ＝ 1 行目だけ。W 級の `close(reason=...)` に渡る値と一致する。"""
    verdict, writer = _finish_warning()
    close_call = next(call for call in writer.calls if call[0] == "close")
    assert close_call[2] == verdict.envelope.splitlines()[0]
    assert "\n" not in close_call[2]


def test_warning_grade_closes_the_issue() -> None:
    """W 級は `comment` → `close` の順（`RunVerdict.closed` が真）。"""
    verdict, writer = _finish_warning()
    assert verdict.grade is Grade.W
    assert writer.names == ["comment", "close"]
    assert verdict.closed is True
