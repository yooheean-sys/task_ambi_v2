"""apps/api のテスト基盤 fixture（偽 LLM サーバ・外部ツール bd）。FR-025・FR-027・SC-007。

`_support` は ルート pyproject.toml の `[tool.pytest.ini_options] pythonpath = ["apps/api/tests"]`
により import できる。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from _support.fake_llm_server import FakeLlmServer

BD_COMMAND = "bd"
ENV_INFO_HINT = (
    "bd の導入手順と所在はリポジトリ外の環境情報ファイル（x3_notes §6-4「外部ツールの所在」）"
    "を参照すること。"
)


@pytest.fixture
def fake_llm_server() -> Iterator[Callable[[list[dict[str, Any]]], FakeLlmServer]]:
    """台本を渡すと偽 LLM サーバを起動して返す（関数スコープ。終了時に shutdown する）。"""
    started: list[FakeLlmServer] = []

    def _start(script: list[dict[str, Any]]) -> FakeLlmServer:
        server = FakeLlmServer(script)
        started.append(server)
        return server

    yield _start

    for server in started:
        server.shutdown()


@pytest.fixture
def bd_bin() -> str:
    """bd 実行ファイルの絶対パス。BD_BIN 環境変数 → PATH 探索の順で解決する。

    見つからない場合は、追加調査なしに原因を特定できるだけの情報を出して失敗する（SC-007）。
    """
    env_value = os.environ.get("BD_BIN")
    if env_value:
        if os.path.isfile(env_value) and os.access(env_value, os.X_OK):
            return env_value
        pytest.fail(
            f"BD_BIN に指定された実行ファイルが使えない: {env_value}\n"
            f"  探したコマンド名: {BD_COMMAND}\n"
            f"  BD_BIN の値: {env_value}\n"
            f"  {ENV_INFO_HINT}"
        )

    resolved = shutil.which(BD_COMMAND)
    if resolved:
        return resolved

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    listed = "\n".join(f"    - {entry}" for entry in path_entries if entry)
    pytest.fail(
        f"外部ツール '{BD_COMMAND}' が見つからない。\n"
        f"  探したコマンド名: {BD_COMMAND}\n"
        f"  BD_BIN の値: 未設定\n"
        f"  探索した PATH:\n{listed}\n"
        f"  {ENV_INFO_HINT}\n"
        f'  bd を持たない環境で土台の検証だけを回す場合: uv run pytest -m "not requires_bd"'
    )
