"""モジュールの配線（登録の解決・検査・依存順の導出・組み立て）。FastAPI を作らない。

振る舞いの約束は contracts/composition-root.md、型の正本は data-model.md §2〜§5。
`registry.py` は本 module の型を import するため、既定の登録集合は**関数の中で遅延 import** する。
"""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from api.config import load_root_config
from api.exceptions import (
    CircularDependencyError,
    DuplicateModuleError,
    DuplicateProviderError,
    ExtraneousOverrideError,
    HandleContractMismatchError,
    UnknownModuleError,
    UnsatisfiedContractError,
)

if TYPE_CHECKING:
    from fastapi import APIRouter

    from common.kernel.config import RootConfig


type ProtocolOverrides = Mapping[type[object], object]
"""契約の型 → その実装。鍵は Protocol クラスそのもの（FR-003・D14-e）。"""

type ModuleFactory = Callable[[Mapping[type[object], object]], WiredModule]
"""登録が持つアダプタ。引数は「その登録が `requires` に宣言した契約だけ」を含む写像。"""

type LifespanFactory = Callable[[], AbstractAsyncContextManager[None]]
"""起動終了の後始末。呼ぶたびに新しいコンテキストを返す（再入可能。research R-06）。"""


@dataclass(frozen=True, slots=True)
class ModuleRegistration:
    """apps/api が 1 モジュールについて知る唯一のまとまり（FR-007・data-model.md §3）。"""

    name: str
    requires: tuple[type[object], ...]
    provides: tuple[type[object], ...]
    build: ModuleFactory
    has_router: bool = False
    has_ui_page: bool = False


@dataclass(frozen=True, slots=True)
class WiredModule:
    """組み立て結果（`<Mod>Handle` の汎用ビュー。data-model.md §4）。"""

    provides: Mapping[type[object], object]
    router: APIRouter | None = None
    lifespan: LifespanFactory | None = None


@dataclass(frozen=True, slots=True)
class Wiring:
    """配線関数の戻り（FR-001 (a) が列挙する 4 つ。data-model.md §5）。"""

    provided: Mapping[type[object], object]
    modules: tuple[str, ...]
    routers: tuple[APIRouter, ...]
    lifespans: tuple[LifespanFactory, ...]


def contract_label(contract: type[object]) -> str:
    """契約の型を `<module>.<qualname>` で表す（contracts/composition-root.md §4）。

    Args:
        contract: 契約の型（Protocol クラス）。

    Returns:
        `common.interfaces.board.BoardReader` の形の表示名。
    """
    return f"{contract.__module__}.{contract.__qualname__}"


def require[T](deps: Mapping[type[object], object], contract: type[T]) -> T:
    """組み立て口が受け取った写像から契約実装を取り出す（`cast` を本関数 1 箇所に閉じる）。

    Args:
        deps: 契約の型 → 実装の写像。
        contract: 取り出す契約の型。

    Returns:
        `contract` を実装したインスタンス。

    Raises:
        UnsatisfiedContractError: 写像に当該契約が無いとき（登録の `requires` の宣言漏れ）。
    """
    try:
        implementation = deps[contract]
    except KeyError as exc:
        available = sorted(contract_label(key) for key in deps)
        msg = (
            f"契約 {contract_label(contract)} が組み立て口に渡された写像に無い"
            f" / 渡された契約: {available}"
            " / 充足手段: 当該モジュールの登録の `requires` にこの契約を宣言すること"
        )
        raise UnsatisfiedContractError(msg) from exc
    return cast("T", implementation)


def build_modules(
    *,
    enabled_modules: Sequence[str] | None = None,
    overrides: ProtocolOverrides | None = None,
    config: RootConfig | None = None,
    registrations: Sequence[ModuleRegistration] | None = None,
) -> Wiring:
    """有効化されたモジュールを依存順に組み立てる（contracts/composition-root.md §2・§3）。

    Args:
        enabled_modules: 有効化するモジュール名。`None` ならルート設定の `enabled_modules`
            （CFG-20）。**`[]` は「0 個を明示」であり `None` と別物**。
        overrides: 契約の型 → 差し替える実装。`None` なら空写像。
        config: ルート設定。`None` なら `load_root_config()` で読む。
        registrations: 登録集合。`None` なら `registry.MODULE_REGISTRATIONS`。

    Returns:
        提供された契約実装の写像・組み立てられたモジュール名（依存順）・公開経路・
        起動終了の後始末を持つ `Wiring`。

    Raises:
        ConfigurationError: ルート設定の不備。
        DuplicateModuleError: 有効 MOD 一覧に同じ名前が 2 回以上ある。
        UnknownModuleError: 有効 MOD 一覧に登録の無い名前がある。
        DuplicateProviderError: 同じ契約が 2 つ以上から供給される。
        ExtraneousOverrideError: 誰も要求しない契約への override がある。
        UnsatisfiedContractError: 供給元の無い契約が残る。
        CircularDependencyError: インスタンスレベルの相互依存がある。
        HandleContractMismatchError: 宣言 `provides` と実物の鍵集合が一致しない。
    """
    resolved_config = config if config is not None else load_root_config()
    names = (
        tuple(enabled_modules)
        if enabled_modules is not None
        else tuple(resolved_config.enabled_modules)
    )
    available = tuple(registrations) if registrations is not None else _default_registrations()
    return _assemble(names, available, dict(overrides) if overrides is not None else {})


