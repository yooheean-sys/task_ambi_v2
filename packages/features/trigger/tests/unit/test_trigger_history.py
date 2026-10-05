"""F02 実行履歴 csv（`repository/history.py`）。

**番号（`H-1`〜`H-9`）の正本は `specs/011-trigger/contracts/fire-pipeline.md`
§4「約束」**である（plan (iii) の同名の表はその写し）。期待値は契約からの写しであり、ここで
発明しない。

⚠ 改行は**バイト列で**確かめる —— 文字列で読むと universal newlines が CRLF を `\\n` に畳んで
しまい、**テストが緑のまま CRLF が出荷される**（R-10）。
"""

from __future__ import annotations

import ast
import csv
import hashlib
import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from trigger.models import HISTORY_COLUMNS, RunHistoryRow
from trigger.repository.history import HistoryRepository

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_COLUMNS = (
    "run_id",
    "kickoff_id",
    "trigger_id",
    "trigger_name",
    "trigger_type",
    "kind",
    "started_at",
    "finished_at",
    "duration_sec",
    "status",
    "issues_ok",
    "issues_failing",
    "notes",
    "dropped_path",
    "correlation_id",
)
"""`openapi.yaml` の `HistoryRow` の properties の並び（C-03・SD-09）。**15 列**。"""

STARTED_AT = datetime(2026, 8, 27, 1, 2, 3, tzinfo=UTC)
MONTH = "202608"


def _repo(root: Path) -> HistoryRepository:
    return HistoryRepository(root)


def _row(run_id: str = "r1", **overrides: object) -> RunHistoryRow:
    fields: dict[str, object] = {
        "run_id": run_id,
        "trigger_id": "t1",
        "trigger_name": "ready 巡回",
        "trigger_type": "interval",
        "kind": "timer",
        "started_at": STARTED_AT,
        "status": "started",
        "correlation_id": "c" * 32,
    }
    fields.update(overrides)
    return RunHistoryRow.model_validate(fields)


def _digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()


# -- 15 列と書式（H-5・H-6） -------------------------------------------------------------


def test_the_columns_are_the_openapi_order_and_there_are_fifteen() -> None:
    """列順は `openapi.yaml` の `HistoryRow` の並び（**15 列**）。"""
    assert HISTORY_COLUMNS == EXPECTED_COLUMNS
    assert len(HISTORY_COLUMNS) == len(EXPECTED_COLUMNS)


def test_the_file_has_a_header_row(trigger_root: Path) -> None:
    """ヘッダ行がある（DM-24）。"""
    repo = _repo(trigger_root)

    repo.append_start(_row())

    first = repo.path_for(MONTH).read_text(encoding="utf-8").splitlines()[0]
    assert first.split(",") == list(EXPECTED_COLUMNS)


def test_the_line_terminator_is_lf_and_there_is_no_bom(trigger_root: Path) -> None:
    """H-5: 改行 `\\n`・BOM なし。**バイト列で**確かめる（R-10）。"""
    repo = _repo(trigger_root)

    repo.append_start(_row())
    repo.append_start(_row("r2"))

    data = repo.path_for(MONTH).read_bytes()
    assert data.count(b"\r\n") == 0, "CRLF が出荷されている（lineterminator の指定漏れ）"
    assert not data.startswith(b"\xef\xbb\xbf"), "BOM が付いている"
    assert data.count(b"\n") == 3, "ヘッダ + 2 行にならない"


def test_none_becomes_an_empty_string_and_comes_back_as_none(trigger_root: Path) -> None:
    """H-6: `None` → 空文字、読み戻しで空文字 → `None`。"""
    repo = _repo(trigger_root)

    repo.append_start(_row())

    raw = list(csv.DictReader(repo.path_for(MONTH).read_text(encoding="utf-8").splitlines()))
    assert raw[0]["kickoff_id"] == ""
    assert raw[0]["issues_ok"] == ""

    read_back = repo.read_history(MONTH)
    assert read_back[0].kickoff_id is None
    assert read_back[0].issues_ok is None
    assert read_back[0].finished_at is None


