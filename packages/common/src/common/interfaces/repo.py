"""repo が提供する契約（IF-51〜55）。

プロジェクトのローカルリポジトリ（`ProjectRepos.local_path`）上のファイルの読取と書込。
**git 操作（commit・push 等）は持たない**（D25-c）。パスはすべてリポジトリ相対の POSIX 形。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/repo/p5_repo_data_model.mmd` の `namespace common_interfaces_repo_py`。
"""

from __future__ import annotations

from typing import Literal, Protocol

from common.interfaces.settings import ProjectRepos
from common.kernel.errors import NotFoundError, ViolationError


class RepoNotFound(NotFoundError):  # noqa: N818  # 例外名は設計が正本（p5_repo_data_model.mmd）。vault の VaultNotFound と同じ命名
    """リポジトリ（未登録・未 clone）または指定のファイルが無い。"""

    rel_path: str

    def __init__(self, message: str, rel_path: str, cause: BaseException | None = None) -> None:
        """不在のリポジトリ相対パス（リポジトリ自体が無いときは空文字）を保持する。"""
        super().__init__(message, cause)
        self.rel_path = rel_path


class RepoPathViolation(ViolationError):  # noqa: N818  # 例外名は設計が正本（p5_repo_data_model.mmd）。vault の VaultPathViolation と同じ命名
    """パス・名前・内容の規則に違反した。"""

    kind: Literal["invalid", "outside_repo", "git_dir", "denied", "too_large", "not_text"]
    detail: str

    def __init__(
        self,
        message: str,
        kind: Literal["invalid", "outside_repo", "git_dir", "denied", "too_large", "not_text"],
        detail: str,
        cause: BaseException | None = None,
    ) -> None:
        """違反の種類と、違反したパス・上限などの詳細を保持する。"""
        super().__init__(message, cause)
        self.kind = kind
        self.detail = detail


class RepoReader(Protocol):
    """リポジトリの読取（IF-51〜53）。"""

    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]:
        """`subdir`（空文字ならリポジトリ全体）配下のファイルをリポジトリ相対で列挙する。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone／`subdir` が実在しない。
            RepoPathViolation: パス規則に違反した。
        """
        ...

    def read_file(self, repos: ProjectRepos, path: str) -> str:
        """ファイルを読む。不在は例外で明示する（黙って空を返さない）。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone／指定のファイルが無い。
            RepoPathViolation: パス規則に違反した。
        """
        ...

    def exists(self, repos: ProjectRepos, path: str) -> bool:
        """ファイルの存在を確認する（完了ゲート）。ファイルの不在は偽を返す。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone。
            RepoPathViolation: パス規則に違反した。
        """
        ...


class RepoWriter(Protocol):
    """リポジトリの書込（IF-54・55）。"""

    def write_file(self, repos: ProjectRepos, path: str, content: str) -> bool:
        """ファイルを作成または上書きし、既存を上書きしたかを返す。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone。
            RepoPathViolation: パス規則に違反した。
        """
        ...

    def delete_file(self, repos: ProjectRepos, path: str) -> None:
        """ファイルを 1 つ削除する（ディレクトリは消さない）。

        Raises:
            RepoNotFound: リポジトリが未登録・未 clone／指定のファイルが無い。
            RepoPathViolation: パス規則に違反した。
        """
        ...
