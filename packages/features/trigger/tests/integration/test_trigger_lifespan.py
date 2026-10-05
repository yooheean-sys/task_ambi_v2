"""F03 常駐の起動停止 と F07 時刻発火（`service/__init__.py`・`service/jobs.py`）。

**scheduler は実物**（`BackgroundScheduler` を短い interval で回す＝ spec A-06・M-05）。
`KickoffRunner` だけがスタブである（`bd`・vault・領域 A・LLM につながらない）。

観測点は contracts/fire-pipeline.md §6（起動列 7 段・終了列 3 段）と plan (vi)。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from conftest import StubKickoffRunner

from trigger import TriggerConfig, TriggerDeps
from trigger.exceptions import TriggerDefinitionsError, TriggerNotFound
from trigger.models import DEFAULT_TRIGGER_ID, LaunchCond, TriggerDef
from trigger.repository import DefinitionsRepository, HistoryRepository
from trigger.schemas import LaunchCondBody, TriggerSpecBody
from trigger.service import TriggerService, jobs

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

FAST_INTERVAL = "1"
"""テストで使う短い間隔（秒）。`min_interval_sec` を 1 に落として通す。"""

DEADLINE = 20.0

START_EVENTS = (
    "trigger_components_created",
    "trigger_definitions_loaded",
    "trigger_default_ensured",
    "trigger_jobs_registered",
    "trigger_settle_started",
    "trigger_watchers_started",
    "trigger_scheduler_started",
)
"""起動列 7 段（contracts/fire-pipeline.md §6）。"""

STOP_EVENTS = (
    "trigger_scheduler_shutdown",
    "trigger_worker_shutdown",
    "trigger_watchers_stopped",
)
"""終了列 3 段（**順を変えない**＝ FR-033・SD-26）。"""


def _config(**overrides: object) -> TriggerConfig:
    fields: dict[str, object] = {"min_interval_sec": 1, "misfire_grace_sec": 1}
    # ⚠ `misfire_grace_time` は APScheduler が **正の整数か None** しか受けない（実測）。
    #    plan (v) の不変条件は `misfire_grace_sec < min_interval_sec` までしか言わないので、
    #    0 を置くと段 4 で TypeError になる（→ design-sync B-59 に記録）。
    fields.update(overrides)
    return TriggerConfig(**fields)


def _service(
    root: Path,
    stub: StubKickoffRunner,
    *,
    config: TriggerConfig | None = None,
    downloads_dir: Path | None = None,
) -> TriggerService:
    settings = config if config is not None else _config()
    return TriggerService(
        deps=TriggerDeps(kickoff_runner=stub),
        config=settings,
        definitions=DefinitionsRepository(root),
        history=HistoryRepository(root),
        downloads_dir=downloads_dir,
    )


def _definition(**overrides: object) -> TriggerDef:
    fields: dict[str, object] = {
        "trigger_id": "t1",
        "name": "速い巡回",
        "description": "",
        "type": "interval",
        "spec": FAST_INTERVAL,
        "condition": LaunchCond(),
        "enabled": True,
        "is_default": False,
    }
    fields.update(overrides)
    return TriggerDef.model_validate(fields)


def _spec_body(**overrides: object) -> TriggerSpecBody:
    fields: dict[str, object] = {
        "name": "新しい",
        "description": "",
        "type": "interval",
        "spec": "5",
        "condition": LaunchCondBody(),
        "enabled": True,
    }
    fields.update(overrides)
    return TriggerSpecBody.model_validate(fields)


def _wait_until(predicate: Any, deadline: float = DEADLINE) -> bool:  # noqa: ANN401
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def running(trigger_root: Path) -> Iterator[tuple[TriggerService, StubKickoffRunner]]:
    """既定トリガーだけを持つ常駐（テストの多くが使う土台）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    service.start()
    try:
        yield service, stub
    finally:
        service.stop()


# --------------------------------------------------------------------------------------
# T037 F07: APScheduler の操作（実物の scheduler）
# --------------------------------------------------------------------------------------


def test_a_cron_spec_string_becomes_a_cron_trigger() -> None:
    """`CronTrigger.from_crontab(expr, timezone=…)` を `spec`（文字列 1 本）から組める。"""
    schedule = jobs.build_schedule(_definition(type="cron", spec="0 9 * * 0-4"), None)

    assert type(schedule).__name__ == "CronTrigger"


def test_an_interval_spec_string_becomes_an_interval_trigger() -> None:
    """`IntervalTrigger(seconds=…)` を `spec`（文字列）から組める。"""
    schedule = jobs.build_schedule(_definition(type="interval", spec="300"), None)

    assert type(schedule).__name__ == "IntervalTrigger"


def test_a_file_definition_cannot_be_put_on_the_scheduler() -> None:
    """`file` 種別は scheduler に載せない（監視の側で扱う）。"""
    with pytest.raises(ValueError, match="載せられない種別"):
        jobs.build_schedule(_definition(type="file", spec="."), None)


