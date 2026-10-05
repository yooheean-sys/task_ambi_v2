"""orchestrator のテスト共通部品（9 契約スタブ・`common` の型を組むヘルパ・組立ヘルパ）。

**実 `bd`・実 vault・実 LLM・ネットワーク・`apps/api/tests/_support` を使わない**（SC-040）。
**他 feature と `api`・`ui` を import しない**（FR-086）。`tmp_path` を使うのは設定読取の
テスト（`test_orchestrator_build.py`）だけである。

置くのは 3 種類だけ（plan (vi)）: (1) 9 契約のスタブ、(2) `common` の型を組むヘルパ、
(3) `make_config()` と組立ヘルパ（**既定値はここに直書きする**＝ `SHIPPED_CONFIG`）。

**`make_config()` は実装の `config.json` を「開く」** —— `OrchestratorConfig` の
`settings_customise_sources` が常に `JsonConfigSettingsSource(json_file=config_path())` を
並べるため、構築のたびにファイルが開かれる（2026-08-26 実測）。ただし `init_settings` が
優先されるので**値は直書きが勝つ**。ファイル I/O を数えるテストは `make_config()` を
計測の外で組むこと。

盤面の 2 契約（`BoardReader`／`BoardWriter`）は `BoardState` を共有する。差し戻しの勘定が
「書いたコメントを読み直して数える」（C-5）ため、書き手と読み手が別の世界を見ていると
SC-020a が組めない。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.interfaces.board import (
    BoardUnavailable,
    ClaimFilter,
    Comment,
    Issue,
    IssueNotFound,
    IssueSpec,
    IssueStatus,
)
from common.interfaces.executor import CrewOutcome, CrewSpec, Escalation, ReviewVerdict
from common.interfaces.monitor import (
    FailureKind,
    Grade,
    Measurement,
    RunVerdict,
)
from common.interfaces.orchestrator import ExecutionContext, LaunchKind
from common.interfaces.settings import (
    HookRef,
    HooksConfig,
    LimitPolicy,
    LLMProfile,
    Member,
    Project,
    ProjectFolders,
    ProjectRepos,
    ProviderKind,
    Rules,
    TaskTemplate,
    TaskTemplates,
)
from common.interfaces.stages import (
    ContextRefs,
    GateResult,
    PromptBundle,
    PromptPurpose,
    Proposal,
    ProposalItem,
    StageSpec,
)
from common.interfaces.vault import FolderCheck
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName, StateLabel
from orchestrator import OrchestratorConfig, OrchestratorDeps, OrchestratorHandle
from orchestrator import build as build_orchestrator
from orchestrator.models import IssueRun, Ports

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

    import httpx

FIXED_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
"""時刻を要する場所で使う固定値（テストが時計に依存しないようにする）。"""

SHIPPED_CONFIG: dict[str, object] = {
    "max_returns": 2,
    "recent_results_n": 20,
    "system_actor": "system",
    "comment_marker": "[orchestrator]",
    "review_persona": "planner",
}
"""出荷時既定（CFG-57〜61）。**実装の `config.json` を読まずにここへ直書きする**。"""


class NotCalledError(AssertionError):
    """orchestrator が呼ばないはずの契約メソッドが呼ばれた。"""

    def __init__(self, name: str) -> None:
        """呼ばれた手の名前を載せる。"""
        super().__init__(f"orchestrator は {name} を呼ばない")


# --------------------------------------------------------------------------------------
# (0) 呼び出しの記録と「N 回目で失敗」の台本
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Call:
    """契約メソッド 1 回の呼び出し（`"BoardWriter.create"` の形と引数）。"""

    target: str
    kwargs: dict[str, Any]


@dataclass(slots=True)
class CallLog:
    """9 契約に共通の呼び出し記録（**順序を保つ**）。"""

    calls: list[Call] = field(default_factory=list[Call])
    hooks: dict[str, Callable[[], None]] = field(default_factory=dict[str, "Callable[[], None]"])
    """呼ばれた瞬間に差し込む小道具（並行の待ち合わせ・相関 ID の観測に使う）。"""

    def record(self, target: str, **kwargs: Any) -> int:
        """1 回分を末尾に積み、その手が**何回目か**（1 起点）を返す。"""
        self.calls.append(Call(target=target, kwargs=kwargs))
        hook = self.hooks.get(target)
        if hook is not None:
            hook()
        return self.count(target)

    def targets(self) -> list[str]:
        """呼ばれた順の対象名。"""
        return [call.target for call in self.calls]

    def of(self, target: str) -> list[Call]:
        """対象名で絞った呼び出し（順序を保つ）。"""
        return [call for call in self.calls if call.target == target]

    def count(self, target: str) -> int:
        """対象名の呼び出し回数。"""
        return len(self.of(target))

    def only(self, *targets: str) -> list[str]:
        """指定した対象名だけを呼ばれた順に並べる（順序検査用）。"""
        return [name for name in self.targets() if name in targets]


@dataclass(slots=True)
class FailurePlan:
    """「`<契約>.<手>` の N 回目で失敗する」の指定（US3 の 4 失敗点）。"""

    at: dict[str, int] = field(default_factory=dict[str, int])
    error: dict[str, BaseException] = field(default_factory=dict[str, BaseException])

    def check(self, target: str, nth: int) -> None:
        """N 回目なら指定の例外（既定は `BoardUnavailable`）を送出する。"""
        if self.at.get(target) != nth:
            return
        raise self.error.get(target, make_unavailable(target))

    def fail(self, target: str, nth: int = 1, error: BaseException | None = None) -> None:
        """台本を 1 件足す（テストから使う）。"""
        self.at[target] = nth
        if error is not None:
            self.error[target] = error


def make_unavailable(where: str) -> BoardUnavailable:
    """`BoardUnavailable` を組む。"""
    return BoardUnavailable(
        f"bd に届かない（{where}）", argv=["bd", "list"], rc=1, stderr_head="boom", attempts=1
    )


# --------------------------------------------------------------------------------------
# (1) `common` の型を組むヘルパ
# --------------------------------------------------------------------------------------


def make_issue(
    issue_id: str = "bd-1",
    *,
    title: str = "題",
    labels: Sequence[str] = (),
    parent: str | None = None,
    acceptance: str = "",
    status: IssueStatus = IssueStatus.open,
    description: str = "本文",
) -> Issue:
    """`Issue` を組む。"""
    return Issue(
        id=issue_id,
        title=title,
        description=description,
        status=status,
        labels=list(labels),
        parent=parent,
        assignee=None,
        acceptance=acceptance,
        dependencies=[],
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )


def make_comment(issue_id: str, actor: str, body: str, comment_id: str = "c-1") -> Comment:
    """`Comment` を組む。"""
    return Comment(id=comment_id, issue_id=issue_id, actor=actor, body=body, created_at=FIXED_NOW)


def make_folders() -> ProjectFolders:
    """`ProjectFolders` を組む。"""
    return ProjectFolders(
        pj_input_dir="pj/alpha/input",
        role_dirs={PersonaKey.planner: "pj/alpha/planner"},
        outcome_dir="pj/alpha/outcome",
    )


def make_project(project_key: str = "alpha", *, repos: ProjectRepos | None = None) -> Project:
    """`Project` を組む。"""
    return Project(
        project_key=project_key,
        name="Alpha",
        folders=make_folders(),
        is_reserved=False,
        repos=repos,
    )


def make_llm_profile() -> LLMProfile:
    """`LLMProfile` を組む（`threshold_t` は計測開始に渡る）。"""
    return LLMProfile(
        name="default",
        provider_kind=ProviderKind.openai_compatible,
        base_url="http://llm.invalid/v1",
        model_id="model-x",
        context_window=8000,
        api_key_ref="LLM_API_KEY",
        threshold_t=6000,
    )


def make_member(
    persona: PersonaKey = PersonaKey.planner,
    *,
    project_key: str = "alpha",
    bd_actor: str = "ai-planner",
) -> Member:
    """`Member` を組む。"""
    return Member(
        member_id=f"{project_key}:{persona.value}",
        project_key=project_key,
        persona_key=persona,
        role="役",
        goal="目標",
        backstory="背景",
        llm_profile=make_llm_profile(),
        tools=[],
        skills=[],
        mcps=[],
        bd_actor=bd_actor,
        casbin_subject=f"sub:{persona.value}",
    )


def make_limits() -> LimitPolicy:
    """`LimitPolicy` を組む。"""
    return LimitPolicy(scope="alpha", max_requests_per_kickoff=10, max_cost_per_kickoff=1.0)


def make_rules() -> Rules:
    """`Rules` を組む。"""
    return Rules(project_rules="共通ルール")


def make_hooks() -> HooksConfig:
    """`HooksConfig` を組む。"""
    return HooksConfig(hooks=[HookRef(name="h", points="PRE_TOOL_CALL")])


def make_templates() -> TaskTemplates:
    """`TaskTemplates` を組む。"""
    return TaskTemplates(
        generation=1,
        templates={
            "child": TaskTemplate(description="子の説明", expected_output="出力", tools=None)
        },
    )


def make_stage_spec(
    *,
    name: StageName = StageName.child,
    mode: str = "execute",
    child_labels: Sequence[str] = (),
    allowed: Sequence[PersonaKey] = (PersonaKey.planner,),
) -> StageSpec:
    """`StageSpec` を組む（`mode` で分解／遂行を切り替える）。"""
    return StageSpec(
        name=name,
        mode="propose" if mode == "propose" else "execute",
        require_labels=set(),
        require_no_parent=None,
        child_labels=list(child_labels),
        allowed_personas=set(allowed),
        max_acceptance=None,
        allow_depends_on=True,
        require_outcome_path=False,
    )


def make_bundle(purpose: PromptPurpose = PromptPurpose.work) -> PromptBundle:
    """`PromptBundle` を組む。"""
    return PromptBundle(
        persona_key=PersonaKey.planner,
        role="役",
        goal="目標",
        backstory="背景",
        task_description="説明",
        expected_output="出力",
        task_tools=None,
        purpose=purpose,
    )


def make_proposal(
    count: int = 2,
    *,
    acceptance: Sequence[Sequence[str]] | None = None,
    depends: Sequence[Sequence[int]] | None = None,
) -> Proposal:
    """`Proposal` を組む（`acceptance` は項ごとの受入条文・`depends` は提案内 index）。"""
    items = [
        ProposalItem(
            title=f"子 {i}",
            summary="要約",
            description="本文",
            acceptance=list(acceptance[i]) if acceptance is not None else [],
            depends_on=list(depends[i]) if depends is not None else [],
            persona=PersonaKey.planner,
        )
        for i in range(count)
    ]
    return Proposal(items=items)


def make_gate_result(
    *,
    ok: bool = True,
    problems: Sequence[str] = (),
    observations: Sequence[str] = (),
    order: Sequence[int] = (0, 1),
) -> GateResult:
    """`GateResult` を組む。"""
    return GateResult(
        ok=ok, problems=list(problems), observations=list(observations), order=list(order)
    )


def make_escalation() -> Escalation:
    """`Escalation`（判断を仰ぐ申告）を組む。"""
    return Escalation(
        what_i_tried="A と B を試した",
        why_stuck="C が決まらない",
        decision_needed="C を決めてほしい",
    )


def make_review(*, approved: bool = True, feedback: str = "問題なし") -> ReviewVerdict:
    """`ReviewVerdict`（レビュー結果）を組む。"""
    return ReviewVerdict(approved=approved, feedback=feedback, files_read=[])


def make_outcome(
    *,
    proposal: Proposal | None = None,
    completed: bool = False,
    escalation: Escalation | None = None,
    review: ReviewVerdict | None = None,
    halted: bool = False,
) -> CrewOutcome:
    """`CrewOutcome` を組む。"""
    return CrewOutcome(
        proposal=proposal,
        task_output=None,
        completed=completed,
        completion_reason="",
        review=review,
        escalation=escalation,
        written=[],
        events=[],
        halted=halted,
        halted_reason=FailureKind.limit_requests if halted else None,
        progress_notes=0,
    )


def make_measurement(kickoff_id: str = "k-1") -> Measurement:
    """`Measurement` を組む。"""
    return Measurement(
        kickoff_id=kickoff_id,
        requests=1,
        prompt_tokens=10,
        completion_tokens=10,
        cost_usd=0.0,
        cost_missing_count=0,
        max_tokens_before_send=10,
        halted_reason=None,
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
        unattributed_requests=0,
    )


def make_verdict(
    *, grade: Grade | None = Grade.I, closed: bool = False, halted: bool = False
) -> RunVerdict:
    """`RunVerdict` を組む。"""
    return RunVerdict(
        grade=grade,
        kinds=[],
        halted=halted,
        closed=closed,
        envelope="[monitor] 封筒",
        measurement=make_measurement(),
        failures=[],
    )


def make_issue_run(
    *,
    issue: Issue | None = None,
    spec: StageSpec | None = None,
    member: Member | None = None,
    project: Project | None = None,
    human_guidance: Sequence[str] = (),
    returns: int = 0,
    outcome: CrewOutcome | None = None,
) -> IssueRun:
    """`IssueRun`（1 課題の処理状態）を組む。"""
    return IssueRun(
        issue=issue if issue is not None else make_issue(),
        spec=spec if spec is not None else make_stage_spec(),
        member=member if member is not None else make_member(),
        project=project if project is not None else make_project(),
        limits=make_limits(),
        rules=make_rules(),
        hooks=make_hooks(),
        templates=make_templates(),
        human_guidance=tuple(human_guidance),
        returns=returns,
        outcome=outcome,
    )


def make_ctx(*, claim_filter: ClaimFilter | None = None) -> ExecutionContext:
    """`ExecutionContext` を組む（human 起動）。"""
    return ExecutionContext(
        kind=LaunchKind.human,
        trigger_snapshot=None,
        dropped_path=None,
        claim_filter=claim_filter if claim_filter is not None else ClaimFilter(project_key=None),
        requested_at=FIXED_NOW,
    )


# --------------------------------------------------------------------------------------
# (2) 盤面の共有状態と 2 契約のスタブ
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class BoardState:
    """盤面の最小の写し（読み手と書き手が同じものを見る）。"""

    issues: dict[str, Issue] = field(default_factory=dict[str, Issue])
    comments: dict[str, list[Comment]] = field(default_factory=dict[str, list[Comment]])
    children: dict[str, list[Issue]] = field(default_factory=dict[str, list[Issue]])
    questions: dict[str, Issue | None] = field(default_factory=dict[str, "Issue | None"])
    working: list[Issue] = field(default_factory=list[Issue])
    claimable: deque[Issue] = field(default_factory=deque[Issue])
    created: list[IssueSpec] = field(default_factory=list[IssueSpec])
    seq: int = 100

    def add(self, issue: Issue) -> Issue:
        """課題を 1 件置く。"""
        self.issues[issue.id] = issue
        return issue

    def offer(self, issue: Issue) -> Issue:
        """確保で拾える課題として積む（`show` でも読めるようにする）。"""
        self.add(issue)
        self.claimable.append(issue)
        return issue

    def new_id(self) -> str:
        """新しい課題 ID を 1 つ発番する。"""
        self.seq += 1
        return f"bd-{self.seq}"


@dataclass(slots=True)
class StubBoardReader:
    """`BoardReader` のスタブ（IF-01〜09。orchestrator は `has_open_issues` を呼ばない）。"""

    log: CallLog
    state: BoardState
    failures: FailurePlan = field(default_factory=FailurePlan)

    def show(self, issue_id: str) -> Issue:
        """課題 1 件を読む。"""
        nth = self.log.record("BoardReader.show", issue_id=issue_id)
        self.failures.check("BoardReader.show", nth)
        issue = self.state.issues.get(issue_id)
        if issue is None:
            raise IssueNotFound(f"課題が無い: {issue_id}", issue_id=issue_id)
        return issue

    def children_of(self, parent_id: str) -> list[Issue]:
        """子課題を読む。"""
        nth = self.log.record("BoardReader.children_of", parent_id=parent_id)
        self.failures.check("BoardReader.children_of", nth)
        return list(self.state.children.get(parent_id, []))

    def open_question_of(self, issue_id: str) -> Issue | None:
        """未回答の質問を 1 件読む。"""
        nth = self.log.record("BoardReader.open_question_of", issue_id=issue_id)
        self.failures.check("BoardReader.open_question_of", nth)
        return self.state.questions.get(issue_id)

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """作業中の残骸を列挙する。"""
        nth = self.log.record("BoardReader.list_working", claim_filter=claim_filter)
        self.failures.check("BoardReader.list_working", nth)
        return list(self.state.working)

    def comments(self, issue_id: str) -> list[Comment]:
        """コメントを時刻順に読む。"""
        nth = self.log.record("BoardReader.comments", issue_id=issue_id)
        self.failures.check("BoardReader.comments", nth)
        return list(self.state.comments.get(issue_id, []))

    def has_open_issues(self, project_key: str) -> bool:
        """orchestrator は呼ばない。"""
        raise NotCalledError("BoardReader.has_open_issues")


@dataclass(slots=True)
class StubBoardWriter:
    """`BoardWriter` のスタブ（IF-10〜22）。"""

    log: CallLog
    state: BoardState
    failures: FailurePlan = field(default_factory=FailurePlan)

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """次の 1 件を確保する。積んだ順に返し、尽きたら `None`。"""
        nth = self.log.record("BoardWriter.claim_next", claim_filter=claim_filter, actor=actor)
        self.failures.check("BoardWriter.claim_next", nth)
        if not self.state.claimable:
            return None
        return self.state.claimable.popleft()

    def create(self, spec: IssueSpec, actor: str) -> str:
        """課題を起票する。"""
        nth = self.log.record("BoardWriter.create", spec=spec, actor=actor)
        self.failures.check("BoardWriter.create", nth)
        issue_id = self.state.new_id()
        issue = make_issue(
            issue_id,
            title=spec.title,
            labels=spec.labels,
            parent=spec.parent,
            acceptance=spec.acceptance,
            description=spec.description,
        )
        self.state.add(issue)
        self.state.created.append(spec)
        if spec.parent is not None:
            self.state.children.setdefault(spec.parent, []).append(issue)
        return issue_id

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """依存を張る。"""
        nth = self.log.record(
            "BoardWriter.add_dependency", from_id=from_id, to_id=to_id, actor=actor
        )
        self.failures.check("BoardWriter.add_dependency", nth)

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """依存元を付け替える。"""
        nth = self.log.record(
            "BoardWriter.move_dependents", old_id=old_id, new_id=new_id, actor=actor
        )
        self.failures.check("BoardWriter.move_dependents", nth)

    reclaim_on_release: bool = False
    """`True` なら解放した課題を確保待ちへ戻す（実盤面と同じく次の確保で拾える）。"""

    def release(self, issue_id: str, actor: str) -> None:
        """確保を解放する。"""
        nth = self.log.record("BoardWriter.release", issue_id=issue_id, actor=actor)
        self.failures.check("BoardWriter.release", nth)
        self.state.working = [i for i in self.state.working if i.id != issue_id]
        issue = self.state.issues.get(issue_id)
        if self.reclaim_on_release and issue is not None:
            self.state.claimable.append(issue)

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """コメントを 1 件付ける（**読み手から見える**）。"""
        nth = self.log.record("BoardWriter.comment", issue_id=issue_id, body=body, actor=actor)
        self.failures.check("BoardWriter.comment", nth)
        bucket = self.state.comments.setdefault(issue_id, [])
        bucket.append(
            make_comment(issue_id, actor, body, comment_id=f"c-{issue_id}-{len(bucket) + 1}")
        )

    close_takes_effect: bool = True
    """`False` なら `close` が例外を出さずに**効かない**（確認読取が `open` を読む）。

    実盤面の `BoardWriter.close` は `BoardWriteUnverified` を**宣言しない**ので、閉じられた
    ことを確かめる手段は読み直しだけである（D04-b）。その分岐を測るための小道具。
    """

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """課題を close する（確認読取が `closed` を読めるようにする）。"""
        nth = self.log.record("BoardWriter.close", issue_id=issue_id, reason=reason, actor=actor)
        self.failures.check("BoardWriter.close", nth)
        issue = self.state.issues.get(issue_id)
        if issue is not None and self.close_takes_effect:
            self.state.issues[issue_id] = replace_issue(issue, status=IssueStatus.closed)

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """ラベルを付け外しする。"""
        nth = self.log.record(
            "BoardWriter.set_labels", issue_id=issue_id, add=add, remove=remove, actor=actor
        )
        self.failures.check("BoardWriter.set_labels", nth)
        issue = self.state.issues.get(issue_id)
        if issue is None:
            return
        labels = [label for label in issue.labels if label not in remove]
        labels.extend(label for label in add if label not in labels)
        self.state.issues[issue_id] = replace_issue(issue, labels=labels)


def replace_issue(
    issue: Issue, *, status: IssueStatus | None = None, labels: Sequence[str] | None = None
) -> Issue:
    """`Issue` の一部を差し替えた新しいインスタンスを作る（frozen なので写しを作る）。"""
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if labels is not None:
        updates["labels"] = list(labels)
    return issue.model_copy(update=updates)


# --------------------------------------------------------------------------------------
# (3) 設定・メンバ・保管庫・計測・段・検査・実行器のスタブ
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StubSettingsReader:
    """`SettingsReader` のスタブ（IF-23〜26・IF-50）。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    project: Project = field(default_factory=make_project)
    limits: LimitPolicy = field(default_factory=make_limits)
    rules: Rules = field(default_factory=make_rules)
    hooks: HooksConfig = field(default_factory=make_hooks)
    templates: TaskTemplates = field(default_factory=make_templates)

    def get_project(self, project_key: str) -> Project:
        """プロジェクトを読む。"""
        nth = self.log.record("SettingsReader.get_project", project_key=project_key)
        self.failures.check("SettingsReader.get_project", nth)
        return self.project

    def get_limits(self, project_key: str) -> LimitPolicy:
        """上限を読む。"""
        nth = self.log.record("SettingsReader.get_limits", project_key=project_key)
        self.failures.check("SettingsReader.get_limits", nth)
        return self.limits

    def get_rules(self, project_key: str) -> Rules:
        """共通ルールを読む。"""
        nth = self.log.record("SettingsReader.get_rules", project_key=project_key)
        self.failures.check("SettingsReader.get_rules", nth)
        return self.rules

    def get_hooks(self, project_key: str) -> HooksConfig:
        """Hook 設定を読む。"""
        nth = self.log.record("SettingsReader.get_hooks", project_key=project_key)
        self.failures.check("SettingsReader.get_hooks", nth)
        return self.hooks

    def get_tasks(self, project_key: str) -> TaskTemplates:
        """段テンプレートを読む。"""
        nth = self.log.record("SettingsReader.get_tasks", project_key=project_key)
        self.failures.check("SettingsReader.get_tasks", nth)
        return self.templates


