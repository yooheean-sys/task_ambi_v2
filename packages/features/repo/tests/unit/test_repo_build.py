"""`repo.build()` と `RepoConfig`（起動時の検査）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

import repo
from common.interfaces.settings import ProjectRepos
from repo import RepoConfig, RepoDeps, build
from repo.exceptions import RepoConfigError

if TYPE_CHECKING:
    from pathlib import Path


def test_reader_and_writer_are_the_same_instance() -> None:
    handle = build(RepoDeps())
    assert handle.repo_reader is handle.repo_writer
    assert handle.router is None


def test_shipped_config_matches_the_code_defaults() -> None:
    """`config.json` とクラス既定が食い違わない（片方だけ直す事故を拾う）。"""
    shipped = json.loads(repo.config_path().read_text(encoding="utf-8"))
    defaults = {name: field.default for name, field in RepoConfig.model_fields.items()}
    assert {key: defaults[key] for key in shipped} == shipped


def test_git_dir_is_always_pruned_even_if_not_configured(repos: ProjectRepos) -> None:
    """`.git` を `excluded_dirs` から外しても一覧に出ない（設定で穴を開けられない）。"""
    handle = build(RepoDeps(), config=RepoConfig(encoding="utf-8", excluded_dirs=[]))
    listed = handle.repo_reader.list_files(repos, "")
    assert "node_modules/dep/index.js" in listed
    assert not any(name.startswith(".git/") for name in listed)


def test_unknown_config_key_is_a_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "config.json"
    broken.write_text(json.dumps({"max_read_bytes": 1, "unknown_key": 1}), encoding="utf-8")
    monkeypatch.setattr("repo.config_path", lambda: broken)
    with pytest.raises(RepoConfigError, match="repo の設定を読めない"):
        build(RepoDeps())
