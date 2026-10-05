"""リポジトリの成果物（`repo/<パス>`。D25-b）の完了ゲート・被覆検査・`CrewSpec.repos`。

抽出は `ProposalGate.extract_repo_tokens` に委ね、orchestrator が持つのは**接頭辞の定数 1 つ**
（`REPO_PREFIX`）だけである。実在・閉じ込めの判定は `RepoReader.exists` に委ねる。
"""

from __future__ import annotations

from conftest import (
    Stubs,
    make_gate_result,
    make_issue,
    make_issue_run,
    make_outcome,
    make_ports,
    make_project,
    make_proposal,
    make_review,
    make_stage_spec,
    project_label,
)

from common.interfaces.executor import CrewSpec
from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.interfaces.settings import ProjectRepos
from orchestrator.models import OUTCOME_PREFIX, REPO_PREFIX, CloseIntent, IssueRun, Retry
from orchestrator.service import execute, kickoff, propose

REPOS = ProjectRepos(remote_url="https://gitlab.example.com/g/r.git", local_path="/work/r")
ACCEPTANCE = "- [ ] repo/src/app.py と repo/tests/test_app.py が在る"
TOKENS = [f"{REPO_PREFIX}src/app.py", f"{REPO_PREFIX}tests/test_app.py"]


def _execute_run(stubs: Stubs, *, repos: ProjectRepos | None = REPOS) -> IssueRun:
    issue = make_issue("bd-1", labels=[project_label()], acceptance=ACCEPTANCE)
    stubs.state.add(issue)
    stubs.proposal_gate.repo_tokens[ACCEPTANCE] = list(TOKENS)
    return make_issue_run(
        issue=issue,
        spec=make_stage_spec(mode="execute"),
        project=make_project(repos=repos),
        outcome=make_outcome(completed=True),
    )


# --------------------------------------------------------------------------------------
# 完了ゲート
# --------------------------------------------------------------------------------------


def test_all_repo_deliverables_exist_then_review(stubs: Stubs) -> None:
    stubs.repo_reader.existing = {"src/app.py", "tests/test_app.py"}
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())

    step = execute.handle(make_ports(stubs), _execute_run(stubs))

    assert [c.kwargs["path"] for c in stubs.log.of("RepoReader.exists")] == [
        "src/app.py",
        "tests/test_app.py",
    ]
    assert all(c.kwargs["repos"] == REPOS for c in stubs.log.of("RepoReader.exists"))
    assert stubs.log.count("CrewExecutor.run") == 1
    assert isinstance(step, CloseIntent)


def test_missing_repo_deliverable_is_a_retry_without_review(stubs: Stubs) -> None:
    stubs.repo_reader.existing = {"src/app.py"}

    step = execute.handle(make_ports(stubs), _execute_run(stubs))

    assert isinstance(step, Retry)
    assert "repo/tests/test_app.py: 実在しない" in step.feedback
    assert "src/app.py:" not in step.feedback.replace("tests/test_app.py", "")
    assert stubs.log.count("CrewExecutor.run") == 0


def test_unregistered_repository_is_reported_without_asking_repo(stubs: Stubs) -> None:
    step = execute.handle(make_ports(stubs), _execute_run(stubs, repos=None))

    assert isinstance(step, Retry)
    assert "リポジトリが登録されていない" in step.feedback
    assert stubs.log.count("RepoReader.exists") == 0


def test_repository_not_cloned_is_reported(stubs: Stubs) -> None:
    stubs.repo_reader.failures.fail(
        "RepoReader.exists",
        1,
        RepoNotFound("git の作業ツリーが無い（未 clone か、パスが違う）", ""),
    )

    step = execute.handle(make_ports(stubs), _execute_run(stubs))

    assert isinstance(step, Retry)
    assert "未 clone" in step.feedback


def test_path_violation_is_reported_with_its_kind(stubs: Stubs) -> None:
    stubs.repo_reader.failures.fail(
        "RepoReader.exists", 1, RepoPathViolation("外", "outside_repo", "src/app.py")
    )
    stubs.repo_reader.existing = {"tests/test_app.py"}

    step = execute.handle(make_ports(stubs), _execute_run(stubs))

    assert isinstance(step, Retry)
    assert "outside_repo" in step.feedback


