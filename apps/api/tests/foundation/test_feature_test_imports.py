"""テストコードの feature 間 import を止める静的走査（FR-010・憲章 §1・US2 AC5）。

import-linter は src レイアウトの tests/ を grimp のモジュールグラフに載せないため、
`packages/features/<mod>/tests/` に書いた他 feature の import は緑のまま通る（research R-03）。
本検査がその穴を埋める。

検出対象は「実在 feature 名 ∪ 既知 9 種」。既知 9 種は `docs/design/phase4/p4_importlinter.toml`
の `root_packages` から導出し、テスト内にハードコードしない（実在集合だけにすると feature が
0 件の初期状態で無条件に緑になるため和を取る）。

本検査は対象ファイルを**読むだけ**で feature／common を import しないため、`apps` 層に閉じており
layers 契約に抵触しない。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

NON_FEATURE_ROOTS = {"api", "ui", "common"}


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


def _known_feature_names(root: Path) -> set[str]:
    """既知 9 種を設計文書から導出する（正本は p4_importlinter.toml の root_packages）。"""
    design = root / "docs" / "design" / "phase4" / "p4_importlinter.toml"
    assert design.is_file(), f"既知 9 種の正本が見つからない: {design}"
    data: dict[str, Any] = tomllib.loads(design.read_text(encoding="utf-8"))
    raw: Any = data["tool"]["importlinter"]["root_packages"]
    return {str(name) for name in raw} - NON_FEATURE_ROOTS


def _existing_feature_names(root: Path) -> set[str]:
    """実在 feature（packages/features/*/src/<name>/）の import 名。"""
    return {
        src.name
        for src in (root / "packages" / "features").glob("*/src/*")
        if src.is_dir() and src.name != "__pycache__"
    }


def _scan_targets(root: Path) -> list[tuple[Path, set[str]]]:
    """走査対象の (.py ファイル, そのファイルが属する feature 名の集合) を返す。

    `packages/common/tests/` は所有 feature を持たないため空集合（全 feature が禁止対象）。
    """
    targets: list[tuple[Path, set[str]]] = []
    for feature_dir in sorted((root / "packages" / "features").glob("*")):
        if not feature_dir.is_dir():
            continue
        owners = {p.name for p in (feature_dir / "src").glob("*") if p.is_dir()}
        targets.extend((py, owners) for py in sorted((feature_dir / "tests").rglob("*.py")))
    targets.extend(
        (py, set()) for py in sorted((root / "packages" / "common" / "tests").rglob("*.py"))
    )
    return [(py, owners) for py, owners in targets if "__pycache__" not in py.parts]


def _imported_top_levels(path: Path) -> list[tuple[str, int]]:
    """ファイル内の絶対 import のトップレベル名と行番号。

    相対 import（level > 0）は自モジュール内を指すので除く。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # 構文エラーを緑にしない
        raise AssertionError(f"{path}: 構文解析に失敗した: {exc}") from exc

    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.append((node.module.split(".")[0], node.lineno))
    return found


def test_detection_set_is_not_empty() -> None:
    """検出対象が空だと走査が無条件に緑になる（設計文書を読めていないことの検出）。"""
    root = _repo_root()
    known = _known_feature_names(root)
    assert len(known) == 9, (  # 9 種目は D25 の repo
        "既知 9 種を p4_importlinter.toml の root_packages から導出できない。"
        f"導出結果: {sorted(known)}"
    )


def test_test_code_does_not_import_other_features() -> None:
    root = _repo_root()
    detect = _known_feature_names(root) | _existing_feature_names(root)

    violations: list[str] = []
    for path, owners in _scan_targets(root):
        forbidden = detect - owners
        violations.extend(
            f"{path.relative_to(root)}:{lineno}: import {module}"
            for module, lineno in _imported_top_levels(path)
            if module in forbidden
        )

    assert not violations, (
        "テストコードから他 feature を import している（憲章 §1「テストコードも同様」・FR-010）。"
        " 依存する Protocol のスタブ実装を注入する形に直すこと。"
        f" 違反: {violations}"
    )
