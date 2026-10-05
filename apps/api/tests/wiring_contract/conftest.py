"""配線契約テスト層の器（止め木・一時ストア・型解決・組み立てヘルパ）。

`specs/015-wiring-contracts` の規約 §3〜§5 が正本。本層は **実 `MODULE_REGISTRATIONS` の 1
エントリと、その向こう側の実体（`<mod>.build(<Mod>Deps) -> <Mod>Handle`）が噛み合うこと**だけを
見る。組み上がった後の振る舞い・契約メソッドの入出力・Handle の内部形は見ない（規約 §1）。

★ **止め木**（`isolated_env_file`）が本ファイルの核である。実リポジトリの `.env` は
`BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT`・`TRIGGER_ROOT`・`DOWNLOADS_DIR` を**実値**で持ち、
settings の `build` は `_scaffold` が・trigger の `build` は `ensure_template` が**雛形を書き込む**
（WD-09）。止め木は `build` の**前**に効いていなければならず、事後確認では手遅れになる（FR-029）。

一時ストアの組み立ては `wiring_stores.py` へ割ってある（plan (vii)-3 の分割規則）。

`contract/conftest.py` の同名 fixture を **import せず同型移植**する。`apps/api/tests/contract/`
直下の補助は、pytest が当該ディレクトリを収集したときにだけ `sys.path` に載るため、import すると
**実行順序依存**になる（FR-033・spec §0-5 (4) の実測）。
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import pkgutil
import typing
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _support.composition import make_root_config
from _support.contract_stubs import CallLog
from fastapi import APIRouter
from wiring_stores import make_area_a, make_area_b, make_light_beads, make_vault_root

from api.registry import MODULE_REGISTRATIONS
from api.wiring import ModuleRegistration, Wiring, build_modules

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from pydantic_settings import BaseSettings

    from common.kernel.config import RootConfig

# --------------------------------------------------------------------------------------
# §4 止め木（`build` の前に効かせる）
# --------------------------------------------------------------------------------------

ENV_FILE_PATH_TARGETS = (
    "board.env_file_path",
    "vault.env_file_path",
    "settings.env_file_path",
    "trigger.env_file_path",
    "executor.models.env_file_path",
)
"""`.env` を読む 5 MOD の差し替え点（規約 §4-1）。

