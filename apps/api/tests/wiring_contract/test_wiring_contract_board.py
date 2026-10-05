"""組 apps/api ⇄ board: 実 registry エントリと実 `build` が噛み合うこと（US2・US4）。

宣言（`registry.py` の現物）: `requires=()`／`provides=(BoardReader, BoardWriter)`／
`has_router=False`／`has_ui_page=False`。

**本 feature で唯一 `requires_bd` を付けるファイル**である（規約 §6。module 大域に `pytestmark` で
1 回だけ置き、関数ごとに付けない）。`bd` を持たない環境では `uv run pytest -m "not requires_bd"` で
本ファイルだけが deselect され、残る 9 組が全件走る（SC-008）。

⚠ **board の `build` は `bd` を 1 度も実行しない。** 起動時検査（EIF-01）は (a) `BEADS_DIR` →
(b) 実在 → (c) 直下の `config.yaml` → (d) `BD_BIN` を `shutil.which` ＋ `os.access(X_OK)` で確かめる
→ (e) `config.json` の 5 段で、**ホワイトリスト外のサブコマンドを打たない**（`board/__init__.py` の
`build` docstring が FR-048・R-15 として明言）。したがって **`bd init` を打たない軽量 beads**
（`.beads/config.yaml` を置くだけ）で足り、`013` の contract 層のような 120 秒枠を要さない。

⚠ **偽の実行可能ファイルで代用しない**（FR-013）——起動時検査 (d) が **PATH 上の実 `bd`** を解決
できることが `requires_bd` の意味だからである。`BD_BIN` は ルート `apps/api/tests/conftest.py` の
既存 `bd_bin` fixture が PATH から解決したものを渡す（本層に移植しない）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.contract_stubs import CallLog, StubKickoffRunner
from conftest import (
    WiringCase,
    assemble,
    assert_env_path_under,
    expected_lifespans,
    make_case,
    resolve_deps_handle,
)

from api.exceptions import ExtraneousOverrideError
from api.wiring import require
from board import BoardEnv
from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.orchestrator import KickoffRunner

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.requires_bd
"""実 `bd` を PATH から解決する（規約 §6。**新しいマーカを登録しない**——`requires_bd` はルート
`pyproject.toml` に登録済みで、そのファイルは変更禁止である）。"""

WIRING_COVERS = ("board",)
"""本ファイルが担う組（被覆検査 C-1／C-2 が読む。規約 §2）。"""

MODULE = WIRING_COVERS[0]
"""モジュール名は宣言から引く（ファイル内で 2 度書かない）。"""


@pytest.fixture
def case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    light_beads: Path,
    bd_bin: str,
    isolated_env_file: Path,
) -> WiringCase:
    """board の組の記述（軽量 beads ＋ PATH 上の実 `bd`。`build` の**前**に解決先を確かめる）。

    `BEADS_DIR` は `.beads` **自身**であって、それを持つ親ではない（D19-a・D19-b）。

    Returns:
        実 registry の board エントリ 1 件を組み立てる `WiringCase`。
    """
    monkeypatch.setenv("BEADS_DIR", str(light_beads))
    monkeypatch.setenv("BD_BIN", bd_bin)
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=isolated_env_file)

    log = CallLog()
    overrides: dict[type[object], object] = {}
    return make_case(MODULE, overrides, log)


def test_board_t1_declared_provides_are_all_resolvable(case: WiringCase) -> None:
    """組 apps/api ⇄ board: 組み上がり、宣言 `provides` の 2 契約が全部取り出せる（FR-008・US2
    AC-3）。

    見る欄: `registration.provides`（2 件）。実 `bd` を PATH から解決する起動時検査 (d) を通る唯一の
    組であり、**このテストにだけ `requires_bd` が効いている**（module 大域の `pytestmark`）。
    """
    wiring = assemble(case)

    resolved = {
        contract: require(wiring.provided, contract) for contract in case.registration.provides
    }
    assert set(resolved) == set(case.registration.provides)
    assert wiring.modules == (MODULE,)

    # 規約 §3-2: 取り出しは Protocol 型で注釈する
    board_reader: BoardReader = require(wiring.provided, BoardReader)
    board_writer: BoardWriter = require(wiring.provided, BoardWriter)
    assert board_reader is resolved[BoardReader]
    assert board_writer is resolved[BoardWriter]


def test_board_t2_provided_keys_are_exactly_requires_union_provides(case: WiringCase) -> None:
    """組 apps/api ⇄ board: `Wiring.provided` の鍵集合が `requires ∪ provides`
    に一致（FR-011 (a)）。

    見る欄: `registration.requires`（空）・`registration.provides`（2 件）。
    """
    wiring = assemble(case)

    expected = set(case.registration.requires) | set(case.registration.provides)

    assert set(wiring.provided) == expected


def test_board_t3a_extraneous_override_is_rejected(case: WiringCase) -> None:
    """組 apps/api ⇄ board: 宣言外の契約を差し替え写像に 1 件足すと拒否される（FR-011 (b)）。

    見る欄: `registration.requires`（空）。⚠ **T3b（1 件欠く）は置かない**——`requires=()` なので
    「欠く」対象が構造上存在しない（規約 §3-3）。
    """
    kickoff_runner: KickoffRunner = StubKickoffRunner(case.call_log)
    assert KickoffRunner not in case.registration.requires

    extraneous: dict[type[object], object] = {**case.overrides, KickoffRunner: kickoff_runner}

    with pytest.raises(ExtraneousOverrideError):
        assemble(case, overrides=extraneous)


def test_board_t4_router_presence_matches_the_has_router_declaration(case: WiringCase) -> None:
    """組 apps/api ⇄ board: `has_router` の宣言と実 `Wiring.routers` の件数が一致（FR-018・GAP-1）。

    見る欄: `registration.has_router`（False）。**期待値は宣言から導く**（SC-015 の変異 1）。
    """
    wiring = assemble(case)

    assert len(wiring.routers) == (1 if case.registration.has_router else 0)


def test_board_t5_no_lifespan_is_registered(case: WiringCase) -> None:
    """組 apps/api ⇄ board: 後始末が積まれない（FR-020・GAP-2）。

    見る欄: `BoardHandle` の `lifespan` 欄（不在）。正は `<Mod>Handle`
    の宣言属性である（**B-03**）。
    """
    _, handle_cls = resolve_deps_handle(MODULE)
    wiring = assemble(case)

    assert len(wiring.lifespans) == expected_lifespans(handle_cls)


def test_board_t0_light_beads_has_no_bd_init_side_effects(light_beads: Path) -> None:
    """組 apps/api ⇄ board: 軽量 beads に `bd init` の副作用が無い（FR-013・FR-031）。

    見る欄: —（外部前提）。`bd init` は `.beads/` だけでなく **git リポジトリを作り commit まで
    行う**ため、打っていないことを痕跡の不在で確かめる（サブディレクトリでの `git init`・
    `git commit` を行わない＝ FR-031）。目印は `config.yaml` の 1 つだけである。
    """
    workspace = light_beads.parent

    assert light_beads.is_dir()
    assert (light_beads / "config.yaml").is_file()
    assert not (workspace / ".git").exists()
    assert sorted(entry.name for entry in light_beads.iterdir()) == ["config.yaml"]
