"""`RepoService`（`RepoReader`・`RepoWriter`）の振る舞い（D25-a）。

閉じ込め（`..`・絶対パス・シンボリックリンク経由の脱出・`.git`）と拒否パターンが、読取・書込・
削除・存在確認・一覧の**すべての口**で効くことを確かめる。
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.interfaces.settings import ProjectRepos

if TYPE_CHECKING:
    from pathlib import Path

    from repo.service import RepoService


# --- リポジトリの解決 ---------------------------------------------------------------


def test_unregistered_repository_is_not_found(service: RepoService) -> None:
    with pytest.raises(RepoNotFound, match="登録されていない"):
        service.exists(ProjectRepos(remote_url="https://example.com/x.git", local_path=None), "a")


def test_missing_directory_is_not_found(service: RepoService, tmp_path: Path) -> None:
    with pytest.raises(RepoNotFound, match="作業ツリーが無い"):
        service.exists(ProjectRepos(local_path=str(tmp_path / "nowhere")), "a")


def test_directory_without_git_is_not_found(service: RepoService, tmp_path: Path) -> None:
    """未 clone の空ディレクトリは作業ツリーとみなさない（上位の git を探しに行かない）。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RepoNotFound, match="未 clone"):
        service.write_file(ProjectRepos(local_path=str(plain)), "a.txt", "x")
    assert not (plain / "a.txt").exists()


def test_relative_local_path_is_not_found(service: RepoService) -> None:
    with pytest.raises(RepoNotFound):
        service.exists(ProjectRepos(local_path="relative/path"), "a")


def test_git_file_marks_a_work_tree(service: RepoService, tmp_path: Path) -> None:
    """worktree・submodule の `.git` はファイルなので、種類を問わず作業ツリーとみなす。"""
    root = tmp_path / "wt"
    root.mkdir()
    (root / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert service.write_file(ProjectRepos(local_path=str(root)), "a.txt", "x") is False


# --- パスの形の規則 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("", "invalid"),
        ("   ", "invalid"),
        (".", "invalid"),
        ("/etc/passwd", "invalid"),
        ("../outside/secret.txt", "invalid"),
        ("src/../../outside/secret.txt", "invalid"),
        ("src\\pkg\\mod.py", "invalid"),
        (".git/config", "git_dir"),
        ("src/.git/hooks/pre-commit", "git_dir"),
        (".env", "denied"),
        ("conf/.env.local", "denied"),
        ("certs/server.pem", "denied"),
        ("keys/id_rsa.pub", "denied"),
    ],
)
def test_path_rules_apply_to_every_operation(
    service: RepoService, repos: ProjectRepos, path: str, kind: str
) -> None:
    for call in (
        lambda: service.read_file(repos, path),
        lambda: service.exists(repos, path),
        lambda: service.write_file(repos, path, "x"),
        lambda: service.delete_file(repos, path),
    ):
        with pytest.raises(RepoPathViolation) as caught:
            call()
        assert caught.value.kind == kind


def test_dot_segments_are_normalized(service: RepoService, repos: ProjectRepos) -> None:
    assert service.read_file(repos, "./src//pkg/./mod.py") == "print('hi')\n"


