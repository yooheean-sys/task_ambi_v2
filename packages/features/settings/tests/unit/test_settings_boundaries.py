"""US6: 境界の機械的な観測（AST 走査）。contracts/ui-pages.md §2・save-pipeline.md §6。

検証行: SC-016（画面のソースに service・repository への参照が 0 件）・SC-017／SC-028
（`httpx`・`requests` が 0 件）・SC-025（環境依存値の層が 0 件）・**SC-029**（雛形の値の出所が
`ui.py` 以外に 0 件）・**SC-024**（S-10。領域 A への書込が `repository.py` 以外に 0 件）・
**SC-013**（S-9。保存経路が作る `kind` が 4 語だけ）。

**文字列 grep ではなく AST 走査**で見る（`001` C-8 と同じ理由。コメントや docstring に語が
現れても偽陽性にしない）。キー名の「文字列として現れない」だけは生のテキスト走査で見る。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "settings"
"""`packages/features/settings/src/settings/`。"""

UI = SRC / "ui.py"
REPOSITORY = SRC / "repository.py"

FORBIDDEN_UI_ROOTS = ("settings",)
"""SC-016・FR-087: 画面は**自モジュールを 1 つも import しない**（HTTP のみ）。"""

FORBIDDEN_UI_PACKAGES = ("httpx", "requests", "pydantic_settings", "dotenv")
"""SC-017・SC-028・SC-025: 画面が触れてはならない外部パッケージ。"""

FORBIDDEN_UI_TOKENS = (
    "SETTINGS_ROOT",
    "APP_BASE_URL",
    "APP_HOST",
    "APP_PORT",
    "LLM_API_KEY",
    "os.environ",
    "getenv",
)
"""SC-025・FR-089: 画面のソースに**文字列としても**現れてはならないキー名と読み口。"""

WRITE_NAMES = ("write_text", "rename", "mkdir", "rmdir", "unlink", "fdopen", "mkstemp")
"""S-10・SC-024: 領域 A へ書く呼び出し名（`repository.py` 以外に 0 件）。