def test_add_job_carries_the_three_arguments_from_the_configuration() -> None:
    """FR-032・M-05: `max_instances=1`・`coalesce=True`・`misfire_grace_time=設定`。"""
    scheduler = jobs.make_scheduler(None)

    jobs.add_job(scheduler, _definition(), lambda: None, timezone=None, misfire_grace_sec=42)

    job: Any = scheduler.get_job("t1", jobstore=jobs.SCHEDULER_JOBSTORE)
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 42, "猶予が設定から採られていない（直書きしている）"


def test_modify_remove_pause_and_resume_all_work() -> None:
    """F07: `modify_job`／`remove_job`／`pause_job`／`resume_job` が単体で動く。

    ⚠ このうち **production 経路が呼ぶのは `remove_job` だけ**である（design-sync **B-61**）。
    `PUT`・`PATCH` は `remove_job` ＋ `add_job` で通すので、`modify_job`・`pause_job`・
    `resume_job` の呼び手は本テストだけになる。
    """
    scheduler = jobs.make_scheduler(None)
    jobs.add_job(scheduler, _definition(), lambda: None, timezone=None, misfire_grace_sec=1)
    scheduler.start()
    try:
        assert jobs.job_ids(scheduler) == ("t1",)
        before = jobs.next_fire_times(scheduler)["t1"]

        jobs.modify_job(scheduler, _definition(spec="600"), timezone=None)
        assert jobs.next_fire_times(scheduler)["t1"] != before, "modify で次回が動かない"

        jobs.pause_job(scheduler, "t1")
        assert "t1" not in jobs.next_fire_times(scheduler), "pause で次回が消えない"

        jobs.resume_job(scheduler, "t1")
        assert "t1" in jobs.next_fire_times(scheduler)

        jobs.remove_job(scheduler, "t1")
        assert jobs.job_ids(scheduler) == ()
    finally:
        jobs.shutdown_scheduler(scheduler, wait=False)


def test_removing_a_job_that_is_not_registered_is_harmless() -> None:
    """未登録の解除は落ちない（削除経路が二重に呼ばれても壊れない）。"""
    scheduler = jobs.make_scheduler(None)

    jobs.remove_job(scheduler, "never")


def test_next_run_time_is_readable(running: tuple[TriggerService, StubKickoffRunner]) -> None:
    """M-05: `job.next_run_time` が読める（`GET /triggers` の `next_fire_at` の出所）。"""
    service, _ = running

    assert DEFAULT_TRIGGER_ID in service.next_fire_at


def test_a_null_timezone_is_interpreted_as_the_system_local_zone() -> None:
    """D15-m: `timezone=None` はシステムローカルで解釈される（例外にならない）。"""
    scheduler = jobs.make_scheduler(None)

    assert scheduler.timezone is not None, "システムローカルが解決されていない"


# --------------------------------------------------------------------------------------
# T043 起動列 7 段・終了列 3 段
# --------------------------------------------------------------------------------------


