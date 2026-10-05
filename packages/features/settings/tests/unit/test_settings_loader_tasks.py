"""`tasks.yaml` のキー単位上書き（D16-h）と `{変数}` 検査の表駆動（D16-c・M-20〜M-22）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    collect_problems,
    default_tasks,
    dump_mapping,
    kinds,
    make_service,
    write,
    write_minimal_area_a,
    write_tasks,
)

from settings.models import ALLOWED_VARS, TASK_KEYS
from settings.service.validate import extract_variables

if TYPE_CHECKING:
    from pathlib import Path


def test_project_override_replaces_the_whole_key(settings_root: Path) -> None:
    """D16-h: PJ 側に在る段だけが**丸ごと**差し替わり、残りは global のまま。"""
    write_minimal_area_a(settings_root)
    override = {"child": {"description": "PJ 版 {acceptance}", "expected_output": "PJ 出力"}}
    write_tasks(settings_root, "pj", dump_mapping(override))
    templates = make_service(settings_root).get_tasks("pj").templates
    assert set(templates) == set(TASK_KEYS)
    assert templates["child"].description == "PJ 版 {acceptance}"
    assert templates["child"].expected_output == "PJ 出力"
    assert templates["program"].description == default_tasks_description("program")


def test_override_is_not_field_wise(settings_root: Path) -> None:
    """D16-h: **フィールド単位の merge をしない**（欠けたフィールドを global から引かない）。

    `description` だけを書いた PJ 上書きは必須フィールドの欠落として起動時に落ちる（V-10a）。
    ここでは「global の `expected_output` を引き継がない」ことを検査の側から観測する。
    """
    write_minimal_area_a(settings_root)
    write_tasks(settings_root, "pj", dump_mapping({"child": {"description": "{acceptance}"}}))
    assert "var_required" in kinds(collect_problems(settings_root))


NO_NAMES: set[str] = set()
"""変数が 1 つも無いことを表す空集合（`set()` は pyright strict で型が定まらない）。"""


def default_tasks_description(key: str) -> str:
    """同梱既定の当該段の本文。"""
    return str(default_tasks()[key]["description"])


@pytest.mark.parametrize(
    ("text", "expected_names", "has_issue"),
    [
        pytest.param("{title}", {"title"}, False, id="単純な変数"),
        pytest.param("{{title}}", NO_NAMES, False, id="{{ }} は変数として拾わない"),
        pytest.param("{issue.id}", {"issue.id"}, False, id="属性参照も取りこぼさない"),
        pytest.param("{issue[0]}", {"issue[0]"}, False, id="添字も取りこぼさない"),
        pytest.param("{0}", {"0"}, False, id="位置引数（番号）"),
        pytest.param("{}", {""}, False, id="位置引数（空の名前）は捨てない"),
        pytest.param("{題名}", {"題名"}, False, id="非 ASCII 名"),
        pytest.param("{title", NO_NAMES, True, id="閉じ括弧なしは形式の破れ"),
        pytest.param("a } b", NO_NAMES, True, id="単独の } も形式の破れ"),
        pytest.param("{title:>10}", {"title"}, True, id="書式指定は拒否"),
        pytest.param("{acceptance!r}", {"acceptance"}, True, id="変換は拒否"),
    ],
)
def test_variable_extraction_uses_formatter_parse(
    text: str, expected_names: set[str], has_issue: bool
) -> None:
    """R-19・M-20〜M-22: 抽出は `string.Formatter().parse()` に一本化する。

    正規表現 `\\{([A-Za-z_]\\w*)\\}` はここに並ぶ 7 通りを**すべて取りこぼす**。
    """
    names, issues = extract_variables(text)
    assert names == expected_names
    assert bool(issues) is has_issue


def test_positional_placeholder_is_outside_the_allowed_set() -> None:
    """位置引数 `{}` は空の名前として抽出され、許可 17 の外なので未知変数になる。"""
    names, _ = extract_variables("{}")
    assert names - ALLOWED_VARS == {""}


@pytest.mark.parametrize(
    ("stage", "body", "expected_kind"),
    [
        pytest.param("child", "{unknown_var} {acceptance}", "var_unknown", id="許可 17 の外"),
        pytest.param("child", "受け入れなし", "var_required", id="段別必須変数の欠落"),
        pytest.param("program", "{acceptance}", "var_required", id="persona_guide の欠落"),
        pytest.param("child", "{title:>10} {acceptance}", "var_unknown", id="書式指定"),
    ],
)
def test_variable_rules_are_table_driven(
    settings_root: Path, stage: str, body: str, expected_kind: str
) -> None:
    """V-12・V-13 を段ごとに表で確かめる。"""
    write_minimal_area_a(settings_root)
    tasks = default_tasks()
    tasks[stage]["description"] = body
    write(settings_root, "global/tasks.yaml", dump_mapping(tasks))
    assert expected_kind in kinds(collect_problems(settings_root))


def test_escaped_braces_do_not_count_as_variables(settings_root: Path) -> None:
    """M-22: 本文に波括弧そのものを書くときは `{{` `}}`（変数として拾わない）。"""
    write_minimal_area_a(settings_root)
    tasks = default_tasks()
    tasks["child"]["description"] = "波括弧 {{literal}} と {acceptance}"
    write(settings_root, "global/tasks.yaml", dump_mapping(tasks))
    assert collect_problems(settings_root) == []
