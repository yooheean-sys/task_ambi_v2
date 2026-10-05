"""`RunSession` の不変条件（T014）と `run`／`_build_crew` の検証（T034）。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from conftest import (
    FIXED_NOW,
    ScriptedLLM,
    Stubs,
    make_bundle,
    make_config,
    make_folders,
    make_member,
    make_profile,
    make_spec,
    make_stubs,
    use_env_file,
)

from common.interfaces.executor import (
    CrewEvent,
    CrewEventKind,
    CrewOutcome,
    Escalation,
    ExecutorFailure,
    ReviewVerdict,
)
from common.interfaces.monitor import FailureKind, RunHalted
from common.interfaces.settings import HookRef
from common.interfaces.stages import Proposal, ProposalItem
from common.interfaces.vault import WrittenPath
from common.kernel.enums import ToolName
from executor.models import ExecutorConfig, LlmFactory, RunSession
from executor.service import CrewExecutorImpl
from executor.tools import TOOL_BUILDERS, ToolRegistry

# --------------------------------------------------------------------------------------
# T014: `RunSession` の不変条件
# --------------------------------------------------------------------------------------


def make_session() -> RunSession:
    """検証用の `RunSession` を 1 個作る。"""
    return RunSession(issue_id="bd-1", member=make_member(), folders=make_folders())


def test_session_starts_empty() -> None:
    """1 回の `run` に 1 個。初期値は「何も積まれていない」状態である。"""
    session = make_session()
    assert session.proposal is None
    assert session.completed is False
    assert session.completion_reason == ""
    assert session.escalation is None
    assert session.review is None
    assert session.written == []
    assert session.events == []
    assert session.halted is False
    assert session.halted_reason is None
    assert session.progress_notes == 0
    assert session.unknown_events == {}


def test_two_sessions_do_not_share_state() -> None:
    """器は実行ごとに新規で、可変の欄を共有しない（FR-015・SC-003）。"""
    first, second = make_session(), make_session()
    first.add_event(CrewEvent(kind=CrewEventKind.tool_called, at=FIXED_NOW, detail={}))
    first.count_progress_note()
    assert second.events == []
    assert second.progress_notes == 0
    assert first.events is not second.events


def test_executor_impl_holds_no_session() -> None:
    """`CrewExecutorImpl` は `RunSession` を**フィールドに持たない**（FR-016）。"""
    fields = getattr(CrewExecutorImpl, "__dataclass_fields__", {})
    assert "session" not in fields
    annotations = {
        name: str(value) for name, value in getattr(CrewExecutorImpl, "__annotations__", {}).items()
    }
    assert not [name for name, value in annotations.items() if "RunSession" in value]


@pytest.mark.parametrize(
    ("setter", "first", "second", "reader"),
    [
        (
            "set_proposal",
            Proposal(items=[]),
            Proposal(
                items=[
                    ProposalItem(
                        title="t",
                        summary="s",
                        description="d",
                        acceptance=[],
                        depends_on=[],
                        persona=None,
                    )
                ]
            ),
            "proposal",
        ),
        (
            "set_escalation",
            Escalation(what_i_tried="a", why_stuck="b", decision_needed="c"),
            Escalation(what_i_tried="x", why_stuck="y", decision_needed="z"),
            "escalation",
        ),
        (
            "set_review",
            ReviewVerdict(approved=False, feedback="1", files_read=[]),
            ReviewVerdict(approved=True, feedback="2", files_read=["f"]),
            "review",
        ),
    ],
)
def test_declaration_last_call_wins(
    setter: str, first: object, second: object, reader: str
) -> None:
    """申告は**代入**であり追記ではない。最後の呼び出しが勝つ（FR-053a）。"""
    session = make_session()
    getattr(session, setter)(first)
    getattr(session, setter)(second)
    assert getattr(session, reader) is second


def test_completion_last_call_wins() -> None:
    """完了申告も最後が勝つ（FR-053a）。"""
    session = make_session()
    session.set_completion("一度目")
    session.set_completion("二度目")
    assert session.completed is True
    assert session.completion_reason == "二度目"


def test_to_outcome_fills_all_eleven_fields() -> None:
    """`to_outcome()` は `CrewOutcome` の **11 欄すべて**を埋める（FR-017）。"""
    session = make_session()
    outcome = session.to_outcome("本文")
    assert isinstance(outcome, CrewOutcome)
    assert set(CrewOutcome.model_fields) == {
        "proposal",
        "task_output",
        "completed",
        "completion_reason",
        "review",
        "escalation",
        "written",
        "events",
        "halted",
        "halted_reason",
        "progress_notes",
    }
    assert outcome.task_output == "本文"
    assert outcome.proposal is None
    assert outcome.review is None
    assert outcome.escalation is None
    assert outcome.written == []
    assert outcome.events == []
    assert outcome.halted is False
    assert outcome.halted_reason is None
    assert outcome.progress_notes == 0


def test_to_outcome_without_completion_declares_empty_reason() -> None:
    """完了申告が無ければ `completed=False`・`completion_reason=""`（FR-017a）。"""
    outcome = make_session().to_outcome(None)
    assert outcome.completed is False
    assert outcome.completion_reason == ""
    assert outcome.task_output is None


def test_unknown_events_are_not_written_to_outcome() -> None:
    """`unknown_events` は器に**写さない**（構造化ログにだけ残す＝ FR-060）。"""
    session = make_session()
    session.note_unknown_event("SomethingElseEvent")
    session.note_unknown_event("SomethingElseEvent")
    assert session.unknown_events == {"SomethingElseEvent": 2}
    assert "unknown" not in str(session.to_outcome(None).model_dump())


def test_halt_maps_only_the_kind() -> None:
    """`halt()` は種類だけを器へ写す。残る 3 属性は載せない（SD-16）。"""
    session = make_session()
    exc = RunHalted(kind=FailureKind.limit_cost, measured=1.5, limit=1.0, request_no=7)
    session.halt(exc)
    outcome = session.to_outcome(None)
    assert session.halted is True
    assert outcome.halted is True
    assert outcome.halted_reason is FailureKind.limit_cost
    dumped = str(outcome.model_dump())
    assert "1.5" not in dumped
    assert "request_no" not in dumped


def test_progress_notes_count_only_successes() -> None:
    """`progress_notes` は成功回数（FR-044a）。"""
    session = make_session()
    for _ in range(3):
        session.count_progress_note()
    assert session.to_outcome(None).progress_notes == 3


def test_written_accumulates_in_order() -> None:
    """書込は追記であり、順序を保つ。"""
    session = make_session()
    for index in range(3):
        session.add_written(
            WrittenPath(
                rel_path=f"outcome/{index}.md", bytes=1, overwritten=False, archived_to=None
            )
        )
    assert [path.rel_path for path in session.to_outcome(None).written] == [
        "outcome/0.md",
        "outcome/1.md",
        "outcome/2.md",
    ]


def test_concurrent_appends_lose_nothing() -> None:
    """追記はすべて `_lock` の中で行う（別スレッドから 100 件流して取りこぼし 0 件＝ SD-19）。"""
    session = make_session()
    total = 100

    def worker(index: int) -> None:
        session.add_event(
            CrewEvent(
                kind=CrewEventKind.tool_called,
                at=datetime.now(UTC),
                detail={"tool": str(index)},
            )
        )
        session.note_unknown_event("XEvent")
        session.count_progress_note()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(session.events) == total
    assert session.unknown_events == {"XEvent": total}
    assert session.progress_notes == total
    assert {event.detail["tool"] for event in session.events} == {str(i) for i in range(total)}


# --------------------------------------------------------------------------------------
# T034: `run` と `_build_crew`（F01／F03）
# --------------------------------------------------------------------------------------


def make_executor(
    stubs: Stubs | None = None, *, config: ExecutorConfig | None = None
) -> tuple[CrewExecutorImpl, Stubs]:
    """`CrewExecutorImpl` とスタブ束を組む。"""
    resolved = stubs if stubs is not None else make_stubs()
    impl = CrewExecutorImpl(
        deps=resolved.deps(),
        config=config if config is not None else make_config(),
        registry=ToolRegistry(builders=TOOL_BUILDERS),
        llm_factory=LlmFactory(),
    )
    return impl, resolved


def patch_crew(
    monkeypatch: pytest.MonkeyPatch, llm: object, *, raises: BaseException | None = None
) -> list[dict[str, Any]]:
    """`Agent`／`Task`／`Crew` の構築と `kickoff` を記録する（**実 AI を呼ばない**）。"""
    seen: list[dict[str, Any]] = []

    class FakeOutput:
        raw = "最終出力"

    class FakeCrew:
        def __init__(self, **kwargs: Any) -> None:
            seen.append({"kind": "Crew", **kwargs})

        def kickoff(self) -> object:
            seen.append({"kind": "kickoff"})
            if raises is not None:
                raise raises
            return FakeOutput()

    def fake_agent(**kwargs: Any) -> object:
        seen.append({"kind": "Agent", **kwargs})
        return object()

    def fake_task(**kwargs: Any) -> object:
        seen.append({"kind": "Task", **kwargs})
        return object()

    monkeypatch.setattr("executor.service.Agent", fake_agent)
    monkeypatch.setattr("executor.service.Task", fake_task)
    monkeypatch.setattr("executor.service.Crew", FakeCrew)

    def fake_build(self: LlmFactory, profile: object, adapter: object) -> object:
        return llm

    monkeypatch.setattr("executor.models.LlmFactory.build", fake_build)
    return seen


def entries(seen: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """記録から特定の種類だけを取り出す。"""
    return [entry for entry in seen if entry["kind"] == kind]


def test_kickoff_runs_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 回の `run` につき kickoff はちょうど 1 回（SC-001）。"""
    impl, _ = make_executor()
    seen = patch_crew(monkeypatch, ScriptedLLM())
    outcome = impl.run(make_spec())
    assert len(entries(seen, "kickoff")) == 1
    assert outcome.task_output == "最終出力"