def test_the_start_sequence_runs_seven_steps_in_order(
    trigger_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """起動列は**常駐部品 → 宣言 → 既定 → 登録 → 完了検知 → 監視 → scheduler** の 7 段。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    with caplog.at_level(logging.INFO, logger="trigger.service"):
        service.start()
    service.stop()

    seen = [record.message for record in caplog.records if record.message in START_EVENTS]
    assert seen == list(START_EVENTS), f"起動列の順が違う: {seen}"


def test_the_stop_sequence_runs_three_steps_in_order(
    trigger_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """終了列は **scheduler → 専用ワーカー → 監視（observer → 完了検知）**の 3 段（SD-26）。

    ⚠ **順を変えない** —— 逆にすると停止中に新しい発火が入る。
    """
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    service.start()

    with caplog.at_level(logging.INFO, logger="trigger.service"):
        service.stop()

    seen = [record.message for record in caplog.records if record.message in STOP_EVENTS]
    assert seen == list(STOP_EVENTS), f"終了列の順が違う: {seen}"
    assert seen[-1] == "trigger_watchers_stopped", "最後の事象が「終了」でない（M-05）"


def test_the_default_trigger_is_ensured_and_registered(trigger_root: Path) -> None:
    """段 3・段 4: 既定トリガーを保証して登録する（宣言ファイルが空でも常駐が立つ）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    service.start()
    try:
        assert service.job_ids == (DEFAULT_TRIGGER_ID,)
        assert DefinitionsRepository(trigger_root).load_definitions()[0].is_default is True
    finally:
        service.stop()


def test_the_declaration_is_re_read_at_lifespan_time(trigger_root: Path) -> None:
    """段 2・D07-a: **build と lifespan の間の人手の編集が反映される**（読み直す）。"""
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.ensure_template(300)
    service = _service(trigger_root, stub)

    # build（＝service 生成）の後で人が編集した
    repo.save_definitions([*repo.load_definitions(), _definition(trigger_id="added")])

    service.start()
    try:
        assert set(service.job_ids) == {DEFAULT_TRIGGER_ID, "added"}
    finally:
        service.stop()


def test_a_disabled_trigger_is_not_registered(trigger_root: Path) -> None:
    """段 4: 登録するのは**有効な**トリガーだけ。"""
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.save_definitions([_definition(trigger_id="off", enabled=False)])
    service = _service(trigger_root, stub)

    service.start()
    try:
        assert "off" not in service.job_ids
    finally:
        service.stop()


def test_a_file_trigger_is_skipped_when_the_downloads_dir_is_unset(
    trigger_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """F-07 (b): `DOWNLOADS_DIR` 未設定で `file` 種別が在るときは**当該 1 件だけ飛ばして起動**。"""
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.save_definitions([_definition(trigger_id="watch", type="file", spec=".")])
    service = _service(trigger_root, stub, downloads_dir=None)

    with caplog.at_level(logging.WARNING, logger="trigger.service"):
        service.start()
    try:
        assert "trigger_registration_skipped" in [record.message for record in caplog.records]
        assert service.scheduler_running is True, "起動が中断された（飛ばすだけのはず）"
        assert "watch" not in service.next_fire_at, "一覧の next_fire_at が null にならない"
    finally:
        service.stop()


def test_a_registration_failure_aborts_the_start(trigger_root: Path) -> None:
    """FR-034: 登録に失敗したら**起動を中断する**（欠けたまま常駐しない）。"""
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.path.write_text(
        "triggers:\n"
        "  - trigger_id: broken\n"
        "    name: 壊れている\n"
        "    description: ''\n"
        "    type: cron\n"
        "    spec: 'not a cron'\n"
        "    condition:\n"
        "      labels: ['role:executor']\n"
        "      project_key: null\n"
        "    enabled: true\n"
        "    is_default: false\n",
        encoding="utf-8",
    )
    service = _service(trigger_root, stub)

    with pytest.raises(ValueError, match="Wrong number of fields|Unrecognized|not a cron"):
        service.start()

    assert service.scheduler_running is False


def test_a_broken_declaration_aborts_the_start(trigger_root: Path) -> None:
    """段 2: `triggers.yaml` が壊れていたら起動中断（`TriggerDefinitionsError`）。"""
    stub = StubKickoffRunner()
    (trigger_root / "triggers.yaml").write_text("triggers: [\n", encoding="utf-8")
    service = _service(trigger_root, stub)

    with pytest.raises(TriggerDefinitionsError):
        service.start()


# --------------------------------------------------------------------------------------
# T044 SD-23: 常駐部品を毎回作り直す（再入可能）
# --------------------------------------------------------------------------------------


def test_building_the_service_starts_no_threads(trigger_root: Path) -> None:
    """SD-23: **`build()` はスレッドを 1 本も起こさない**（部品は `start()` で作る）。"""
    before = threading.active_count()

    _service(trigger_root, StubKickoffRunner())

    assert threading.active_count() == before


def test_the_same_service_can_be_started_and_stopped_twice(
    trigger_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R-03: 同じ factory で **2 回入って開始 2・停止 2**（再入可能）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    with caplog.at_level(logging.INFO, logger="trigger.service"):
        for _ in range(2):
            service.start()
            service.stop()

    messages = [record.message for record in caplog.records]
    assert messages.count("trigger_scheduler_started") == 2
    assert messages.count("trigger_scheduler_shutdown") == 2


def test_the_second_round_still_fires_manually_and_on_the_timer(trigger_root: Path) -> None:
    """SD-23: **2 回目の中でも**手動実行と時刻発火が動く（部品が作り直されている）。

    ⚠ 使い回すと `submit` が `RuntimeError: cannot schedule new futures after shutdown`、
    `Thread.start` が `RuntimeError: threads can only be started once` になる。
    """
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.save_definitions([_definition()])
    service = _service(trigger_root, stub)

    service.start()
    service.stop()
    first_round = stub.count

    service.start()
    try:
        service.run_manual("t1")
        assert _wait_until(lambda: stub.count >= first_round + 1), "2 回目で手動実行が動かない"
        assert _wait_until(lambda: stub.count >= first_round + 2), "2 回目で時刻発火が動かない"
    finally:
        service.stop()


def test_the_second_round_still_settles_dropped_files(
    trigger_root: Path, downloads_dir: Path
) -> None:
    """SD-23: 2 回目の中でもファイル投下（完了検知 → 発火）が動く。"""
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.save_definitions([_definition(trigger_id="w", type="file", spec=".")])
    service = _service(
        trigger_root,
        stub,
        config=_config(stable_seconds=1, poll_interval_sec=0.2),
        downloads_dir=downloads_dir,
    )

    service.start()
    service.stop()

    service.start()
    try:
        (downloads_dir / "second_round.bin").write_bytes(b"payload")

        assert _wait_until(lambda: stub.count >= 1), "2 回目で完了検知が動かない"
    finally:
        service.stop()


def test_stop_only_drops_the_components(trigger_root: Path) -> None:
    """SD-23: `stop()` は参照を落とすだけで**作り直さない**（停止したまま常駐が復活しない）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    service.start()

    service.stop()

    assert service.scheduler_running is False
    assert service.job_ids == ()
    assert service.watcher_entries == ()


def test_stopping_twice_is_harmless(trigger_root: Path) -> None:
    """停止列が二重に呼ばれても落ちない。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    service.start()

    service.stop()
    service.stop()


# --------------------------------------------------------------------------------------
# T045 C-5: 起動中断の片付け（孤児スレッドを残さない）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step",
    ["components", "definitions", "default", "jobs", "settle", "watchers", "scheduler"],
)
def test_a_failure_in_any_start_step_leaves_no_orphan_threads(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch, downloads_dir: Path, step: str
) -> None:
    """C-5・R-04: 段 1〜7 のどこで失敗しても**孤児スレッドが 0**（逆順で片付ける）。

    ⚠ `AsyncExitStack` は `__aenter__` の中で落ちたコンテキストを片付けない。trigger 側が
    自分で片付けないと、起動中断のたびにスレッドが残る（FR-034「欠けたまま常駐しない」）。
    """
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.save_definitions([_definition()])
    service = _service(
        trigger_root, stub, config=_config(poll_interval_sec=0.2), downloads_dir=downloads_dir
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = f"段 {step} で失敗"
        raise RuntimeError(msg)

    if step == "components":
        monkeypatch.setattr("trigger.service.jobs.make_scheduler", _boom)
    elif step == "definitions":
        monkeypatch.setattr(
            "trigger.repository.definitions.DefinitionsRepository.load_definitions", _boom
        )
    elif step == "default":
        monkeypatch.setattr("trigger.service.ensure_default", _boom)
    elif step == "jobs":
        monkeypatch.setattr("trigger.service.jobs.add_job", _boom)
    elif step == "settle":
        monkeypatch.setattr("trigger.repository.settle.SettleWorker.start", _boom)
    elif step == "watchers":
        monkeypatch.setattr("trigger.repository.watcher.WatcherRepository.start", _boom)
    else:
        monkeypatch.setattr("trigger.service.jobs.start_scheduler", _boom)

    before = {thread.name for thread in threading.enumerate()}

    with pytest.raises(RuntimeError, match=f"段 {step} で失敗"):
        service.start()

    assert _wait_until(
        lambda: {thread.name for thread in threading.enumerate()} <= before, deadline=10.0
    ), f"孤児スレッドが残った: {{thread.name for thread in threading.enumerate()}} - {before}"
    assert service.scheduler_running is False


# --------------------------------------------------------------------------------------
# T046 SC-001・SC-003・SC-017
# --------------------------------------------------------------------------------------


def test_sc001_a_short_interval_fires_exactly_once_in_one_period(trigger_root: Path) -> None:
    """SC-001: 常駐開始から間隔 1 回分＋猶予の間に kickoff の呼び出しが**ちょうど 1 回**。"""
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions([_definition(spec="1")])
    service = _service(trigger_root, stub)

    service.start()
    try:
        assert _wait_until(lambda: stub.count >= 1), "1 回も発火しない"
        first = stub.count
        assert first == 1, f"1 周期で複数回発火した: {first}"
    finally:
        service.stop()


def test_sc003_a_slow_kickoff_is_never_run_concurrently_and_is_never_caught_up(
    trigger_root: Path,
) -> None:
    """SC-003・N-5: 間隔の 3 倍かかる kickoff の間、呼び出しは **1 回だけ**・取り返しは **0 回**。

    `max_instances=1`（同時実行 1）と `coalesce=True`（追い発火 0）の観測点である。
    """
    stub = StubKickoffRunner(mode="slow", seconds=3.0)
    DefinitionsRepository(trigger_root).save_definitions([_definition(spec="1")])
    service = _service(trigger_root, stub)

    service.start()
    try:
        assert _wait_until(lambda: stub.count >= 1)
        time.sleep(3.0)
        during = stub.count
        assert during == 1, f"進行中に重ねて呼ばれた（max_instances=1 が効いていない）: {during}"

        time.sleep(1.5)
        after = stub.count
        assert after <= 2, f"取り逃した分を取り返している（coalesce が効いていない）: {after}"
    finally:
        service.stop()


def test_sc017_stop_waits_for_the_kickoff_in_flight(trigger_root: Path) -> None:
    """SC-017・N-6: 停止時に**進行中の kickoff が最後まで走ってから**止まる。"""
    stub = StubKickoffRunner(mode="slow", seconds=2.0)
    DefinitionsRepository(trigger_root).save_definitions([_definition(spec="1")])
    service = _service(trigger_root, stub)
    service.start()
    assert _wait_until(lambda: stub.entered.is_set()), "発火しない"

    began = time.monotonic()
    service.stop()
    elapsed = time.monotonic() - began

    assert elapsed >= 1.0, f"進行中を待たずに止めた: {elapsed:.3f} 秒"
    rows = HistoryRepository(trigger_root).read_history(_month())
    assert [row.status for row in rows] == ["ok"], "進行中の実行が確定していない"


def _month() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m")


# --------------------------------------------------------------------------------------
# T054a FR-028: 保存が常駐へ反映される（US3 AC2）
# --------------------------------------------------------------------------------------


def test_creating_a_trigger_writes_the_file_and_registers_the_job(
    running: tuple[TriggerService, StubKickoffRunner], trigger_root: Path
) -> None:
    """FR-028・US3 AC2: `triggers.yaml` に書かれ、**同時に `get_jobs()` が +1**。"""
    service, _ = running
    before = len(service.job_ids)

    view = service.create_trigger(_spec_body())

    assert len(service.job_ids) == before + 1, "次の起動を待たないと登録されない"
    assert view.trigger_id in service.job_ids
    assert view.is_default is False, "口から作ったのに既定になっている"
    saved = {item.trigger_id for item in DefinitionsRepository(trigger_root).load_definitions()}
    assert view.trigger_id in saved


def test_updating_a_trigger_reregisters_the_job_and_moves_the_next_fire_time(
    running: tuple[TriggerService, StubKickoffRunner],
) -> None:
    """FR-028: `update_trigger` の反映で **`next_fire_at` が動く**（次の起動を待たない）。

    反映は `remove_job` ＋ `add_job` である（**`modify_job` ではない**＝ design-sync B-61）。
    どちらでも `next_fire_at` は動くので、**登録が 1 件のまま残る**ことを併せて固定する。
    """
    service, _ = running
    created = service.create_trigger(_spec_body(spec="600"))
    before = service.next_fire_at[created.trigger_id]

    service.update_trigger(created.trigger_id, _spec_body(spec="60"))

    assert service.next_fire_at[created.trigger_id] != before
    assert service.job_ids.count(created.trigger_id) == 1, "登録が消えたか二重になった"


def test_updating_a_disabled_trigger_does_not_need_a_registered_job(
    running: tuple[TriggerService, StubKickoffRunner],
) -> None:
    """B-61 を機械的に固定する: **登録が無い相手でも `PUT` が通る**。

    契約 §2.4 の言う `modify_job` で反映していたら、登録の無いジョブに当たって
    `JobLookupError` になる。`remove_job` ＋ `add_job` なので通る。
    """
    service, _ = running
    created = service.create_trigger(_spec_body(spec="600", enabled=False))
    assert created.trigger_id not in service.job_ids, "前提: 無効なので登録されていない"

    view = service.update_trigger(created.trigger_id, _spec_body(spec="60", enabled=False))

    assert view.enabled is False
    assert created.trigger_id not in service.job_ids, "無効なのに登録された"


def test_toggling_a_trigger_removes_and_readds_the_job(
    running: tuple[TriggerService, StubKickoffRunner],
) -> None:
    """FR-028: `toggle_trigger` が常駐へ反映される（**`remove_job` ＋ `add_job`**）。

    ⚠ 契約 §2.5 は `pause_job`／`resume_job` と書くが実装は登録の付け外しである
    （design-sync **B-61**）。`next_fire_at` だけを見るとどちらでも緑になるので、
    **`job_ids`（＝ `scheduler.get_jobs()`）に残るか消えるか**で選んだ側を機械的に固定する
    —— `pause_job` なら `next_fire_at` からは落ちても `job_ids` には残る。
    """
    service, _ = running
    created = service.create_trigger(_spec_body())

    service.toggle_trigger(created.trigger_id, enabled=False)
    assert created.trigger_id not in service.next_fire_at
    assert created.trigger_id not in service.job_ids, "pause で済ませている（登録が残っている）"

    service.toggle_trigger(created.trigger_id, enabled=True)
    assert created.trigger_id in service.next_fire_at
    assert created.trigger_id in service.job_ids


def test_the_default_trigger_can_be_toggled(
    running: tuple[TriggerService, StubKickoffRunner],
) -> None:
    """FR-019: **既定トリガーもトグルできる**（削除だけができない）。"""
    service, _ = running

    view = service.toggle_trigger(DEFAULT_TRIGGER_ID, enabled=False)

    assert view.enabled is False
    assert DEFAULT_TRIGGER_ID not in service.next_fire_at
    assert DEFAULT_TRIGGER_ID not in service.job_ids


def test_deleting_a_trigger_removes_the_job(
    running: tuple[TriggerService, StubKickoffRunner], trigger_root: Path
) -> None:
    """FR-028: `delete_trigger` で `remove_job` される。"""
    service, _ = running
    created = service.create_trigger(_spec_body())

    service.delete_trigger(created.trigger_id)

    assert created.trigger_id not in service.job_ids
    saved = {item.trigger_id for item in DefinitionsRepository(trigger_root).load_definitions()}
    assert created.trigger_id not in saved


# --------------------------------------------------------------------------------------
# T087 FR-016・FR-029: 領域 B に書けなかったとき 3 つが食い違わない
# --------------------------------------------------------------------------------------


Facts = set[tuple[str, str, str, bool]]
"""突き合わせる 1 件の内容: `(trigger_id, type, spec, enabled)`。

⚠ **id 集合だけを比べない** —— `update`・`toggle` は id を変えないので、id だけの比較では
「一覧は新しい値・宣言ファイルは古い値」の食い違いを**素通りする**（実測で確認済み）。
"""


def _listed_facts(service: TriggerService) -> Facts:
    """in-memory の一覧（`GET /triggers` の出所）。"""
    return {(v.trigger_id, v.type, v.spec, v.enabled) for v in service.list_triggers()}


def _saved_facts(root: Path) -> Facts:
    """宣言ファイル（領域 B。**こちらが正**＝ FR-016）。"""
    return {
        (item.trigger_id, item.type, item.spec, item.enabled)
        for item in DefinitionsRepository(root).load_definitions()
    }


def _registered(facts: Facts) -> set[str]:
    """宣言から導かれる「登録されているはずの `trigger_id`」（`file` は scheduler に載らない）。"""
    return {tid for tid, kind, _, enabled in facts if enabled and kind != "file"}


def _mutate(service: TriggerService, operation: str, trigger_id: str) -> None:
    """4 つの書込操作を 1 つの表から呼ぶ。"""
    if operation == "create":
        service.create_trigger(_spec_body(name="増やす"))
    elif operation == "update":
        service.update_trigger(trigger_id, _spec_body(spec="900"))
    elif operation == "toggle":
        service.toggle_trigger(trigger_id, enabled=False)
    else:
        service.delete_trigger(trigger_id)


@pytest.mark.parametrize("operation", ["create", "update", "toggle", "delete"])
def test_a_failed_write_does_not_split_the_list_the_file_and_the_jobs(
    running: tuple[TriggerService, StubKickoffRunner], trigger_root: Path, operation: str
) -> None:
    """T087・FR-016・FR-029: 領域 B に書けなくても**一覧・宣言ファイル・登録が揃ったまま**。

    先に in-memory を書き換えてから保存すると巻き戻らず、削除では「一覧から消えたのに
    発火し続け、一覧に無いので再削除は 404」になる。**保存に成功してから差し替える**。
    """
    service, _ = running
    victim = service.create_trigger(_spec_body(spec="600"))
    before = _saved_facts(trigger_root)
    before_jobs = set(service.job_ids)
    assert _listed_facts(service) == before, "前提: 一覧と宣言ファイルが揃っていない"
    assert before_jobs == _registered(before), "前提: 登録が宣言と揃っていない"

    trigger_root.chmod(0o500)  # 領域 B を読み取り専用にする（SC-004 と同じ手口）
    try:
        with pytest.raises(TriggerDefinitionsError):
            _mutate(service, operation, victim.trigger_id)
    finally:
        trigger_root.chmod(0o700)

    after = _saved_facts(trigger_root)
    assert after == before, "宣言ファイルが変わった（書込は失敗したはず）"
    assert _listed_facts(service) == after, "一覧が宣言ファイルと食い違った"
    assert set(service.job_ids) == before_jobs == _registered(after), "常駐の登録が食い違った"


def test_a_trigger_whose_deletion_failed_can_still_be_deleted(
    running: tuple[TriggerService, StubKickoffRunner], trigger_root: Path
) -> None:
    """T087: 書込に失敗した削除は**無かったことになる**（一覧に残り、再削除できる）。"""
    service, _ = running
    created = service.create_trigger(_spec_body(spec="600"))

    trigger_root.chmod(0o500)
    try:
        with pytest.raises(TriggerDefinitionsError):
            service.delete_trigger(created.trigger_id)
    finally:
        trigger_root.chmod(0o700)

    assert created.trigger_id in {view.trigger_id for view in service.list_triggers()}
    assert created.trigger_id in service.job_ids, "一覧に無いのに発火し続ける形になっている"

    service.delete_trigger(created.trigger_id)

    assert created.trigger_id not in service.job_ids
    assert created.trigger_id not in {tid for tid, *_ in _saved_facts(trigger_root)}


def test_toggling_on_a_file_trigger_without_a_downloads_dir_leaves_the_reason(
    trigger_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T098 / F-07 (b): `PATCH` で有効化しても監視を開始できないなら**理由を 1 行残す**。

    `toggle_trigger` は `validate` を通らず、正本の `toggleTrigger` は **422 を持たない**ので、
    宣言は `enabled: true` になるが監視は始まらない。ここで記録が無いと、利用者から見た状態は
    「有効にしたのに永久に発火せず、理由がどこにも無い」になる（**次回の起動**で段 4 が同じ
    記録を出すまで気づけない）。段 4（`_register_all`）と**同じ語**が出ることを固定する。

    ⚠ 既存の 2 本はどちらもここを通らない —— `test_toggling_a_trigger_removes_and_readds_the_job`
    は `interval` 種別しか見ず、`test_a_file_trigger_is_skipped_when_the_downloads_dir_is_unset`
    は起動列の段 4 であって `PATCH` を通らない。
    """
    stub = StubKickoffRunner()
    repo = DefinitionsRepository(trigger_root)
    repo.save_definitions([_definition(trigger_id="watch", type="file", spec=".", enabled=False)])
    service = _service(trigger_root, stub, downloads_dir=None)

    service.start()
    try:
        caplog.clear()  # 段 4 の記録と混ざらないようにする（当該トリガーは無効なので本来 0 件）
        with caplog.at_level(logging.WARNING, logger="trigger.service"):
            view = service.toggle_trigger("watch", enabled=True)

        assert view.enabled is True, "宣言は有効になる（口が 422 を持たないので拒否できない）"
        assert service.watcher_entries == (), "監視は始まらない"
        skipped = [
            record for record in caplog.records if record.message == "trigger_registration_skipped"
        ]
        assert len(skipped) == 1, "監視を開始できないのに理由がどこにも残らない"
        assert getattr(skipped[0], "trigger_id", None) == "watch"
        assert getattr(skipped[0], "reason", None)
    finally:
        service.stop()


def test_a_file_trigger_starts_and_stops_the_watcher(
    trigger_root: Path, downloads_dir: Path
) -> None:
    """FR-028: `file` 種別では**監視の開始・停止も伴う**（`watchers` が増減する）。"""
    stub = StubKickoffRunner()
    service = _service(
        trigger_root, stub, config=_config(poll_interval_sec=0.2), downloads_dir=downloads_dir
    )
    service.start()
    try:
        created = service.create_trigger(_spec_body(type="file", spec="."))
        assert {entry.trigger_id for entry in service.watcher_entries} == {created.trigger_id}

        service.delete_trigger(created.trigger_id)
        assert service.watcher_entries == ()
    finally:
        service.stop()


def test_an_unknown_trigger_id_is_a_not_found(
    running: tuple[TriggerService, StubKickoffRunner],
) -> None:
    """不明 id は `TriggerNotFound`（口では 404）。"""
    service, _ = running

    for action in (
        lambda: service.get_trigger("nope"),
        lambda: service.update_trigger("nope", _spec_body()),
        lambda: service.toggle_trigger("nope", enabled=True),
        lambda: service.delete_trigger("nope"),
        lambda: service.run_manual("nope"),
    ):
        with pytest.raises(TriggerNotFound):
            action()


# --------------------------------------------------------------------------------------
# T054b FR-029: 削除は次回以降だけを止める（US3 AC6）
# --------------------------------------------------------------------------------------


def test_deleting_while_a_kickoff_is_running_lets_it_finish(trigger_root: Path) -> None:
    """FR-029・US3 AC6・D06-j(2): 削除しても**進行中の kickoff は最後まで走る**。

    `TriggerSnapshot` を値コピーで渡してあるので、削除後も履歴から追える（FR-048）。
    """
    stub = StubKickoffRunner(mode="slow", seconds=2.0)
    DefinitionsRepository(trigger_root).save_definitions([_definition(spec="1")])
    service = _service(trigger_root, stub)
    service.start()
    try:
        assert _wait_until(lambda: stub.entered.is_set()), "発火しない"

        service.delete_trigger("t1")

        assert "t1" not in service.job_ids, "登録が残っている"
        assert _wait_until(lambda: not service.running, deadline=10.0), "進行中が打ち切られた"
    finally:
        service.stop()

    rows = HistoryRepository(trigger_root).read_history(_month())
    assert len(rows) == 1, f"行が 1 本でない: {rows}"
    assert rows[0].status == "ok", "削除で進行中の実行が確定しなかった"
    assert rows[0].trigger_id == "t1", "削除後にトリガーが追えない（値コピーでない）"


# --------------------------------------------------------------------------------------
# T091 FR-029・FR-042a (c)・SC-022: `PUT`／`PATCH` が同居する監視を巻き添えにしない
# --------------------------------------------------------------------------------------


def _fired(stub: StubKickoffRunner, trigger_id: str, filename: str) -> bool:
    """`file` 入口の発火に「そのトリガー × その投下ファイル」の組が在るか。"""
    return any(
        ctx.trigger_snapshot is not None
        and ctx.trigger_snapshot.trigger_id == trigger_id
        and ctx.dropped_path is not None
        and filename in ctx.dropped_path
        for ctx in list(stub.calls)
    )


def test_editing_or_disabling_one_file_trigger_keeps_the_other_on_the_same_folder(
    trigger_root: Path, downloads_dir: Path
) -> None:
    """T091: `_deactivate()` を通る `PUT`・`PATCH` が**同居する監視を殺さない**。

    `DELETE`・`PATCH`・`PUT` はいずれも `_deactivate()` →`WatcherRepository.remove()` を通る。
    `unschedule()` は `ObservedWatch`（`(path, recursive)` が等しければ同一物）に紐づく
    handler を**全部**落とすので、trigger 単位で外さないと同じフォルダを見る他のトリガーが
    **黙って**拾わなくなる（`GET /watcher` は `alive=True` のままなので口からも読めない）。
    """
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions(
        [
            _definition(trigger_id="wa", name="監視 A", type="file", spec="."),
            _definition(trigger_id="wb", name="監視 B", type="file", spec="."),
        ]
    )
    service = _service(
        trigger_root,
        stub,
        config=_config(stable_seconds=1, poll_interval_sec=0.2),
        downloads_dir=downloads_dir,
    )
    service.start()
    try:
        assert {entry.trigger_id for entry in service.watcher_entries} == {"wa", "wb"}
        (downloads_dir / "before.bin").write_bytes(b"payload")
        assert _wait_until(
            lambda: _fired(stub, "wa", "before.bin") and _fired(stub, "wb", "before.bin")
        ), "2 件とも拾えていない（前提が成り立たない）"

        # `PUT`: `wa` を編集する（`_deactivate` → `_activate`）
        service.update_trigger("wa", _spec_body(name="監視 A 改", type="file", spec="."))

        (downloads_dir / "after_put.bin").write_bytes(b"payload")
        assert _wait_until(lambda: _fired(stub, "wb", "after_put.bin")), (
            "wa の PUT で wb の監視まで止まった"
        )
        assert _wait_until(lambda: _fired(stub, "wa", "after_put.bin")), "PUT した wa が拾わない"

        # `PATCH`: `wa` を無効化する（`_deactivate` だけが働く）
        service.toggle_trigger("wa", enabled=False)

        (downloads_dir / "after_patch.bin").write_bytes(b"payload")
        assert _wait_until(lambda: _fired(stub, "wb", "after_patch.bin")), (
            "wa の PATCH で wb の監視まで止まった"
        )
        assert not _fired(stub, "wa", "after_patch.bin"), "無効化した wa が発火した"

        # 生死が実態と一致する（拾えている wb だけが残り、`alive=True` を見せる）
        assert [(entry.trigger_id, entry.alive) for entry in service.watcher_entries] == [
            ("wb", True)
        ]
    finally:
        service.stop()


# --------------------------------------------------------------------------------------
# T092 FR-033 段 3・SC-017・US1 AC6・N-6: `file` 入口の進行中 kickoff も停止列が待ち切る
# --------------------------------------------------------------------------------------


def test_stop_waits_for_a_file_kickoff_in_flight(trigger_root: Path, downloads_dir: Path) -> None:
    """T092: `settle.stop()` の `join` が `file` 入口の kickoff を**打ち切らない**。

    `test_sc017_stop_waits_for_the_kickoff_in_flight` は **timer 入口**
    （`scheduler.shutdown(wait=True)`）しか見ないので、この経路は緑のまま出荷されていた。

    観測点は 2 つ: (1) 進行中が残っているのに `is_alive()` が `False` を返さない
    （＝ `self._thread` を `join` の**前**に落とさない。落とすと打ち切りが口からもログからも
    読めない）。(2) 履歴の行が **1 本のまま `ok` で確定する**。
    """
    stub = StubKickoffRunner(mode="slow", seconds=3.0)
    DefinitionsRepository(trigger_root).save_definitions(
        [_definition(trigger_id="w", type="file", spec=".")]
    )
    service = _service(
        trigger_root,
        stub,
        config=_config(stable_seconds=1, poll_interval_sec=0.2),
        downloads_dir=downloads_dir,
    )
    service.start()
    settle = service._settle  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]  # 停止列の**途中**の生死は口から読めない
    assert settle is not None
    stopped = threading.Event()

    def _stop() -> None:
        try:
            service.stop()
        finally:
            stopped.set()

    (downloads_dir / "in_flight.bin").write_bytes(b"payload")
    assert _wait_until(lambda: stub.entered.is_set()), "完了検知が kickoff へ入らない"

    began = time.monotonic()
    stopper = threading.Thread(target=_stop, name="trigger-test-stopper", daemon=True)
    stopper.start()
    torn: list[float] = []
    while not stopped.wait(0.05):
        if not settle.is_alive() and service.running:
            torn.append(time.monotonic() - began)
    stopper.join(timeout=DEADLINE)
    elapsed = time.monotonic() - began

    assert not stopper.is_alive(), "stop() が返らない"
    assert torn == [], f"進行中の kickoff が残っているのに is_alive() が False: {torn}"
    assert elapsed >= 2.0, f"進行中の file kickoff を待たずに止めた: {elapsed:.3f} 秒"
    assert not settle.is_alive()
    rows = HistoryRepository(trigger_root).read_history(_month())
    assert [row.status for row in rows] == ["ok"], f"履歴の行が 1 本で確定していない: {rows}"