@dataclass(slots=True)
class StubMemberResolver:
    """`MemberResolver` のスタブ（IF-27・IF-28）。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    members: dict[PersonaKey, Member] = field(default_factory=dict[PersonaKey, Member])
    humans: set[str] = field(default_factory=set[str])

    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member:
        """メンバを解決する。"""
        nth = self.log.record(
            "MemberResolver.resolve", project_key=project_key, persona_key=persona_key
        )
        self.failures.check("MemberResolver.resolve", nth)
        return self.members.get(persona_key, make_member(persona_key, project_key=project_key))

    def is_human(self, actor: str) -> bool:
        """actor が人間かを返す（台帳内＝AI）。"""
        self.log.record("MemberResolver.is_human", actor=actor)
        return actor in self.humans


@dataclass(slots=True)
class StubVaultReader:
    """`VaultReader` のスタブ（orchestrator が呼ぶのは 2 メソッドだけ）。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    folder_check: FolderCheck = field(default_factory=lambda: FolderCheck(missing=[], ok=True))
    existing: set[str] = field(default_factory=set[str])

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck:
        """フォルダの存在を確認する。"""
        nth = self.log.record("VaultReader.check_project_folders", folders=folders)
        self.failures.check("VaultReader.check_project_folders", nth)
        return self.folder_check

    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[Any]:
        """orchestrator は呼ばない。"""
        raise NotCalledError("VaultReader.list_inputs")

    def read_input(self, folders: ProjectFolders, ref: Any) -> str:
        """orchestrator は呼ばない。"""
        raise NotCalledError("VaultReader.read_input")

    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str:
        """orchestrator は呼ばない。"""
        raise NotCalledError("VaultReader.read_outcome_file")

    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool:
        """成果物の存在を確認する（完了ゲート）。"""
        nth = self.log.record("VaultReader.outcome_exists", filename=filename)
        self.failures.check("VaultReader.outcome_exists", nth)
        return filename in self.existing