def test_datetimes_round_trip_as_utc_isoformat(trigger_root: Path) -> None:
    """H-6: `datetime` は `isoformat()`（UTC・`+00:00`）で往復する。"""
    repo = _repo(trigger_root)

    repo.append_start(_row())

    raw = list(csv.DictReader(repo.path_for(MONTH).read_text(encoding="utf-8").splitlines()))
    assert raw[0]["started_at"] == "2026-08-27T01:02:03+00:00"
    assert repo.read_history(MONTH)[0].started_at == STARTED_AT


def test_notes_with_commas_and_newlines_survive_the_csv_quoting(trigger_root: Path) -> None:
    """`notes` は `" / "` 連結で**切り詰めない**。カンマ・改行は csv の引用に任せる。"""
    repo = _repo(trigger_root)
    notes = "a,b / 改行\nを含む / " + "長" * 500
    repo.append_start(_row())

    repo.finalize("r1", MONTH, {"status": "ok", "notes": notes})

    assert repo.read_history(MONTH)[0].notes == notes


# -- 2 段書込（H-1〜H-4・H-9） ------------------------------------------------------------


def test_append_start_then_finalize_keeps_exactly_one_row(trigger_root: Path) -> None:
    """SC-002: 1 実行につき行は**ちょうど 1 本**（開始 → 確定で 2 本にならない）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row())

    repo.finalize(
        "r1",
        MONTH,
        {
            "kickoff_id": "k1",
            "finished_at": datetime(2026, 8, 27, 1, 2, 8, tzinfo=UTC),
            "duration_sec": 5.0,
            "status": "ok",
            "issues_ok": 2,
            "issues_failing": 1,
            "notes": "x / y",
        },
    )

    rows = repo.read_history(MONTH)
    assert len(rows) == 1
    only = rows[0]
    assert only.run_id == "r1"
    assert only.kickoff_id == "k1"
    assert only.status == "ok"
    assert only.issues_ok == 2
    assert only.issues_failing == 1
    assert only.duration_sec == 5.0


def test_the_match_is_by_run_id_not_by_offset(trigger_root: Path) -> None:
    """H-1: 照合は **`run_id`**（行番号・オフセットを覚えない）。"""
    repo = _repo(trigger_root)
    for run_id in ("r1", "r2", "r3"):
        repo.append_start(_row(run_id))

    repo.finalize("r2", MONTH, {"status": "busy"})

    rows = {row.run_id: row.status for row in repo.read_history(MONTH)}
    assert rows == {"r1": "started", "r2": "busy", "r3": "started"}


def test_finalize_of_an_unknown_run_id_changes_nothing(trigger_root: Path) -> None:
    """H-1: 一致が 0 件なら**書換えを試みずログだけ**（開始行が書けなかった run）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row())
    before = _digest(repo.path_for(MONTH))

    repo.finalize("unknown", MONTH, {"status": "ok"})

    assert _digest(repo.path_for(MONTH)) == before


def test_finalize_of_a_missing_month_file_changes_nothing(trigger_root: Path) -> None:
    """開始行のファイルすら無ければ何もしない（段 5 が失敗していた場合）。"""
    repo = _repo(trigger_root)

    repo.finalize("r1", MONTH, {"status": "ok"})

    assert not repo.path_for(MONTH).exists()


