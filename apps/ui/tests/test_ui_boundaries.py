"""US6: apps/ui が境界を破っていないことの機械的な検査。

検証行: U-2・U-3（FR-030・FR-031・FR-033・SC-011）。
feature の `service`・`repository` への import が 0 件であることは **AST 走査**で見る
（文字列 grep を使わない。`001` C-8 と同じ理由）。秘密・モジュール個別設定・自身の待受ポートの
キー名は「文字列として現れない」ことを求められているので、こちらは生のテキスト走査で見る。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

FEATURE_NAMES = (
    "board",
    "vault",
    "settings",
    "monitor",
    "stages",
    "executor",
    "orchestrator",
    "trigger",
)
"""既知 8 種のモジュール名。"""

ALLOWED_FEATURE_SUBMODULE = "ui"
"""feature から import してよい唯一のサブモジュール（D11-f）。"""

FORBIDDEN_TOKENS = (
    "LLM_API_KEY",
    "VAULT_ROOT",
    "SETTINGS_ROOT",
    "TRIGGER_ROOT",
    "BEADS_DIR",
    "DOWNLOADS_DIR",
    "UI_PORT",
)
"""U-3: `apps/ui/src` に文字列として現れてはならないキー名。"""


def _source_dir() -> pathlib.Path:
    """`apps/ui/src/ui/`。"""
    return pathlib.Path(__file__).resolve().parents[1] / "src" / "ui"


def _source_files() -> list[pathlib.Path]:
    """走査対象の Python ファイル。"""
    return sorted(_source_dir().rglob("*.py"))


def _imported_paths(tree: ast.AST) -> list[str]:
    """ファイルが import しているモジュールパスを集める。

    Args:
        tree: 解析済みの構文木。

    Returns:
        `board.service` のようなドット区切りのパス。`from board import ui` は `board.ui` に写す。
    """
    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            paths.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            paths.append(node.module)
            paths.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return paths


def _violations(paths: list[str]) -> list[str]:
    """feature への import のうち `<mod>.ui` 以外を拾う。"""
    found: list[str] = []
    for path in paths:
        parts = path.split(".")
        if parts[0] not in FEATURE_NAMES:
            continue
        if len(parts) >= 2 and parts[1] == ALLOWED_FEATURE_SUBMODULE:
            continue
        found.append(path)
    return found


def test_u2_no_import_of_feature_service_or_repository() -> None:
    """U-2・FR-031・SC-011: feature の `service`・`repository` への import が 0 件。"""
    offenders: dict[str, list[str]] = {}
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = _violations(_imported_paths(tree))
        if found:
            offenders[path.name] = found

    assert not offenders, (
        "apps/ui が feature モジュールの内部を import している"
        f"（import してよいのは `<mod>.{ALLOWED_FEATURE_SUBMODULE}` だけ。D11-f）: {offenders}"
    )


@pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
def test_u3_forbidden_config_keys_do_not_appear(token: str) -> None:
    """U-3・FR-030・FR-033: 秘密・モジュール個別設定・自身の待受ポートのキー名が現れない。"""
    hits = [path.name for path in _source_files() if token in path.read_text(encoding="utf-8")]

    assert not hits, f"apps/ui/src に {token} の文字列がある: {hits}"


def test_scan_target_is_not_empty() -> None:
    """走査の母数が空でないこと（＝「対象が無いので緑」になっていないこと）を確かめる。"""
    files = _source_files()

    assert {path.name for path in files} >= {"__init__.py", "config.py", "main.py", "pages.py"}


def test_detector_catches_a_forbidden_import(tmp_path: pathlib.Path) -> None:
    """負の検証（CHK003）: 禁じた import を置くと本検査が実際に捕まえる。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from board.service import BoardService\nimport vault.repository\nfrom trigger import ui\n",
        encoding="utf-8",
    )

    found = _violations(_imported_paths(ast.parse(sample.read_text(encoding="utf-8"))))

    assert "board.service" in found
    assert "vault.repository" in found
    assert "trigger.ui" not in found, "許可された `<mod>.ui` まで拾っている"
