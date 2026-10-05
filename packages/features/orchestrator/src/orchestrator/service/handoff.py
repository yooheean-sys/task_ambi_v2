"""F12 — 人間へ渡す 1 経路（コメント → 印の付け替え → 解放 の 3 手）。

**ほかの形を使わない**（質問の起票・単なる滞留・状態の発明）＝ FR-047・P5D-034。3 手はいずれも
**自分の行為**なのでシステム actor で書く（P5D-037・SC-028）。

盤面に届かなければ**構造化ログにだけ停止痕跡を残して打ち切る**（FR-049・D04-b）。盤面に痕跡を
残せない以上、盤面へ書けたことにしない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.board import BoardUnavailable
from common.kernel.enums import Labels, StateLabel
from common.kernel.log import LoggerFactory
from orchestrator.models import HandOff, HandOffReason, IssueDisposition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from orchestrator.models import Ports

_logger = LoggerFactory.get_logger(__name__)


def hand_off(ports: Ports, issue_id: str, handoff: HandOff) -> IssueDisposition:
    """理由コメント → 失敗の印への付け替え → 解放 を**この順で 1 回ずつ**行う（SC-011）。

    Args:
        ports: 10 契約と自分の設定。
        issue_id: 人間へ渡す課題。
        handoff: 区分・要約・詳細。

    Returns:
        `unsafe` なら `handed_off_unsafe`、ほかは `handed_off`。盤面に届かなければ `halted`。
    """
    writer = ports.deps.board_writer
    actor = ports.config.system_actor
    try:
        writer.comment(issue_id, handoff.render(ports.config.comment_marker), actor)
        writer.set_labels(
            issue_id,
            add=[Labels.state(StateLabel.failing)],
            remove=[Labels.state(StateLabel.working)],
            actor=actor,
        )
        writer.release(issue_id, actor)
    except BoardUnavailable as exc:
        _logger.error(
            "orchestrator.handoff.board_unavailable",
            extra={"issue_id": issue_id, "reason": handoff.reason.value},
            exc_info=exc,
        )
        return IssueDisposition.halted
    if handoff.reason is HandOffReason.unsafe:
        return IssueDisposition.handed_off_unsafe
    return IssueDisposition.handed_off


def guard_children(child_ids: Sequence[str]) -> HandOff:
    """既に子がいるので分解も再投入もしない（D01 二重ガード）。"""
    return HandOff(
        reason=HandOffReason.guard_children,
        summary="既に子がいるので分解も再投入もしない",
        details=(f"既存の子: {', '.join(child_ids)}",),
    )


def returns_exceeded(count: int, limit: int, last_feedback: str) -> HandOff:
    """差し戻しが上限に達した（FR-042）。"""
    return HandOff(
        reason=HandOffReason.returns_exceeded,
        summary="差し戻しが上限に達した",
        details=(
            f"差し戻しの件数: {count}",
            f"上限: {limit}",
            f"最後の指摘: {last_feedback}",
        ),
    )


def unsafe(done: Sequence[str]) -> HandOff:
    """起票順序の途中で失敗した。**自動では再試行しない**（FR-046・D01-b）。"""
    return HandOff(
        reason=HandOffReason.unsafe,
        summary="途中で失敗した。自動では再試行しない",
        details=tuple(done),
    )


def folder_missing(missing: Sequence[str]) -> HandOff:
    """プロジェクトのフォルダが実在しない（D07-g）。"""
    return HandOff(
        reason=HandOffReason.folder_missing,
        summary="プロジェクトのフォルダが実在しない",
        details=tuple(f"不在のパス: {path}" for path in missing),
    )


def config_missing(kind: str, detail: str) -> HandOff:
    """段・主体・プロジェクト・レビュー担当が決まらない（DIFF-12 の裁定でここへ寄せる）。"""
    return HandOff(
        reason=HandOffReason.config_missing,
        summary="段・主体・プロジェクト・レビュー担当が決まらない",
        details=(f"{kind}: {detail}",),
    )
