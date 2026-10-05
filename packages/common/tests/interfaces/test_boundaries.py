"""SC-009: common から feature／apps への import 0 件・httpx を実行時に読まない。

import の検出は **AST** で行う（文字列 grep はコメントや docstring 内の記述を
誤検出し、`importlib` 経由の動的 import を見逃す）。検査対象には
`packages/common/tests/` と `__init__.py` を含む（憲章 §7-5）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

FORBIDDEN_ROOTS = frozenset(
    {
        "board",
        "vault",
        "settings",
        "monitor",
        "stages",
        "executor",
        "orchestrator",
        "trigger",
        "repo",
        "api",
        "ui",
    }
)

FORBIDDEN_FILENAMES = (
    "router.py",
    "ui.py",
    "service.py",
    "repository.py",
    "schemas.py",
    "models.py",
    "dependencies.py",
    "exceptions.py",
)


def _python_files(repo_root: Path) -> list[Path]:
    roots = (repo_root / "packages/common/src", repo_root / "packages/common/tests")
    return sorted(path for root in roots for path in root.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    """そのファイルが import している最上位パッケージ名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_scanned_file_set_is_not_empty(repo_root: Path) -> None:
    files = _python_files(repo_root)
    assert len(files) > 0
    assert any(path.name == "__init__.py" for path in files)


def test_no_import_of_feature_or_app_packages(repo_root: Path) -> None:
    offenders = [
        f"{path.relative_to(repo_root)}: {sorted(_imported_roots(path) & FORBIDDEN_ROOTS)}"
        for path in _python_files(repo_root)
        if _imported_roots(path) & FORBIDDEN_ROOTS
    ]
    assert offenders == []


def test_httpx_is_imported_only_in_the_monitor_contract(repo_root: Path) -> None:
    """静的な出現箇所（FR-036）。実行時ロード（下のテスト）とは別の受け入れ条件。"""
    src = repo_root / "packages/common/src"
    importers = sorted(
        str(path.relative_to(src)) for path in src.rglob("*.py") if "httpx" in _imported_roots(path)
    )
    assert importers == ["common/interfaces/monitor.py"]


def test_httpx_in_log_is_a_logger_name_not_an_import(repo_root: Path) -> None:
    """`log.py` の `httpx` は FR-025 が要求する**ロガ名の文字列**であって import ではない。

    `LoggerFactory` は `crewai`／`litellm`／`httpx` のロガ水準を固定するが、
    これらのライブラリを import しない（contracts/kernel.md §3-3 約束 4）。
    """
    log_py = repo_root / "packages/common/src/common/kernel/log.py"
    assert "httpx" in log_py.read_text(encoding="utf-8")
    assert "httpx" not in _imported_roots(log_py)


def test_httpx_is_not_loaded_at_runtime() -> None:
    """子プロセスで確かめる（同一プロセスの `sys.modules` は先行 import に汚される）。"""
    code = (
        "import sys; import common.interfaces.monitor, common.interfaces.executor; "
        "print('httpx' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("filename", FORBIDDEN_FILENAMES)
def test_feature_module_files_are_absent(repo_root: Path, filename: str) -> None:
    """common は feature モジュールではない（FR-001）。"""
    found = sorted(str(p) for p in (repo_root / "packages/common/src/common").rglob(filename))
    assert found == []
