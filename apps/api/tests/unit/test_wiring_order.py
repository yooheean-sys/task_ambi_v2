"""US2: 登録の追加だけでモジュールが増え、組み立て順が依存関係から導出される。

検証行: CR-2・CR-11（US2 AC1／AC2・FR-008）。
`unit/` に置くのは HTTP を通らないため（憲章 §8 の統合テストの定義に合わない。research R-16）。
"""

from __future__ import annotations

from _support.composition import make_root_config
from _support.fake_modules import (
    DESIGN_DEPENDENCY_LEVELS,
    TOPOLOGY_MODULE_NAMES,
    BoardContract,
    CallRecorder,
    StubImplementation,
    make_registration,
    make_topology,
)

from api.wiring import ModuleRegistration, Wiring, build_modules


def _wire_topology(registrations: tuple[ModuleRegistration, ...]) -> Wiring:
    """9 モジュールを模した偽の登録をすべて有効化して組み立てる。"""
    return build_modules(
        enabled_modules=list(TOPOLOGY_MODULE_NAMES),
        config=make_root_config(),
        registrations=registrations,
    )


def test_cr2_single_registration_is_built_once_and_provides_land() -> None:
    """CR-2・US2 AC1: 偽の登録 1 件で組み立て口が 1 回呼ばれ、`provided` に `provides` が入る。"""
    recorder = CallRecorder()
    registration = make_registration(name="alpha", recorder=recorder, provides=(BoardContract,))

    wiring = build_modules(
        enabled_modules=["alpha"], config=make_root_config(), registrations=(registration,)
    )

    assert recorder.build_count("alpha") == 1
    assert wiring.modules == ("alpha",)
    implementation = wiring.provided[BoardContract]
    assert isinstance(implementation, StubImplementation)
    assert implementation.label() == "alpha"


def test_us2_ac1_adding_one_registration_needs_no_change_to_the_wiring_code() -> None:
    """US2 AC1: 登録を 1 件足すだけで組み立て対象が増える（`registrations` 引数のみで完結）。"""
    recorder = CallRecorder()
    base = make_registration(name="alpha", recorder=recorder, provides=(BoardContract,))
    added = make_registration(name="beta", recorder=recorder, requires=(BoardContract,))

    before = build_modules(
        enabled_modules=["alpha"], config=make_root_config(), registrations=(base,)
    )
    after = build_modules(
        enabled_modules=["alpha", "beta"], config=make_root_config(), registrations=(base, added)
    )

    assert before.modules == ("alpha",)
    assert after.modules == ("alpha", "beta")
    assert recorder.build_count("beta") == 1


def test_cr11_derived_order_is_a_topological_order_of_the_design_levels() -> None:
    """CR-11・FR-008: 導出順が設計側の依存順と矛盾しない位相順序である。

    設計側は同層の並びを持たないので**完全一致では照合しない**。層 i の全モジュールが
    層 i+1 の全モジュールより前に来ることを確かめる。
    """
    order = _wire_topology(make_topology(CallRecorder())).modules

    assert set(order) == set(TOPOLOGY_MODULE_NAMES)
    assert len(order) == len(TOPOLOGY_MODULE_NAMES)

    position = {name: index for index, name in enumerate(order)}
    for earlier, later in zip(DESIGN_DEPENDENCY_LEVELS, DESIGN_DEPENDENCY_LEVELS[1:], strict=False):
        assert max(position[name] for name in earlier) < min(position[name] for name in later), (
            f"設計側の依存順に反する: {earlier} が {later} より後に来ている / 導出順: {order}"
        )


def test_cr11_same_layer_is_ordered_lexicographically() -> None:
    """CR-11: 同層はモジュール名の辞書順で決定的に並ぶ。"""
    order = _wire_topology(make_topology(CallRecorder())).modules

    for level in DESIGN_DEPENDENCY_LEVELS:
        if len(level) < 2:
            continue
        appearing = [name for name in order if name in level]
        assert appearing == sorted(level), f"同層 {level} の並びが辞書順でない: {appearing}"


def test_derived_order_does_not_depend_on_the_order_of_registrations() -> None:
    """US2 AC2: 導出順が登録集合の並びに依存しない（順序が登録の並びの写しではない）。"""
    forward = _wire_topology(make_topology(CallRecorder())).modules
    backward = _wire_topology(tuple(reversed(make_topology(CallRecorder())))).modules

    assert forward == backward


def test_each_module_is_built_exactly_once_in_the_derived_order() -> None:
    """9 モジュールすべてが 1 回ずつ、導出順どおりに組み立てられる。"""
    recorder = CallRecorder()

    wiring = _wire_topology(make_topology(recorder))

    assert tuple(recorder.builds) == wiring.modules
    assert all(recorder.build_count(name) == 1 for name in TOPOLOGY_MODULE_NAMES)


def test_build_receives_only_the_declared_contracts() -> None:
    """contracts §2-3: 組み立て口に渡る写像は `requires` に宣言した契約だけ。"""
    recorder = CallRecorder()
    registrations = make_topology(recorder)
    by_name = {registration.name: registration for registration in registrations}

    _wire_topology(registrations)

    for name, seen in recorder.seen_deps.items():
        assert set(seen) == set(by_name[name].requires), (
            f"{name} に宣言していない契約が渡っている: {sorted(str(key) for key in seen)}"
        )