@dataclass(slots=True)
class StubRepoReader:
    """`RepoReader` のスタブ（orchestrator が呼ぶのは `exists` だけ。D25-b）。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    existing: set[str] = field(default_factory=set[str])

    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]:
        """orchestrator は呼ばない。"""
        raise NotCalledError("RepoReader.list_files")

    def read_file(self, repos: ProjectRepos, path: str) -> str:
        """orchestrator は呼ばない。"""
        raise NotCalledError("RepoReader.read_file")

    def exists(self, repos: ProjectRepos, path: str) -> bool:
        """リポジトリの成果物の存在を確認する（完了ゲート）。"""
        nth = self.log.record("RepoReader.exists", repos=repos, path=path)
        self.failures.check("RepoReader.exists", nth)
        return path in self.existing


@dataclass(slots=True)
class StubRunMonitor:
    """`RunMonitor` のスタブ（IF-38・IF-39）。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    verdicts: deque[RunVerdict] = field(default_factory=deque[RunVerdict])
    default_verdict: RunVerdict = field(default_factory=make_verdict)

    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold_t: int) -> None:
        """計測を開始する。"""
        nth = self.log.record(
            "RunMonitor.begin", kickoff_id=kickoff_id, limits=limits, threshold_t=threshold_t
        )
        self.failures.check("RunMonitor.begin", nth)

    def finish(
        self, kickoff_id: str, issue_id: str, actor: str, outcome: CrewOutcome
    ) -> RunVerdict:
        """計測を締めて判定を返す。"""
        nth = self.log.record(
            "RunMonitor.finish",
            kickoff_id=kickoff_id,
            issue_id=issue_id,
            actor=actor,
            outcome=outcome,
        )
        self.failures.check("RunMonitor.finish", nth)
        if self.verdicts:
            return self.verdicts.popleft()
        return self.default_verdict


