"""`SettingsReader` の 5 メソッド（IF-23〜26・IF-50。settings-F03〜F06・F25）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    config_for_tests,
    make_service,
    mtimes,
    write,
    write_minimal_area_a,
    write_project,
)

from common.interfaces.settings import ProjectNotFound
from common.kernel.enums import PersonaKey
from settings.models import SCOPE_GLOBAL, TASK_KEYS

if TYPE_CHECKING:
    from pathlib import Path

LIMITS_BODY = """\
global:
  max_requests_per_kickoff: 10
  max_cost_per_kickoff: 2.5
overrides:
  pj:
    max_requests_per_kickoff: 3
    max_cost_per_kickoff: 0.5
"""


# -- get_project（IF-23・F03） ---------------------------------------------------------


def test_get_project_returns_key_name_folders_and_reserved(settings_root: Path) -> None:
    """US1 AC-1: キー・名前・3 種のフォルダ対応付け・予約かどうかが返る。"""
    write_minimal_area_a(settings_root)
    project = make_service(settings_root).get_project("pj")
    assert project.project_key == "pj"
    assert project.name == "デモ"
    assert project.folders.pj_input_dir == "pj"
    assert project.folders.outcome_dir == "pj/outcome"
    assert project.folders.role_dirs == {PersonaKey.programmer: "pj/roles/programmer"}
    assert project.is_reserved is False


def test_get_project_raises_for_unknown_key(settings_root: Path) -> None:
    """US1 AC-2: 未登録キーは**既定に倒さず**契約例外にする。"""
    write_minimal_area_a(settings_root)
    with pytest.raises(ProjectNotFound):
        make_service(settings_root).get_project("nope")


def test_reserved_project_is_not_special_cased(settings_root: Path) -> None:
    """US1 AC-3・P5D-014: 予約 PJ `_program` も未登録なら他と同じ扱いである。"""
    write_minimal_area_a(settings_root)
    with pytest.raises(ProjectNotFound):
        make_service(settings_root).get_project("_program")


def test_reserved_flag_is_read_from_the_declaration(settings_root: Path) -> None:
    """`is_reserved` は宣言から読む（キー名から推定しない）。"""
    write_minimal_area_a(settings_root, projects=("_program",))
    body = (settings_root / "projects/_program/project.yaml").read_text(encoding="utf-8")
    write(settings_root, "projects/_program/project.yaml", body.replace("false", "true"))
    assert make_service(settings_root).get_project("_program").is_reserved is True


# -- get_limits（IF-24・F04） ----------------------------------------------------------


def test_get_limits_falls_back_to_module_defaults(settings_root: Path) -> None:
    """US1 AC-11: `limits.yaml` **不在**ならモジュール設定の出荷時既定（唯一の既定）。"""
    write_minimal_area_a(settings_root)
    assert not (settings_root / "global/limits.yaml").exists()
    limits = make_service(settings_root).get_limits("pj")
    defaults = config_for_tests().limits_defaults
    assert limits.scope == SCOPE_GLOBAL
    assert limits.max_requests_per_kickoff == defaults.max_requests_per_kickoff
    assert limits.max_cost_per_kickoff == defaults.max_cost_per_kickoff


def test_get_limits_uses_global_when_no_override(settings_root: Path) -> None:
    """US1 AC-9: 上書きが無ければ既定が返り、適用範囲が全体であることが分かる。"""
    write_minimal_area_a(settings_root, projects=("pj", "other"))
    write(settings_root, "global/limits.yaml", LIMITS_BODY)
    limits = make_service(settings_root).get_limits("other")
    assert limits.scope == SCOPE_GLOBAL
    assert limits.max_requests_per_kickoff == 10


def test_get_limits_applies_the_project_override(settings_root: Path) -> None:
    """US1 AC-10: PJ 上書きが効き、適用範囲が当該 PJ であることが分かる。"""
    write_minimal_area_a(settings_root)
    write(settings_root, "global/limits.yaml", LIMITS_BODY)
    limits = make_service(settings_root).get_limits("pj")
    assert limits.scope == "pj"
    assert limits.max_requests_per_kickoff == 3
    assert limits.max_cost_per_kickoff == 0.5


def test_get_limits_is_the_only_method_that_ignores_project_existence(
    settings_root: Path,
) -> None:
    """IF-24 の例外欄は `—`。**9 メソッドで唯一プロジェクトの実在を見ない**。"""
    write_minimal_area_a(settings_root)
    limits = make_service(settings_root).get_limits("未登録")
    assert limits.scope == SCOPE_GLOBAL


# -- get_rules（IF-25・F05） -----------------------------------------------------------


def test_get_rules_returns_the_body(settings_root: Path) -> None:
    """US1 AC-12: `rules.md` が在れば本文が返る。"""
    write_minimal_area_a(settings_root)
    write(settings_root, "projects/pj/rules.md", "# 共通ルール\n本文\n")
    assert make_service(settings_root).get_rules("pj").project_rules == "# 共通ルール\n本文\n"


def test_get_rules_returns_empty_when_the_file_is_absent(settings_root: Path) -> None:
    """US1 AC-12: **不在は失敗ではない**（空文字）。"""
    write_minimal_area_a(settings_root)
    assert make_service(settings_root).get_rules("pj").project_rules == ""


def test_get_rules_does_not_mix_absence_with_unknown_project(settings_root: Path) -> None:
    """US1 AC-15: **`rules.md` 不在の空文字と PJ 未登録の失敗を混ぜない**。"""
    write_minimal_area_a(settings_root)
    with pytest.raises(ProjectNotFound):
        make_service(settings_root).get_rules("nope")


# -- get_hooks（IF-26・F06） -----------------------------------------------------------


def test_get_hooks_returns_the_declared_list(settings_root: Path) -> None:
    """US1 AC-14: 宣言された Hook 参照の一覧が返る。"""
    write_minimal_area_a(settings_root)
    write(
        settings_root,
        "projects/pj/hooks.yaml",
        "hooks:\n  - name: guard\n    points: PRE_TOOL_CALL\n    agents:\n      - programmer\n",
    )
    hooks = make_service(settings_root).get_hooks("pj").hooks
    assert [hook.name for hook in hooks] == ["guard"]
    assert hooks[0].points == "PRE_TOOL_CALL"
    assert hooks[0].agents == ["programmer"]


def test_get_hooks_returns_empty_when_the_file_is_absent(settings_root: Path) -> None:
    """US1 AC-14: 不在なら空の一覧。"""
    write_minimal_area_a(settings_root)
    assert make_service(settings_root).get_hooks("pj").hooks == []


def test_get_hooks_raises_for_unknown_project(settings_root: Path) -> None:
    """US1 AC-15: 未登録キーに空の設定を返さない。"""
    write_minimal_area_a(settings_root)
    with pytest.raises(ProjectNotFound):
        make_service(settings_root).get_hooks("nope")


# -- get_tasks（IF-50・F25） -----------------------------------------------------------


def test_get_tasks_returns_seven_keys_with_generation(settings_root: Path) -> None:
    """US1 AC-13: 7 キーが**世代番号つき**で返る。"""
    write_minimal_area_a(settings_root)
    templates = make_service(settings_root).get_tasks("pj")
    assert templates.generation == 1
    assert set(templates.templates) == set(TASK_KEYS)


def test_get_tasks_passes_tools_explicitly(settings_root: Path) -> None:
    """DIFF-07: `TaskTemplate.tools` は既定値を持たないので `None` を**明示的に渡す**。"""
    write_minimal_area_a(settings_root)
    templates = make_service(settings_root).get_tasks("pj").templates
    assert templates["child"].tools is None
    assert templates["review_normal"].tools is not None


def test_get_tasks_raises_for_unknown_project(settings_root: Path) -> None:
    """未登録キーに global のテンプレートを返さない。"""
    write_minimal_area_a(settings_root)
    with pytest.raises(ProjectNotFound):
        make_service(settings_root).get_tasks("nope")


# -- 5 メソッド共通 ---------------------------------------------------------------------


def test_reads_never_touch_the_file_system(settings_root: Path) -> None:
    """FR-014・SC-016: 読取の前後で領域 A の更新時刻が 1 つも変わらない。"""
    write_minimal_area_a(settings_root)
    service = make_service(settings_root)
    before = mtimes(settings_root)
    service.get_project("pj")
    service.get_limits("pj")
    service.get_rules("pj")
    service.get_hooks("pj")
    service.get_tasks("pj")
    assert mtimes(settings_root) == before


def test_project_without_role_dirs_declares_nothing(settings_root: Path) -> None:
    """`role_dirs` は宣言された分だけを写す（settings は実体を確かめない＝ FR-007）。"""
    write_minimal_area_a(settings_root)
    write_project(settings_root, "pj", ("programmer", "planner"))
    folders = make_service(settings_root).get_project("pj").folders
    assert set(folders.role_dirs) == {PersonaKey.programmer, PersonaKey.planner}
