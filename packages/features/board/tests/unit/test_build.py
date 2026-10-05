"""起動時の 5 検査と組み立て（US5・FR-047〜049・SC-009）。

**検査順は a → b → c → d → e で、最初に失敗したものだけを報告する**。
`.env` と `config.json` の所在は `board.env_file_path`／`board.config_path` を差し替えて隔離する
（実リポジトリの `.env` が `BEADS_DIR` を持つため、差し替えないと検査 a を再現できない）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import board
from board import BoardConfig, BoardDeps, build
from board.exceptions import (
    BoardBinaryError,
    BoardConfigError,
    BoardEnvError,
    BoardStartupError,
    BoardWorkspaceError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

BD_STUB = sys.executable
"""実行可能であることだけが要る（起動はしない＝ FR-048）。"""


@pytest.fixture
def beads_dir(tmp_path: Path) -> Path:
    """`.beads` の体裁を持つ一時ディレクトリ（`bd init` は**しない**＝ D19-d）。"""
    workspace = tmp_path / "ws"
    directory = workspace / ".beads"
    directory.mkdir(parents=True)
    (directory / "config.yaml").write_text("project: boardtest\n", encoding="utf-8")
    return directory


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """`.env` を空ファイルに差し替え、OS 環境変数からも 2 キーを外す。"""
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(board, "env_file_path", lambda: empty_env)
    monkeypatch.delenv("BEADS_DIR", raising=False)
    monkeypatch.delenv("BD_BIN", raising=False)
    yield empty_env


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動時検査が `bd` を 1 度も起動しないことの止め木（FR-048・R-15）。"""

    def forbidden(*args: object, **kwargs: object) -> None:
        msg = f"起動時検査が subprocess を起動した: {args!r}"
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.mark.usefixtures("isolated", "no_subprocess")
class TestStartupChecks:
    """FR-047 の 5 検査。失敗の記述から**見に行った絶対パス**が読める（SC-009）。"""

    def test_a_missing_beads_dir_key(self, isolated: Path) -> None:
        with pytest.raises(BoardEnvError) as exc:
            build(BoardDeps())
        message = str(exc.value)
        assert "BEADS_DIR" in message
        assert str(isolated) in message

    def test_b_workspace_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing = tmp_path / "no-such" / ".beads"
        monkeypatch.setenv("BEADS_DIR", str(missing))
        monkeypatch.setenv("BD_BIN", BD_STUB)
        with pytest.raises(BoardWorkspaceError) as exc:
            build(BoardDeps())
        assert str(missing.resolve()) in str(exc.value)

    def test_b_workspace_is_a_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        as_file = tmp_path / "beads-file"
        as_file.write_text("", encoding="utf-8")
        monkeypatch.setenv("BEADS_DIR", str(as_file))
        monkeypatch.setenv("BD_BIN", BD_STUB)
        with pytest.raises(BoardWorkspaceError):
            build(BoardDeps())

    def test_c_config_yaml_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`BEADS_DIR` は `.beads` 自身であって親ではない（D19-a／D19-b）。"""
        parent = tmp_path / "ws"
        (parent / ".beads").mkdir(parents=True)
        monkeypatch.setenv("BEADS_DIR", str(parent))
        monkeypatch.setenv("BD_BIN", BD_STUB)
        with pytest.raises(BoardWorkspaceError) as exc:
            build(BoardDeps())
        message = str(exc.value)
        assert str((parent / "config.yaml").resolve()) in message
        assert ".beads" in message

    def test_d_bd_bin_cannot_be_resolved(
        self, beads_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BEADS_DIR", str(beads_dir))
        monkeypatch.setenv("BD_BIN", "/nonexistent/path/to/bd")
        with pytest.raises(BoardBinaryError) as exc:
            build(BoardDeps())
        assert "/nonexistent/path/to/bd" in str(exc.value)

    def test_d_bd_bin_is_not_executable(
        self, beads_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        not_executable = tmp_path / "bd-not-exec"
        not_executable.write_text("", encoding="utf-8")
        not_executable.chmod(0o644)
        monkeypatch.setenv("BEADS_DIR", str(beads_dir))
        monkeypatch.setenv("BD_BIN", str(not_executable))
        with pytest.raises(BoardBinaryError):
            build(BoardDeps())

    def test_e_unknown_key_in_config_json(
        self, beads_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = tmp_path / "config.json"
        bad.write_text(json.dumps({"timeout_sec": 120, "unknown_key": 1}), encoding="utf-8")
        monkeypatch.setattr(board, "config_path", lambda: bad)
        monkeypatch.setenv("BEADS_DIR", str(beads_dir))
        monkeypatch.setenv("BD_BIN", BD_STUB)
        with pytest.raises(BoardConfigError) as exc:
            build(BoardDeps())
        message = str(exc.value)
        assert str(bad) in message
        assert "unknown_key" in message

    def test_e_wrong_type_in_config_json(
        self, beads_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = tmp_path / "config.json"
        bad.write_text(json.dumps({"timeout_sec": "しばらく"}), encoding="utf-8")
        monkeypatch.setattr(board, "config_path", lambda: bad)
        monkeypatch.setenv("BEADS_DIR", str(beads_dir))
        monkeypatch.setenv("BD_BIN", BD_STUB)
        with pytest.raises(BoardConfigError):
            build(BoardDeps())

    def test_only_the_first_failure_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """b と d が同時に壊れていても b だけを報告する（原因が 1 つに定まる）。"""
        monkeypatch.setenv("BEADS_DIR", str(tmp_path / "missing"))
        monkeypatch.setenv("BD_BIN", "/nonexistent/bd")
        with pytest.raises(BoardWorkspaceError):
            build(BoardDeps())

    def test_startup_errors_share_one_base(self) -> None:
        assert issubclass(BoardEnvError, BoardStartupError)
        assert issubclass(BoardWorkspaceError, BoardStartupError)
        assert issubclass(BoardBinaryError, BoardStartupError)
        assert issubclass(BoardConfigError, BoardStartupError)


@pytest.mark.usefixtures("isolated", "no_subprocess")
class TestAssembly:
    """組み立ての不変条件（FR-003〜005・FR-049）。"""

    @pytest.fixture(autouse=True)
    def _valid_environment(
        self, isolated: Path, beads_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`isolated`（2 キーを外す）の**後**に設定する（fixture の依存で順序を固定する）。"""
        assert isolated.exists()
        monkeypatch.setenv("BEADS_DIR", str(beads_dir))
        monkeypatch.setenv("BD_BIN", BD_STUB)

    def test_call_shape_from_registry_is_unchanged(self) -> None:
        """`registry.py` の `build_board(BoardDeps())` は変わらない（FR-056）。"""
        handle = build(BoardDeps())
        assert handle is not None

    def test_reader_and_writer_are_the_same_instance(self) -> None:
        """FR-005・R-07: 別インスタンスだと `BdCli` のロックが割れる。"""
        handle = build(BoardDeps())
        assert handle.board_reader is handle.board_writer

    def test_router_is_none(self) -> None:
        """board は HTTP 経路を持たない（FR-001・FR-004）。"""
        assert build(BoardDeps()).router is None

    def test_config_can_be_overridden(self) -> None:
        """FR-049・DIFF-08: 統合テストクラスタが挙動設定を差し替えられる。"""
        handle = build(BoardDeps(), config=BoardConfig(timeout_sec=1, lock_wait_max_attempts=1))
        assert handle.board_reader is handle.board_writer

    def test_overridden_config_skips_reading_config_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        override = BoardConfig(timeout_sec=5)  # 差し替え値は**壊す前**に組む
        broken = tmp_path / "broken.json"
        broken.write_text('{"unknown_key": 1}', encoding="utf-8")
        monkeypatch.setattr(board, "config_path", lambda: broken)
        handle = build(BoardDeps(), config=override)
        assert handle.router is None

    def test_deps_is_empty(self) -> None:
        """FR-003: board は他モジュールの契約に依存しない。"""
        assert BoardDeps() == BoardDeps()

    def test_build_never_runs_bd(self) -> None:
        """FR-048・R-15: `bd version` を打たず `shutil.which` ＋ `os.access` で確かめる。"""
        build(BoardDeps())
