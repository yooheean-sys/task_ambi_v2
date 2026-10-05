"""外部ストアとの受け渡し用の内部 VO（data-model §2。正本＝ `p5_trigger_data_model.mmd`）。

置くのは領域 B の宣言（`TriggerDef`・`LaunchCond`）・履歴 csv の 1 行（`RunHistoryRow`）・
インメモリ状態（`WatcherEntry`・`WatcherState`）・完了検知の中間状態（`FileEvent`）だけである。

**モジュール間の受け渡し型はここに置かない**（`common/interfaces/` が正本＝憲章 §7-2）。
trigger は Protocol を 1 本も提供しないので、そもそもここから外へ出る型は無い。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # pydantic のフィールド型なので実行時に要る
from typing import Literal

from pydantic import BaseModel, ConfigDict

from common.interfaces.board import ClaimFilter
from common.kernel.enums import Labels

TriggerType = Literal["cron", "interval", "file"]
"""トリガーの種別（`openapi.yaml` の `TriggerSpec.type` の enum）。"""

LaunchKindLiteral = Literal["timer", "file", "manual"]
"""履歴の `kind`（**`hooks` は `file` に畳む**。`human` は trigger を通らない＝ D09-e）。"""

HistoryStatus = Literal["started", "ok", "noop", "stopped", "busy"]
"""履歴の `status`。`KickoffStatus` の 4 つに**開始行の `started` が加わる**（P5D-043）。"""

DEFAULT_TRIGGER_ID = "ready-sweep"
"""既定トリガー（`ready 巡回`）の `trigger_id`。**固定値**（削除不可の識別に使う）。"""

DEFAULT_TRIGGER_NAME = "ready 巡回"
"""既定トリガーの表示名。"""

DEFAULT_WATCH_DIR = "."
"""`file` 種別の `spec` の既定（`DOWNLOADS_DIR` からの相対）。"""


class LaunchCond(BaseModel):
    """起動条件（DM-23）。**唯一のメソッド `to_claim_filter()` を持つ**（F06・plan C-3）。

    憲章 §7-2 の「振る舞いを持たせない」は**モジュール間・UI 境界の値**に掛かる制約であり、
    `LaunchCond` は内部 VO なので該当しない（渡るのは戻り値の `ClaimFilter`＝ common の型で、
    そちらは frozen・振る舞いなし）。`p5_trigger_data_model.mmd` の DM-23 が明示している形。
    """

    model_config = ConfigDict(frozen=True)

    labels: tuple[str, ...] = (Labels.role_executor(),)
    """AND 条件のラベル。既定は実行担当の 1 つだけ（D06-h・FR-026）。"""

    project_key: str | None = None

    def to_claim_filter(self) -> ClaimFilter:
        """盤面の確保条件へ**写すだけ**（F06・FR-026）。

        Returns:
            `ClaimFilter`（`labels` は集合、`project_key` はそのまま）。
        """
        return ClaimFilter(labels=frozenset(self.labels), project_key=self.project_key)


class TriggerDef(BaseModel):
    """宣言ファイル（`triggers.yaml`）の 1 件（DM-22）。**8 欄**。"""

    model_config = ConfigDict(frozen=True)

    trigger_id: str
    """`uuid4().hex`。既定トリガーだけ固定値 `"ready-sweep"`。"""

    name: str
    description: str = ""

    type: TriggerType

    spec: str
    """**種別によらず文字列 1 本**（DIFF-11）—— `cron`＝crontab 5 欄／`interval`＝秒（`"300"`）／
    `file`＝`watch_dir`（`DOWNLOADS_DIR` 相対・既定 `"."`）。"""

    condition: LaunchCond = LaunchCond()
    enabled: bool = True

    is_default: bool = False
    """`ready 巡回`（削除不可・`spec` と `enabled` のみ変更可＝ D06-j(1)・FN-30）。"""


class RunHistoryRow(BaseModel):
    """履歴 csv の 1 行 ＝ 1 run（DM-24）。**15 列**。

    列順は `openapi.yaml` の `HistoryRow` の properties の並び（C-03・SD-09）。未確定の欄は
    `None` で持ち、csv 側で空文字に写す（H-6）。
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    kickoff_id: str | None = None
    trigger_id: str
    trigger_name: str
    trigger_type: TriggerType
    kind: LaunchKindLiteral
    started_at: datetime
    finished_at: datetime | None = None
    duration_sec: float | None = None
    status: HistoryStatus
    issues_ok: int | None = None
    issues_failing: int | None = None
    notes: str | None = None
    dropped_path: str | None = None
    correlation_id: str


HISTORY_COLUMNS: tuple[str, ...] = tuple(RunHistoryRow.model_fields)
"""csv の列順（**`openapi.yaml` の `HistoryRow` の並び**）。`RunHistoryRow` の宣言順が正本。"""


class WatcherEntry(BaseModel):
    """1 件の `file` トリガーに対する監視の状態。"""

    model_config = ConfigDict(frozen=True)

    trigger_id: str
    watch_dir: str
    alive: bool
    """スレッド生死。**死んでも自動復旧しない**（憲章 §10・N-7・US2 AC6）。"""


class WatcherState(BaseModel):
    """インメモリ状態（DM-25 ＋ **DIFF-12**）。**4 欄**。

    `GET /watcher` 以外に読む手段を作らない（憲章 §4 注記・FR-064）。
    """

    model_config = ConfigDict(frozen=True)

    scheduler_running: bool
    """`scheduler.running`。**`openapi.yaml` の `WatcherView` に在って設計側の型に無い**
    （DIFF-12。正本＝ P5D-035 に従う）。"""

    watchers: tuple[WatcherEntry, ...] = ()
    next_fire_at: dict[str, datetime] = {}  # noqa: RUF012  # pydantic のフィールド既定（毎回コピーされる）
    running: bool = False
    """進行中の `fire` があるか（`fire()` の入口/出口で増減する Lock 付きカウンタ）。"""


class FileEvent:
    """検知 → 完了 → 発火の中間状態。**frozen にしない**（`last_size`・`stable_since` を書換える）。

    `trigger_id` を持つのは**重複抑止の鍵に要る**ためである（FR-042a。`p5_trigger_data_model.mmd`
    に無いので A 表 A-2 で足した）。
    """

    __slots__ = (
        "first_seen",
        "last_mtime",
        "last_size",
        "path",
        "source",
        "stable_since",
        "trigger_id",
    )

    def __init__(
        self,
        trigger_id: str,
        path: str,
        source: Literal["watchdog", "hook"],
        now: float,
    ) -> None:
        """保留に載せる時点の値で組む（`stat` はまだ取らない）。

        Args:
            trigger_id: どの `file` トリガーが拾ったか（重複抑止の鍵の一部）。
            path: `os.path.realpath` 済みの実パス。
            source: 入口の別。**`kind` は両方とも `file`** に畳む。
            now: `time.monotonic()`。
        """
        self.trigger_id = trigger_id
        self.path = path
        self.source: Literal["watchdog", "hook"] = source
        self.first_seen = now
        self.last_size = -1
        self.last_mtime = -1
        self.stable_since = now

    @property
    def key(self) -> tuple[str, str]:
        """保留の一意鍵（`trigger_id` ＋ 実パス）。同じ組は 1 件しか保留に載せない。"""
        return (self.trigger_id, self.path)
