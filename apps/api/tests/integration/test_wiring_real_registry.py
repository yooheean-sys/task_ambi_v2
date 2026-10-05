"""実の `MODULE_REGISTRATIONS` で組み立てが噛み合うことを確かめる（FR-069a・SC-014a・US5 AC-9）。

`monitor` は **`requires` が非空になる初の登録**であり、`require(deps, BoardReader)`／
`require(deps, BoardWriter)` の経路が実際に通るのはここが初めてである。静的検査（REG-8f＝
`check_registration_graph`）は現物が「**組み立てを行わずに**」「実物の `build` を 1 つも呼ばずに」
依存順を導く（`wiring.py:281-301`）ため、この噛み合いを測れない。

**偽の登録を使わない** —— `registrations=` を渡さないので `wiring._default_registrations()` が
`api.registry.MODULE_REGISTRATIONS` を遅延 import で読む（`wiring.py:154-163`）。

**`enabled_modules` を 2 件に絞る理由**: 5 件を実際に組むと vault の保管庫パスや settings の
宣言的設定ファイルなど**外部前提**を引き込む。monitor が要求するのは盤面の 2 契約だけなので、
`requires` 非空の経路の検証には 2 件で足りる。5 要素・12 契約・依存順の全体は静的検査
（REG-8e・REG-8f）が測る。
"""

from __future__ import annotations

from _support.composition import make_root_config

from api.wiring import build_modules
from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.monitor import LLMHttpHook, RunMonitor

MODULES = ["board", "monitor"]
"""実配線で組む 2 件（`monitor` の `requires` を `board` が満たす最小の組）。"""


def test_real_registry_assembles_without_error() -> None:
    """① 実の登録で例外なく組み上がる。"""
    wiring = build_modules(
        enabled_modules=MODULES, config=make_root_config(enabled_modules=MODULES)
    )
    assert wiring.modules


def test_monitor_requires_are_resolved_from_board() -> None:
    """② `require(deps, BoardReader)`／`require(deps, BoardWriter)` が実際に解決される。

    `_build_monitor` が受け取る写像に盤面の 2 契約が無ければ `UnsatisfiedContractError` が
    飛ぶ。組み上がった時点でこの経路が通ったことになる。
    """
    wiring = build_modules(
        enabled_modules=MODULES, config=make_root_config(enabled_modules=MODULES)
    )
    assert BoardReader in wiring.provided
    assert BoardWriter in wiring.provided


def test_monitor_provides_two_distinct_instances() -> None:
    """③ `RunMonitor`・`LLMHttpHook` が取り出せ、**別インスタンス**である。"""
    wiring = build_modules(
        enabled_modules=MODULES, config=make_root_config(enabled_modules=MODULES)
    )
    run_monitor = wiring.provided[RunMonitor]
    llm_http_hook = wiring.provided[LLMHttpHook]
    assert run_monitor is not llm_http_hook


def test_board_is_assembled_before_monitor() -> None:
    """④ 依存順は `board` → `monitor`（供給側が先）。"""
    wiring = build_modules(
        enabled_modules=MODULES, config=make_root_config(enabled_modules=MODULES)
    )
    assert wiring.modules == ("board", "monitor")


def test_monitor_adds_no_router() -> None:
    """⑤ monitor は公開経路を持たないので `routers` が増えない（FR-012）。"""
    with_monitor = build_modules(
        enabled_modules=MODULES, config=make_root_config(enabled_modules=MODULES)
    )
    board_only = build_modules(
        enabled_modules=["board"], config=make_root_config(enabled_modules=["board"])
    )
    assert len(with_monitor.routers) == len(board_only.routers)
