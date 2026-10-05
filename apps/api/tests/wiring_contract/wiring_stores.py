"""一時外部ストアの組み立て（規約 §5。`conftest.py` から呼ばれる純関数）。

plan (vii)-3 の分割規則（`conftest.py` が 300 行を超えたら**一時ストアの組み立てだけ**を割る）に
従って切り出した。**同一ディレクトリの補助**（`test_` で始まらない `.py`）なので、単独実行でも
import でき、実行順序依存が生じない（規約 §2）。

⚠ `apps/api/tests/contract/contract_stores.py` を **import せず最小形で同型移植**している。あちらは
pytest が `contract/` を収集したときにだけ `sys.path` に載るため、import すると**実行順序依存**に
なる（FR-033・spec §0-5 (4) の実測）。

`013` との差: `bd init` を打つ `init_beads` を移植しない／vault の 3 領域・ルート外ファイルを
作らない／settings の yaml 7 系統を作らない（中身は `build` の `_scaffold` が書く）／trigger の
領域 B は `013` に対応物が無い（本 feature が初）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from pathlib import Path

BEADS_CONFIG_FILENAME = "config.yaml"
"""board の起動時検査 (c) が見る目印（`BEADS_DIR` **直下**にある必要がある）。"""


def assert_outside_repo(path: Path) -> Path:
    """一時ディレクトリがリポジトリの**外**にあることを確かめる（FR-028・規約 §5）。

    Args:
        path: 確かめる一時ディレクトリ。

    Returns:
        解決済みの絶対パス。
    """
    resolved = path.resolve()
    repo_root = find_repo_root().resolve()
    if resolved.is_relative_to(repo_root):
        pytest.fail(
            "一時ストアがリポジトリの中に作られようとしている（FR-028）\n"
            f"  一時ディレクトリ: {resolved}\n"
            f"  リポジトリルート: {repo_root}"
        )
    return resolved


def make_light_beads(tmp_path: Path) -> Path:
    """`bd init` を**打たない**軽量 beads を作る（FR-013・規約 §5）。

    board の `build` は **`bd` を 1 度も実行しない**——起動時検査は (a) `BEADS_DIR` → (b) 実在 →
    (c) 直下の `config.yaml` → (d) `BD_BIN` を `shutil.which` ＋ `os.access(X_OK)` → (e)
    `config.json` の 5 段で、サブコマンドを打たない（`board/__init__.py` の `build` docstring が
    FR-048・R-15 として明言）。したがって `bd init`（embedded Dolt の初期化と git commit）は
    要らず、`.beads/config.yaml` を置くだけで (b)(c) を通せる。**サブディレクトリでの `git init`・
    `git commit` を行わない**（FR-031）。

    Args:
        tmp_path: pytest の一時ディレクトリ。

    Returns:
        `BEADS_DIR` に渡す値（`.beads` **自身**であって、それを持つ親ではない＝ D19-a・D19-b）。
    """
    assert_outside_repo(tmp_path)
    beads_dir = tmp_path / "light" / ".beads"
    beads_dir.mkdir(parents=True)
    (beads_dir / BEADS_CONFIG_FILENAME).write_text("", encoding="utf-8")
    return beads_dir


def make_vault_root(tmp_path: Path) -> Path:
    """保管庫ルートの空ディレクトリを作る（vault の `build` は実在確認しかしない）。

    Args:
        tmp_path: pytest の一時ディレクトリ。

    Returns:
        `VAULT_ROOT` に渡す値。
    """
    assert_outside_repo(tmp_path)
    root = tmp_path / "vault"
    root.mkdir()
    return root


def make_area_a(tmp_path: Path) -> Path:
    """領域 A の空ディレクトリを作る（**中身は settings の `build` の `_scaffold` が書く**）。

    Args:
        tmp_path: pytest の一時ディレクトリ。

    Returns:
        `SETTINGS_ROOT` に渡す値。
    """
    assert_outside_repo(tmp_path)
    root = tmp_path / "area_a"
    root.mkdir()
    return root


def make_area_b(tmp_path: Path) -> Path:
    """領域 B の空ディレクトリを作る（**中身は trigger の `build` の `ensure_template` が書く**）。

    Args:
        tmp_path: pytest の一時ディレクトリ。

    Returns:
        `TRIGGER_ROOT` に渡す値。
    """
    assert_outside_repo(tmp_path)
    root = tmp_path / "area_b"
    root.mkdir()
    return root
