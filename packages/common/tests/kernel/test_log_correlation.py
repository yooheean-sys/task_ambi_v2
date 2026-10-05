"""SC-007: 相関 ID が入れ子・asyncio タスク・スレッドで独立する（漏洩 0 件）。"""

from __future__ import annotations

import asyncio
import threading

from common.kernel.log import CorrelationContext


def test_unbound_current_is_none() -> None:
    """未束縛時は `None` を返す（落ちない）。"""
    assert CorrelationContext.current() is None


def test_bind_and_reset() -> None:
    token = CorrelationContext.bind("c-1")
    assert CorrelationContext.current() == "c-1"
    CorrelationContext.reset(token)
    assert CorrelationContext.current() is None


def test_nested_bind_returns_to_the_previous_value() -> None:
    outer = CorrelationContext.bind("c-1")
    inner = CorrelationContext.bind("c-2")
    assert CorrelationContext.current() == "c-2"
    CorrelationContext.reset(inner)
    assert CorrelationContext.current() == "c-1"
    CorrelationContext.reset(outer)
    assert CorrelationContext.current() is None


def test_reset_after_an_exception_still_restores() -> None:
    """例外経路で `reset` し損ねないことは呼び側の責務。`try/finally` で成立する。"""
    token = CorrelationContext.bind("c-1")
    try:
        raise RuntimeError("失敗")
    except RuntimeError:
        pass
    finally:
        CorrelationContext.reset(token)
    assert CorrelationContext.current() is None


def test_concurrent_asyncio_tasks_are_independent() -> None:
    async def worker(name: str, delay: float) -> str:
        token = CorrelationContext.bind(name)
        await asyncio.sleep(delay)
        seen = CorrelationContext.current()
        CorrelationContext.reset(token)
        return seen or ""

    async def main() -> tuple[str, ...]:
        return tuple(await asyncio.gather(worker("a", 0.02), worker("b", 0.0)))

    assert asyncio.run(main()) == ("a", "b")
    assert CorrelationContext.current() is None


def test_threads_are_independent() -> None:
    seen: dict[str, str | None] = {}
    started = threading.Barrier(2)

    def worker(name: str) -> None:
        token = CorrelationContext.bind(name)
        started.wait(timeout=5)
        seen[name] = CorrelationContext.current()
        CorrelationContext.reset(token)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert seen == {"a": "a", "b": "b"}
    assert CorrelationContext.current() is None


def test_parent_context_is_not_polluted_by_a_task() -> None:
    token = CorrelationContext.bind("parent")

    async def worker() -> None:
        inner = CorrelationContext.bind("child")
        CorrelationContext.reset(inner)

    async def main() -> None:
        await asyncio.gather(worker())

    asyncio.run(main())
    assert CorrelationContext.current() == "parent"
    CorrelationContext.reset(token)


def test_correlation_id_is_not_a_protocol_argument() -> None:
    """相関 ID は契約の引数に持たせない（FR-020）。41 メソッドに現れないこと。"""
    from _support.declarations import all_protocol_methods

    offenders = [
        f"{p}.{n}"
        for (p, n), decl in all_protocol_methods().items()
        if "correlation_id" in decl.arg_names
    ]
    assert offenders == []
