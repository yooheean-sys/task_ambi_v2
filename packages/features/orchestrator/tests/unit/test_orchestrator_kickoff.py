"""計測・実行・判定の受け（F07・F11）。US1 AC-4／AC-5・SC-015・SC-020・SC-029・SC-030。

正本は `specs/009-orchestrator/contracts/result-mapping.md` §2・§6。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest
from conftest import (
    Stubs,
    make_comment,
    make_gate_result,
    make_issue,
    make_issue_run,
    make_outcome,
    make_ports,
    make_project,
    make_proposal,
    make_review,
    make_stage_spec,
    make_verdict,
    project_label,
)

from common.interfaces.executor import CrewOutcome, CrewSpec, ExecutorFailure
from common.interfaces.monitor import Grade, MappingUnverified
from common.interfaces.settings import Project, ProjectRepos
from common.interfaces.stages import ContextRefs, PromptPurpose
from orchestrator.models import IssueDisposition, IssueRun
from orchestrator.service import kickoff

KICKOFF_ID = "k-1"
WRITES = (
    "BoardWriter.create",
    "BoardWriter.add_dependency",
    "BoardWriter.move_dependents",
    "BoardWriter.release",
    "BoardWriter.comment",
    "BoardWriter.close",
    "BoardWriter.set_labels",
)


def _execute_run(
    stubs: Stubs,
    *,
    acceptance: str = "",
    outcome: CrewOutcome | None = None,
    human_guidance: Sequence[str] = (),
    project: Project | None = None,
) -> IssueRun:
    """遂行の段（既定は 完了申告 → レビュー承認 → `CloseIntent`）の 1 課題。"""
    issue = make_issue("bd-1", labels=[project_label()], acceptance=acceptance)
    stubs.state.add(issue)
    stubs.proposal_gate.tokens[acceptance] = []
    stubs.crew_executor.default_outcome = (
        outcome if outcome is not None else make_outcome(completed=True, review=make_review())
    )
    return make_issue_run(
        issue=issue,
        spec=make_stage_spec(mode="execute"),
        human_guidance=human_guidance,
        project=project,
    )


def _propose_run(stubs: Stubs) -> IssueRun:
    issue = make_issue("bd-1", labels=[project_label()])
    stubs.state.add(issue)
    stubs.crew_executor.default_outcome = make_outcome(proposal=make_proposal(2))
    stubs.proposal_gate.default_result = make_gate_result(order=(0, 1))
    return make_issue_run(issue=issue, spec=make_stage_spec(mode="propose"))


def _writes_after(stubs: Stubs, target: str) -> list[str]:
    names = stubs.log.targets()
    index = names.index(target)
    return [name for name in names[index + 1 :] if name in WRITES]


# --------------------------------------------------------------------------------------
# 呼び出し順と締めの回数（US1 AC-4・SC-015・FR-033・FR-034a）
# --------------------------------------------------------------------------------------


def test_begin_prompt_run_step_finish_in_order(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert stubs.log.only(
        "RunMonitor.begin",
        "StageCatalog.build_prompt",
        "CrewExecutor.run",
        "RunMonitor.finish",
    ) == [
        "RunMonitor.begin",
        "StageCatalog.build_prompt",
        "CrewExecutor.run",
        "StageCatalog.build_prompt",  # レビュー用の組立（F09）
        "CrewExecutor.run",
        "RunMonitor.finish",
    ]


def test_begin_receives_the_limits_and_threshold(stubs: Stubs) -> None:
    ports = make_ports(stubs)
    run = _execute_run(stubs)

    kickoff.run_issue(ports, run, KICKOFF_ID)

    call = stubs.log.of("RunMonitor.begin")[0]
    assert stubs.log.count("RunMonitor.begin") == 1
    assert call.kwargs["kickoff_id"] == KICKOFF_ID
    assert call.kwargs["limits"] is run.limits
    assert call.kwargs["threshold_t"] == run.member.llm_profile.threshold_t


def test_finish_uses_the_system_actor(stubs: Stubs) -> None:
    """`finish` に渡す actor は `system_actor`（SD-32・B-38。`member.bd_actor` ではない）。"""
    ports = make_ports(stubs, system_actor="sysbot")

    kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert stubs.log.of("RunMonitor.finish")[0].kwargs["actor"] == "sysbot"


@pytest.mark.parametrize("scenario", ["成功", "差し戻し", "上限停止", "実行器の失敗"])
def test_finish_is_called_exactly_once_in_every_scenario(stubs: Stubs, scenario: str) -> None:
    ports = make_ports(stubs)
    run = _execute_run(stubs)
    if scenario == "実行器の失敗":
        stubs.crew_executor.failures.fail(
            "CrewExecutor.run", 1, ExecutorFailure("組立に失敗", phase="build", reason="boom")
        )
    elif scenario == "差し戻し":
        stubs.crew_executor.default_outcome = make_outcome(completed=False)
    elif scenario == "上限停止":
        stubs.crew_executor.default_outcome = make_outcome(halted=True)

    kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.count("RunMonitor.finish") == 1


def test_executor_failure_does_not_escape_and_reaches_finish(stubs: Stubs) -> None:
    """実行器の失敗は**呼び手へ漏らさず** `finish` に渡す（US1 AC-5・FR-039）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs)
    stubs.crew_executor.failures.fail(
        "CrewExecutor.run", 1, ExecutorFailure("組立に失敗", phase="build", reason="boom")
    )

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert result.stop is False
    passed = stubs.log.of("RunMonitor.finish")[0].kwargs["outcome"]
    assert passed.completed is False
    assert "boom" in passed.completion_reason


