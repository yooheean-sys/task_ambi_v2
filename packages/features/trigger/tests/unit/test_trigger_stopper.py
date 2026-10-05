"""T-1: 止め木そのものの検査（plan (ix)・(v) の ⚠）。

**このファイルが落ちたら他のテストは実運用に触れうる。** trigger の止め木は settings より
被害が 1 段重い —— 外れると

- 実運用の領域 B（`<TRIGGER_ROOT>`）に `triggers.yaml` の雛形と履歴 csv が書かれ、
- **実運用の Windows Downloads に `PollingObserver` が張り付き、投下ファイルで実際に
  kickoff が起きうる**。

止め木は `tests/conftest.py` の autouse fixture `_isolate_real_area_b` であり、効くのは
`trigger.env_file_path`（module 関数）・`TriggerEnv` の明示 `__init__`・`build()` 側の
`TriggerEnv(_env_file=env_file_path())` の **3 つが対で**在るときだけである（R-13）。
"""

from __future__ import annotations

import os

import trigger
from common.kernel.config import find_repo_root


def test_t1_env_file_path_is_replaced_by_a_temporary_file() -> None:
    """(1) `trigger.env_file_path` が**一時パス**を返す（実運用の `.env` を指していない）。"""
    replaced = trigger.env_file_path()
    real = find_repo_root() / ".env"

    assert replaced != real, (
        "止め木が効いていない: trigger.env_file_path() が実リポジトリの .env を指している。"
        f" 返り値: {replaced}"
    )
    assert replaced.read_text(encoding="utf-8") == "", (
        f"止め木の .env が空でない（実値を持つ .env を掴んでいる可能性）: {replaced}"
    )


def test_t1_trigger_root_and_downloads_dir_are_out_of_the_environment() -> None:
    """(2) `TRIGGER_ROOT`・`DOWNLOADS_DIR` が**環境変数から外れている**。

    `.env` を差し替えても OS 環境変数が残っていれば pydantic-settings はそちらを採る
    （解決順序は init > OS 環境変数 > `.env`）。両方を外して初めて止め木になる。
    """
    assert "TRIGGER_ROOT" not in os.environ, (
        "止め木が効いていない: TRIGGER_ROOT が OS 環境変数に残っている"
        f"（値: {os.environ.get('TRIGGER_ROOT')}）"
    )
    assert "DOWNLOADS_DIR" not in os.environ, (
        "止め木が効いていない: DOWNLOADS_DIR が OS 環境変数に残っている"
        f"（値: {os.environ.get('DOWNLOADS_DIR')}）"
    )
