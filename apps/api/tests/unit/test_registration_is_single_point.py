"""US2: モジュール名を知っているのは `registry.py` だけである（SC-005・quickstart S7）。

組み立ての手順を担うコード（`wiring.py`・`app.py`・`main.py` ほか）にモジュール名の文字列が
1 つも現れないことを **AST 走査**で確かめる。文字列 grep を使わないのは、docstring・コメントの
誤検出を避けるため（`001` C-8 と同じ理由）。ここが緑である限り、モジュールを 1 つ足すときに
apps/api 側で編集するのは `registry.py` の 1 箇所だけで済む（FR-007）。
"""

from __future__ import annotations

import ast
import pathlib
import re
import tomllib
from typing import Any

import pytest

MODULE_NAMES = (
    "board",
    "vault",
    "settings",
    "monitor",
    "stages",
    "executor",
    "orchestrator",
    "trigger",
)
"""既知 8 種のモジュール名（正本は `docs/design/phase4/p4_importlinter.toml`）。"""

REGISTRATION_POINT = "registry.py"
"""唯一モジュール名を書いてよいファイル。"""


def _repo_root() -> pathlib.Path:
    """`[tool.uv.workspace]` を持つ `pyproject.toml` を上方向に探す（cwd 非依存）。"""
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            data: dict[str, Any] = tomllib.loads(candidate.read_text(encoding="utf-8"))
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return parent
    raise AssertionError("workspace ルートが見つからない")


def _source_files() -> list[pathlib.Path]:
    """`registry.py` を除く `apps/api/src/api/` の Python ファイル。"""
    source_dir = _repo_root() / "apps" / "api" / "src" / "api"
    return sorted(
        path
        for path in source_dir.glob("*.py")
        if path.name != REGISTRATION_POINT and path.name != "__init__.py"
    )


def _documentation_ids(tree: ast.AST) -> set[int]:
    """裸の文字列文（module／class／function の docstring と属性 docstring）の識別子集合。"""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _collect(path: pathlib.Path) -> tuple[list[str], list[str]]:
    """ファイルから「識別子」と「docstring でない文字列定数」を集める。

    Args:
        path: 走査対象の Python ファイル。

    Returns:
        (識別子の一覧, 文字列定数の一覧)。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    documentation = _documentation_ids(tree)

    identifiers: list[str] = []
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.append(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.append(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.append(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            identifiers.append(node.name)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            identifiers.append(node.arg)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in documentation
        ):
            literals.append(node.value)
    return identifiers, literals


@pytest.mark.parametrize("path", _source_files(), ids=lambda path: path.name)
def test_module_names_appear_only_in_the_registration_point(path: pathlib.Path) -> None:
    """SC-005: 組み立ての手順を担うコードにモジュール名が現れない。"""
    identifiers, literals = _collect(path)

    hits: list[str] = []
    for name in MODULE_NAMES:
        hits.extend(f"識別子 {found!r}" for found in identifiers if found == name)
        pattern = re.compile(rf"\b{name}\b")
        hits.extend(
            f"文字列 {literal!r}" for literal in literals if pattern.search(literal) is not None
        )

    assert not hits, (
        f"{path.name} にモジュール名が現れている（登録は {REGISTRATION_POINT} の 1 箇所に"
        f" 閉じること。SC-005）: {hits}"
    )


def test_the_registration_point_exists_and_is_the_only_exception() -> None:
    """走査の母数が空でないこと（＝「対象が無いので緑」になっていないこと）を確かめる。"""
    source_dir = _repo_root() / "apps" / "api" / "src" / "api"

    assert (source_dir / REGISTRATION_POINT).is_file()
    assert len(_source_files()) >= 5, "走査対象が少なすぎる（構成が変わった可能性）"


def test_detector_would_catch_a_module_name(tmp_path: pathlib.Path) -> None:
    """負の検証: モジュール名を書いたファイルを本検査が実際に捕まえる（CHK003）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""board という語は docstring にはあってよい。"""\n\nORDER = ["board", "vault"]\n',
        encoding="utf-8",
    )

    identifiers, literals = _collect(sample)

    assert "ORDER" in identifiers, "識別子を拾えていない"
    assert "board" in literals, "docstring 以外の文字列定数を拾えていない"
    assert "vault" in literals
    assert not any("board という語" in literal for literal in literals), (
        "docstring を誤って拾っている（文字列 grep と同じ誤検出になる）"
    )