def test_halted_outcome_is_a_normal_result(stubs: Stubs) -> None:
    """上限到達は**例外ではなく正常な結果**として受ける（FR-039）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(halted=True))

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.of("RunMonitor.finish")[0].kwargs["outcome"].halted is True
    assert result.stop is False


# --------------------------------------------------------------------------------------
# 判定の受け（contracts §2・§6 の #1〜#4・SC-029）
# --------------------------------------------------------------------------------------


def test_grade_f_writes_nothing_and_reports_failing(stubs: Stubs) -> None:
    stubs.run_monitor.default_verdict = make_verdict(grade=Grade.F)
    ports = make_ports(stubs)

    result = kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert result.disposition is IssueDisposition.graded_failing
    assert _writes_after(stubs, "RunMonitor.finish") == []


def test_grade_w_with_closed_suppresses_our_close(stubs: Stubs) -> None:
    stubs.run_monitor.default_verdict = make_verdict(grade=Grade.W, closed=True)
    ports = make_ports(stubs)

    result = kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert result.disposition is IssueDisposition.closed_by_monitor
    assert stubs.log.count("BoardWriter.close") == 0


def test_grade_i_notes_the_envelope_and_executes_the_close_intent(stubs: Stubs) -> None:
    stubs.run_monitor.default_verdict = make_verdict(grade=Grade.I)
    ports = make_ports(stubs)

    result = kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert result.disposition is IssueDisposition.closed_by_self
    assert stubs.log.count("BoardWriter.close") == 1
    assert _writes_after(stubs, "RunMonitor.finish") == ["BoardWriter.close"]
    assert any("等級 I" in note for note in result.notes)


def test_close_is_read_back(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert stubs.log.only("BoardWriter.close", "BoardReader.show")[-2:] == [
        "BoardWriter.close",
        "BoardReader.show",
    ]


def test_mapping_unverified_halts_without_touching_the_board(
    stubs: Stubs, caplog: pytest.LogCaptureFixture
) -> None:
    stubs.run_monitor.failures.fail(
        "RunMonitor.finish",
        1,
        MappingUnverified("読み戻せない", issue_id="bd-1", expected="closed", actual="open"),
    )
    ports = make_ports(stubs)

    with caplog.at_level(logging.ERROR):
        result = kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert result.disposition is IssueDisposition.halted
    assert result.stop is True
    assert _writes_after(stubs, "RunMonitor.finish") == []
    assert "orchestrator.kickoff.mapping_unverified" in [
        record.getMessage() for record in caplog.records
    ]


def test_limit_halt_maps_to_halted_before_grade_f(stubs: Stubs) -> None:
    """上限停止は**等級 F より先**に「止まった」へ写す（写像表 行 7・contracts §2）。

    段別処理にも差し戻しにも進まないので、実行器は **1 回**・盤面へのコメントは **0 件**。
    """
    stubs.run_monitor.default_verdict = make_verdict(grade=Grade.F)
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(halted=True))

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert result.disposition is IssueDisposition.halted
    assert result.stop is False  # 上限は 1 課題あたり（FR-034a）なので次の課題へ進む
    assert stubs.log.count("CrewExecutor.run") == 1
    assert stubs.log.count("BoardWriter.comment") == 0
    assert _writes_after(stubs, "RunMonitor.finish") == []


def test_limit_halt_yields_to_the_close_by_monitor(stubs: Stubs) -> None:
    """等級 W で計測側が既に閉じていれば、上限停止より**盤面の事実**を採る（contracts §2）。"""
    stubs.run_monitor.default_verdict = make_verdict(grade=Grade.W, closed=True)
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(halted=True))

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert result.disposition is IssueDisposition.closed_by_monitor
    assert stubs.log.count("BoardWriter.close") == 0


def test_close_uses_the_member_actor_not_the_system_actor(stubs: Stubs) -> None:
    """`close` は **AI の行為**なのでメンバの `bd_actor` で書く（SC-028・P5D-037）。

    SC-028 が挙げる「AI の行為」は**起票と close の 2 つ**だが、起票側しか actor を測って
    おらず、`close` を `system_actor` へ潰しても緑だった（変異実測）。**2 つの actor を
    別値にした台本**で、取り違えが判別できる形にする。
    """
    ports = make_ports(stubs, system_actor="sysbot")
    run = _execute_run(stubs)

    kickoff.run_issue(ports, run, KICKOFF_ID)

    actor: str = stubs.log.of("BoardWriter.close")[0].kwargs["actor"]
    assert actor == run.member.bd_actor == "ai-planner"
    assert actor != "sysbot"


def test_a_close_that_does_not_take_effect_halts_instead_of_reporting_closed(
    stubs: Stubs, caplog: pytest.LogCaptureFixture
) -> None:
    """読み直して閉じていなければ `closed` と報告しない（D04-b・FR-070）。

    `BoardWriter.close` は `BoardWriteUnverified` を**宣言しない**ので、確認読取の結果が
    唯一の検証手段である。戻りを捨てていたときは、盤面が開いたままでも `closed_by_self`
    を返していた（2026-08-26 実測の欠陥）。
    """
    stubs.board_writer.close_takes_effect = False  # 例外は出ないが閉じない
    ports = make_ports(stubs)

    with caplog.at_level(logging.ERROR):
        result = kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert stubs.log.count("BoardWriter.close") == 1
    assert stubs.log.count("BoardReader.show") >= 1  # 読み直しは行われている
    assert result.disposition is IssueDisposition.halted
    assert result.stop is True
    assert "orchestrator.kickoff.close_unverified" in [
        record.getMessage() for record in caplog.records
    ]


def test_close_failure_halts_and_leaves_the_board_to_the_next_kickoff(
    stubs: Stubs, caplog: pytest.LogCaptureFixture
) -> None:
    """`close` が落ちたら**盤面には手を触れず**打ち切る（FR-070・SC-017）。

    SC-017 が挙げる失敗点は「起票・依存張り・付け替え・**親を閉じる**」の 4 つだが、
    `test_orchestrator_propose.py` の 4 点は `create`／`add_dependency`／`move_dependents`／
    確認読取であり、**`close` は含まない** —— plan C-1 が `close` を締めの後（`kickoff.py`）へ
    移したためである。本 feature の分解の段は `CloseIntent` を返すに留まるので、`close` の
    失敗分岐はこの層でしか通らない。
    """
    stubs.board_writer.failures.fail("BoardWriter.close", 1)  # 既定は `BoardUnavailable`
    ports = make_ports(stubs)

    with caplog.at_level(logging.ERROR):
        result = kickoff.run_issue(ports, _execute_run(stubs), KICKOFF_ID)

    assert result.disposition is IssueDisposition.halted
    assert result.stop is True
    assert stubs.log.count("BoardWriter.comment") == 0  # 盤面に手を触れない（FR-070）
    assert stubs.log.count("BoardWriter.release") == 0
    assert "orchestrator.kickoff.close_failed" in [record.getMessage() for record in caplog.records]


def test_review_rejection_never_closes_on_the_same_surface(stubs: Stubs) -> None:
    """レビュー却下で `close` は **0 回**（SC-024。承認側の 1 回と**同じ面**で測る）。

    `execute.handle` を直接叩く層では、承認でも却下でも `close` は構造上 0 回（plan C-1）
    なので `assert close == 0` が恒真になる。1 回／0 回の対比はこの層でしか取れない。
    """
    ports = make_ports(stubs)
    rejected = make_review(approved=False, feedback="不足")
    run = _execute_run(stubs, outcome=make_outcome(completed=True, review=rejected))

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.count("BoardWriter.close") == 0
    assert stubs.log.count("CrewExecutor.run") == 6  # 作業 3 ＋ レビュー 3（SC-020a）
    assert result.disposition is IssueDisposition.handed_off
    assert result.returns == 2


# --------------------------------------------------------------------------------------
# 差し戻しのループ（FR-041・FR-042・SC-020・SC-020a）
# --------------------------------------------------------------------------------------


def test_default_limit_runs_the_executor_three_times_with_two_comments(stubs: Stubs) -> None:
    """既定 `max_returns=2` で実行器 3 回・差し戻しコメント 2 件（SC-020a）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(completed=False))

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.count("CrewExecutor.run") == 3
    returns = [
        call
        for call in stubs.log.of("BoardWriter.comment")
        if call.kwargs["body"].startswith("[orchestrator] 差し戻し")
    ]
    assert len(returns) == 2
    assert returns[0].kwargs["body"].startswith("[orchestrator] 差し戻し（1/2 回目）")
    assert returns[1].kwargs["body"].startswith("[orchestrator] 差し戻し（2/2 回目）")
    assert result.disposition is IssueDisposition.handed_off
    assert result.returns == 2


