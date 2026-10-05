"""判定器の構築と、構築直後の 3 つの担保（contracts/authorizer.md §2・§3）。

実測が明らかにした「**構築時には落ちず、最初の判定で初めて落ちる**」経路を塞ぐ。casbin の
実体に触れるのは `repository.new_enforcer()` の 1 箇所で、本ファイルは担保と失敗の翻訳だけを持つ。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from settings.exceptions import SettingsAreaError
from settings.models import PROBE_TRIPLE, PolicyEnforcer
from settings.repository import new_enforcer

if TYPE_CHECKING:
    from pathlib import Path

_P_ROW_WIDTH = 3
"""`p` 行の列数（`sub, obj, act`）。合わないと `enforce()` で落ちる＝ M-09・FR-048c。"""

_G_ROW_WIDTH = 2
"""`g` 行の列数（`_, _`）。同上。"""


def build_enforcer(model_conf: Path, policy_csv: Path) -> PolicyEnforcer:
    """判定器を組み、**構築直後に 3 つの担保**を掛ける。

    実測が明らかにした「**構築時には落ちず、最初の判定で初めて落ちる**」経路を塞ぐ。これを
    しないと「起動は通ったが最初の Tool 実行で落ちる」＝憲章 §5 が避けようとした状態になる。

    担保 1 `enable_auto_save(False)`（M-12・FR-048d）→ 担保 2 列数の検査（M-09・FR-048c）→
    担保 3 空打ち 1 回（M-08・FR-048b）。**順序は 2 → 3**（列数の違反は行の位置を示せるため
    先に出す）。**担保 3 の戻り値は判定に使わない**（obj が `*` だけの行があると健全な設定でも
    真が返りうる＝ M-03 (3)。真偽を条件にすると誤検出する）。

    Args:
        model_conf: `global/model.conf` の絶対パス。
        policy_csv: `projects/<key>/policy.csv` の絶対パス。

    Returns:
        3 つの担保を掛け終えた判定器。

    Raises:
        SettingsAreaError: 構築または担保に失敗した（**見に行った絶対パス**を載せる）。
    """
    try:
        enforcer = new_enforcer(model_conf, policy_csv)
    except (OSError, RuntimeError, ValueError) as exc:
        msg = f"判定器を組めない（model={model_conf} / policy={policy_csv}）: {exc}"
        raise SettingsAreaError(msg, [msg]) from exc

    enforcer.enable_auto_save(False)
    try:
        _check_row_widths(enforcer, policy_csv)
    except TypeError as exc:
        # M-07: `[role_definition]` を欠く `model.conf` では `get_grouping_policy()` が
        # `TypeError: 'NoneType' object is not subscriptable` を送出する。担保 2 の入口で
        # 起きるが、原因は列数ではなくモデル側なのでそう報告する。
        msg = f"権限モデルが壊れている（model={model_conf}）: {exc}"
        raise SettingsAreaError(msg, [msg]) from exc
    try:
        enforcer.enforce(*PROBE_TRIPLE)
    except Exception as exc:
        msg = f"権限モデルが壊れている（model={model_conf}）: {exc}"
        raise SettingsAreaError(msg, [msg]) from exc
    return enforcer


def _check_row_widths(enforcer: PolicyEnforcer, policy_csv: Path) -> None:
    """担保 2: `p` 行が 3 列・`g` 行が 2 列であることを確かめる（M-09・FR-048c）。

    Raises:
        SettingsAreaError: 列数が合わない行がある（放置すると `enforce()` の時点で
            `RuntimeError: invalid policy size` になる）。
    """
    issues = [
        f"p 行の列数が {_P_ROW_WIDTH} でない（{policy_csv}）: {row}"
        for row in enforcer.get_policy()
        if len(row) != _P_ROW_WIDTH
    ]
    issues.extend(
        f"g 行の列数が {_G_ROW_WIDTH} でない（{policy_csv}）: {row}"
        for row in enforcer.get_grouping_policy()
        if len(row) != _G_ROW_WIDTH
    )
    if issues:
        raise SettingsAreaError("権限ファイルの列数が合わない（一覧を参照）", issues)
