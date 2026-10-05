"""リポジトリの 4 道具（D25-a。IF-51・52・54・55）。

**共通**: 対応付けは `session.repos`（`CrewSpec.repos` の写し）から**呼び出しのたびに**引く。
未登録（`None`）なら repo に問い合わせずに理由を返す。パスはリポジトリ相対の POSIX 形で、
閉じ込め（`..`・シンボリックリンク・`.git`・秘密の拒否パターン）は repo が判定する。

`RepoNotFound`・`RepoPathViolation` は**すべて戻り値の理由**に翻訳する（例外を外へ出さない）。
**不在を成功に倒さない**。commit・push の手段は持たない（D25-c）。
"""

from typing import Any

from pydantic import BaseModel, Field

from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.interfaces.settings import ProjectRepos
from common.kernel.enums import ToolName
from executor.tools.base import (
    ACT_READ,
    ACT_WRITE,
    OBJ_REPO_FILES,
    GuardedTool,
    Preparation,
    ToolContext,
)

UNREGISTERED = (
    "ERROR: このプロジェクトにはローカルリポジトリが登録されていない。"
    "escalate_to_human で人に相談してください。"
)
"""`session.repos` が無いときの文言（repo に問い合わせない）。"""

RETRY_HINT = "パスを確かめて呼び直してください（リポジトリ相対。repo/ は付けない）。"
"""契約例外の理由に添える次の一手。"""


class _SubdirArg(BaseModel):
    """一覧の入力。"""

    subdir: str = Field(
        default="",
        description="一覧するディレクトリ（リポジトリ相対。空ならリポジトリ全体）",
    )


class _PathArg(BaseModel):
    """パス 1 つだけを受ける入力。"""

    path: str = Field(description="リポジトリ相対のファイルパス（例: src/app/main.py）")


class _WriteArgs(BaseModel):
    """書込の入力。"""

    path: str = Field(description="リポジトリ相対のファイルパス（例: src/app/main.py）")
    content: str = Field(description="ファイルの全文（部分ではなく全体を渡す）")


class _RepoTool(GuardedTool):
    """リポジトリに触れる道具の共通部（対応付けの引き方を 1 本にする）。"""

    def repos(self) -> ProjectRepos | None:
        """**呼び出しのたびに**セッションから引く（持ち回らない）。"""
        return self.ctx.session.repos


class ListRepoFilesTool(_RepoTool):
    """リポジトリのファイルを一覧する。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((OBJ_REPO_FILES, ACT_READ),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        repos = self.repos()
        if repos is None:
            return UNREGISTERED
        subdir = str(kwargs.get("subdir", "") or "")
        try:
            names = self.ctx.deps.repo_reader.list_files(repos, subdir)
        except (RepoNotFound, RepoPathViolation) as exc:
            return f"ERROR: {exc}。{RETRY_HINT}"
        return "\n".join(names) if names else "(ファイルなし)"


class ReadRepoFileTool(_RepoTool):
    """リポジトリのファイルを読む（**不在を成功に倒さない**）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((OBJ_REPO_FILES, ACT_READ),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        repos = self.repos()
        if repos is None:
            return UNREGISTERED
        path = str(kwargs.get("path", ""))
        try:
            return self.ctx.deps.repo_reader.read_file(repos, path)
        except (RepoNotFound, RepoPathViolation) as exc:
            return f"ERROR: {exc}。{RETRY_HINT}"


class WriteRepoFileTool(_RepoTool):
    """リポジトリのファイルを作成・上書きする。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((OBJ_REPO_FILES, ACT_WRITE),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        repos = self.repos()
        if repos is None:
            return UNREGISTERED
        path = str(kwargs.get("path", ""))
        content = str(kwargs.get("content", ""))
        try:
            overwritten = self.ctx.deps.repo_writer.write_file(repos, path, content)
        except (RepoNotFound, RepoPathViolation) as exc:
            return f"ERROR: {exc}。{RETRY_HINT}"
        verb = "上書きした" if overwritten else "作成した"
        return f"{verb}: {path}（{len(content.encode())} バイト）"


class DeleteRepoFileTool(_RepoTool):
    """リポジトリのファイルを 1 つ削除する（ディレクトリは消さない）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((OBJ_REPO_FILES, ACT_WRITE),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        repos = self.repos()
        if repos is None:
            return UNREGISTERED
        path = str(kwargs.get("path", ""))
        try:
            self.ctx.deps.repo_writer.delete_file(repos, path)
        except (RepoNotFound, RepoPathViolation) as exc:
            return f"ERROR: {exc}。{RETRY_HINT}"
        return f"削除した: {path}"


def build_list_repo_files(ctx: ToolContext) -> GuardedTool:
    """`list_repo_files` を組む。"""
    return ListRepoFilesTool(
        name=ToolName.list_repo_files.value,
        description=(
            "プロジェクトのローカルリポジトリのファイルを一覧する（リポジトリ相対のパス）。"
            "subdir を渡すとその配下だけ。多すぎるときは subdir で絞る。"
        ),
        args_schema=_SubdirArg,
        ctx=ctx,
        tool_name=ToolName.list_repo_files,
    )


def build_read_repo_file(ctx: ToolContext) -> GuardedTool:
    """`read_repo_file` を組む。"""
    return ReadRepoFileTool(
        name=ToolName.read_repo_file.value,
        description="ローカルリポジトリのファイルを読む。パスは list_repo_files で確かめる。",
        args_schema=_PathArg,
        ctx=ctx,
        tool_name=ToolName.read_repo_file,
    )


def build_write_repo_file(ctx: ToolContext) -> GuardedTool:
    """`write_repo_file` を組む。"""
    return WriteRepoFileTool(
        name=ToolName.write_repo_file.value,
        description=(
            "ローカルリポジトリのファイルを作成または上書きする（全文を渡す）。"
            "親ディレクトリは自動で作る。commit はしない。"
        ),
        args_schema=_WriteArgs,
        ctx=ctx,
        tool_name=ToolName.write_repo_file,
    )


def build_delete_repo_file(ctx: ToolContext) -> GuardedTool:
    """`delete_repo_file` を組む。"""
    return DeleteRepoFileTool(
        name=ToolName.delete_repo_file.value,
        description="ローカルリポジトリのファイルを 1 つ削除する（ディレクトリは消さない）。",
        args_schema=_PathArg,
        ctx=ctx,
        tool_name=ToolName.delete_repo_file,
    )
