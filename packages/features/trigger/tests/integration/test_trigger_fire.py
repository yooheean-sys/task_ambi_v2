"""F10 発火の 1 経路（`fire()` の 7 段）と F09 手動実行。

正本は [contracts/fire-pipeline.md](../../../../specs/011-trigger/contracts/fire-pipeline.md)
§2・§3・§7。**安全側に倒さない**（N-1〜N-6）ことがこのファイルの主題である。

4 入口（`timer`・`file`・`manual`・`hooks`）はすべて `fire()` に合流する。**入口ごとに別の
経路を作らない**（P5D-041）。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from conftest import StubKickoffRunner

from common.interfaces.orchestrator import LaunchKind, TriggerSnapshot
from trigger import TriggerConfig, TriggerDeps
from trigger.exceptions import TriggerNotFound
from trigger.models import LaunchCond, TriggerDef
from trigger.repository import DefinitionsRepository, HistoryRepository
from trigger.service import TriggerService

if TYPE_CHECKING:
    from pathlib import Path

    from trigger.models import RunHistoryRow

HEX32 = 32
DEADLINE = 20.0

SNAPSHOT = TriggerSnapshot(trigger_id="t1", name="速い巡回", type="interval")


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
    return TriggerService(
        deps=TriggerDeps(kickoff_runner=stub),
        config=config if config is not None else _config(),
        definitions=DefinitionsRepository(root),
        history=HistoryRepository(root),
        downloads_dir=downloads_dir,
    )


def _month() -> str:
    return datetime.now(UTC).strftime("%Y%m")


def _rows(root: Path) -> list[RunHistoryRow]:
    return HistoryRepository(root).read_history(_month())


def _wait_until(predicate: Any, deadline: float = DEADLINE) -> bool:  # noqa: ANN401
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------------------
# T039 骨格 3 本（contracts/fire-pipeline.md §2）
# --------------------------------------------------------------------------------------


def test_a_successful_fire_writes_one_row_that_moves_from_started_to_ok(
    trigger_root: Path,
) -> None:
    """段 5 → 段 6 → 段 7: 開始行が 1 本立ち、**同じ `run_id` の行**が `ok` で埋まる（SC-002）。"""
    stub = StubKickoffRunner(results=("closed",), notes=("ひとこと",))
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    rows = _rows(trigger_root)
    assert len(rows) == 1, f"行が 2 本になった: {rows}"
    only = rows[0]
    assert only.status == "ok"
    assert only.kickoff_id == "k-ok"
    assert only.trigger_id == "t1"
    assert only.trigger_name == "速い巡回"
    assert only.trigger_type == "interval"
    assert only.kind == "timer"
    assert only.finished_at is not None
    assert only.duration_sec is not None
    assert only.notes == "ひとこと"


def test_the_started_row_exists_before_the_kickoff_returns(trigger_root: Path) -> None:
    """段 5 は段 6 の**前**に走る（プロセスが落ちても `started` の行が残る＝ US1 AC5）。"""
    seen: list[str] = []
    stub = StubKickoffRunner(mode="slow", seconds=1.0)
    service = _service(trigger_root, stub)

    import threading

    thread = threading.Thread(
        target=lambda: service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())
    )
    thread.start()
    assert _wait_until(lambda: stub.entered.is_set())
    seen.extend(row.status for row in _rows(trigger_root))
    thread.join()

    assert seen == ["started"], f"kickoff の最中に開始行が無い: {seen}"


def test_kickoff_busy_becomes_a_busy_row_and_is_never_swallowed(trigger_root: Path) -> None:
    """N-2・US1 AC4: `KickoffBusy` は **`status="busy"` の 1 行**にする（握りつぶさない）。"""
    stub = StubKickoffRunner(mode="busy", current_kickoff_id="k-running")
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    rows = _rows(trigger_root)
    assert len(rows) == 1
    only = rows[0]
    assert only.status == "busy"
    assert only.notes == "busy: current_kickoff_id=k-running"
    assert only.kickoff_id is None, "busy なのに kickoff_id が入っている"
    assert only.issues_ok is None
    assert only.issues_failing is None


def test_any_other_exception_becomes_a_stopped_row_and_is_not_re_raised(
    trigger_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """N-4・FR-035: その他の `Exception` は `status="stopped"`。

    **再送出しない**（ジョブは残す）。
    """
    stub = StubKickoffRunner(mode="boom")
    service = _service(trigger_root, stub)

    with caplog.at_level(logging.ERROR, logger="trigger.service"):
        service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    rows = _rows(trigger_root)
    assert len(rows) == 1
    assert rows[0].status == "stopped"
    assert rows[0].kickoff_id is None
    failures = [record for record in caplog.records if record.message == "kickoff_failed"]
    assert failures, "構造化ログに残っていない"
    assert failures[0].exc_info is not None, "exc_info が付いていない"


def test_base_exception_is_not_caught(trigger_root: Path) -> None:
    """`BaseException`（`KeyboardInterrupt`・`SystemExit`・`RunHalted`）は**捕まえない**（Z-6）。"""

    class _Halting:
        def run(self, ctx: object) -> Any:  # noqa: ANN401
            del ctx
            raise KeyboardInterrupt

    service = TriggerService(
        deps=TriggerDeps(kickoff_runner=_Halting()),  # pyright: ignore[reportArgumentType]  # Protocol に構造適合するテスト用スタブ
        config=_config(),
        definitions=DefinitionsRepository(trigger_root),
        history=HistoryRepository(trigger_root),
        downloads_dir=None,
    )

    with pytest.raises(KeyboardInterrupt):
        service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())


# --------------------------------------------------------------------------------------
# T040 畳み方と 7 段の細目
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("results", "expected_ok", "expected_failing"),
    [
        (("closed",), 1, 0),
        (("failing",), 0, 1),
        (("unsafe",), 0, 1),
        (("halted",), 0, 1),
        (("released",), 0, 0),
        (("escalated",), 0, 0),
        (("closed", "failing", "unsafe", "halted", "released", "escalated"), 1, 3),
    ],
    ids=["closed", "failing", "unsafe", "halted", "released", "escalated", "all_six"],
)
def test_the_six_issue_results_fold_into_two_counters(
    trigger_root: Path, results: tuple[str, ...], expected_ok: int, expected_failing: int
) -> None:
    """§3・SD-10: `closed` → `issues_ok`／`failing`・`unsafe`・`halted` → `issues_failing`。

    **`released`・`escalated` はどちらにも数えない**（まだ終わっていない）。したがって
    `issues_ok + issues_failing < len(issues)` になりうる（**欠落ではなく仕様**）。
    """
    stub = StubKickoffRunner(results=results)
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    only = _rows(trigger_root)[0]
    assert only.issues_ok == expected_ok
    assert only.issues_failing == expected_failing
    if len(results) > 1:
        assert (only.issues_ok or 0) + (only.issues_failing or 0) < len(results)


def test_notes_are_joined_with_slashes_and_never_truncated(trigger_root: Path) -> None:
    """§3: `notes` は `" / "` 連結で**切り詰めない**（カンマ・改行は csv の引用に任せる）。"""
    notes = ("a,b", "改行\nを含む", "長" * 400)
    stub = StubKickoffRunner(notes=notes)
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    assert _rows(trigger_root)[0].notes == " / ".join(notes)


def test_finished_at_is_taken_by_the_caller_and_duration_is_derived(
    trigger_root: Path,
) -> None:
    """`finished_at` は**呼び手側で採る**。

    `duration_sec = (finished_at - started_at).total_seconds()`。
    """
    stub = StubKickoffRunner(mode="slow", seconds=0.3)
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    only = _rows(trigger_root)[0]
    assert only.finished_at is not None
    assert only.duration_sec is not None
    assert only.duration_sec >= 0.3
    assert only.duration_sec == pytest.approx(
        (only.finished_at - only.started_at).total_seconds(), abs=0.01
    )


def test_started_at_is_both_the_history_value_and_the_requested_at(trigger_root: Path) -> None:
    """C-05: 段 3 の `started_at` が履歴の `started_at` と `requested_at` の**両方**になる。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    assert stub.calls[0].requested_at == _rows(trigger_root)[0].started_at