def _default_registrations() -> tuple[ModuleRegistration, ...]:
    """`registry.MODULE_REGISTRATIONS` を遅延 import で読む。

    `registry.py` が本 module の型を import するため、module 先頭で import すると輪になる。

    Returns:
        既定の登録集合。
    """
    from api.registry import MODULE_REGISTRATIONS

    return MODULE_REGISTRATIONS


def _assemble(
    names: tuple[str, ...],
    available: tuple[ModuleRegistration, ...],
    overrides: dict[type[object], object],
) -> Wiring:
    """検査と組み立ての本体（contracts/composition-root.md §3 の段 3〜10）。

    Args:
        names: 解決済みの有効 MOD 一覧。
        available: 解決済みの登録集合。
        overrides: 解決済みの override 写像。

    Returns:
        組み立て結果の `Wiring`。
    """
    _check_duplicate_modules(names)
    by_name = {registration.name: registration for registration in available}
    _check_known_modules(names, by_name)
    selected = tuple(by_name[name] for name in names)
    provider_of = _provider_index(selected)
    _check_overrides(selected, provider_of, overrides)
    _check_unsatisfied(selected, provider_of, frozenset(overrides))
    order = _topological_order(selected, provider_of)
    return _run_builds(order, by_name, overrides)


def _check_duplicate_modules(names: tuple[str, ...]) -> None:
    """有効 MOD 一覧の重複を検出する（contracts §3 の段 3・FR-049）。

    黙って除去しない。同じ登録が 2 回並ぶと二重供給の検査に先に掛かり、原因と食い違う
    失敗（「自分自身と二重供給している」）が出るため、**二重供給の検査より前**に落とす。

    Args:
        names: 解決済みの有効 MOD 一覧。

    Raises:
        DuplicateModuleError: 同じ名前が 2 回以上あるとき。
    """
    counts = Counter(names)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if not duplicates:
        return
    detail = ", ".join(f"{name} × {counts[name]} 回" for name in duplicates)
    msg = (
        f"有効 MOD 一覧に同じ名前が 2 回以上ある: {duplicates} / 重複回数: {detail}"
        " / 充足手段: config.json の enabled_modules（または enabled_modules 引数）から"
        "重複を除くこと"
    )
    raise DuplicateModuleError(msg)


def _check_known_modules(names: tuple[str, ...], by_name: Mapping[str, ModuleRegistration]) -> None:
    """有効 MOD 一覧の各名前に登録が在ることを検査する（contracts §3 の段 4・FR-012）。

    Args:
        names: 解決済みの有効 MOD 一覧。
        by_name: モジュール名 → 登録。

    Raises:
        UnknownModuleError: 登録の無い名前があるとき。
    """
    unknown = sorted({name for name in names if name not in by_name})
    if not unknown:
        return
    msg = (
        f"有効 MOD 一覧に登録の無いモジュール名がある: {unknown}"
        f" / 登録済み: {sorted(by_name)}"
        " / 充足手段: registry.py に登録を足すか、config.json の enabled_modules から外すこと"
    )
    raise UnknownModuleError(msg)


