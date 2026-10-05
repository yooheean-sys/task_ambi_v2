"""1 kickoff の計測と盤面への写像（monitor-F06〜F11）。契約は contracts/verdict-mapping.md。

`MonitorService` は `RunMonitor`（IF-38・IF-39）を実装する。`finish` は
**detach → 検出 → 等級と封筒 → 写像 → 確認読取**の 5 段を固定順で回し、
**呼び手へ送出してよい例外は `MappingUnverified` の 1 種類だけ**である（FR-057・SC-012）。

盤面の例外（`BoardUnavailable`・`IssueNotFound`）は写像でも確認読取でも捕まえて翻訳する
（共有側の契約 docstring は `BoardUnavailable` しか宣言しないが、現物は 4 メソッドとも
`IssueNotFound` を送出しうる＝ DIFF-05・SD-10）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn

from common.interfaces.board import BoardUnavailable, IssueNotFound, IssueStatus
from common.interfaces.executor import CrewEventKind
from common.interfaces.monitor import (
    FailureEvent,
    FailureKind,
    Grade,
    MappingUnverified,
    Measurement,
    RunVerdict,
)
from common.kernel.enums import Labels, StateLabel
from common.kernel.log import UNBOUND_CORRELATION_ID, CorrelationContext, LoggerFactory
from monitor.models import EVENT_RULES, Envelope, RawFailure, RunState

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter, Comment, Issue
    from common.interfaces.executor import CrewOutcome
    from common.interfaces.settings import LimitPolicy
    from monitor import MonitorConfig, MonitorDeps
    from monitor.repository import HttpHook

__all__ = ["MonitorService"]

_LOGGER = LoggerFactory.get_logger("monitor.service")

_GRADE_ORDER: tuple[Grade, ...] = (Grade.F, Grade.W, Grade.I)
"""最重の判定順（`F > W > I`＝ FR-044）。"""


class MonitorService:
    """`RunMonitor` の実装（monitor-F06〜F11）。"""

    def __init__(self, hook: HttpHook, deps: MonitorDeps, config: MonitorConfig) -> None:
        """フック・盤面の 2 契約・等級の割当・封筒の印を控える。"""
        self._hook = hook
        self._reader: BoardReader = deps.board_reader
        self._writer: BoardWriter = deps.board_writer
        self._grade_map = dict(config.grade_map)
        self._marker = config.envelope_marker
        self._correlation_id: str | None = None

    # -- IF-38 ---------------------------------------------------------------------------

    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold_t: int) -> None:
        """計測を開始し、以後の Hook 呼び出しをこの kickoff に帰属させる（IF-38）。

        二重 begin は single-flight（P5D-036）の違反だが、monitor は**検出して記録するだけ**で
        止めない（FR-034。**例外を送出しない**）。

        Args:
            kickoff_id: 開始する kickoff の識別子。
            limits: 回数と費用の上限（**長さの閾値は持たない**）。
            threshold_t: 長さの閾値（settings／orchestrator が導出した値）。
        """
        correlation_id = CorrelationContext.current()
        state = RunState(
            kickoff_id=kickoff_id,
            limits=limits,
            threshold_t=threshold_t,
            correlation_id=correlation_id,
            started_at=datetime.now(UTC),
        )
        superseded = self._hook.attach(state)
        self._correlation_id = correlation_id
        if superseded is not None:
            _LOGGER.warning(
                "monitor.begin.superseded",
                extra={
                    "kickoff_id": kickoff_id,
                    "superseded_kickoff_id": superseded.kickoff_id,
                    "superseded_requests": superseded.requests,
                },
            )

    # -- IF-39 ---------------------------------------------------------------------------

    def finish(
        self, kickoff_id: str, issue_id: str, actor: str, outcome: CrewOutcome
    ) -> RunVerdict:
        """計測を締め、等級を付け、F／W は盤面へ写像して確認読取まで行う（IF-39）。

        **5 段を固定順で呼ぶだけ**にする（contracts/verdict-mapping.md）。

        Args:
            kickoff_id: 締める kickoff の識別子（`begin` と食い違えば**こちらが正**）。
            issue_id: 写像先の課題。
            actor: 盤面に記録する行為者。
            outcome: 1 回の実行の結果。

        Returns:
            等級・種類・封筒・計測を載せた判定。

        Raises:
            MappingUnverified: 写像が読めない／`bd` に届かない。
        """
        measurement, raw = self._hook.detach(kickoff_id)  # ① 確定
        detected = self._detect(raw, measurement, outcome)  # ② 検出
        failures = tuple(  # ③ 等級（**`FailureEvent` を組むのはここ 1 箇所だけ**）
            FailureEvent(
                grade=self._grade_of(item.kind, kickoff_id),
                kind=item.kind,
                evidence=item.evidence,
                at=item.at,
            )
            for item in detected
        )
        grade = self._worst([event.grade for event in failures])
        envelope = Envelope(
            marker=self._marker,
            grade=Grade.I if grade is None else grade,
            kinds=tuple(event.kind for event in failures),
            kickoff_id=kickoff_id,
            correlation_id=self._correlation_id or UNBOUND_CORRELATION_ID,
            measurement=measurement,
            failures=failures,
        )
        closed = False
        if grade is Grade.F:  # ④ 写像
            abort_reason = self._map_failure(issue_id, actor, envelope)
            self._verify_mapping(issue_id, grade, envelope, abort_reason)  # ⑤ 確認読取
        elif grade is Grade.W:
            abort_reason = self._map_warning(issue_id, actor, envelope)
            self._verify_mapping(issue_id, grade, envelope, abort_reason)
            closed = abort_reason is None
        # I 級・失敗なしは ④ も ⑤ も行わない（FR-050・SC-009）
        return RunVerdict(
            grade=grade,
            kinds=[event.kind for event in failures],
            halted=measurement.halted_reason is not None or outcome.halted,
            closed=closed,
            envelope=envelope.render(),
            measurement=measurement,
            failures=list(failures),
        )

    # -- monitor-F08（検出） --------------------------------------------------------------

    def _detect(
        self, raw: list[RawFailure], measurement: Measurement, outcome: CrewOutcome
    ) -> list[RawFailure]:
        """6 観測点から失敗を集め、**種類で重複排除**する（contracts/verdict-mapping.md §2）。

        観測点の順が「最初の 1 件の `evidence` を残す」の意味を決める。

        Args:
            raw: Hook が積んだ失敗（観測点 1・2。実行中に確定済み）。
            measurement: 確定した計測（観測点 3 の片側）。
            outcome: 1 回の実行の結果（観測点 3〜6）。

        Returns:
            重複排除して `FailureKind` の宣言順に並べ替えた失敗の列。
        """
        at = datetime.now(UTC)
        collected = list(raw)  # 観測点 1・2
        halted_kind = self._halted_kind(measurement, outcome)  # 観測点 3
        if halted_kind is not None:
            collected.append(
                RawFailure(
                    kind=halted_kind,
                    evidence={"halted_reason": halted_kind.value},
                    at=at,
                )
            )
        # 観測点 4
        collected.extend(self._from_events(outcome, at, measurement.kickoff_id))
        if outcome.proposal is not None:  # 観測点 5
            collected.append(
                RawFailure(
                    kind=FailureKind.structured_conversion,
                    evidence={"items": len(outcome.proposal.items)},
                    at=at,
                )
            )
        blank = _blank_output(outcome.task_output)  # 観測点 6
        if blank is not None:
            collected.append(
                RawFailure(kind=FailureKind.no_output, evidence={"task_output": blank}, at=at)
            )
        return _dedupe(collected)

    def _halted_kind(self, measurement: Measurement, outcome: CrewOutcome) -> FailureKind | None:
        """停止の種類の出所を決める（§2.4。**フック側が正**）。"""
        from_hook = measurement.halted_reason
        from_outcome = outcome.halted_reason
        if from_hook is not None and from_outcome is not None and from_hook is not from_outcome:
            _LOGGER.warning(
                "monitor.detect.halted_mismatch",
                extra={
                    "kickoff_id": measurement.kickoff_id,
                    "hook_reason": from_hook.value,
                    "outcome_reason": from_outcome.value,
                },
            )
        if from_hook is not None:
            return from_hook
        if from_outcome is not None:
            return from_outcome
        if outcome.halted:  # 起こりえない組合せ。**種類を捏造せず**警告だけを残す
            _LOGGER.warning(
                "monitor.detect.halted_unknown", extra={"kickoff_id": measurement.kickoff_id}
            )
        return None

    def _from_events(self, outcome: CrewOutcome, at: datetime, kickoff_id: str) -> list[RawFailure]:
        """`CrewOutcome.events` を `EVENT_RULES` に照らす（§2.2）。

        `events` が空でも他の観測点で判定する。**検出器の欠落は種類を捏造せず警告に残すだけ**
        （`FailureKind` に受け皿が無い＝是正後の FR-042・SD-21）。

        Args:
            outcome: 1 回の実行の結果。
            at: 積む失敗に載せる時刻（同一 `finish` 内で揃える）。
            kickoff_id: 警告を run に紐付ける識別子（`None` を渡さない＝ plan (iii) 4）。

        Returns:
            照合で当たった失敗の列。
        """
        if not outcome.events:
            _LOGGER.warning("monitor.detect.no_events", extra={"kickoff_id": kickoff_id})
        incomplete = not outcome.completed or any(
            event.kind is CrewEventKind.crew_failed for event in outcome.events
        )
        found: list[RawFailure] = []
        for event in outcome.events:
            for rule in EVENT_RULES:  # **最初に当たった 1 行だけ**を採る
                if rule.match != event.kind.value:
                    continue
                if rule.requires_incomplete and not incomplete:
                    continue
                found.append(
                    RawFailure(kind=rule.kind, evidence={"event_kind": event.kind.value}, at=at)
                )
                break
        return found

    # -- monitor-F09（等級と封筒） --------------------------------------------------------

    def _grade_of(self, kind: FailureKind, kickoff_id: str) -> Grade:
        """`grade_map` を読んで等級を返す（**コード中でここ 1 箇所だけ**＝ FR-043・SC-007）。

        未知の種類は **`I` ではなく `F` に倒す**（未知の失敗を無症状にしない＝ FR-045・P3）。
        """
        value = self._grade_map.get(kind.value)
        if value is None:
            _LOGGER.warning(
                "monitor.grade.unknown_kind",
                extra={"kickoff_id": kickoff_id, "kind": kind.value},
            )
            return Grade.F
        return Grade(value)

    def _worst(self, grades: list[Grade]) -> Grade | None:
        """最重の等級を返す（`F > W > I`。失敗 0 件なら `None`＝ FR-044）。"""
        for grade in _GRADE_ORDER:
            if grade in grades:
                return grade
        return None

    # -- monitor-F10（写像） --------------------------------------------------------------

    def _map_failure(self, issue_id: str, actor: str, envelope: Envelope) -> str | None:
        """F 級の写像（`comment` → `set_labels` → `release`）。

        Returns:
            打ち切り理由（`<手の名前>: <例外名>`）。最後まで通れば `None`。
        """
        return self._run_steps(
            (
                ("comment", lambda: self._writer.comment(issue_id, envelope.render(), actor)),
                (
                    "set_labels",
                    lambda: self._writer.set_labels(
                        issue_id,
                        [Labels.state(StateLabel.failing)],
                        [Labels.state(StateLabel.working)],
                        actor,
                    ),
                ),
                ("release", lambda: self._writer.release(issue_id, actor)),
            )
        )

    def _map_warning(self, issue_id: str, actor: str, envelope: Envelope) -> str | None:
        """W 級の写像（`comment` → `close`）。

        Returns:
            打ち切り理由（`<手の名前>: <例外名>`）。最後まで通れば `None`。
        """
        return self._run_steps(
            (
                ("comment", lambda: self._writer.comment(issue_id, envelope.render(), actor)),
                ("close", lambda: self._writer.close(issue_id, envelope.summary(), actor)),
            )
        )

    def _run_steps(self, steps: tuple[tuple[str, Callable[[], None]], ...]) -> str | None:
        """各手を順に打ち、不達を受けたら**以後の手を打ち切る**（送出しない＝ FR-053）。"""
        for name, step in steps:
            try:
                step()
            except (BoardUnavailable, IssueNotFound) as exc:
                return f"{name}: {type(exc).__name__}"
        return None

    # -- monitor-F11（確認読取） ----------------------------------------------------------

    def _verify_mapping(
        self, issue_id: str, grade: Grade, envelope: Envelope, abort_reason: str | None
    ) -> None:
        """確認読取で写像の反映を確かめる（contracts/verdict-mapping.md §6）。

        **打ち切ったあとも確認読取は試みる**（FR-053a）。読み直し自体が失敗した場合は受けた
        例外の名前を `actual` に載せる。**外部ツールの終了コードを成功の証拠にしない**（FR-055）。

        Args:
            issue_id: 写像先の課題。
            grade: 最重の等級（`F` または `W`）。
            envelope: 盤面へ載せた封筒。
            abort_reason: 写像を打ち切った理由（無ければ `None`）。

        Raises:
            MappingUnverified: 読めない／一致しない／打ち切りがあった。
        """
        expected = _expected(grade, envelope)
        try:
            issue = self._reader.show(issue_id)
            comments = self._reader.comments(issue_id)
        except (BoardUnavailable, IssueNotFound) as exc:
            actual = f"readback: {type(exc).__name__}"
            self._halt(issue_id, envelope, abort_reason or actual, expected, actual, exc)
        matched = self._matches(grade, envelope, issue, comments)
        actual = _actual(issue, self._find_envelope(comments, envelope.kickoff_id))
        if abort_reason is not None:  # 部分的に書けたことを成功に倒さない（US3 AC-8）
            self._halt(issue_id, envelope, abort_reason, expected, actual, None)
        if not matched:
            self._halt(issue_id, envelope, "mismatch", expected, actual, None)

    def _matches(
        self, grade: Grade, envelope: Envelope, issue: Issue, comments: list[Comment]
    ) -> bool:
        """読み戻した値が期待と一致するか（§6.1）。"""
        if self._find_envelope(comments, envelope.kickoff_id) is None:
            return False
        if grade is Grade.F:
            return Labels.state(StateLabel.failing) in issue.labels and _is_unassigned(
                issue.assignee
            )
        return issue.status is IssueStatus.closed

    def _find_envelope(self, comments: list[Comment], kickoff_id: str) -> Comment | None:
        """**末尾から遡り**、印で始まる最初のコメントを封筒とみなす（§6.2）。

        末尾の 1 件に固定すると割り込みコメント 1 件で毎回停止し、位置を問わない形にすると
        前回の失敗が残した古い封筒と取り違える。だから**遡り ＋ 識別子の一致**の 2 段で見る
        （FR-054a・FR-054b）。走査は `created_at` の同値順序を信じず**返された列の逆順**で行う。
        """
        for comment in reversed(comments):
            headline = comment.body.splitlines()[0] if comment.body else ""
            if headline.startswith(self._marker):
                return comment if f"kickoff_id={kickoff_id}" in comment.body else None
        return None

    def _halt(
        self,
        issue_id: str,
        envelope: Envelope,
        reason: str,
        expected: str,
        actual: str,
        cause: BaseException | None,
    ) -> NoReturn:
        """停止痕跡を `ERROR` で残してから `MappingUnverified` を送出する（FR-056・§6.5）。

        `correlation_id` は**自分で入れない**（整形器が `CorrelationContext` から載せる）。

        Raises:
            MappingUnverified: 常に送出する（呼び手はこの関数の後へ進まない）。
        """
        _LOGGER.error(
            "monitor.mapping.unverified",
            extra={
                "kickoff_id": envelope.kickoff_id,
                "issue_id": issue_id,
                "envelope": envelope.render(),
                "reason": reason,
                "expected": expected,
                "actual": actual,
            },
        )
        raise MappingUnverified(
            f"写像を確認読取で確かめられない: {issue_id}",
            issue_id=issue_id,
            expected=expected,
            actual=actual,
            cause=cause,
        )


def _dedupe(collected: list[RawFailure]) -> list[RawFailure]:
    """**種類**で重複排除し（最初の 1 件を残す）、`FailureKind` の宣言順に並べる（§2.5）。"""
    first: dict[FailureKind, RawFailure] = {}
    for item in collected:
        first.setdefault(item.kind, item)
    order = list(FailureKind)
    return sorted(first.values(), key=lambda item: order.index(item.kind))


def _blank_output(task_output: str | None) -> str | None:
    """`task_output` が空か（`None`／空文字／空白のみ）を判定する（§2.3）。

    **「定型のみ」は実装しない** —— 判定語彙が設計・台帳・共有側のどこにも無く、FR-060 が設定を
    6 項目に固定しているため置き場も無い（SD-15・B-25。spec FR-040 の一部を意図して未達）。
    """
    if task_output is None:
        return "none"
    if task_output.strip() == "":
        return "blank"
    return None


def _is_unassigned(assignee: str | None) -> bool:
    """担当が空か（未設定・空文字・空白のみのいずれも空）＝ FR-054c・DIFF-06。"""
    return assignee is None or assignee.strip() == ""


def _expected(grade: Grade, envelope: Envelope) -> str:
    """期待した盤面の状態（§6.4）。"""
    head = (
        f"grade=F labels+{Labels.state(StateLabel.failing)} assignee=empty"
        if grade is Grade.F
        else "grade=W status=closed"
    )
    return f"{head} envelope[kickoff_id={envelope.kickoff_id}]"


def _actual(issue: Issue, envelope: Comment | None) -> str:
    """読み戻した盤面の状態（§6.4）。"""
    found = "ok" if envelope is not None else "none"
    return (
        f"status={issue.status.value} labels={list(issue.labels)}"
        f" assignee={issue.assignee!r} envelope={found}"
    )
