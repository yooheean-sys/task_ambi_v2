"""`packages/common/tests` 共通の fixture。

設計文書を読むテストが `docs/design/` の絶対パスを得るための入口を 1 本だけ置く。
テストは cwd に依存してはならないため、リポジトリルートは `__file__` から遡って求める。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_WORKSPACE_MARKER = "[tool.uv.workspace]"


def _find_repo_root() -> Path:
    """`[tool.uv.workspace]` を持つ `pyproject.toml` を上方向に探す。"""
    for candidate in Path(__file__).resolve().parents:
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and _WORKSPACE_MARKER in pyproject.read_text(encoding="utf-8"):
            return candidate
    msg = f"workspace root not found above {Path(__file__).resolve()}"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """リポジトリルートの絶対パス。"""
    return _find_repo_root()


@pytest.fixture(scope="session")
def design_root(repo_root: Path) -> Path:
    """`docs/design` の絶対パス。"""
    return repo_root / "docs" / "design"