def test_outcome_and_repo_deliverables_are_both_checked(stubs: Stubs) -> None:
    stubs.proposal_gate.tokens[ACCEPTANCE] = [f"{OUTCOME_PREFIX}report.md"]
    stubs.repo_reader.existing = {"src/app.py", "tests/test_app.py"}
    stubs.vault_reader.existing = set()

    step = execute.handle(make_ports(stubs), _execute_run(stubs))

    assert isinstance(step, Retry)
    assert "report.md: 実在しない" in step.feedback
    assert stubs.log.count("RepoReader.exists") == 2


def test_no_repo_tokens_means_no_repo_calls(stubs: Stubs) -> None:
    """repo トークンが無い課題（従来の課題）では repo に問い合わせない。"""
    run = _execute_run(stubs)
    stubs.proposal_gate.repo_tokens[ACCEPTANCE] = []
    stubs.crew_executor.default_outcome = make_outcome(review=make_review())

    step = execute.handle(make_ports(stubs), run)

    assert stubs.log.count("RepoReader.exists") == 0
    assert isinstance(step, CloseIntent)


# --------------------------------------------------------------------------------------
# CrewSpec.repos（executor の repo 系道具の材料）
# --------------------------------------------------------------------------------------


def test_work_and_review_crew_specs_carry_the_repos(stubs: Stubs) -> None:
    stubs.repo_reader.existing = {"src/app.py", "tests/test_app.py"}
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())

    kickoff.run_issue(make_ports(stubs), _execute_run(stubs), "k-1")

    specs: list[CrewSpec] = [c.kwargs["spec"] for c in stubs.log.of("CrewExecutor.run")]
    assert len(specs) == 2
    assert all(spec.repos == REPOS for spec in specs)


# --------------------------------------------------------------------------------------
# 被覆検査
# --------------------------------------------------------------------------------------

PARENT = "repo/src/app.py と outcome/design.md を作る"
CHILD_CODE = "repo/src/app.py に実装する"
CHILD_DOC = "outcome/design.md を書く"


def _propose_run(stubs: Stubs, children: list[list[str]]) -> IssueRun:
    parent = make_issue("bd-1", labels=[project_label()], acceptance=PARENT)
    stubs.state.add(parent)
    stubs.proposal_gate.tokens.update(
        {PARENT: [f"{OUTCOME_PREFIX}design.md"], CHILD_DOC: [f"{OUTCOME_PREFIX}design.md"]}
    )
    stubs.proposal_gate.repo_tokens.update(
        {PARENT: [f"{REPO_PREFIX}src/app.py"], CHILD_CODE: [f"{REPO_PREFIX}src/app.py"]}
    )
    stubs.proposal_gate.default_result = make_gate_result(order=tuple(range(len(children))))
    return make_issue_run(
        issue=parent,
        spec=make_stage_spec(mode="propose", child_labels=["kind:child"]),
        outcome=make_outcome(proposal=make_proposal(len(children), acceptance=children)),
    )


def test_uncovered_repo_deliverable_is_a_retry(stubs: Stubs) -> None:
    step = propose.handle(make_ports(stubs), _propose_run(stubs, [[CHILD_DOC]]))

    assert isinstance(step, Retry)
    assert "repo/src/app.py" in step.feedback
    assert stubs.log.count("BoardWriter.create") == 0


def test_covered_repo_and_outcome_deliverables_proceed(stubs: Stubs) -> None:
    step = propose.handle(make_ports(stubs), _propose_run(stubs, [[CHILD_CODE], [CHILD_DOC]]))

    assert isinstance(step, CloseIntent)
    assert stubs.log.count("BoardWriter.create") == 2


def test_repo_and_outcome_with_the_same_name_do_not_cover_each_other(stubs: Stubs) -> None:
    """`repo/a.md` と `outcome/a.md` は別物（鍵を接頭辞込みにする理由）。"""
    parent_text = "repo/a.md を作る"
    child_text = "outcome/a.md を書く"
    parent = make_issue("bd-1", labels=[project_label()], acceptance=parent_text)
    stubs.state.add(parent)
    stubs.proposal_gate.repo_tokens[parent_text] = [f"{REPO_PREFIX}a.md"]
    stubs.proposal_gate.tokens[child_text] = [f"{OUTCOME_PREFIX}a.md"]
    stubs.proposal_gate.default_result = make_gate_result(order=(0,))
    run = make_issue_run(
        issue=parent,
        spec=make_stage_spec(mode="propose", child_labels=["kind:child"]),
        outcome=make_outcome(proposal=make_proposal(1, acceptance=[[child_text]])),
    )

    step = propose.handle(make_ports(stubs), run)

    assert isinstance(step, Retry)
    assert "repo/a.md" in step.feedback
