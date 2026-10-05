"""settings の書込 22 操作（200 × 13・201 × 2・204 × 4・422 × 3）と保存ヘッダ。

**既知差分 6 件は `xfail(strict=True)`**（`reason` は `http_ledger.py` から引く）。

⚠ **行き止まり 3 操作**（`createCommand` 201・`deleteAgent` 204・`deleteCommand` 204）は
相互ロックで **422 にしかならない**（`010` B-40／B-49／B-51）。ここでは 422 を宣言し、
201・204 側は台帳 `UNREACHED` の U-1〜U-3 が理由を持つ。**ロックを塞ぐ手当てはしない**
（契約の改訂を要し FR-037 の外）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.openapi_source import load_document, openapi_path
from http_asserts import assert_conforms, assert_no_save_headers, assert_save_headers
from http_calls import SETTINGS_WRITE_CALLS, HttpCall, call_of
from http_ledger import divergence_of
from http_stores import area_bytes
from http_wiring import client_of

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

DEAD_END_OPERATIONS = ("createCommand_422", "deleteAgent_422", "deleteCommand_422")
"""口から成功系へ到達できない 3 操作（422 の表明が所有する対）。"""


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    """正本（読むだけ）。"""
    return dict(load_document(openapi_path()))


def _xfail(method: str, path: str, status: str) -> pytest.MarkDecorator:
    """既知差分の対に `xfail(strict=True)` を付ける（`reason` は台帳から引く）。"""
    row = divergence_of(method, path, status)
    if row is None:
        message = f"KNOWN_DIVERGENCES に無い対に xfail を付けようとした: {method} {path} {status}"
        raise KeyError(message)
    return pytest.mark.xfail(strict=True, reason=row.xfail_reason)


def _send(app: FastAPI, call: HttpCall) -> Any:  # noqa: ANN401  # httpx.Response（型は面のみ）
    """recipe のとおりに 1 回叩く。"""
    with client_of(app) as client:
        return client.request(call.method, call.url, json=call.body)


SUCCESS_CASES = (
    pytest.param(
        "putGlobalAgent",
        marks=[
            pytest.mark.http_op("PUT", "/settings/agents/global/{persona_key}", "200"),
            _xfail("PUT", "/settings/agents/global/{persona_key}", "200"),
        ],
        id="putGlobalAgent",
    ),
    pytest.param(
        "putAgent",
        marks=[
            pytest.mark.http_op("PUT", "/settings/agents/{project_key}/{persona_key}", "200"),
            _xfail("PUT", "/settings/agents/{project_key}/{persona_key}", "200"),
        ],
        id="putAgent",
    ),
    pytest.param(
        "updateCommand",
        marks=pytest.mark.http_op("PUT", "/settings/commands/{name}", "200"),
        id="updateCommand",
    ),
    pytest.param(
        "putHooks",
        marks=pytest.mark.http_op("PUT", "/settings/hooks/{project_key}", "200"),
        id="putHooks",
    ),
    pytest.param(
        "putHumans",
        marks=pytest.mark.http_op("PUT", "/settings/humans", "200"),
        id="putHumans",
    ),
    pytest.param(
        "putGlobalLimits",
        marks=pytest.mark.http_op("PUT", "/settings/limits", "200"),
        id="putGlobalLimits",
    ),
    pytest.param(
        "putProjectLimits",
        marks=pytest.mark.http_op("PUT", "/settings/limits/{project_key}", "200"),
        id="putProjectLimits",
    ),
    pytest.param(
        "updateLlmProfile",
        marks=[
            pytest.mark.http_op("PUT", "/settings/llm-profiles/{name}", "200"),
            _xfail("PUT", "/settings/llm-profiles/{name}", "200"),
        ],
        id="updateLlmProfile",
    ),
    pytest.param(
        "putPolicy",
        marks=pytest.mark.http_op("PUT", "/settings/policies/{project_key}", "200"),
        id="putPolicy",
    ),
    pytest.param(
        "updateProject",
        marks=pytest.mark.http_op("PUT", "/settings/projects/{project_key}", "200"),
        id="updateProject",
    ),
    pytest.param(
        "putProjectRules",
        marks=pytest.mark.http_op("PUT", "/settings/rules/projects/{project_key}", "200"),
        id="putProjectRules",
    ),
    pytest.param(
        "putGlobalTasks",
        marks=[
            pytest.mark.http_op("PUT", "/settings/tasks/global", "200"),
            _xfail("PUT", "/settings/tasks/global", "200"),
        ],
        id="putGlobalTasks",
    ),
    pytest.param(
        "putProjectTasks",
        marks=[
            pytest.mark.http_op("PUT", "/settings/tasks/{project_key}", "200"),
            _xfail("PUT", "/settings/tasks/{project_key}", "200"),
        ],
        id="putProjectTasks",
    ),
    pytest.param(
        "createProject",
        marks=pytest.mark.http_op("POST", "/settings/projects", "201"),
        id="createProject",
    ),
    pytest.param(
        "createLlmProfile",
        marks=[
            pytest.mark.http_op("POST", "/settings/llm-profiles", "201"),
            _xfail("POST", "/settings/llm-profiles", "201"),
        ],
        id="createLlmProfile",
    ),
)

DELETE_CASES = (
    pytest.param(
        "deleteProject",
        marks=pytest.mark.http_op("DELETE", "/settings/projects/{project_key}", "204"),
        id="deleteProject",
    ),
    pytest.param(
        "deleteProjectTasks",
        marks=pytest.mark.http_op("DELETE", "/settings/tasks/{project_key}", "204"),
        id="deleteProjectTasks",
    ),
    pytest.param(
        "deleteLlmProfile",
        marks=pytest.mark.http_op("DELETE", "/settings/llm-profiles/{name}", "204"),
        id="deleteLlmProfile",
    ),
    pytest.param(
        "deleteProjectLimits",
        marks=pytest.mark.http_op("DELETE", "/settings/limits/{project_key}", "204"),
        id="deleteProjectLimits",
    ),
)

DEAD_END_CASES = (
    pytest.param(
        "createCommand_422",
        marks=pytest.mark.http_op("POST", "/settings/commands", "422"),
        id="createCommand",
    ),
    pytest.param(
        "deleteAgent_422",
        marks=pytest.mark.http_op("DELETE", "/settings/agents/{project_key}/{persona_key}", "422"),
        id="deleteAgent",
    ),
    pytest.param(
        "deleteCommand_422",
        marks=pytest.mark.http_op("DELETE", "/settings/commands/{name}", "422"),
        id="deleteCommand",
    ),
)


@pytest.mark.parametrize("operation_id", SUCCESS_CASES)
def test_settings_write_returns_the_declared_view(
    request: pytest.FixtureRequest, doc: dict[str, Any], operation_id: str
) -> None:
    """settings の書込 15 操作（**200 × 13・201 × 2**）—— 本文が宣言スキーマに適合する。

    対象は PUT 13（`putGlobalAgent`・`putAgent`・`updateCommand`・`putHooks`・`putHumans`・
    `putGlobalLimits`・`putProjectLimits`・`updateLlmProfile`・`putPolicy`・`updateProject`・
    `putProjectRules`・`putGlobalTasks`・`putProjectTasks`）と POST 2（`createProject`・
    `createLlmProfile`）。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    response = _send(app, call)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )


@pytest.mark.parametrize("operation_id", DELETE_CASES)
def test_settings_delete_returns_no_content(
    request: pytest.FixtureRequest, doc: dict[str, Any], operation_id: str
) -> None:
    """settings の削除 4 操作の **204** —— 本文が空である（FR-024・SC-008）。

    対象は `deleteProject`・`deleteProjectTasks`・`deleteLlmProfile`・`deleteProjectLimits`。
    ⚠ 残る 2 操作（`deleteAgent`・`deleteCommand`）の 204 は**台帳 U-2・U-3**（到達不能）。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    response = _send(app, call)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )


@pytest.mark.parametrize("operation_id", DEAD_END_CASES)
def test_dead_end_write_rejects_and_leaves_the_store_untouched(
    request: pytest.FixtureRequest,
    area_a_root: Path,
    doc: dict[str, Any],
    operation_id: str,
) -> None:
    """行き止まり 3 操作の **422** —— 拒否・保存ヘッダなし・領域 A が 1 バイトも変わらない。

    対象は `POST /settings/commands`（`createCommand`・`010` B-40）・
    `DELETE /settings/agents/{project_key}/{persona_key}`（`deleteAgent`・B-49）・
    `DELETE /settings/commands/{name}`（`deleteCommand`・B-51）の各 422（US3 AC6・FR-027）。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    before = area_bytes(area_a_root)
    response = _send(app, call)
    after = area_bytes(area_a_root)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )
    assert_no_save_headers(response)
    assert after == before, "拒否されたのに領域 A が書き換わっている（保存していないはず）"


@pytest.mark.parametrize(
    "operation_id",
    [call.operation_id for call in SETTINGS_WRITE_CALLS if call.status != "422"],
)
def test_reachable_write_carries_the_save_headers(
    request: pytest.FixtureRequest, operation_id: str
) -> None:
    """到達可能な書込 **19 操作**に保存ヘッダ 2 つが載る（FR-027・FR-029・SC-005）。

    ヘッダ名は `settings.router` の公開定数を import し、世代は **10 進整数として読める**
    ことだけを見る（値は表明しない）。反映区分は recipe の `effect` 列（**テストのリテラル**）と
    一致すること。⚠ **204 にも保存ヘッダは載る**（D-1）。
    この検証は `http_op` を**宣言しない**（対の所有は上の 3 表）。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    response = _send(app, call)

    assert str(response.status_code) == call.status, (
        f"{call.method} {call.url} が {call.status} を返さない: "
        f"{response.status_code} {response.text[:300]}"
    )
    assert call.effect is not None, f"{operation_id} の recipe に effect が無い"
    assert_save_headers(response, effect=call.effect)


def test_dead_end_operations_are_the_declared_three() -> None:
    """行き止まりの表明が **3 操作**であること（`010` の裁定と台帳の対応）。

    この検証は `http_op` を宣言しない（数の一致だけを見る）。
    """
    declared = tuple(call.operation_id for call in SETTINGS_WRITE_CALLS if call.status == "422")
    assert declared == DEAD_END_OPERATIONS, (
        f"行き止まりの 422 が 3 操作でない: {declared}（期待 {DEAD_END_OPERATIONS}）"
    )
