"""相関 ID の発番・受信値の正規化・非 HTTP 入口の束縛（contracts/correlation-id.md）。

束縛の仕組みは `common.kernel.log.CorrelationContext`（`001` 実装済み）に載せる。
**`packages/common` に発番手段を足さない**（D20-e）。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from collections.abc import Generator

MAX_CORRELATION_ID_LENGTH = 128
"""引き継ぐ受信値の上限（**文字数**で数える）。超えたら新規発番する（FR-048）。"""


def new_correlation_id() -> str:
    """相関 ID を発番する（FR-047）。

    Returns:
        UUID version 4 を 32 桁の小文字 16 進（ハイフン無し）で表した文字列。
    """
    return uuid.uuid4().hex


def normalize_correlation_id(raw: str | None) -> str:
    """受信した相関 ID を正規化する（contracts/correlation-id.md §2・FR-048）。

    `None`・空文字列・128 文字超・制御文字（`ord < 0x20` または `ord == 0x7F`）を含む値は
    引き継がず新規発番する。それ以外は**形式を問わずそのまま引き継ぐ**。

    Args:
        raw: 受信ヘッダの値（latin-1 復号済み）。ヘッダが無ければ `None`。

    Returns:
        この要求に用いる相関 ID。
    """
    if not raw:
        return new_correlation_id()
    if len(raw) > MAX_CORRELATION_ID_LENGTH:
        return new_correlation_id()
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
        return new_correlation_id()
    return raw


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Generator[str]:
    """HTTP を通らない入口で相関 ID を束縛する（FR-024）。

    Args:
        correlation_id: 束縛する相関 ID。`None` なら `new_correlation_id()` で発番する。

    Yields:
        束縛した相関 ID。`with` を抜けると束縛を解除する。
    """
    resolved = correlation_id if correlation_id is not None else new_correlation_id()
    token = CorrelationContext.bind(resolved)
    try:
        yield resolved
    finally:
        CorrelationContext.reset(token)
