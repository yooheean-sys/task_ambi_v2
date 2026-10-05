# pyright: reportUnnecessaryTypeIgnoreComment=true
"""SC-010: `CrewExecutor`。引数名違いは `run(self, crew_spec: CrewSpec)`（正は `spec`）。"""

from __future__ import annotations

from common.interfaces.executor import CrewExecutor, CrewOutcome, CrewSpec


class _ExecutorOk:
    def run(self, spec: CrewSpec) -> CrewOutcome: ...


class _ExecutorMissingRun:
    """`run` を欠く。"""


class _ExecutorRenamedArg:
    """`run` の引数名が違う（`spec` ではなく `crew_spec`）。"""

    def run(self, crew_spec: CrewSpec) -> CrewOutcome: ...


executor_ok: CrewExecutor = _ExecutorOk()
executor_missing: CrewExecutor = _ExecutorMissingRun()  # type: ignore
executor_renamed: CrewExecutor = _ExecutorRenamedArg()  # type: ignore
