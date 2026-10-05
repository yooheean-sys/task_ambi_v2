"""F11 HTTP の口（6 パス・**10 操作**）。正本は `docs/design/phase6/openapi.yaml`。

受け入れ条件は
[contracts/trigger-http.md](../../../../specs/011-trigger/contracts/trigger-http.md) §1・§2・§6。

**`app` を組まず、`APIRouter` を単体の `FastAPI` に載せて `TestClient` で叩く**（settings・
orchestrator の先例）。`httpx` はルートの dev 依存で、`src` レイアウトのため grimp のグラフに
載らない（A-07）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import pytest
import yaml
from conftest import StubKickoffRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.kernel.config import find_repo_root
from trigger import TriggerConfig, TriggerDeps
from trigger.models import DEFAULT_TRIGGER_ID
from trigger.repository import DefinitionsRepository, HistoryRepository
from trigger.router import create_trigger_router
from trigger.service import TriggerService

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path


class HttpClient(Protocol):
    """router のテストが使う `TestClient` の面（**`apps/api/tests/_support` を使わない**）。

    starlette の `testclient` は型検査時に `httpx2` を import する作りで、`httpx2` を入れて
    いない本リポジトリでは応答の型が解決できない。実行時に返るのは `httpx.Response`
    （`httpx` はルートの dev グループにある）なので、使う面だけをここで型として固定する
    （`009` の先例と同型）。
    """

    def get(self, url: str, *, params: Mapping[str, str] | None = None) -> httpx.Response:
        """GET 要求を送る。"""
        ...

    def post(self, url: str, *, json: object | None = None) -> httpx.Response:
        """POST 要求を送る。"""
        ...

    def put(self, url: str, *, json: object | None = None) -> httpx.Response:
        """PUT 要求を送る。"""
        ...

    def patch(self, url: str, *, json: object | None = None) -> httpx.Response:
        """PATCH 要求を送る。"""
        ...

    def delete(self, url: str) -> httpx.Response:
        """DELETE 要求を送る。"""
        ...


@dataclass(frozen=True, slots=True)
class Wiring:
    """口を叩く手段（`client`）と、生成 openapi を読む相手（`app`）。"""

    client: HttpClient
    app: FastAPI


HTTP_OK = 200
HTTP_CREATED = 201
HTTP_ACCEPTED = 202
HTTP_NO_CONTENT = 204
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422

EXPECTED_PATHS = {
    "/triggers",
    "/triggers/{trigger_id}",
    "/triggers/{trigger_id}/run",
    "/history",
    "/watcher",
    "/hooks/file",
}
"""**6 パス**（`/triggers`・`/history`・`/watcher`・`/hooks/file` に散っている＝ prefix 1 本では
切り出せない。正本からは `x-mod: trigger` で選ぶ）。"""

EXPECTED_OPERATIONS = 10
"""**10 操作**（契約 §1）。"""

SPEC_BODY: dict[str, Any] = {
    "name": "新しい",
    "description": "",
    "type": "interval",
    "spec": "300",
    "condition": {"labels": ["role:executor"], "project_key": None},
    "enabled": True,
}


@pytest.fixture
def wiring(trigger_root: Path, downloads_dir: Path) -> Iterator[Wiring]:
    """常駐を起こした service の router だけを載せた `FastAPI` と `TestClient`。"""
    stub = StubKickoffRunner()
    service = TriggerService(
        deps=TriggerDeps(kickoff_runner=stub),
        config=TriggerConfig(min_interval_sec=1, misfire_grace_sec=1, poll_interval_sec=0.2),
        definitions=DefinitionsRepository(trigger_root),
        history=HistoryRepository(trigger_root),
        downloads_dir=downloads_dir,
    )
    service.start()
    app = FastAPI()
    app.include_router(create_trigger_router(service))
    try:
        with TestClient(app) as raw:
            http: HttpClient = raw
            yield Wiring(client=http, app=app)
    finally:
        service.stop()


@pytest.fixture
def client(wiring: Wiring) -> HttpClient:
    """口を叩く手段だけを見るテスト向けの近道。"""
    return wiring.client


def _body(response: httpx.Response) -> dict[str, Any]:
    """応答本文を写像として読む（HTTP 境界の narrow をこの 1 箇所に閉じる）。"""
    payload: Any = response.json()
    return dict(payload)


def _rows(response: httpx.Response) -> list[dict[str, Any]]:
    """応答本文を配列として読む。"""
    payload: Any = response.json()
    return [dict(item) for item in payload]


def _create(client: HttpClient, **overrides: object) -> dict[str, Any]:
    body = dict(SPEC_BODY)
    body.update(overrides)
    response = client.post("/triggers", json=body)
    assert response.status_code == HTTP_CREATED, response.text
    return _body(response)


# -- 1. GET /triggers -------------------------------------------------------------------


def test_list_triggers_returns_200_with_next_fire_at_and_last_result(
    client: HttpClient,
) -> None:
    """FR-063・US5 AC1: 一覧に **`next_fire_at`・`last_result`** が載る。"""
    response = client.get("/triggers")

    assert response.status_code == HTTP_OK
    rows = _rows(response)
    assert len(rows) == 1
    only = rows[0]
    assert only["trigger_id"] == DEFAULT_TRIGGER_ID
    assert only["is_default"] is True
    assert only["next_fire_at"] is not None, "登録済みなのに次回実行時刻が載らない"
    assert only["last_result"] is None, "まだ 1 度も実行していないのに直近結果がある"


# -- 2. POST /triggers ------------------------------------------------------------------


def test_create_trigger_returns_201(client: HttpClient) -> None:
    """追加は **201** ＋ `TriggerView`。`is_default` は**常に false**。"""
    created = _create(client)

    assert created["is_default"] is False
    assert created["trigger_id"]
    assert len(_rows(client.get("/triggers"))) == 2


def test_create_trigger_returns_422_for_a_violation(client: HttpClient) -> None:
    """validate 違反は **422** ＋ `Problem`（`kind` は `trigger_spec` の 1 語）。"""
    response = client.post("/triggers", json={**SPEC_BODY, "spec": "0"})

    assert response.status_code == HTTP_UNPROCESSABLE
    problems = _body(response)["problems"]
    assert problems
    assert {item["kind"] for item in problems} == {"trigger_spec"}
    assert problems[0]["where"] == "spec"
    assert problems[0]["reason"]


# -- 3. GET /triggers/{trigger_id} -------------------------------------------------------


def test_get_trigger_returns_200_and_404(client: HttpClient) -> None:
    """1 件取得は **200**、不明 id は **404** ＋ `Detail`。"""
    assert client.get(f"/triggers/{DEFAULT_TRIGGER_ID}").status_code == HTTP_OK

    response = client.get("/triggers/nope")
    assert response.status_code == HTTP_NOT_FOUND
    assert "detail" in _body(response)


# -- 4. PUT /triggers/{trigger_id} -------------------------------------------------------


def test_update_trigger_returns_200(client: HttpClient) -> None:
    """全量の編集は **200**（`TriggerSpec` は 5 欄すべて必須なので全量が届く）。"""
    created = _create(client)

    response = client.put(f"/triggers/{created['trigger_id']}", json={**SPEC_BODY, "spec": "600"})

    assert response.status_code == HTTP_OK
    assert _body(response)["spec"] == "600"


def test_update_trigger_returns_404_for_an_unknown_id(client: HttpClient) -> None:
    """不明 id は **404**。"""
    assert client.put("/triggers/nope", json=SPEC_BODY).status_code == HTTP_NOT_FOUND


def test_update_of_the_default_trigger_returns_422_for_a_forbidden_change(
    client: HttpClient,
) -> None:
    """SD-12: 既定トリガーは `spec`・`enabled` **以外**が現在値と違えば **422**。"""
    current = _body(client.get(f"/triggers/{DEFAULT_TRIGGER_ID}"))

    response = client.put(f"/triggers/{DEFAULT_TRIGGER_ID}", json={**current, "name": "勝手に改名"})

    assert response.status_code == HTTP_UNPROCESSABLE
    assert {item["where"] for item in _body(response)["problems"]} == {"name"}


def test_update_of_the_default_trigger_allows_the_interval(client: HttpClient) -> None:
    """SD-12: `spec` の変更は通る。"""
    current = _body(client.get(f"/triggers/{DEFAULT_TRIGGER_ID}"))

    response = client.put(f"/triggers/{DEFAULT_TRIGGER_ID}", json={**current, "spec": "900"})

    assert response.status_code == HTTP_OK
    assert _body(response)["spec"] == "900"


# -- 5. PATCH /triggers/{trigger_id} -----------------------------------------------------


def test_toggle_trigger_returns_200_and_404(client: HttpClient) -> None:
    """トグルは **200**、不明 id は **404**。**422 を持たない**。"""
    response = client.patch(f"/triggers/{DEFAULT_TRIGGER_ID}", json={"enabled": False})

    assert response.status_code == HTTP_OK
    assert _body(response)["enabled"] is False
    assert client.patch("/triggers/nope", json={"enabled": True}).status_code == HTTP_NOT_FOUND


def test_the_default_trigger_can_be_toggled_over_http(client: HttpClient) -> None:
    """FR-019: **既定トリガーもトグルできる**（削除だけができない）。"""
    off = client.patch(f"/triggers/{DEFAULT_TRIGGER_ID}", json={"enabled": False})
    assert off.status_code == HTTP_OK

    on = client.patch(f"/triggers/{DEFAULT_TRIGGER_ID}", json={"enabled": True})
    assert on.status_code == HTTP_OK
    assert _body(on)["enabled"] is True


# -- 6. DELETE /triggers/{trigger_id} ----------------------------------------------------


def test_delete_trigger_returns_204_with_no_body(client: HttpClient) -> None:
    """削除は **204**（**本文なし**）。"""
    created = _create(client)

    response = client.delete(f"/triggers/{created['trigger_id']}")

    assert response.status_code == HTTP_NO_CONTENT
    assert response.content == b"", "204 に本文が付いている"


def test_delete_trigger_returns_404_and_422(client: HttpClient) -> None:
    """不明 id は **404**、既定トリガーは **422**（V-7）。"""
    assert client.delete("/triggers/nope").status_code == HTTP_NOT_FOUND

    response = client.delete(f"/triggers/{DEFAULT_TRIGGER_ID}")
    assert response.status_code == HTTP_UNPROCESSABLE
    assert {item["where"] for item in _body(response)["problems"]} == {"trigger_id"}


# -- 7. POST /triggers/{trigger_id}/run --------------------------------------------------


def test_run_trigger_returns_202_with_a_run_id(client: HttpClient) -> None:
    """手動実行は **202** ＋ `RunAccepted`（`run_id`）。"""
    response = client.post(f"/triggers/{DEFAULT_TRIGGER_ID}/run")

    assert response.status_code == HTTP_ACCEPTED
    assert _body(response)["run_id"]


def test_run_trigger_returns_404_for_an_unknown_id(client: HttpClient) -> None:
    """不明 id は **404**。"""
    assert client.post("/triggers/nope/run").status_code == HTTP_NOT_FOUND


# -- 8. GET /history ---------------------------------------------------------------------


def test_list_history_of_the_current_month_is_200(client: HttpClient) -> None:
    """`month` 省略は**当月**（まだ 1 行も無ければ空配列で 200）。"""
    response = client.get("/history")

    assert response.status_code == HTTP_OK
    assert _rows(response) == []


def test_list_history_of_a_month_with_no_file_is_an_empty_list_with_200(
    client: HttpClient,
) -> None:
    """SD-13・F-09: **まだ 1 度も実行していない月は空配列で 200**（404 にしない）。"""
    response = client.get("/history", params={"month": "209912"})

    assert response.status_code == HTTP_OK
    assert _rows(response) == []


def test_a_malformed_month_is_rejected_by_the_pattern(client: HttpClient) -> None:
    """書式違反は FastAPI が `pattern` から **422** を返す（trigger の validate ではない）。"""
    assert client.get("/history", params={"month": "abc"}).status_code == HTTP_UNPROCESSABLE


# -- 9. GET /watcher ---------------------------------------------------------------------


def test_get_watcher_returns_the_four_fields(client: HttpClient) -> None:
    """`WatcherView` の **4 欄**（`scheduler_running` を含む＝ DIFF-12）。"""
    response = client.get("/watcher")

    assert response.status_code == HTTP_OK
    body = _body(response)
    assert set(body) == {"scheduler_running", "watchers", "next_fire_at", "running"}
    assert body["scheduler_running"] is True
    assert body["running"] is False


# -- 10. POST /hooks/file ----------------------------------------------------------------


def test_post_file_hook_returns_202_with_matched_trigger_ids(
    client: HttpClient, downloads_dir: Path
) -> None:
    """退避経路は **202** ＋ `matched_trigger_ids`。"""
    created = _create(client, type="file", spec=".")
    target = downloads_dir / "drop.bin"
    target.write_bytes(b"payload")

    response = client.post("/hooks/file", json={"path": str(target)})

    assert response.status_code == HTTP_ACCEPTED
    body = _body(response)
    assert body["accepted"] is True
    assert body["matched_trigger_ids"] == [created["trigger_id"]]


def test_post_file_hook_returns_422_outside_the_watched_folders(
    client: HttpClient, tmp_path: Path
) -> None:
    """監視対象の外は **422**（`where="path"`）。"""
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"payload")

    response = client.post("/hooks/file", json={"path": str(outside)})

    assert response.status_code == HTTP_UNPROCESSABLE
    problems = _body(response)["problems"]
    assert {item["where"] for item in problems} == {"path"}
    assert "監視対象の外" in problems[0]["reason"]


# -- T052 生成 openapi と正本の照合（SC-012・SD-18・SD-22） ------------------------------


def _design() -> dict[str, Any]:
    path = find_repo_root() / "docs" / "design" / "phase6" / "openapi.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _trigger_paths(design: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """正本から trigger の節を **`x-mod: trigger`** で選ぶ。

    ⚠ 6 パスは `/triggers`・`/history`・`/watcher`・`/hooks/file` に散っており、`009` の先例
    （`path.startswith("/kickoffs")`）のような **prefix 1 本では切り出せない**。
    """
    selected: dict[str, dict[str, Any]] = {}
    paths: dict[str, dict[str, Any]] = design["paths"]
    for path, item in paths.items():
        operations: dict[str, Any] = {}
        for method, raw in item.items():
            if not isinstance(raw, dict):
                continue
            body: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]  # yaml.safe_load は Unknown を返す（境界の narrow はここ 1 箇所）
            if body.get("x-mod") == "trigger":
                operations[method] = body
        if operations:
            selected[path] = operations
    return selected


def test_the_generated_openapi_matches_the_design(wiring: Wiring) -> None:
    """SC-012: **パス 6・操作 10** が一致し、応答コードは**部分集合**（設計 ⊆ 生成）。"""
    design = _trigger_paths(_design())
    generated: dict[str, Any] = wiring.app.openapi()

    assert set(design) == EXPECTED_PATHS
    assert set(generated["paths"]) == EXPECTED_PATHS, "パスの集合が等号でない"
    assert sum(len(operations) for operations in design.values()) == EXPECTED_OPERATIONS

    for path, operations in sorted(design.items()):
        for method, expected in sorted(operations.items()):
            actual = generated["paths"][path][method]
            assert actual["operationId"] == expected["operationId"], f"{method} {path}"
            assert set(expected["responses"]) <= set(actual["responses"]), (
                f"{method} {path}: 設計の応答コードが生成側に無い"
                f" / 設計 {sorted(expected['responses'])} ⊄ 生成 {sorted(actual['responses'])}"
            )


def test_the_history_404_is_declared_even_though_it_is_never_returned(
    wiring: Wiring,
) -> None:
    """SD-22: **`GET /history` の 404 が生成側に宣言されている**。

    ⚠ 部分集合は逆向きに効かない —— 正本の `listHistory` は `{200, 404}` なので、生成側が 404 を
    持たないと `{200,404} <= {200,422}` が偽になり **SC-012 が落ちる**。実装は 404 を返さない
    （宣言だけの口は**これ 1 つに限る**）。
    """
    generated: dict[str, Any] = wiring.app.openapi()

    assert "404" in generated["paths"]["/history"]["get"]["responses"]


def test_the_union_of_the_response_codes() -> None:
    """FR-061: 応答コードの和は `{200, 201, 202, 204, 404, 422}`（生成側は 422 を足しうる）。"""
    design = _trigger_paths(_design())

    codes = {
        code
        for operations in design.values()
        for op in operations.values()
        for code in op["responses"]
    }

    assert codes == {"200", "201", "202", "204", "404", "422"}
