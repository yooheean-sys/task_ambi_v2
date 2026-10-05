"""repo 提供の IF（IF-51〜55）の適合検証。D25。

一時の作業ツリーの**実物**に対して、`RepoReader`・`RepoWriter` の 5 メソッドが台帳どおりの型を
返し台帳どおりの例外を投げることを確かめる。台帳の出力欄の注記が振る舞いを規定している行を
落とさない —— IF-52「不在は例外で明示。黙って空を返さない」・IF-53「ファイルの不在は偽」・
IF-54「既存を上書きしたら真」・IF-55「ディレクトリは消さない」。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import REPO_SOURCE_BODY, REPO_SOURCE_PATH

from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.interfaces.settings import ProjectRepos

if TYPE_CHECKING:
    from common.interfaces.repo import RepoReader, RepoWriter

UNCLONED = ProjectRepos(remote_url=None, local_path=None)
"""リポジトリ未登録の対応付け（`RepoNotFound` を踏む）。"""


def _root(repos: ProjectRepos) -> Path:
    assert repos.local_path is not None
    return Path(repos.local_path)


@pytest.mark.contract_if("IF-51")
def test_contract_repo_list_files_returns_repo_relative_paths(
    repo_reader: RepoReader, repo_store: ProjectRepos
) -> None:
    """IF-51: リポジトリ相対の POSIX パスを返す。`.git` と拒否パターン（`.env`）は載せない。"""
    names = repo_reader.list_files(repo_store, "")

    assert isinstance(names, list)
    assert names == [REPO_SOURCE_PATH]

    with pytest.raises(RepoNotFound):
        repo_reader.list_files(UNCLONED, "")


@pytest.mark.contract_if("IF-52")
def test_contract_repo_read_file_returns_text_and_raises_on_absence(
    repo_reader: RepoReader, repo_store: ProjectRepos
) -> None:
    """IF-52: 本文を返す。不在は `RepoNotFound`・外は `RepoPathViolation`（黙って空にしない）。"""
    assert repo_reader.read_file(repo_store, REPO_SOURCE_PATH) == REPO_SOURCE_BODY

    with pytest.raises(RepoNotFound):
        repo_reader.read_file(repo_store, "src/absent.py")
    with pytest.raises(RepoPathViolation) as caught:
        repo_reader.read_file(repo_store, "../outside.txt")
    assert caught.value.kind == "invalid"


@pytest.mark.contract_if("IF-53")
def test_contract_repo_exists_is_false_for_absence_and_raises_when_not_cloned(
    repo_reader: RepoReader, repo_store: ProjectRepos
) -> None:
    """IF-53（orchestrator 観点・完了ゲート）: ファイルの不在は偽、未登録・未 clone は例外。"""
    assert repo_reader.exists(repo_store, REPO_SOURCE_PATH) is True
    assert repo_reader.exists(repo_store, "src/absent.py") is False

    with pytest.raises(RepoNotFound):
        repo_reader.exists(UNCLONED, REPO_SOURCE_PATH)
    with pytest.raises(RepoPathViolation) as caught:
        repo_reader.exists(repo_store, ".git/config")
    assert caught.value.kind == "git_dir"


@pytest.mark.contract_if("IF-54")
def test_contract_repo_write_file_reports_overwrite(
    repo_writer: RepoWriter, repo_store: ProjectRepos
) -> None:
    """IF-54: 新規は偽・既存の上書きは真を返し、実ファイルが書き換わる。秘密の名前は拒む。"""
    assert repo_writer.write_file(repo_store, "src/new.py", "x = 1\n") is False
    assert repo_writer.write_file(repo_store, "src/new.py", "x = 2\n") is True
    assert (_root(repo_store) / "src/new.py").read_text(encoding="utf-8") == "x = 2\n"

    with pytest.raises(RepoPathViolation) as caught:
        repo_writer.write_file(repo_store, ".env", "SECRET=2\n")
    assert caught.value.kind == "denied"


@pytest.mark.contract_if("IF-55")
def test_contract_repo_delete_file_removes_only_files(
    repo_writer: RepoWriter, repo_store: ProjectRepos
) -> None:
    """IF-55: ファイルを 1 つ消す。不在は `RepoNotFound`・ディレクトリは消さない。"""
    result = repo_writer.delete_file(repo_store, REPO_SOURCE_PATH)

    assert result is None
    assert not (_root(repo_store) / REPO_SOURCE_PATH).exists()
    with pytest.raises(RepoNotFound):
        repo_writer.delete_file(repo_store, REPO_SOURCE_PATH)
    with pytest.raises(RepoPathViolation):
        repo_writer.delete_file(repo_store, "src")
    assert (_root(repo_store) / "src").is_dir()