def test_a_run_that_crosses_a_month_boundary_is_written_back_to_the_start_month(
    trigger_root: Path,
) -> None:
    """H-4: 月は `started_at` から決め、**開始行のあるファイル**へ書き戻す（FR-057）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row("r1", started_at=datetime(2026, 8, 31, 23, 59, 0, tzinfo=UTC)))

    repo.finalize(
        "r1",
        "202608",
        {"finished_at": datetime(2026, 9, 1, 0, 30, 0, tzinfo=UTC), "status": "ok"},
    )

    assert not repo.path_for("202609").exists(), "新しい月に孤児の行ができた"
    assert repo.read_history("202608")[0].status == "ok"


def test_thirty_writes_from_three_threads_are_serialised(trigger_root: Path) -> None:
    """H-3: `threading.Lock` **1 つ**で直列化する（発火は 3 系統のスレッドから来る＝ FR-054）。"""
    repo = _repo(trigger_root)
    total = 30

    def _work(offset: int) -> None:
        for index in range(total // 3):
            run_id = f"r{offset}-{index}"
            repo.append_start(_row(run_id))
            repo.finalize(run_id, MONTH, {"status": "ok"})

    threads = [threading.Thread(target=_work, args=(offset,)) for offset in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = repo.read_history(MONTH)
    assert len(rows) == total, f"行数が合わない: {len(rows)}"
    assert {row.run_id for row in rows} == {
        f"r{offset}-{index}" for offset in range(3) for index in range(total // 3)
    }
    assert all(row.status == "ok" for row in rows), "確定が取りこぼされた"


def test_a_failing_replace_leaves_the_history_file_byte_identical(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-9・SC-005: 書換えの途中で失敗しても中身は**1 バイトも変わらない**（旧ファイルが残る）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row())
    before = _digest(repo.path_for(MONTH))

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "置換に失敗"
        raise OSError(msg)

    monkeypatch.setattr("trigger.repository.history.os.replace", _boom)

    repo.finalize("r1", MONTH, {"status": "ok"})

    assert _digest(repo.path_for(MONTH)) == before
    assert not repo.temp_path_for(MONTH).exists(), "一時ファイルが残っている"


def test_a_failing_replace_logs_the_finalized_values(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """H-9・FR-056: `os.replace` に失敗したら**確定値を構造化ログに残す**（再送出しない）。

    ⚠ 対になる `test_a_failing_replace_leaves_the_history_file_byte_identical` は
    バイト同一性（SC-005）と一時ファイルの消去しか見ないので、`extra` から `patch` を落としても
    緑のまま出荷される（T100 (b)）。FR-055 側（`history_start_failed`）はログを見ているので、
    **片側だけが固定されていない**状態だった。行に書けなかった確定値は**ここにしか残らない**。
    """
    repo = _repo(trigger_root)
    repo.append_start(_row())

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "置換に失敗"
        raise OSError(msg)

    monkeypatch.setattr("trigger.repository.history.os.replace", _boom)

    with caplog.at_level(logging.WARNING, logger="trigger.repository.history"):
        repo.finalize("r1", MONTH, {"status": "ok", "issues_ok": 2})

    failed = [record for record in caplog.records if record.message == "history_finalize_failed"]
    assert len(failed) == 1, "置換に失敗したのに記録が残らない"
    assert getattr(failed[0], "run_id", None) == "r1"
    assert getattr(failed[0], "month", None) == MONTH
    assert getattr(failed[0], "patch", None) == {"status": "ok", "issues_ok": 2}, (
        "確定値が載っていない（行にも書けていないので、この値はどこからも読めなくなる）"
    )


def test_the_temp_file_lives_in_the_same_directory_as_the_history_file(
    trigger_root: Path,
) -> None:
    """H-2・R-11: 一時ファイルは**置換先と同じディレクトリ**（`EXDEV` を避ける）。"""
    repo = _repo(trigger_root)

    assert repo.temp_path_for(MONTH).parent == repo.path_for(MONTH).parent
    assert repo.temp_path_for(MONTH).name == ".history_202608.csv.tmp"


# -- 読取（表示のためだけ。H-8・FR-058） ---------------------------------------------------


def test_read_history_of_a_month_with_no_file_is_an_empty_list(trigger_root: Path) -> None:
    """まだ 1 度も実行していない月は**空リスト**（SD-13・F-09。404 にしない）。"""
    assert _repo(trigger_root).read_history("209912") == []


def test_last_result_of_finds_the_newest_row_for_the_trigger(trigger_root: Path) -> None:
    """`last_result_of` は `trigger_id` 一致で**末尾から**拾う（`GET /triggers` の表示用）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row("r1", trigger_id="t1"))
    repo.append_start(_row("r2", trigger_id="t2"))
    repo.append_start(_row("r3", trigger_id="t1"))
    repo.finalize("r3", MONTH, {"status": "ok"})

    latest = repo.last_result_of("t1", MONTH)

    assert latest is not None
    assert latest.run_id == "r3"
    assert latest.status == "ok"


