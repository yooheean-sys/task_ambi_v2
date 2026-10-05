"""横断: registry の宣言が `<Mod>Deps`／`<Mod>Handle` の実属性と 1:1 であること（US3・US4）。

GAP-3 を埋める。`data-model.md` §3 の出典欄は `requires`＝「D11-f の `<Mod>Deps` の属性集合」・
`provides`＝「D11-f の `<Mod>Handle` の Protocol 実装属性」と定めるが、
**これを機械で照合するテストが
存在しなかった**。とくに**過剰宣言は実行時に露見しない** —— `_run_builds` は宣言した `requires` の
分だけ写像を渡すので、アダプタが使わなければ黙って通る。

⚠ **突合は属性名の綴り規則に依存せず「型」で行う**（FR-017）。`LLMHttpHook` → `llm_http_hook` の
ような不規則な変換を実装しない。注釈の解決には `conftest.LOCALNS`（14 Protocol ＋ 3）を使う。

⚠ **現時点で D-1〜D-4 は 8/8 緑**である。本ファイルが置くのは**回帰の見張り**であって、いま在る
食い違いの修正ではない。

⚠ 本ファイルは**組を宣言しない**（横断ファイル）。**`requires_bd` を付けない**（`build` を呼ばない
ので board の宣言も検査対象に含む＝ research R-13）。
"""

from __future__ import annotations

import csv
import dataclasses
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from conftest import INTERFACE_PROTOCOLS, LOCALNS, resolve_deps_handle
from fastapi import APIRouter

from api.registry import MODULE_REGISTRATIONS
from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from collections.abc import Sequence

    from api.wiring import ModuleRegistration

CONTRACT_CSV_RELATIVE = "docs/design/phase4/p4_module_contract.csv"
"""`p4_module_contract.csv` の所在（**行番号で引かない**＝ CLAUDE.md の参照書式）。"""

PROVIDER_COLUMN = "提供モジュール"
CONSUMER_COLUMN = "利用モジュール"
PROTOCOL_COLUMN = "Protocol名"


@dataclass(frozen=True, slots=True)
class DeclarationPair:
    """1 MOD の宣言と実属性の対（data-model §2-3）。"""

    name: str
    deps_cls: type
    handle_cls: type
    deps_types: frozenset[object]
    handle_protocol_types: frozenset[object]
    router_type: object
    has_lifespan_field: bool


@dataclass(frozen=True, slots=True)
class DeclarationDiff:
    """宣言と実属性の食い違い（すべて空なら 1:1）。"""

    declared_not_in_deps: frozenset[str]
    deps_not_declared: frozenset[str]
    declared_not_in_handle: frozenset[str]
    handle_not_declared: frozenset[str]


def _resolved_field_types(cls: type) -> dict[str, object]:
    """凍結データクラスのフィールド名 → **解決済みの型**（`LOCALNS` を与えて解く）。

    `from __future__ import annotations` により `dataclasses.fields(cls)[i].type`
    は**文字列**なので、
    `typing.get_type_hints` で解く。Protocol の import が `if TYPE_CHECKING:` の中にあるため、
    `LOCALNS` を与えないと 16 クラス中 11 クラスが `NameError` になる（research R-03）。

    Args:
        cls: `<Mod>Deps` または `<Mod>Handle`。

    Returns:
        フィールド名 → 解決済みの型注釈。
    """
    hints = typing.get_type_hints(cls, localns=LOCALNS)
    return {field.name: hints[field.name] for field in dataclasses.fields(cls)}


def _protocol_set() -> frozenset[object]:
    """`common.interfaces` の 14 Protocol の集合。

    Returns:
        Protocol クラスの集合。
    """
    return frozenset(INTERFACE_PROTOCOLS.values())


def make_pair(name: str) -> DeclarationPair:
    """1 MOD の `DeclarationPair` を組む（data-model §2-3）。

    Args:
        name: feature モジュール名。

    Returns:
        宣言と実属性の対。
    """
    deps_cls, handle_cls = resolve_deps_handle(name)
    protocols = _protocol_set()
    deps_fields = _resolved_field_types(deps_cls)
    handle_fields = _resolved_field_types(handle_cls)
    return DeclarationPair(
        name=name,
        deps_cls=deps_cls,
        handle_cls=handle_cls,
        deps_types=frozenset(deps_fields.values()),
        handle_protocol_types=frozenset(
            value for value in handle_fields.values() if value in protocols
        ),
        router_type=handle_fields.get("router"),
        has_lifespan_field="lifespan" in handle_fields,
    )


