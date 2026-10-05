"""出来事の翻訳（F11・T030・US1）。**表駆動**で書く。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

import crewai.events as crewai_events
import pytest
from conftest import DEFAULT_EVENT_ALLOWLIST, make_config, make_folders, make_member

from common.interfaces.executor import CrewEventKind
from executor.models import RunSession
from executor.service import (
    DETAIL_KEYS,
    DETAIL_VALUE_LIMIT,
    EVENT_KIND_BY_CLASS,
    EventTranslator,
    all_event_classes,
)

EXPECTED_MAPPING = {
    "LLMCallStartedEvent": CrewEventKind.llm_call_started,
    "LLMCallCompletedEvent": CrewEventKind.llm_call_completed,
    "LLMCallFailedEvent": CrewEventKind.llm_call_failed,
    "ToolUsageStartedEvent": CrewEventKind.tool_called,
    "ToolUsageFinishedEvent": CrewEventKind.tool_called,
    "ToolUsageErrorEvent": CrewEventKind.tool_failed,
    "LLMGuardrailStartedEvent": CrewEventKind.guardrail_failed,
    "LLMGuardrailCompletedEvent": CrewEventKind.guardrail_failed,
    "CrewKickoffCompletedEvent": CrewEventKind.crew_completed,
    "CrewKickoffFailedEvent": CrewEventKind.crew_failed,
}


def _dummy(annotation: Any) -> Any:
    """必須欄を埋めるための無害な値。"""
    text = str(annotation)
    if "str" in text:
        return ""
    if "bool" in text:
        return False
    if "int" in text:
        return 0
    if "datetime" in text:
        return datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    if "dict" in text or "Mapping" in text:
        return {}
    if "list" in text:
        return []
    return None


def make_event(class_name: str, **overrides: Any) -> Any:
    """crewai の**実クラス**でイベントを 1 件組む（通信ゼロ）。

    `model_construct` を使い**検証を通さない**。翻訳が見るのは実クラス名と素の文字列欄だけで、
    103 クラスそれぞれの検証器を満たすための材料を用意することに意味が無いため（既定値は
    `model_construct` が埋める）。
    """
    cls = getattr(crewai_events, class_name)
    kwargs = {
        name: _dummy(info.annotation)
        for name, info in cls.model_fields.items()
        if info.is_required()
    }
    kwargs.update(overrides)
    return cls.model_construct(**kwargs)


def make_translator() -> tuple[EventTranslator, RunSession]:
    """翻訳器と器を組む。"""
    session = RunSession(issue_id="bd-1", member=make_member(), folders=make_folders())
    return EventTranslator(session=session, config=make_config()), session


# --------------------------------------------------------------------------------------
# 写像（contracts/event-translation.md §2）
# --------------------------------------------------------------------------------------


def test_mapping_has_exactly_ten_entries() -> None:
    """`EVENT_KIND_BY_CLASS` は 10 エントリで、契約の表と一致する。"""
    assert dict(EVENT_KIND_BY_CLASS) == EXPECTED_MAPPING
    assert len(EVENT_KIND_BY_CLASS) == 10


def test_allowlist_default_matches_the_mapping() -> None:
    """出荷時既定の翻訳対象と写像表の鍵が一致する。"""
    assert set(DEFAULT_EVENT_ALLOWLIST) == set(EVENT_KIND_BY_CLASS)


@pytest.mark.parametrize(("class_name", "expected"), sorted(EXPECTED_MAPPING.items()))
def test_allowlisted_events_are_translated(class_name: str, expected: CrewEventKind) -> None:
    """翻訳対象の 10 クラスが 8 種へ写る。"""
    translator, session = make_translator()
    translator.handle(None, make_event(class_name))
    assert [event.kind for event in session.events] == [expected]
    assert session.events[0].detail["event"] == class_name


def test_tool_usage_finished_does_not_branch_on_failure() -> None:
    """`ToolUsageFinishedEvent` は `failure` 欄で**分岐しない**（常に `tool_called`）。"""
    translator, session = make_translator()
    for value in (True, False):
        translator.handle(
            None, make_event("ToolUsageFinishedEvent", tool_name="t", output=str(value))
        )
    assert [event.kind for event in session.events] == [
        CrewEventKind.tool_called,
        CrewEventKind.tool_called,
    ]


def test_both_guardrail_classes_map_to_one_kind_but_keep_the_class_name() -> None:
    """`LLMGuardrail*` の 2 クラスは同じ種類に写し、`detail.event` に実クラス名を残す。"""
    translator, session = make_translator()
    translator.handle(None, make_event("LLMGuardrailStartedEvent"))
    translator.handle(None, make_event("LLMGuardrailCompletedEvent"))
    assert {event.kind for event in session.events} == {CrewEventKind.guardrail_failed}
    assert [event.detail["event"] for event in session.events] == [
        "LLMGuardrailStartedEvent",
        "LLMGuardrailCompletedEvent",
    ]


# --------------------------------------------------------------------------------------
# 一覧に無い出来事（FR-060・SC-020）
# --------------------------------------------------------------------------------------


def test_unknown_events_are_counted_not_stored() -> None:
    """翻訳対象の外は器に **0 件**・`unknown_events` に件数と名前（SC-020）。"""
    translator, session = make_translator()
    outsiders = [
        name
        for name in (cls.__name__ for cls in all_event_classes())
        if name not in EVENT_KIND_BY_CLASS
    ][:12]
    assert len(outsiders) >= 10
    for name in outsiders:
        translator.handle(None, make_event(name))
    assert session.events == []
    assert sum(session.unknown_events.values()) == len(outsiders)
    assert set(session.unknown_events) == set(outsiders)


def test_matching_is_exact_not_wildcard() -> None:
    """照合は**完全一致**（`LLMCall*` のようなワイルドカードを解釈しない＝ FR-058）。"""
    translator, session = make_translator()
    translator.config = make_config(event_allowlist=["LLMCall*"])
    translator.handle(None, make_event("LLMCallStartedEvent"))
    assert session.events == []
    assert session.unknown_events == {"LLMCallStartedEvent": 1}


def test_unknown_is_not_rolled_into_crew_failed() -> None:
    """`crew_failed` に丸めない（監視側の失敗検出が誤発火する＝ DIFF-10）。"""
    translator, session = make_translator()
    translator.handle(None, make_event("AgentExecutionStartedEvent"))
    assert [event.kind for event in session.events] == []


# --------------------------------------------------------------------------------------
# 分担（SC-019）と重複排除（FR-062）
# --------------------------------------------------------------------------------------


def test_translation_never_produces_the_two_tool_layer_kinds() -> None:
    """`tool_denied`・`outcome_overwritten` は**購読の翻訳では 0 件**（道具の層が積む）。"""
    assert CrewEventKind.tool_denied not in set(EVENT_KIND_BY_CLASS.values())
    assert CrewEventKind.outcome_overwritten not in set(EVENT_KIND_BY_CLASS.values())


def test_translation_never_produces_agent_retry_or_summarization() -> None:
    """`agent_retry`・`summarization` が積まれる事例が 0 件（SC-019。発生源が無い）。"""
    kinds = set(EVENT_KIND_BY_CLASS.values())
    assert CrewEventKind.agent_retry not in kinds
    assert CrewEventKind.summarization not in kinds
    names = {cls.__name__.lower() for cls in all_event_classes()}
    assert not [name for name in names if "retry" in name or "summar" in name]


def test_no_deduplication() -> None:
    """**重複排除をしない**（同じ種類が何件来てもそのまま＝ FR-062）。"""
    translator, session = make_translator()
    for _ in range(5):
        translator.handle(None, make_event("LLMCallStartedEvent"))
    assert len(session.events) == 5


# --------------------------------------------------------------------------------------
# `detail` の規約（§5・FR-061）
# --------------------------------------------------------------------------------------


def test_detail_keys_are_the_six_allowed_ones() -> None:
    """`detail` のキーは 6 つだけ。"""
    assert DETAIL_KEYS == ("event", "tool", "error", "model", "reason", "path")


def test_detail_values_are_truncated() -> None:
    """値はすべて 200 文字で切る（FR-061）。"""
    translator, session = make_translator()
    translator.handle(None, make_event("ToolUsageErrorEvent", tool_name="t" * 500, error="e" * 500))
    (event,) = session.events
    assert set(event.detail) <= set(DETAIL_KEYS)
    for value in event.detail.values():
        assert len(value) <= DETAIL_VALUE_LIMIT


def test_detail_carries_no_crewai_object_repr() -> None:
    """crewai の型・内部表現を値に入れない。"""
    translator, session = make_translator()
    translator.handle(None, make_event("CrewKickoffFailedEvent", crew_name="c", error="壊れた"))
    (event,) = session.events
    assert "crewai." not in str(event.detail)
    assert "object at 0x" not in str(event.detail)


def test_timestamp_comes_from_the_event() -> None:
    """`at` は `BaseEvent.timestamp` を使う。"""
    stamp = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    translator, session = make_translator()
    translator.handle(None, make_event("LLMCallStartedEvent", timestamp=stamp))
    assert session.events[0].at == stamp


# --------------------------------------------------------------------------------------
# 購読の張り方（SD-18・SD-19）
# --------------------------------------------------------------------------------------


def test_all_event_classes_are_derived_from_all_not_dir() -> None:
    """一覧は `crewai.events.__all__` から導出する（`dir()` を使わない＝ SD-18）。"""
    classes = all_event_classes()
    assert len(classes) == 103
    assert {cls.__name__ for cls in classes} >= set(EVENT_KIND_BY_CLASS)


def test_concurrent_handlers_lose_nothing() -> None:
    """別スレッドから 100 件同時に流しても取りこぼさない（ロック＝ SD-19）。"""
    translator, session = make_translator()
    total = 100
    events = [make_event("LLMCallStartedEvent") for _ in range(total)]
    threads = [threading.Thread(target=translator.handle, args=(None, event)) for event in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(session.events) == total


def test_handler_swallows_its_own_exceptions() -> None:
    """ハンドラは自分で例外を握る（バスが `BaseException` ごと握り潰すため＝ §3）。"""
    translator, session = make_translator()

    class Broken:
        """`type(...).__name__` を引くと落ちる出来事の代わり。"""

        @property
        def timestamp(self) -> datetime:
            msg = "壊れている"
            raise RuntimeError(msg)

    translator.handle(None, Broken())
    assert session.events == []
