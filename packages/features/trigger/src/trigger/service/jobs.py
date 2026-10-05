"""F07 時刻発火（APScheduler の操作）。**唯一の `apscheduler` import 点**。

`.importlinter` の `scheduler-scope` が「`apscheduler`・`watchdog` は trigger だけ」を機械検査する。
所有側の手当ては `allow_indirect_imports = True`（`trigger/__init__.py` の `build` が
`service → jobs → apscheduler` の連鎖を作るため、サブモジュール粒度では閉じられない＝ SD-16）。

`add_job` の 3 点（FR-032・実測 M-05）:

| 引数 | 値 | 効果 |
|---|---|---|
| `max_instances` | `1` | 同時実行は最大 1。溢れたら**警告ログのみ**でジョブは残る |
| `coalesce` | `True` | 遅れた分をまとめて 1 回にする＝**追い発火 0**（憲章 §2・FR-012） |
| `misfire_grace_time` | `config.misfire_grace_sec` | **設定から採る**（コードに直書きしない） |
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

# apscheduler 3.11.3 は `py.typed` を持たない（R-09）。**型スタブ不在の抑制はこの 5 行だけ**に
# 閉じ、Unknown が外へ漏れないよう境界の型を `Scheduler`（= Any）で止める（005 SD-36 と同型）。
import apscheduler.executors.pool as _pool  # pyright: ignore[reportMissingTypeStubs]  # apscheduler 3.11.3 は py.typed を持たない（R-09）
import apscheduler.jobstores.memory as _memory  # pyright: ignore[reportMissingTypeStubs]  # 同上
import apscheduler.schedulers.background as _background  # pyright: ignore[reportMissingTypeStubs]  # 同上
import apscheduler.triggers.cron as _cron  # pyright: ignore[reportMissingTypeStubs]  # 同上
import apscheduler.triggers.interval as _interval  # pyright: ignore[reportMissingTypeStubs]  # 同上

if TYPE_CHECKING:
    from collections.abc import Callable

    from trigger.models import TriggerDef

CRON_FIELDS = 5
"""crontab の欄数（**秒を持たない**）。"""

Scheduler = Any
"""apscheduler の scheduler の型（R-09: `py.typed` を持たないので pyright には Unknown）。

