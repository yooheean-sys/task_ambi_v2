"""executor の中核（`CrewExecutorImpl`・Crew の組立・Hook の登録・出来事の翻訳）。

executor-F01／F03／F10／F11。**crewai の語彙を外へ 1 つも出さない**。

**`BaseException` を丸ごと捕まえる `except` を 1 件も書かない**（FR-065）。`BaseException` 系を
捕まえるのは `run` の上限停止の 1 箇所だけである。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import crewai.events as crewai_events
from crewai import Agent, Crew, Process, Task
from crewai.events.base_events import BaseEvent
from crewai.events.event_bus import crewai_event_bus
from crewai.hooks import InterceptionPoint, on, unregister_hook

from common.interfaces.executor import CrewEvent, CrewEventKind, CrewOutcome, ExecutorFailure
from common.interfaces.monitor import RunHalted
from common.kernel.log import LoggerFactory
from executor.models import (
    REASON_HEAD_CHARS,
    ExecutorConfig,
    ExecutorDeps,
    InterceptorAdapter,
    LlmFactory,
    RunSession,
)
from executor.tools import ToolRegistry
from executor.tools.base import GuardedTool, ToolContext

if TYPE_CHECKING:
    from collections.abc import Sequence

    from common.interfaces.executor import CrewSpec
    from common.interfaces.settings import HookRef

type HookFunction = Callable[[object], None]

# --------------------------------------------------------------------------------------
# F11: 出来事の翻訳（contracts/event-translation.md）
# --------------------------------------------------------------------------------------

EVENT_KIND_BY_CLASS: Mapping[str, CrewEventKind] = {
    "LLMCallStartedEvent": CrewEventKind.llm_call_started,
    "LLMCallCompletedEvent": CrewEventKind.llm_call_completed,
    "LLMCallFailedEvent": CrewEventKind.llm_call_failed,
    "ToolUsageStartedEvent": CrewEventKind.tool_called,
    "ToolUsageFinishedEvent": CrewEventKind.tool_called,
    "ToolUsageErrorEvent": CrewEventKind.tool_failed,
    "LLMGuardrailStartedEvent": CrewEventKind.guardrail_failed,
    "LLMGuardrailCompletedEvent": CrewEventKind.guardrail_failed,
    "CrewKickoffCompletedEvent": CrewEventKind.crew_completed,
    "CrewKickoffFailedEvent": CrewEventKind.crew_failed,
}
"""実クラス名 → 中立型の種類（**1 クラス = 1 種類の単純な写像**。欄で分岐しない）。

