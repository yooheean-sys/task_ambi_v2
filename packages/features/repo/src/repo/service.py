"""`RepoReader`・`RepoWriter` の実装（`RepoService`）。

パスの**形**の規則（空・絶対・`..`・バックスラッシュ・`.git`・拒否パターン）と上限（読取・書込・一覧）を
ここで見て、作業ツリーへの入出力は `FsAdapter` に任せる。`FsError` 系は**ここで契約例外へ
翻訳する**（`repository.py` は契約例外を送出しない）。

**判断を持たない**: 何を書いてよいか（権限）は executor の前段（casbin）、成果物が良いかは
orchestrator のレビューが決める。repo は「閉じ込めた相対パスで読み書きできるか」だけを返す。
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.kernel.log import LoggerFactory
from repo.exceptions import FsEscapeError, FsGitDirError, FsKindError, FsNotFoundError
from repo.repository import GIT_DIR

if TYPE_CHECKING:
    from common.interfaces.settings import ProjectRepos
    from repo.repository import FsAdapter

_logger = LoggerFactory.get_logger("repo.service")
"""書込・削除の成否を残す（相関 ID はログ基盤が自動で載せる）。"""


class RepoService:
    """`RepoReader` と `RepoWriter` を 1 つで実装する（`repo_reader is repo_writer`）。"""

    def __init__(
        self,
        adapter: FsAdapter,
        *,
        encoding: str,
        max_read_bytes: int,
        max_write_bytes: int,
        max_list_entries: int,
        deny_patterns: tuple[str, ...],
    ) -> None:
        """入出力のアダプタと上限・拒否パターンを受け取る。"""
        self._adapter = adapter
        self._encoding = encoding
        self._max_read_bytes = max_read_bytes
        self._max_write_bytes = max_write_bytes
        self._max_list_entries = max_list_entries
        self._deny_patterns = deny_patterns

    # -- RepoReader ----------------------------------------------------------------------

    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]:
        """`subdir`（空文字ならリポジトリ全体）配下のファイルをリポジトリ相対で列挙する。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone／`subdir` が実在しない。
            RepoPathViolation: パス規則に違反した／件数が上限を超えた。
        """
        root = self._root(repos)
        rel = self._check_rel(subdir, allow_root=True)
        base = self._resolve(root, rel)
        if not self._adapter.is_dir(base):
            msg = f"ディレクトリが無い: {rel}"
            raise RepoNotFound(msg, rel)
        names = [name for name in self._adapter.walk(root, base) if not self._is_denied(name)]
        if len(names) > self._max_list_entries:
            msg = f"一覧が多すぎる: {rel or '.'}"
            detail = f"{len(names)} 件 > 上限 {self._max_list_entries} 件（subdir で絞る）"
            raise RepoPathViolation(msg, "too_large", detail)
        return names

    def read_file(self, repos: ProjectRepos, path: str) -> str:
        """ファイルを読む。不在は例外で明示する（黙って空を返さない）。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone／指定のファイルが無い。
            RepoPathViolation: パス規則に違反した／大きすぎる／テキストでない。
        """
        root = self._root(repos)
        rel = self._check_rel(path)
        target = self._resolve(root, rel)
        try:
            data = self._adapter.read_bytes(target, rel, self._max_read_bytes)
        except FsNotFoundError as exc:
            raise RepoNotFound(str(exc), rel, exc) from exc
        except FsKindError as exc:
            raise RepoPathViolation(str(exc), "invalid", rel, exc) from exc
        if len(data) > self._max_read_bytes:
            msg = f"ファイルが大きすぎる: {rel}"
            raise RepoPathViolation(msg, "too_large", f"上限 {self._max_read_bytes} バイト")
        if b"\x00" in data:
            msg = f"テキストファイルでない: {rel}"
            raise RepoPathViolation(msg, "not_text", rel)
        try:
            return data.decode(self._encoding)
        except UnicodeDecodeError as exc:
            msg = f"{self._encoding} で読めない: {rel}"
            raise RepoPathViolation(msg, "not_text", rel, exc) from exc

    def exists(self, repos: ProjectRepos, path: str) -> bool:
        """ファイルの存在を確認する（完了ゲート）。ファイルの不在は偽を返す。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone。
            RepoPathViolation: パス規則に違反した。
        """
        root = self._root(repos)
        rel = self._check_rel(path)
        return self._adapter.is_file(self._resolve(root, rel))

    # -- RepoWriter ----------------------------------------------------------------------

    def write_file(self, repos: ProjectRepos, path: str, content: str) -> bool:
        """ファイルを作成または上書きし、既存を上書きしたかを返す。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone。
            RepoPathViolation: パス規則に違反した／大きすぎる。
        """
        root = self._root(repos)
        rel = self._check_rel(path)
        data = content.encode(self._encoding)
        if len(data) > self._max_write_bytes:
            msg = f"書く内容が大きすぎる: {rel}"
            raise RepoPathViolation(msg, "too_large", f"上限 {self._max_write_bytes} バイト")
        target = self._resolve(root, rel)
        try:
            overwritten = self._adapter.write_atomic(target, rel, data)
        except FsKindError as exc:
            raise RepoPathViolation(str(exc), "invalid", rel, exc) from exc
        _logger.info(
            "repo.write",
            extra={"rel_path": rel, "bytes": len(data), "overwritten": overwritten},
        )
        return overwritten

    def delete_file(self, repos: ProjectRepos, path: str) -> None:
        """ファイルを 1 つ削除する（ディレクトリは消さない）。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone／指定のファイルが無い。
            RepoPathViolation: パス規則に違反した／ディレクトリを指した。
        """
        root = self._root(repos)
        rel = self._check_rel(path)
        target = self._resolve(root, rel)
        try:
            self._adapter.delete(target, rel)
        except FsNotFoundError as exc:
            raise RepoNotFound(str(exc), rel, exc) from exc
        except FsKindError as exc:
            raise RepoPathViolation(str(exc), "invalid", rel, exc) from exc
        _logger.info("repo.delete", extra={"rel_path": rel})

    # -- 規則 ---------------------------------------------------------------------------

    def _root(self, repos: ProjectRepos) -> Path:
        """作業ツリーのルートを返す。

        Raises:
            RepoNotFound: `local_path` が未登録／git の作業ツリーでない（未 clone を含む）。
        """
        if repos.local_path is None:
            msg = "リポジトリが登録されていない（project.yaml の repos.local_path）"
            raise RepoNotFound(msg, "")
        root = self._adapter.work_tree(repos.local_path)
        if root is None:
            msg = f"git の作業ツリーが無い（未 clone か、パスが違う）: {repos.local_path}"
            raise RepoNotFound(msg, "")
        return root

    def _check_rel(self, rel: str, *, allow_root: bool = False) -> str:
        """パスの**形**を検査し、正規化したリポジトリ相対の POSIX 形を返す。

        `allow_root` のときだけ空文字・`.` を「リポジトリ全体」として受ける（一覧用）。

        Raises:
            RepoPathViolation: 空・絶対・`..`・バックスラッシュ・NUL（invalid）／`.git`（git_dir）／
                拒否パターン（denied）。
        """
        text = rel.strip()
        if "\\" in text or "\x00" in text:
            msg = f"パスに使えない文字がある（区切りは / ）: {rel!r}"
            raise RepoPathViolation(msg, "invalid", rel)
        if text.startswith("/"):
            msg = f"絶対パスは使えない（リポジトリ相対で書く）: {rel}"
            raise RepoPathViolation(msg, "invalid", rel)
        parts = PurePosixPath(text).parts if text else ()
        if not parts:
            if allow_root:
                return ""
            msg = "パスが空"
            raise RepoPathViolation(msg, "invalid", rel)
        if ".." in parts:
            msg = f"親ディレクトリ（..）は使えない: {rel}"
            raise RepoPathViolation(msg, "invalid", rel)
        if GIT_DIR in parts:
            msg = f".git の配下には触れない: {rel}"
            raise RepoPathViolation(msg, "git_dir", rel)
        normalized = PurePosixPath(*parts).as_posix()
        if self._is_denied(normalized):
            msg = f"拒否パターンに当たる（秘密を含みうる）: {rel}"
            raise RepoPathViolation(msg, "denied", rel)
        return normalized

    def _is_denied(self, rel: str) -> bool:
        """構成要素のどれかが拒否パターン（`fnmatch`・大文字小文字を区別）に当たるか。"""
        return any(
            fnmatchcase(part, pattern)
            for part in PurePosixPath(rel).parts
            for pattern in self._deny_patterns
        )

    def _resolve(self, root: Path, rel: str) -> Path:
        """実体まで解決する（閉じ込めの唯一の入口）。

        Raises:
            RepoPathViolation: 作業ツリーの外（outside_repo）／`.git` の配下（git_dir）。
        """
        try:
            return self._adapter.resolve(root, rel)
        except FsEscapeError as exc:
            raise RepoPathViolation(str(exc), "outside_repo", rel, exc) from exc
        except FsGitDirError as exc:
            raise RepoPathViolation(str(exc), "git_dir", rel, exc) from exc