def compare_declaration(
    requires: Sequence[type[object]],
    provides: Sequence[type[object]],
    deps_cls: type,
    handle_cls: type,
) -> DeclarationDiff:
    """宣言と実属性を突き合わせる**純関数**（T076 が合成の登録を入力に与えて自己実証する）。

    Args:
        requires: 登録の `requires`。
        provides: 登録の `provides`。
        deps_cls: `<Mod>Deps`。
        handle_cls: `<Mod>Handle`。

    Returns:
        食い違い（すべて空なら 1:1）。
    """
    protocols = _protocol_set()
    deps_types = frozenset(_resolved_field_types(deps_cls).values())
    handle_types = frozenset(
        value for value in _resolved_field_types(handle_cls).values() if value in protocols
    )
    declared_requires = frozenset(requires)
    declared_provides = frozenset(provides)

    def names(items: frozenset[object]) -> frozenset[str]:
        return frozenset(getattr(item, "__name__", repr(item)) for item in items)

    return DeclarationDiff(
        declared_not_in_deps=names(declared_requires - deps_types),
        deps_not_declared=names(deps_types - declared_requires),
        declared_not_in_handle=names(declared_provides - handle_types),
        handle_not_declared=names(handle_types - declared_provides),
    )


def _csv_rows() -> tuple[dict[str, str], ...]:
    """`p4_module_contract.csv` を**読み取り専用**で開いて全行を返す（research R-04）。

    Returns:
        行の辞書のタプル。
    """
    path = find_repo_root() / CONTRACT_CSV_RELATIVE
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def _includes_apirouter(annotation: object) -> bool:
    """解決型が `APIRouter` を**含む**か（`APIRouter` 単体でも `APIRouter | None` でも真）。

    ⚠ **`is NoneType` で固定しない**（FR-019・2026-09-01 裁定＝ WD-41）。現物は `has_router=False`
    の
    5 件が `NoneType` に狭まっているが、**D11-f と `p4_module_dependency_notes.md` §4 はどちらも
    `<Mod>Handle.router` を `APIRouter | None` と定めており**、
    狭い側を要件に固定すると設計判断記録に
    反する規則をテストが作ることになる（起票は **B-12**）。

    Args:
        annotation: `<Mod>Handle.router` の解決済み型注釈。

    Returns:
        `APIRouter` を含むなら真。
    """
    args = typing.get_args(annotation)
    candidates = args if args else (annotation,)
    return any(candidate is APIRouter for candidate in candidates)


def _ids(registration: ModuleRegistration) -> str:
    """パラメトライズの表示名（MOD 名）。"""
    return registration.name


@pytest.mark.parametrize("registration", MODULE_REGISTRATIONS, ids=_ids)
def test_d1_requires_matches_the_deps_attributes(registration: ModuleRegistration) -> None:
    """横断 D-1: `requires` の型集合が `<Mod>Deps` の属性の型集合と一致する（FR-014・US3 AC-1）。

    見る欄: `requires` × `<Mod>Deps` の属性。board・vault・stages は両方とも空である。
    **過剰宣言は実行時に露見しない**（`_run_builds` は宣言分の写像を渡すので、アダプタが使わなければ
    黙って通る）ため、この突合だけが捕まえられる。
    """
    pair = make_pair(registration.name)
    diff = compare_declaration(
        registration.requires, registration.provides, pair.deps_cls, pair.handle_cls
    )

    assert not diff.declared_not_in_deps, (
        f"{registration.name}: `requires` に宣言したが {pair.deps_cls.__name__} に対応属性が無い: "
        f"{sorted(diff.declared_not_in_deps)}（過剰宣言）"
    )
    assert not diff.deps_not_declared, (
        f"{registration.name}: {pair.deps_cls.__name__} が持つのに `requires` に宣言が無い: "
        f"{sorted(diff.deps_not_declared)}（宣言漏れ）"
    )


