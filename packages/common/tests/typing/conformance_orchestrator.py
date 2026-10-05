# pyright: reportUnnecessaryTypeIgnoreComment=true
"""SC-010: `KickoffRunner`。"""

from __future__ import annotations

from common.interfaces.orchestrator import (
    ExecutionContext,
    KickoffResult,
    KickoffRunner,
)


class _RunnerOk:
    def run(self, ctx: ExecutionContext) -> KickoffResult: ...


class _RunnerMissingRun:
    """`run` を欠く。"""


class _RunnerRenamedArg:
    """`run` の引数名が違う（`ctx` ではなく `context`）。"""

    def run(self, context: ExecutionContext) -> KickoffResult: ...


runner_ok: KickoffRunner = _RunnerOk()
runner_missing: KickoffRunner = _RunnerMissingRun()  # type: ignore
runner_renamed: KickoffRunner = _RunnerRenamedArg()  # type: ignore
