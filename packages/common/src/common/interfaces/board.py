"""board が提供する契約（IF-01〜22）。

盤面（beads）の読み書き。利用側はこのファイル 1 つで「呼べるメソッド・渡す型・
返る型・飛んでくる例外」が読める。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/board/p5_board_data_model.mmd` の `namespace common_interfaces_board_py`。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from common.kernel.enums import Labels
from common.kernel.errors import NotFoundError, UnavailableError, UnverifiedError


class IssueStatus(StrEnum):
    """課題の状態。"""

    open = "open"
    in_progress = "in_progress"
    blocked = "blocked"
    closed = "closed"


class IssueRef(BaseModel):
    """依存先の要約。"""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: IssueStatus
    labels: list[str]
    dependency_type: Literal["blocks", "parent-child"]


class Issue(BaseModel):
    """課題 1 件（DM-13 の写像）。"""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str
    status: IssueStatus
    labels: list[str]
    parent: str | None
    assignee: str | None
    acceptance: str
    dependencies: list[IssueRef]
    created_at: datetime
    updated_at: datetime


class Comment(BaseModel):
    """コメント 1 件（DM-16 の生データ）。種別は持たない（P5D-005）。"""

    model_config = ConfigDict(frozen=True)

    id: str
    issue_id: str
    actor: str
    body: str
    created_at: datetime


class IssueSpec(BaseModel):
    """起票仕様。ラベルは呼び手が全て載せる（FN-05）。"""

    model_config = ConfigDict(frozen=True)

    title: str
    description: str
    labels: list[str]
    parent: str | None
    acceptance: str
    priority: int = 2
    issue_type: str = "task"


class ClaimFilter(BaseModel):
    """起動条件の写像（DM-23）。`labels` は AND 条件。"""

    model_config = ConfigDict(frozen=True)

    labels: frozenset[str] = frozenset({Labels.role_executor()})
    project_key: str | None


class IssueNotFound(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_board_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """指定の課題が盤面に無い。"""

    issue_id: str

    def __init__(self, message: str, issue_id: str, cause: BaseException | None = None) -> None:
        """`issue_id` を保持する。"""
        super().__init__(message, cause)
        self.issue_id = issue_id


class BoardUnavailable(UnavailableError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_board_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """外部ツール `bd` に届かない。"""

    argv: list[str]
    rc: int
    stderr_head: str
    attempts: int

    def __init__(
        self,
        message: str,
        argv: list[str],
        rc: int,
        stderr_head: str,
        attempts: int,
        cause: BaseException | None = None,
    ) -> None:
        """実行した argv・終了コード・stderr 先頭・試行回数を保持する。"""
        super().__init__(message, cause)
        self.argv = argv
        self.rc = rc
        self.stderr_head = stderr_head
        self.attempts = attempts


class BoardWriteUnverified(UnverifiedError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_board_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """書いた結果を確認読取で確かめられない（P3）。"""

    issue_id: str
    mismatch: str

    def __init__(
        self, message: str, issue_id: str, mismatch: str, cause: BaseException | None = None
    ) -> None:
        """`issue_id` と不一致の内容を保持する（R-16）。"""
        super().__init__(message, cause)
        self.issue_id = issue_id
        self.mismatch = mismatch


class BoardReader(Protocol):
    """盤面の読取（IF-01〜09）。"""

    def show(self, issue_id: str) -> Issue:
        """課題 1 件を読む。

        Raises:
            IssueNotFound: 指定の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def children_of(self, parent_id: str) -> list[Issue]:
        """子課題を盤面から導出して読む。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def open_question_of(self, issue_id: str) -> Issue | None:
        """未回答の `kind:question` 子を 1 件返す。無ければ `None`。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """`state:working` の残骸を列挙する。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def comments(self, issue_id: str) -> list[Comment]:
        """コメントを時刻順に読む。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def has_open_issues(self, project_key: str) -> bool:
        """`project:<key>` の open な課題があるか（削除ガード）。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...


class BoardWriter(Protocol):
    """盤面の書込（IF-10〜22）。"""

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """次の 1 件を確保（claim）する。対象が無ければ `None`。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def create(self, spec: IssueSpec, actor: str) -> str:
        """課題を起票し、新しい issue_id を返す。

        Raises:
            BoardUnavailable: `bd` に届かない。
            BoardWriteUnverified: 書込を確認読取で確かめられない。
        """
        ...

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """兄弟依存（blocks）を張る。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """依存元を付け替える。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def release(self, issue_id: str, actor: str) -> None:
        """確保（claim）を解放する（`state:working` を外す）。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """コメントを 1 件付ける。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """課題を close する。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """ラベルを付け外しする。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        ...