def test_last_result_of_an_unknown_trigger_is_none(trigger_root: Path) -> None:
    """一致が無ければ `None`（一覧では `last_result` が `null` になる）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row("r1", trigger_id="t1"))

    assert repo.last_result_of("nope", MONTH) is None


def test_the_history_directory_is_created_on_demand(trigger_root: Path) -> None:
    """`history/` が無ければ作る（`append_start` の入口）。"""
    repo = _repo(trigger_root)
    assert not (trigger_root / "history").exists()

    repo.append_start(_row())

    assert (trigger_root / "history").is_dir()


def test_history_files_are_never_deleted_automatically(trigger_root: Path) -> None:
    """H-7・FR-051: **自動削除しない**（古い月のファイルは書込後も残る）。"""
    repo = _repo(trigger_root)
    repo.append_start(_row("old", started_at=datetime(2020, 1, 1, tzinfo=UTC)))

    repo.append_start(_row("new", started_at=datetime(2026, 8, 1, tzinfo=UTC)))

    assert repo.path_for("202001").exists()
    assert repo.path_for("202608").exists()


# -- FR-058: 読み戻しは表示のみ（憲章 §2・N-3。⚠ 分岐に使った瞬間に憲章違反） ------------


def test_the_history_is_only_read_from_the_two_display_paths() -> None:
    """H-8・FR-058: `read_history`／`last_result_of` の呼び手が**表示 2 経路だけ**である。

    観測点は「`trigger/` の src 全体を AST で走査し、この 2 つのメソッドの呼び出し元が
    `service/views.py`（`GET /triggers` の `last_result`）と `service/__init__.py` の
    `read_history`（`GET /history`）に限られること」。**`if`・`while`・三項・`assert` の条件に
    使っている箇所が 0 件**であることまで見る（分岐に使った瞬間に憲章 §2 違反）。
    """
    from pathlib import Path as _Path

    import trigger

    src = _Path(trigger.__file__).parent
    callers: dict[str, set[str]] = {}
    in_condition: list[str] = []

    for path in sorted(src.rglob("*.py")):
        if path.name == "history.py":
            continue  # 所有者自身（`last_result_of` が `read_history` を呼ぶ）は対象外
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for call in _history_calls(node):
                callers.setdefault(call, set()).add(path.name)
            in_condition.extend(
                f"{path.name}:{getattr(node, 'lineno', 0)}"
                for _ in _history_calls_in_condition(node)
            )

    # `GET /history` の経路だけ（`router.py` の口 → `service/__init__.py` の 1 メソッド）
    assert callers.get("read_history", set()) <= {"router.py", "__init__.py"}, (
        f"read_history の呼び手が表示経路の外に出た: {callers.get('read_history')}"
    )
    assert callers.get("last_result_of", set()) <= {"views.py"}, (
        f"last_result_of の呼び手が表示経路の外に出た: {callers.get('last_result_of')}"
    )
    assert not in_condition, f"履歴の読み戻しを分岐に使っている（憲章 §2 違反）: {in_condition}"


def _history_calls(node: ast.AST) -> list[str]:
    """`node` が履歴読取の呼び出しならメソッド名を返す。"""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("read_history", "last_result_of")
    ):
        return [node.func.attr]
    return []


def _history_calls_in_condition(node: ast.AST) -> list[str]:
    """`node` の**条件式**の中にある履歴読取の呼び出しを返す。"""
    tests: list[ast.expr] = []
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        tests.append(node.test)
    found: list[str] = []
    for test in tests:
        found.extend(call for child in ast.walk(test) for call in _history_calls(child))
    return found