def test_the_execution_context_carries_all_five_fields(trigger_root: Path) -> None:
    """段 4: `ExecutionContext` の 5 欄を組む（`claim_filter` は起動条件の写像）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    service.fire(
        SNAPSHOT,
        LaunchKind.file,
        condition=LaunchCond(labels=("role:executor", "kind:task"), project_key="pj"),
        dropped_path="/tmp/x.bin",  # noqa: S108  # 値の写しだけを見る（ファイルに触らない）
    )

    ctx = stub.calls[0]
    assert ctx.kind == LaunchKind.file
    assert ctx.trigger_snapshot == SNAPSHOT
    assert ctx.dropped_path == "/tmp/x.bin"  # noqa: S108  # 同上
    assert ctx.claim_filter.labels == frozenset({"role:executor", "kind:task"})
    assert ctx.claim_filter.project_key == "pj"


def test_the_kickoff_still_happens_when_the_start_row_cannot_be_written(
    trigger_root: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-1・SC-004・FR-055: **開始行が書けなくても kickoff は 1 回起きる**（止めない）。

    段 5 が失敗していたら段 7 は**書換えを試みずログだけ**にする。

    ⚠ 「呼ばない」を `finalize` の **spy で**固定する（T100 (a)）—— `fire()` の
    `if started_written:` を外して無条件に呼ぶ形へ戻しても、`finalize` は `path.is_file()` が
    偽で `history_finalize_skipped` を出して返るだけなので、`stub.count` と
    `history_start_failed` の 2 つだけを見ていると**このテストは緑のまま**になる。
    """
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    finalize_calls: list[str] = []
    real = HistoryRepository.finalize

    def _spy(self: HistoryRepository, run_id: str, month: str, patch: Any) -> None:  # noqa: ANN401
        finalize_calls.append(run_id)
        real(self, run_id, month, patch)

    monkeypatch.setattr("trigger.repository.history.HistoryRepository.finalize", _spy)
    trigger_root.chmod(0o500)  # 読み取り専用にする（history/ を作れない）
    try:
        with caplog.at_level(logging.WARNING, logger="trigger.service"):
            service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())
    finally:
        trigger_root.chmod(0o700)

    assert stub.count == 1, "開始行が書けないと kickoff まで止まった"
    assert "history_start_failed" in [record.message for record in caplog.records]
    assert finalize_calls == [], "段 5 が失敗しているのに段 7 が書換えを試みている"


