"""F03 — 起動時の拾い直し（未回答の質問あり／子あり／純粋な残骸 の 3 分岐）。

判定の出所は**盤面の導出のみ**（D01-c の回復層側）。分解の直前（F08）と**同じ契約**
（`children_of`）から引き、結果をキャッシュしない（FR-018）。

**回収では `state:failing` を付けない** —— 凍結 PoC の回収時 `state:failing` は目視用の装飾で、
V4 の `P5D-034`（人間へ渡す 1 経路）とは意味が違う。

**拾い直しで人間へ渡した課題は処理結果の一覧に載せない**（FR-071a・SD-34）。確保の前に走るので
段・主体が未解決で `IssueRunSummary` を組めない。`KickoffResult.notes` に 1 行だけ残す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.interfaces.board import BoardUnavailable, IssueNotFound
from common.kernel.log import LoggerFactory
from orchestrator.models import IssueDisposition, recover_comment
from orchestrator.service import handoff

if TYPE_CHECKING:
    from common.interfaces.board import ClaimFilter
    from orchestrator.models import Ports

_logger = LoggerFactory.get_logger(__name__)

RECOVER_REASON = "前回の実行が途中で終わった残骸を回収した"
"""回収コメントの理由。"""


@dataclass(frozen=True, slots=True)
class RecoverReport:
    """拾い直しの結果（周回へ返すのは `notes` と打ち切りの有無だけ）。"""

    notes: tuple[str, ...]
    """`KickoffResult.notes` へそのまま載せる行（FR-071a）。"""

    stopped: bool
    """盤面に届かず打ち切ったか（FR-017・SC-009）。"""


def recover_orphans(ports: Ports, claim_filter: ClaimFilter) -> RecoverReport:
    """作業中の残骸を列挙し、3 分岐で始末する（FR-013〜FR-018）。

    Args:
        ports: 10 契約と自分の設定。
        claim_filter: 実行文脈の確保条件（列挙の絞り込みに使う）。

    Returns:
        `notes` と打ち切りの有無。**盤面に届かないときは拾い直しを省いて先へ進まない**。
    """
    notes: list[str] = []
    try:
        orphans = ports.deps.board_reader.list_working(claim_filter)
    except (BoardUnavailable, IssueNotFound) as exc:
        _logger.error("orchestrator.recover.board_unavailable", exc_info=exc)
        return RecoverReport(notes=(), stopped=True)

    for orphan in orphans:
        try:
            stopped = _recover_one(ports, orphan.id, notes)
        except (BoardUnavailable, IssueNotFound) as exc:
            _logger.error(
                "orchestrator.recover.board_unavailable",
                extra={"issue_id": orphan.id},
                exc_info=exc,
            )
            return RecoverReport(notes=tuple(notes), stopped=True)
        if stopped:
            return RecoverReport(notes=tuple(notes), stopped=True)
    return RecoverReport(notes=tuple(notes), stopped=False)


def _recover_one(ports: Ports, issue_id: str, notes: list[str]) -> bool:
    """残骸 1 件を 3 分岐で始末する。打ち切るべきなら `True` を返す。

    Args:
        ports: 10 契約と自分の設定。
        issue_id: 残骸の識別子。
        notes: 一覧に載せられない課題の行を積む先。

    Returns:
        周回を打ち切るべきなら `True`。
    """
    reader = ports.deps.board_reader
    if reader.open_question_of(issue_id) is not None:
        return False  # 未回答の質問あり → その課題へ 1 回も書き込まない（FR-014）

    children = reader.children_of(issue_id)
    if children:
        disposition = handoff.hand_off(
            ports, issue_id, handoff.guard_children([child.id for child in children])
        )
        if disposition is IssueDisposition.halted:
            notes.append(f"skipped {issue_id}: 起動時の拾い直しで子がいたが盤面へ届かなかった")
            return True
        notes.append(f"skipped {issue_id}: 起動時の拾い直しで子がいたため人間へ渡した")
        return False

    writer = ports.deps.board_writer
    actor = ports.config.system_actor
    writer.comment(issue_id, recover_comment(ports.config.comment_marker, RECOVER_REASON), actor)
    writer.release(issue_id, actor)
    return False
