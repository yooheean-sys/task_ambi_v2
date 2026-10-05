"""`build()` → 9 メソッド通しと、スナップショットの原子性・世代・実行時 FS 非接触。

US1 AC-16・US7・SC-005・SC-016。**settings ＋ 実ファイルシステム（一時領域 A）の噛み合い**に
限る（前半は HTTP 経路も UI も持たず、層契約により `apps` を import できない＝ C-5）。
実配線での `/health` の確認は quickstart S8 が手作業 1 行で行う。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    make_snapshot,
    mtimes,
    stub_deps,
    write,
    write_minimal_area_a,
    write_policy,
)

from common.interfaces.settings import ProjectNotFound, UnknownCommand
from common.kernel.enums import PersonaKey
from settings import SettingsHandle, build
from settings.service import SettingsService

if TYPE_CHECKING:
    from pathlib import Path

COMMANDS = "commands:\n  pytest:\n    argv:\n      - uv\n      - run\n      - pytest\n"
ROWS = [
    "p, member:pj.programmer, tool:read_issue, execute",
    "p, member:pj.programmer, cmd:pytest, execute",
]


@pytest.fixture
def handle(settings_root: Path, monkeypatch: pytest.MonkeyPatch) -> SettingsHandle:
    """一時領域 A に対して実際に組み立てた `SettingsHandle`。"""
    write_minimal_area_a(settings_root)
    write(settings_root, "global/commands.yaml", COMMANDS)
    write(settings_root, "projects/pj/rules.md", "# ルール\n")
    write(
        settings_root,
        "projects/pj/hooks.yaml",
        "hooks:\n  - name: guard\n    points: PRE_TOOL_CALL\n",
    )
    write_policy(settings_root, "pj", ROWS)
    monkeypatch.setenv("SETTINGS_ROOT", str(settings_root))
    return build(stub_deps())


def _sample(handle: SettingsHandle) -> dict[str, object]:
    """9 メソッドを一通り呼び、応答を比較できる形にまとめる。"""
    reader, resolver = handle.settings_reader, handle.member_resolver
    return {
        "project": reader.get_project("pj").model_dump(),
        "limits": reader.get_limits("pj").model_dump(),
        "rules": reader.get_rules("pj").model_dump(),
        "hooks": reader.get_hooks("pj").model_dump(),
        "tasks": reader.get_tasks("pj").model_dump(),
        "member": resolver.resolve("pj", PersonaKey.programmer).model_dump(),
        "is_human": resolver.is_human("pj.programmer"),
        "enforce": handle.authorizer.enforce("member:pj.programmer", "tool:read_issue", "execute"),
        "command": handle.command_registry.resolve_command("pytest"),
    }


def test_all_nine_methods_answer_after_build(handle: SettingsHandle) -> None:
    """SC-001: 4 契約・9 メソッドが実物の領域 A に対して通しで応答する。"""
    sample = _sample(handle)
    assert sample["is_human"] is False
    assert sample["enforce"] is True
    assert sample["command"] == ["uv", "run", "pytest"]
    with pytest.raises(ProjectNotFound):
        handle.settings_reader.get_project("nope")
    with pytest.raises(UnknownCommand):
        handle.command_registry.resolve_command("rm")


def test_answers_do_not_change_when_area_a_is_broken(
    handle: SettingsHandle, settings_root: Path
) -> None:
    """US1 AC-16・SC-005: 領域 A を壊しても応答が**壊す前と 1 バイトも変わらない**。"""
    before = _sample(handle)
    for path in sorted(settings_root.rglob("*")):
        if path.is_file():
            path.write_text("壊した\n", encoding="utf-8")
    assert _sample(handle) == before


def test_no_writes_outside_of_scaffolding(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-016・US7 AC-4: build と 9 メソッドの前後で更新時刻が**1 つも変わらない**。

    雛形生成は事前に済ませてあるので、ここで観測するのは「それ以外に書込が無い」ことである。
    """
    write_minimal_area_a(settings_root)
    write(settings_root, "global/commands.yaml", COMMANDS)
    write_policy(settings_root, "pj", ROWS)
    monkeypatch.setenv("SETTINGS_ROOT", str(settings_root))
    before = mtimes(settings_root)
    built = build(stub_deps())
    assert mtimes(settings_root) == before, "build が雛形生成以外の書込を行った"
    _sample(built)
    assert mtimes(settings_root) == before, "9 メソッドがファイルを書き換えた"