@pytest.mark.parametrize("registration", MODULE_REGISTRATIONS, ids=_ids)
def test_d2_provides_matches_the_handle_protocol_attributes(
    registration: ModuleRegistration,
) -> None:
    """横断 D-2: `provides` の型集合が `<Mod>Handle` の Protocol
    属性と一致する（FR-015・US3 AC-2）。

    見る欄: `provides` × `<Mod>Handle` の Protocol 属性。`router`・`lifespan` は Protocol でないので
    自然に外れる。trigger は両方とも空である。
    """
    pair = make_pair(registration.name)
    diff = compare_declaration(
        registration.requires, registration.provides, pair.deps_cls, pair.handle_cls
    )

    assert not diff.declared_not_in_handle, (
        f"{registration.name}: `provides` に宣言したが "
        f"{pair.handle_cls.__name__} に対応属性が無い: "
        f"{sorted(diff.declared_not_in_handle)}"
    )
    assert not diff.handle_not_declared, (
        f"{registration.name}: {pair.handle_cls.__name__} が持つのに `provides` に宣言が無い: "
        f"{sorted(diff.handle_not_declared)}"
    )


@pytest.mark.parametrize("registration", MODULE_REGISTRATIONS, ids=_ids)
def test_d3_declarations_match_the_design_contract_csv(registration: ModuleRegistration) -> None:
    """横断 D-3: `requires`／`provides` が `p4_module_contract.csv` と一致する（FR-016・US3 AC-3）。

    見る欄: `requires`・`provides` × csv の利用列／提供列。Protocol 名の集合として**両方向**で見る。
    csv は**読み取り専用**で開き、**行番号で引かない**（CLAUDE.md の参照書式）。
    """
    rows = _csv_rows()
    consumed = {row[PROTOCOL_COLUMN] for row in rows if row[CONSUMER_COLUMN] == registration.name}
    supplied = {row[PROTOCOL_COLUMN] for row in rows if row[PROVIDER_COLUMN] == registration.name}
    declared_requires = {contract.__name__ for contract in registration.requires}
    declared_provides = {contract.__name__ for contract in registration.provides}

    assert consumed == declared_requires, (
        f"{registration.name}: csv の利用列と `requires` が食い違う\n"
        f"  csv にだけ在る: {sorted(consumed - declared_requires)}\n"
        f"  registry にだけ在る: {sorted(declared_requires - consumed)}"
    )
    assert supplied == declared_provides, (
        f"{registration.name}: csv の提供列と `provides` が食い違う\n"
        f"  csv にだけ在る: {sorted(supplied - declared_provides)}\n"
        f"  registry にだけ在る: {sorted(declared_provides - supplied)}"
    )


def test_d4_csv_and_localns_agree_with_the_real_registrations() -> None:
    """横断 D-4（自己検査）: csv の MOD 名・Protocol 名が実登録と `LOCALNS`
    に一致する（FR-016・R-03）。

    見る欄: `name` × csv の MOD 名列／`LOCALNS` が集めた Protocol × csv の `Protocol名` 列。
    **片方だけが増えたら赤くなる**——D-1〜D-3 が「対象を取りこぼしたまま緑」になることを防ぐ。
    """
    rows = _csv_rows()
    csv_modules = {row[PROVIDER_COLUMN] for row in rows} | {row[CONSUMER_COLUMN] for row in rows}
    registered = {registration.name for registration in MODULE_REGISTRATIONS}
    csv_protocols = {row[PROTOCOL_COLUMN] for row in rows}

    assert csv_modules == registered, (
        "csv に現れる MOD 名と実登録名が食い違う\n"
        f"  csv にだけ在る: {sorted(csv_modules - registered)}\n"
        f"  実登録にだけ在る: {sorted(registered - csv_modules)}"
    )
    assert set(INTERFACE_PROTOCOLS) == csv_protocols, (
        "`common.interfaces` が定義する Protocol と csv の Protocol名 列が食い違う\n"
        f"  interfaces にだけ在る: {sorted(set(INTERFACE_PROTOCOLS) - csv_protocols)}\n"
        f"  csv にだけ在る: {sorted(csv_protocols - set(INTERFACE_PROTOCOLS))}"
    )
    assert len(INTERFACE_PROTOCOLS) == len(csv_protocols)