def test_no_executor_call_after_the_limit(stubs: Stubs) -> None:
    """上限に達したあと実行器の呼び出しが 0 回で人間へ渡る（SC-020）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(completed=False))

    kickoff.run_issue(ports, run, KICKOFF_ID)

    names = stubs.log.targets()
    handoff_at = next(
        i
        for i, call in enumerate(stubs.log.calls)
        if call.target == "BoardWriter.comment" and "停止[returns_exceeded]" in call.kwargs["body"]
    )
    assert "CrewExecutor.run" not in names[handoff_at:]


def test_return_count_is_re_read_from_the_board(stubs: Stubs) -> None:
    """差し戻しのたびに `comments` を引き直す（C-5・SC-019）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(completed=False))

    kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.count("BoardReader.comments") == 3


def test_existing_return_comments_count_toward_the_limit(stubs: Stubs) -> None:
    """再起動を跨いでも盤面のコメントで数える（SC-019）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(completed=False))
    stubs.state.comments["bd-1"] = [
        make_comment("bd-1", "system", "[orchestrator] 差し戻し（1/2 回目）[分類]: 既存", "c-a"),
        make_comment("bd-1", "system", "[orchestrator] 差し戻し（2/2 回目）[分類]: 既存", "c-b"),
    ]

    result = kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.count("CrewExecutor.run") == 1
    assert result.disposition is IssueDisposition.handed_off


def test_retry_reruns_within_the_same_kickoff(stubs: Stubs) -> None:
    """差し戻しの再実行は**同じ kickoff の中**（P5D-021。`begin` は増えない）。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs, outcome=make_outcome(completed=False))

    kickoff.run_issue(ports, run, KICKOFF_ID)

    assert stubs.log.count("RunMonitor.begin") == 1
    assert stubs.log.count("RunMonitor.finish") == 1
    feedbacks = [call.kwargs["feedback"] for call in stubs.log.of("StageCatalog.build_prompt")]
    assert feedbacks[0] is None
    assert feedbacks[1] is not None


