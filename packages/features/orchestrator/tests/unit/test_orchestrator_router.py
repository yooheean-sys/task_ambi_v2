"""HTTP の 3 本の口（F13・FR-072〜FR-078・SC-031〜SC-033）。

正本は `docs/design/phase6/openapi.yaml`。受け入れ条件は
`specs/009-orchestrator/contracts/kickoff-http.md` §7 の 9 件。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import pytest
from conftest import (
    HttpClient,
    Stubs,
    make_client,
    make_config,
    make_issue,
    make_outcome,
    make_review,
    project_label,
)
from fastapi import FastAPI

from common.kernel.log import CorrelationContext
from orchestrator import OrchestratorHandle, build

if TYPE_CHECKING:
    from collections.abc import Iterator

HEX32 = 32


def _handle(stubs: Stubs, **overrides: object) -> OrchestratorHandle:
    return build(stubs.deps(), config=make_config(**overrides))


def _client(handle: OrchestratorHandle) -> HttpClient:
    return make_client(handle)


@pytest.fixture
def gate(stubs: Stubs) -> Iterator[tuple[threading.Event, threading.Event]]:
    """周回を `list_working` で待たせる関門（進行中を模す）。"""
    entered = threading.Event()
    resume = threading.Event()

    def hold() -> None:
        entered.set()
        resume.wait(5)

    stubs.log.hooks["BoardReader.list_working"] = hold
    try:
        yield entered, resume
    finally:
        resume.set()


def _offer_one(stubs: Stubs) -> None:
    stubs.state.offer(make_issue("bd-1", labels=[project_label()]))
    stubs.proposal_gate.tokens[""] = []
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())


# --------------------------------------------------------------------------------------
# POST /kickoffs（§7 の #1・#2・#3）
# --------------------------------------------------------------------------------------


def test_post_accepts_and_returns_immediately(stubs: Stubs) -> None:
    with _client(_handle(stubs)) as client:
        response = client.post("/kickoffs")

    assert response.status_code == 202
    body: dict[str, Any] = response.json()
    assert body["status"] == "running"
    assert len(body["kickoff_id"]) == HEX32
    int(body["kickoff_id"], 16)


def test_post_while_running_is_refused_with_409(
    stubs: Stubs, gate: tuple[threading.Event, threading.Event]
) -> None:
    entered, resume = gate
    with _client(_handle(stubs)) as client:
        first = client.post("/kickoffs")
        entered.wait(5)

        second = client.post("/kickoffs")

        assert second.status_code == 409
        body: dict[str, Any] = second.json()
        assert body["current_kickoff_id"] == first.json()["kickoff_id"]
        assert body["detail"]
        resume.set()


def test_five_refusals_keep_a_single_record(
    stubs: Stubs, gate: tuple[threading.Event, threading.Event]
) -> None:
    """断りを積まない（SC-006a・FR-012a）。"""
    entered, resume = gate
    handle = _handle(stubs)
    with _client(handle) as client:
        accepted = client.post("/kickoffs")
        entered.wait(5)
        for _ in range(5):
            assert client.post("/kickoffs").status_code == 409

        current = client.get("/kickoffs/current")
        assert current.json()["running"] is True
        assert current.json()["record"]["kickoff_id"] == accepted.json()["kickoff_id"]
        resume.set()


def test_default_claim_filter_is_role_executor_without_project(stubs: Stubs) -> None:
    """本文を省略すると `ClaimFilter(labels={"role:executor"}, project_key=None)`（FR-078）。"""
    with _client(_handle(stubs)) as client:
        client.post("/kickoffs")
    _wait_for_claim(stubs)

    claim = stubs.log.of("BoardWriter.claim_next")[0].kwargs["claim_filter"]
    assert claim.labels == frozenset({"role:executor"})
    assert claim.project_key is None


def test_claim_filter_from_the_request_is_passed_through(stubs: Stubs) -> None:
    with _client(_handle(stubs)) as client:
        client.post(
            "/kickoffs",
            json={"claim_filter": {"labels": ["role:executor", "kind:child"], "project_key": "p"}},
        )
    _wait_for_claim(stubs)

    claim = stubs.log.of("BoardWriter.claim_next")[0].kwargs["claim_filter"]
    assert claim.labels == frozenset({"role:executor", "kind:child"})
    assert claim.project_key == "p"


def test_a_claim_filter_without_project_key_is_accepted(stubs: Stubs) -> None:
    """正本が任意とする `project_key` を省いた本文が **202** で通る（SC-031・FR-072・T080）。

    共有側の `ClaimFilter` をそのまま入れ子にしていたときは **422**（`loc` は
    `[body, claim_filter, project_key]`）だった。
    """
    with _client(_handle(stubs)) as client:
        response = client.post("/kickoffs", json={"claim_filter": {"labels": ["role:executor"]}})
    _wait_for_claim(stubs)

    assert response.status_code == 202
    claim = stubs.log.of("BoardWriter.claim_next")[0].kwargs["claim_filter"]
    assert claim.labels == frozenset({"role:executor"})
    assert claim.project_key is None


def test_an_empty_claim_filter_falls_back_to_the_defaults(stubs: Stubs) -> None:
    """`labels` も省くと型の既定（実行担当の 1 つだけ）に落ちる。"""
    with _client(_handle(stubs)) as client:
        response = client.post("/kickoffs", json={"claim_filter": {}})
    _wait_for_claim(stubs)

    assert response.status_code == 202
    claim = stubs.log.of("BoardWriter.claim_next")[0].kwargs["claim_filter"]
    assert claim.labels == frozenset({"role:executor"})
    assert claim.project_key is None


def test_correlation_id_is_carried_into_the_worker_thread(stubs: Stubs) -> None:
    """入口の相関 ID をワーカースレッドへ引き継ぐ（SC-033・FR-077）。"""
    seen: list[str | None] = []
    done = threading.Event()

    def observe() -> None:
        seen.append(CorrelationContext.current())
        done.set()

    stubs.log.hooks["BoardWriter.claim_next"] = observe
    token = CorrelationContext.bind("cid-42")
    try:
        with _client(_handle(stubs)) as client:
            client.post("/kickoffs")
        done.wait(5)
    finally:
        CorrelationContext.reset(token)

    assert seen == ["cid-42"]


def test_no_correlation_id_means_no_binding(stubs: Stubs) -> None:
    """現在値が無ければ束縛しない（発番しない＝ FR-077・DIFF-17）。"""
    seen: list[str | None] = []
    done = threading.Event()

    def observe() -> None:
        seen.append(CorrelationContext.current())
        done.set()

    stubs.log.hooks["BoardWriter.claim_next"] = observe
    with _client(_handle(stubs)) as client:
        client.post("/kickoffs")
    done.wait(5)

    assert seen == [None]


# --------------------------------------------------------------------------------------
# GET /kickoffs/{id}（§7 の #4・#5）
# --------------------------------------------------------------------------------------


def test_get_returns_the_finished_record(stubs: Stubs) -> None:
    _offer_one(stubs)
    with _client(_handle(stubs)) as client:
        kickoff_id = client.post("/kickoffs").json()["kickoff_id"]
        _wait_until_done(client, kickoff_id)

        response = client.get(f"/kickoffs/{kickoff_id}")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["status"] == "done"
    assert body["result"]["kickoff_id"] == kickoff_id
    assert body["ctx"]["kind"] == "human"
    assert body["ctx"]["claim_filter"]["project_key"] is None


def test_unknown_id_is_404(stubs: Stubs) -> None:
    with _client(_handle(stubs)) as client:
        response = client.get("/kickoffs/deadbeef")

    assert response.status_code == 404
    assert response.json()["detail"]


# --------------------------------------------------------------------------------------
# GET /kickoffs/current（§7 の #6・SC-032）
# --------------------------------------------------------------------------------------


def test_current_is_200_with_running_false_when_idle(stubs: Stubs) -> None:
    """走っていなくても **200 ＋ `running=false`**（**204 でない**＝ SC-032・DIFF-10）。"""
    with _client(_handle(stubs)) as client:
        response = client.get("/kickoffs/current")

    assert response.status_code == 200
    assert response.json() == {"running": False, "record": None}


# --------------------------------------------------------------------------------------
# 生成スキーマと openapi.yaml の照合（SC-031・quickstart S5）
# --------------------------------------------------------------------------------------


def test_generated_openapi_matches_the_design(stubs: Stubs) -> None:
    import yaml

    from common.kernel.config import find_repo_root

    design_path = find_repo_root() / "docs" / "design" / "phase6" / "openapi.yaml"
    design: dict[str, Any] = yaml.safe_load(design_path.read_text(encoding="utf-8"))

    app = FastAPI()
    app.include_router(_handle(stubs).router)
    generated: dict[str, Any] = app.openapi()

    paths = {path for path in design["paths"] if path.startswith("/kickoffs")}
    assert paths == {"/kickoffs", "/kickoffs/current", "/kickoffs/{kickoff_id}"}
    assert paths <= set(generated["paths"])

    for path in sorted(paths):
        for method in design["paths"][path]:
            if method == "parameters":
                continue
            expected = design["paths"][path][method]
            actual = generated["paths"][path][method]
            assert actual["operationId"] == expected["operationId"], path
            assert actual["tags"] == expected["tags"], path
            assert set(expected["responses"]) <= set(actual["responses"]), path


def test_required_fields_of_the_generated_schemas(stubs: Stubs) -> None:
    app = FastAPI()
    app.include_router(_handle(stubs).router)
    schemas: dict[str, Any] = app.openapi()["components"]["schemas"]

    assert set(schemas["KickoffAccepted"]["required"]) == {"kickoff_id", "status"}
    assert set(schemas["KickoffBusyDetail"]["required"]) == {"detail", "current_kickoff_id"}
    assert {"kickoff_id", "status", "ctx"} <= set(schemas["KickoffStatusResponse"]["required"])
    assert "running" in schemas["CurrentKickoffResponse"]["required"]


# --------------------------------------------------------------------------------------
# 入れ子まで見る `required` の突合（SC-031・T080）
# --------------------------------------------------------------------------------------

_FROZEN_REQUIRED_GAPS: dict[str, frozenset[str]] = {
    "ClaimFilter": frozenset({"labels"}),
}
"""正本が必須と書くのに生成側で必須にならない項目（`design-sync.md` B-34 に記録済み）。