def test_crew_is_one_agent_one_task_sequential(monkeypatch: pytest.MonkeyPatch) -> None:
    """構成は **1 Agent・1 Task・`Process.sequential`**・**委譲を許さない**（FR-027・P-13）。"""
    from crewai import Process

    impl, _ = make_executor()
    seen = patch_crew(monkeypatch, ScriptedLLM())
    impl.run(make_spec())
    (agent,) = entries(seen, "Agent")
    (task,) = entries(seen, "Task")
    (crew,) = entries(seen, "Crew")
    assert len(crew["agents"]) == 1
    assert len(crew["tasks"]) == 1
    assert crew["process"] is Process.sequential
    assert agent["allow_delegation"] is False
    assert agent["max_retry_limit"] == 1
    assert task["description"] == make_bundle().task_description
    assert task["expected_output"] == make_bundle().expected_output


def test_persona_fields_come_from_the_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """人格の 3 欄は **`spec.bundle` 側**から採る（`member` 側から採る事例 0 件＝ FR-025・C4）。"""
    impl, _ = make_executor()
    seen = patch_crew(monkeypatch, ScriptedLLM())
    impl.run(make_spec())
    (agent,) = entries(seen, "Agent")
    assert agent["role"] == "BUNDLE-ROLE"
    assert agent["goal"] == "BUNDLE-GOAL"
    assert agent["backstory"] == "BUNDLE-BACKSTORY"
    assert "MEMBER-" not in str([agent["role"], agent["goal"], agent["backstory"]])


