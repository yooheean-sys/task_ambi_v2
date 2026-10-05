"""US5: 1 回の実行がログで串刺しにできる（HTTP 入口の相関 ID）。

検証行: X-1〜X-7・X-10（US5 全 AC・SC-008・FR-019〜023）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from _support.composition import ClientFactory, build_test_app, make_root_config
from _support.fake_modules import CallRecorder, make_registration
from fastapi import APIRouter

from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from collections.abc import Iterator

    from api.wiring import ModuleRegistration

CORRELATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_HEADER = "X-Correlation-ID"
LOG_LINES_PER_REQUEST = 2


class _CorrelationRecordingHandler(logging.Handler):
    """1 レコードごとに、そのとき束縛されている相関 ID を控える。"""

    def __init__(self) -> None:
        """記録用の空の一覧を用意する。"""
        super().__init__()
        self.seen: list[str | None] = []

    def emit(self, record: logging.LogRecord) -> None:
        """束縛中の相関 ID を控える。"""
        self.seen.append(CorrelationContext.current())


@pytest.fixture
def recording_handler() -> Iterator[_CorrelationRecordingHandler]:
    """ルートロガに記録用ハンドラを付ける（終了時に外す）。"""
    handler = _CorrelationRecordingHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


def _echo_registration(name: str = "echo", *, delay: float = 0.0) -> ModuleRegistration:
    """束縛中の相関 ID を返す経路を 1 本持つ偽のモジュール登録。

    Args:
        name: モジュール名。
        delay: 応答前に待つ秒数（並行要求の検証で使う）。

    Returns:
        `GET /echo` を持つ `ModuleRegistration`。
    """
    router = APIRouter()
    logger = logging.getLogger("test.echo")

    async def echo() -> dict[str, str | None]:
        """処理中に 2 行ログを出し、束縛中の相関 ID を返す。"""
        logger.info("処理の開始")
        if delay:
            await asyncio.sleep(delay)
        logger.info("処理の終了")
        return {"correlation_id": CorrelationContext.current()}

    router.add_api_route("/echo", echo, methods=["GET"])
    return make_registration(name=name, recorder=CallRecorder(), router=router)


def test_x1_one_id_is_issued_and_carried_by_every_log_line(
    client_factory: ClientFactory, recording_handler: _CorrelationRecordingHandler
) -> None:
    """X-1・US5 AC1・SC-008: ヘッダ無しで 1 つ発番され、処理中の全ログ行に載る。"""
    with client_factory(
        enabled_modules=["echo"], config=make_root_config(), registrations=(_echo_registration(),)
    ) as client:
        recording_handler.seen.clear()
        response = client.get("/echo")

    bound = response.json()["correlation_id"]
    assert CORRELATION_ID_PATTERN.match(bound) is not None
    assert len(recording_handler.seen) >= LOG_LINES_PER_REQUEST
    assert set(recording_handler.seen) == {bound}, "全ログ行が同じ ID になっていない"


def test_x2_and_x3_incoming_id_is_carried_over_and_echoed(client_factory: ClientFactory) -> None:
    """X-2・X-3・US5 AC2／AC3: 受信値を引き継ぎ、同名ヘッダで応答に返す。"""
    with client_factory(
        enabled_modules=["echo"], config=make_root_config(), registrations=(_echo_registration(),)
    ) as client:
        response = client.get("/echo", headers={DEFAULT_HEADER: "abc-123"})

    assert response.json()["correlation_id"] == "abc-123", "新規発番が起きている"
    assert response.headers[DEFAULT_HEADER] == "abc-123"


def test_x3_newly_issued_id_is_also_echoed(client_factory: ClientFactory) -> None:
    """X-3・FR-023: 新規発番の場合も応答に ID を返す。"""
    with client_factory(enabled_modules=[], config=make_root_config(), registrations=()) as client:
        response = client.get("/health")

    returned = response.headers[DEFAULT_HEADER]
    assert CORRELATION_ID_PATTERN.match(returned) is not None


def test_x4_concurrent_requests_do_not_mix_ids(client_factory: ClientFactory) -> None:
    """X-4・US5 AC4・SC-008: 並行する 2 要求で ID が混ざらない。"""
    with (
        client_factory(
            enabled_modules=["echo"],
            config=make_root_config(),
            registrations=(_echo_registration(delay=0.05),),
        ) as client,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = [
            pool.submit(client.get, "/echo", headers={DEFAULT_HEADER: value})
            for value in ("first-request", "second-request")
        ]
        bodies = [future.result() for future in futures]

    seen = {response.json()["correlation_id"] for response in bodies}
    assert seen == {"first-request", "second-request"}
    for response in bodies:
        assert response.headers[DEFAULT_HEADER] == response.json()["correlation_id"]


def test_x5_context_is_released_after_the_request(client_factory: ClientFactory) -> None:
    """X-5・US5 AC5・FR-021: 要求処理の後に `CorrelationContext.current()` が `None` に戻る。"""
    with client_factory(enabled_modules=[], config=make_root_config(), registrations=()) as client:
        assert client.get("/health").status_code == 200

    assert CorrelationContext.current() is None


def test_x6_header_name_comes_from_the_root_config(client_factory: ClientFactory) -> None:
    """X-6・US5 AC6・FR-022: `config.log.correlation_header` を差し替えるとヘッダ名が変わる。"""
    custom = "X-Trace-Id"
    with client_factory(
        enabled_modules=["echo"],
        config=make_root_config(correlation_header=custom),
        registrations=(_echo_registration(),),
    ) as client:
        response = client.get("/echo", headers={custom: "traced"})

    assert response.json()["correlation_id"] == "traced"
    assert response.headers[custom] == "traced"
    assert DEFAULT_HEADER not in response.headers


@pytest.mark.parametrize("raw", ["broken\rvalue", "x" * 129], ids=["control-character", "too-long"])
def test_x7_rejected_incoming_values_are_replaced(client_factory: ClientFactory, raw: str) -> None:
    """X-7・US5 AC7・FR-048: 制御文字入り・129 文字は引き継がず新規発番する。"""
    with client_factory(
        enabled_modules=["echo"], config=make_root_config(), registrations=(_echo_registration(),)
    ) as client:
        response = client.get("/echo", headers={DEFAULT_HEADER: raw})

    bound = response.json()["correlation_id"]
    assert bound != raw
    assert CORRELATION_ID_PATTERN.match(bound) is not None
    assert response.headers[DEFAULT_HEADER] == bound
    assert response.status_code == 200, "受信値をそのまま返すと h11 が応答を組み立てられない"


def test_x10_exactly_one_middleware_is_registered() -> None:
    """X-10・FR-019: ミドルウェアの登録数が 1。"""
    app = build_test_app(enabled_modules=[], config=make_root_config(), registrations=())

    assert len(app.user_middleware) == 1