**`replace` を名前で見ない**のは、`dataclasses.replace`（候補づくりが使う純関数）と
`str.replace` が同名で、名前だけでは区別できないためである。`os.replace` の側は
`WRITE_MODULES` の import 禁止で塞ぐ（`os` を import できなければ `os.replace` は書けない）。
"""

WRITE_MODULES = ("os", "shutil", "tempfile")
"""同上（**モジュールごと**禁じる）。`repository.py` 以外がこれらを import しないことで、
`os.replace`・`shutil.*`・`tempfile.mkstemp` の経路がまとめて塞がる。"""

SAVE_PATH_FILES = ("save.py", "projects.py", "members.py", "ai_config.py")
"""S-9・SC-013: 保存経路が `ValidationProblem` を作る 4 ファイル。"""

SAVE_PATH_KINDS = frozenset({"key_rule", "folder_missing", "llm_ref", "orphan", "format"})
"""保存時だけの規則（`key_rule`・`folder_missing`・既定プロファイルの削除＝ `llm_ref`・
repos の形式＝ `format`（D24））と、候補 policy の読めなさの翻訳（`orphan`）。
**これ以外が現れたら起動時の 28 規則を自前で書き直した証拠**である（S-9 の判定基準。
裁定 2026-08-27。`format` は repos の形式検査のため D24 で追加）。"""

PRESET_VALUES = ("openrouter.ai", "127.0.0.1:8080")
"""SC-029: 新規プロファイルの雛形の値。**出所は `ui.py` だけ**（正本は画面側＝ FR-094）。"""


def _tree(path: Path) -> ast.AST:
    """ファイルを構文木にする。"""
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported(tree: ast.AST) -> list[str]:
    """import しているモジュールパスを集める（`from a import b` は `a.b` にも写す）。"""
    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            paths.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            paths.append(node.module)
            paths.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return paths


def _source_files() -> list[Path]:
    """`src/settings/` 配下の Python ファイル（走査の母数）。"""
    return sorted(SRC.rglob("*.py"))


# -- P-6・SC-016: 画面が自モジュールを 1 つも import しない -----------------------------


def test_sc016_the_screen_never_imports_its_own_module() -> None:
    """SC-016・FR-087・US6 AC-6: `settings.*` への参照が **0 件**。

    これにより `ui-pages` 契約が `allow_indirect_imports` **なしで**成立する（010 SD-08）。
    """
    found = [path for path in _imported(_tree(UI)) if path.split(".")[0] in FORBIDDEN_UI_ROOTS]

    assert not found, f"画面が自モジュールを import している: {found}"


def test_sc016_the_screen_references_no_settings_attribute() -> None:
    """SC-016: `settings.` で始まる属性参照も 0 件（import 以外の経路も塞ぐ）。"""
    names = {
        node.value.id
        for node in ast.walk(_tree(UI))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }

    assert not names & set(FORBIDDEN_UI_ROOTS)


# -- P-7・SC-017・SC-028: 画面が禁じられた外部パッケージを使わない ----------------------


@pytest.mark.parametrize("package", FORBIDDEN_UI_PACKAGES)
def test_sc017_sc028_the_screen_uses_no_forbidden_package(package: str) -> None:
    """SC-017・SC-028・FR-088: `httpx`・`requests`・環境依存値の層への参照が 0 件。"""
    found = [path for path in _imported(_tree(UI)) if path.split(".")[0] == package]

    assert not found, f"画面が {package} を import している: {found}"


def test_the_screen_only_imports_the_standard_library_and_nicegui() -> None:
    """契約 §2: import してよいのは `nicegui` と標準ライブラリだけ（許可の側から見る）。

    `016` で画面を Streamlit から NiceGUI へ置き換えたため、許可集合の `streamlit` を
    `nicegui` に替え、`fetch` の `asyncio.to_thread`（R-08）のために `asyncio` を足した。
    """
    allowed = {
        "nicegui",
        "asyncio",
        "json",
        "urllib",
        "dataclasses",
        "typing",
        "collections",
        "__future__",
    }
    roots = {path.split(".")[0] for path in _imported(_tree(UI))}

    assert roots <= allowed, f"許可の外を import している: {sorted(roots - allowed)}"


# -- P-9・SC-025: 環境依存値のキー名が文字列としても現れない ----------------------------


@pytest.mark.parametrize("token", FORBIDDEN_UI_TOKENS)
def test_sc025_no_environment_key_appears_in_the_screen(token: str) -> None:
    """SC-025・FR-089: 画面は環境依存値の層を読まない（base URL は引数で受ける）。"""
    assert token not in UI.read_text(encoding="utf-8")


# -- SC-029: 雛形の値の出所が画面のソース以外に 0 件 -------------------------------------


@pytest.mark.parametrize("value", PRESET_VALUES)
def test_sc029_the_preset_values_live_only_in_the_screen(value: str) -> None:
    """SC-029・FR-094・010 SD-19: 雛形の**正本は画面側**（`settings/config.json` を読まない）。"""
    holders = [path.name for path in _source_files() if value in path.read_text(encoding="utf-8")]

    assert holders == ["ui.py"], f"雛形の値が画面以外にもある: {holders}"


def test_sc029_the_screen_never_reads_the_module_config() -> None:
    """SC-029: 画面が `settings/config.json` の `llm_presets`（CFG-38）を読む経路が無い。"""
    source = UI.read_text(encoding="utf-8")

    assert "llm_presets" not in source
    assert "config.json" not in source


# -- S-10・SC-024: 領域 A への書込が `repository.py` 以外に 0 件 ------------------------


def test_s10_area_a_is_written_from_repository_only() -> None:
    """S-10・SC-024・FR-026: 実行時に領域 A を書く経路が **`AreaAWriter` の 1 箇所**に閉じる。

    `src/settings/` **全体**の AST を走査し、書込に使いうる呼び出しが `repository.py` 以外に
    現れないことを確かめる（`open(..., "w")`・`write_text`・`os.replace`・`shutil`・
    `Path.rename`・`mkdir`）。
    """
    offenders: dict[str, list[str]] = {}
    for path in _source_files():
        if path.name == REPOSITORY.name:
            continue
        tree = _tree(path)
        found = _write_calls(tree)
        found.extend(name for name in _imported(tree) if name.split(".")[0] in WRITE_MODULES)
        if found:
            offenders[path.name] = found

    assert not offenders, f"領域 A への書込が repository.py の外にある: {offenders}"


def _write_calls(tree: ast.AST) -> list[str]:
    """書込に使いうる呼び出しを拾う（`open(..., "w")` を含む）。"""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in WRITE_NAMES:
            found.append(func.attr)
        elif isinstance(func, ast.Name) and func.id in {*WRITE_NAMES, "open"}:
            if func.id == "open" and not _opens_for_write(node):
                continue
            found.append(func.id)
    return found


def _opens_for_write(node: ast.Call) -> bool:
    """`open(...)` の第 2 引数（または `mode=`）が書込みモードか。"""
    modes = [
        argument.value
        for argument in node.args[1:2]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]
    modes.extend(
        keyword.value.value
        for keyword in node.keywords
        if keyword.arg == "mode"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    )
    return any(set(mode) & {"w", "a", "x", "+"} for mode in modes)


def test_the_write_detector_actually_catches_something(tmp_path: Path) -> None:
    """負の検証: 走査が実際に書込を捕まえる（「対象が無いので緑」になっていないこと）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'p = None\np.write_text("x")\np.rename("y")\nopen("f", "w")\nopen("f")\n',
        encoding="utf-8",
    )
    tree = ast.parse(sample.read_text(encoding="utf-8"))

    assert _write_calls(tree) == ["write_text", "rename", "open"], "読取の open まで拾っている"