@pytest.mark.parametrize(
    ("member_iter", "member_rpm", "member_time", "config_iter", "config_rpm", "expected"),
    [
        (None, None, None, 20, None, (20, None, None)),
        (7, 3, 60, 20, 99, (7, 3, 60)),
        (None, None, 45, 20, 99, (20, 99, 45)),
    ],
    ids=["all-defaults", "member-wins", "execution-time-passes-through"],
)
def test_three_limits(
    monkeypatch: pytest.MonkeyPatch,
    member_iter: int | None,
    member_rpm: int | None,
    member_time: int | None,
    config_iter: int,
    config_rpm: int | None,
    expected: tuple[int, int | None, int | None],
) -> None:
    """反復・毎分要求はメンバ優先で設定を既定に、**実行時間はそのまま**（FR-026・C2）。"""
    impl, _ = make_executor(config=make_config(max_iter=config_iter, max_rpm=config_rpm))
    seen = patch_crew(monkeypatch, ScriptedLLM())
    member = make_member(max_iter=member_iter, max_rpm=member_rpm, max_execution_time=member_time)
    impl.run(make_spec(member=member))
    (agent,) = entries(seen, "Agent")
    assert (agent["max_iter"], agent["max_rpm"], agent["max_execution_time"]) == expected


def test_skills_and_mcps_are_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`skills`・`mcps` は **1.15.17 では渡さない**（型が `Member` 側と合わない＝ C5・SD-22）。"""
    impl, _ = make_executor()
    seen = patch_crew(monkeypatch, ScriptedLLM())
    impl.run(make_spec(member=make_member(skills=["s1", "s2"])))
    (agent,) = entries(seen, "Agent")
    assert "skills" not in agent
    assert "mcps" not in agent


def test_no_crewai_type_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """戻り値・`CrewEvent.detail` に crewai 由来の型が **0 件**（SC-002）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())
    outcome = impl.run(make_spec())
    rendered = str(outcome.model_dump())
    assert "crewai" not in rendered
    assert "object at 0x" not in rendered
    for event in outcome.events:
        assert set(event.detail) <= {"event", "tool", "error", "model", "reason", "path"}


