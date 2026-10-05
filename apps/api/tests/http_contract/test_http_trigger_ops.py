"""trigger の成功系 10 対。

`/triggers`・`/triggers/{trigger_id}`・`/run`・`/history`・`/watcher`・`/hooks/file` を担う。

領域 B の実物（一時ディレクトリ）と `with TestClient(app)` の lifespan で踏む。
`build()` が `triggers.yaml` の**既定トリガー 1 件だけの雛形**を生成するので、テストは雛形を
自前で書かない。

発火・実行の業務的な帰結は**検証しない**（層 3＝ `012` の領分＝ FR-009）。
既定トリガーの業務規則（FN-30／FN-33）も層 1 の領分。
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
HISTORY_PATH = "/history"
WATCHER_PATH = "/watcher"
FILE_HOOK_PATH = "/hooks/file"


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    """正本（読むだけ）。"""
    return dict(load_document(openapi_path()))


def _interval_spec(name: str, *, spec: str = "300", enabled: bool = True) -> dict[str, Any]:
    """`interval` 種別の `TriggerSpec`（正本の required 5 欄をすべて埋める）。"""
    return {
        "name": name,
        "description": "層 5 の HTTP 契約テストが作る",
        "type": "interval",
        "spec": spec,
        "condition": {"labels": ["role:executor"], "project_key": None},
        "enabled": enabled,
    }


def _file_spec(name: str, *, watch_dir: str = ".") -> dict[str, Any]:
    """`file` 種別の `TriggerSpec`（`spec` は `DOWNLOADS_DIR` 相対の監視ディレクトリ）。"""
    return {
        "name": name,
        "description": "層 5 の HTTP 契約テストが作る",
        "type": "file",
        "spec": watch_dir,
        "condition": {"labels": ["role:executor"], "project_key": None},
        "enabled": True,
    }


def _create(client: HttpClient, body: dict[str, Any]) -> dict[str, Any]:
    """トリガーを 1 件作って応答本文を返す（前提の準備。ここでは適合を主張しない）。"""
    response = client.post(TRIGGERS_PATH, json=body)
    if response.status_code != 201:
        pytest.fail(f"前提のトリガー作成に失敗した: {response.status_code} {response.text[:300]}")
    created: dict[str, Any] = response.json()
    return created


@pytest.mark.http_op("GET", "/triggers", "200")
def test_list_triggers_returns_the_declared_views(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """GET /triggers (listTriggers) の **200** —— 本文が `TriggerView[]` に適合する。

    雛形が生成した既定トリガー 1 件が並ぶ（件数の業務規則は層 1 の領分）。
    """
    with client_of(trigger_app) as client:
        response = client.get(TRIGGERS_PATH)

    assert_conforms(doc, path=TRIGGERS_PATH, method="GET", status="200", response=response)
    body: list[dict[str, Any]] = response.json()
    assert body, "雛形の既定トリガーが 1 件も返らない"


@pytest.mark.http_op("POST", "/triggers", "201")
def test_create_trigger_returns_the_declared_view(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """POST /triggers (createTrigger) の **201** —— 本文が `TriggerView` に適合する。"""
    with client_of(trigger_app) as client:
        response = client.post(TRIGGERS_PATH, json=_interval_spec("created"))

    assert_conforms(doc, path=TRIGGERS_PATH, method="POST", status="201", response=response)


@pytest.mark.http_op("GET", "/triggers/{trigger_id}", "200")
def test_get_trigger_returns_the_declared_view(trigger_app: FastAPI, doc: dict[str, Any]) -> None:
    """GET /triggers/{trigger_id} (getTrigger) の **200** —— 本文が `TriggerView` に適合する。"""
    with client_of(trigger_app) as client:
        created = _create(client, _interval_spec("to-read"))
        response = client.get(f"/triggers/{created['trigger_id']}")

    assert_conforms(doc, path=TRIGGER_TEMPLATE, method="GET", status="200", response=response)


@pytest.mark.http_op("PUT", "/triggers/{trigger_id}", "200")
def test_update_trigger_returns_the_declared_view(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """PUT /triggers/{trigger_id} (updateTrigger) の **200** —— 本文が `TriggerView` に適合する。

    既定トリガーではなく自分で作ったトリガーを対象にする（既定の業務規則は層 1 の領分）。
    """
    with client_of(trigger_app) as client:
        created = _create(client, _interval_spec("to-update"))
        response = client.put(
            f"/triggers/{created['trigger_id']}", json=_interval_spec("updated", spec="600")
        )

    assert_conforms(doc, path=TRIGGER_TEMPLATE, method="PUT", status="200", response=response)


@pytest.mark.http_op("PATCH", "/triggers/{trigger_id}", "200")
def test_toggle_trigger_returns_the_declared_view(
    trigger_app: FastAPI, doc: dict[str, Any]
) -> None:
    """PATCH /triggers/{trigger_id} (toggleTrigger) の **200**。

    本文が `TriggerView` に適合する。
    """
    with client_of(trigger_app) as client:
        created = _create(client, _interval_spec("to-toggle"))
        response = client.patch(f"/triggers/{created['trigger_id']}", json={"enabled": False})

    assert_conforms(doc, path=TRIGGER_TEMPLATE, method="PATCH", status="200", response=response)


@pytest.mark.http_op("DELETE", "/triggers/{trigger_id}", "204")
def test_delete_trigger_returns_no_content(trigger_app: FastAPI, doc: dict[str, Any]) -> None:
    """DELETE /triggers/{trigger_id} (deleteTrigger) の **204** —— 本文が空である（FR-024）。

    削除するのは**既定でない**トリガー（既定は削除不可＝ 422 で、それは errors 側が踏む）。
    """
    with client_of(trigger_app) as client:
        created = _create(client, _interval_spec("to-delete"))
        response = client.delete(f"/triggers/{created['trigger_id']}")

    assert_conforms(doc, path=TRIGGER_TEMPLATE, method="DELETE", status="204", response=response)


@pytest.mark.http_op("POST", "/triggers/{trigger_id}/run", "202")
def test_run_trigger_is_accepted(trigger_app: FastAPI, doc: dict[str, Any]) -> None:
    """POST /triggers/{trigger_id}/run (runTrigger) の **202** —— 本文が `RunAccepted` に適合する。

    発火の業務的な帰結は**検証しない**（層 3 の領分）。
    """
    with client_of(trigger_app) as client:
        created = _create(client, _interval_spec("to-run"))
        response = client.post(f"/triggers/{created['trigger_id']}/run")

    assert_conforms(doc, path=RUN_TEMPLATE, method="POST", status="202", response=response)


@pytest.mark.http_op("GET", "/history", "200")
def test_list_history_returns_the_declared_rows(trigger_app: FastAPI, doc: dict[str, Any]) -> None:
    """GET /history (listHistory) の **200** —— 本文が `HistoryRow[]` に適合する。

    **(a) 実行履歴が 1 行も無い状態（`[]`）** と **(b) 手動実行の後**の両方で見る。
    ⚠ `listHistory` の **404 は台帳 U-4**（実装が返さない）なので宣言しない。
    `month` の入力検証は層 1 の領分。
    """
    with client_of(trigger_app) as client:
        empty = client.get(HISTORY_PATH)
        assert_conforms(doc, path=HISTORY_PATH, method="GET", status="200", response=empty)
        assert empty.json() == [], f"実行前の履歴が空でない: {empty.text[:200]}"

        created = _create(client, _interval_spec("to-record"))
        client.post(f"/triggers/{created['trigger_id']}/run")
        after = client.get(HISTORY_PATH)

    assert_conforms(doc, path=HISTORY_PATH, method="GET", status="200", response=after)
    assert after.json(), "手動実行の後も履歴が 1 行も無い"


@pytest.mark.http_op("GET", "/watcher", "200")
def test_watcher_returns_the_declared_view(trigger_app: FastAPI, doc: dict[str, Any]) -> None:
    """GET /watcher (getWatcher) の **200** —— 本文が `WatcherView` に適合する。

    `with TestClient(app)` の**内側**で叩く（外だと常駐が上がっていない）。
    4 フィールドの業務的な意味は検証しない（層 1 の領分）。
    """
    with client_of(trigger_app) as client:
        response = client.get(WATCHER_PATH)

    assert_conforms(doc, path=WATCHER_PATH, method="GET", status="200", response=response)


@pytest.mark.http_op("POST", "/hooks/file", "202")
def test_post_file_hook_is_accepted(
    trigger_app: FastAPI, downloads_dir: Path, doc: dict[str, Any]
) -> None:
    """POST /hooks/file (postFileHook) の **202** —— 本文が `FileHookAccepted` に適合する。

    `type=file`・`spec="."` のトリガーを作り、`DOWNLOADS_DIR` に投下したファイルのパスを送る。
    """
    dropped = downloads_dir / "dropped.md"
    dropped.write_text("投下ファイル\n", encoding="utf-8")
    with client_of(trigger_app) as client:
        _create(client, _file_spec("watches-downloads"))
        response = client.post(FILE_HOOK_PATH, json={"path": str(dropped), "source": "hook"})

    assert_conforms(doc, path=FILE_HOOK_PATH, method="POST", status="202", response=response)
