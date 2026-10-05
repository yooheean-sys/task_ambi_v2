"""保管庫の 5 道具（executor-F06。IF-33／34／35／37）。

**共通**: `folders = narrow(session.folders, member.persona_key)` を**呼び出しのたびに**通す
（FR-039・FR-039a）。役割が宣言に無ければ `role_dirs={}` を渡し、保管庫が返す理由をそのまま
文言にする（FR-039b。**空の一覧に倒さない**）。

`VaultNotFound`・`VaultPathViolation` は**すべて戻り値の理由**に翻訳する（FR-041・SC-013）。
**不在を成功に倒さない**（FN-13）。
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from common.interfaces.executor import CrewEvent, CrewEventKind
from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import FileRef, VaultArea, VaultNotFound, VaultPathViolation
from common.kernel.enums import PersonaKey, ToolName
from executor.tools.base import (
    ACT_READ,
    ACT_WRITE,
    GuardedTool,
    Preparation,
    ToolContext,
    obj_for_vault,
)

INPUT_AREAS = (VaultArea.pj_input, VaultArea.role)
"""`list_input_files` が返す領域（`list_inputs` は 3 領域をまとめて返す＝ SD-12）。"""


def narrow(folders: ProjectFolders, persona: PersonaKey) -> ProjectFolders:
    """役割の絞り（P5D-067・D22）。`role_dirs` を**当該役割の 1 件だけ**にする。

    役割が宣言に無ければ `role_dirs={}` にする。**空の一覧に倒さない**（FR-039b）——
    保管庫が「役割フォルダが宣言に無い」と例外で答えるので、その理由を文言にする。
    """
    directory = folders.role_dirs.get(persona)
    role_dirs = {persona: directory} if directory is not None else {}
    return folders.model_copy(update={"role_dirs": role_dirs})


def _reason(exc: BaseException) -> str:
    """契約例外を文言へ（**絶対パス・crewai の型・スタックトレースを入れない**＝ §4）。"""
    return str(exc)


class _NoArgs(BaseModel):
    """引数を持たない道具の入力。"""


class _FilenameArg(BaseModel):
    """ファイル名 1 つだけを受ける入力。"""

    filename: str = Field(description="保管庫のファイル名（パスではなく名前）")


class _WriteArgs(BaseModel):
    """成果物の書込の入力。"""

    filename: str = Field(description="書き出すファイル名（パスではなく名前）")
    content: str = Field(description="書き出す本文")


class _VaultTool(GuardedTool):
    """保管庫に触れる道具の共通部（絞りの経路を 1 本にする）。"""

    def folders(self) -> ProjectFolders:
        """**呼び出しのたびに**原本から絞る（絞った結果を持ち回らない）。"""
        return narrow(self.ctx.session.folders, self.ctx.member.persona_key)

    def _list_inputs(self) -> list[FileRef] | str:
        """`list_inputs` を引く。契約例外は文言へ翻訳して返す。"""
        try:
            return self.ctx.deps.vault_reader.list_inputs(
                self.folders(), self.ctx.member.persona_key
            )
        except (VaultNotFound, VaultPathViolation) as exc:
            return f"ERROR: {_reason(exc)}。ファイル名を確かめて呼び直してください。"


class ListInputFilesTool(_VaultTool):
    """入力（PJ 入力・役割フォルダ）のファイルを一覧する。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=tuple((obj_for_vault(area), ACT_READ) for area in INPUT_AREAS))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        listed = self._list_inputs()
        if isinstance(listed, str):
            return listed
        names = [ref.name for ref in listed if ref.area in INPUT_AREAS]
        return "\n".join(names) if names else "(入力ファイルなし)"