def test_two_runs_do_not_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 回続けて実行して 1 回目の申告・書込・出来事が 0 件混ざらない（SC-003）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())
    first = impl.run(make_spec())
    second = impl.run(make_spec())
    assert first.written == [] and second.written == []
    assert second.events == []
    assert second.completed is False
    assert second.progress_notes == 0


def test_no_completion_declaration_leaves_empty_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """完了申告なし → `completed=False`・`completion_reason=""`（FR-017a）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())
    outcome = impl.run(make_spec())
    assert outcome.completed is False
    assert outcome.completion_reason == ""


# --- 上限停止（SC-017・SC-018・US5 AC-4〜6） ---------------------------------------------


def test_run_halted_becomes_a_normal_return(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RunHalted` → 呼び手へ例外 0 件・`halted=True`・`halted_reason=kind`（SC-017）。"""
    impl, _ = make_executor()
    halted = RunHalted(kind=FailureKind.limit_cost, measured=2.0, limit=1.0, request_no=9)
    patch_crew(monkeypatch, ScriptedLLM(), raises=halted)
    outcome = impl.run(make_spec())
    assert outcome.halted is True
    assert outcome.halted_reason is FailureKind.limit_cost
    assert outcome.task_output is None


def test_run_halted_keeps_everything_already_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停止しても積んだ書込・出来事・申告がすべて残る（US5 AC-4〜6）。"""
    impl, stubs = make_executor()
    halted = RunHalted(kind=FailureKind.limit_requests, measured=11.0, limit=10.0, request_no=11)

    captured: list[Any] = []
    # pyright の抑制理由: 組み上がった道具を観測するために内部手続きを差し替える。
    # 公開口（`run`）からは道具の一覧を取り出せない。
    original = CrewExecutorImpl._build_crew  # pyright: ignore[reportPrivateUsage]

    def spy(self: CrewExecutorImpl, spec: Any, llm: Any, tools: Any) -> Any:
        captured.extend(tools)
        return original(self, spec, llm, tools)

    monkeypatch.setattr(CrewExecutorImpl, "_build_crew", spy)

    class FakeOutputCrew:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def kickoff(self) -> object:
            for tool in captured:
                if tool.name == "complete_issue":
                    tool.run(reason="途中まで")
                if tool.name == "record_progress":
                    tool.run(note="進んだ")
            raise halted

    def bare(**kwargs: Any) -> object:
        return object()

    def fake_build(self: LlmFactory, profile: object, adapter: object) -> object:
        return ScriptedLLM()

    monkeypatch.setattr("executor.service.Agent", bare)
    monkeypatch.setattr("executor.service.Task", bare)
    monkeypatch.setattr("executor.service.Crew", FakeOutputCrew)
    monkeypatch.setattr("executor.models.LlmFactory.build", fake_build)

    member = make_member(tools=[ToolName.complete_issue, ToolName.record_progress])
    outcome = impl.run(make_spec(member=member))
    assert outcome.halted is True
    assert outcome.completed is True
    assert outcome.completion_reason == "途中まで"
    assert outcome.progress_notes == 1
    assert stubs.log.count("BoardWriter.comment") == 1


@pytest.mark.parametrize(
    "wrapper",
    ["except-Exception", "except-RuntimeError", "try-finally", "contextlib-suppress"],
)
def test_halt_passes_through_generic_exception_handlers(
    monkeypatch: pytest.MonkeyPatch, wrapper: str
) -> None:
    """上限停止が「一般的な失敗の捕捉」を素通りする（SC-018。`BaseException` 直系）。"""
    import contextlib

    halted = RunHalted(kind=FailureKind.ctx_exceeded, measured=1.0, limit=0.5, request_no=1)

    def raise_halt() -> None:
        raise halted

    if wrapper == "except-Exception":
        with pytest.raises(RunHalted):
            try:
                raise_halt()
            except Exception:  # noqa: BLE001  # 素通りすることの検証
                pytest.fail("上限停止が except Exception に捕まった")
    elif wrapper == "except-RuntimeError":
        with pytest.raises(RunHalted):
            try:
                raise_halt()
            except RuntimeError:
                pytest.fail("上限停止が except RuntimeError に捕まった")
    elif wrapper == "try-finally":
        touched: list[str] = []
        with pytest.raises(RunHalted):
            try:
                raise_halt()
            finally:
                touched.append("finally")
        assert touched == ["finally"]
    else:
        with pytest.raises(RunHalted), contextlib.suppress(Exception):
            raise_halt()

    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM(), raises=halted)
    assert impl.run(make_spec()).halted is True


# --- 例外の翻訳（SC-021・FR-063） -------------------------------------------------------


def test_missing_api_key_is_a_build_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """鍵の不在は構築段（`phase="build"`）（SC-021・SC-023）。"""
    use_env_file(monkeypatch, tmp_path, "OTHER=1\n")
    impl, _ = make_executor()
    member = make_member(profile=make_profile(api_key_ref="LLM_API_KEY_MISSING"))
    with pytest.raises(ExecutorFailure) as excinfo:
        impl.run(make_spec(member=member))
    assert excinfo.value.phase == "build"


def test_agent_construction_failure_is_a_build_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Agent` の構築失敗も構築段（SC-021）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())

    def boom(**kwargs: Any) -> object:
        msg = "Agent が組めない"
        raise ValueError(msg)

    monkeypatch.setattr("executor.service.Agent", boom)
    with pytest.raises(ExecutorFailure) as excinfo:
        impl.run(make_spec())
    assert excinfo.value.phase == "build"
    assert excinfo.value.reason.startswith("ValueError: ")


def test_unknown_hook_is_a_build_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """カタログ外の差し込みも構築段（FR-056・SC-021）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())
    with pytest.raises(ExecutorFailure) as excinfo:
        impl.run(make_spec(hooks=[HookRef(name="unknown", points="PRE_TOOL_CALL", agents=[])]))
    assert excinfo.value.phase == "build"