def test_symlink_escape_is_rejected(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    (work_tree / "link").symlink_to(work_tree.parent / "outside", target_is_directory=True)
    with pytest.raises(RepoPathViolation) as caught:
        service.read_file(repos, "link/secret.txt")
    assert caught.value.kind == "outside_repo"
    with pytest.raises(RepoPathViolation):
        service.write_file(repos, "link/new.txt", "x")
    assert not (work_tree.parent / "outside" / "new.txt").exists()


def test_symlink_into_git_dir_is_rejected(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    (work_tree / "meta").symlink_to(work_tree / ".git", target_is_directory=True)
    with pytest.raises(RepoPathViolation) as caught:
        service.read_file(repos, "meta/config")
    assert caught.value.kind == "git_dir"


# --- 読取 -------------------------------------------------------------------------


def test_read_returns_text(service: RepoService, repos: ProjectRepos) -> None:
    assert service.read_file(repos, "README.md") == "# readme\n"


def test_read_missing_file_is_not_found(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoNotFound) as caught:
        service.read_file(repos, "src/nothing.py")
    assert caught.value.rel_path == "src/nothing.py"


def test_read_directory_is_invalid(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        service.read_file(repos, "src")
    assert caught.value.kind == "invalid"


def test_read_too_large(small_service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        small_service.read_file(repos, "src/pkg/mod.py")
    assert caught.value.kind == "too_large"


def test_read_binary_is_not_text(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    (work_tree / "img.bin").write_bytes(b"\x89PNG\x00\x01")
    with pytest.raises(RepoPathViolation) as caught:
        service.read_file(repos, "img.bin")
    assert caught.value.kind == "not_text"


def test_read_undecodable_is_not_text(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    (work_tree / "latin.txt").write_bytes(b"caf\xe9")
    with pytest.raises(RepoPathViolation) as caught:
        service.read_file(repos, "latin.txt")
    assert caught.value.kind == "not_text"


# --- 書込 -------------------------------------------------------------------------


def test_write_creates_file_and_parents(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    assert service.write_file(repos, "src/new/feature.py", "x = 1\n") is False
    assert (work_tree / "src/new/feature.py").read_text(encoding="utf-8") == "x = 1\n"
    assert service.exists(repos, "src/new/feature.py") is True


def test_write_reports_overwrite_and_keeps_mode(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    script = work_tree / "run.sh"
    script.write_text("echo old\n", encoding="utf-8")
    script.chmod(0o755)
    assert service.write_file(repos, "run.sh", "echo new\n") is True
    assert script.read_text(encoding="utf-8") == "echo new\n"
    assert stat.S_IMODE(script.stat().st_mode) == 0o755


def test_write_new_file_is_not_private(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    """一時ファイル（`mkstemp` は 0600）の権限を新規ファイルへ持ち込まない。"""
    service.write_file(repos, "doc.md", "x")
    assert stat.S_IMODE((work_tree / "doc.md").stat().st_mode) == 0o644


def test_write_leaves_no_temporary_files(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    service.write_file(repos, "src/a.py", "a")
    service.write_file(repos, "src/a.py", "b")
    assert [name for name in os.listdir(work_tree / "src") if name.startswith(".repotmp-")] == []


def test_write_onto_directory_is_invalid(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        service.write_file(repos, "src/pkg", "x")
    assert caught.value.kind == "invalid"


def test_write_below_a_file_is_invalid(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        service.write_file(repos, "README.md/child.txt", "x")
    assert caught.value.kind == "invalid"


def test_write_too_large(small_service: RepoService, repos: ProjectRepos, work_tree: Path) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        small_service.write_file(repos, "big.txt", "123456789")
    assert caught.value.kind == "too_large"
    assert not (work_tree / "big.txt").exists()


# --- 削除・存在 -----------------------------------------------------------------------


def test_delete_removes_only_the_file(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    service.delete_file(repos, "src/pkg/mod.py")
    assert not (work_tree / "src/pkg/mod.py").exists()
    assert (work_tree / "src/pkg").is_dir()


def test_delete_missing_is_not_found(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoNotFound):
        service.delete_file(repos, "src/nothing.py")


def test_delete_directory_is_invalid(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        service.delete_file(repos, "src")
    assert caught.value.kind == "invalid"
    assert (work_tree / "src").is_dir()


def test_exists_is_false_for_missing_and_directories(
    service: RepoService, repos: ProjectRepos
) -> None:
    assert service.exists(repos, "README.md") is True
    assert service.exists(repos, "nothing.md") is False
    assert service.exists(repos, "src") is False


# --- 一覧 -------------------------------------------------------------------------


def test_list_prunes_excluded_and_denied(
    service: RepoService, repos: ProjectRepos, work_tree: Path
) -> None:
    (work_tree / ".repotmp-leftover").write_text("x", encoding="utf-8")
    assert service.list_files(repos, "") == ["README.md", "src/pkg/mod.py"]


def test_list_subdir(service: RepoService, repos: ProjectRepos) -> None:
    assert service.list_files(repos, "src") == ["src/pkg/mod.py"]
    assert service.list_files(repos, ".") == ["README.md", "src/pkg/mod.py"]


def test_list_missing_subdir_is_not_found(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoNotFound):
        service.list_files(repos, "docs")


def test_list_rejects_git_dir(service: RepoService, repos: ProjectRepos) -> None:
    with pytest.raises(RepoPathViolation) as caught:
        service.list_files(repos, ".git")
    assert caught.value.kind == "git_dir"


def test_list_too_many(small_service: RepoService, repos: ProjectRepos, work_tree: Path) -> None:
    (work_tree / "extra.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RepoPathViolation) as caught:
        small_service.list_files(repos, "")
    assert caught.value.kind == "too_large"
