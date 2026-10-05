"""`BoardWriter.create`／`BoardWriter.close` を呼ぶ唯一の場所（SC-027・plan の C-2）。

`propose.py`（子の起票）・`execute.py`（質問の起票）・`kickoff.py`（親／リーフの close）は
本ファイルの関数を呼ぶ。**契約例外はここで握り潰さず呼び出し元へ素通しする** —— 差し戻しに
するか人間へ渡すかは呼び手（段別処理）の判断であり、通しの責務ではない。

`actor` は**引数で受けたものをそのまま使い、既定値を持たない**（P5D-037 の 2 系統をここが
決めない。起票と close はメンバの `bd_actor`、確保・解放・コメントはシステム actor）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from common.interfaces.board import Issue, IssueSpec
    from orchestrator.models import Ports


def create_issue(ports: Ports, spec: IssueSpec, actor: str) -> str:
    """課題を起票する。**`BoardWriter.create` を呼ぶ唯一の場所**（D01「起票 1 箇所」・SC-027）。

    確認読取はここでは行わない —— 分解（F08）は子をすべて起票したあとに `children_of` で
    一括して確かめる（FR-045 の順序）ため、1 件ごとに読み直すと順序が壊れる。

    Args:
        ports: 10 契約と自分の設定。
        spec: 起票仕様（ラベルは呼び手が全て載せる＝ FN-05）。
        actor: 起票の actor（AI の行為＝メンバの `bd_actor`）。

    Returns:
        起票された課題の識別子。

    Raises:
        BoardUnavailable: `bd` に届かない（呼び手が UNSAFE として扱う）。
        BoardWriteUnverified: 書込を確認読取で確かめられない（同上）。
    """
    return ports.deps.board_writer.create(spec, actor)


def close_issue(ports: Ports, issue_id: str, reason: str, actor: str) -> Issue:
    """課題を閉じ、**必ず `show` で読み直す**（D04-b）。`close` を呼ぶ唯一の場所。

    Args:
        ports: 10 契約と自分の設定。
        issue_id: 閉じる課題の識別子。
        reason: 閉じる理由。
        actor: close の actor（AI の行為＝メンバの `bd_actor`）。

    Returns:
        閉じたあとに読み直した課題。

    Raises:
        BoardUnavailable: `bd` に届かない。
        IssueNotFound: 読み直しで課題が見つからない。
    """
    ports.deps.board_writer.close(issue_id, reason, actor)
    return ports.deps.board_reader.show(issue_id)