class ListOutcomeFilesTool(_VaultTool):
    """成果物のファイルを一覧する（**専用の契約メソッドは無い**＝ SD-12）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((obj_for_vault(VaultArea.outcome), ACT_READ),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        listed = self._list_inputs()
        if isinstance(listed, str):
            return listed
        names = [ref.name for ref in listed if ref.area is VaultArea.outcome]
        return "\n".join(names) if names else "(成果物なし)"


class ReadInputFileTool(_VaultTool):
    """入力ファイルを読む（名前 → `FileRef` に**解決してから**読む＝ SD-13・F-37）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        filename = str(kwargs.get("filename", ""))
        listed = self._list_inputs()
        if isinstance(listed, str):
            return Preparation(denial=listed)
        candidates = [ref for ref in listed if ref.area in INPUT_AREAS]
        matched = [ref for ref in candidates if ref.name == filename]
        if not matched:
            available = ", ".join(ref.name for ref in candidates) or "(なし)"
            return Preparation(
                denial=(
                    f"ERROR: '{filename}' は入力に無い。"
                    f"ファイル名を確かめて呼び直してください。利用可能: {available}"
                )
            )
        ref = matched[0]
        return Preparation(checks=((obj_for_vault(ref.area), ACT_READ),), payload=ref)

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        if not isinstance(payload, FileRef):  # pragma: no cover - `_prepare` が必ず載せる
            return "ERROR: 入力ファイルの参照を解決できなかった。"
        try:
            return self.ctx.deps.vault_reader.read_input(self.folders(), payload)
        except (VaultNotFound, VaultPathViolation) as exc:
            return f"ERROR: {_reason(exc)}。ファイル名を確かめて呼び直してください。"


class ReadOutcomeFileTool(_VaultTool):
    """成果物ファイルを読む（**不在を成功に倒さない**＝ FN-13）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((obj_for_vault(VaultArea.outcome), ACT_READ),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        filename = str(kwargs.get("filename", ""))
        try:
            return self.ctx.deps.vault_reader.read_outcome_file(self.folders(), filename)
        except (VaultNotFound, VaultPathViolation) as exc:
            return f"ERROR: {_reason(exc)}。ファイル名を確かめて呼び直してください。"


class WriteOutcomeTool(_VaultTool):
    """成果物を書く（`narrow()` も通す＝**絞りの経路を 1 本にする**）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((obj_for_vault(VaultArea.outcome), ACT_WRITE),))

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        filename = str(kwargs.get("filename", ""))
        content = str(kwargs.get("content", ""))
        try:
            written = self.ctx.deps.outcome_writer.write_outcome(self.folders(), filename, content)
        except (VaultNotFound, VaultPathViolation) as exc:
            return f"ERROR: {_reason(exc)}。ファイル名を確かめて呼び直してください。"
        self.ctx.session.add_written(written)
        if written.overwritten:
            self.ctx.session.add_event(
                CrewEvent(
                    kind=CrewEventKind.outcome_overwritten,
                    at=datetime.now(UTC),
                    detail={"path": written.rel_path},
                )
            )
        return f"書き出した: {written.rel_path}（{written.bytes} バイト）"


def build_list_input_files(ctx: ToolContext) -> GuardedTool:
    """`list_input_files` を組む。"""
    return ListInputFilesTool(
        name=ToolName.list_input_files.value,
        description="入力（PJ 入力・自分の役割フォルダ）のファイル名を一覧する。引数は無い。",
        args_schema=_NoArgs,
        ctx=ctx,
        tool_name=ToolName.list_input_files,
    )


def build_read_input_file(ctx: ToolContext) -> GuardedTool:
    """`read_input_file` を組む。"""
    return ReadInputFileTool(
        name=ToolName.read_input_file.value,
        description="入力ファイルを名前で読む。名前は list_input_files で確かめる。",
        args_schema=_FilenameArg,
        ctx=ctx,
        tool_name=ToolName.read_input_file,
    )


def build_list_outcome_files(ctx: ToolContext) -> GuardedTool:
    """`list_outcome_files` を組む。"""
    return ListOutcomeFilesTool(
        name=ToolName.list_outcome_files.value,
        description="成果物フォルダのファイル名を一覧する。引数は無い。",
        args_schema=_NoArgs,
        ctx=ctx,
        tool_name=ToolName.list_outcome_files,
    )


def build_read_outcome_file(ctx: ToolContext) -> GuardedTool:
    """`read_outcome_file` を組む。"""
    return ReadOutcomeFileTool(
        name=ToolName.read_outcome_file.value,
        description="成果物ファイルを名前で読む。名前は list_outcome_files で確かめる。",
        args_schema=_FilenameArg,
        ctx=ctx,
        tool_name=ToolName.read_outcome_file,
    )


def build_write_outcome(ctx: ToolContext) -> GuardedTool:
    """`write_outcome` を組む。"""
    return WriteOutcomeTool(
        name=ToolName.write_outcome.value,
        description="成果物を書き出す。同名があれば上書きになる。",
        args_schema=_WriteArgs,
        ctx=ctx,
        tool_name=ToolName.write_outcome,
    )