def test_the_month_is_taken_from_the_start_not_from_the_finish(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-4: 段 7 の月は**段 3 の `started_at` から**引き回す（`finished_at` から導出しない）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    seen: list[str] = []
    real = HistoryRepository.finalize

    def _spy(self: HistoryRepository, run_id: str, month: str, patch: Any) -> None:  # noqa: ANN401
        seen.append(month)
        real(self, run_id, month, patch)

    monkeypatch.setattr("trigger.repository.history.HistoryRepository.finalize", _spy)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    assert seen == [_rows(trigger_root)[0].started_at.strftime("%Y%m")]


def test_the_running_counter_goes_up_and_down(trigger_root: Path) -> None:
    """`WatcherState.running`（進行中の `fire` があるか）は入口/出口で増減する。"""
    import threading

    stub = StubKickoffRunner(mode="slow", seconds=1.0)
    service = _service(trigger_root, stub)
    assert service.running is False

    thread = threading.Thread(
        target=lambda: service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())
    )
    thread.start()
    try:
        assert _wait_until(lambda: service.running is True), "進行中に running が立たない"
    finally:
        thread.join()

    assert service.running is False


# --------------------------------------------------------------------------------------
# T041 相関 ID（SC-018・R-12）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", [LaunchKind.timer, LaunchKind.file, LaunchKind.manual], ids=["timer", "file", "manual"]
)
def test_a_correlation_id_is_issued_for_every_entry_point(
    trigger_root: Path, kind: LaunchKind
) -> None:
    """SC-018: **4 入口すべて**で発番され、履歴の行と構造化ログに**同じ値**で載る。

    （`hooks` は `kind=file` に畳むので、`kind` としては 3 通りである＝ P5D-041）
    """
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, kind, condition=LaunchCond())

    only = _rows(trigger_root)[0]
    assert len(only.correlation_id) == HEX32
    assert only.correlation_id == stub.correlation_ids[0], (
        "kickoff の最中に束縛されていた値と履歴の値が違う"
    )


