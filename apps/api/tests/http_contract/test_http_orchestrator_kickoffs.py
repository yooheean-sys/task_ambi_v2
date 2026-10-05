"""orchestrator の 3 パス・3 操作・5 対。

`/kickoffs`・`/kickoffs/current`・`/kickoffs/{kickoff_id}` を担う。

9 契約スタブで組んだ実配線に対し、**202 の受理**・200 の照会・404・**409（single-flight）**を踏む。

周回の業務的な帰結（起票・提案・停止条件）は**検証しない**（層 3＝ `012` の領分＝ FR-009）。
後始末は上限つきポーリング（`drain_kickoff`）で行い、**ワーカースレッドを join しない**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.openapi_source import load_document, openapi_path
from http_asserts import assert_conforms
from http_wiring import DRAIN_TIMEOUT_SECONDS, client_of, drain_kickoff

if TYPE_CHECKING:
    from fastapi import FastAPI
    from http_wiring import GatedBoardReader

KICKOFFS_PATH = "/kickoffs"
CURRENT_PATH = "/kickoffs/current"
KICKOFF_TEMPLATE = "/kickoffs/{kickoff_id}"
UNKNOWN_KICKOFF_ID = "居ない"


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    """正本（読むだけ）。"""
    return dict(load_document(openapi_path()))


@pytest.mark.http_op("GET", "/kickoffs/current", "200")
def test_current_kickoff_returns_the_declared_view_when_idle(
    kickoffs_app: FastAPI, doc: dict[str, Any]
) -> None:
    """GET /kickoffs/current (getCurrentKickoff) の **200** —— 本文が `CurrentKickoff` に適合する。

    周回していない状態でも **200**（404 ではない）が返り、`running` が false であること。
    正本は `getCurrentKickoff` に **200 しか宣言していない**ので、「404 を返さない」ことも
    あわせて表明する（FR-025・spec Edge Cases）。
    """
    with client_of(kickoffs_app) as client:
        response = client.get(CURRENT_PATH)

    assert response.status_code != 404, (  # noqa: PLR2004  # 正本が宣言しない status の否定
        "周回していない状態で 404 が返った（正本は 200 しか宣言していない）"
    )
    assert_conforms(doc, path=CURRENT_PATH, method="GET", status="200", response=response)
    body: dict[str, Any] = response.json()
    assert body["running"] is False, f"周回していないのに running が真: {body}"


@pytest.mark.http_op("POST", "/kickoffs", "202")
def test_create_kickoff_is_accepted(kickoffs_app: FastAPI, doc: dict[str, Any]) -> None:
    """POST /kickoffs (createKickoff) の **202** —— 本文が `KickoffAccepted` に適合する。

    本文なしで叩く（`requestBody` は `required: false`）。受理後は上限つきポーリングで
    排出まで待つ（ワーカースレッドを join しない＝ FR-043）。
    """
    with client_of(kickoffs_app) as client:
        response = client.post(KICKOFFS_PATH)
        try:
            assert_conforms(doc, path=KICKOFFS_PATH, method="POST", status="202", response=response)
        finally:
            drain_kickoff(client)


@pytest.mark.http_op("GET", "/kickoffs/{kickoff_id}", "200")
def test_get_kickoff_returns_the_declared_record(
    kickoffs_app: FastAPI, doc: dict[str, Any]
) -> None:
    """GET /kickoffs/{kickoff_id} (getKickoff) の **200** —— 本文が `KickoffRecordView` に適合する。

    受理応答が返した `kickoff_id` を使う（パスの宣言は**テンプレートのまま**）。
    """
    with client_of(kickoffs_app) as client:
        accepted = client.post(KICKOFFS_PATH)
        kickoff_id: str = accepted.json()["kickoff_id"]
        try:
            drain_kickoff(client)
            response = client.get(f"/kickoffs/{kickoff_id}")
        finally:
            drain_kickoff(client)

    assert_conforms(doc, path=KICKOFF_TEMPLATE, method="GET", status="200", response=response)


@pytest.mark.http_op("GET", "/kickoffs/{kickoff_id}", "404")
def test_get_kickoff_reports_an_unknown_id_as_not_found(
    kickoffs_app: FastAPI, doc: dict[str, Any]
) -> None:
    """GET /kickoffs/{kickoff_id} (getKickoff) の **404** —— 本文が `Detail` に適合する。"""
    with client_of(kickoffs_app) as client:
        response = client.get(f"/kickoffs/{UNKNOWN_KICKOFF_ID}")

    assert_conforms(doc, path=KICKOFF_TEMPLATE, method="GET", status="404", response=response)


@pytest.mark.http_op("POST", "/kickoffs", "409")
def test_create_kickoff_rejects_a_second_run_while_one_is_in_flight(
    gated_kickoffs_app: FastAPI, gated_board_reader: GatedBoardReader, doc: dict[str, Any]
) -> None:
    """POST /kickoffs (createKickoff) の **409** —— 本文が `KickoffBusyDetail` に適合する。

    `BoardReader.list_working` を 1 回だけ塞いで in-flight を固定し、2 本目を叩く。
    ⚠ `MemberResolver.is_human` を塞ぐ形では踏めない（`build()` が組み立て時に呼ぶだけで
    周回に居ない＝ HD-25）。表明したら `try/finally` で**必ず解放**して排出まで待つ。
    """
    with client_of(gated_kickoffs_app) as client:
        first = client.post(KICKOFFS_PATH)
        assert first.status_code == 202, f"1 本目が受理されなかった: {first.status_code}"  # noqa: PLR2004
        try:
            entered = gated_board_reader.entered.wait(timeout=DRAIN_TIMEOUT_SECONDS)
            assert entered, "1 本目が周回（list_working）に入らなかった —— 409 を踏めない"
            response = client.post(KICKOFFS_PATH)
            assert_conforms(doc, path=KICKOFFS_PATH, method="POST", status="409", response=response)
        finally:
            gated_board_reader.release.set()
            drain_kickoff(client)