共有側 `ClaimFilter.labels` は既定を持つため `required` に載らない。`packages/common/` は
FR-002 で凍結・`docs/design/phase6/` は plan (b) で変更禁止なので**どちらも直せない**。実際の
応答には常に現れる（pydantic は既定値も直列化する）ので実害は無い。**鍵は正本側の名前**。
"""

_MAX_DEPTH = 12
"""突合の深さの上限（環状参照が入り込んだら止める）。"""


def _design_doc() -> dict[str, Any]:
    """正本の openapi.yaml を読む。"""
    import yaml

    from common.kernel.config import find_repo_root

    path = find_repo_root() / "docs" / "design" / "phase6" / "openapi.yaml"
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _deref(node: dict[str, Any], doc: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """`$ref` を 1 段辿って実体と名前を返す（`schemas`／`responses` の両方に効く）。"""
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node, None
    section, name = ref.rsplit("/", 2)[-2:]
    resolved: dict[str, Any] = doc["components"][section][name]
    return resolved, name


def _flatten(node: dict[str, Any], doc: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """`$ref`・`allOf`・`oneOf`／`anyOf` を 1 つの節へ潰す（`null` の枝は捨てる）。"""
    node, name = _deref(node, doc)
    parts: list[dict[str, Any]] = node.get("allOf", [])
    if parts:
        required: set[str] = set(node.get("required", []))
        properties: dict[str, Any] = dict(node.get("properties", {}))
        for part in parts:
            merged, _ = _flatten(part, doc)
            required |= set(merged.get("required", []))
            properties.update(merged.get("properties", {}))
        return {"required": sorted(required), "properties": properties}, name
    for key in ("oneOf", "anyOf"):
        options: list[dict[str, Any]] = node.get(key, [])
        if not options:
            continue
        objects = [option for option in options if option.get("type") != "null"]
        return _flatten(objects[0], doc) if len(objects) == 1 else ({}, name)
    return node, name


def _compare_required(
    design: dict[str, Any],
    generated: dict[str, Any],
    docs: tuple[dict[str, Any], dict[str, Any]],
    path: str,
    problems: list[str],
    *,
    is_request: bool,
    depth: int = 0,
) -> None:
    """1 対の節を潰して `required` を比べ、共通の項目へ降りる。

    向きは本文の役割で決まる:

    - **要求本文は 生成 ⊆ 正本** —— 生成側だけが必須にすると、正本どおりの本文が 422 になる。
    - **応答本文は 正本 ⊆ 生成** —— 正本が約束した項目が生成側で任意に落ちるのを捕まえる。
    """
    assert depth <= _MAX_DEPTH, f"{path}: 突合が深すぎる（環状参照？）"
    design_node, design_name = _flatten(design, docs[0])
    generated_node, _ = _flatten(generated, docs[1])
    design_required = set(design_node.get("required", []))
    generated_required = set(generated_node.get("required", []))
    if is_request:
        extra = generated_required - design_required
        if extra:
            problems.append(f"{path}: 生成側だけが必須にしている {sorted(extra)}")
    else:
        allowed = _FROZEN_REQUIRED_GAPS.get(design_name or "", frozenset())
        missing = design_required - generated_required - allowed
        if missing:
            problems.append(f"{path}: 正本が必須とする {sorted(missing)} が生成側で任意")

    design_props: dict[str, Any] = design_node.get("properties", {})
    generated_props: dict[str, Any] = generated_node.get("properties", {})
    for key in sorted(set(design_props) & set(generated_props)):
        _compare_required(
            design_props[key],
            generated_props[key],
            docs,
            f"{path}.{key}",
            problems,
            is_request=is_request,
            depth=depth + 1,
        )
    design_items: dict[str, Any] | None = design_node.get("items")
    generated_items: dict[str, Any] | None = generated_node.get("items")
    if design_items is not None and generated_items is not None:
        _compare_required(
            design_items,
            generated_items,
            docs,
            f"{path}[]",
            problems,
            is_request=is_request,
            depth=depth + 1,
        )


def _json_schema(node: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any] | None:
    """要求／応答の節から `application/json` のスキーマを取り出す（無ければ `None`）。"""
    resolved, _ = _deref(node, doc)
    content: dict[str, Any] = resolved.get("content", {})
    media: dict[str, Any] = content.get("application/json", {})
    schema: dict[str, Any] | None = media.get("schema")
    return schema


def test_required_fields_match_the_design_in_nested_schemas(stubs: Stubs) -> None:
    """正本と生成スキーマの `required` を**入れ子まで再帰的に**突き合わせる（SC-031・T080）。

    最上位 4 スキーマの手書き期待値（`test_required_fields_of_the_generated_schemas`）は
    入れ子を 1 つも見ておらず、`ClaimFilter` の必須項目の逆転を素通ししていた。
    """
    design = _design_doc()
    app = FastAPI()
    app.include_router(_handle(stubs).router)
    generated: dict[str, Any] = app.openapi()
    docs = (design, generated)
    problems: list[str] = []

    for path in sorted(p for p in design["paths"] if p.startswith("/kickoffs")):
        operations: dict[str, Any] = design["paths"][path]
        for method, operation in operations.items():
            if method == "parameters":
                continue
            actual: dict[str, Any] = generated["paths"][path][method]
            body: dict[str, Any] | None = operation.get("requestBody")
            if body is not None:
                expected_schema = _json_schema(body, design)
                actual_schema = _json_schema(actual["requestBody"], generated)
                if expected_schema is not None and actual_schema is not None:
                    _compare_required(
                        expected_schema,
                        actual_schema,
                        docs,
                        f"{method.upper()} {path} <body>",
                        problems,
                        is_request=True,
                    )
            responses: dict[str, Any] = operation["responses"]
            for code, response in responses.items():
                expected_schema = _json_schema(response, design)
                actual_schema = _json_schema(actual["responses"][code], generated)
                assert expected_schema is not None, f"{path} {code}: 正本に本文が無い"
                assert actual_schema is not None, f"{path} {code}: 生成側に本文スキーマが無い"
                _compare_required(
                    expected_schema,
                    actual_schema,
                    docs,
                    f"{method.upper()} {path} -> {code}",
                    problems,
                    is_request=False,
                )

    assert problems == []


def test_router_has_no_prefix(stubs: Stubs) -> None:
    routes = {getattr(route, "path", "") for route in _handle(stubs).router.routes}

    assert routes == {"/kickoffs", "/kickoffs/current", "/kickoffs/{kickoff_id}"}


# --------------------------------------------------------------------------------------
# 待ち合わせの小道具（固定の sleep に頼らない）
# --------------------------------------------------------------------------------------


def _wait_for_claim(stubs: Stubs, timeout: float = 5.0) -> None:
    deadline = threading.Event()
    for _ in range(int(timeout * 200)):
        if stubs.log.count("BoardWriter.claim_next") >= 1:
            return
        deadline.wait(0.005)
    msg = "ワーカースレッドが確保まで到達しなかった"
    raise AssertionError(msg)


def _wait_until_done(client: HttpClient, kickoff_id: str, timeout: float = 5.0) -> None:
    waiter = threading.Event()
    for _ in range(int(timeout * 200)):
        body: dict[str, Any] = client.get(f"/kickoffs/{kickoff_id}").json()
        if body["status"] == "done":
            return
        waiter.wait(0.005)
    msg = "周回が終わらなかった"
    raise AssertionError(msg)
