"""US3: 失敗の検出（monitor-F08・contracts/verdict-mapping.md §2）。

**すべての種類を `I` に倒した `grade_map`** で回すので、盤面への書込は 0 回になり
（FR-050）、`RunVerdict.kinds` が「何を検出したか」だけを映す。
"""

from __future__ import annotations

import logging

import pytest
from conftest import (
    StubBoardReader,
    StubBoardWriter,
    make_config,
    make_event,
    make_hook,
    make_outcome,
    make_request,
    make_service,
    make_state,
)

from common.interfaces.executor import CrewEvent, CrewEventKind
from common.interfaces.monitor import FailureKind, RunHalted
from common.interfaces.stages import Proposal, ProposalItem
from monitor.repository import HttpHook
from monitor.service import MonitorService

ALL_INFO = {kind.value: "I" for kind in FailureKind}
"""12 種すべてを `I` に倒す割当（写像を起こさずに検出だけを見る）。"""


def _service() -> tuple[MonitorService, HttpHook, StubBoardWriter]:
    """すべてを `I` に倒した割当で組む（写像を起こさずに検出だけを見る）。"""
    config = make_config(grade_map=ALL_INFO)
    hook = make_hook(config)
    writer = StubBoardWriter()
    service = make_service(hook=hook, reader=StubBoardReader(), writer=writer, config=config)
    service.begin("k-1", make_state().limits, 1000)
    return service, hook, writer


def _kinds(outcome_kwargs: dict[str, object]) -> list[FailureKind]:
    service, _, writer = _service()
    verdict = service.finish("k-1", "i-1", "bot", make_outcome(**outcome_kwargs))  # pyright: ignore[reportArgumentType]  # 表駆動の可変引数
    assert writer.calls == []  # I 級は書込 0 回
    return verdict.kinds


def _halt_once(hook: HttpHook) -> None:
    """上限 0 の kickoff で 1 本送り、フック側に停止の記録を残す。"""
    with pytest.raises(RunHalted):
        hook.on_outbound(make_request())


# --------------------------------------------------------------------------------------
# `CrewEventKind` 12 種の表（受け皿の無い 8 種は積まない＝ FR-038b）
# --------------------------------------------------------------------------------------

_UNMAPPED = [
    CrewEventKind.llm_call_started,
    CrewEventKind.llm_call_completed,
    CrewEventKind.tool_called,
    CrewEventKind.tool_denied,
    CrewEventKind.tool_failed,
    CrewEventKind.outcome_overwritten,
    CrewEventKind.crew_completed,
    CrewEventKind.crew_failed,
]


@pytest.mark.parametrize("event_kind", _UNMAPPED)
def test_events_without_a_receptacle_are_not_recorded(event_kind: CrewEventKind) -> None:
    """受け皿の無い 8 種は**積まない**（種類を捏造しない）。"""
    assert _kinds({"events": [make_event(event_kind)], "completed": True}) == []


def test_crew_failed_is_used_only_to_judge_incompleteness() -> None:
    """`crew_failed` 自体は積まないが「完了していない」の判定には使う。"""
    events = [make_event(CrewEventKind.crew_failed), make_event(CrewEventKind.llm_call_failed)]
    assert _kinds({"events": events, "completed": True}) == [FailureKind.api_exhausted]


def test_summarization_is_unconditional() -> None:
    """`summarization` は限定語なしの 1 対 1（M-01）。"""
    assert _kinds({"events": [make_event(CrewEventKind.summarization)], "completed": True}) == [
        FailureKind.summarization
    ]


@pytest.mark.parametrize(
    ("completed", "expected"),
    [(True, [FailureKind.retry_succeeded]), (False, [FailureKind.api_exhausted])],
)
def test_agent_retry_splits_on_completion(completed: bool, expected: list[FailureKind]) -> None:
    """`agent_retry` は完了しているかで種類が分かれる（FR-038a）。"""
    kwargs: dict[str, object] = {
        "events": [make_event(CrewEventKind.agent_retry)],
        "completed": completed,
    }
    if not completed:
        kwargs["task_output"] = "成果物"
    assert _kinds(kwargs) == expected


@pytest.mark.parametrize(
    ("completed", "expected"), [(True, []), (False, [FailureKind.api_exhausted])]
)
def test_llm_call_failed_needs_incompleteness(completed: bool, expected: list[FailureKind]) -> None:
    """`llm_call_failed` は**完了していれば積まない**（FR-038a）。"""
    assert (
        _kinds({"events": [make_event(CrewEventKind.llm_call_failed)], "completed": completed})
        == expected
    )


@pytest.mark.parametrize(
    ("completed", "expected"), [(True, []), (False, [FailureKind.guardrail_limit])]
)
def test_guardrail_failed_needs_incompleteness(
    completed: bool, expected: list[FailureKind]
) -> None:
    """`guardrail_failed` は完了していないときだけ（SD-16 の拡張）。"""
    assert (
        _kinds({"events": [make_event(CrewEventKind.guardrail_failed)], "completed": completed})
        == expected
    )


