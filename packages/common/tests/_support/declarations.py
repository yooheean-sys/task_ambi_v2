"""実装側（`common.interfaces.*`）の宣言を列挙する。

突合の対象は **(a) Pydantic の受け渡し型・(b) Protocol・(c) 列挙・(d) 契約例外の 4 種だけ**
（FR-041・plan.md (viii)）。`RedactFilter`・`find_repo_root`・`_correlation_id` は
4 種のいずれでもないため対象外であり、「宣言にあって設計に無い」の誤検出にならない。

注釈は**文字列のまま**扱う。`from __future__ import annotations` と `TYPE_CHECKING`
import により、`CrewOutcome`・`httpx.Request` は実行時に解決できないため
（`typing.get_type_hints` は使えない）。
"""

from __future__ import annotations

import importlib
import inspect
import re
import typing
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType

from _support.design_docs import INTERFACE_MODULES
from pydantic import BaseModel

_RAISES_HEADER = re.compile(r"^\s*Raises:\s*$")
_RAISES_ENTRY = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:")


@dataclass(frozen=True)
class MethodDecl:
    """Protocol の 1 メソッドの宣言。"""

    protocol: str
    name: str
    arg_names: tuple[str, ...]
    return_annotation: str
    is_async: bool
    raises: tuple[str, ...]
    doc: str | None

    @property
    def arg_count(self) -> int:
        """`self` を除いた引数の個数。"""
        return len(self.arg_names)


def interface_modules() -> dict[str, ModuleType]:
    """提供モジュール名 → 契約ファイルのモジュール。"""
    return {
        name: importlib.import_module(f"common.interfaces.{name}") for name in INTERFACE_MODULES
    }


def _own_members(module: ModuleType) -> list[tuple[str, type[object]]]:
    """そのファイルで**定義された**クラスだけを返す（import した型を数えない）。"""
    found: list[tuple[str, type[object]]] = []
    for name, value in vars(module).items():
        if isinstance(value, type) and value.__module__ == module.__name__:
            found.append((name, value))
    return found


def declared_models() -> dict[str, type[BaseModel]]:
    """宣言された受け渡し型（`BaseModel` 派生）。"""
    models: dict[str, type[BaseModel]] = {}
    for module in interface_modules().values():
        for name, value in _own_members(module):
            if issubclass(value, BaseModel):
                models[name] = value
    return models


def declared_enums() -> dict[str, type[StrEnum]]:
    """宣言された提供モジュール固有の列挙（`StrEnum` 派生）。"""
    enums: dict[str, type[StrEnum]] = {}
    for module in interface_modules().values():
        for name, value in _own_members(module):
            if issubclass(value, StrEnum):
                enums[name] = value
    return enums


def declared_exceptions() -> dict[str, type[BaseException]]:
    """宣言された契約例外（`BaseException` 派生）。"""
    found: dict[str, type[BaseException]] = {}
    for module in interface_modules().values():
        for name, value in _own_members(module):
            if issubclass(value, BaseException):
                found[name] = value
    return found


def declared_protocols() -> dict[str, type[object]]:
    """宣言された Protocol。"""
    protocols: dict[str, type[object]] = {}
    for module in interface_modules().values():
        for name, value in _own_members(module):
            if typing.is_protocol(value):
                protocols[name] = value
    return protocols


def _parse_raises(doc: str | None) -> tuple[str, ...]:
    """docstring の `Raises:` 節から例外名を読む（FR-033c）。"""
    if doc is None:
        return ()
    lines = doc.splitlines()
    names: list[str] = []
    inside = False
    for line in lines:
        if _RAISES_HEADER.match(line):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip():
            break
        entry = _RAISES_ENTRY.match(line)
        if entry is None:
            break
        names.append(entry.group(1))
    return tuple(names)


def protocol_methods(name: str, protocol: type[object]) -> dict[str, MethodDecl]:
    """Protocol の宣言メソッドを読む（注釈は文字列のまま）。"""
    methods: dict[str, MethodDecl] = {}
    for member in typing.get_protocol_members(protocol):
        func = inspect.getattr_static(protocol, member)
        if not inspect.isfunction(func):
            continue
        signature = inspect.signature(func)
        arg_names = tuple(p for p in signature.parameters if p != "self")
        annotation = signature.return_annotation
        methods[member] = MethodDecl(
            protocol=name,
            name=member,
            arg_names=arg_names,
            return_annotation=annotation if isinstance(annotation, str) else str(annotation),
            is_async=inspect.iscoroutinefunction(func),
            raises=_parse_raises(inspect.getdoc(func)),
            doc=inspect.getdoc(func),
        )
    return methods


def all_protocol_methods() -> dict[tuple[str, str], MethodDecl]:
    """`(Protocol 名, メソッド名)` → 宣言。"""
    declarations: dict[tuple[str, str], MethodDecl] = {}
    for name, protocol in declared_protocols().items():
        for method, decl in protocol_methods(name, protocol).items():
            declarations[name, method] = decl
    return declarations
