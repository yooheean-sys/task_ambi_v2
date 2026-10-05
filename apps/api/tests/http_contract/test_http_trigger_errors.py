"""trigger の否定側 9 対（404 × 5・422 × 4）。

**業務 validate 経路の 422 だけ**を検証する。⚠ 口の型違反による FastAPI 内蔵 422
（`{"detail":[…]}`）に適合検証を置かない（正本の `Problem` と形が違う＝ FR-026・B-03）。

⚠ `listHistory` の 404 は**台帳 U-4**（実装が返さない）なのでこの表に入れない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.openapi_source import load_document, openapi_path
from http_asserts import assert_conforms
from http_wiring import client_of

if TYPE_CHECKING:
    from pathlib import Path

    from _support.composition import HttpClient
    from fastapi import FastAPI

TRIGGERS_PATH = "/triggers"
TRIGGER_TEMPLATE = "/triggers/{trigger_id}"
RUN_TEMPLATE = "/triggers/{trigger_id}/run"
FILE_HOOK_PATH = "/hooks/file"
UNKNOWN_TRIGGER_ID = "居ない"


def _spec(name: str, *, kind: str = "interval", spec: str = "300") -> dict[str, Any]:
    """`TriggerSpec`（正本の required 5 欄をすべて埋める）。"""
    return {
        "name": name,
        "description": "層 5 の HTTP 契約テストが作る",
        "type": kind,
        "spec": spec,
        "condition": {"labels": ["role:executor"], "project_key": None},
        "enabled": True,
    }


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    """正本（読むだけ）。"""
    return dict(load_document(openapi_path()))


def _default_trigger(client: HttpClient) -> dict[str, Any]:
    """雛形が生成した既定トリガー（削除不可・名前変更不可）。"""
    for row in client.get(TRIGGERS_PATH).json():
        if row["is_default"]:
            found: dict[str, Any] = row
            return found
    pytest.fail("雛形の既定トリガーが見つからない（422 の前提が作れない）")


NOT_FOUND_CASES = (
    pytest.param(
        "GET",
        TRIGGER_TEMPLATE,
        f"/triggers/{UNKNOWN_TRIGGER_ID}",
        None,
        marks=pytest.mark.http_op("GET", "/triggers/{trigger_id}", "404"),
        id="getTrigger",
    ),
    pytest.param(
        "PUT",
        TRIGGER_TEMPLATE,
        f"/triggers/{UNKNOWN_TRIGGER_ID}",
        _spec("なんでもよい"),
        marks=pytest.mark.http_op("PUT", "/triggers/{trigger_id}", "404"),
        id="updateTrigger",
    ),
    pytest.param(
        "PATCH",
        TRIGGER_TEMPLATE,
        f"/triggers/{UNKNOWN_TRIGGER_ID}",
        {"enabled": False},
        marks=pytest.mark.http_op("PATCH", "/triggers/{trigger_id}", "404"),
        id="toggleTrigger",
    ),
    pytest.param(
        "DELETE",
        TRIGGER_TEMPLATE,
        f"/triggers/{UNKNOWN_TRIGGER_ID}",
        None,
        marks=pytest.mark.http_op("DELETE", "/triggers/{trigger_id}", "404"),
        id="deleteTrigger",
    ),
    pytest.param(
        "POST",
        RUN_TEMPLATE,
        f"/triggers/{UNKNOWN_TRIGGER_ID}/run",
        None,
        marks=pytest.mark.http_op("POST", "/triggers/{trigger_id}/run", "404"),
        id="runTrigger",
    ),
)


@pytest.mark.parametrize(("method", "template", "url", "body"), NOT_FOUND_CASES)
def test_unknown_trigger_id_is_reported_as_not_found(
    trigger_app: FastAPI,
    doc: dict[str, Any],
    method: str,
    template: str,
    url: str,
    body: dict[str, Any] | None,
) -> None:
    """未知の `trigger_id` に対する **404** —— 本文が `Detail` に適合する。

    対象は `/triggers/{trigger_id}` の 4 操作 ＋ `/triggers/{trigger_id}/run`（各 1 対）。
    """
    with client_of(trigger_app) as client:
        response = client.request(method, url, json=body)

    assert_conforms(doc, path=template, method=method, status="404", response=response)


@pytest.mark.http_op("POST", "/triggers", "422")
def test_create_trigger_rejects_an_interval_below_the_lower_bound(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """POST /triggers (createTrigger) の **422** —— 本文が `Problem` に適合する（下限割れ）。"""
    with client_of(trigger_app) as client:
        response = client.post(TRIGGERS_PATH, json=_spec("下限割れ", spec="0"))

    assert_conforms(doc, path=TRIGGERS_PATH, method="POST", status="422", response=response)


@pytest.mark.http_op("PUT", "/triggers/{trigger_id}", "422")
def test_update_trigger_rejects_renaming_the_default_trigger(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """PUT /triggers/{trigger_id} (updateTrigger) の **422**。

    既定トリガーの名前は変えられない。
    """
    with client_of(trigger_app) as client:
        default = _default_trigger(client)
        body = _spec("別名にする")
        body["type"] = default["type"]
        body["spec"] = default["spec"]
        response = client.put(f"/triggers/{default['trigger_id']}", json=body)

    assert_conforms(doc, path=TRIGGER_TEMPLATE, method="PUT", status="422", response=response)


@pytest.mark.http_op("DELETE", "/triggers/{trigger_id}", "422")
def test_delete_trigger_rejects_removing_the_default_trigger(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """DELETE /triggers/{trigger_id} (deleteTrigger) の **422** —— 既定トリガーは削除できない。"""
    with client_of(trigger_app) as client:
        default = _default_trigger(client)
        response = client.delete(f"/triggers/{default['trigger_id']}")

    assert_conforms(doc, path=TRIGGER_TEMPLATE, method="DELETE", status="422", response=response)


@pytest.mark.http_op("POST", "/hooks/file", "422")
def test_post_file_hook_rejects_a_path_outside_the_watched_directory(
    trigger_app: FastAPI, tmp_path: Path, doc: dict[str, Any]
) -> None:
    """POST /hooks/file (postFileHook) の **422** —— 監視対象の外のパスは受理しない。

    ⚠ 本文の型違反（`path` を落とす等）による**内蔵 422** はここで検証しない（FR-026）。
    """
    outside = tmp_path / "outside.md"
    outside.write_text("監視対象の外\n", encoding="utf-8")
    with client_of(trigger_app) as client:
        response = client.post(FILE_HOOK_PATH, json={"path": str(outside), "source": "hook"})

    assert_conforms(doc, path=FILE_HOOK_PATH, method="POST", status="422", response=response)