# --------------------------------------------------------------------------------------
# 出来事以外の観測点
# --------------------------------------------------------------------------------------


def test_proposal_presence_is_structured_conversion() -> None:
    """`proposal is not None` → `structured_conversion`（SD-02・SC-006a の正の検証）。"""
    proposal = Proposal(
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
    )
    assert _kinds({"proposal": proposal}) == [FailureKind.structured_conversion]


@pytest.mark.parametrize("task_output", [None, "", "   ", "　\t\n"])
def test_blank_task_output_is_no_output(task_output: str | None) -> None:
    """`None`／空文字／空白のみ（全角空白を含む）は `no_output`。"""
    assert _kinds({"task_output": task_output}) == [FailureKind.no_output]


def test_non_blank_task_output_is_not_recorded() -> None:
    """それ以外は積まない（**「定型のみ」は実装しない**＝ SD-15・B-25）。"""
    assert _kinds({"task_output": "定型の文言だけの出力"}) == []


# --------------------------------------------------------------------------------------
# 停止の種類の出所（§2.4）
# --------------------------------------------------------------------------------------


def test_halted_reason_from_the_hook_only() -> None:
    """フック側だけに停止がある場合。"""
    service, hook, writer = _service()
    service.begin("k-2", make_state(max_requests=0).limits, 1000)
    _halt_once(hook)
    verdict = service.finish("k-2", "i-1", "bot", make_outcome())
    assert verdict.kinds == [FailureKind.limit_requests]
    assert verdict.halted is True
    assert writer.calls == []


def test_halted_reason_from_the_outcome_only() -> None:
    """実行結果側だけに停止がある場合。"""
    kinds = _kinds({"halted": True, "halted_reason": FailureKind.api_exhausted, "completed": False})
    assert kinds == [FailureKind.api_exhausted]


def test_hook_wins_when_the_two_sources_disagree(caplog: pytest.LogCaptureFixture) -> None:
    """両方あって食い違うときは**フック側を正**とし警告する。"""
    service, hook, writer = _service()
    service.begin("k-3", make_state(max_requests=0).limits, 1000)
    _halt_once(hook)
    with caplog.at_level(logging.WARNING):
        verdict = service.finish(
            "k-3",
            "i-1",
            "bot",
            make_outcome(halted=True, halted_reason=FailureKind.api_exhausted, completed=False),
        )
    assert FailureKind.limit_requests in verdict.kinds
    assert "monitor.detect.halted_mismatch" in caplog.text
    assert writer.calls == []


def test_halted_without_any_reason_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    """`halted` が真なのに両方 `None` なら警告だけを残す（種類を捏造しない）。"""
    service, _, _ = _service()
    with caplog.at_level(logging.WARNING):
        verdict = service.finish(
            "k-1", "i-1", "bot", make_outcome(halted=True, halted_reason=None, completed=True)
        )
    assert verdict.kinds == []
    assert verdict.halted is True
    assert "monitor.detect.halted_unknown" in caplog.text


# --------------------------------------------------------------------------------------
# 重複排除と並び
# --------------------------------------------------------------------------------------


def test_duplicate_kinds_are_collapsed_keeping_the_first_evidence() -> None:
    """同じ種類が 2 回発火しても 1 つ。**最初の 1 件の `evidence`** が残る。"""
    events = [
        make_event(CrewEventKind.summarization, which="first"),
        make_event(CrewEventKind.summarization, which="second"),
    ]
    service, _, _ = _service()
    verdict = service.finish("k-1", "i-1", "bot", make_outcome(events=events))
    assert verdict.kinds == [FailureKind.summarization]
    assert len(verdict.failures) == 1


def test_kinds_are_sorted_in_declaration_order() -> None:
    """`RunVerdict.kinds` は `FailureKind` の**宣言順**に並ぶ（出力が一意になる）。"""
    events: list[CrewEvent] = [
        make_event(CrewEventKind.summarization),
        make_event(CrewEventKind.agent_retry),
        make_event(CrewEventKind.llm_call_failed),
    ]
    kinds = _kinds({"events": events, "completed": False, "task_output": "成果物"})
    assert kinds == [FailureKind.api_exhausted, FailureKind.summarization]


def test_empty_events_still_judges_from_other_sources(caplog: pytest.LogCaptureFixture) -> None:
    """`events` が空でも他の観測点で判定し、**検出器の欠落は警告に残すだけ**（是正後の FR-042）。"""
    service, _, _ = _service()
    with caplog.at_level(logging.WARNING):
        verdict = service.finish("k-1", "i-1", "bot", make_outcome(events=[], task_output=None))
    assert verdict.kinds == [FailureKind.no_output]
    assert "monitor.detect.no_events" in caplog.text
    record = next(r for r in caplog.records if r.getMessage() == "monitor.detect.no_events")
    assert record.__dict__["kickoff_id"] == "k-1"  # この警告だけ run に紐付かない、を防ぐ