# --------------------------------------------------------------------------------------
# ContextRefs の詰め方（FR-035・DIFF-18）
# --------------------------------------------------------------------------------------


def test_context_refs_for_the_work_run(stubs: Stubs) -> None:
    ports = make_ports(stubs)
    run = _execute_run(stubs, acceptance="受入条文")

    kickoff.run_issue(ports, run, KICKOFF_ID)

    ctx: ContextRefs = stubs.log.of("StageCatalog.build_prompt")[0].kwargs["ctx"]
    assert ctx.issue_id == "bd-1"
    assert ctx.acceptance == "受入条文"
    assert ctx.purpose is PromptPurpose.work
    assert ctx.input_refs == []  # 常に空（DIFF-18）
    assert ctx.existing_children == []
    assert ctx.parent_title is None


def test_context_refs_for_the_propose_run_lists_existing_children(stubs: Stubs) -> None:
    ports = make_ports(stubs)
    run = _propose_run(stubs)
    stubs.state.children["bd-1"] = []

    kickoff.run_issue(ports, run, KICKOFF_ID)

    ctx: ContextRefs = stubs.log.of("StageCatalog.build_prompt")[0].kwargs["ctx"]
    assert ctx.existing_children == []
    spec: CrewSpec = stubs.log.of("CrewExecutor.run")[0].kwargs["spec"]
    assert spec.mode == "propose"
    assert spec.purpose is PromptPurpose.work


