"""相関 ID の発番と束縛（data-model §4・plan C-1・research R-01）。

⚠ **`apps/api/src/api/correlation.py` と同型だが別実装である。** 共通化しない理由は 2 つで、
いずれも本 feature では動かせない（→ **`011` SD-21**。裁定はユーザ）:

1. **`packages/common` に置けない** —— `api/correlation.py` の docstring が「`packages/common`
   に発番手段を足さない」（D20-e）と明言し、FR-002 が common の変更を禁じる。
2. **`apps/api` から取れない** —— `.importlinter` の `layers: apps -> features -> common` により
   feature は `api` を import できない。

`common.kernel.log.CorrelationContext` は `bind`／`reset`／`current` の 3 つ**だけ**で発番手段を
持たないので、束縛にはそれを使い、発番だけを本ファイルが持つ。

trigger は **HTTP を通らない入口で自分が発番する初の MOD** である（憲章 §6）。4 入口
（`timer`・`file`・`manual`・`hooks`）はすべて `TriggerService.fire()` に合流し、その段 2 が
`correlation_scope()` を開く。**Protocol の引数には載せない**（D08-d）。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from collections.abc import Generator


def new_correlation_id() -> str:
    """相関 ID を 1 つ発番する（32 桁の小文字 16 進）。

    Returns:
        `uuid.uuid4().hex`。
    """
    return uuid.uuid4().hex


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Generator[str]:
    """相関 ID を束縛し、**`finally` で必ず解く**。

    ⚠ 解かないと次の発火に前回の ID が漏れる —— `ThreadPoolExecutor(max_workers=1)` と
    APScheduler の executor は**スレッドを使い回す**ため、`ContextVar` の値が残る（R-12）。

    Args:
        correlation_id: 束縛する値。省略時は `new_correlation_id()` で発番する。

    Yields:
        束縛した相関 ID。
    """
    value = correlation_id if correlation_id is not None else new_correlation_id()
    token = CorrelationContext.bind(value)
    try:
        yield value
    finally:
        CorrelationContext.reset(token)
