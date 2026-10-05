"""道具の前段（`GuardedTool`）と共通の材料（`ToolContext`・語彙 4 系統）。

executor-F05。**16 種すべてがこの基底を継承する**（FR-038・SC-007。13〜16 種目は D25 の repo 系）。

`contracts/guarded-tool.md` §1 の 5 不変条件を構造で守る:

1. 16 種すべてが `GuardedTool` を継承する。
2. 派生は `_execute` を実装し、**`_run` を override しない**。
3. 判定は `enforce(subject, obj, act)` の 3 引数。`subject` は常に `member.casbin_subject`。
4. 二段の道具は 2 回呼ぶ。**どちらか一方でも否なら `_execute` を呼ばない**（FR-037）。
5. **例外を外へ出さない**。`crewai.tools.BaseTool.run` は `_run` を `try` で囲まないので
   （実測＝ SD-21）、投げると `tool_usage` の `except Exception` に落ちて AI に届かない。
"""

# **本ファイルだけ `from __future__ import annotations` を置かない**。`GuardedTool` は pydantic の
# モデルで、その派生は別モジュール（`tools/*.py`・テスト）に置かれる。注釈を文字列のままにすると
# pydantic が派生クラスの側で `ToolContext` の欄（`RunSession`・`Member` ほか）を再解決しようとし、
# `... is not fully defined` で組み立てに失敗する。実体で注釈すれば再解決が起きない。

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from crewai.tools import BaseTool

from common.interfaces.executor import CrewEvent, CrewEventKind
from common.interfaces.settings import Member
from common.interfaces.stages import PromptBundle
from common.interfaces.vault import VaultArea
from common.kernel.enums import ToolName
from executor.models import ExecutorConfig, ExecutorDeps, RunSession

# 本ファイルは `from __future__ import annotations` を置かないので、`ToolContext` の欄の型は
# **クラス定義時に評価される**。したがって `TYPE_CHECKING` ではなく実行時に import する。
# 向きは `tools/ → models` であり、C1 の一方向を崩さない。

ACT_EXECUTE = "execute"
ACT_READ = "read"
ACT_WRITE = "write"
"""`act` はこの 3 語だけ（contracts/guarded-tool.md §2）。"""

DENIAL_TEMPLATE = (
    "ERROR: 道具 '{name}' の実行は許可されていない。"
    "別の手段を使うか、escalate_to_human で人に相談してください。"
)
"""前段で拒否したときの文言（命令形で `escalate_to_human` を名指しする＝ §4）。"""


def obj_for_tool(name: ToolName) -> str:
    """`tool:<ToolName の値>`（1 段目・16 種すべて）。"""
    return f"tool:{name.value}"


def obj_for_cmd(name: str) -> str:
    """`cmd:<登録名>`（2 段目・`run_command` だけ）。"""
    return f"cmd:{name}"


def obj_for_vault(area: VaultArea) -> str:
    """`vault:<VaultArea の値>`（2 段目・保管庫の 5 道具）。"""
    return f"vault:{area.value}"


OBJ_REPO_FILES = "repo:files"
"""リポジトリの 4 道具の 2 段目（D25-a）。読取 3 種は `read`、書込・削除は `write` で判定する。"""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """12 道具が共通して要るものを束ねた内部 VO（data-model §3）。

    **`session`・`deps`・`config` はいずれも `executor.models` から import する**
    （`executor/__init__.py` からではない＝ C1）。この 1 点で `tools/ → __init__.py` の逆流が
    消える。

    **`folders` を持たせない**。`session.folders`（原本）から都度 `narrow()` して使う
    （絞った結果を持ち回ると、絞り忘れの経路ができる）。
    """

    session: RunSession
    """唯一の可変。申告・書込・出来事を積む先。"""

    deps: ExecutorDeps
    """9 契約（道具が使うのは 8 つ）。"""

    config: ExecutorConfig
    """`run_command_timeout_sec` だけを使う。"""

    member: Member
    """`casbin_subject`・`bd_actor`・`persona_key`。"""

    issue_id: str
    """AI に選ばせない（指示書が束縛する）。"""

    bundle: PromptBundle
    """`proposal_schema`（`propose_breakdown` の手引き）。"""


if TYPE_CHECKING:
    ToolContextField = ToolContext
else:
    ToolContextField = Any
