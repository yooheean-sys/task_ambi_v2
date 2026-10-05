"""F01・F02・F04・F14 — 周回の骨格・single-flight・確保・記録の保持。

`OrchestratorService.run` が `KickoffRunner`（IF-49）そのものである。呼び手へ送出してよい
契約例外は **`KickoffBusy` の 1 種だけ**（FR-003）で、利用先の契約例外 11 種はここまでに
すべて受け止める。

**ロックは 100% 解放する**（FR-008・SC-005）—— 周回が例外で終わっても `finally` で
`SingleFlight.end()` を通す。**断られた起動では識別子を発番せず記録も残さない**
（FR-012a・SC-006a。発番は `SingleFlight.start()` の内側で行う）。
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from common.interfaces.board import BoardUnavailable, IssueNotFound
from common.interfaces.orchestrator import (
    IssueRunSummary,
    KickoffBusy,
    KickoffResult,
    KickoffStatus,
)
from common.kernel.enums import Labels, StateLabel
from common.kernel.log import CorrelationContext, LoggerFactory
from orchestrator.models import (
    RESULT_OF,
    IssueDisposition,
    IssueRun,
    KickoffRecord,
    RecordStore,
    SingleFlight,
)
from orchestrator.service import kickoff, prepare, recover
from orchestrator.service.prepare import PrepareFailure

if TYPE_CHECKING:
    from common.interfaces.board import Issue
    from common.interfaces.monitor import Grade
    from common.interfaces.orchestrator import ExecutionContext
    from orchestrator.models import Ports

_logger = LoggerFactory.get_logger(__name__)

_HANDED_OFF = frozenset({IssueDisposition.handed_off, IssueDisposition.handed_off_unsafe})
"""「手放した課題」に数える分岐（FR-022b。回収由来は含めない）。"""


def new_kickoff_id() -> str:
    """Kickoff の識別子を発番する（FR-077a。土台の発番関数は層契約により import できない）。"""
    return uuid.uuid4().hex


class OrchestratorService:
    """1 kickoff の周回（`KickoffRunner` の実装）。"""

    def __init__(self, *, ports: Ports, flight: SingleFlight, records: RecordStore) -> None:
        """10 契約と設定・進行中の印・直近 N 件の記録を受け取る。

        Args:
            ports: 10 契約と自分の設定。
            flight: 両入口で共有する「進行中の印」（FR-006）。
            records: 直近 N 件の記録。
        """
        self._ports = ports
        self._flight = flight
        self._records = records

    # -- IF-49 ---------------------------------------------------------------------

    def run(self, ctx: ExecutionContext) -> KickoffResult:
        """実行文脈を受けて kickoff を 1 回まわす（F01）。

        Args:
            ctx: 1 回の kickoff の実行文脈。

        Returns:
            処理した課題の一覧と状態。

        Raises:
            KickoffBusy: 別の kickoff が実行中（**呼び手へ出す唯一の契約例外**）。
        """
        token = self._flight.start(new_kickoff_id)
        if not token.acquired:
            msg = "別の kickoff が実行中"
            raise KickoffBusy(msg, current_kickoff_id=token.kickoff_id)
        return self._cycle(ctx, token.kickoff_id)

    # -- F13 の段の並び 3（受付口。single-flight の取得と記録の登録と不可分） -------------

    def start_background(self, ctx: ExecutionContext, correlation_id: str | None) -> str:
        """周回を別スレッドで起動し、**待たずに**識別子を返す（FR-073・contracts §2）。

        single-flight の取得・記録の登録・ワーカースレッドの起動を**不可分**に行う。
        相関 ID は**入口で決まっている値を引き継ぐ**（FR-077・DIFF-02）—— 現在値が無ければ
        束縛しない（発番しない）。

        Args:
            ctx: 1 回の kickoff の実行文脈（口が組む）。
            correlation_id: 入口の相関 ID（無ければ `None`）。

        Returns:
            発番された kickoff の識別子。

        Raises:
            KickoffBusy: 別の kickoff が実行中（口が 409 に翻訳する）。
        """
        token = self._flight.start(new_kickoff_id)
        if not token.acquired:
            msg = "別の kickoff が実行中"
            raise KickoffBusy(msg, current_kickoff_id=token.kickoff_id)
        kickoff_id = token.kickoff_id
        self._records.open(
            KickoffRecord(kickoff_id=kickoff_id, ctx=ctx, status="running", result=None)
        )

        def _worker() -> None:
            bound = CorrelationContext.bind(correlation_id) if correlation_id is not None else None
            try:
                self._drive_background(ctx, kickoff_id)
            finally:
                if bound is not None:
                    CorrelationContext.reset(bound)

        threading.Thread(target=_worker, name=f"orchestrator-{kickoff_id}", daemon=True).start()
        return kickoff_id

    def _drive_background(self, ctx: ExecutionContext, kickoff_id: str) -> None:
        """ワーカースレッドの本体。**想定外の例外も握り潰さない**（`exc_info` つきで残す）。"""
        try:
            self._cycle_body(ctx, kickoff_id)
        except Exception:
            # 想定外の例外も握り潰さず `exc_info` つきで残す（契約 §6）。スレッドの外へ
            # 出る先が無いので、ここで再送出しても誰も受け取れない。
            _logger.exception("orchestrator.worker.unexpected", extra={"kickoff_id": kickoff_id})

    # -- 記録の参照（router が使う） --------------------------------------------------

    def record(self, kickoff_id: str) -> KickoffRecord | None:
        """識別子で記録を引く（知らない・こぼれた識別子は `None`＝ FR-075）。"""
        return self._records.get(kickoff_id)

    def current_record(self) -> KickoffRecord | None:
        """走っている記録（無ければ `None`＝ FR-076）。"""
        return self._records.current()

    # -- 周回の本体 -------------------------------------------------------------------

    def _cycle(self, ctx: ExecutionContext, kickoff_id: str) -> KickoffResult:
        """記録を登録してから周回する（`run` の入口）。"""
        self._records.open(
            KickoffRecord(kickoff_id=kickoff_id, ctx=ctx, status="running", result=None)
        )
        return self._cycle_body(ctx, kickoff_id)

    def _cycle_body(self, ctx: ExecutionContext, kickoff_id: str) -> KickoffResult:
        """周回の本体を 1 本の `try/finally` で囲む（FR-008・FR-012）。"""
        started_at = _now()
        issues: list[IssueRunSummary] = []
        notes: list[str] = []
        status = KickoffStatus.stopped
        try:
            stopped = self._walk(ctx, kickoff_id, issues, notes)
            status = (
                KickoffStatus.stopped
                if stopped
                else (KickoffStatus.ok if issues else KickoffStatus.noop)
            )
        finally:
            result = KickoffResult(
                kickoff_id=kickoff_id,
                status=status,
                issues=list(issues),
                notes=list(notes),
                started_at=started_at,
                finished_at=_now(),  # 共有側で必須なので必ず詰める（DIFF-11）
            )
            self._records.complete(kickoff_id, result)
            self._flight.end()
        return result

    def _walk(
        self,
        ctx: ExecutionContext,
        kickoff_id: str,
        issues: list[IssueRunSummary],
        notes: list[str],
    ) -> bool:
        """拾い直し → 確保の周回。打ち切ったら `True` を返す。"""
        report = recover.recover_orphans(self._ports, ctx.claim_filter)
        notes.extend(report.notes)
        if report.stopped:  # 拾い直しを省いて先へ進まない（FR-017・SC-009）
            return True

        released: set[str] = set()
        while True:
            try:
                issue = self._ports.deps.board_writer.claim_next(
                    ctx.claim_filter, self._ports.config.system_actor
                )
                if issue is None:  # 今は対象が無い（失敗として扱わない＝ FR-020）
                    return False
                if issue.id in released:  # 一度手放した課題の再確保（FR-022a）
                    self._release(issue.id)
                    notes.append(
                        f"skipped {issue.id}: "
                        "同じ起動で一度手放した課題を再確保したため解放して周回を終えた"
                    )
                    return False
                if _is_failing(issue):  # 失敗の印つき（FR-021・SC-010）
                    self._release(issue.id)
                    released.add(issue.id)
                    notes.append(f"skipped {issue.id}: state:failing のため触れずに解放した")
                    continue
            except (BoardUnavailable, IssueNotFound) as exc:
                _logger.error(
                    "orchestrator.loop.board_unavailable",
                    extra={"kickoff_id": kickoff_id},
                    exc_info=exc,
                )
                return True
            if self._process(issue, kickoff_id, issues, notes, released):
                return True

    def _process(
        self,
        issue: Issue,
        kickoff_id: str,
        issues: list[IssueRunSummary],
        notes: list[str],
        released: set[str],
    ) -> bool:
        """1 課題を前処理 → 実行 → 判定まで通す。打ち切るべきなら `True`。"""
        try:
            prepared = prepare.prepare_issue(self._ports, issue.id)
        except (BoardUnavailable, IssueNotFound) as exc:
            _logger.error(
                "orchestrator.loop.board_unavailable",
                extra={"kickoff_id": kickoff_id, "issue_id": issue.id},
                exc_info=exc,
            )
            notes.append(f"skipped {issue.id}: 盤面に届かず周回を打ち切った")
            return True
        if isinstance(prepared, PrepareFailure):
            released.add(issue.id)  # 人間へ渡した（FR-022b）
            _record_prepare_failure(issue.id, prepared, issues, notes)
            return prepared.disposition is IssueDisposition.halted

        outcome = kickoff.run_issue(self._ports, prepared, kickoff_id)
        notes.extend(outcome.notes)
        issues.append(_summary(prepared, outcome.disposition, outcome.grade, outcome.returns))
        if outcome.disposition in _HANDED_OFF:
            released.add(issue.id)  # 人間へ渡した（FR-022b）
        return outcome.stop

    def _release(self, issue_id: str) -> None:
        """確保を解放する（自分の行為＝システム actor）。"""
        self._ports.deps.board_writer.release(issue_id, self._ports.config.system_actor)


def _record_prepare_failure(
    issue_id: str,
    failure: PrepareFailure,
    issues: list[IssueRunSummary],
    notes: list[str],
) -> None:
    """前処理で人間へ渡した課題を一覧か `notes` に落とす。

    段・主体が未解決だと `IssueRunSummary` を組めない（`stage`・`persona` が省略不可）ので、
    その場合は `notes` に 1 行残す（FR-071a と同じ構造的理由）。
    """
    if failure.stage is None or failure.persona is None:
        notes.append(f"skipped {issue_id}: 段・主体が決まらないまま人間へ渡した")
        return
    issues.append(
        IssueRunSummary(
            issue_id=issue_id,
            stage=failure.stage,
            persona=failure.persona,
            result=RESULT_OF[failure.disposition],
            grade=None,
            returns=0,
        )
    )


def _summary(
    run: IssueRun,
    disposition: IssueDisposition,
    grade: Grade | None,
    returns: int,
) -> IssueRunSummary:
    """1 課題の処理結果を共有側の要約へ写す（写像は `RESULT_OF` の 1 箇所）。"""
    return IssueRunSummary(
        issue_id=run.issue.id,
        stage=run.spec.name,
        persona=run.member.persona_key,
        result=RESULT_OF[disposition],
        grade=grade,
        returns=returns,
    )


def _is_failing(issue: Issue) -> bool:
    """課題に失敗の印が付いているか。"""
    return Labels.state(StateLabel.failing) in issue.labels


def _now() -> datetime:
    """現在時刻（UTC）。"""
    return datetime.now(UTC)