def _check_overrides(
    selected: Sequence[ModuleRegistration],
    provider_of: Mapping[type[object], str],
    overrides: Mapping[type[object], object],
) -> None:
    """差し替え写像の鍵を検査する（contracts §3 の段 6・FR-045・FR-046）。

    **未充足の検査より先に**行う。鍵が Protocol 型であるため取り違えが型検査を通ってしまい、
    それを「未充足」として報告すると原因（鍵の間違い）ではなく結果（穴が埋まっていない）が出る。

    Args:
        selected: 有効化された登録。
        provider_of: 契約 → 提供モジュール名。
        overrides: override 写像。

    Raises:
        ExtraneousOverrideError: 有効化されたどのモジュールも要求しない鍵があるとき。
        DuplicateProviderError: 有効化されたモジュールが提供する契約の鍵があるとき。
    """
    required = {contract for registration in selected for contract in registration.requires}
    extraneous = [contract for contract in overrides if contract not in required]
    if extraneous:
        msg = (
            "override に、有効化されたどのモジュールも要求しない契約がある: "
            f"{sorted(contract_label(contract) for contract in extraneous)}"
            f" / 要求されている契約: {sorted(contract_label(contract) for contract in required)}"
        )
        raise ExtraneousOverrideError(msg)

    duplicated = [contract for contract in overrides if contract in provider_of]
    if duplicated:
        first = duplicated[0]
        owner = provider_of[first]
        msg = (
            f"契約 {contract_label(first)} は有効化された {owner} が提供しており、"
            "override と二重供給になる"
            f" / 二重供給の契約: {sorted(contract_label(contract) for contract in duplicated)}"
            f" / 充足手段: {owner} を有効 MOD 一覧から外すこと"
            "（override でモジュールの組み立ては抑止されない）"
        )
        raise DuplicateProviderError(msg)


def check_registration_graph(registrations: Sequence[ModuleRegistration]) -> tuple[str, ...]:
    """全登録を有効化したときの依存順を、**組み立てを行わずに**導出する（REG-8f）。

    整合検査（`apps/api/tests/foundation/test_registry_registration.py`）が「設計上の依存関係が
    閉じていること」を、実物の `build` を 1 つも呼ばずに確かめるための入口。

    Args:
        registrations: 検査する登録集合（override は考慮しない）。

    Returns:
        導出した依存順のモジュール名。

    Raises:
        DuplicateProviderError: 同じ契約を 2 つ以上が提供している。
        UnsatisfiedContractError: 供給元の無い契約が残る。
        CircularDependencyError: インスタンスレベルの相互依存がある。
    """
    selected = tuple(registrations)
    provider_of = _provider_index(selected)
    _check_unsatisfied(selected, provider_of, frozenset())
    return _topological_order(selected, provider_of)


def _provider_index(selected: Sequence[ModuleRegistration]) -> dict[type[object], str]:
    """契約 → 提供モジュール名の索引を作る（contracts §3 の段 5）。

    Args:
        selected: 有効化された登録。

    Returns:
        契約の型 → 提供モジュール名。

    Raises:
        DuplicateProviderError: 同じ契約を 2 つ以上が提供しているとき。
    """
    provider_of: dict[type[object], str] = {}
    for registration in selected:
        for contract in registration.provides:
            owner = provider_of.get(contract)
            if owner is not None:
                msg = (
                    f"契約 {contract_label(contract)} を有効化された 2 つのモジュールが"
                    f"提供している: {owner}, {registration.name}"
                    " / 充足手段: いずれかを有効 MOD 一覧から外すこと"
                )
                raise DuplicateProviderError(msg)
            provider_of[contract] = registration.name
    return provider_of


def _check_unsatisfied(
    selected: Sequence[ModuleRegistration],
    provider_of: Mapping[type[object], str],
    override_keys: frozenset[type[object]],
) -> None:
    """供給元の無い契約が残っていないことを検査する（contracts §3 の段 7・FR-011）。

    Args:
        selected: 有効化された登録。
        provider_of: 契約 → 提供モジュール名。
        override_keys: override が埋める契約の集合。

    Raises:
        UnsatisfiedContractError: 未充足の契約があるとき（契約・要求元・充足手段を本文に載せる）。
    """
    missing: dict[type[object], list[str]] = {}
    for registration in selected:
        for contract in registration.requires:
            if contract in provider_of or contract in override_keys:
                continue
            missing.setdefault(contract, []).append(registration.name)
    if not missing:
        return

    details = " / ".join(
        f"契約 {contract_label(contract)} の供給元が無い（要求元: {sorted(requesters)}）"
        for contract, requesters in missing.items()
    )
    sample = contract_label(next(iter(missing)))
    msg = (
        f"供給元の無い契約が残っている: {details}"
        " / 充足手段: 提供するモジュールを有効 MOD 一覧に加えるか、"
        f"overrides={{{sample}: <実装>}} を渡すこと"
    )
    raise UnsatisfiedContractError(msg)


