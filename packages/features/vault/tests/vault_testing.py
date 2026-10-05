"""vault のテストの共有部品（木の組み立て・symlink の張り方・`rel` 一覧の採取）。

`conftest.py` と別名にする理由: `tests/unit/` と `tests/integration/` の双方に `conftest.py`
があると、いずれも top-level モジュール名 `conftest` として読み込まれ `from conftest import …`
が先に読まれた方に解決してしまう（board が実測済み）。一意な名前のモジュールに置いて曖昧さを
消す。
"""

from __future__ import annotations

import logging
from pathlib import Path

from vault import VaultConfig
from vault.repository import FsAdapter
from vault.service import VaultService

DEFAULT_ENCODING = "utf-8"
"""テストが使う実効文字コード（ルート `config.json` の `defaults.encoding` と同じ）。"""


def make_adapter(vault_root: Path, config: VaultConfig | None = None) -> FsAdapter:
    """一時 vault に対する `FsAdapter` を組み立てる。"""
    return FsAdapter(
        vault_root=vault_root,
        config=config if config is not None else VaultConfig(),
        encoding=DEFAULT_ENCODING,
    )


def make_service(vault_root: Path, config: VaultConfig | None = None) -> VaultService:
    """一時 vault に対する `VaultService` を組み立てる（`build()` を通さない単体用）。"""
    settings = config if config is not None else VaultConfig()
    return VaultService(
        make_adapter(vault_root, settings), config=settings, encoding=DEFAULT_ENCODING
    )


def write_file(root: Path, rel: str, body: str = "x") -> Path:
    """`root` からの相対パスにファイルを作る（親フォルダは既に在る前提にしない）。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding=DEFAULT_ENCODING)
    return path


def link(root: Path, rel: str, target: Path) -> Path:
    """`root/rel` に `target` を指す symlink を張る。

    **リンク先は `tmp_path` の内側に作る**（pytest の掃除は symlink を辿らないため、外側に
    実体を作ると残る）。
    """
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    return path


def rels_under(root: Path) -> set[str]:
    """`root` 配下の全エントリの相対パス（掃除・不変の確認に使う）。"""
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def dirs_under(root: Path) -> set[str]:
    """`root` 配下のディレクトリだけの相対パス（「フォルダを作らない」の確認＝ SC-004）。"""
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir()}


def log_extra(record: logging.LogRecord, key: str) -> object:
    """`extra` で足した項目を取り出す（`LogRecord` の属性は静的に見えないため `__dict__` から）。"""
    return record.__dict__[key]