"""`GuardedTool.ctx` の注釈。**型検査では `ToolContext`・実行時は `Any`** にする。

`ToolContext` は executor 自身が組む内部 VO で、AI の入力から作られることは無い。実行時に
`ToolContext` のまま置くと pydantic（`crewai.tools.BaseTool`）が中身へ再帰し、
`ExecutorDeps` の 9 欄（**いずれも非 `runtime_checkable` な Protocol**）で
`'cls' must be valid as the first argument to 'isinstance'` になってスキーマの構築ごと落ちる。
`@runtime_checkable` を付けるには `packages/common/` の変更が要る（FR-004 で禁止）。
`SkipValidation`・`InstanceOf`・`PlainValidator` はいずれも**内側のスキーマを一度作ってから**
包むので、同じ理由で使えない（実測 2026-08-25）。
"""


@dataclass(frozen=True, slots=True)
class Preparation:
    """`_execute` の**前に**済ませる準備の結果。

    2 段目の判定材料（`checks`）は `_execute` より先に決まっていなければならない
    （FR-037「どちらか一方でも否なら `_execute` を呼ばない」）。`read_input_file` のように
    2 段目の対象が解決の結果に依存する道具は、ここで解決して `payload` に載せる
    （`_execute` から契約を 2 度引かないため）。

    準備の時点で戻り値の文言に翻訳した場合は `denial` に載せる（例外を外へ出さない）。
    """

    checks: Sequence[tuple[str, str]] = ()
    payload: object | None = None
    denial: str | None = None


class GuardedTool(BaseTool, ABC):
    """前段（認可判定）を必ず通してから本体を呼ぶ道具の基底。

    `BaseTool.model_config` が `arbitrary_types_allowed=True` なので、`ToolContext` を
    **素の pydantic フィールド**として持てる（実測＝ SD-21）。
    """

    ctx: ToolContextField
    """この 1 回の実行の材料（`_as_is` で素通しする＝走査させない）。"""

    tool_name: ToolName
    """語彙 `tool:<name>` の材料（`BaseTool.name` とは別に持つ）。"""

    def _run(self, **kwargs: Any) -> str:
        """**基底だけが持つ**。判定を通してから `_execute` を呼ぶ（override 禁止）。"""
        denial = self._enforce(obj_for_tool(self.tool_name), ACT_EXECUTE)
        if denial is not None:
            return denial
        prepared = self._prepare(**kwargs)
        if prepared.denial is not None:
            return prepared.denial
        for obj, act in prepared.checks:
            denial = self._enforce(obj, act)
            if denial is not None:
                return denial
        return self._execute(prepared.payload, **kwargs)

    def _prepare(self, **kwargs: Any) -> Preparation:
        """2 段目の判定材料と準備物を返す（既定は 1 段だけ）。"""
        return Preparation()

    @abstractmethod
    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        """道具の本体。**例外を外へ出さない**（すべて戻り値の文言に翻訳する）。"""

    # -- 前段 ----------------------------------------------------------------------------

    def _enforce(self, obj: str, act: str) -> str | None:
        """1 回分の判定。許可なら `None`、否なら**拒否文言**を返し `tool_denied` を 1 件積む。

        判定器が例外を送出したときは**拒否側に倒す**（黙って許可しない＝ FR-036・SC-006）。
        `except Exception` なので、上限停止（`RunHalted`＝ `BaseException` 直系）は素通りする。
        """
        allowed = False
        try:
            allowed = self.ctx.deps.authorizer.enforce(self.ctx.member.casbin_subject, obj, act)
        except Exception:  # noqa: BLE001  # 判定器の例外は拒否側に倒す（FR-036）。文面は載せない
            allowed = False
        if allowed:
            return None
        self._record_denial(obj, act)
        return DENIAL_TEMPLATE.format(name=self.tool_name.value)

    def _record_denial(self, obj: str, act: str) -> None:
        """`tool_denied` を **1 回の拒否につき 1 件**積む（§5）。

        `reason` には `enforce` に渡した `obj` と `act` だけを入れる。**判定器の例外メッセージは
        入れない**（秘密が混ざりうる）。
        """
        self.ctx.session.add_event(
            CrewEvent(
                kind=CrewEventKind.tool_denied,
                at=datetime.now(UTC),
                detail={"tool": self.tool_name.value, "reason": f"{obj} / {act}"},
            )
        )
