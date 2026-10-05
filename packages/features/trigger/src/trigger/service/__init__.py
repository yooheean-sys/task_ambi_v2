"""ビジネスロジック（憲章 §3 のサブパッケージ分割。plan C-2）。

`TriggerService` が持つのは 4 つである:

- **F03** 常駐の起動停止（`start`／`stop`。起動列 **7 段**・終了列 **3 段**）
- **F04** 既定トリガーの保証（`ensure_default`）
- **F09** 手動実行（`run_manual`。専用ワーカーへ `submit` して即 `run_id` を返す）
- **F10** 発火の 1 経路（`fire`。4 入口がすべてここへ合流する＝ P5D-041）

加えて口向けのメソッド（F11）を持つ。**`fire()` を増やさない** —— 入口が増えても合流点は 1 つ。

⚠ **常駐部品は `start()` で毎回作り直す**（SD-23）。`threading.Thread` は再 `start()` できず
`ThreadPoolExecutor` は `shutdown` 後に `submit` できないので、`build()` で作って使い回すと
**2 回目の `start()` が必ず落ちる**（`LifespanFactory` は再入可能が要求＝ R-03）。
**`build()` はスレッドを 1 本も起こさない**。
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from common.interfaces.orchestrator import (
    ExecutionContext,
    KickoffBusy,
    KickoffResult,
    LaunchKind,
    TriggerSnapshot,
)
from common.kernel.log import LoggerFactory
from trigger.correlation import correlation_scope
from trigger.exceptions import TriggerNotFound, validation_error
from trigger.models import (
    LaunchCond,
    LaunchKindLiteral,
    RunHistoryRow,
    TriggerDef,
    TriggerType,
    WatcherEntry,
    WatcherState,
)
from trigger.repository import (
    DefinitionsRepository,
    SettleWorker,
    WatcherRepository,
    default_trigger,
)
from trigger.repository.history import month_of
from trigger.schemas import (
    TriggerSpecBody,
    TriggerViewBody,
    ValidationProblemItem,
    WatcherViewBody,
)
from trigger.service import jobs, views
from trigger.service.jobs import Scheduler
from trigger.service.validate import (
    realpath_within,
    resolve_watch_dir,
    validate_definition,
    validate_delete,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime as DateTime  # noqa: N812  # 注釈だけで使う別名

    from trigger import TriggerConfig, TriggerDeps
    from trigger.repository import HistoryRepository

_logger = LoggerFactory.get_logger("trigger.service")

WORKER_THREAD_PREFIX = "trigger-fire"
"""`manual`・`hooks` の専用ワーカー（`max_workers=1`）のスレッド名（SD-08）。

