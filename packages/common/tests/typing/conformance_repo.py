# pyright: reportUnnecessaryTypeIgnoreComment=true
"""`RepoReader`・`RepoWriter`（D25）。"""

from __future__ import annotations

from common.interfaces.repo import RepoReader, RepoWriter
from common.interfaces.settings import ProjectRepos


class _RepoReaderOk:
    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]: ...
    def read_file(self, repos: ProjectRepos, path: str) -> str: ...
    def exists(self, repos: ProjectRepos, path: str) -> bool: ...


class _RepoReaderMissingExists:
    """`exists` を欠く。"""

    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]: ...
    def read_file(self, repos: ProjectRepos, path: str) -> str: ...


class _RepoWriterOk:
    def write_file(self, repos: ProjectRepos, path: str, content: str) -> bool: ...
    def delete_file(self, repos: ProjectRepos, path: str) -> None: ...


class _RepoWriterWrongReturn:
    """`write_file` の戻りが違う（`bool` ではなく `str`）。"""

    def write_file(self, repos: ProjectRepos, path: str, content: str) -> str: ...
    def delete_file(self, repos: ProjectRepos, path: str) -> None: ...


reader_ok: RepoReader = _RepoReaderOk()
reader_missing: RepoReader = _RepoReaderMissingExists()  # type: ignore
writer_ok: RepoWriter = _RepoWriterOk()
writer_wrong: RepoWriter = _RepoWriterWrongReturn()  # type: ignore
