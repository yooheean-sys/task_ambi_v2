"""orchestrator の内部 VO・入れ物・段の戻り・結果の語彙の写像・コメントの印。

正本は `docs/design/phase5/orchestrator/p5_orchestrator_data_model.mmd` の
`namespace orchestrator_internal`。形の確定は `specs/009-orchestrator/data-model.md`。

本ファイルは `common` 以外の自モジュール内 import を持たない（葉）。`OrchestratorConfig`・
`OrchestratorDeps` は `__init__.py` にあるため `if TYPE_CHECKING:` でのみ参照する
（実行時の輪を作らない＝ data-model.md §12）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal

from common.kernel.enums import Labels

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from common.interfaces.board import Comment, Issue
    from common.interfaces.executor import CrewOutcome
    from common.interfaces.orchestrator import ExecutionContext, KickoffResult
    from common.interfaces.settings import (
        HooksConfig,
        LimitPolicy,
        Member,
        Project,
        Rules,
        TaskTemplates,
    )
    from common.interfaces.stages import StageSpec
    from orchestrator import OrchestratorConfig, OrchestratorDeps

# --------------------------------------------------------------------------------------
# §2 定数（印の語と正準形の接頭辞）
# --------------------------------------------------------------------------------------

OUTCOME_PREFIX: Final = "outcome/"
"""**orchestrator が持つ正準形の知識はこれだけ**（FR-051・FR-061・SC-023a）。

完了ゲート（F09）と親条文の被覆検査（FR-058）が**同じ剥がし方**をする。接頭辞より後ろの
名前規則は保管庫の名前ゲートに委ね、段の側が持つ正準形の全条件を複製しない。
"""


def outcome_key(token: str) -> str | None:
    """成果物のトークンから**接頭辞を剥がした残り**（＝ファイル名）を返す。

    接頭辞で始まらないトークンは `None`（完了ゲートでは即不合格＝ FR-052、被覆検査では
    突合の対象にしない＝ FR-058）。**完了ゲート（F09）と被覆検査（F08）が同じ剥がし方を
    するよう、`OUTCOME_PREFIX` を参照するのはこの関数 1 つだけにする**（FR-051・FR-061）。

    Args:
        token: 段の側（`ProposalGate.extract_outcome_tokens`）が抜いたトークン。

    Returns:
        接頭辞より後ろの名前。接頭辞で始まらなければ `None`。
    """
    if not token.startswith(OUTCOME_PREFIX):
        return None
    return token[len(OUTCOME_PREFIX) :]


REPO_PREFIX: Final = "repo/"
"""リポジトリの成果物の接頭辞（D25-b）。**orchestrator が持つリポジトリの正準形の知識はこれだけ**。

