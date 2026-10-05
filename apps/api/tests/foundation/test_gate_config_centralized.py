"""ゲート設定がルート pyproject.toml に一元化されていることの検査（FR-006・憲章 §4）。

メンバ側の pyproject.toml に [tool.ruff]・[tool.pyright]・[tool.importlinter] を置くと、
手元と CI で異なる規則が適用され「手元は緑・CI は赤」が起きる。ルート以外の
ruff.toml / .ruff.toml / pyrightconfig.json も同じ理由で禁じる。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

FORBIDDEN_TABLES = ("ruff", "pyright", "importlinter")
FORBIDDEN_FILES = ("ruff.toml", ".ruff.toml", "pyrightconfig.json")
SKIP_PARTS = {".venv", "pocs", "_archive", "node_modules", "__pycache__"}


def _repo_root() -> Path:
    """[tool.uv.workspace] を持つ pyproject.toml を上方向に探す（cwd 非依存。research R-09）。"""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            data: dict[str, Any] = tomllib.loads(candidate.read_text(encoding="utf-8"))
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return parent
    raise AssertionError(
        "workspace ルート（[tool.uv.workspace] を持つ pyproject.toml）が見つからない"
    )


def _member_pyprojects(root: Path) -> list[Path]:
    found = [*root.glob("packages/**/pyproject.toml"), *root.glob("apps/*/pyproject.toml")]
    return sorted(p for p in found if not SKIP_PARTS.intersection(p.parts))


def test_member_pyprojects_have_no_gate_tables() -> None:
    root = _repo_root()
    members = _member_pyprojects(root)
    assert members, "メンバの pyproject.toml が 1 つも見つからない（走査の失敗を緑にしない）"

    offenders: list[str] = []
    for path in members:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        tool: dict[str, Any] = data.get("tool", {})
        offenders.extend(
            f"{path.relative_to(root)}: [tool.{table}]"
            for table in FORBIDDEN_TABLES
            if table in tool
        )

    assert not offenders, (
        "ゲート設定はルート pyproject.toml に一元化する（憲章 §4・FR-006）。"
        f"メンバ側の設定テーブルを削って、ルート pyproject.toml へ移すこと: {offenders}"
    )


def test_no_gate_config_files_outside_root() -> None:
    root = _repo_root()
    offenders: list[str] = []
    for name in FORBIDDEN_FILES:
        for path in root.rglob(name):
            if path.parent == root or SKIP_PARTS.intersection(path.parts):
                continue
            offenders.append(str(path.relative_to(root)))

    assert not offenders, (
        "ゲート設定ファイルはリポジトリルートにのみ置く（憲章 §4・FR-006）。"
        f"削って、ルート pyproject.toml の [tool.ruff] / [tool.pyright] へ移すこと: {offenders}"
    )