`ToolUsageFinishedEvent.failure` や `LLMGuardrailCompletedEvent.success` で分岐しない理由:
分岐を入れると翻訳が判断を持ち、SC-019（分担外の経路から積まれる種類が 0 件）が写像表だけでは
検査できなくなる。また `tool_failed` は `ToolUsageErrorEvent` から必ず積まれるので、`Finished`
からも積むと二重になり、重複排除を持たない（FR-062）本層で件数が狂う。
"""

DETAIL_KEYS = ("event", "tool", "error", "model", "reason", "path")
"""`CrewEvent.detail` に入れてよいキー（**この 6 つだけ**＝ §5）。"""

DETAIL_VALUE_LIMIT = 200
"""`detail` の値の上限文字数（器を膨らませない＝ FR-061）。"""


def all_event_classes() -> list[type[BaseEvent]]:
    """`crewai.events.__all__` から `BaseEvent` 派生を導出する（103 件）。

    **`dir()` を使わない**。`crewai.events` はモジュール `__getattr__` による遅延読込で、
    読み込み前の `dir()` には Event 名が 1 件も出ない（実測＝ SD-18）。使うと購読が 0 件になり、
    起動時検査では全件「対応が無い」になって必ず起動中断する。
    """
    classes: list[type[BaseEvent]] = []
    for name in crewai_events.__all__:
        candidate = getattr(crewai_events, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseEvent):
            classes.append(candidate)
    return classes


def _clip(value: object) -> str:
    """値を文字列にして 200 文字で切る。"""
    return str(value)[:DETAIL_VALUE_LIMIT]


def _detail_of(event: object) -> dict[str, str]:
    """`detail` を組む（**6 つのキーだけ**・値はすべて 200 文字で切る）。

    crewai の型・秘密・巨大な本文を入れない。`str()` した結果も含め、型名以外の内部表現を
    入れない（`__repr__` を持つオブジェクトは載せず、素の文字列の欄だけを拾う）。
    """
    detail: dict[str, str] = {"event": type(event).__name__}
    for key, attribute in (("tool", "tool_name"), ("error", "error"), ("model", "model")):
        value = getattr(event, attribute, None)
        if isinstance(value, str) and value:
            detail[key] = _clip(value)
    return detail


@dataclass(slots=True)
class EventTranslator:
    """1 回の実行のあいだ、バスから届く出来事を中立型へ写す（executor-F11）。

    ハンドラは `ThreadPoolExecutor` の**別スレッド**で走る（SD-19）。積む操作は
    `RunSession` 側のメソッドが `_lock` の中で行う。
    """

    session: RunSession
    config: ExecutorConfig

    def handle(self, source: object, event: object) -> None:
        """1 件を振り分ける（**自分で例外を握る**＝ §3）。

        バスはハンドラの例外を `BaseException` ごと握り潰すので、握られると失敗に気づけない。
        ここで `except Exception` して器の外に残す（`RunHalted` は `BaseException` 直系なので
        素通りする）。
        """
        try:
            self._dispatch(event)
        except Exception as exc:  # noqa: BLE001  # バスが握り潰す前に自分で拾う（§3）
            self.session.note_unknown_event(f"<handler-error:{type(exc).__name__}>")

    def _dispatch(self, event: object) -> None:
        """翻訳対象なら器へ、そうでなければ件数と名前だけを控える（FR-058・FR-060）。"""
        name = type(event).__name__
        if name not in self.config.event_allowlist:  # ★ 完全一致（ワイルドカードを解釈しない）
            self.session.note_unknown_event(name)
            return
        kind = EVENT_KIND_BY_CLASS.get(name)
        if kind is None:  # 起動時検査 (b) が防ぐが、実行時も黙って捨てない
            self.session.note_unknown_event(name)
            return
        stamp = getattr(event, "timestamp", None)
        self.session.add_event(
            CrewEvent(
                kind=kind,
                at=stamp if isinstance(stamp, datetime) else datetime.now(UTC),
                detail=_detail_of(event),
            )
        )


# --------------------------------------------------------------------------------------
# F10: 追加の差し込みの登録と解除（contracts/guarded-tool.md の外・DIFF-15）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisteredHook:
    """登録した 1 件（**解除には同じ関数オブジェクトが要る**）。"""

    point: InterceptionPoint
    func: HookFunction


def _make_hook_function(ref: HookRef) -> HookFunction:
    """**HookRef 1 件ごとに新しい関数オブジェクト**を作る（SD-20）。

    同じ関数を 2 回装飾すると `_registered_hook` が上書きされ、1 個目の包みが解除不能になる
    （実測）。`HooksConfig.hooks` に同名が 2 回現れても両方を解除できるようにする。

    **権限の判定をここに置かない**（FR-055）。判定は `GuardedTool` の前段だけが行う。
    """

    def hook(context: object) -> None:
        """登録された印を残すだけの差し込み（M1 は台帳が空なので実体は要らない）。"""
        return None

    hook.__name__ = f"hook_{ref.name}_{ref.points}"
    return hook


def register_hooks(refs: Sequence[HookRef], config: ExecutorConfig) -> list[RegisteredHook]:
    """`hook_catalog` と照合して登録し、解除に使う参照を返す（FR-054・FR-056）。

    Returns:
        登録した差し込みの一覧（`unregister_hooks` にそのまま渡す）。

    Raises:
        ExecutorFailure: 台帳に無い名前がある（`phase="build"`）。
    """
    registered: list[RegisteredHook] = []
    for ref in refs:
        if ref.name not in config.hook_catalog:
            unregister_hooks(registered)  # 途中まで登録した分を残さない
            msg = (
                f"差し込み '{ref.name}' が hook_catalog に無い（台帳: {list(config.hook_catalog)}）"
            )
            raise ExecutorFailure(msg, phase="build", reason=f"UnknownHook: {ref.name}")
        point = InterceptionPoint[ref.points]
        func = _make_hook_function(ref)
        on(point, agents=list(ref.agents) or None)(func)
        registered.append(RegisteredHook(point=point, func=func))
    return registered


def unregister_hooks(registered: Sequence[RegisteredHook]) -> None:
    """登録した参照をすべて解除する（**例外で終わった場合を含め必ず通す**＝ FR-054）。"""
    for entry in registered:
        unregister_hook(entry.point, entry.func)


# --------------------------------------------------------------------------------------
# F01／F03: 1 回の実行（IF-48）
# --------------------------------------------------------------------------------------

_LOGGER = LoggerFactory.get_logger("executor.service")


@dataclass(frozen=True, slots=True)
class CrewExecutorImpl:
    """`CrewExecutor`（IF-48）の実装。

    **`RunSession` をフィールドに持たない**（FR-015・FR-016・SC-003）。器は `run` の局所で
    実行ごとに新規に作り、終わったら捨てる。
    """

    deps: ExecutorDeps
    config: ExecutorConfig
    registry: ToolRegistry
    llm_factory: LlmFactory

    def run(self, spec: CrewSpec) -> CrewOutcome:
        """Crew を組み立てて実行し、中立型の結果を返す（executor-F01）。

        入力（`CrewSpec`）は**検証しない** —— 呼び手（orchestrator）が詰めた材料であり、
        executor は組み立てて走らせるだけである（FR-010）。

        Returns:
            11 欄すべてを埋めた `CrewOutcome`。

        Raises:
            ExecutorFailure: 組立（`phase="build"`）／実行（`phase="kickoff"`）が失敗した。
        """
        session = RunSession(  # 1: 実行ごとに新規
            issue_id=spec.issue_id, member=spec.member, folders=spec.folders, repos=spec.repos
        )
        api_key_ref = spec.member.llm_profile.api_key_ref
        crew, registered = self._build(spec, session)  # 2: 構築（scoped_handlers の外）
        translator = EventTranslator(session=session, config=self.config)
        task_output: str | None = None
        try:
            with crewai_event_bus.scoped_handlers():  # 3: 実行の分離（次の run に漏れない）
                for event_class in all_event_classes():  # 103 クラス全部（SD-18）
                    crewai_event_bus.on(event_class)(translator.handle)
                try:
                    # 抑制の理由: crewai 側の `kickoff` 署名に未解決の総称型が残る
                    # （`input_files: dict[str, Unknown]`）。executor は戻りを `raw` としか
                    # 読まないので、この不明さが executor の型安全性に影響しない。
                    output = crew.kickoff()  # pyright: ignore[reportUnknownMemberType]
                    task_output = getattr(output, "raw", None)
                except RunHalted as halted:  # ★ コード全体で唯一の階層外例外の捕捉（FR-065）
                    session.halt(halted)
                    _LOGGER.warning(
                        "上限で停止した",
                        extra={
                            "issue_id": spec.issue_id,
                            "kickoff": spec.mode,
                            "api_key_ref": api_key_ref,
                            "halted": True,
                            "kind": halted.kind.value,
                            "measured": halted.measured,
                            "limit": halted.limit,
                            "request_no": halted.request_no,
                        },
                    )
                except Exception as exc:
                    msg = f"kickoff が失敗した（課題 '{spec.issue_id}'）"
                    raise ExecutorFailure(
                        msg,
                        phase="kickoff",
                        reason=f"{type(exc).__name__}: {exc}"[:REASON_HEAD_CHARS],
                        cause=exc,
                    ) from exc
                finally:
                    flushed = crewai_event_bus.flush()  # 別スレッドのハンドラを待つ（SD-19）
                    _LOGGER.info(
                        "1 回の実行を終えた",
                        extra={
                            "issue_id": spec.issue_id,
                            "kickoff": spec.mode,
                            "api_key_ref": api_key_ref,
                            "unknown_events": dict(session.unknown_events),
                            "halted": session.halted,
                            "flush_completed": flushed,
                        },
                    )
        finally:  # 4: 例外で抜けても必ず解除する（FR-054）
            unregister_hooks(registered)
        return session.to_outcome(task_output)  # 5

    def _build(self, spec: CrewSpec, session: RunSession) -> tuple[Crew, list[RegisteredHook]]:
        """構築段（鍵解決 → `LLM` → 装備 → Crew → 差し込み）。

        ここでの失敗はすべて `ExecutorFailure(phase="build")` になる（FR-063・SC-021）。

        Returns:
            組み上がった `Crew` と、解除に使う差し込みの参照。

        Raises:
            ExecutorFailure: 構築段が失敗した。
        """
        registered: list[RegisteredHook] = []
        try:
            adapter = InterceptorAdapter(hook=self.deps.llm_http_hook)
            llm = self.llm_factory.build(spec.member.llm_profile, adapter)
            context = ToolContext(
                session=session,
                deps=self.deps,
                config=self.config,
                member=spec.member,
                issue_id=spec.issue_id,
                bundle=spec.bundle,
            )
            tools = self.registry.resolve(spec.member.tools, spec.bundle.task_tools, context)
            crew = self._build_crew(spec, llm, tools)
            registered = register_hooks(spec.hooks.hooks, self.config)
        except ExecutorFailure:
            unregister_hooks(registered)
            raise
        except Exception as exc:
            unregister_hooks(registered)
            msg = f"Crew を組み立てられない（課題 '{spec.issue_id}'）"
            raise ExecutorFailure(
                msg,
                phase="build",
                reason=f"{type(exc).__name__}: {exc}"[:REASON_HEAD_CHARS],
                cause=exc,
            ) from exc
        return crew, registered

    def _build_crew(self, spec: CrewSpec, llm: object, tools: Sequence[GuardedTool]) -> Crew:
        """**1 Agent・1 Task・逐次**の Crew を組む（executor-F03。P-13）。

        - **人格の 3 欄は `spec.bundle` 側**から採る（C4・FR-025）。`Member` 側から採るのは
          `PromptBundle` に無い欄だけである。
        - `max_iter`・`max_rpm` は**メンバ側を優先**し、`None` ならモジュール設定を既定にする。
          **`max_execution_time` は設定へ落とさずそのまま渡す**（設定に欄が無い＝ C2・FR-026）。
        - **`skills`・`mcps` は渡さない**（1.15.17 の `Agent` は 2 欄を持つが、`str` の意味が
          探索パスであるなど `Member` 側の値と型が合わない＝ C5・SD-22）。
        - **委譲を許さない**（`allow_delegation=False`）。1 Agent なので委譲先が無い（FR-027）。

        Returns:
            組み上がった `Crew`。
        """
        member = spec.member
        agent = Agent(
            role=spec.bundle.role,
            goal=spec.bundle.goal,
            backstory=spec.bundle.backstory,
            llm=llm,
            tools=list(tools),
            max_iter=member.max_iter if member.max_iter is not None else self.config.max_iter,
            max_rpm=member.max_rpm if member.max_rpm is not None else self.config.max_rpm,
            max_execution_time=member.max_execution_time,
            max_retry_limit=1,
            allow_delegation=False,
            verbose=False,
        )
        task = Task(
            description=spec.bundle.task_description,
            expected_output=spec.bundle.expected_output,
            agent=agent,
        )
        return Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
