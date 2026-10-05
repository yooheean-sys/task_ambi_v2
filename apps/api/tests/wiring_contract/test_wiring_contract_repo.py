"""組 apps/api ⇄ repo: 実 registry エントリと実 `build` が噛み合うこと（D25）。

宣言（`registry.py` の現物）: `requires=()`／`provides=(RepoReader, RepoWriter)`／
`has_router=False`／`has_ui_page=False`。外部前提は**無い** —— repo は `.env` を読まず、起動時に
リポジトリの実在も見ない（ルートは呼び出しごとに `ProjectRepos` で渡される）。したがって
vault の T0（止め木の解決先）に当たる検査は置かず、代わりに T0 で「`build` が作業ツリーに
触れない」ことを確かめる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import CallLog, StubKickoffRunner
from conftest import (
    WiringCase,
    assemble,
    expected_lifespans,
    make_case,
    resolve_deps_handle,
)

from api.exceptions import ExtraneousOverrideError
from api.wiring import require
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.repo import RepoReader, RepoWriter

if TYPE_CHECKING:
    from pathlib import Path

WIRING_COVERS = ("repo",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case() -> WiringCase:
    """repo の組の記述（外部前提が無いので差し替えも空）。

    Returns:
        実 registry の repo エントリ 1 件を組み立てる `WiringCase`。
    """
    log = CallLog()
    overrides: dict[type[object], object] = {}
    return make_case(MODULE, overrides, log)


def test_repo_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ repo: 組み上がり、宣言 `provides` の 2 契約が全部取り出せる。

    見る欄: `registration.provides`（2 件）。期待する契約は**登録の `provides` から導く**。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    repo_reader: RepoReader = require(wiring.provided, RepoReader)
    repo_writer: RepoWriter = require(wiring.provided, RepoWriter)
    assert repo_reader is resolved[RepoReader]
    assert repo_writer is resolved[RepoWriter]
    assert repo_reader is repo_writer  # 1 つの RepoService が両方を実装する


def test_repo_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ repo: `Wiring.provided` の鍵集合が `requires ∪ provides` に一致。

    見る欄: `registration.requires`（空）・`registration.provides`（2 件）。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_repo_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ repo: 宣言外の契約を差し替え写像に 1 件足すと拒否される。

    見る欄: `registration.requires`（空）。T3b（1 件欠く）は `requires=()` なので置かない。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_repo_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ repo: `has_router` の宣言と実 `Wiring.routers` の件数が一致。

    見る欄: `registration.has_router`（False）。**期待値は宣言から導く**。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_repo_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ repo: 後始末が積まれない。

    見る欄: `RepoHandle` の `lifespan` 欄（不在）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_repo_t0_build_does_not_touch_any_work_tree(case: WiringCase, tmp_path: Path) -> None:
    """組 apps/api ⇄ repo: 組み立ては作業ツリーを作らず触らない（起動時にリポジトリを見ない）。

    見る欄: —（外部前提が無いことの確認）。未登録・未 clone のプロジェクトがあっても起動が
    止まらないことの裏付けである。
    """
    before = sorted(tmp_path.iterdir())

    assemble(case)

    assert sorted(tmp_path.iterdir()) == before