def test_parent_acceptance_is_carried(stubs: Stubs) -> None:
    """親の条文・Hook・フォルダが `ContextRefs` と **`CrewSpec` の両方**に渡る（FR-036）。"""
    stubs.state.add(make_issue("bd-0", title="親", acceptance="親の条文"))
    issue = make_issue("bd-1", labels=[project_label()], parent="bd-0")
    stubs.state.add(issue)
    stubs.proposal_gate.tokens[""] = []
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())
    ports = make_ports(stubs)
    run = make_issue_run(issue=issue, spec=make_stage_spec(mode="execute"))

    kickoff.run_issue(ports, run, KICKOFF_ID)

    ctx: ContextRefs = stubs.log.of("StageCatalog.build_prompt")[0].kwargs["ctx"]
    assert ctx.parent_title == "親"
    assert ctx.parent_acceptance == "親の条文"
    spec: CrewSpec = stubs.log.of("CrewExecutor.run")[0].kwargs["spec"]
    assert spec.parent_acceptance == "親の条文"
    assert spec.hooks == run.hooks
    assert spec.folders == run.project.folders
    assert spec.member == run.member


def test_human_guidance_reaches_both_prompts(stubs: Stubs) -> None:
    """人間の指示が**作業とレビューの両方**の組立へ渡る（FR-035。F06 → F07・F09 の接続）。

    `IssueRun.human_guidance` の中身までしか見ていないと、「集めてはいるがプロンプトに
    載せない」退行が機械的に検出されない（`human_guidance=[]` に変えても緑になる）。
    """
    ports = make_ports(stubs)
    guidance = ("先に方針を決めて", "外部 API は使わない")
    run = _execute_run(stubs, human_guidance=guidance)

    kickoff.run_issue(ports, run, KICKOFF_ID)

    prompts = stubs.log.of("StageCatalog.build_prompt")
    work: ContextRefs = prompts[0].kwargs["ctx"]
    review: ContextRefs = prompts[1].kwargs["ctx"]
    assert work.human_guidance == list(guidance)
    assert review.human_guidance == list(guidance)


def test_project_repos_reach_both_prompts(stubs: Stubs) -> None:
    """CFG-74・D24: プロジェクトのリポジトリ対応付けが**作業とレビューの両方**の組立へ渡る。

    コード編纂の段で「リポジトリはここである」を AI に教える注入経路の検証。
    未登録なら `None` のまま渡り、stages が空文字で埋める（FR-013 踏襲）。
    """
    ports = make_ports(stubs)
    repos = ProjectRepos(
        remote_url="https://gitlab.example.com/g/r.git", local_path="/home/demo/work/r"
    )
    run = _execute_run(stubs, project=make_project(repos=repos))

    kickoff.run_issue(ports, run, KICKOFF_ID)

    prompts = stubs.log.of("StageCatalog.build_prompt")
    work: ContextRefs = prompts[0].kwargs["ctx"]
    review: ContextRefs = prompts[1].kwargs["ctx"]
    assert work.repository_remote_url == "https://gitlab.example.com/g/r.git"
    assert work.repository_local_path == "/home/demo/work/r"
    assert review.repository_remote_url == "https://gitlab.example.com/g/r.git"
    assert review.repository_local_path == "/home/demo/work/r"


def test_project_without_repos_passes_none(stubs: Stubs) -> None:
    """CFG-74・運用要件: リポジトリ未登録のプロジェクトは `None` のまま渡る。"""
    ports = make_ports(stubs)
    run = _execute_run(stubs)

    kickoff.run_issue(ports, run, KICKOFF_ID)

    prompts = stubs.log.of("StageCatalog.build_prompt")
    work: ContextRefs = prompts[0].kwargs["ctx"]
    assert work.repository_remote_url is None
    assert work.repository_local_path is None