def test_the_scan_target_is_not_empty() -> None:
    """走査の母数が空でないこと（＝検査が空振りしていないこと）。"""
    names = {path.name for path in _source_files()}

    assert {"ui.py", "repository.py", "crud.py", "save.py", "projects.py"} <= names


# -- S-9・SC-013: 保存経路が作る `kind` が 5 語だけ --------------------------------------


def test_s9_the_save_path_invents_no_new_rule() -> None:
    """S-9・SC-013・R-01: 同じ規則を 2 箇所に書かないことの**代理指標**。

    `service/{save,projects,members,ai_config}.py` が生成する `ValidationProblem` の `kind` が
    **`key_rule`・`folder_missing`・`llm_ref`・`orphan`・`format` の 5 語だけ**であること。
    前 3 語は**保存時だけの 3 規則**、`orphan` は**候補 policy の読めなさの翻訳**、
    `format` は **repos の形式検査**（D24）であり、これ以外の
    `kind`（`forward`・`unknown_field`・`agent_ref` ほか 9 語）が現れたら**起動時の 28 規則を
    自前で書き直した証拠**になる。
    """
    found: set[str] = set()
    for name in SAVE_PATH_FILES:
        path = SRC / "service" / name
        found |= _problem_kinds(_tree(path))

    assert found <= SAVE_PATH_KINDS, f"保存経路が新しい規則を作っている: {sorted(found)}"
    assert found == SAVE_PATH_KINDS, f"想定した 4 語が揃っていない: {sorted(found)}"


def _problem_kinds(tree: ast.AST) -> set[str]:
    """`ValidationProblem(...)` の `kind=` に渡された文字列リテラルを集める。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "ValidationProblem":
            continue
        found |= {
            keyword.value.value
            for keyword in node.keywords
            if keyword.arg == "kind"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        }
    return found


def test_the_kind_detector_actually_catches_something(tmp_path: Path) -> None:
    """負の検証: 走査が実際に `kind` を拾う（空振りしていないこと）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'ValidationProblem(where="w", kind="forward", reason="r")\n', encoding="utf-8"
    )

    assert _problem_kinds(ast.parse(sample.read_text(encoding="utf-8"))) == {"forward"}
