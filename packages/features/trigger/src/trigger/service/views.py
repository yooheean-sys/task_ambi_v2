"""F11 の表示用組み立て（`TriggerView`・`WatcherView`）。

`TriggerView` は**3 つの出所**を束ねる（FR-063）:

1. 宣言（領域 B の `triggers.yaml`）＝**正**
2. `next_fire_at` ← scheduler の `job.next_run_time`（インメモリ。未登録なら `null`）
3. `last_result` ← 履歴 csv の**直近 1 行**（**表示のためだけ**に読む＝ FR-058・H-8）

⚠ 履歴を読むのはここと `GET /history` の**2 経路だけ**である。**分岐に使った瞬間に憲章 §2
違反**になる（N-3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trigger.schemas import (
    HistoryRowBody,
    LaunchCondBody,
    TriggerViewBody,
    WatcherEntryBody,
    WatcherViewBody,
)

if TYPE_CHECKING:
    from datetime import datetime

    from trigger.models import RunHistoryRow, TriggerDef, WatcherState
    from trigger.repository import HistoryRepository


def build_trigger_view(
    definition: TriggerDef,
    *,
    next_fire_at: datetime | None,
    history: HistoryRepository,
    month: str,
) -> TriggerViewBody:
    """宣言 ＋ 次回実行時刻 ＋ 直近結果を 1 つの表示用 DTO にする。

    ⚠ **履歴の直近 1 行を読むのはこの関数だけ**である（`GET /history` と合わせて表示 2 経路）。
    呼び手を 1 箇所に閉じておくと、`test_trigger_history.py` の AST 走査が「分岐に使っていない」
    ことを機械的に固定できる（H-8・FR-058・N-3）。

    Args:
        definition: 宣言（正）。
        next_fire_at: scheduler の次回実行時刻（未登録・一時停止中なら `None`）。
        history: 履歴 csv のアダプタ（**表示のためだけ**に読む）。
        month: 直近 1 行を探す月（当月）。

    Returns:
        `openapi.yaml` の `TriggerView` に対応する DTO。
    """
    last_result = history.last_result_of(definition.trigger_id, month)
    return TriggerViewBody(
        trigger_id=definition.trigger_id,
        is_default=definition.is_default,
        name=definition.name,
        description=definition.description,
        type=definition.type,
        spec=definition.spec,
        condition=LaunchCondBody(
            labels=list(definition.condition.labels),
            project_key=definition.condition.project_key,
        ),
        enabled=definition.enabled,
        next_fire_at=next_fire_at,
        last_result=None if last_result is None else to_history_body(last_result),
    )


def to_history_body(row: RunHistoryRow) -> HistoryRowBody:
    """履歴の 1 行を表示用 DTO にする（**15 欄をそのまま写す**）。"""
    return HistoryRowBody.model_validate(row.model_dump())


def build_watcher_view(state: WatcherState) -> WatcherViewBody:
    """インメモリ状態を `WatcherView`（**4 欄**）にする。

    `scheduler_running` は `openapi.yaml` に在って設計側の型に無い欄で、正本（P5D-035）に従って
    載せる（DIFF-12）。**この口以外に読む手段を作らない**（憲章 §4 注記・FR-064）。
    """
    return WatcherViewBody(
        scheduler_running=state.scheduler_running,
        watchers=[
            WatcherEntryBody(
                trigger_id=entry.trigger_id, watch_dir=entry.watch_dir, alive=entry.alive
            )
            for entry in state.watchers
        ],
        next_fire_at=dict(state.next_fire_at),
        running=state.running,
    )