**並列度を上げない** —— orchestrator が single-flight（P5D-036）なので、上げても `busy` の行が
増えるだけである。要求ごとにスレッドを作らないので、停止時に待つ対象も 1 つで済む。
"""

_ISSUES_OK = ("closed",)
"""`issues_ok` に数える結果（SD-10）。"""

_ISSUES_FAILING = ("failing", "unsafe", "halted")
"""`issues_failing` に数える結果（SD-10）。**`released`・`escalated` はどちらにも数えない**
（まだ終わっていない）。したがって `issues_ok + issues_failing < len(issues)` になりうる。"""


def ensure_default(
    repository: DefinitionsRepository,
    ready_interval_default_sec: int,
    definitions: Sequence[TriggerDef] | None = None,
) -> tuple[TriggerDef, ...]:
    """F04: 既定トリガー（`ready 巡回`）を **1 件保証**する（起動列の段 3）。

    無ければ足して**書き戻す**。既にあれば**書き戻さない**（バイト列が変わらない）。判定は
    `is_default` の旗で行う（人が `trigger_id` を書き換えても既定は 1 件のまま）。

    Args:
        repository: 宣言ファイルのアダプタ。
        ready_interval_default_sec: 初期間隔（**`config.json` から採る**＝ FR-020）。
        definitions: 既に読んである宣言（省略時は読み直す）。

    Returns:
        既定トリガーを含む宣言の並び。
    """
    current = tuple(definitions) if definitions is not None else repository.load_definitions()
    if any(item.is_default for item in current):
        return current
    updated = (*current, default_trigger(ready_interval_default_sec))
    repository.save_definitions(updated)
    return updated


def snapshot_of(definition: TriggerDef) -> TriggerSnapshot:
    """宣言から `TriggerSnapshot`（**値コピー**）を作る（D06-j(2)・FR-048）。

    削除後も履歴からトリガーが追えるのは、この値コピーを `ExecutionContext` に載せるためである。
    """
    return TriggerSnapshot(
        trigger_id=definition.trigger_id, name=definition.name, type=definition.type
    )


class TriggerService:
    """常駐・発火・口を束ねるサービス（F03・F04・F09・F10・F11）。"""

    def __init__(
        self,
        *,
        deps: TriggerDeps,
        config: TriggerConfig,
        definitions: DefinitionsRepository,
        history: HistoryRepository,
        downloads_dir: Path | None,
    ) -> None:
        """依存とアダプタを受け取る（**スレッドを 1 本も起こさない**＝ SD-23）。

        Args:
            deps: `KickoffRunner`（IF-49）1 件だけ。
            config: 挙動設定（CFG-62〜68）。
            definitions: 領域 B の宣言ファイルのアダプタ。
            history: 履歴 csv のアダプタ。
            downloads_dir: `DOWNLOADS_DIR`（未設定なら `None`）。
        """
        self._runner = deps.kickoff_runner
        self._config = config
        self._definitions = definitions
        self._history = history
        self._downloads_dir = downloads_dir

        self._defs: dict[str, TriggerDef] = {}
        self._loaded = False
        self._scheduler: Scheduler | None = None
        self._worker: ThreadPoolExecutor | None = None
        self._settle: SettleWorker | None = None
        self._watcher: WatcherRepository | None = None
        self._running_count = 0
        self._running_lock = threading.Lock()

    # ------------------------------------------------------------------ 観測（インメモリ）

    @property
    def running(self) -> bool:
        """進行中の `fire` があるか（`fire()` の入口/出口で増減する Lock 付きカウンタ）。"""
        with self._running_lock:
            return self._running_count > 0

    @property
    def scheduler_running(self) -> bool:
        """`scheduler.running`（DIFF-12）。"""
        return self._scheduler is not None and jobs.is_running(self._scheduler)

    @property
    def job_ids(self) -> tuple[str, ...]:
        """登録済みのジョブ id。"""
        return () if self._scheduler is None else jobs.job_ids(self._scheduler)

    @property
    def next_fire_at(self) -> dict[str, DateTime]:
        """`trigger_id → job.next_run_time`（一時停止中のジョブは載らない）。"""
        return {} if self._scheduler is None else jobs.next_fire_times(self._scheduler)

    @property
    def watcher_entries(self) -> tuple[WatcherEntry, ...]:
        """`WatcherState.watchers` に載せる一覧（`watcher_enabled=false` なら空）。"""
        return () if self._watcher is None else self._watcher.state()

    # ------------------------------------------------------------------ F03 起動列 7 段

    def start(self) -> None:
        """常駐を起こす（起動列 **7 段**。contracts/fire-pipeline.md §6）。

        途中で失敗したら**逆順で片付けてから再送出する**（C-5・R-04）—— `AsyncExitStack` は
        `__aenter__` の中で落ちたコンテキストを片付けないので、trigger 側が自分で片付けないと
        **起動中断のたびに孤児スレッドが残る**（FR-034「欠けたまま常駐しない」）。

        Raises:
            TriggerDefinitionsError: 段 2・段 3（宣言が読めない／書き戻せない）。
            Exception: 段 4 の登録失敗ほか（**起動中断**＝ FR-034）。
        """
        try:
            # 段 1: 常駐部品を**毎回新しく作る**（SD-23）
            self._scheduler = jobs.make_scheduler(self._config.timezone)
            self._worker = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=WORKER_THREAD_PREFIX
            )
            self._settle = SettleWorker(
                self._on_settled, stable_seconds=self._config.stable_seconds
            )
            self._watcher = WatcherRepository(
                self._on_detected,
                poll_interval_sec=self._config.poll_interval_sec,
                enabled=self._config.watcher_enabled,
            )
            _logger.info("trigger_components_created")

            # 段 2: 領域 B を**読み直す**（build と lifespan の間の人手の編集を反映＝ D07-a）
            declared = self._definitions.load_definitions()
            _logger.info("trigger_definitions_loaded", extra={"count": len(declared)})

            # 段 3: 既定トリガーを保証
            declared = ensure_default(
                self._definitions, self._config.ready_interval_default_sec, declared
            )
            _logger.info("trigger_default_ensured")
            self._defs = {item.trigger_id: item for item in declared}
            self._loaded = True

            # 段 4: 有効なトリガーを登録する（`file` は監視の側へ）
            watch_entries = self._register_all(declared)
            _logger.info("trigger_jobs_registered", extra={"count": len(self.job_ids)})

            # 段 5: 完了検知を起こす（**`watcher_enabled` と独立**＝ S-4）
            self._settle.start()
            _logger.info("trigger_settle_started")

            # 段 6: 監視を起こす
            self._watcher.start(watch_entries)
            _logger.info("trigger_watchers_started", extra={"count": len(watch_entries)})

            # 段 7: scheduler を起こす
            jobs.start_scheduler(self._scheduler)
            _logger.info("trigger_scheduler_started")
        except BaseException:
            self._cleanup_after_failed_start()
            raise

    def stop(self) -> None:
        """常駐を止める（終了列 **3 段**。**順を変えない**＝ FR-033・SD-26）。

        逆にすると停止中に新しい発火が入る。**停止した部品は捨てる**（次の `start()` が段 1 で
        作り直す＝ SD-23）。まだ起こしていなければ何もしない。
        """
        scheduler, worker = self._scheduler, self._worker
        settle, watcher = self._settle, self._watcher
        self._scheduler = self._worker = None
        self._settle = self._watcher = None

        if scheduler is not None:
            # 段 1: 進行中 kickoff は完走する（D06-j(2)・N-6）。**イベントループを塞ぐ**（C-4）
            jobs.shutdown_scheduler(scheduler, wait=True)
            _logger.info("trigger_scheduler_shutdown")
        if worker is not None:
            # 段 2: manual／hooks の 1 本
            worker.shutdown(wait=True)
            _logger.info("trigger_worker_shutdown")
        if watcher is not None or settle is not None:
            # 段 3: observer を止めてから完了検知スレッドを join
            if watcher is not None:
                watcher.stop()
            if settle is not None:
                settle.stop()
            _logger.info("trigger_watchers_stopped")

    def _cleanup_after_failed_start(self) -> None:
        """起動中断の片付け（**逆順**。段 1 で作っただけの部品は捨てるだけでよい）＝ C-5。"""
        if self._scheduler is not None:
            jobs.shutdown_scheduler(self._scheduler, wait=False)
        if self._watcher is not None:
            self._watcher.stop()
        if self._settle is not None:
            self._settle.stop()
        if self._worker is not None:
            self._worker.shutdown(wait=False)
        self._scheduler = self._worker = None
        self._settle = self._watcher = None
        _logger.warning("trigger_start_aborted")

    def _register_all(self, declared: Sequence[TriggerDef]) -> list[tuple[str, str]]:
        """段 4: 有効なトリガーを登録し、監視対象の一覧を返す。

        `DOWNLOADS_DIR` 未設定で `file` 種別が在るときは**当該 1 件だけ登録を飛ばす**
        （F-07 (b)。構造化ログに残し、一覧では `next_fire_at` が `null` になる）。
        """
        watch_entries: list[tuple[str, str]] = []
        for definition in declared:
            if not definition.enabled:
                continue
            if definition.type == "file":
                resolved = self._watch_dir_of(definition)
                if resolved is None:
                    _log_registration_skipped(definition.trigger_id)
                    continue
                watch_entries.append((definition.trigger_id, resolved))
                continue
            self._add_job(definition)
        return watch_entries

    def _add_job(self, definition: TriggerDef) -> None:
        """1 件を scheduler に登録する（発火時に**現在の宣言**を引き直す閉包を渡す）。"""
        if self._scheduler is None:
            return
        trigger_id = definition.trigger_id
        jobs.add_job(
            self._scheduler,
            definition,
            lambda: self._fire_timer(trigger_id),
            timezone=self._config.timezone,
            misfire_grace_sec=self._config.misfire_grace_sec,
        )

    def _watch_dir_of(self, definition: TriggerDef) -> str | None:
        """`file` 種別の `watch_dir` を解決する（外縁検査つき）。"""
        if self._downloads_dir is None:
            return None
        return resolve_watch_dir(self._downloads_dir, definition.spec)

    # ------------------------------------------------------------------ F10 発火の 1 経路

    def fire(
        self,
        snapshot: TriggerSnapshot,
        kind: LaunchKind,
        *,
        condition: LaunchCond,
        dropped_path: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """発火の 1 経路（**7 段**。contracts/fire-pipeline.md §2）。

        **戻り値を持たない** —— 結果は履歴 csv と構造化ログにだけ残る。**安全側に倒さない**
        （N-1〜N-6）: 開始行が書けなくても kickoff を止めず、`busy` を握りつぶさず、例外で
        ジョブを消さない。**`BaseException` は捕まえない**（`RunHalted`・`KeyboardInterrupt`・
        `SystemExit`）。

        Args:
            snapshot: 起動元トリガの**値コピー**（削除後も履歴が追える＝ D06-j(2)）。
            kind: `timer`／`file`／`manual`（**`hooks` は `file` に畳む**）。
            condition: 起動条件（発火時点の宣言から取る）。
            dropped_path: `file` のときだけ非 `None`。
            run_id: 手動実行が**その場で**発番した値（省略時は段 1 で発番する）。
        """
        # 段 1: run_id（`kickoff_id` と同形式だが**別発番**＝ SD-14）
        identifier = run_id if run_id is not None else uuid.uuid4().hex
        self._enter()
        try:
            # 段 2: 相関 ID を発番して束縛（憲章 §6）。`finally` で必ず解く（R-12）
            with correlation_scope() as correlation_id:
                # 段 3: `RunHistoryRow.started_at` と `ExecutionContext.requested_at` の両方（C-05）
                started_at = datetime.now(UTC)
                month = month_of(started_at)
                # 段 4
                ctx = ExecutionContext(
                    kind=kind,
                    trigger_snapshot=snapshot,
                    dropped_path=dropped_path,
                    claim_filter=condition.to_claim_filter(),
                    requested_at=started_at,
                )
                # 段 5: 開始行（**書けなくても kickoff を止めない**＝ N-1・FR-055・SC-004）
                started_written = self._append_start(
                    identifier, snapshot, kind, started_at, dropped_path, correlation_id
                )
                # 段 6: 同期・ブロッキング（D09-e。非同期化・リトライ・タイムアウトを足さない）
                patch = self._run_kickoff(ctx, started_at)
                # 段 7: 同じ行を確定（月は段 3 の `started_at` から引き回す＝ H-4）
                if started_written:
                    self._history.finalize(identifier, month, patch)
                else:
                    _logger.warning(
                        "history_finalize_skipped",
                        extra={"run_id": identifier, "month": month, "patch": patch},
                    )
        finally:
            self._leave()

    def _append_start(
        self,
        run_id: str,
        snapshot: TriggerSnapshot,
        kind: LaunchKind,
        started_at: datetime,
        dropped_path: str | None,
        correlation_id: str,
    ) -> bool:
        """段 5: 開始行を追記する。失敗しても**先へ進む**（N-1）。"""
        row = RunHistoryRow(
            run_id=run_id,
            trigger_id=snapshot.trigger_id,
            trigger_name=snapshot.name,
            trigger_type=_narrow_type(snapshot.type),
            kind=_narrow_kind(kind),
            started_at=started_at,
            status="started",
            dropped_path=dropped_path,
            correlation_id=correlation_id,
        )
        try:
            self._history.append_start(row)
        except OSError as exc:
            _logger.warning(
                "history_start_failed",
                extra={"run_id": run_id, "trigger_id": snapshot.trigger_id, "error": str(exc)},
            )
            return False
        return True

    def _run_kickoff(self, ctx: ExecutionContext, started_at: datetime) -> dict[str, object]:
        """段 6: `KickoffRunner.run` を**同期で**呼び、確定値の写像を返す。

        `KickoffBusy` は **`status="busy"` の 1 行**にする（握りつぶさない＝ N-2）。その他の
        `Exception` は `status="stopped"` にして**再送出しない**（ジョブは残す＝ N-4・FR-035）。
        """
        try:
            result = self._runner.run(ctx)
        except KickoffBusy as exc:
            return self._closing(
                started_at,
                status="busy",
                notes=f"busy: current_kickoff_id={exc.current_kickoff_id}",
            )
        except Exception:
            _logger.exception("kickoff_failed", extra={"kind": str(ctx.kind)})
            return self._closing(started_at, status="stopped")
        return self._from_result(result, started_at)

    def _from_result(self, result: KickoffResult, started_at: datetime) -> dict[str, object]:
        """`KickoffResult` を履歴の確定値へ写す（§3 の畳み方＝ SD-10）。"""
        patch = self._closing(started_at, status=str(result.status))
        patch["kickoff_id"] = result.kickoff_id
        patch["issues_ok"] = sum(1 for item in result.issues if item.result in _ISSUES_OK)
        patch["issues_failing"] = sum(1 for item in result.issues if item.result in _ISSUES_FAILING)
        # **切り詰めない**（I 級の付記の置き場＝ P5D-039）。カンマ・改行は csv の引用に任せる
        patch["notes"] = " / ".join(result.notes)
        return patch

    def _closing(
        self, started_at: datetime, *, status: str, notes: str | None = None
    ) -> dict[str, object]:
        """終了時刻・所要を採る（**`finished_at` は呼び手側で採る**）。"""
        finished_at = datetime.now(UTC)
        return {
            "finished_at": finished_at,
            "duration_sec": (finished_at - started_at).total_seconds(),
            "status": status,
            "notes": notes,
        }

    def _enter(self) -> None:
        with self._running_lock:
            self._running_count += 1

    def _leave(self) -> None:
        with self._running_lock:
            self._running_count -= 1

    # ------------------------------------------------------------------ 4 入口

    def _fire_timer(self, trigger_id: str) -> None:
        """`timer` 入口（APScheduler の executor スレッド上）。"""
        definition = self._defs.get(trigger_id)
        if definition is None:
            return
        self.fire(snapshot_of(definition), LaunchKind.timer, condition=definition.condition)

    def _fire_file(self, trigger_id: str, path: str) -> None:
        """`file` 入口（完了検知のスレッド上）。**`hooks` もここへ合流する**（FR-040）。"""
        definition = self._defs.get(trigger_id)
        if definition is None:
            return
        self.fire(
            snapshot_of(definition),
            LaunchKind.file,
            condition=definition.condition,
            dropped_path=path,
        )

    def _on_settled(self, trigger_id: str, path: str) -> None:
        """完了検知の callback（`SettleWorker` は `fire` を直接呼ばない＝ SD-07）。"""
        self._fire_file(trigger_id, path)

    def _on_detected(self, trigger_id: str, path: str) -> None:
        """監視の callback（**パスを完了検知へ渡すだけ**）。"""
        if self._settle is not None:
            self._settle.submit(trigger_id, path, "watchdog")

    # ------------------------------------------------------------------ F09 手動実行

    def run_manual(self, trigger_id: str) -> str:
        """手動実行を受け付け、**待たせずに** `run_id` を返す（FR-049・US4 AC1）。

        **`enabled=false` のトリガーも手で起こせる**（`openapi.yaml` に 422 が無い）。

        Args:
            trigger_id: 起こすトリガー。

        Returns:
            その場で発番した `run_id`（履歴の開始行と結ぶ＝ P5D-043）。

        Raises:
            TriggerNotFound: 不明な `trigger_id`（口では 404）。
        """
        definition = self._get(trigger_id)
        run_id = uuid.uuid4().hex
        snapshot = snapshot_of(definition)
        condition = definition.condition
        if self._worker is None:
            # 常駐が起きていない（lifespan の外）。同期で実行する。
            self.fire(snapshot, LaunchKind.manual, condition=condition, run_id=run_id)
            return run_id
        self._worker.submit(
            self.fire, snapshot, LaunchKind.manual, condition=condition, run_id=run_id
        )
        return run_id

    # ------------------------------------------------------------------ F11 口向け

    def list_triggers(self) -> list[TriggerViewBody]:
        """一覧（宣言 ＋ `next_fire_at` ＋ `last_result`）＝ FR-063。"""
        self._ensure_loaded()
        return [self._view(definition) for definition in self._defs.values()]

    def get_trigger(self, trigger_id: str) -> TriggerViewBody:
        """1 件。

        Raises:
            TriggerNotFound: 不明な `trigger_id`。
        """
        return self._view(self._get(trigger_id))

    def create_trigger(self, body: TriggerSpecBody) -> TriggerViewBody:
        """追加（`validate → 領域 B 書込 → add_job → watcher 開始` の順＝ FR-028）。

        `trigger_id` は `uuid4().hex` で発番し、**`is_default` は常に `false`**（既定トリガーは
        口から作れない）。

        Raises:
            TriggerValidationError: validate 違反（**宣言ファイルは 1 バイトも変わらない**）。
            TriggerDefinitionsError: 宣言ファイルに書けない（**追加は無かったことになる**）。
        """
        self._ensure_loaded()
        candidate = _from_body(uuid.uuid4().hex, body, is_default=False)
        definition = validate_definition(
            candidate, config=self._config, downloads_dir=self._downloads_dir
        )
        self._commit({**self._defs, definition.trigger_id: definition})
        self._activate(definition)
        return self._view(definition)

    def update_trigger(self, trigger_id: str, body: TriggerSpecBody) -> TriggerViewBody:
        """全量の編集（既定トリガーは `spec`・`enabled` **以外**が現在値と違えば 422＝ SD-12）。

        Raises:
            TriggerNotFound: 不明な `trigger_id`。
            TriggerValidationError: validate 違反。
            TriggerDefinitionsError: 宣言ファイルに書けない（**編集は無かったことになる**）。
        """
        current = self._get(trigger_id)
        candidate = _from_body(trigger_id, body, is_default=current.is_default)
        definition = validate_definition(
            candidate,
            config=self._config,
            downloads_dir=self._downloads_dir,
            current=current,
        )
        self._commit({**self._defs, trigger_id: definition})
        self._deactivate(current)
        self._activate(definition)
        return self._view(definition)

    def toggle_trigger(self, trigger_id: str, *, enabled: bool) -> TriggerViewBody:
        """有効／無効の切替のみ。**既定トリガーもトグルできる**（FR-019。422 を持たない）。

        Raises:
            TriggerNotFound: 不明な `trigger_id`。
            TriggerDefinitionsError: 宣言ファイルに書けない（**切替は無かったことになる**）。
        """
        current = self._get(trigger_id)
        definition = current.model_copy(update={"enabled": enabled})
        self._commit({**self._defs, trigger_id: definition})
        self._deactivate(current)
        self._activate(definition)
        return self._view(definition)

    def delete_trigger(self, trigger_id: str) -> None:
        """削除（**次回以降の発火を止めるだけ**。進行中の kickoff は完走＝ FR-029・D06-j(2)）。

        Raises:
            TriggerNotFound: 不明な `trigger_id`。
            TriggerValidationError: 既定トリガーの削除（V-7）。
            TriggerDefinitionsError: 宣言ファイルに書けない（**削除は無かったことになる**）。
        """
        current = self._get(trigger_id)
        validate_delete(current)
        self._commit({key: item for key, item in self._defs.items() if key != trigger_id})
        self._deactivate(current)

    def read_history(self, month: str | None = None) -> list[RunHistoryRow]:
        """履歴を読む（**表示のためだけ**＝ FR-058）。省略時は当月・無い月は空リスト。"""
        return self._history.read_history(month or month_of(datetime.now(UTC)))

    def watcher_state(self) -> WatcherViewBody:
        """インメモリ状態（**4 欄**）。`GET /watcher` 以外に読む手段を作らない（FR-064）。"""
        state = WatcherState(
            scheduler_running=self.scheduler_running,
            watchers=() if self._watcher is None else self._watcher.state(),
            next_fire_at=self.next_fire_at,
            running=self.running,
        )
        return views.build_watcher_view(state)

    def accept_file_hook(self, path: str) -> list[str]:
        """退避経路（`POST /hooks/file`）。**同じ完了検知を経て同じ `fire` へ合流する**（FR-040）。

        Args:
            path: 投下ファイルのパス。

        **常駐が起きていない（`start()` の外）ときは同期で発火する**（T090）—— `run_manual` の
        退避（専用ワーカーが無ければ同期で `fire`）と対称にする。黙って投入を飛ばすと、口は
        **202 ＋ `matched_trigger_ids`** を返すのに 1 度も発火しない。完了検知の専用スレッドが
        無いので安定待ち（`stable_seconds`）と重複抑止は飛ばし、その callback（`_on_settled`）へ
        直接入って**同じ `fire(kind=file)` へ合流する**（FR-040 の合流点は変えない）。

        Returns:
            一致した `trigger_id` の一覧。

        Raises:
            TriggerValidationError: どの `file` トリガーの `watch_dir` にも含まれない（422）。
        """
        self._ensure_loaded()
        matched: list[str] = []
        real: str | None = None
        for definition in self._defs.values():
            if definition.type != "file" or not definition.enabled:
                continue
            base = self._watch_dir_of(definition)
            if base is None:
                continue
            resolved = realpath_within(Path(base), path)
            if resolved is None:
                continue
            real = resolved
            matched.append(definition.trigger_id)
        if not matched or real is None:
            raise validation_error(
                [
                    ValidationProblemItem(
                        where="path",
                        reason=(
                            f"監視対象の外にあるパスは受け付けない: 「{path}」"
                            " / 有効な file トリガーの watch_dir の内側を指定する"
                        ),
                    )
                ]
            )
        for trigger_id in matched:
            if self._settle is None:
                # 常駐が起きていない（lifespan の外）。`run_manual` と同じく同期で回す（T090）。
                self._on_settled(trigger_id, real)
                continue
            self._settle.submit(trigger_id, real, "hook")
        return matched

    # ------------------------------------------------------------------ 内部

    def _ensure_loaded(self) -> None:
        """宣言を 1 度だけ読む（`start()` の前に口を叩かれた場合の保険）。"""
        if self._loaded:
            return
        self._defs = {item.trigger_id: item for item in self._definitions.load_definitions()}
        self._loaded = True

    def _get(self, trigger_id: str) -> TriggerDef:
        """1 件を引く。

        Raises:
            TriggerNotFound: 不明な `trigger_id`。
        """
        self._ensure_loaded()
        definition = self._defs.get(trigger_id)
        if definition is None:
            msg = f"トリガーが見つからない: {trigger_id}"
            raise TriggerNotFound(msg)
        return definition

    def _commit(self, defs: dict[str, TriggerDef]) -> None:
        """宣言ファイルへ**先に**書き、成功したときだけ in-memory を差し替える（FR-016）。

        **書込は `definitions.py` の 1 箇所に閉じる**。

        ⚠ **順を逆にしない**（T087）—— 先に `self._defs` を書き換えると、`save_definitions` が
        `TriggerDefinitionsError` を投げたときに巻き戻らず、**一覧（in-memory）・`triggers.yaml`・
        `job_ids` の 3 つが食い違ったまま常駐が続く**（削除なら「一覧から消えたのに発火し続け、
        一覧に無いので再削除は 404、再起動まで消せない」になる）。`save_definitions` は
        一時ファイル ＋ `os.replace` なので、失敗したときは宣言ファイルも 1 バイトも変わらない
        ＝ **3 つとも変更前のまま揃う**（FR-016「宣言が正」・FR-029）。

        `_activate()`／`_deactivate()` は呼び手が本メソッドの**後**に呼ぶ。書込に失敗すれば
        そこへ到達しないので、常駐（登録・監視）も変更前のままになる。

        Args:
            defs: 差し替え後の宣言（`trigger_id → TriggerDef`）。**並びがそのまま保存される**。

        Raises:
            TriggerDefinitionsError: 宣言ファイルに書けない（**in-memory は変更しない**）。
        """
        self._definitions.save_definitions(tuple(defs.values()))
        self._defs = defs

    def _activate(self, definition: TriggerDef) -> None:
        """常駐へ反映する（登録・監視の開始）。**次の起動を待たない**（FR-028）。

        `file` 種別で `watch_dir` が解決できない（`DOWNLOADS_DIR` 未設定／基準の外／実在しない）
        ときは、**段 4（`_register_all`）とまったく同じ `trigger_registration_skipped` を残す**
        （F-07 (b)・T098）。⚠ **黙って `return` しない** —— 到達経路は
        `PATCH /triggers/{trigger_id}`（`toggle_trigger`）で、`toggle_trigger` は `validate` を
        通らず、正本の `toggleTrigger` は **422 を持たない**ので口で拒否もできない。記録が無いと
        利用者からは「有効にしたのに永久に発火せず、理由がどこにも無い」状態になる（`enabled: true`
        は `triggers.yaml` に残るので、**次回の起動で初めて**段 4 が同じ記録を出す）。

        この手当ては `create_trigger`／`update_trigger` 経由の到達にも及ぶ（3 経路とも本メソッドを
        通る）。両者は `validate_definition` の `V-6`／`V-5` が先に落とすので常態では届かないが、
        `resolve_watch_dir` は `realpath` ＋ `stat` で**実在**を見るため、validate を通った後に
        当該フォルダが消えれば届く。**その競合でも同じ 1 行が残る**。

        `resolved` が解けても `self._watcher` が `None`（＝ `start()` していない）なら何もしない。
        これは `_add_job` が `self._scheduler is None` で即 `return` するのと**対称**で、
        「常駐していない」は「監視を開始できない」とは別の事象なので記録しない。
        """
        if not definition.enabled:
            return
        if definition.type == "file":
            resolved = self._watch_dir_of(definition)
            if resolved is None:
                _log_registration_skipped(definition.trigger_id)
                return
            if self._watcher is not None:
                self._watcher.add(definition.trigger_id, resolved)
            return
        self._add_job(definition)

    def _deactivate(self, definition: TriggerDef) -> None:
        """常駐から外す（登録の解除・監視の停止）。"""
        if self._scheduler is not None:
            jobs.remove_job(self._scheduler, definition.trigger_id)
        if self._watcher is not None:
            self._watcher.remove(definition.trigger_id)

    def _view(self, definition: TriggerDef) -> TriggerViewBody:
        """表示用 DTO を組む（**3 つの出所**を束ねる＝ FR-063）。"""
        return views.build_trigger_view(
            definition,
            next_fire_at=self.next_fire_at.get(definition.trigger_id),
            history=self._history,
            month=month_of(datetime.now(UTC)),
        )


def _log_registration_skipped(trigger_id: str) -> None:
    """`file` 種別の監視を開始できないことを 1 行残す（F-07 (b)）。

    起動列の段 4（`_register_all`）と CRUD 経由（`_activate`）の**両方**がここを呼ぶ。
    ⚠ **文言を経路ごとに分けない** —— 同じ事象が経路によって在ったり無かったり、
    別の語で出たりすると、利用者も運用も「発火しない理由」を 1 つの語で追えなくなる。
    """
    _logger.warning(
        "trigger_registration_skipped",
        extra={
            "trigger_id": trigger_id,
            "reason": "DOWNLOADS_DIR が未設定か watch_dir が解決できない",
        },
    )


def _from_body(trigger_id: str, body: TriggerSpecBody, *, is_default: bool) -> TriggerDef:
    """HTTP 境界の DTO を内部 VO へ写す。"""
    return TriggerDef(
        trigger_id=trigger_id,
        name=body.name,
        description=body.description,
        type=body.type,
        spec=body.spec,
        condition=LaunchCond(
            labels=tuple(body.condition.labels), project_key=body.condition.project_key
        ),
        enabled=body.enabled,
        is_default=is_default,
    )


def _narrow_kind(kind: LaunchKind) -> LaunchKindLiteral:
    """`LaunchKind` を履歴の `kind` へ畳む。

    **`hooks` は `file` に畳み**、`human` は trigger を通らない（D09-e）ので現れない。
    """
    if kind == LaunchKind.file:
        return "file"
    if kind == LaunchKind.manual:
        return "manual"
    return "timer"


def _narrow_type(value: str) -> TriggerType:
    """`TriggerSnapshot.type`（契約側は素の `str`）を履歴の `trigger_type` へ絞る。"""
    if value == "cron":
        return "cron"
    if value == "file":
        return "file"
    return "interval"
