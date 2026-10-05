"""雛形生成（contracts/area-a-loader.md §5・FR-016・FR-018・US3 AC-3／AC-4）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from settings_testing import scaffold, write

from settings import defaults_path
from settings.repository import AreaAScaffolder

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED = (
    "global/model.conf",
    "global/agents.yaml",
    "global/tasks.yaml",
    "global/llm_profiles.yaml",
    "global/commands.yaml",
    "global/humans.yaml",
)


def test_generates_six_files_and_two_folders(settings_root: Path) -> None:
    """生成物は **6 件**。`global/`・`projects/` の 2 フォルダも用意する。"""
    created = scaffold(settings_root)
    assert set(created) == set(EXPECTED)
    assert (settings_root / "global").is_dir()
    assert (settings_root / "projects").is_dir()


def test_does_not_generate_limits(settings_root: Path) -> None:
    """**`limits.yaml` を生成しない**（生成すると CFG-39 の ③ 段が永久に効かなくなる）。"""
    scaffold(settings_root)
    assert not (settings_root / "global/limits.yaml").exists()


def test_does_not_generate_project_files(settings_root: Path) -> None:
    """**PJ ごとのファイルを生成しない**（プロジェクトの登録は F11＝後半）。"""
    scaffold(settings_root)
    assert list((settings_root / "projects").iterdir()) == []


def test_copies_the_bundled_defaults_verbatim(settings_root: Path) -> None:
    """`model.conf`・`agents.yaml`・`tasks.yaml` は `defaults/` の写しである。"""
    scaffold(settings_root)
    for name in ("model.conf", "agents.yaml", "tasks.yaml"):
        source = (defaults_path() / name).read_text(encoding="utf-8")
        assert (settings_root / "global" / name).read_text(encoding="utf-8") == source


def test_generates_empty_skeletons(settings_root: Path) -> None:
    """`llm_profiles.yaml`／`commands.yaml`／`humans.yaml` は**空の骨格**（R-17・FR-016 (d)）。"""
    scaffold(settings_root)
    assert (settings_root / "global/llm_profiles.yaml").read_text("utf-8") == (
        "profiles: {}\ndefault: null\n"
    )
    assert (settings_root / "global/commands.yaml").read_text("utf-8") == "commands: {}\n"
    assert (settings_root / "global/humans.yaml").read_text("utf-8") == "human_actors: []\n"


def test_existing_files_are_never_overwritten(settings_root: Path) -> None:
    """US3 AC-4・FR-016: **既存を上書きしない**。

    `os.replace` は既存を**黙って上書きする**ため、`exists()` の分岐が唯一の防壁である（M-24）。
    """
    scaffold(settings_root)
    write(settings_root, "global/agents.yaml", "# 手で書いた印\n")
    created = scaffold(settings_root)
    assert created == ()
    assert (settings_root / "global/agents.yaml").read_text("utf-8") == "# 手で書いた印\n"


def test_generation_only_touches_absent_files(settings_root: Path) -> None:
    """2 回目の生成では既存 5 件の更新時刻が変わらない（不在の 1 件だけを作る）。"""
    scaffold(settings_root)
    (settings_root / "global/humans.yaml").unlink()
    kept = [rel for rel in EXPECTED if "humans" not in rel]
    before = {rel: (settings_root / rel).stat().st_mtime_ns for rel in kept}
    created = scaffold(settings_root)
    assert created == ("global/humans.yaml",)
    after = {rel: (settings_root / rel).stat().st_mtime_ns for rel in before}
    assert after == before


def test_line_endings_are_normalized_to_lf(settings_root: Path) -> None:
    """M-26: 生成物の改行は `\\n` に揃える（`newline="\\n"` を明示する）。"""
    scaffold(settings_root)
    for rel in EXPECTED:
        assert b"\r\n" not in (settings_root / rel).read_bytes()


def test_no_temporary_file_is_left_behind(settings_root: Path) -> None:
    """M-23: 一時ファイル → 置き換えの手順で、残骸を残さない。"""
    scaffold(settings_root)
    names = {path.name for path in (settings_root / "global").iterdir()}
    assert names == {rel.removeprefix("global/") for rel in EXPECTED}


def test_missing_defaults_aborts_before_any_write(settings_root: Path) -> None:
    """同梱既定が読めなければ**書込に入る前に**中断する（生成物が 1 件も残らない）。

    `_planned()` はタプルを組む時点で `defaults/` を 3 件とも読むため、この経路は
    `_write_if_absent` に一度も到達しない。**片付け経路の担保にはならない**ので、
    それは下の `test_failure_during_write_cleans_up_the_temporary_file` が受け持つ。
    """
    scaffolder = AreaAScaffolder(settings_root, settings_root / "存在しない既定")
    with pytest.raises(OSError, match="No such file"):
        scaffolder.ensure()
    assert list((settings_root / "global").iterdir()) == []


def test_failure_during_write_cleans_up_the_temporary_file(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-018: **書込中に失敗しても**一時ファイルを残さない（契約 §5 手順 4）。

    `os.replace` に `OSError` を注入して、`_write_if_absent` の `except OSError:` →
    `temporary.unlink(missing_ok=True)` → `raise` を実際に通す。ここを通さないと、
    片付け経路は 1 度も実行されないまま緑になる。
    """

    def _fail(src: object, dst: object) -> None:
        raise OSError(28, "書込に失敗した")

    monkeypatch.setattr("settings.repository.os.replace", _fail)
    scaffolder = AreaAScaffolder(settings_root, defaults_path())
    with pytest.raises(OSError, match="書込に失敗した"):
        scaffolder.ensure()
    assert list((settings_root / "global").iterdir()) == []
    assert list((settings_root / "projects").iterdir()) == []


def test_logs_relative_paths_only(settings_root: Path, caplog: pytest.LogCaptureFixture) -> None:
    """FR-051: `settings.scaffold.created` の `extra` は**領域 A ルート相対のパスだけ**。"""
    with caplog.at_level(logging.INFO, logger="settings.repository"):
        scaffold(settings_root)
    records = [item for item in caplog.records if item.getMessage() == "settings.scaffold.created"]
    assert len(records) == len(EXPECTED)
    rels = {getattr(item, "rel", "") for item in records}
    assert rels == set(EXPECTED)
    assert not any(str(settings_root) in str(rel) for rel in rels)
