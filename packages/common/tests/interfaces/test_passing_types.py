"""SC-005: 受け渡し型 37 ＋ 提供モジュール固有の列挙 9 が設計と過不足 0。

浅い凍結（FR-038a）を前提にする。`list`／`dict`／`set` の**中身**の書き換えは防がない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _support.declarations import declared_enums, declared_models
from _support.design_docs import (
    DesignClass,
    enumerations,
    load_interface_classes,
    passing_types,
)
from _support.instances import INSTANCES, ISSUE
from pydantic import BaseModel, ValidationError

_CONTAINERS = ("dict", "list", "set", "frozenset")


def _annotations(model: type[BaseModel]) -> dict[str, str]:
    """フィールド名 → 宣言された注釈（文字列のまま）。"""
    raw: dict[str, Any] = dict(model.__annotations__)
    return {name: str(raw[name]) for name in model.model_fields if name in raw}


def _canonical(annotation: str) -> str:
    """空白と引用符の揺れを吸収する。

    `from __future__ import annotations` の注釈は AST から生成されるため、
    `Literal["a"]` と書いても実行時には `Literal['a']` になる。
    """
    return annotation.replace(" ", "").replace("'", '"')


def _matches(design_type: str, declared: str) -> bool:
    """mmd の型表記と宣言された注釈を突き合わせる。

    mmd が容器名だけを書いている（`dict`）場合は**外側の容器だけ**を照合する。
    """
    if design_type in _CONTAINERS:
        return declared.split("[", 1)[0].strip() == design_type
    return _canonical(declared) == _canonical(design_type)


# --- (a) 母数 --------------------------------------------------------------


def test_registered_instances_cover_every_declared_model() -> None:
    assert len(INSTANCES) == 38
    assert set(INSTANCES) == set(declared_models())


def test_declared_models_match_the_design(design_root: Path) -> None:
    design = passing_types(load_interface_classes(design_root))
    assert set(declared_models()) == set(design)
    assert len(design) == 38


# --- (b) 属性の再代入が拒否される -------------------------------------------


@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_attribute_reassignment_is_rejected(name: str) -> None:
    instance = INSTANCES[name]
    field = next(iter(type(instance).model_fields))
    with pytest.raises(ValidationError) as caught:
        setattr(instance, field, getattr(instance, field))
    assert caught.value.errors()[0]["type"] == "frozen_instance"


def test_shallow_freeze_does_not_protect_collection_contents() -> None:
    """浅い凍結（FR-038a）。SC-005 の「書き換えが拒否される」は**属性の再代入**を指す。"""
    issue = ISSUE
    issue.labels.append("note:added")
    assert "note:added" in issue.labels


# --- (c) model_copy(update=...) --------------------------------------------


def _updated_value(value: object) -> object | None:
    if isinstance(value, str):
        return value + "!"
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    return None


@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_model_copy_returns_a_new_instance(name: str) -> None:
    instance = INSTANCES[name]
    update: dict[str, object] = {}
    for field in type(instance).model_fields:
        candidate = _updated_value(getattr(instance, field))
        if candidate is not None:
            update = {field: candidate}
            break
    before = {f: getattr(instance, f) for f in type(instance).model_fields}
    copied = instance.model_copy(update=update)
    assert copied is not instance
    for field, expected in update.items():
        assert getattr(copied, field) == expected
    for field, expected in before.items():
        assert getattr(instance, field) == expected


# --- (d) 属性集合が mmd と一致 ---------------------------------------------


def _design_and_declared(design_root: Path) -> list[tuple[str, DesignClass, type[BaseModel]]]:
    design = passing_types(load_interface_classes(design_root))
    declared = declared_models()
    return [(name, design[name], declared[name]) for name in sorted(design)]


def test_attribute_names_match(design_root: Path) -> None:
    """`threshold_T` ↔ `threshold_t` は大文字小文字を無視して照合する（R-03）。"""
    mismatched: list[tuple[str, list[str], list[str]]] = []
    for name, design, model in _design_and_declared(design_root):
        expected = sorted(a.name.lower() for a in design.attributes)
        actual = sorted(f.lower() for f in model.model_fields)
        if expected != actual:
            mismatched.append((name, expected, actual))
    assert mismatched == []


def test_attribute_types_match_where_the_design_declares_one(design_root: Path) -> None:
    """mmd が型を書いている属性だけを照合する（書いていない属性は設計に型が無い）。"""
    mismatched: list[tuple[str, str, str, str]] = []
    checked = 0
    for name, design, model in _design_and_declared(design_root):
        annotations = _annotations(model)
        for attr in design.attributes:
            if attr.type_text is None:
                continue
            declared = annotations[attr.name if attr.name in annotations else attr.name.lower()]
            checked += 1
            if not _matches(attr.type_text, declared):
                mismatched.append((name, attr.name, attr.type_text, declared))
    assert mismatched == []
    assert checked > 0


def test_required_and_optional_match(design_root: Path) -> None:
    """`既定 X`／`任意`／印なしが必須・省略可・既定値と一致する（R-17）。"""
    mismatched: list[str] = []
    for name, design, model in _design_and_declared(design_root):
        for attr in design.attributes:
            field_name = attr.name if attr.name in model.model_fields else attr.name.lower()
            field = model.model_fields[field_name]
            if attr.required != field.is_required():
                mismatched.append(f"{name}.{attr.name}: required={attr.required}")
    assert mismatched == []


def test_defaults_match(design_root: Path) -> None:
    """`既定 X` の X が宣言の既定値に現れ、`任意` は `None` か空コレクションになる。"""
    problems: list[str] = []
    optional_count = 0
    for name, design, model in _design_and_declared(design_root):
        annotations = _annotations(model)
        for attr in design.attributes:
            if attr.required:
                continue
            optional_count += 1
            field_name = attr.name if attr.name in model.model_fields else attr.name.lower()
            default = model.model_fields[field_name].default
            if attr.default_text is not None:
                if attr.default_text not in repr(default):
                    problems.append(f"{name}.{attr.name}: {attr.default_text} not in {default!r}")
            elif annotations[field_name].endswith("| None"):
                if default is not None:
                    problems.append(f"{name}.{attr.name}: expected None, got {default!r}")
            elif default not in ({}, [], set(), frozenset()):
                problems.append(f"{name}.{attr.name}: expected empty collection, got {default!r}")
    assert problems == []
    assert optional_count == 17  # 17 件目は D25 の `CrewSpec.repos`


# --- (e) 提供モジュール固有の列挙 9 件 --------------------------------------


def test_module_enums_match_the_design(design_root: Path) -> None:
    design = enumerations(load_interface_classes(design_root))
    declared = declared_enums()
    assert set(declared) == set(design)
    assert len(design) == 9
    mismatched = [
        (name, sorted(design[name].values), sorted(m.value for m in declared[name]))
        for name in design
        if sorted(design[name].values) != sorted(m.value for m in declared[name])
    ]
    assert mismatched == []


def test_module_enum_members_are_name_equals_value() -> None:
    offenders = [
        f"{name}.{member.name}"
        for name, enum in declared_enums().items()
        for member in enum
        if member.name != member.value
    ]
    assert offenders == []