接頭辞より後ろのパス規則（`..`・`.git`・閉じ込め）は repo の判定に委ね、複製しない
（`OUTCOME_PREFIX` と保管庫の名前ゲートの関係と同じ）。
"""


def repo_key(token: str) -> str | None:
    """リポジトリの成果物のトークンから**接頭辞を剥がした残り**（＝リポジトリ相対のパス）を返す。

    完了ゲート（F09）と被覆検査（F08）が同じ剥がし方をするよう、`REPO_PREFIX` を参照するのは
    この関数 1 つだけにする（`outcome_key` と同じ規律）。

    Args:
        token: 段の側（`ProposalGate.extract_repo_tokens`）が抜いたトークン。

    Returns:
        接頭辞より後ろのパス。接頭辞で始まらなければ `None`。
    """
    if not token.startswith(REPO_PREFIX):
        return None
    return token[len(REPO_PREFIX) :]


RETURN_LABEL: Final = "差し戻し"
"""差し戻しコメントの語。前方一致の鍵は `return_prefix()` が組む（FR-038）。"""

RECOVER_LABEL: Final = "回収"
"""回収コメントの語。**差し戻しの前方一致に掛からない**（FR-037・SD-02）。"""

HANDOFF_LABEL: Final = "停止"
"""人間へ渡すコメントの語。同じく差し戻しの前方一致に掛からない（SD-03）。"""

SINGLE_CHILD_OBSERVATION: Final = "single_child"
"""`GateResult.observations` に現れたら観測の印を付ける（D06-f・FR-045）。"""

SINGLE_CHILD_NOTE: Final = Labels.note("single-child")
"""子が 1 件のときに付ける観測ラベル。"""


def return_comment(marker: str, n: int, limit: int, category: str, reason: str) -> str:
    """差し戻しコメントの本文を組む（SD-03）。

    Args:
        marker: コメントの印（`comment_marker`）。
        n: 今回が何回目か（1 起点）。
        limit: 差し戻しの上限（`max_returns`）。
        category: 差し戻しの分類。**印の直後**に置き、前方一致の勘定を壊さない。
        reason: 差し戻しの理由。

    Returns:
        `<marker> 差し戻し（<n>/<limit> 回目）[<category>]: <reason>` の 1 行。
    """
    return f"{marker} {RETURN_LABEL}（{n}/{limit} 回目）[{category}]: {reason}"


def recover_comment(marker: str, reason: str) -> str:
    """回収コメントの本文を組む（FR-016）。

    Args:
        marker: コメントの印。
        reason: 回収した理由。

    Returns:
        `<marker> 回収: <reason>` の 1 行。**差し戻しの前方一致に掛からない**。
    """
    return f"{marker} {RECOVER_LABEL}: {reason}"


def return_prefix(marker: str) -> str:
    """差し戻しを数えるときの**前方一致の鍵**（唯一の出所。FR-038）。

    Args:
        marker: コメントの印。

    Returns:
        `<marker> 差し戻し`。
    """
    return f"{marker} {RETURN_LABEL}"


def count_return_comments(comments: Iterable[Comment], marker: str, system_actor: str) -> int:
    """盤面のコメントから差し戻しの件数を数える（FR-037・C-5）。

    **判定のたびに引き直した `comments` を渡すこと**（プロセスメモリで数えない）。回収コメント・
    人間へ渡すコメント・人間や AI メンバのコメントは数に入らない。読めるコメントが無ければ 0
    を返す（一過性の障害で人間に投げない＝ P-01）。

    Args:
        comments: 盤面から引き直したコメント一覧。
        marker: コメントの印。
        system_actor: 自分（システム）の actor。

    Returns:
        自分が書いた差し戻しコメントの件数。
    """
    prefix = return_prefix(marker)
    return sum(1 for c in comments if c.actor == system_actor and c.body.startswith(prefix))


# --------------------------------------------------------------------------------------
# §3 SingleFlight（P5D-036・C-4）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlightToken:
    """`SingleFlight.start()` の戻り。"""

    kickoff_id: str
    """`acquired` なら発番した id、そうでなければ進行中の id。"""

    acquired: bool
    """印を立てられたか。"""


class SingleFlight:
    """プロセス内に 1 つだけ持つ「進行中の印」（P5D-036）。両入口で共有する（FR-006）。

    ガードは**数命令ぶんしか保持しない**。kickoff の実行中は保持しないので FR-005 の
    「進行中の kickoff を待たない」を満たす（C-4）。永続化しない（再起動で解放）。
    """

    def __init__(self) -> None:
        """印とそれを守るガードを 1 つずつ持つ。"""
        self._guard = threading.Lock()
        self._current: str | None = None

    def start(self, mint: Callable[[], str]) -> FlightToken:
        """印の検査 → 発番 → 印立て を**原子的に**行う。

        **発番は印を立てられたときだけ**行う（FR-012a・FR-077a。断られた起動では発番しない）。

        Args:
            mint: 新しい kickoff の識別子を作る関数。

        Returns:
            取れたら発番した id と `True`、取れなければ進行中の id と `False`。
        """
        with self._guard:
            if self._current is not None:
                return FlightToken(kickoff_id=self._current, acquired=False)
            kickoff_id = mint()
            self._current = kickoff_id
            return FlightToken(kickoff_id=kickoff_id, acquired=True)

    def end(self) -> None:
        """印を戻す。**`finally` からのみ呼ぶ**（FR-008・SC-005）。"""
        with self._guard:
            self._current = None

    def current(self) -> str | None:
        """進行中の kickoff の識別子。無ければ `None`。"""
        with self._guard:
            return self._current


# --------------------------------------------------------------------------------------
# §4 KickoffRecord と RecordStore（P5D-035・C-8・R-14）
# --------------------------------------------------------------------------------------

type RecordStatus = Literal["running", "done"]
"""本 feature が作る記録の状態。**`rejected` は現れない**（FR-012a・SD-11）。"""


@dataclass(frozen=True, slots=True)
class KickoffRecord:
    """1 起動の記録（インメモリ。永続化しない＝ FR-011）。"""

    kickoff_id: str
    ctx: ExecutionContext
    status: RecordStatus
    result: KickoffResult | None


class RecordStore:
    """直近 N 件のインメモリ保持（FR-010〜FR-012）。

    router の 3 本の口（スレッドプール）と周回スレッドが同時に触るため `threading.Lock` 1 つで
    守る（R-14）。**断られた起動の記録は積まない**（FR-012a・SC-006a。`open()` は受付が通った
    起動でしか呼ばれない）。
    """

    def __init__(self, capacity: int) -> None:
        """保持件数の上限を受け取る。

        Args:
            capacity: 保持する記録の上限（`recent_results_n`）。
        """
        self._capacity = capacity
        self._lock = threading.Lock()
        self._records: dict[str, KickoffRecord] = {}

    def open(self, record: KickoffRecord) -> None:
        """走っている記録を登録する。溢れたら**古い `done` から**捨てる（FR-010）。

        Args:
            record: 登録する記録（`status="running"`）。
        """
        with self._lock:
            self._records[record.kickoff_id] = record
            self._evict()

    def complete(self, kickoff_id: str, result: KickoffResult) -> None:
        """記録を「終わった」状態へ更新し結果を添える（FR-012）。

        押し出されて見つからなければ**黙って何もしない**（例外にしない）。

        Args:
            kickoff_id: 対象の識別子。
            result: 周回の結果。
        """
        with self._lock:
            existing = self._records.get(kickoff_id)
            if existing is None:
                return
            self._records[kickoff_id] = replace(existing, status="done", result=result)

    def get(self, kickoff_id: str) -> KickoffRecord | None:
        """識別子で記録を引く。知らない・こぼれた識別子は `None`（FR-075）。

        Args:
            kickoff_id: 対象の識別子。

        Returns:
            記録。無ければ `None`。
        """
        with self._lock:
            return self._records.get(kickoff_id)

    def current(self) -> KickoffRecord | None:
        """走っている記録（single-flight により高々 1 件）。無ければ `None`（FR-076）。"""
        with self._lock:
            for record in self._records.values():
                if record.status == "running":
                    return record
            return None

    def _evict(self) -> None:
        """上限を超えたぶんを古い順に捨てる。**走っている記録は最後まで残す**。"""
        while len(self._records) > self._capacity:
            victim = next(
                (key for key, rec in self._records.items() if rec.status == "done"),
                next(iter(self._records)),
            )
            del self._records[victim]


# --------------------------------------------------------------------------------------
# §5 IssueRun（1 課題の処理状態。インメモリのみ）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssueRun:
    """1 課題の処理状態。**インメモリのみ**（進行状態の正は盤面）。"""

    issue: Issue
    spec: StageSpec
    member: Member
    project: Project
    limits: LimitPolicy
    rules: Rules
    hooks: HooksConfig
    templates: TaskTemplates
    human_guidance: tuple[str, ...]

    returns: int
    """**判定の出所は盤面。ここは要約に載せるための写し**（C-5・FR-037）。

    「前処理で読んだコメント＋自分の加算」に**最適化しない** —— 差し戻しの判定は
    `count_return_comments()` に盤面から引き直した値を渡して行う（SC-019）。
    """

    outcome: CrewOutcome | None
    """`CrewExecutor.run` の最後の戻り。差し戻しのたびに差し替わる。"""


# --------------------------------------------------------------------------------------
# §6 HandOff（人間へ渡す 1 経路の本文。P5D-034）
# --------------------------------------------------------------------------------------


class HandOffReason(StrEnum):
    """人間へ渡す 5 区分（FR-048。**増やさない**）。"""

    guard_children = "guard_children"
    """ガードに掛かった（既に子がいる）。"""

    returns_exceeded = "returns_exceeded"
    """差し戻しの上限に達した。"""

    unsafe = "unsafe"
    """起票順序の途中で失敗した（部分起票のまま）。"""

    folder_missing = "folder_missing"
    """プロジェクトのフォルダが不在。"""

    config_missing = "config_missing"
    """設定欠落（段・主体・PJ・レビュー担当が決まらない。DIFF-12 の裁定でここへ寄せる）。"""


@dataclass(frozen=True, slots=True)
class HandOff:
    """人間へ渡すときの理由と詳細（モジュール内部の VO。憲章 §7-2 の対象外）。"""

    reason: HandOffReason
    summary: str
    details: tuple[str, ...]

    def render(self, marker: str) -> str:
        """盤面へ載せる本文を組む（contracts/result-mapping.md §3 が正本）。

        1 行目は `<marker> 停止[...]` で始まり、**差し戻しの前方一致にも回収の前方一致にも
        掛からない**（FR-038・SD-03）。

        Args:
            marker: コメントの印。

        Returns:
            1 行目が見出し、2 行目以降が `- <detail>` の本文。
        """
        head = f"{marker} {HANDOFF_LABEL}[{self.reason.value}]: {self.summary}"
        return "\n".join([head, *(f"- {detail}" for detail in self.details)])


# --------------------------------------------------------------------------------------
# §7 Ports（10 契約と自分の設定を束ねた第 1 引数。C-8）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ports:
    """10 契約と自分の設定を束ねた、`service/` 各ファイルの第 1 引数。

    **他ファイルから私有属性を読む形を避ける**ためだけの入れ物であり、振る舞いを持たない。
    """

    deps: OrchestratorDeps
    config: OrchestratorConfig


# --------------------------------------------------------------------------------------
# §8 段の戻り（close を締めの後に置くための型。C-1）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloseIntent:
    """段別処理が「閉じたい」と言うための値。

    **`CloseIntent` を返しても閉じるとは限らない** —— `service/kickoff.py` が `finish` の戻りを
    見て、等級 W で `verdict.closed` が立っていれば抑止する（FR-068）。
    """

    reason: str
    actor: str


@dataclass(frozen=True, slots=True)
class Retry:
    """差し戻し。`feedback` を付けて**同じ kickoff の中で**再実行する（P5D-021）。"""

    category: str
    """差し戻しコメントの `[分類]`。"""

    feedback: str


@dataclass(frozen=True, slots=True)
class Settled:
    """盤面の処理が段の中で完結した（エスカレーション・人間へ渡した）。close しない。"""

    disposition: IssueDisposition


type StepResult = CloseIntent | Retry | Settled
"""`propose.handle()`／`execute.handle()` が返す 3 形。"""


# --------------------------------------------------------------------------------------
# §9 結果の語彙の写像（FR-071・SC-030a。**実装の 1 箇所**）
# --------------------------------------------------------------------------------------


class IssueDisposition(StrEnum):
    """1 課題の処理が終わった分岐（写像表の左列。7 語で全件）。"""

    closed_by_self = "closed_by_self"
    """分解で親を閉じた／レビュー承認で閉じた。"""

    closed_by_monitor = "closed_by_monitor"
    """等級 W で計測側が既に閉じていた（自分の close を抑止）。"""

    graded_failing = "graded_failing"
    """等級 F（計測側が失敗の印と解放を済ませている）。"""

    handed_off = "handed_off"
    """人間へ渡した（ガード／上限／フォルダ不在／設定欠落）。"""

    handed_off_unsafe = "handed_off_unsafe"
    """人間へ渡した（途中失敗＝部分起票のまま失敗）。"""

    escalated = "escalated"
    """判断を仰いだ。"""

    halted = "halted"
    """上限で止まった／写像を確かめられなかった／打ち切った。"""


type IssueResult = Literal["closed", "failing", "unsafe", "escalated", "halted"]
"""本モジュールが到達する結果の語（SC-030a の **5 語**）。