def _topological_order(
    selected: Sequence[ModuleRegistration], provider_of: Mapping[type[object], str]
) -> tuple[str, ...]:
    """提供元 → 利用元の有向グラフを Kahn の算法で位相整列する（contracts §3 の段 8・FR-008）。

    同じ層に並ぶ（互いに依存しない）ものはモジュール名の**辞書順**で決定的に並べる。
    override が埋める契約には提供元のノードが無いので辺を作らない。

    Args:
        selected: 有効化された登録。
        provider_of: 契約 → 提供モジュール名。

    Returns:
        依存順のモジュール名。

    Raises:
        CircularDependencyError: 整列が全ノードを消化できないとき（残りが輪の当事者）。
    """
    dependencies: dict[str, set[str]] = {registration.name: set() for registration in selected}
    dependents: dict[str, set[str]] = {registration.name: set() for registration in selected}
    for registration in selected:
        for contract in registration.requires:
            provider = provider_of.get(contract)
            if provider is None:
                continue
            dependencies[registration.name].add(provider)
            dependents[provider].add(registration.name)

    ready = [name for name, waiting in dependencies.items() if not waiting]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for consumer in sorted(dependents[name]):
            dependencies[consumer].discard(name)
            if not dependencies[consumer]:
                heapq.heappush(ready, consumer)

    if len(order) != len(dependencies):
        remaining = set(dependencies) - set(order)
        raise CircularDependencyError(_circular_message(remaining, selected, provider_of))
    return tuple(order)


def _circular_message(
    remaining: set[str],
    selected: Sequence[ModuleRegistration],
    provider_of: Mapping[type[object], str],
) -> str:
    """輪の当事者すべてと、各者が要求している契約を並べる（contracts §4）。

    Args:
        remaining: 整列で消化できなかったノード（＝輪の当事者）。
        selected: 有効化された登録。
        provider_of: 契約 → 提供モジュール名。

    Returns:
        例外の本文。
    """
    by_name = {registration.name: registration for registration in selected}
    parts: list[str] = []
    for name in sorted(remaining):
        for contract in by_name[name].requires:
            provider = provider_of.get(contract)
            if provider is not None and provider in remaining:
                parts.append(f"{name} は {contract_label(contract)} を要求し {provider} が提供")
    return (
        "インスタンスレベルの相互依存を検出した（憲章 §3 の設計見直しシグナル）:"
        f" 輪の当事者 {sorted(remaining)} / " + " / ".join(parts)
    )


def _run_builds(
    order: tuple[str, ...],
    by_name: Mapping[str, ModuleRegistration],
    overrides: Mapping[type[object], object],
) -> Wiring:
    """依存順に組み立て口を呼び、結果を `Wiring` にまとめる（contracts §3 の段 9・段 10）。

    **巻き戻さない**（FR-044）。組み立て口が例外を送出したら、既に組み立てたものに触れず
    そのまま伝播させる。

    Args:
        order: 依存順のモジュール名。
        by_name: モジュール名 → 登録。
        overrides: override 写像。`resolved` の初期値になり、`Wiring.provided` にも**残る**
            （実際に配線に使われたため。data-model §5）。組み立て口ではないので、値に
            後始末を持たせても呼ばれる経路が存在しない（FR-043）。

    Returns:
        組み立て結果の `Wiring`。

    Raises:
        HandleContractMismatchError: 宣言 `provides` と実物の鍵集合が一致しないとき。
    """
    resolved: dict[type[object], object] = dict(overrides)
    routers: list[APIRouter] = []
    lifespans: list[LifespanFactory] = []

    for name in order:
        registration = by_name[name]
        deps = MappingProxyType(
            {contract: resolved[contract] for contract in registration.requires}
        )
        wired = registration.build(deps)
        declared = set(registration.provides)
        actual = set(wired.provides)
        if actual != declared:
            msg = (
                f"{name} の登録は {sorted(contract_label(c) for c in declared)} を宣言したが、"
                f"組み立て口が返したのは {sorted(contract_label(c) for c in actual)}"
                f" / 欠け: {sorted(contract_label(c) for c in declared - actual)}"
                f" / 余分: {sorted(contract_label(c) for c in actual - declared)}"
            )
            raise HandleContractMismatchError(msg)
        resolved.update(wired.provides)
        if wired.router is not None:
            routers.append(wired.router)
        if wired.lifespan is not None:
            lifespans.append(wired.lifespan)

    return Wiring(
        provided=MappingProxyType(resolved),
        modules=order,
        routers=tuple(routers),
        lifespans=tuple(lifespans),
    )