def test_two_fires_on_the_same_worker_get_different_correlation_ids(
    trigger_root: Path,
) -> None:
    """R-12: **同じワーカーへ 2 回続けて `submit` すると 2 つの値が異なる**。

    ⚠ `finally` の `reset` を怠るとスレッドの使い回しで前回の ID が漏れる。
    """
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions(
        [
            TriggerDef(
                trigger_id="t1",
                name="手で起こす",
                type="interval",
                spec="3600",
                condition=LaunchCond(),
            )
        ]
    )
    service = _service(trigger_root, stub)
    service.start()
    try:
        service.run_manual("t1")
        service.run_manual("t1")

        assert _wait_until(lambda: stub.count >= 2), "2 回目が走らない"
    finally:
        service.stop()

    ids = [row.correlation_id for row in _rows(trigger_root)]
    assert len(set(ids)) == 2, f"前回の相関 ID が漏れている: {ids}"
    assert len(set(stub.correlation_ids)) == 2


def test_the_correlation_id_is_unbound_after_the_fire(trigger_root: Path) -> None:
    """段 2〜7 は `try/finally` で囲み、`finally` で束縛を解く。"""
    from common.kernel.log import CorrelationContext

    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    before = CorrelationContext.current()

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    assert CorrelationContext.current() == before