**Unknown をこのファイルの外へ漏らさない**ために、境界の型をここで `Any` に固定する
（`005` SD-36 と同型の割り切り）。呼び手（`service/__init__.py`）はこの別名だけを見る。
"""

SCHEDULER_JOBSTORE = "default"
"""**インメモリの jobstore 1 つだけ**（D07-a。外から書き換える経路を作らない＝ FR-021）。"""


def make_scheduler(timezone: str | None) -> Scheduler:
    """`BackgroundScheduler` ＋ `MemoryJobStore` を新しく作る（起動列の段 1）。

    **`BackgroundScheduler` のままでよい**（実測で確定）—— 専用スレッドで回るので FastAPI の
    イベントループを塞がない。`AsyncIOScheduler` へ替える必要は無い（P5D-041）。

    Args:
        timezone: IANA のタイムゾーン名（`None` はシステムローカル＝ D15-m）。

    Returns:
        まだ `start()` していない scheduler。
    """
    scheduler: Any = _background.BackgroundScheduler(
        jobstores={SCHEDULER_JOBSTORE: _memory.MemoryJobStore()},
        executors={"default": _pool.ThreadPoolExecutor(max_workers=1)},
        timezone=timezone,
    )
    return scheduler


def start_scheduler(scheduler: Scheduler) -> None:
    """Scheduler を起こす（起動列の段 7。**別関数にしてあるのは片付けの試験点にするため**）。"""
    scheduler.start()


def shutdown_scheduler(scheduler: Scheduler, *, wait: bool) -> None:
    """Scheduler を止める。

    ⚠ `wait=True` は**イベントループを塞ぐ**（実測 0.951 秒＝ C-4）。停止列の段 1 は
    `wait=True`（進行中 kickoff は完走＝ D06-j(2)・N-6）、起動中断の片付けは `wait=False`。

    Args:
        scheduler: 対象。
        wait: 進行中のジョブの完走を待つか。
    """
    if not scheduler.running:
        return
    scheduler.shutdown(wait=wait)


def check_cron(expression: str, timezone: str | None) -> str | None:
    """Crontab 式を APScheduler に解釈させ、失敗したら**原文**を返す（V-1・V-2 の reason 用）。

    Args:
        expression: crontab 式。
        timezone: 解釈のタイムゾーン。

    Returns:
        解釈できれば `None`、できなければ例外の原文。
    """
    factory: Any = _cron.CronTrigger
    try:
        factory.from_crontab(expression, timezone=timezone)
    except (ValueError, TypeError) as exc:
        return str(exc)
    return None


def build_schedule(definition: TriggerDef, timezone: str | None) -> object:
    """宣言から APScheduler の trigger を組む（`spec` は**文字列 1 本**＝ DIFF-11）。

    Args:
        definition: `type` が `cron` か `interval` の宣言（`file` は scheduler に載せない）。
        timezone: 解釈のタイムゾーン。

    Returns:
        `CronTrigger` または `IntervalTrigger`。

    Raises:
        ValueError: `spec` を解釈できない（validate を通っていれば起きない）。
    """
    if definition.type == "cron":
        factory: Any = _cron.CronTrigger
        cron: Any = factory.from_crontab(definition.spec, timezone=timezone)
        return cron
    if definition.type == "interval":
        interval: Any = _interval.IntervalTrigger(seconds=int(definition.spec), timezone=timezone)
        return interval
    msg = f"scheduler に載せられない種別: {definition.type}"
    raise ValueError(msg)


def add_job(
    scheduler: Scheduler,
    definition: TriggerDef,
    func: Callable[[], None],
    *,
    timezone: str | None,
    misfire_grace_sec: int,
) -> None:
    """1 件を登録する（**3 点は FR-032 のとおり**）。

    Args:
        scheduler: 登録先。
        definition: 宣言（`trigger_id` がジョブ id になる）。
        func: 発火時に呼ぶ無引数の呼び出し可能（`fire()` を束ねた閉包）。
        timezone: 解釈のタイムゾーン。
        misfire_grace_sec: 猶予（**設定から採る**）。
    """
    scheduler.add_job(
        func,
        trigger=build_schedule(definition, timezone),
        id=definition.trigger_id,
        name=definition.name,
        jobstore=SCHEDULER_JOBSTORE,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=misfire_grace_sec,
        replace_existing=True,
    )


def modify_job(scheduler: Scheduler, definition: TriggerDef, *, timezone: str | None) -> None:
    """登録済みの 1 件の間隔・式を差し替える。

    ⚠ **`PUT` の実装はこれを呼ばない**（design-sync **B-61**）—— `update_trigger` は
    `remove_job` ＋ `add_job` で通す。`PUT` は `type`（`cron`↔`interval`↔`file`）も `enabled` も
    変えられるので、**登録が無い相手**（`file` 種別・無効・`DOWNLOADS_DIR` 未設定で飛ばした 1 件）
    に当たると `modify_job` は `JobLookupError` になる。契約 §2.4 の「反映は `modify_job`」は
    その場合を言っておらず、**口から見える振る舞いに差は無い**ため実装側を残した。

    ⚠ **`next_run_time` を明示的に採り直す** —— APScheduler の `modify_job` は `trigger` を
    差し替えても**次回実行時刻を再計算しない**（実測）。採り直さないと、保存した新しい間隔は
    「次に 1 回発火してから」しか効かない。

    本関数は `test_trigger_lifespan.py` の F07 単体検証だけが呼ぶ。
    """
    schedule: Any = build_schedule(definition, timezone)
    scheduler.modify_job(
        definition.trigger_id,
        jobstore=SCHEDULER_JOBSTORE,
        trigger=schedule,
        name=definition.name,
        next_run_time=schedule.get_next_fire_time(None, datetime.now(scheduler.timezone)),
    )


def remove_job(scheduler: Scheduler, trigger_id: str) -> None:
    """登録を外す（**次回以降の発火を止めるだけ**。進行中の kickoff は完走＝ FR-029）。"""
    try:
        scheduler.remove_job(trigger_id, jobstore=SCHEDULER_JOBSTORE)
    except Exception:  # noqa: BLE001  # apscheduler の JobLookupError は型スタブが無く名指しできない
        return


def pause_job(scheduler: Scheduler, trigger_id: str) -> None:
    """一時停止。

    ⚠ **`PATCH` の実装はこれを呼ばない**（design-sync **B-61**。`modify_job` の注記と同じ理由）
    —— `toggle_trigger` は `remove_job` ＋ `add_job` で通す。未登録のジョブに当てると
    `JobLookupError` になる。本関数は F07 の単体検証だけが呼ぶ。
    """
    scheduler.pause_job(trigger_id, jobstore=SCHEDULER_JOBSTORE)


def resume_job(scheduler: Scheduler, trigger_id: str) -> None:
    """再開。

    ⚠ **`PATCH` の実装はこれを呼ばない**（design-sync **B-61**）。本関数は F07 の単体検証だけが
    呼ぶ。
    """
    scheduler.resume_job(trigger_id, jobstore=SCHEDULER_JOBSTORE)


def job_ids(scheduler: Scheduler) -> tuple[str, ...]:
    """登録済みのジョブ id（テストと `WatcherView` の組み立てが読む）。"""
    jobs: list[Any] = list(scheduler.get_jobs(SCHEDULER_JOBSTORE))
    return tuple(str(job.id) for job in jobs)


def next_fire_times(scheduler: Scheduler) -> dict[str, datetime]:
    """`trigger_id → job.next_run_time`（実測 M-05 で読めることを確認済み）。

    Returns:
        次回実行時刻を持つジョブだけの写像（`pause_job` されたジョブは `None` なので落ちる）。
    """
    result: dict[str, datetime] = {}
    jobs: list[Any] = list(scheduler.get_jobs(SCHEDULER_JOBSTORE))
    for job in jobs:
        moment: datetime | None = getattr(job, "next_run_time", None)
        if moment is not None:
            result[str(job.id)] = moment
    return result


def is_running(scheduler: Scheduler) -> bool:
    """`scheduler.running`（`WatcherView.scheduler_running`＝ DIFF-12）。"""
    return bool(scheduler.running)
