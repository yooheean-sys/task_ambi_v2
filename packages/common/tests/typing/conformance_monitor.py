# pyright: reportUnnecessaryTypeIgnoreComment=true
"""SC-010: `RunMonitor`・`LLMHttpHook`。

`LLMHttpHook` の偽実装では `aon_outbound`・`aon_inbound` を `async def` で書く。
**どのメソッドが非同期かが契約の宣言から一意に分かる**ことを示す（US3 受け入れシナリオ 4）。
`httpx` は `TYPE_CHECKING` 経由でのみ参照する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.executor import CrewOutcome
from common.interfaces.monitor import LLMHttpHook, RunMonitor, RunVerdict
from common.interfaces.settings import LimitPolicy

if TYPE_CHECKING:
    import httpx


class _MonitorOk:
    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold_t: int) -> None: ...
    def finish(
        self, kickoff_id: str, issue_id: str, actor: str, outcome: CrewOutcome
    ) -> RunVerdict: ...


class _MonitorMissingFinish:
    """`finish` を欠く。"""

    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold_t: int) -> None: ...


class _MonitorRenamedArg:
    """`begin` の引数名が違う（`threshold_t` ではなく `threshold`）。"""

    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold: int) -> None: ...
    def finish(
        self, kickoff_id: str, issue_id: str, actor: str, outcome: CrewOutcome
    ) -> RunVerdict: ...


monitor_ok: RunMonitor = _MonitorOk()
monitor_missing: RunMonitor = _MonitorMissingFinish()  # type: ignore
monitor_renamed: RunMonitor = _MonitorRenamedArg()  # type: ignore


class _HookOk:
    def on_outbound(self, request: httpx.Request) -> httpx.Request: ...
    def on_inbound(self, response: httpx.Response) -> httpx.Response: ...
    async def aon_outbound(self, request: httpx.Request) -> httpx.Request: ...
    async def aon_inbound(self, response: httpx.Response) -> httpx.Response: ...


class _HookMissingAonInbound:
    """`aon_inbound` を欠く。"""

    def on_outbound(self, request: httpx.Request) -> httpx.Request: ...
    def on_inbound(self, response: httpx.Response) -> httpx.Response: ...
    async def aon_outbound(self, request: httpx.Request) -> httpx.Request: ...


class _HookRenamedArg:
    """`aon_outbound` の引数名が違う（`request` ではなく `req`）。"""

    def on_outbound(self, request: httpx.Request) -> httpx.Request: ...
    def on_inbound(self, response: httpx.Response) -> httpx.Response: ...
    async def aon_outbound(self, req: httpx.Request) -> httpx.Request: ...
    async def aon_inbound(self, response: httpx.Response) -> httpx.Response: ...


hook_ok: LLMHttpHook = _HookOk()
hook_missing: LLMHttpHook = _HookMissingAonInbound()  # type: ignore
hook_renamed: LLMHttpHook = _HookRenamedArg()  # type: ignore