def test_the_correlation_id_is_not_on_the_protocol_arguments(trigger_root: Path) -> None:
    """D08-d: **Protocol の引数には載せない**（`ExecutionContext` に欄が無い）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)

    service.fire(SNAPSHOT, LaunchKind.timer, condition=LaunchCond())

    assert "correlation_id" not in type(stub.calls[0]).model_fields


# --------------------------------------------------------------------------------------
# T049 F09 手動実行（US4）
# --------------------------------------------------------------------------------------


def test_run_manual_returns_a_run_id_without_waiting(trigger_root: Path) -> None:
    """FR-049・US4 AC1: **待たされずに** `run_id` が返る（専用ワーカーへ `submit` して即返す）。"""
    stub = StubKickoffRunner(mode="slow", seconds=2.0)
    DefinitionsRepository(trigger_root).save_definitions(
        [
            TriggerDef(
                trigger_id="t1",
                name="手動",
                type="interval",
                spec="3600",
                condition=LaunchCond(),
            )
        ]
    )
    service = _service(trigger_root, stub)
    service.start()
    try:
        began = time.monotonic()
        run_id = service.run_manual("t1")
        elapsed = time.monotonic() - began

        assert len(run_id) == HEX32
        assert elapsed < 0.5, f"受付が待たされた: {elapsed:.3f} 秒"
        assert _wait_until(lambda: stub.count >= 1)
    finally:
        service.stop()


def test_run_manual_adds_one_history_row_with_kind_manual(trigger_root: Path) -> None:
    """US4 AC2: 少し後に履歴が **`kind="manual"` で 1 行**増える（`run_id` が一致する）。"""
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions(
        [
            TriggerDef(
                trigger_id="t1",
                name="手動",
                type="interval",
                spec="3600",
                condition=LaunchCond(),
            )
        ]
    )
    service = _service(trigger_root, stub)
    service.start()
    try:
        run_id = service.run_manual("t1")

        assert _wait_until(lambda: len(_rows(trigger_root)) == 1)
    finally:
        service.stop()

    only = _rows(trigger_root)[0]
    assert only.run_id == run_id, "202 で返した run_id と履歴の行が結ばれていない"
    assert only.kind == "manual"
    assert only.status == "ok"


def test_run_manual_of_an_unknown_trigger_is_a_not_found(trigger_root: Path) -> None:
    """US4 AC3: 不明 id は `TriggerNotFound`（口では 404）。"""
    stub = StubKickoffRunner()
    service = _service(trigger_root, stub)
    service.start()
    try:
        with pytest.raises(TriggerNotFound):
            service.run_manual("nope")
    finally:
        service.stop()


def test_a_disabled_trigger_can_still_be_run_by_hand(trigger_root: Path) -> None:
    """契約 §2.7: **`enabled=false` のトリガーも手で起こせる**（`openapi.yaml` に 422 が無い）。"""
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions(
        [
            TriggerDef(
                trigger_id="off",
                name="止めてある",
                type="interval",
                spec="3600",
                condition=LaunchCond(),
                enabled=False,
            )
        ]
    )
    service = _service(trigger_root, stub)
    service.start()
    try:
        assert "off" not in service.job_ids, "前提: 無効なので登録されていない"

        service.run_manual("off")

        assert _wait_until(lambda: stub.count >= 1), "無効なトリガーを手で起こせない"
    finally:
        service.stop()


def test_the_file_hook_joins_the_same_fire(trigger_root: Path, downloads_dir: Path) -> None:
    """FR-040: 退避経路（`hooks`）も**同じ完了検知を経て同じ `fire(kind=file)`** へ合流する。"""
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions(
        [
            TriggerDef(
                trigger_id="w",
                name="投下監視",
                type="file",
                spec=".",
                condition=LaunchCond(),
            )
        ]
    )
    service = _service(
        trigger_root,
        stub,
        config=_config(stable_seconds=1, poll_interval_sec=0.2, watcher_enabled=False),
        downloads_dir=downloads_dir,
    )
    service.start()
    try:
        target = downloads_dir / "hooked.bin"
        target.write_bytes(b"payload")

        matched = service.accept_file_hook(str(target))
        assert matched == ["w"]
        assert _wait_until(lambda: stub.count >= 1), "退避経路だけで発火しない（SC-008）"
    finally:
        service.stop()

    only = _rows(trigger_root)[0]
    assert only.kind == "file", "hooks が file に畳まれていない"
    assert only.dropped_path == str(target.resolve())


def test_the_file_hook_fires_synchronously_when_the_daemon_is_not_running(
    trigger_root: Path, downloads_dir: Path
) -> None:
    """T090: **常駐が起きていなくても発火する**（`run_manual` の退避と対称）。

    完了検知の専用スレッドが無いとき、投入を黙って飛ばすと口は 202 ＋ `matched_trigger_ids`
    を返しながら 1 度も発火しない。同期で `fire(kind=file)` へ合流させる。
    """
    stub = StubKickoffRunner()
    DefinitionsRepository(trigger_root).save_definitions(
        [
            TriggerDef(
                trigger_id="w",
                name="投下監視",
                type="file",
                spec=".",
                condition=LaunchCond(),
            )
        ]
    )
    service = _service(trigger_root, stub, downloads_dir=downloads_dir)  # start() を呼ばない
    target = downloads_dir / "hooked.bin"
    target.write_bytes(b"payload")

    matched = service.accept_file_hook(str(target))

    assert matched == ["w"]
    assert stub.count == 1, "常駐が起きていないと黙って握りつぶしている"
    only = _rows(trigger_root)[0]
    assert only.kind == "file", "hooks が file に畳まれていない"
    assert only.dropped_path == str(target.resolve())
