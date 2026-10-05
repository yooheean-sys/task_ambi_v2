"""settings の否定側 37 対（404 × 23・409 × 1・422 × 13）。

**業務 validate 経路の 422 だけ**を検証する。⚠ 口の型違反による FastAPI 内蔵 422
（`{"detail":[…]}`）に適合検証を置かない（正本の `Problem` と形が違う＝ FR-026・B-03）。
⚠ 未知の `persona_key` は path の enum に当たって内蔵 422 になるので **404 の踏み方に使わない**
（未知の `project_key` で踏む）。

**既知差分 7 件は `xfail(strict=True)`**（実測の `kind` が `llm_ref`・`var_required` で正本の
7 語 enum の外＝ B-12。`010` B-42 が「切り詰めない」と定めている）。

エラー応答には**保存ヘッダが載らない**こと、409・422 では**領域 A が 1 バイトも変わらない**
ことも見る（plan (vi)）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.openapi_source import load_document, openapi_path
from http_asserts import assert_conforms, assert_no_save_headers
from http_calls import SETTINGS_ERROR_CALLS, HttpCall, call_of
from http_ledger import divergence_of
from http_stores import area_bytes
from http_wiring import client_of

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI


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


NOT_FOUND_CASES = (
    pytest.param(
        "getGlobalAgent_404",
        marks=pytest.mark.http_op("GET", "/settings/agents/global/{persona_key}", "404"),
        id="getGlobalAgent",
    ),
    pytest.param(
        "getAgent_404",
        marks=pytest.mark.http_op("GET", "/settings/agents/{project_key}/{persona_key}", "404"),
        id="getAgent",
    ),
    pytest.param(
        "deleteAgent_404",
        marks=pytest.mark.http_op("DELETE", "/settings/agents/{project_key}/{persona_key}", "404"),
        id="deleteAgent",
    ),
    pytest.param(
        "getCommand_404",
        marks=pytest.mark.http_op("GET", "/settings/commands/{name}", "404"),
        id="getCommand",
    ),
    pytest.param(
        "updateCommand_404",
        marks=pytest.mark.http_op("PUT", "/settings/commands/{name}", "404"),
        id="updateCommand",
    ),
    pytest.param(
        "deleteCommand_404",
        marks=pytest.mark.http_op("DELETE", "/settings/commands/{name}", "404"),
        id="deleteCommand",
    ),
    pytest.param(
        "getHooks_404",
        marks=pytest.mark.http_op("GET", "/settings/hooks/{project_key}", "404"),
        id="getHooks",
    ),
    pytest.param(
        "putHooks_404",
        marks=pytest.mark.http_op("PUT", "/settings/hooks/{project_key}", "404"),
        id="putHooks",
    ),
    pytest.param(
        "deleteProjectLimits_404",
        marks=pytest.mark.http_op("DELETE", "/settings/limits/{project_key}", "404"),
        id="deleteProjectLimits",
    ),
    pytest.param(
        "putProjectLimits_404",
        marks=pytest.mark.http_op("PUT", "/settings/limits/{project_key}", "404"),
        id="putProjectLimits",
    ),
    pytest.param(
        "getLlmProfile_404",
        marks=pytest.mark.http_op("GET", "/settings/llm-profiles/{name}", "404"),
        id="getLlmProfile",
    ),
    pytest.param(
        "updateLlmProfile_404",
        marks=pytest.mark.http_op("PUT", "/settings/llm-profiles/{name}", "404"),
        id="updateLlmProfile",
    ),
    pytest.param(
        "deleteLlmProfile_404",
        marks=pytest.mark.http_op("DELETE", "/settings/llm-profiles/{name}", "404"),
        id="deleteLlmProfile",
    ),
    pytest.param(
        "getPolicy_404",
        marks=pytest.mark.http_op("GET", "/settings/policies/{project_key}", "404"),
        id="getPolicy",
    ),
    pytest.param(
        "putPolicy_404",
        marks=pytest.mark.http_op("PUT", "/settings/policies/{project_key}", "404"),
        id="putPolicy",
    ),
    pytest.param(
        "getProject_404",
        marks=pytest.mark.http_op("GET", "/settings/projects/{project_key}", "404"),
        id="getProject",
    ),
    pytest.param(
        "listProjectRoles_404",
        marks=pytest.mark.http_op("GET", "/settings/projects/{project_key}/roles", "404"),
        id="listProjectRoles",
    ),
    pytest.param(
        "updateProject_404",
        marks=pytest.mark.http_op("PUT", "/settings/projects/{project_key}", "404"),
        id="updateProject",
    ),
    pytest.param(
        "deleteProject_404",
        marks=pytest.mark.http_op("DELETE", "/settings/projects/{project_key}", "404"),
        id="deleteProject",
    ),
    pytest.param(
        "getProjectRules_404",
        marks=pytest.mark.http_op("GET", "/settings/rules/projects/{project_key}", "404"),
        id="getProjectRules",
    ),
    pytest.param(
        "putProjectRules_404",
        marks=pytest.mark.http_op("PUT", "/settings/rules/projects/{project_key}", "404"),
        id="putProjectRules",
    ),
    pytest.param(
        "getProjectTasks_404",
        marks=pytest.mark.http_op("GET", "/settings/tasks/{project_key}", "404"),
        id="getProjectTasks",
    ),
    pytest.param(
        "putProjectTasks_404",
        marks=pytest.mark.http_op("PUT", "/settings/tasks/{project_key}", "404"),
        id="putProjectTasks",
    ),
    pytest.param(
        "deleteProjectTasks_404",
        marks=pytest.mark.http_op("DELETE", "/settings/tasks/{project_key}", "404"),
        id="deleteProjectTasks",
    ),
)

REJECTED_CASES = (
    pytest.param(
        "createProject_422",
        marks=pytest.mark.http_op("POST", "/settings/projects", "422"),
        id="createProject",
    ),
    pytest.param(
        "updateProject_422",
        marks=pytest.mark.http_op("PUT", "/settings/projects/{project_key}", "422"),
        id="updateProject",
    ),
    pytest.param(
        "putHumans_422",
        marks=pytest.mark.http_op("PUT", "/settings/humans", "422"),
        id="putHumans",
    ),
    pytest.param(
        "putPolicy_422",
        marks=pytest.mark.http_op("PUT", "/settings/policies/{project_key}", "422"),
        id="putPolicy",
    ),
    pytest.param(
        "putGlobalLimits_422",
        marks=pytest.mark.http_op("PUT", "/settings/limits", "422"),
        id="putGlobalLimits",
    ),
    pytest.param(
        "putProjectLimits_422",
        marks=pytest.mark.http_op("PUT", "/settings/limits/{project_key}", "422"),
        id="putProjectLimits",
    ),
    pytest.param(
        "putGlobalAgent_422",
        marks=[
            pytest.mark.http_op("PUT", "/settings/agents/global/{persona_key}", "422"),
            _xfail("PUT", "/settings/agents/global/{persona_key}", "422"),
        ],
        id="putGlobalAgent",
    ),
    pytest.param(
        "putAgent_422",
        marks=[
            pytest.mark.http_op("PUT", "/settings/agents/{project_key}/{persona_key}", "422"),
            _xfail("PUT", "/settings/agents/{project_key}/{persona_key}", "422"),
        ],
        id="putAgent",
    ),
    pytest.param(
        "createLlmProfile_422",
        marks=[
            pytest.mark.http_op("POST", "/settings/llm-profiles", "422"),
            _xfail("POST", "/settings/llm-profiles", "422"),
        ],
        id="createLlmProfile",
    ),
    pytest.param(
        "updateLlmProfile_422",
        marks=[
            pytest.mark.http_op("PUT", "/settings/llm-profiles/{name}", "422"),
            _xfail("PUT", "/settings/llm-profiles/{name}", "422"),
        ],
        id="updateLlmProfile",
    ),
    pytest.param(
        "deleteLlmProfile_422",
        marks=[
            pytest.mark.http_op("DELETE", "/settings/llm-profiles/{name}", "422"),
            _xfail("DELETE", "/settings/llm-profiles/{name}", "422"),
        ],
        id="deleteLlmProfile",
    ),
    pytest.param(
        "putGlobalTasks_422",
        marks=[
            pytest.mark.http_op("PUT", "/settings/tasks/global", "422"),
            _xfail("PUT", "/settings/tasks/global", "422"),
        ],
        id="putGlobalTasks",
    ),
    pytest.param(
        "putProjectTasks_422",
        marks=[
            pytest.mark.http_op("PUT", "/settings/tasks/{project_key}", "422"),
            _xfail("PUT", "/settings/tasks/{project_key}", "422"),
        ],
        id="putProjectTasks",
    ),
)


@pytest.mark.parametrize("operation_id", NOT_FOUND_CASES)
def test_unknown_key_is_reported_as_not_found(
    request: pytest.FixtureRequest, doc: dict[str, Any], operation_id: str
) -> None:
    """settings の **404 × 23** —— 本文が `Detail` に適合する（US4 AC1・FR-022）。

    未登録のキーで叩く。⚠ `getGlobalAgent` だけは手順が違う —— 既定の雛形は 9 ペルソナ
    すべてを埋めるので、`global/agents.yaml` に 1 ペルソナだけを置いた変種で
    **実在するが既定が無い**ペルソナを引く。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    response = _send(app, call)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )


@pytest.mark.http_op("DELETE", "/settings/projects/{project_key}", "409")
def test_delete_project_conflicts_while_the_board_has_open_issues(
    settings_app_with_open_issues: FastAPI, doc: dict[str, Any]
) -> None:
    """DELETE /settings/projects/{project_key} (deleteProject) の **409**。

    盤面に未完了が在るときの削除ガード。本文は `Detail` に適合する
    （⚠ **違反一覧は返らない**＝ US4 AC2・SC-007・FR-023）。
    """
    call = call_of("deleteProject_409")
    response = _send(settings_app_with_open_issues, call)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )


@pytest.mark.parametrize("operation_id", REJECTED_CASES)
def test_business_validation_rejects_with_a_problem(
    request: pytest.FixtureRequest, doc: dict[str, Any], operation_id: str
) -> None:
    """settings の **422 × 13** —— 本文が `Problem` に適合する（US4 AC4・FR-021）。

    いずれも業務 validate 経路で踏む（既存キーの登録・役割フォルダの欠落・メンバ ID を
    人間として宣言・台帳に居ない主体・上限 0・居ない LLM プロファイル・閾値 T が 0 以下・
    既定プロファイルの削除・段別必須変数の欠落）。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    response = _send(app, call)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )


@pytest.mark.parametrize("operation_id", [call.operation_id for call in SETTINGS_ERROR_CALLS])
def test_error_response_does_not_carry_save_headers(
    request: pytest.FixtureRequest, area_a_root: Path, operation_id: str
) -> None:
    """404・409・422 の **37 対**に保存ヘッダが載らない（plan (vi)）。

    409・422 では領域 A が **1 バイトも変わらない**ことも見る（保存していないため）。
    この検証は `http_op` を**宣言しない**（対の所有は上の 3 つ）。
    """
    call = call_of(operation_id)
    app: FastAPI = request.getfixturevalue(call.fixture)
    before = area_bytes(area_a_root)
    response = _send(app, call)
    after = area_bytes(area_a_root)

    assert str(response.status_code) == call.status, (
        f"{call.method} {call.url} が {call.status} を返さない: "
        f"{response.status_code} {response.text[:300]}"
    )
    assert_no_save_headers(response)
    if call.status in ("409", "422"):
        assert after == before, "拒否されたのに領域 A が書き換わっている"


def test_create_project_also_rejects_when_the_vault_folders_are_missing(
    settings_app_folder_missing: FastAPI, doc: dict[str, Any]
) -> None:
    """POST /settings/projects (createProject) の 422 —— **別機序**（保管庫のフォルダ不在）。

    対の所有は `createProject_422`（既存キー＝ `key_rule`）なので、ここでは `http_op` を
    宣言しない。同じ status を**別の機序**でも踏めることの表明である。
    """
    call = call_of("createProject_422")
    with client_of(settings_app_folder_missing) as client:
        response = client.post(call.url, json=call.body)

    assert_conforms(doc, path=call.template, method="POST", status="422", response=response)


def test_valid_requests_do_not_produce_error_statuses(
    settings_app: FastAPI, settings_app_two_projects: FastAPI, doc: dict[str, Any]
) -> None:
    """否定側 —— 条件を満たす要求に 404・409・422 が**誤って返らない**（US4 AC5・FR-025）。

    ⚠ `PUT /settings/rules/projects/{project_key}` は **422 を宣言していない**ので、
    宣言に無い status を「返らないこと」として確かめるのではなく、**宣言された 200・404 だけ**を
    検証する（正本を正とする＝ spec Edge Cases）。
    この検証は `http_op` を宣言しない（対の所有は各表）。
    """
    with client_of(settings_app) as client:
        existing = client.get("/settings/projects/pj")
        updated = client.put("/settings/projects/pj", json=call_of("updateProject").body)
    with client_of(settings_app_two_projects) as client:
        removed = client.delete("/settings/projects/keep")

    assert existing.status_code == 200, (
        f"実在キーの getProject が 200 でない: {existing.text[:200]}"
    )
    assert updated.status_code == 200, (
        f"規則に適合する updateProject が 200 でない: {updated.text[:200]}"
    )
    assert removed.status_code == 204, (
        f"未完了が無いのに deleteProject が 204 でない: {removed.status_code} {removed.text[:200]}"
    )
    assert_conforms(
        doc, path="/settings/projects/{project_key}", method="GET", status="200", response=existing
    )