@dataclass(slots=True)
class StubStageCatalog:
    """`StageCatalog` のスタブ（IF-44・IF-45）。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    spec: StageSpec = field(default_factory=make_stage_spec)
    bundle: PromptBundle = field(default_factory=make_bundle)

    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec:
        """段を決める。"""
        nth = self.log.record(
            "StageCatalog.resolve_stage", kind_labels=kind_labels, has_parent=has_parent
        )
        self.failures.check("StageCatalog.resolve_stage", nth)
        return self.spec

    def build_prompt(
        self,
        spec: StageSpec,
        member: Member,
        rules: Rules,
        templates: TaskTemplates,
        ctx: ContextRefs,
        feedback: str | None,
    ) -> PromptBundle:
        """プロンプトを組む。"""
        nth = self.log.record(
            "StageCatalog.build_prompt",
            spec=spec,
            member=member,
            rules=rules,
            templates=templates,
            ctx=ctx,
            feedback=feedback,
        )
        self.failures.check("StageCatalog.build_prompt", nth)
        return self.bundle


@dataclass(slots=True)
class StubProposalGate:
    """`ProposalGate` のスタブ（IF-46・IF-47）。

    `tokens` は**受入条文の全文をそのまま鍵**にした写像である。抽出規則を持たせないことで、
    「orchestrator が独自の抽出規則を持たない」（FR-061）をテスト側でも守る。
    """

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    results: deque[GateResult] = field(default_factory=deque[GateResult])
    default_result: GateResult = field(default_factory=make_gate_result)
    tokens: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    repo_tokens: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def validate_proposal(
        self, proposal: Proposal, spec: StageSpec, existing_children: list[str]
    ) -> GateResult:
        """提案を検査する。"""
        nth = self.log.record(
            "ProposalGate.validate_proposal",
            proposal=proposal,
            spec=spec,
            existing_children=existing_children,
        )
        self.failures.check("ProposalGate.validate_proposal", nth)
        if self.results:
            return self.results.popleft()
        return self.default_result

    def extract_outcome_tokens(self, acceptance: str) -> list[str]:
        """受入条文からトークンを抜く。"""
        nth = self.log.record("ProposalGate.extract_outcome_tokens", acceptance=acceptance)
        self.failures.check("ProposalGate.extract_outcome_tokens", nth)
        return list(self.tokens.get(acceptance, []))

    def extract_repo_tokens(self, acceptance: str) -> list[str]:
        """受入条文からリポジトリの成果物のトークンを抜く（D25-b。`tokens` と同じ流儀）。"""
        nth = self.log.record("ProposalGate.extract_repo_tokens", acceptance=acceptance)
        self.failures.check("ProposalGate.extract_repo_tokens", nth)
        return list(self.repo_tokens.get(acceptance, []))


@dataclass(slots=True)
class StubCrewExecutor:
    """`CrewExecutor` のスタブ（IF-48）。台本に例外を混ぜられる。"""

    log: CallLog
    failures: FailurePlan = field(default_factory=FailurePlan)
    script: deque[CrewOutcome | BaseException] = field(
        default_factory=deque["CrewOutcome | BaseException"]
    )
    default_outcome: CrewOutcome = field(default_factory=make_outcome)

    def run(self, spec: CrewSpec) -> CrewOutcome:
        """Crew を実行する。"""
        nth = self.log.record("CrewExecutor.run", spec=spec)
        self.failures.check("CrewExecutor.run", nth)
        if not self.script:
            return self.default_outcome
        item = self.script.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


# --------------------------------------------------------------------------------------
# (4) 10 契約の束と組立ヘルパ
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class Stubs:
    """10 契約のスタブ一式（共有の `CallLog`・`BoardState` つき）。"""

    log: CallLog
    state: BoardState
    board_reader: StubBoardReader
    board_writer: StubBoardWriter
    settings_reader: StubSettingsReader
    member_resolver: StubMemberResolver
    vault_reader: StubVaultReader
    run_monitor: StubRunMonitor
    stage_catalog: StubStageCatalog
    proposal_gate: StubProposalGate
    crew_executor: StubCrewExecutor
    repo_reader: StubRepoReader

    def deps(self) -> OrchestratorDeps:
        """`OrchestratorDeps` を組む。"""
        return OrchestratorDeps(
            board_reader=self.board_reader,
            board_writer=self.board_writer,
            settings_reader=self.settings_reader,
            member_resolver=self.member_resolver,
            vault_reader=self.vault_reader,
            run_monitor=self.run_monitor,
            stage_catalog=self.stage_catalog,
            proposal_gate=self.proposal_gate,
            crew_executor=self.crew_executor,
            repo_reader=self.repo_reader,
        )


def make_stubs() -> Stubs:
    """10 契約のスタブを 1 つの `CallLog`・`BoardState` で束ねる。"""
    log = CallLog()
    state = BoardState()
    return Stubs(
        log=log,
        state=state,
        board_reader=StubBoardReader(log=log, state=state),
        board_writer=StubBoardWriter(log=log, state=state),
        settings_reader=StubSettingsReader(log=log),
        member_resolver=StubMemberResolver(log=log),
        vault_reader=StubVaultReader(log=log),
        run_monitor=StubRunMonitor(log=log),
        stage_catalog=StubStageCatalog(log=log),
        proposal_gate=StubProposalGate(log=log),
        crew_executor=StubCrewExecutor(log=log),
        repo_reader=StubRepoReader(log=log),
    )


@pytest.fixture
def stubs() -> Stubs:
    """10 契約のスタブ一式。"""
    return make_stubs()


def make_config(**overrides: object) -> OrchestratorConfig:
    """出荷時既定（テスト側の直書き）に上書きを重ねた設定を作る。

    **値は直書きが勝つが、構築の副作用として実装の `config.json` が開かれる**（上の注記）。
    """
    values = dict(SHIPPED_CONFIG)
    values.update(overrides)
    return OrchestratorConfig(**values)


def make_handle(stubs: Stubs, **overrides: object) -> OrchestratorHandle:
    """スタブと差し替え設定で `build()` を通す（**`config.json` の値は使わない**）。"""
    return build_orchestrator(stubs.deps(), config=make_config(**overrides))


def make_ports(stubs: Stubs, **overrides: object) -> Ports:
    """`service/` 各ファイルの第 1 引数（`Ports`）を組む。"""
    return Ports(deps=stubs.deps(), config=make_config(**overrides))


class HttpClient(Protocol):
    """router のテストが使う `TestClient` の面（**`apps/api/tests/_support` を使わない**）。

    starlette の `testclient` は型検査時に `httpx2` を import する作りで、`httpx2` を入れて
    いない本リポジトリでは応答の型が解決できない。実行時に返るのは `httpx.Response`
    （`httpx` はルートの dev グループにある）なので、使う面だけをここで型として固定する。
    """

    def get(self, url: str) -> httpx.Response:
        """GET 要求を送る。"""
        ...

    def post(self, url: str, *, json: object | None = None) -> httpx.Response:
        """POST 要求を送る。"""
        ...

    def __enter__(self) -> HttpClient:
        """lifespan を通してクライアントを返す。"""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """lifespan の終了処理を通す。"""
        ...


def make_client(handle: OrchestratorHandle) -> HttpClient:
    """`handle.router` だけを載せた `FastAPI` に `TestClient` を繋ぐ。"""
    app = FastAPI()
    app.include_router(handle.router)
    client: HttpClient = TestClient(app)
    return client


def working_label() -> str:
    """`state:working`。"""
    return Labels.state(StateLabel.working)


def failing_label() -> str:
    """`state:failing`。"""
    return Labels.state(StateLabel.failing)


def kind_label(value: KindLabel = KindLabel.child) -> str:
    """`kind:<value>`。"""
    return Labels.kind(value)


def project_label(project_key: str = "alpha") -> str:
    """`project:<key>`。"""
    return Labels.project(project_key)


def persona_label(value: PersonaKey = PersonaKey.planner) -> str:
    """`persona:<value>`。"""
    return Labels.persona(value)
