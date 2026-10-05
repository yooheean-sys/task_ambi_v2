"""F07・F11 — 計測開始 → 組立 → 実行器 → 段別処理 → 締め → 判定の解釈。

**`begin` と `finish` を 1 課題につき 1 対にする**（C-3）。`try/finally` を 1 ファイルに閉じる
ことで「締めは成功・失敗を問わず必ず 1 回」（FR-033・SC-015）を構造で守る。したがって
**上限（要求回数・費用）は「1 課題あたり」に効く**（FR-034a・DIFF-20）。2 件目以降の課題で
計測側が二重開始を記録する（`monitor.begin.superseded`）のは**正常**である。

**`close` は締めの後に 1 回だけ**（C-1）。段別処理は `CloseIntent` を返すに留め、等級 W で
計測側が既に閉じていれば**抑止する**（FR-068）。閉じたら**読み直した状態まで確かめる**
（D04-b。`BoardWriter.close` は `BoardWriteUnverified` を宣言しないので、読み直しが唯一の
検証手段である）。

**差し戻しの再実行は同じ kickoff の中**（FR-041・P5D-021）。回数は**判定のたびに盤面の
コメントを引き直して**数える（C-5・SC-019）。

**上限停止（`CrewOutcome.halted`）は実行器を呼び直さずに打ち切り**、等級 F より先に
「止まった」へ写す（FR-071 写像表 行 7・contracts/result-mapping.md §2）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from common.interfaces.board import BoardUnavailable, IssueNotFound, IssueStatus
from common.interfaces.executor import CrewOutcome, CrewSpec, ExecutorFailure
from common.interfaces.monitor import Grade, MappingUnverified
from common.interfaces.stages import ContextRefs, PromptPurpose, UnknownPersona
from common.kernel.log import LoggerFactory
from orchestrator.models import (
    CloseIntent,
    IssueDisposition,
    Retry,
    Settled,
    StepResult,
    count_return_comments,
    return_comment,
)
from orchestrator.service import execute, handoff, propose, writes

if TYPE_CHECKING:
    from common.interfaces.board import Issue
    from common.interfaces.monitor import RunVerdict
    from orchestrator.models import IssueRun, Ports

_logger = LoggerFactory.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IssueOutcome:
    """1 課題の処理が終わった記録（周回が要約と打ち切りの判断に使う）。"""

    disposition: IssueDisposition
    grade: Grade | None
    returns: int
    notes: tuple[str, ...]
    stop: bool
    """周回を打ち切るか（写像不能・盤面不達）。"""


def run_issue(ports: Ports, run: IssueRun, kickoff_id: str) -> IssueOutcome:
    """1 課題を実行し、判定を受けて分岐する（F07・F11）。

    Args:
        ports: 10 契約と自分の設定。
        run: 前処理で組んだ 1 課題の処理状態。
        kickoff_id: この起動の識別子（計測の帰属先）。

    Returns:
        処理の分岐・等級・差し戻し回数・付記・打ち切りの要否。
    """
    deps = ports.deps
    notes: list[str] = []
    step: StepResult | None = None
    outcome: CrewOutcome | None = None
    returns = run.returns
    board_failed = False
    verdict: RunVerdict | None = None

    deps.run_monitor.begin(kickoff_id, run.limits, run.member.llm_profile.threshold_t)
    try:
        outcome, step, returns = _drive(ports, run)
    except ExecutorFailure as exc:  # 呼び手へ漏らさず締めに渡す（FR-039）
        outcome = _failure_outcome(f"ExecutorFailure({exc.phase}): {exc.reason}")
    except UnknownPersona as exc:  # 段が許すペルソナに当てはまらない → F12（DIFF-12 の裁定）
        step = Settled(
            handoff.hand_off(
                ports, run.issue.id, handoff.config_missing("UnknownPersona", str(exc))
            )
        )
        outcome = _failure_outcome(f"UnknownPersona: {exc}")
    except (BoardUnavailable, IssueNotFound) as exc:
        board_failed = True
        _logger.error(
            "orchestrator.kickoff.board_unavailable",
            extra={"kickoff_id": kickoff_id, "issue_id": run.issue.id},
            exc_info=exc,
        )
    finally:
        try:
            verdict = deps.run_monitor.finish(
                kickoff_id,
                run.issue.id,
                ports.config.system_actor,
                outcome if outcome is not None else _failure_outcome("盤面に届かず打ち切った"),
            )
        except MappingUnverified as exc:
            _logger.error(
                "orchestrator.kickoff.mapping_unverified",
                extra={
                    "kickoff_id": kickoff_id,
                    "issue_id": exc.issue_id,
                    "expected": exc.expected,
                    "actual": exc.actual,
                },
                exc_info=exc,
            )

    return _interpret(
        ports,
        run,
        step,
        verdict,
        returns,
        notes,
        board_failed=board_failed,
        halted=outcome is not None and outcome.halted,
    )


# --------------------------------------------------------------------------------------
# F07 — 組立 → 実行器 → 段別処理 → 差し戻しのループ
# --------------------------------------------------------------------------------------


def _drive(ports: Ports, run: IssueRun) -> tuple[CrewOutcome, StepResult | None, int]:
    """作業の実行と段別処理を、差し戻しの上限まで回す。

    **実行器が上限で止まったら段別処理にも差し戻しにも進まない**（FR-039・FR-071 行 7）——
    `begin`／`finish` が 1 課題につき 1 対（FR-034a）である以上、上限は**その課題ぶんを既に
    使い切って**おり、呼び直しても超過したまま走るだけである。段の戻りを `None` にして
    締めへ抜ける。

    Args:
        ports: 10 契約と自分の設定。
        run: 1 課題の処理状態。

    Returns:
        最後の実行結果・段の戻り（**上限停止なら `None`**）・実測した差し戻し回数。
    """
    outcome = _run_executor(ports, run, None)
    current = replace(run, outcome=outcome)
    while True:
        if outcome.halted:  # 上限停止 → 実行器を呼び直さない（写像表 行 7）
            return outcome, None, current.returns
        step = (
            propose.handle(ports, current)
            if current.spec.mode == "propose"
            else execute.handle(ports, current)
        )
        if not isinstance(step, Retry):
            return outcome, step, current.returns

        measured = count_return_comments(
            ports.deps.board_reader.comments(run.issue.id),
            ports.config.comment_marker,
            ports.config.system_actor,
        )
        limit = ports.config.max_returns
        if measured >= limit:  # **語のとおりの勘定**（FR-042・SC-020a）
            disposition = handoff.hand_off(
                ports, run.issue.id, handoff.returns_exceeded(measured, limit, step.feedback)
            )
            return outcome, Settled(disposition), measured

        ports.deps.board_writer.comment(
            run.issue.id,
            return_comment(
                ports.config.comment_marker, measured + 1, limit, step.category, step.feedback
            ),
            ports.config.system_actor,
        )
        outcome = _run_executor(ports, current, step.feedback)
        current = replace(current, outcome=outcome, returns=measured + 1)


def _run_executor(ports: Ports, run: IssueRun, feedback: str | None) -> CrewOutcome:
    """プロンプトを組んで実行器を 1 回呼ぶ（`purpose=work`）。"""
    parent = _parent_of(ports, run.issue)
    bundle = ports.deps.stage_catalog.build_prompt(
        run.spec,
        run.member,
        run.rules,
        run.templates,
        _context_refs(ports, run, parent),
        feedback,
    )
    return ports.deps.crew_executor.run(
        CrewSpec(
            issue_id=run.issue.id,
            mode=run.spec.mode,
            purpose=PromptPurpose.work,
            bundle=bundle,
            member=run.member,
            hooks=run.hooks,
            folders=run.project.folders,
            parent_acceptance=parent.acceptance if parent is not None else None,
            repos=run.project.repos,
        )
    )


def _context_refs(ports: Ports, run: IssueRun, parent: Issue | None) -> ContextRefs:
    """`build_prompt` の材料を詰める（FR-035・DIFF-18）。"""
    children = (
        [child.title for child in ports.deps.board_reader.children_of(run.issue.id)]
        if run.spec.mode == "propose"
        else []
    )
    parent_acceptance = parent.acceptance if parent is not None else None
    repos = run.project.repos  # CFG-74・D24: 未登録なら None（→ 空文字で埋まる）
    return ContextRefs(
        issue_id=run.issue.id,
        title=run.issue.title,
        description=run.issue.description,
        acceptance=run.issue.acceptance,
        parent_title=parent.title if parent is not None else None,
        parent_acceptance=parent_acceptance,
        human_guidance=list(run.human_guidance),
        input_refs=[],  # orchestrator は入力を列挙しない（常に空＝ DIFF-18）
        existing_children=children,
        purpose=PromptPurpose.work,
        is_final_review=bool(parent_acceptance),  # D16-a
        repository_remote_url=repos.remote_url if repos is not None else None,
        repository_local_path=repos.local_path if repos is not None else None,
    )


def _parent_of(ports: Ports, issue: Issue) -> Issue | None:
    """親課題を読む（無ければ `None`）。**キャッシュしない**（FR-088）。"""
    if issue.parent is None:
        return None
    return ports.deps.board_reader.show(issue.parent)


def _failure_outcome(reason: str) -> CrewOutcome:
    """締めに渡すための最小の実行結果（実行器の失敗・盤面不達）。"""
    return CrewOutcome(
        proposal=None,
        task_output=None,
        completed=False,
        completion_reason=reason,
        review=None,
        escalation=None,
        written=[],
        events=[],
        halted=False,
        halted_reason=None,
        progress_notes=0,
    )


# --------------------------------------------------------------------------------------
# F11 — 判定の受け（contracts/result-mapping.md §2）
# --------------------------------------------------------------------------------------


def _interpret(
    ports: Ports,
    run: IssueRun,
    step: StepResult | None,
    verdict: RunVerdict | None,
    returns: int,
    notes: list[str],
    *,
    board_failed: bool,
    halted: bool,
) -> IssueOutcome:
    """締めの戻りを解釈し、`close` の実行と抑止を決める。

    分岐の順は `contracts/result-mapping.md` §2 が正本。**上限停止は等級 F より先に見る**
    —— 既定の `grade_map` は `limit_requests`・`limit_cost` を F へ写すため（monitor の
    `config.json`）、F を先に見ると写像表 行 7 の「上限停止」入口が**到達不能**になる。
    ただし**等級 W で計測側が既に閉じていれば `closed` を優先**する（盤面の事実が正。
    等級は 1 つしか立たないので F と W の判定順は振る舞いに影響しない）。
    """
    if verdict is None:  # 写像を確かめられなかった（FR-070・SC-030）
        return IssueOutcome(IssueDisposition.halted, None, returns, tuple(notes), stop=True)
    if board_failed:
        return IssueOutcome(
            IssueDisposition.halted, verdict.grade, returns, tuple(notes), stop=True
        )
    if isinstance(step, Settled) and step.disposition is IssueDisposition.halted:
        return IssueOutcome(
            IssueDisposition.halted, verdict.grade, returns, tuple(notes), stop=True
        )
    if verdict.grade is Grade.W and verdict.closed:  # 自分の close を抑止（FR-068）
        return IssueOutcome(
            IssueDisposition.closed_by_monitor, verdict.grade, returns, tuple(notes), stop=False
        )
    if verdict.grade is None or verdict.grade is Grade.I:  # 盤面には書かない（FR-069）
        notes.append(f"{run.issue.id}: 等級 I: {verdict.envelope}")
    if halted:  # 上限で実行が止まった（写像表 行 7）。**打ち切らない**＝上限は 1 課題あたり
        return IssueOutcome(
            IssueDisposition.halted, verdict.grade, returns, tuple(notes), stop=False
        )
    if verdict.grade is Grade.F:  # 自分では何も書かない（monitor が済ませている）
        return IssueOutcome(
            IssueDisposition.graded_failing, verdict.grade, returns, tuple(notes), stop=False
        )
    if isinstance(step, Settled):
        return IssueOutcome(step.disposition, verdict.grade, returns, tuple(notes), stop=False)
    if isinstance(step, CloseIntent):
        return _close(ports, run, step, verdict, returns, notes)
    return IssueOutcome(IssueDisposition.halted, verdict.grade, returns, tuple(notes), stop=False)


def _close(
    ports: Ports,
    run: IssueRun,
    intent: CloseIntent,
    verdict: RunVerdict,
    returns: int,
    notes: list[str],
) -> IssueOutcome:
    """抑止されなかった `CloseIntent` を実行し、**確認読取の結果まで見る**（D04-b）。

    `BoardWriter.close` は `BoardWriteUnverified` を**宣言しない**（`common/interfaces/board.py`）
    ので、閉じたことを確かめる手段は `writes.close_issue` が返す読み直しの結果**だけ**である。
    戻りを捨てると読み直しは `IssueNotFound` の探りにしかならず、**盤面が閉じていないのに
    `closed` と報告する**（2026-08-26 実測の欠陥）。

    閉じられていなければ close の失敗と同じ扱いにする —— 盤面には手を触れず、停止痕跡を
    残して周回を打ち切り、次の起動の拾い直しに委ねる（FR-070）。
    """
    try:
        closed = writes.close_issue(ports, run.issue.id, intent.reason, intent.actor)
    except (BoardUnavailable, IssueNotFound) as exc:
        _logger.error(
            "orchestrator.kickoff.close_failed",
            extra={"issue_id": run.issue.id},
            exc_info=exc,
        )
        return IssueOutcome(
            IssueDisposition.halted, verdict.grade, returns, tuple(notes), stop=True
        )
    if closed.status is not IssueStatus.closed:  # 確認読取が閉じたことを示さない（D04-b）
        _logger.error(
            "orchestrator.kickoff.close_unverified",
            extra={
                "issue_id": run.issue.id,
                "expected": IssueStatus.closed.value,
                "actual": closed.status.value,
            },
        )
        return IssueOutcome(
            IssueDisposition.halted, verdict.grade, returns, tuple(notes), stop=True
        )
    return IssueOutcome(
        IssueDisposition.closed_by_self, verdict.grade, returns, tuple(notes), stop=False
    )
