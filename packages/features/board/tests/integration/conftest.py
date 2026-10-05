"""実 `bd` に対する統合テストの土台（一時 `BEADS_DIR`）。

⚠ **リポジトリ内で `bd init` しない**（D19-d・FR-061・SC-014）。`bd init` は `.beads/` だけでなく
git リポジトリを作り commit まで行い、`AGENTS.md`・`CLAUDE.md`・`.claude/`・`.codex/`・`.agents/`
も生成する（M-10）。`tmp_path` の既定 basetemp は `/tmp/pytest-of-<user>/…`＝リポジトリ外だが、
`TMPDIR` がリポジトリ内を指す環境で事故らないよう **`assert` の止め木**を必ず通す。
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404  外部ツールは公約インターフェース（CLI）経由でのみ触る
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from board import BoardConfig, BoardDeps, BoardHandle, build
from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from collections.abc import Iterator

    from common.interfaces.board import BoardReader, BoardWriter

pytestmark = pytest.mark.requires_bd

ACTOR = "demo_pj.programmer"
"""`.` を含む actor 名も無改変で読み戻る（M-01）。"""

PROJECT_KEY = "boardtest"


@pytest.fixture(scope="session")
def bd_bin() -> str:
    """PATH 上の `bd`（無ければ skip）。"""
    resolved = shutil.which(os.environ.get("BD_BIN", "bd"))
    if resolved is None:
        pytest.skip('bd が PATH 上に無い（`uv run pytest -m "not requires_bd"` で単体だけ回せる）')
    return resolved


@pytest.fixture
def beads_dir(tmp_path: Path, bd_bin: str) -> Iterator[Path]:
    """一時ワークスペースに `bd init` した `.beads` を渡し、テスト後に片付ける。"""
    assert find_repo_root() not in tmp_path.parents, f"一時 dir がリポジトリ内にある: {tmp_path}"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(  # noqa: S603  bd_bin は fixture が解決した実行ファイル
        [bd_bin, "init", "-p", PROJECT_KEY],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    yield workspace / ".beads"
    # `.git` と embedded Dolt を明示的に消す（pytest は直近 3 回分の tmp を保持する）
    shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def handle(beads_dir: Path, bd_bin: str, monkeypatch: pytest.MonkeyPatch) -> BoardHandle:
    """`build()` を通した実配線（`BEADS_DIR`／`BD_BIN` は `monkeypatch.setenv` で差し替える）。"""
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))
    monkeypatch.setenv("BD_BIN", bd_bin)
    return build(BoardDeps(), config=BoardConfig(timeout_sec=120))


@pytest.fixture
def reader(handle: BoardHandle) -> BoardReader:
    """`BoardReader` として使う（憲章 §3: Protocol 型を注釈する）。"""
    board_reader: BoardReader = handle.board_reader
    return board_reader


@pytest.fixture
def writer(handle: BoardHandle) -> BoardWriter:
    """`BoardWriter` として使う（`reader` と同一インスタンス＝ FR-005）。"""
    board_writer: BoardWriter = handle.board_writer
    return board_writer