`013` の 4 件に **`trigger.env_file_path`** を足した 5 件である（`013` は trigger を扱っていない）。
**executor だけ `models` 側**でないと効かない（再エクスポートを差し替えても `models.py` が
実行ごとに引く関数は元のまま）。
"""

CLEARED_ENV_KEYS = (
    "BEADS_DIR",
    "BD_BIN",
    "VAULT_ROOT",
    "SETTINGS_ROOT",
    "TRIGGER_ROOT",
    "DOWNLOADS_DIR",
)
"""OS 環境変数の側から実運用ストアが漏れないよう消すキー（`.env` より優先されるため）。"""

API_KEY_ENV_PREFIX = "LLM_API_KEY"
"""実 LLM API の鍵（FR-030。前方一致で消す）。"""


@pytest.fixture(autouse=True)
def isolated_env_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """実 `.env`・実運用ストア・実 LLM API へ届かない既定に倒す（FR-029・FR-030・規約 §4）。

    2 段構えの 1 段目である。**先に安全側へ倒し**、MOD 別の fixture が 2 段目として一時
    ディレクトリへ指し直す。指し直しを忘れたテストは `build()` が「キーが無い」で落ちるので、
    実物を掴んだまま緑になることが構造的に起きない。

    **autouse だが名前でも要求できる** —— MOD 別の fixture は `build()` の前に
    `assert_env_path_under(..., env_file=isolated_env_file)` を通すためにこの値を要る
    （`board.env_file_path` を import 済みの名前で呼ぶと差し替え**前**の関数を掴むので、
    差し替えた `.env` の所在は fixture 経由で受け取る）。

    Yields:
        差し替え先の空の `.env` の絶対パス。
    """
    empty_env = tmp_path_factory.mktemp("wiring_env") / ".env"
    empty_env.write_text("", encoding="utf-8")
    for target in ENV_FILE_PATH_TARGETS:
        monkeypatch.setattr(target, lambda: empty_env)
    for key in CLEARED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key in [name for name in os.environ if name.startswith(API_KEY_ENV_PREFIX)]:
        monkeypatch.delenv(key, raising=False)
    yield empty_env


def assert_env_path_under(
    env_cls: type[BaseSettings],
    field: str,
    tmp_path: Path,
    *,
    env_file: Path,
) -> Path:
    """`build()` を呼ぶ**前**に、その MOD が読む解決先が `tmp_path` 配下かを確かめる（規約 §4-3）。

    `build()` そのものを検査に使わない —— settings の `build()` は `_scaffold` が、trigger の
    `build()` は `ensure_template` が雛形を**書き込む**ため、事後確認では手遅れになる。読み方は
    `build()` と**同じ経路**（`<Mod>Env(_env_file=env_file_path())`）にする。

    ⚠ **止め木が効くのは `<Mod>Env` の明示 `__init__` と `build()` 側の `_env_file` 指定が
    「対で」在るときだけ**である。どちらかが失われるとテストは緑のまま実運用ストアを掴む
    （`013` の実測）。本関数はその前提が崩れていないことを毎回確かめる位置に置く。

    使い分け: **board・vault・settings・trigger の 4 MOD が本関数**、**executor だけ形が違い
    `assert_env_file_under`** を使う（`build` が `.env` を読まず `<Mod>Env` に Path フィールドが
    無いため。2026-09-01 裁定＝ WD-39・研究 R-05）。

    Args:
        env_cls: その MOD の `<Mod>Env`（`BoardEnv`・`VaultEnv`・`SettingsEnv`・`TriggerEnv`）。
        field: 解決先を持つフィールド名（`BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT`・
            `TRIGGER_ROOT`）。
        tmp_path: 配下であるべき一時ディレクトリ。
        env_file: `build()` が読む `.env` の絶対パス（`env_file_path()` の戻り）。

    Returns:
        解決済みの絶対パス（`tmp_path` 配下であることが確かめられたもの）。
    """
    expected = tmp_path.resolve()
    try:
        env = env_cls(_env_file=env_file)
    except Exception as exc:
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__} を build() と同じ経路で読めない: {exc}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    raw: object = getattr(env, field, None)
    if not isinstance(raw, Path):
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__}.{field} が Path でない: {raw!r}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    resolved = raw.expanduser().resolve()
    if not resolved.is_relative_to(expected):
        pytest.fail(
            "止め木の能動検査: 解決先が一時ディレクトリの外にある"
            "（実物に触れる前に止めた＝ FR-028・FR-029）\n"
            f"  読んだキー名: {field}\n"
            f"  解決値: {resolved}\n"
            f"  期待した接頭辞: {expected}\n"
            f"  読んだ .env: {env_file}"
        )
    return resolved


def assert_env_file_under(env_file: object, expected_root: Path) -> Path:
    """差し替え点の**戻り**が一時 `.env` であることを確かめる（executor 専用。規約 §4-3）。

    executor に `assert_env_path_under` を使えないのは、`build` が `.env` を読まず `ExecutorEnv`
    に Path フィールドが無いためである（`models.py` が実行ごとに `dotenv_values(env_file_path())`
    を引く）。差し替え点そのものは `Path` を返すので、**別の形の能動検査なら置ける**——これにより
    差し替え点 5 件すべてが実際に効いていることを実出力で示す（FR-029・FR-030 の証跡を兼ねる）。

    ⚠ **境界は `tmp_path` ではなく pytest の一時ルート**（`tmp_path_factory.getbasetemp()`）である。
    `isolated_env_file` は `013` からの同型移植で `tmp_path_factory.mktemp()` を使うため、`.env` は
    `tmp_path` の**兄弟**（`{basetemp}/wiring_env0/.env`）に置かれ、`tmp_path` 配下には**入らない**
    （実測）。plan／research R-05／規約 §4-3／SC-012 は境界を `tmp_path` と書いているが、その組み
    合わせでは検査が構造的に必ず落ちる —— 記録は `design-sync.md` **B-13**。検査の**目的**
    （差し替えが効いており実 `.env` を掴んでいないこと）はこの境界で満たされる。

    Args:
        env_file: `executor.models.env_file_path()` の戻り。**宣言型ではなく実行時の値**を見るため
            `object` で受ける（宣言型で受けると「`Path` であること」の検査が静的に自明になり、
            差し替えが壊れて別物が返る場合を捕まえられない）。
        expected_root: 配下であるべき一時ルート（pytest の basetemp）。

    Returns:
        解決済みの絶対パス。
    """
    expected = expected_root.resolve()
    if not isinstance(env_file, Path):
        pytest.fail(f"止め木の能動検査: 差し替え点の戻りが Path でない: {env_file!r}")
    resolved = env_file.expanduser().resolve()
    if not resolved.is_relative_to(expected):
        pytest.fail(
            "止め木の能動検査: 差し替え点の戻りが一時ディレクトリの外にある"
            "（実 .env を掴んでいる＝ FR-029・FR-030）\n"
            f"  戻り値: {resolved}\n"
            f"  期待した接頭辞: {expected}"
        )
    if not resolved.is_file():
        pytest.fail(f"止め木の能動検査: 差し替え先の .env が実在しない: {resolved}")
    return resolved


# --------------------------------------------------------------------------------------
# §5 外部前提（一時ストア。組み立て本体は `wiring_stores.py`）
# --------------------------------------------------------------------------------------
#
# **明示の teardown を置かない**（FR-028・Assumptions）。実 build が作る副作用は `tmp_path` 配下
# （settings の `_scaffold`・trigger の `ensure_template` が書く雛形）と `bd init` を打たない軽量
# beads に閉じるので、pytest の一時ディレクトリの後始末で足りる。


@pytest.fixture
def light_beads(tmp_path: Path) -> Path:
    """`bd init` を**打たない**軽量 beads（規約 §5・FR-013）。

    Returns:
        `BEADS_DIR` に渡す値（`.beads` **自身**＝ D19-a・D19-b）。
    """
    return make_light_beads(tmp_path)


@pytest.fixture
def temp_vault_root(tmp_path: Path) -> Path:
    """保管庫ルートの空ディレクトリ（vault の `build` は実在確認しかしない）。

    Returns:
        `VAULT_ROOT` に渡す値。
    """
    return make_vault_root(tmp_path)


@pytest.fixture
def temp_area_a(tmp_path: Path) -> Path:
    """領域 A の空ディレクトリ（**中身は settings の `build` の `_scaffold` が書く**）。

    Returns:
        `SETTINGS_ROOT` に渡す値。
    """
    return make_area_a(tmp_path)


@pytest.fixture
def temp_area_b(tmp_path: Path) -> Path:
    """領域 B の空ディレクトリ（**中身は trigger の `build` の `ensure_template` が書く**）。

    Returns:
        `TRIGGER_ROOT` に渡す値。
    """
    return make_area_b(tmp_path)


# --------------------------------------------------------------------------------------
# §2-4 `LOCALNS`（注釈解決の名前空間。19 名）と §2-3 型解決
# --------------------------------------------------------------------------------------


def _collect_interface_protocols() -> dict[str, type]:
    """`common.interfaces` の submodule で**定義された** Protocol を集める（research R-03）。

    再エクスポートを拾わないよう `value.__module__ == module.__name__` で絞る。実測で
    **16 件**ちょうど（`p4_module_contract.csv` の `Protocol名` 列と一致。15・16 件目は D25）。

    Returns:
        Protocol 名 → Protocol クラス。
    """
    package = import_module("common.interfaces")
    collected: dict[str, type] = {}
    for info in pkgutil.iter_modules(list(package.__path__)):
        module = import_module(f"{package.__name__}.{info.name}")
        for name, value in vars(module).items():
            if not isinstance(value, type):
                continue
            if getattr(value, "__module__", None) != module.__name__:
                continue
            if bool(getattr(value, "_is_protocol", False)):
                collected[name] = value
    return collected


INTERFACE_PROTOCOLS: dict[str, type] = _collect_interface_protocols()
"""`common.interfaces` の 16 Protocol（名前 → クラス）。件数はハードコードしない。"""

LOCALNS: dict[str, object] = {
    **INTERFACE_PROTOCOLS,
    "APIRouter": APIRouter,
    "Callable": Callable,
    "AbstractAsyncContextManager": AbstractAsyncContextManager,
}
"""`get_type_hints` に与える 19 名（16 Protocol ＋ 3）。

