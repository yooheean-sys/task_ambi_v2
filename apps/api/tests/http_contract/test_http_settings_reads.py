"""settings の読取 17 操作 × 200（US2 AC1・FR-019・FR-020）。

領域 A の実物（一時ディレクトリ）を `build()` の**前**に置いて組んだ実配線に対し、
応答本文を正本の component schema へ通す。

**既知差分 7 件は `xfail(strict=True)`**（FR-044・HD-30）—— `reason` は `http_ledger.py` の
`KNOWN_DIVERGENCES` から引くので、台帳を直すと文言も追随する。**`strict=True` を外さない**／
**検査器を緩めて吸収しない**（FR-042）。

あわせて**読取 17 操作に保存ヘッダが載らない**ことを見る（FR-028・SC-005）。⚠ 層 1 の
`READ_CALLS` は 15 パスまでしか観測しておらず `getCommand` が抜けている（B-07）ので、
ここでは 17 操作すべてを通す。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.openapi_source import load_document, openapi_path
from http_asserts import assert_conforms, assert_no_save_headers
from http_calls import SETTINGS_READ_CALLS, call_of
from http_ledger import divergence_of
from http_wiring import client_of

if TYPE_CHECKING:
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


READ_CASES = (
    pytest.param(
        "listAgents",
        marks=[
            pytest.mark.http_op("GET", "/settings/agents", "200"),
            _xfail("GET", "/settings/agents", "200"),
        ],
        id="listAgents",
    ),
    pytest.param(
        "getGlobalAgent",
        marks=[
            pytest.mark.http_op("GET", "/settings/agents/global/{persona_key}", "200"),
            _xfail("GET", "/settings/agents/global/{persona_key}", "200"),
        ],
        id="getGlobalAgent",
    ),
    pytest.param(
        "getAgent",
        marks=[
            pytest.mark.http_op("GET", "/settings/agents/{project_key}/{persona_key}", "200"),
            _xfail("GET", "/settings/agents/{project_key}/{persona_key}", "200"),
        ],
        id="getAgent",
    ),
    pytest.param(
        "listCommands",
        marks=pytest.mark.http_op("GET", "/settings/commands", "200"),
        id="listCommands",
    ),
    pytest.param(
        "getCommand",
        marks=pytest.mark.http_op("GET", "/settings/commands/{name}", "200"),
        id="getCommand",
    ),
    pytest.param(
        "getHooks",
        marks=pytest.mark.http_op("GET", "/settings/hooks/{project_key}", "200"),
        id="getHooks",
    ),
    pytest.param(
        "getHumans",
        marks=pytest.mark.http_op("GET", "/settings/humans", "200"),
        id="getHumans",
    ),
    pytest.param(
        "getLimits",
        marks=pytest.mark.http_op("GET", "/settings/limits", "200"),
        id="getLimits",
    ),
    pytest.param(
        "listLlmProfiles",
        marks=[
            pytest.mark.http_op("GET", "/settings/llm-profiles", "200"),
            _xfail("GET", "/settings/llm-profiles", "200"),
        ],
        id="listLlmProfiles",
    ),
    pytest.param(
        "getLlmProfile",
        marks=[
            pytest.mark.http_op("GET", "/settings/llm-profiles/{name}", "200"),
            _xfail("GET", "/settings/llm-profiles/{name}", "200"),
        ],
        id="getLlmProfile",
    ),
    pytest.param(
        "getPolicy",
        marks=pytest.mark.http_op("GET", "/settings/policies/{project_key}", "200"),
        id="getPolicy",
    ),
    pytest.param(
        "listProjects",
        marks=pytest.mark.http_op("GET", "/settings/projects", "200"),
        id="listProjects",
    ),
    pytest.param(
        "getProject",
        marks=pytest.mark.http_op("GET", "/settings/projects/{project_key}", "200"),
        id="getProject",
    ),
    pytest.param(
        "listProjectRoles",
        marks=pytest.mark.http_op("GET", "/settings/projects/{project_key}/roles", "200"),
        id="listProjectRoles",
    ),
    pytest.param(
        "getProjectRules",
        marks=pytest.mark.http_op("GET", "/settings/rules/projects/{project_key}", "200"),
        id="getProjectRules",
    ),
    pytest.param(
        "getGlobalTasks",
        marks=[
            pytest.mark.http_op("GET", "/settings/tasks/global", "200"),
            _xfail("GET", "/settings/tasks/global", "200"),
        ],
        id="getGlobalTasks",
    ),
    pytest.param(
        "getProjectTasks",
        marks=[
            pytest.mark.http_op("GET", "/settings/tasks/{project_key}", "200"),
            _xfail("GET", "/settings/tasks/{project_key}", "200"),
        ],
        id="getProjectTasks",
    ),
)


@pytest.mark.parametrize("operation_id", READ_CASES)
def test_settings_read_returns_the_declared_view(
    settings_app: FastAPI, doc: dict[str, Any], operation_id: str
) -> None:
    """settings の読取 17 操作の **200** —— 本文が正本の宣言スキーマに適合する。

    対象は `/settings/agents`・`/settings/agents/global/{persona_key}`・
    `/settings/agents/{project_key}/{persona_key}`・`/settings/commands`・
    `/settings/commands/{name}`・`/settings/hooks/{project_key}`・`/settings/humans`・
    `/settings/limits`・`/settings/llm-profiles`・`/settings/llm-profiles/{name}`・
    `/settings/policies/{project_key}`・`/settings/projects`・
    `/settings/projects/{project_key}`・`/settings/rules/projects/{project_key}`・
    `/settings/tasks/global`・`/settings/tasks/{project_key}` の各 GET（すべて 200）。
    """
    call = call_of(operation_id)
    with client_of(settings_app) as client:
        response = client.get(call.url)

    assert_conforms(
        doc, path=call.template, method=call.method, status=call.status, response=response
    )


@pytest.mark.parametrize("operation_id", [call.operation_id for call in SETTINGS_READ_CALLS])
def test_settings_read_does_not_carry_save_headers(
    settings_app: FastAPI, operation_id: str
) -> None:
    """読取 17 操作の 200 に**保存ヘッダが載らない**（FR-028・SC-005・US3 AC3）。

    対の所有は上の表なので、この検証は `http_op` を**宣言しない**（規約 §5）。
    ⚠ 層 1 は 15 パスまでしか観測していない（`getCommand` が抜けている＝ B-07）。
    """
    call = call_of(operation_id)
    with client_of(settings_app) as client:
        response = client.get(call.url)

    assert response.status_code == 200, (  # noqa: PLR2004  # 前提（適合は上の表が見る）
        f"{call.method} {call.url} が 200 を返さない: {response.status_code}"
    )
    assert_no_save_headers(response)