def test_generation_starts_at_one_for_every_service(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SD-28: 世代は **`SettingsService` ごとに 1 から**始まる（`build()` ごとではない）。

    空の領域 A には PJ が無く `get_tasks` から世代を観測できないため、PJ 1 件を置いた
    一時領域 A で見る（quickstart S2 の申し送り）。
    """
    write_minimal_area_a(settings_root)
    monkeypatch.setenv("SETTINGS_ROOT", str(settings_root))
    first = build(stub_deps())
    second = build(stub_deps())
    assert first.settings_reader.get_tasks("pj").generation == 1
    assert second.settings_reader.get_tasks("pj").generation == 1
    assert first.settings_reader is not second.settings_reader


def _service_of(handle: SettingsHandle) -> SettingsService:
    """差し替えを起こす経路は前半に無いので、**公開メソッド**を持つ実装体まで絞る。

    `_snapshot` には触らない（R-07 が棄却した形。後半で公開したときにテストの書き換えが要る）。
    差し替える候補は `make_snapshot()` が同じ領域 A から組み直す。
    """
    service = handle.settings_reader
    assert isinstance(service, SettingsService)
    return service


def test_replace_snapshot_is_atomic_for_readers(
    handle: SettingsHandle, settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """US7 AC-1: 差し替えの前後で読取は**どちらか一方の世代に必ず属し**、混ざらない。"""
    service = _service_of(handle)
    old = service.get_tasks("pj")
    candidate = dataclasses.replace(make_snapshot(settings_root), generation=2)
    with caplog.at_level(logging.INFO, logger="settings.service"):
        service.replace_snapshot(candidate)
    new = service.get_tasks("pj")
    assert old.generation == 1
    assert new.generation == 2
    assert new.templates.keys() == old.templates.keys()
    assert "settings.snapshot.replaced" in caplog.text


def test_replace_snapshot_requires_monotonic_generations(
    handle: SettingsHandle, settings_root: Path
) -> None:
    """US7 AC-3: 世代が増えない差し替えは**内部の不変条件の破れ**として拒否する。"""
    service = _service_of(handle)
    candidate = make_snapshot(settings_root)
    for generation in (1, 0):
        with pytest.raises(ValueError, match="単調増加"):
            service.replace_snapshot(dataclasses.replace(candidate, generation=generation))
    assert service.get_tasks("pj").generation == 1


def test_snapshot_cannot_be_mutated(settings_root: Path) -> None:
    """US7 AC-2・FR-024: スナップショットの中身を変えようとするとできない。"""
    write_minimal_area_a(settings_root)
    snapshot = make_snapshot(settings_root)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.generation = 99  # pyright: ignore[reportAttributeAccessIssue]  # frozen の観測
    with pytest.raises(TypeError):
        snapshot.projects["新規"] = snapshot.projects["pj"]  # pyright: ignore[reportIndexIssue]  # MappingProxyType


def test_returned_values_are_immutable(handle: SettingsHandle) -> None:
    """FR-024: 返る受け渡し型も frozen である（呼び手が書き換えられない）。"""
    from pydantic import ValidationError  # noqa: PLC0415

    project = handle.settings_reader.get_project("pj")
    with pytest.raises(ValidationError):
        project.name = "書き換え"  # pyright: ignore[reportAttributeAccessIssue]  # frozen の観測
