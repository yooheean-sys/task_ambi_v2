"""`GET /health`（`getHealth`）の 200 —— 提供実装 `apps/api`（1 パス・1 操作・1 対）。

器の正しさを 1 対で実証する（実 composition root ＋ 相関 ID ミドルウェア ＋ 正本駆動の
schema 検査 ＋ 宣言 → 被覆検査の一巡）。

生成 `openapi.json` の schema 突合は**やらない**（層 2 の `test_smoke_health_contract.py` H-4 の
領分＝ FR-007）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.openapi_source import load_document, openapi_path
from http_asserts import assert_conforms
from http_wiring import client_of

if TYPE_CHECKING:
    from _support.fake_modules import CallRecorder
    from fastapi import FastAPI

HEALTH_PATH = "/health"


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    """正本（読むだけ）。"""
    return dict(load_document(openapi_path()))


@pytest.mark.http_op("GET", "/health", "200")
def test_health_returns_the_declared_liveness_view(
    health_topology_app: FastAPI, topology_recorder: CallRecorder, doc: dict[str, Any]
) -> None:
    """GET /health (getHealth) の **200** —— 本文が正本の `Health` に適合する。

    `modules` が**実際に組み立てられた MOD 名（依存順）**と一致することも見る（US2 AC4）。
    """
    with client_of(health_topology_app) as client:
        response = client.get(HEALTH_PATH)

    assert_conforms(doc, path=HEALTH_PATH, method="GET", status="200", response=response)
    body: dict[str, Any] = response.json()
    assert body["modules"] == topology_recorder.builds, (
        "/health の modules が実際に組み立てられた MOD 名（依存順）と一致しない\n"
        f"  応答: {body['modules']}\n"
        f"  組み立ての記録: {topology_recorder.builds}"
    )


def test_health_returns_an_empty_module_list_when_nothing_is_enabled(
    health_minimal_app: FastAPI, doc: dict[str, Any]
) -> None:
    """GET /health (getHealth) の 200 —— 最小形（有効 MOD 0 件）でも宣言どおりに返る。

    対の所有は上の 1 本（規約 §5）なので、ここでは `http_op` を宣言しない。
    """
    with client_of(health_minimal_app) as client:
        response = client.get(HEALTH_PATH)

    assert_conforms(doc, path=HEALTH_PATH, method="GET", status="200", response=response)
    body: dict[str, Any] = response.json()
    assert body["modules"] == [], f"有効 MOD 0 件なのに modules が空でない: {body['modules']}"