def test_kickoff_failure_is_a_kickoff_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kickoff()` の内側の例外は実行段（`phase="kickoff"`）（SC-021）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM(), raises=RuntimeError("kickoff が壊れた"))
    with pytest.raises(ExecutorFailure) as excinfo:
        impl.run(make_spec())
    assert excinfo.value.phase == "kickoff"
    assert excinfo.value.reason.startswith("RuntimeError: ")
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_failure_reason_carries_no_crewai_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """`reason` に crewai の型そのものを載せない（型名の文字列だけ）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM(), raises=RuntimeError("壊れた"))
    with pytest.raises(ExecutorFailure) as excinfo:
        impl.run(make_spec())
    assert "object at 0x" not in excinfo.value.reason


def test_hooks_are_unregistered_even_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """実行が例外で終わっても差し込みは `finally` で必ず解除される（FR-054・SC-026）。"""
    from crewai.hooks import InterceptionPoint, get_hooks

    impl, _ = make_executor(config=make_config(hook_catalog=["audit"]))
    patch_crew(monkeypatch, ScriptedLLM(), raises=RuntimeError("壊れた"))
    refs = [HookRef(name="audit", points="PRE_TOOL_CALL", agents=[])]
    with pytest.raises(ExecutorFailure):
        impl.run(make_spec(hooks=refs))
    assert len(get_hooks(InterceptionPoint["PRE_TOOL_CALL"])) == 0


def test_source_contains_no_bare_base_exception_handler() -> None:
    """`BaseException` を丸ごと捕まえる `except` を 1 件も書かない（FR-065）。

    **コード行だけを見る**（docstring・コメントの言及は対象外）。
    """
    import inspect

    from executor import service

    handlers = [
        line.strip()
        for line in inspect.getsource(service).splitlines()
        if line.strip().startswith("except ")
    ]
    assert [line for line in handlers if "BaseException" in line] == []
    assert len([line for line in handlers if line.startswith("except RunHalted")]) == 1


# --- 構造化ログ（T066。憲章 §6・plan (iii)-11） -----------------------------------------

#: plan (iii)-11 が `extra` に載せると定めた項目のうち、`kickoff` を除く 5 つ。
LOG_FIELDS_BESIDES_KICKOFF = (
    "issue_id",
    "api_key_ref",
    "unknown_events",
    "halted",
    "flush_completed",
)


def only_record(caplog: pytest.LogCaptureFixture, message: str) -> Any:
    """`executor.service` が出した当該メッセージの記録をちょうど 1 件取り出す。"""
    found = [
        record
        for record in caplog.records
        if record.name == "executor.service" and record.getMessage() == message
    ]
    assert len(found) == 1, f"'{message}' の記録が {len(found)} 件"
    return found[0]


@pytest.mark.parametrize("mode", ["propose", "execute"])
def test_completion_log_carries_the_kickoff_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: Literal["propose", "execute"],
) -> None:
    """実行完了ログの `kickoff` は**分解（propose）と遂行（execute）の別**である。

    定数 `"done"` では、どちらの実行だったかが記録から判別できず monitor・orchestrator
    との突合ができない（plan (iii)-11）。
    """
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())
    with caplog.at_level("INFO", logger="executor.service"):
        impl.run(make_spec(issue_id="bd-7", mode=mode))
    record = only_record(caplog, "1 回の実行を終えた")
    assert record.kickoff == mode


def test_completion_log_carries_every_field_the_plan_lists(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """plan (iii)-11 の残り 5 項目が 1 つも欠けていない（欠けても実行は緑のまま進む）。"""
    impl, _ = make_executor()
    patch_crew(monkeypatch, ScriptedLLM())
    with caplog.at_level("INFO", logger="executor.service"):
        impl.run(make_spec(issue_id="bd-7"))
    record = only_record(caplog, "1 回の実行を終えた")
    missing = [field for field in LOG_FIELDS_BESIDES_KICKOFF if not hasattr(record, field)]
    assert missing == []
    assert record.issue_id == "bd-7"
    assert record.api_key_ref == make_profile().api_key_ref
    assert record.unknown_events == {}
    assert record.halted is False
    assert record.flush_completed is True


@pytest.mark.parametrize("mode", ["propose", "execute"])
def test_halt_log_carries_the_kickoff_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: Literal["propose", "execute"],
) -> None:
    """上限で停止した実行も**どちらだったか**を残す（事後分析に要る）。"""
    impl, _ = make_executor()
    halted = RunHalted(kind=FailureKind.limit_cost, measured=2.0, limit=1.0, request_no=9)
    patch_crew(monkeypatch, ScriptedLLM(), raises=halted)
    with caplog.at_level("INFO", logger="executor.service"):
        impl.run(make_spec(mode=mode))
    record = only_record(caplog, "上限で停止した")
    assert record.kickoff == mode
    assert record.halted is True
    assert record.kind == FailureKind.limit_cost.value
