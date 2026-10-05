"""repo のテスト共通 fixture。

作業ツリーは**一時ディレクトリ**に作る（`.git` は空ディレクトリで足りる。repo は git コマンドを
呼ばない）。シンボリックリンクのリンク先も `tmp_path` の内側に作る（pytest の掃除は symlink を
辿らないため、外側に実体を作ると残る）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from common.interfaces.settings import ProjectRepos
from repo import RepoConfig, RepoDeps, build
from repo.service import RepoService

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def work_tree(tmp_path: Path) -> Path:
    """一時の作業ツリー。`.git/`・ソース・除外ディレクトリ・秘密ファイルと、外側の 1 ファイル。"""
    root = tmp_path / "work"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "mod.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "README.md").write_text("# readme\n", encoding="utf-8")
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "node_modules" / "dep" / "index.js").write_text("x\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE\n", encoding="utf-8")
    return root


@pytest.fixture
def repos(work_tree: Path) -> ProjectRepos:
    """作業ツリーを指す対応付け。"""
    return ProjectRepos(remote_url=None, local_path=str(work_tree))


@pytest.fixture
def service() -> RepoService:
    """既定設定で組み立てたサービス（実体は `RepoService`）。"""
    return _service(RepoConfig(encoding="utf-8"))


@pytest.fixture
def small_service() -> RepoService:
    """上限を小さくしたサービス（上限超過の検査用）。"""
    return _service(
        RepoConfig(encoding="utf-8", max_read_bytes=8, max_write_bytes=8, max_list_entries=2)
    )


def _service(config: RepoConfig) -> RepoService:
    handle = build(RepoDeps(), config=config)
    assert isinstance(handle.repo_reader, RepoService)
    return handle.repo_reader
