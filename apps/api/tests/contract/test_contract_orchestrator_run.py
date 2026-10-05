"""orchestrator 提供の IF-49（`KickoffRunner.run`）の適合検証。US6。

台帳の `同期/非同期` 欄は **`同期（ブロッキング。スレッド化は呼び側）`** である。したがって
`run()` は呼び出しスレッドで完結し、呼び手（trigger）がスレッド化の責務を持つ（R-08）。

`KickoffBusy` は single-flight を 2 スレッドで奪い合わせて踏む。**片付けを必ず行う**
（`Event.set()` → `join(timeout=…)`。塞いだままテストを終えない＝ FR-028・SC-008）。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubBoardWriter,
    StubCrewExecutor,
    StubMemberResolver,
    StubRunMonitor,
    StubSettingsReader,
    StubStageCatalog,
)
from conftest import (
    SAMPLE_PROJECT_KEY,
    make_member,
    make_project_folders,
    make_rules,
    make_task_templates,
)

from common.interfaces.board import ClaimFilter, Issue, IssueStatus
from common.interfaces.executor import CrewOutcome, Escalation
from common.interfaces.monitor import FailureKind, Grade, Measurement, RunVerdict
from common.interfaces.orchestrator import (
    ExecutionContext,
    IssueRunSummary,
    KickoffBusy,
    KickoffResult,
    KickoffStatus,
    LaunchKind,
)
from common.interfaces.settings import HooksConfig, LimitPolicy, Project
from common.interfaces.stages import PromptBundle, PromptPurpose, StageSpec
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName

if TYPE_CHECKING:
    from conftest import OrchestratorFactory

    from common.interfaces.orchestrator import KickoffRunner

JOIN_TIMEOUT_SEC = 30.0
"""スレッドの回収を待つ上限（塞いだままにしない）。"""

ENTER_TIMEOUT_SEC = 30.0
"""別スレッドが single-flight を握るまで待つ上限。"""


def _context() -> ExecutionContext:
    """DM-19 の実行文脈（手動起動・起動条件は role:executor ＋ PJ）。"""
    return ExecutionContext(
        kind=LaunchKind.manual,
        trigger_snapshot=None,
        dropped_path=None,
        claim_filter=ClaimFilter(
            labels=frozenset({Labels.role_executor()}), project_key=SAMPLE_PROJECT_KEY
        ),
        requested_at=datetime.now(tz=UTC),
    )


class _BlockingBoardReader(StubBoardReader):
    """`list_working` で待たせるスタブ（single-flight を保持したまま止めるため）。

    記録つきスタブの規約（既定は無害・差し替えはコンストラクタ引数）はそのままに、
    **待ち合わせの都合だけ**を派生で足す。`CallLog` への記録は基底に委ねる。
    """

    def __init__(self, log: CallLog, entered: threading.Event, gate: threading.Event) -> None:
        """入場の合図と解放の合図を受け取る。"""
        super().__init__(log)
        self._entered = entered
        self._gate = gate

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """呼ばれたことを知らせてから解放を待つ。"""
        self._entered.set()
        self._gate.wait(timeout=ENTER_TIMEOUT_SEC)
        return super().list_working(claim_filter)


@pytest.mark.contract_if("IF-49")
def test_contract_orchestrator_run_returns_kickoff_result_synchronously(
    call_log: CallLog,
    kickoff_runner: KickoffRunner,
) -> None:
    """IF-49 正常系: DM-18 の `KickoffResult` が**同期に（ブロッキングで）**返る。

    9 スタブが空を返すので `list_working` → `[]`、`claim_next` → `None` となり、
    処理した課題が 0 件 ＝ `status=noop` になる（対象が無いことを失敗として扱わない）。

    あわせて**入力欄の材料が渡っている**ことを踏む —— 台帳 IF-49 の入力欄
    `ExecutionContext（DM-19: 起動種別・trigger_snapshot・dropped_path・ClaimFilter）` のうち、
    提供実装が実際に読むのは `claim_filter` だけである（残り 3 項目は読まれないので原理的に
    検証できない＝ design-sync.md B-20）。その 1 項目を assert しないと、**`ctx.claim_filter`
    を捨てて自前の `ClaimFilter` を組む実装でも緑**になる。渡した文脈を変数に受け、
    `list_working`（残骸の回収経路）と `claim_next`（確保経路）の**両方**へ同一物が届くことを言う。
    """
    context = _context()

    result = kickoff_runner.run(context)

    assert isinstance(result, KickoffResult)
    assert result.status is KickoffStatus.noop
    assert result.issues == []
    assert result.kickoff_id
    assert result.finished_at >= result.started_at

    assert call_log.of("BoardReader", "list_working")[0].kwargs["claim_filter"] == (
        context.claim_filter
    )
    assert call_log.of("BoardWriter", "claim_next")[0].kwargs["claim_filter"] == (
        context.claim_filter
    )


PROCESSED_ISSUE_ID = "contractbd-000042"
"""1 課題を処理する経路で確保される課題。"""

ENVELOPE = "[monitor] grade=I kinds=usage_missing\nkickoff_id=stub"
"""判定スタブが返す封筒（I 級付記にそのまま載る）。"""


class _OneShotBoardWriter(StubBoardWriter):
    """`claim_next` が **1 度だけ**課題を返し、2 度目からは `None` を返すスタブ。

    実物の `claim_next` は確保した課題を `state:working` にして次から拾わない。既定の
    `StubBoardWriter` は同じ課題を何度でも返すので、1 課題を通す経路では周回が終わらない
    （記録つきスタブの規約はそのままに、**周回の停止条件だけ**を派生で足す）。
    """

    def __init__(self, log: CallLog, issue: Issue) -> None:
        """1 度だけ返す課題を受け取る。"""
        super().__init__(log, claimed=issue)
        self._remaining = 1

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """1 件目だけ返す（記録は基底に委ねる）。"""
        claimed = super().claim_next(claim_filter, actor)
        if self._remaining == 0:
            return None
        self._remaining -= 1
        return claimed


def _claimed_issue() -> Issue:
    """確保される課題（`project:`・`persona:`・`kind:` の 3 ラベルを持つ）。"""
    now = datetime.now(tz=UTC)
    return Issue(
        id=PROCESSED_ISSUE_ID,
        title="契約テストの遂行課題",
        description="説明",
        status=IssueStatus.in_progress,
        labels=[
            Labels.project(SAMPLE_PROJECT_KEY),
            Labels.persona(PersonaKey.programmer),
            Labels.kind(KindLabel.child),
        ],
        parent=None,
        assignee=None,
        acceptance="- [ ] 契約どおりに振る舞うことを確認できる",
        dependencies=[],
        created_at=now,
        updated_at=now,
    )


def _execute_stage() -> StageSpec:
    """遂行の段（`mode="execute"`＝分解の経路に入らない）。"""
    return StageSpec(
        name=StageName.child,
        mode="execute",
        require_labels=set(),
        require_no_parent=False,
        child_labels=[],
        allowed_personas=set(),
        min_items=1,
        max_acceptance=None,
        allow_depends_on=True,
        require_outcome_path=False,
    )


def _bundle() -> PromptBundle:
    """段カタログのスタブが返すプロンプト（中身は経路に影響しない）。"""
    return PromptBundle(
        persona_key=PersonaKey.programmer,
        role="役割",
        goal="目標",
        backstory="背景",
        task_description="作業指示",
        expected_output="期待成果",
        task_tools=None,
        purpose=PromptPurpose.work,
        proposal_schema=None,
    )


def _halted_outcome() -> CrewOutcome:
    """上限で止まった実行結果（実行器を呼び直さずに締めへ抜ける＝写像表 行 7）。"""
    return CrewOutcome(
        proposal=None,
        task_output=None,
        completed=False,
        completion_reason="上限で停止した",
        review=None,
        escalation=None,
        written=[],
        events=[],
        halted=True,
        halted_reason=FailureKind.limit_requests,
        progress_notes=0,
    )


def _grade_i_verdict() -> RunVerdict:
    """等級 I の判定（盤面には書かない＝ `notes` に付記だけが残る）。"""
    now = datetime.now(tz=UTC)
    return RunVerdict(
        grade=Grade.I,
        kinds=[FailureKind.usage_missing],
        halted=True,
        closed=False,
        envelope=ENVELOPE,
        measurement=Measurement(
            kickoff_id="stub",
            requests=1,
            prompt_tokens=12,
            completion_tokens=8,
            cost_usd=0.0,
            cost_missing_count=1,
            max_tokens_before_send=3,
            halted_reason=FailureKind.limit_requests,
            started_at=now,
            finished_at=now,
            unattributed_requests=0,
        ),
        failures=[],
    )


@pytest.mark.contract_if("IF-49")
def test_contract_orchestrator_run_summarizes_one_processed_issue(
    call_log: CallLog, orchestrator_factory: OrchestratorFactory
) -> None:
    """IF-49 正常系: 1 課題を処理した `KickoffResult` の **`issues` と `notes`** を踏む。

    台帳の出力欄は `KickoffResult（DM-18: status・result・kickoff_id・issue_id・I 級付記）`
    を名指しするが、上の正常系は 9 スタブが空を返す `status=noop` だけで、
    `IssueRunSummary` が 1 件も組まれず `notes` への assert は 0 件である（FR-016）。

    **提供実装は orchestrator の 1 つのまま**（消費側 MOD の実装は登場しない＝ FR-007）で、
    スタブに値を渡せば 1 課題を通せる。ここが踏むのは最短の経路 ——
    実行器が**上限で止まった**（`CrewOutcome.halted`）ときは実行器を呼び直さず段別処理にも
    進まず締めへ抜ける（写像表 行 7）ので、`IssueRunSummary` が 1 件だけ積まれる。

    等級 I は**盤面に書かない**等級なので、判定は `notes` の 1 行としてだけ現れる
    （`service/kickoff.py:298`）。付記の本文は判定スタブが返した封筒**そのもの**である。

    周回の停止は `claim_next` が 2 度目に `None` を返すことで起こす（`_OneShotBoardWriter`）
    —— 実物は確保した課題を次から拾わない。
    """
    issue = _claimed_issue()
    reader = StubBoardReader(call_log, issues={PROCESSED_ISSUE_ID: issue})
    writer = _OneShotBoardWriter(call_log, issue)
    settings = StubSettingsReader(
        call_log,
        project=Project(
            project_key=SAMPLE_PROJECT_KEY,
            name="契約テストの PJ",
            folders=make_project_folders(),
            is_reserved=False,
        ),
        limits=LimitPolicy(scope="global", max_requests_per_kickoff=10, max_cost_per_kickoff=10.0),
        rules=make_rules(),
        hooks=HooksConfig(hooks=[]),
        tasks=make_task_templates([StageName.child]),  # 前処理の設定 5 種は 5 つとも要る
    )
    runner: KickoffRunner = orchestrator_factory(
        reader,
        writer_stub=writer,
        settings_stub=settings,
        member_stub=StubMemberResolver(call_log, member=make_member(PersonaKey.programmer)),
        monitor_stub=StubRunMonitor(call_log, verdict=_grade_i_verdict()),
        catalog_stub=StubStageCatalog(call_log, spec=_execute_stage(), bundle=_bundle()),
        executor_stub=StubCrewExecutor(call_log, outcome=_halted_outcome()),
    ).kickoff_runner

    context = _context()

    result = runner.run(context)

    assert result.status is KickoffStatus.ok, "1 件処理したので noop ではない"
    assert len(result.issues) == 1
    summary = result.issues[0]
    assert isinstance(summary, IssueRunSummary)
    assert summary.issue_id == PROCESSED_ISSUE_ID
    assert summary.result == "halted"
    assert summary.stage is StageName.child
    assert summary.persona is PersonaKey.programmer
    assert summary.grade is Grade.I
    assert summary.returns == 0

    assert result.notes == [f"{PROCESSED_ISSUE_ID}: 等級 I: {ENVELOPE}"]
    assert ("BoardWriter", "close") not in call_log.names(), "等級 I は盤面に書かない"
    assert ("BoardWriter", "set_labels") not in call_log.names()

    # 入力欄の `ClaimFilter` が**そのまま**盤面へ渡る（B-20 が切り分けた検証可能な 1 項目）
    assert call_log.of("BoardReader", "list_working")[0].kwargs["claim_filter"] == (
        context.claim_filter
    )
    assert call_log.of("BoardWriter", "claim_next")[0].kwargs["claim_filter"] == (
        context.claim_filter
    )


ESCALATION = Escalation(
    what_i_tried="試したこと",
    why_stuck="行き詰まった理由",
    decision_needed="人に決めてほしいこと",
)
"""実行器が返す「判断を仰ぐ」申告（段別処理が質問課題を起票して待たせる）。"""

QUESTION_ID = "stub-created-1"
"""`StubBoardWriter.create` の既定の戻り（起票された質問課題の識別子）。"""


def _escalating_outcome() -> CrewOutcome:
    """判断を仰いだ実行結果（**上限では止まっていない**＝写像表 行 7 に入らない）。"""
    return CrewOutcome(
        proposal=None,
        task_output="判断を仰ぐ",
        completed=False,
        completion_reason="判断を仰いだ",
        review=None,
        escalation=ESCALATION,
        written=[],
        events=[],
        halted=False,
        halted_reason=None,
        progress_notes=0,
    )


def _clean_verdict() -> RunVerdict:
    """失敗を 1 件も検出しなかった判定（等級が付かない＝盤面には書かない）。"""
    now = datetime.now(tz=UTC)
    return RunVerdict(
        grade=None,
        kinds=[],
        halted=False,
        closed=False,
        envelope=ENVELOPE,
        measurement=Measurement(
            kickoff_id="stub",
            requests=1,
            prompt_tokens=12,
            completion_tokens=8,
            cost_usd=0.001,
            cost_missing_count=0,
            max_tokens_before_send=3,
            halted_reason=None,
            started_at=now,
            finished_at=now,
            unattributed_requests=0,
        ),
        failures=[],
    )


@pytest.mark.contract_if("IF-49")
def test_contract_orchestrator_run_summarizes_a_non_halted_issue(
    call_log: CallLog, orchestrator_factory: OrchestratorFactory
) -> None:
    """IF-49: `IssueRunSummary.result` が **`halted` 以外**の値を取る（FR-016）。

    台帳の出力欄は `KickoffResult（DM-18: status・**result**・kickoff_id・issue_id・
    I 級付記）` を名指しし、`result` は 6 値の Literal（`common/interfaces/orchestrator.py`）
    だが、上の 1 課題テストが通すのは **`"halted"` の 1 値だけ**である —— `result` を定数
    `"halted"` で返す実装でも緑になってしまう（B-23 と同じ「歯止めの穴」）。

    ここは**上限で止まっていない**実行結果を返させて別の値へ抜ける。最短は「判断を仰ぐ」
    経路で、実行器が `escalation` を申告すると段別処理が質問課題を起票し、依存で待たせて
    確保を解放する（`service/execute.py` の `_escalate`）。等級は**付かない**判定を返して
    盤面への写像を起こさない（付くと `graded_failing`／`closed_by_monitor` が先に立つ）。

    **提供実装は orchestrator の 1 つのまま**（FR-007）。**1 本に限る**——
    5 値すべては網羅せず、「`halted` 以外へ到達しうる」ことだけを踏む。
    """
    issue = _claimed_issue()
    reader = StubBoardReader(call_log, issues={PROCESSED_ISSUE_ID: issue})
    writer = _OneShotBoardWriter(call_log, issue)
    settings = StubSettingsReader(
        call_log,
        project=Project(
            project_key=SAMPLE_PROJECT_KEY,
            name="契約テストの PJ",
            folders=make_project_folders(),
            is_reserved=False,
        ),
        limits=LimitPolicy(scope="global", max_requests_per_kickoff=10, max_cost_per_kickoff=10.0),
        rules=make_rules(),
        hooks=HooksConfig(hooks=[]),
        tasks=make_task_templates([StageName.child]),
    )
    runner: KickoffRunner = orchestrator_factory(
        reader,
        writer_stub=writer,
        settings_stub=settings,
        member_stub=StubMemberResolver(call_log, member=make_member(PersonaKey.programmer)),
        monitor_stub=StubRunMonitor(call_log, verdict=_clean_verdict()),
        catalog_stub=StubStageCatalog(call_log, spec=_execute_stage(), bundle=_bundle()),
        executor_stub=StubCrewExecutor(call_log, outcome=_escalating_outcome()),
    ).kickoff_runner

    result = runner.run(_context())

    assert result.status is KickoffStatus.ok
    assert len(result.issues) == 1
    summary = result.issues[0]
    assert isinstance(summary, IssueRunSummary)
    assert summary.issue_id == PROCESSED_ISSUE_ID
    assert summary.result != "halted", "6 値の Literal のうち `halted` 以外へ到達する"
    assert summary.result == "escalated"
    assert summary.grade is None, "等級が付かない判定（上の 1 課題テストは Grade.I）"
    assert summary.returns == 0

    # 判断を仰いだ経路の副作用 —— 質問課題を起票し、依存で待たせ、確保を解放する
    assert len(call_log.of("BoardWriter", "create")) == 1
    dependency = call_log.of("BoardWriter", "add_dependency")[0].kwargs
    assert dependency["from_id"] == PROCESSED_ISSUE_ID
    assert dependency["to_id"] == QUESTION_ID
    assert call_log.of("BoardWriter", "release"), "待たせるあいだ確保を解放する"
    assert ("BoardWriter", "close") not in call_log.names(), "判断待ちの課題は閉じない"


@pytest.mark.contract_if("IF-49")
def test_contract_orchestrator_run_raises_kickoff_busy_during_single_flight(
    call_log: CallLog,
    orchestrator_factory: OrchestratorFactory,
) -> None:
    """IF-49 例外系: single-flight 中の 2 度目の `run()` で `KickoffBusy`（R-08）。

    スレッド A を `list_working` で塞いで single-flight を保持させ、その間にテスト本体が
    `run()` を呼ぶ。**片付け**: `Event.set()` → `join(timeout=…)` で必ずスレッドを終わらせる。
    """
    entered = threading.Event()
    gate = threading.Event()
    reader = _BlockingBoardReader(call_log, entered, gate)
    runner: KickoffRunner = orchestrator_factory(reader).kickoff_runner
    first: list[KickoffResult] = []

    def _hold() -> None:
        first.append(runner.run(_context()))

    worker = threading.Thread(target=_hold, name="contract-kickoff-holder", daemon=True)
    worker.start()
    try:
        assert entered.wait(timeout=ENTER_TIMEOUT_SEC), "別スレッドが single-flight を握れない"

        with pytest.raises(KickoffBusy) as caught:
            runner.run(_context())

        assert caught.value.current_kickoff_id
    finally:
        gate.set()
        worker.join(timeout=JOIN_TIMEOUT_SEC)

    assert not worker.is_alive(), "スレッドを塞いだまま終えない（FR-028・SC-008）"
    assert first and first[0].status is KickoffStatus.noop
