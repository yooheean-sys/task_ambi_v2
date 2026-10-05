"""受け渡し型 37 件の代表インスタンス（SC-005 の母数）。

`common.interfaces.*` に宣言された `BaseModel` 派生の集合とこの登録が過不足 0 で
一致することを `tests/interfaces/test_passing_types.py` が検査する。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from common.interfaces.board import (
    ClaimFilter,
    Comment,
    Issue,
    IssueRef,
    IssueSpec,
    IssueStatus,
)
from common.interfaces.executor import (
    CrewEvent,
    CrewEventKind,
    CrewOutcome,
    CrewSpec,
    Escalation,
    ReviewVerdict,
)
from common.interfaces.monitor import (
    FailureEvent,
    FailureKind,
    Grade,
    Measurement,
    RunVerdict,
)
from common.interfaces.orchestrator import (
    ExecutionContext,
    IssueRunSummary,
    KickoffResult,
    KickoffStatus,
    LaunchKind,
    TriggerSnapshot,
)
from common.interfaces.settings import (
    HookRef,
    HooksConfig,
    LimitPolicy,
    LLMProfile,
    McpSpec,
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
from common.interfaces.vault import FileRef, FolderCheck, VaultArea, WrittenPath
from common.kernel.enums import Labels, PersonaKey, StageName, ToolName

AT = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)

# --- board -----------------------------------------------------------------
ISSUE_REF = IssueRef(
    id="bd-2",
    title="親",
    status=IssueStatus.open,
    labels=[Labels.role_executor()],
    dependency_type="blocks",
)
ISSUE = Issue(
    id="bd-1",
    title="題名",
    description="本文",
    status=IssueStatus.open,
    labels=["kind:child"],
    parent="bd-2",
    assignee=None,
    acceptance="- outcome/result.md",
    dependencies=[ISSUE_REF],
    created_at=AT,
    updated_at=AT,
)
COMMENT = Comment(id="c-1", issue_id="bd-1", actor="ai:programmer", body="進捗", created_at=AT)
ISSUE_SPEC = IssueSpec(
    title="題名",
    description="本文",
    labels=["kind:child"],
    parent="bd-2",
    acceptance="- outcome/result.md",
)
CLAIM_FILTER = ClaimFilter(project_key="demo")

# --- settings --------------------------------------------------------------
PROJECT_FOLDERS = ProjectFolders(
    pj_input_dir="demo/input",
    role_dirs={PersonaKey.programmer: "demo/roles/programmer"},
    outcome_dir="demo/outcome",
)
PROJECT_REPOS = ProjectRepos(
    remote_url="https://gitlab.example.com/demo/demo.git",
    local_path="/home/demo/work/demo",
)
PROJECT = Project(project_key="demo", name="デモ", folders=PROJECT_FOLDERS, is_reserved=False)
MCP_SPEC = McpSpec(name="fs", transport="stdio", command="uvx", args=["mcp-server-fs"])
LLM_PROFILE = LLMProfile(
    name="local",
    provider_kind=ProviderKind.openai_compatible,
    base_url="http://127.0.0.1:8080/v1",
    model_id="qwen3",
    context_window=32768,
    api_key_ref="LLM_API_KEY_LOCAL",
    threshold_t=30000,
)
MEMBER = Member(
    member_id="demo.programmer",
    project_key="demo",
    persona_key=PersonaKey.programmer,
    role="プログラマ",
    goal="実装する",
    backstory="経歴",
    llm_profile=LLM_PROFILE,
    tools=[ToolName.read_issue],
    skills=["python"],
    mcps=[MCP_SPEC],
    bd_actor="ai:demo.programmer",
    casbin_subject="member:demo.programmer",
)
LIMIT_POLICY = LimitPolicy(scope="demo", max_requests_per_kickoff=40, max_cost_per_kickoff=1.5)
RULES = Rules(project_rules="- 日本語で書く")
TASK_TEMPLATE = TaskTemplate(
    description="{title} を実装する", expected_output="成果物", tools=[ToolName.write_outcome]
)
TASK_TEMPLATES = TaskTemplates(generation=3, templates={"child": TASK_TEMPLATE})
HOOK_REF = HookRef(name="guard", points="PRE_TOOL_CALL")
HOOKS_CONFIG = HooksConfig(hooks=[HOOK_REF])

# --- vault -----------------------------------------------------------------
FOLDER_CHECK = FolderCheck(missing=[], ok=True)
FILE_REF = FileRef(
    area=VaultArea.pj_input,
    rel_path="demo/input/spec.md",
    name="spec.md",
    size_bytes=1024,
    mtime=AT,
)
WRITTEN_PATH = WrittenPath(
    rel_path="demo/outcome/result.md", bytes=2048, overwritten=False, archived_to=None
)

# --- monitor ---------------------------------------------------------------
FAILURE_EVENT = FailureEvent(
    grade=Grade.W, kind=FailureKind.usage_missing, evidence={"request_no": 3}, at=AT
)
MEASUREMENT = Measurement(
    kickoff_id="k-1",
    requests=12,
    prompt_tokens=1000,
    completion_tokens=200,
    cost_usd=0.12,
    cost_missing_count=1,
    max_tokens_before_send=8000,
    halted_reason=None,
    started_at=AT,
    finished_at=AT,
    unattributed_requests=0,
)
RUN_VERDICT = RunVerdict(
    grade=Grade.W,
    kinds=[FailureKind.usage_missing],
    halted=False,
    closed=True,
    envelope="等級 W",
    measurement=MEASUREMENT,
    failures=[FAILURE_EVENT],
)

# --- stages ----------------------------------------------------------------
STAGE_SPEC = StageSpec(
    name=StageName.child,
    mode="execute",
    require_labels={"kind:child"},
    require_no_parent=None,
    child_labels=[],
    allowed_personas={PersonaKey.programmer},
    max_acceptance=None,
    allow_depends_on=False,
    require_outcome_path=True,
)
CONTEXT_REFS = ContextRefs(
    issue_id="bd-1",
    title="題名",
    description="本文",
    acceptance="- outcome/result.md",
    parent_title=None,
    parent_acceptance=None,
    human_guidance=[],
    input_refs=["demo/input/spec.md"],
    existing_children=[],
    purpose=PromptPurpose.work,
    is_final_review=False,
)
PROMPT_BUNDLE = PromptBundle(
    persona_key=PersonaKey.programmer,
    role="プログラマ",
    goal="実装する",
    backstory="経歴",
    task_description="実装せよ",
    expected_output="成果物",
    task_tools=[ToolName.write_outcome],
    purpose=PromptPurpose.work,
)
PROPOSAL_ITEM = ProposalItem(
    title="子 1",
    summary="要約",
    description="本文",
    acceptance=["- outcome/a.md"],
    depends_on=[],
    persona=PersonaKey.programmer,
)
PROPOSAL = Proposal(items=[PROPOSAL_ITEM])
GATE_RESULT = GateResult(ok=True, problems=[], observations=[], order=[0])

# --- executor --------------------------------------------------------------
CREW_EVENT = CrewEvent(kind=CrewEventKind.crew_completed, at=AT, detail={"name": "crew"})
ESCALATION = Escalation(what_i_tried="試した", why_stuck="詰まった", decision_needed="判断が要る")
REVIEW_VERDICT = ReviewVerdict(approved=True, feedback="良い", files_read=["demo/outcome/a.md"])
CREW_SPEC = CrewSpec(
    issue_id="bd-1",
    mode="execute",
    purpose=PromptPurpose.work,
    bundle=PROMPT_BUNDLE,
    member=MEMBER,
    hooks=HOOKS_CONFIG,
    folders=PROJECT_FOLDERS,
    parent_acceptance=None,
)
CREW_OUTCOME = CrewOutcome(
    proposal=None,
    task_output="出力",
    completed=True,
    completion_reason="complete_issue",
    review=None,
    escalation=None,
    written=[WRITTEN_PATH],
    events=[CREW_EVENT],
    halted=False,
    halted_reason=None,
    progress_notes=2,
)

# --- orchestrator ----------------------------------------------------------
TRIGGER_SNAPSHOT = TriggerSnapshot(trigger_id="t-1", name="毎朝", type="timer")
EXECUTION_CONTEXT = ExecutionContext(
    kind=LaunchKind.timer,
    trigger_snapshot=TRIGGER_SNAPSHOT,
    dropped_path=None,
    claim_filter=CLAIM_FILTER,
    requested_at=AT,
)
ISSUE_RUN_SUMMARY = IssueRunSummary(
    issue_id="bd-1",
    stage=StageName.child,
    persona=PersonaKey.programmer,
    result="closed",
    grade=None,
    returns=0,
)
KICKOFF_RESULT = KickoffResult(
    kickoff_id="k-1",
    status=KickoffStatus.ok,
    issues=[ISSUE_RUN_SUMMARY],
    notes=[],
    started_at=AT,
    finished_at=AT,
)

INSTANCES: dict[str, BaseModel] = {
    "IssueRef": ISSUE_REF,
    "Issue": ISSUE,
    "Comment": COMMENT,
    "IssueSpec": ISSUE_SPEC,
    "ClaimFilter": CLAIM_FILTER,
    "ProjectFolders": PROJECT_FOLDERS,
    "ProjectRepos": PROJECT_REPOS,
    "Project": PROJECT,
    "McpSpec": MCP_SPEC,
    "LLMProfile": LLM_PROFILE,
    "Member": MEMBER,
    "LimitPolicy": LIMIT_POLICY,
    "Rules": RULES,
    "TaskTemplate": TASK_TEMPLATE,
    "TaskTemplates": TASK_TEMPLATES,
    "HookRef": HOOK_REF,
    "HooksConfig": HOOKS_CONFIG,
    "FolderCheck": FOLDER_CHECK,
    "FileRef": FILE_REF,
    "WrittenPath": WRITTEN_PATH,
    "FailureEvent": FAILURE_EVENT,
    "Measurement": MEASUREMENT,
    "RunVerdict": RUN_VERDICT,
    "StageSpec": STAGE_SPEC,
    "ContextRefs": CONTEXT_REFS,
    "PromptBundle": PROMPT_BUNDLE,
    "ProposalItem": PROPOSAL_ITEM,
    "Proposal": PROPOSAL,
    "GateResult": GATE_RESULT,
    "CrewEvent": CREW_EVENT,
    "Escalation": ESCALATION,
    "ReviewVerdict": REVIEW_VERDICT,
    "CrewSpec": CREW_SPEC,
    "CrewOutcome": CREW_OUTCOME,
    "TriggerSnapshot": TRIGGER_SNAPSHOT,
    "ExecutionContext": EXECUTION_CONTEXT,
    "IssueRunSummary": ISSUE_RUN_SUMMARY,
    "KickoffResult": KICKOFF_RESULT,
}
"""型名 → 代表インスタンス。37 件ちょうど。"""
