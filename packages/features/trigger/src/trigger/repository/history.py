"""F02 実行履歴 csv（`<TRIGGER_ROOT>/history/history_YYYYMM.csv`）の 2 段書込と読取。

**「1 実行 ＝ 1 行」**（P5D-043）—— 開始時に `status="started"` の行を追記し、終了時に**同じ
`run_id` の行**を確定値で埋める。憲章 §2 例外 (b) の現物が「開始時に書いた行を終了時に確定値で
埋める書換え」を明示的に許している（C-01）。

**ロジックはこの csv を読み戻さない**（H-8・FR-013・FR-058）。読むのは表示の 2 経路
（`GET /triggers` の `last_result` と `GET /history`）**だけ**で、**分岐に使った瞬間に憲章 §2
違反**になる。

書式は H-5: **15 列・ヘッダあり・UTF-8（BOM なし）・改行 LF**。ファイルは `newline=""` で
開き、`csv.writer(..., lineterminator=LINE_TERMINATOR)` を**明示する**
（`csv` の既定は CRLF＝ R-10）。
"""

from __future__ import annotations

import csv
import io
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from common.kernel.log import LoggerFactory
from trigger.models import HISTORY_COLUMNS, RunHistoryRow

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

HISTORY_DIRNAME = "history"
LINE_TERMINATOR = "\n"
"""H-5・R-10: `csv.writer` の既定は CRLF なので**明示する**。"""

_logger = LoggerFactory.get_logger("trigger.repository.history")


def month_of(moment: datetime) -> str:
    """`YYYYMM`（**`started_at` から決める**＝ H-4。`finished_at` から導出しない）。

    Args:
        moment: 開始時刻。

    Returns:
        月の識別子。
    """
    return moment.strftime("%Y%m")


class HistoryRepository:
    """履歴 csv のアダプタ（F02）。**`threading.Lock` 1 つ**で全操作を直列化する（H-3）。"""

    def __init__(self, root: Path) -> None:
        """領域 B のルートを受け取る。

        Args:
            root: `<TRIGGER_ROOT>`。履歴は `<root>/history/` に置く。
        """
        self._dir = root / HISTORY_DIRNAME
        self._lock = threading.Lock()

    def path_for(self, month: str) -> Path:
        """その月の履歴ファイルの絶対パス。"""
        return self._dir / f"history_{month}.csv"

    def temp_path_for(self, month: str) -> Path:
        """書換えの一時ファイル（**置換先と同じディレクトリ**＝ H-2・R-11）。"""
        return self._dir / f".history_{month}.csv.tmp"

    def append_start(self, row: RunHistoryRow) -> None:
        """開始行を追記する（`status="started"`・未確定欄は空）。

        Args:
            row: 開始時点の 15 列（未確定欄は `None`）。

        Raises:
            OSError: 書けなかった。**呼び手（`fire()` の段 5）は捕まえて先へ進む**
                （kickoff を止めない＝ N-1・FR-055・SC-004）。
        """
        month = month_of(row.started_at)
        path = self.path_for(month)
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            exists = path.is_file()
            with path.open("a", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator=LINE_TERMINATOR)
                if not exists:
                    writer.writerow(HISTORY_COLUMNS)
                writer.writerow(_to_cells(row))

    def finalize(self, run_id: str, month: str, patch: Mapping[str, object]) -> None:
        """同じ `run_id` の行を確定値で埋める（**1 件だけ**。H-1）。

        ファイル全体を一時ファイルへ書き `os.replace` する（H-2。部分書換えは行長が変わると
        壊れる）。**一致が 0 件なら書換えを試みずログだけ**にする（開始行が書けなかった run）。
        置換に失敗したら**旧ファイルが残る**（H-9・SC-005）。

        Args:
            run_id: 照合の鍵（行番号・オフセットを覚えない）。
            month: **開始行のあるファイル**の月（`fire()` が段 3 の `started_at` から引き回す）。
            patch: 上書きする欄。
        """
        path = self.path_for(month)
        temp = self.temp_path_for(month)
        with self._lock:
            if not path.is_file():
                _logger.warning(
                    "history_finalize_skipped", extra={"run_id": run_id, "month": month}
                )
                return
            try:
                rows = _read_rows(path)
            except OSError as exc:
                _logger.warning(
                    "history_finalize_failed",
                    extra={"run_id": run_id, "month": month, "error": str(exc)},
                )
                return
            matched = [row for row in rows if row.get("run_id") == run_id]
            if not matched:
                _logger.warning(
                    "history_finalize_skipped", extra={"run_id": run_id, "month": month}
                )
                return
            matched[0].update({key: _to_cell(value) for key, value in patch.items()})
            try:
                _write_all(temp, rows)
                os.replace(temp, path)
            except OSError as exc:
                # H-9: `os.replace` の前に落ちれば置換されない＝**中身は 1 バイトも変わらない**。
                # 確定値を構造化ログに残す（FR-056）。**再送出しない**（呼び手は段 7）。
                _logger.warning(
                    "history_finalize_failed",
                    extra={
                        "run_id": run_id,
                        "month": month,
                        "error": str(exc),
                        "patch": dict(patch),
                    },
                )
            finally:
                temp.unlink(missing_ok=True)

    def read_history(self, month: str) -> list[RunHistoryRow]:
        """その月の履歴を読む（**表示のためだけ**＝ H-8・FR-058）。

        Args:
            month: `YYYYMM`。

        Returns:
            宣言順（追記順）の行。**無い月は空リスト**（SD-13。404 にしない）。
        """
        path = self.path_for(month)
        with self._lock:
            if not path.is_file():
                return []
            rows = _read_rows(path)
        return [_from_cells(row) for row in rows]

    def last_result_of(self, trigger_id: str, month: str) -> RunHistoryRow | None:
        """そのトリガーの直近 1 行（**表示のためだけ**＝ FR-058）。

        Args:
            trigger_id: 探すトリガー。
            month: `YYYYMM`。

        Returns:
            末尾から探した最初の一致（無ければ `None`）。
        """
        for row in reversed(self.read_history(month)):
            if row.trigger_id == trigger_id:
                return row
        return None


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Csv を全行読む（`newline=""` で開く＝ H-5）。"""
    with path.open("r", encoding="utf-8", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _write_all(temp: Path, rows: list[dict[str, str]]) -> None:
    """全行を一時ファイルへ書く（ヘッダ ＋ 15 列・改行 LF）。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(HISTORY_COLUMNS), lineterminator=LINE_TERMINATOR
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in HISTORY_COLUMNS})
    with temp.open("w", encoding="utf-8", newline="") as stream:
        stream.write(buffer.getvalue())


def _to_cells(row: RunHistoryRow) -> list[str]:
    """`RunHistoryRow` を 15 個のセルへ写す（`None` → 空文字＝ H-6）。"""
    dumped = row.model_dump()
    return [_to_cell(dumped[column]) for column in HISTORY_COLUMNS]


def _to_cell(value: object) -> str:
    """1 つの値をセルへ写す（`None` → 空文字・`datetime` → `isoformat()`）。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _from_cells(cells: Mapping[str, str]) -> RunHistoryRow:
    """セルから `RunHistoryRow` を組む（空文字 → `None`＝ H-6）。"""
    fields: dict[str, Any] = {
        column: (None if cells.get(column, "") == "" else cells.get(column))
        for column in HISTORY_COLUMNS
    }
    return RunHistoryRow.model_validate(fields)