`<Mod>Deps`／`<Mod>Handle` は Protocol の import を `if TYPE_CHECKING:` の中に持つため、素の
`get_type_hints` では 16 クラス中 11 クラスが `NameError` になる。⚠ **`AbstractAsyncContextManager`
を落とすと `TriggerHandle` だけが `NameError`** になる（`TriggerHandle.lifespan` の宣言型が
`Callable[[], AbstractAsyncContextManager[None]]` であるため＝ research R-03・WD-31）。
"""


def resolve_deps_handle(module_name: str) -> tuple[type, type]:
    """`<mod>.build` の注釈から `<Mod>Deps`／`<Mod>Handle` を引く（research R-02）。

    **D11-f の共通形（`build(deps: <Mod>Deps) -> <Mod>Handle`）そのものを正本にする**形である。
    クラス名の綴り推測（`name.capitalize() + "Deps"`）を使わないのは、8 MOD が偶然すべて単一の
    小文字語だから通るだけで命名規則への依存が残るからである。共通形が崩れれば（`build` の第 1
    引数が別の型になる等）**この同定自体が失敗して赤くなる**——D11-f の見張りを兼ねる。

    Args:
        module_name: feature モジュール名（`registration.name` と同じ）。

    Returns:
        `(<Mod>Deps, <Mod>Handle)` の対。
    """
    module = import_module(module_name)
    build = module.build
    hints = typing.get_type_hints(build, localns=LOCALNS)
    first_param = next(iter(inspect.signature(build).parameters))
    return hints[first_param], hints["return"]


def expected_lifespans(handle_cls: type) -> int:
    """`<Mod>Handle` に `lifespan` 欄が在れば 1、無ければ 0 を返す。

    `ModuleRegistration` に `lifespan` の宣言フィールドが**無い**（6 欄＝`name`・`requires`・
    `provides`・`build`・`has_router`・`has_ui_page`）ため、「後始末を持つのはどの MOD か」の正は
    **`<Mod>Handle` の宣言属性**である（B-03）。

    Args:
        handle_cls: `<Mod>Handle`。

    Returns:
        期待される `Wiring.lifespans` の件数。
    """
    return 1 if "lifespan" in {f.name for f in dataclasses.fields(handle_cls)} else 0


# --------------------------------------------------------------------------------------
# §3-1 検証単位（`WiringCase` と組み立てヘルパ）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WiringCase:
    """1 つの registry エントリを組み立てるのに要るもの（data-model §2-1）。

    不変条件: `set(overrides) == set(registration.requires)`（過不足なし）／`registration.name` が
    `config.enabled_modules` の唯一の要素／`registrations` を**持たない**（`build_modules` に
    渡さないので `_default_registrations` が実 registry を遅延 import する＝ FR-008）。
    """

    registration: ModuleRegistration
    overrides: Mapping[type[object], object]
    config: RootConfig
    call_log: CallLog


def registration_of(name: str) -> ModuleRegistration:
    """実 `MODULE_REGISTRATIONS` から `name` の 1 件を引く（**偽の登録で代用しない**）。

    Args:
        name: feature モジュール名。

    Returns:
        実 registry の当該エントリ。
    """
    for registration in MODULE_REGISTRATIONS:
        if registration.name == name:
            return registration
    pytest.fail(f"実 MODULE_REGISTRATIONS に登録が無い: {name}")


def make_case(name: str, overrides: Mapping[type[object], object], call_log: CallLog) -> WiringCase:
    """1 組の `WiringCase` を組む（`config` はルート `config.json` を読ませない）。

    `config` を `make_root_config(enabled_modules=[name])` で組むのは、app に渡すのと**同一の源**
    から引くためである（`014` HD-41 と同じ理由）。

    Args:
        name: feature モジュール名。
        overrides: 宣言 `requires` とちょうど同じ鍵を持つ差し替え写像。
        call_log: スタブ横断の呼び出し記録。

    Returns:
        組み立てに渡す `WiringCase`。
    """
    return WiringCase(
        registration=registration_of(name),
        overrides=overrides,
        config=make_root_config(enabled_modules=[name]),
        call_log=call_log,
    )


def assemble(case: WiringCase, *, overrides: Mapping[type[object], object] | None = None) -> Wiring:
    """実 registry を通してその MOD 1 件だけを組み立てる（FR-037・規約 §3-1）。

    **`registrations=` を渡さない**ので `_default_registrations()` が実 registry を遅延 import
    する。**アダプタ関数（`registration.build(deps)`）を直に呼ばない**——直呼びでは `requires` の
    絞り込み・`provides` の集合一致検査・`routers`／`lifespans` への積み上げが経路から外れ、
    FR-011・FR-018・FR-020 を実出力で確かめられなくなる。

    Args:
        case: 組の記述。
        overrides: 差し替え写像を入れ替えるとき（T3a・T3b の負の実証）に渡す。`None` なら
            `case.overrides`。

    Returns:
        組み立て結果の `Wiring`。
    """
    return build_modules(
        enabled_modules=[case.registration.name],
        overrides=case.overrides if overrides is None else overrides,
        config=case.config,
    )
