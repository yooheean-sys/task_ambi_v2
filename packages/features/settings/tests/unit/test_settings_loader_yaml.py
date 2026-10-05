"""PyYAML の書式規約 Y-1〜Y-9（contracts/area-a-loader.md §3・実測 M-13〜M-19）。

**基名は `test_settings_*` で統一する**（FR-073。`tests/` に `__init__.py` を置かないため
基名がそのまま top-level モジュール名になり、feature をまたいで重複すると全体の pytest だけが
collection error で止まる）。
"""

from __future__ import annotations

import pytest

from settings.repository import YamlError, load_yaml_mapping
from settings.service.resolve import normalize_mapping

REL = "global/agents.yaml"


def test_y1_python_object_tag_is_rejected() -> None:
    """Y-1: `!!python/object:` は拒否される（任意コード実行の経路を持たない）。"""
    with pytest.raises(YamlError):
        load_yaml_mapping("a: !!python/object:os.system {}\n", REL)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("- a\n- b\n", id="list"),
        pytest.param("just a string\n", id="str"),
    ],
)
def test_y2_top_level_must_be_mapping(text: str) -> None:
    """Y-2: トップレベルが mapping でなければ構文誤り（`safe_load` は例外にしない＝ M-19）。"""
    with pytest.raises(YamlError):
        load_yaml_mapping(text, REL)


def test_y3_empty_file_is_normalized_to_empty_mapping() -> None:
    """Y-3: 空ファイルは `None` になるので `{}` へ正規化する（M-13）。"""
    assert load_yaml_mapping("", REL) == {}


def test_y4_value_less_key_is_none_and_normalized() -> None:
    """Y-4: 値なし `programmer:` は `{}` ではなく `None`（M-13）。正規化は `resolve` が持つ。"""
    data = load_yaml_mapping("programmer:\n", REL)
    assert data == {"programmer": None}
    assert normalize_mapping(data["programmer"]) == {}


def test_y5_duplicate_key_is_a_syntax_error_with_position() -> None:
    """Y-5: 同一キーの重複は**行・列つき**の構文誤り（R-14・M-14）。"""
    text = "programmer:\n  role: a\nprogrammer:\n  role: b\n"
    with pytest.raises(YamlError) as excinfo:
        load_yaml_mapping(text, REL)
    message = str(excinfo.value)
    assert "duplicate key" in message
    assert "line 3" in message


def test_y5_nested_duplicate_key_is_detected() -> None:
    """Y-5: 入れ子の mapping の重複も検出する（M-14）。"""
    with pytest.raises(YamlError):
        load_yaml_mapping("programmer:\n  role: a\n  role: b\n", REL)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("{title}", "{title}", id="variable"),
        pytest.param("{{title}}", "{{title}}", id="escaped"),
        pytest.param("{ 行頭の波括弧", "{ 行頭の波括弧", id="leading-brace"),
        pytest.param("受け入れ: {acceptance}", "受け入れ: {acceptance}", id="colon"),
    ],
)
def test_y6_block_scalar_keeps_braces_as_text(body: str, expected: str) -> None:
    """Y-6: ブロックスカラーの中身は**すべて文字列**になる（M-16）。"""
    data = load_yaml_mapping(f"child:\n  description: |-\n    {body}\n", REL)
    child = data["child"]
    assert isinstance(child, dict)
    assert child["description"] == expected


def test_y6_bare_flow_mapping_silently_changes_the_type() -> None:
    """Y-6 の裏: 素の `description: {title}` は**失敗せずに** dict へ化ける（M-15 (b)）。

    この 1 経路が validate の **V-10b**（値の `str` 性）が塞ぐ対象である。
    """
    data = load_yaml_mapping("child:\n  description: {title}\n", REL)
    child = data["child"]
    assert isinstance(child, dict)
    assert child["description"] == {"title": None}


def test_y8_japanese_crlf_and_bom_are_transparent() -> None:
    """Y-8: 日本語・CRLF・BOM は透過する（M-17・M-18）。"""
    plain = load_yaml_mapping("programmer:\n  role: プログラマ\n", REL)
    crlf = load_yaml_mapping("programmer:\r\n  role: プログラマ\r\n", REL)
    bom = load_yaml_mapping("﻿programmer:\n  role: プログラマ\n", REL)
    assert plain == crlf == bom


def test_y9_anchor_alias_and_merge_key_pass() -> None:
    """Y-9: anchor／alias／merge key `<<` は通る（重複キー検出がこれを壊さない）。"""
    text = "base: &base\n  role: 共通\nprogrammer:\n  <<: *base\n  goal: 目的\n"
    data = load_yaml_mapping(text, REL)
    programmer = data["programmer"]
    assert isinstance(programmer, dict)
    assert programmer == {"role": "共通", "goal": "目的"}