共有側 `IssueRunSummary.result` の 6 値の**部分**である。`released` を含まない（FR-071a）ので
6 値ぜんぶの写しではなく、`IssueRunSummary` を組む箇所で pyright が共有側の `Literal` との
適合を検査する（語が増減すれば構築点が赤くなる）。
"""

RESULT_OF: Final[dict[IssueDisposition, IssueResult]] = {
    IssueDisposition.closed_by_self: "closed",
    IssueDisposition.closed_by_monitor: "closed",
    IssueDisposition.graded_failing: "failing",
    IssueDisposition.handed_off: "failing",
    IssueDisposition.handed_off_unsafe: "unsafe",
    IssueDisposition.escalated: "escalated",
    IssueDisposition.halted: "halted",
}
"""**7 行で全件**（FR-071）。値に現れる語は 5 つ（closed・failing・unsafe・escalated・halted）。

`released` は**どの分岐からも使わない**（FR-071a・DIFF-07・SD-10）—— 失敗の印つき確保の
即時解放（FR-021）も、再確保時の解放（FR-022a）も、起動時の拾い直しで人間へ渡したもの
（FR-015）も**段・主体の解決前**に起きるため、`stage`・`persona` が省略不可の
`IssueRunSummary` を組めない。載らない課題は `KickoffResult.notes` に 1 行残す。

**`RESULT_OF` はモジュール全体で 1 つだけ**（SC-030a）。
"""
