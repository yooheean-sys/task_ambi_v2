"""コマンドの 1 道具（executor-F09。IF-30）。

**引数列を渡す手段を持たない**（FR-045・SC-014）。`args_schema` は `name` の 1 欄だけで、
実行する argv は `resolve_command(name)` が返す**固定 argv** である。

**3 通りの異常はすべて戻り値**（FR-047・SC-015）—— 台帳に無い／時間切れ／終了コードが 0 でない。
"""

import subprocess  # noqa: S404  # 台帳が返す固定 argv だけを shell=False・timeout 付きで起動する（P-11）
from typing import Any

from pydantic import BaseModel, Field

from common.interfaces.settings import UnknownCommand
from common.kernel.enums import ToolName
from executor.tools.base import ACT_EXECUTE, GuardedTool, Preparation, ToolContext, obj_for_cmd

STDOUT_LIMIT_BYTES = 2000
"""標準出力の切り詰め量（P-11）。

**コード定数にする**。設定台帳（`x3_config_catalog.csv`）に対応する欄が無く、FR-068 が
`ExecutorConfig` を 6 キーに固定しているため、設定へ開くと契約 §1 の「過不足を許さない」が破れる。
"""

STDERR_HEAD_CHARS = 200
"""失敗時に載せる標準エラーの先頭文字数。"""


class _NameArg(BaseModel):
    """コマンドの入力（**登録名 1 つだけ**）。"""

    name: str = Field(description="コマンド台帳の登録名。引数は渡せない。")


class RunCommandTool(GuardedTool):
    """台帳の登録名でコマンドを 1 つ実行する（二段許可）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        name = str(kwargs.get("name", ""))
        return Preparation(checks=((obj_for_cmd(name), ACT_EXECUTE),), payload=name)

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        name = str(kwargs.get("name", ""))
        try:
            argv = self.ctx.deps.command_registry.resolve_command(name)
        except UnknownCommand as exc:
            return f"ERROR: '{name}' は許可リストに無い。利用可能: {exc}"
        timeout = self.ctx.config.run_command_timeout_sec
        try:
            completed = subprocess.run(  # noqa: S603  # argv は台帳の固定値・shell=False・timeout 付き（P-11）
                argv,
                shell=False,
                timeout=timeout,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: {timeout} 秒を超えたので打ち切った"
        if completed.returncode != 0:
            head = (completed.stderr or "")[:STDERR_HEAD_CHARS]
            return f"ERROR: rc={completed.returncode} / stderr(先頭): {head}"
        return _truncate(completed.stdout or "")


def _truncate(text: str) -> str:
    """標準出力を先頭 `STDOUT_LIMIT_BYTES` バイトで切る。空なら `(出力なし)`。"""
    if not text:
        return "(出力なし)"
    encoded = text.encode("utf-8")
    if len(encoded) <= STDOUT_LIMIT_BYTES:
        return text
    return encoded[:STDOUT_LIMIT_BYTES].decode("utf-8", errors="ignore")


def build_run_command(ctx: ToolContext) -> GuardedTool:
    """`run_command` を組む。"""
    return RunCommandTool(
        name=ToolName.run_command.value,
        description="コマンド台帳に登録された名前でコマンドを 1 つ実行する。引数は渡せない。",
        args_schema=_NameArg,
        ctx=ctx,
        tool_name=ToolName.run_command,
    )