def test_d1_negative_an_unused_extra_require_is_named() -> None:
    """横断 D-1（負の実証）: 使われない契約を `requires` に 1 件足すと名指しで赤くなる（US3 AC-4）。

    見る欄: —（判定器そのものの検査）。突合を**純関数**に切り出してあるので、**合成の登録**を
    入力として与えるだけで実証できる。**`registry.py` は変更しない**（合成データは関数の中）。
    """
    registration = MODULE_REGISTRATIONS[0]
    pair = make_pair(registration.name)
    unused = next(
        protocol
        for _, protocol in sorted(INTERFACE_PROTOCOLS.items())
        if protocol not in pair.deps_types
    )

    diff = compare_declaration(
        (*registration.requires, unused), registration.provides, pair.deps_cls, pair.handle_cls
    )

    assert diff.declared_not_in_deps == frozenset({unused.__name__})
    assert not diff.deps_not_declared
    assert not diff.declared_not_in_handle
    assert not diff.handle_not_declared


@pytest.mark.parametrize("registration", MODULE_REGISTRATIONS, ids=_ids)
def test_d11f_common_shape_of_build_is_resolvable(registration: ModuleRegistration) -> None:
    """横断: `build` が D11-f の共通形（`build(deps: <Mod>Deps) -> <Mod>Handle`）である（R-02）。

    見る欄: `build`（D11-f の共通形）。同定は `get_type_hints(<mod>.build, localns=LOCALNS)` の
    第 1 位置引数と戻り値で行うので、**共通形が崩れれば同定そのものが失敗して赤くなる**——
    クラス名の綴り規則にも属性名の綴り規則にも依存しない見張りである。
    """
    deps_cls, handle_cls = resolve_deps_handle(registration.name)

    assert dataclasses.is_dataclass(deps_cls)
    assert dataclasses.is_dataclass(handle_cls)
    assert deps_cls.__name__.endswith("Deps")
    assert handle_cls.__name__.endswith("Handle")


@pytest.mark.parametrize("registration", MODULE_REGISTRATIONS, ids=_ids)
def test_d5_router_annotation_matches_the_has_router_declaration(
    registration: ModuleRegistration,
) -> None:
    """横断 D-5: `has_router` の宣言と `<Mod>Handle.router` の**宣言型**が一致する（FR-019・US4
    AC-3）。

    見る欄: `has_router` × `<Mod>Handle.router` の解決型。**判定は「`APIRouter` を含むか」で行う**
    ——⚠ **`router_type is NoneType` で固定しない**。現物は `False` 側が `NoneType`
    に狭まっているが、
    **D11-f と `p4_module_dependency_notes.md` §4 はどちらも `router: APIRouter | None`
    と定める**ため、
    狭い現物側を要件に固定しない（2026-09-01 裁定＝ WD-41。起票は **B-12**）。

    **`True`／`False` をテストに書かず `registration.has_router` から導く**（SC-015 の変異 1）。
    """
    pair = make_pair(registration.name)

    assert _includes_apirouter(pair.router_type) == registration.has_router, (
        f"{registration.name}: has_router={registration.has_router} だが "
        f"{pair.handle_cls.__name__}.router の解決型は {pair.router_type!r}"
    )


def test_d6_only_the_trigger_handle_declares_a_lifespan_field() -> None:
    """横断 D-6: `lifespan` 欄を持つ `<Mod>Handle` は 1 件だけである（FR-020・B-03）。

    見る欄: `<Mod>Handle.lifespan` の有無。⚠ `ModuleRegistration` に `lifespan` の宣言フィールドが
    **無い**（6 欄）ので、「後始末を持つのはどの MOD か」の正は `<Mod>Handle` の現物である。
    **件数もモジュール名も書かず**、`has_ui_page` のような宣言欄が無いこの事実を実属性から数える。
    """
    with_lifespan = sorted(
        registration.name
        for registration in MODULE_REGISTRATIONS
        if make_pair(registration.name).has_lifespan_field
    )

    assert len(with_lifespan) == 1, (
        f"`lifespan` 欄を持つ Handle が 1 件でない: {with_lifespan}"
        "（増減したなら registry のアダプタ側と `Wiring.lifespans` の期待も見直すこと）"
    )
