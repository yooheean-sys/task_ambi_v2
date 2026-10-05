"""US1: モジュール 0 個でアプリが組み立てられ、生存確認が 200 を返す。

検証行: CR-1・H-1・H-2・H-5（US1 AC1／AC2／AC4・SC-001・SC-002）。
本ファイルが `integration/` に置かれたまま `uv run pytest` の既定収集で発見されることが、
FR-038（US3 AC5）の確認そのものでもある。
"""

from __future__ import annotations

from typing import Any

from _support.composition import (
    build_test_app,
    build_test_client,
    collect_api_routes,
    make_root_config,
)

from api.wiring import build_modules

HEALTH_BODY_KEYS = {"status", "app", "env_label", "modules"}


def test_cr1_build_modules_with_zero_modules_returns_empty_wiring() -> None:
    """CR-1: `enabled_modules=[]` で `Wiring` の 4 属性がいずれも空（FR-005）。"""
    wiring = build_modules(enabled_modules=[], config=make_root_config())

    assert dict(wiring.provided) == {}
    assert wiring.modules == ()
    assert wiring.routers == ()
    assert wiring.lifespans == ()


def test_zero_modules_has_three_distinct_states() -> None:
    """「モジュールが 0 個」の 3 状態がいずれも成功する（contracts §1・CHK005）。

    `[]`（0 個を明示）・`None`（ルート設定由来で現値が空）・`registrations=()`（登録が空）。
    """
    config = make_root_config(enabled_modules=[])

    explicit = build_modules(enabled_modules=[], config=config)
    from_config = build_modules(enabled_modules=None, config=config)
    empty_registry = build_modules(enabled_modules=[], config=config, registrations=())

    assert explicit.modules == ()
    assert from_config.modules == ()
    assert empty_registry.modules == ()


def test_us1_ac1_create_app_succeeds_with_zero_modules() -> None:
    """US1 AC1・SC-001: 登録 0 件で `create_app()` が例外なく `FastAPI` を返す。"""
    app = build_test_app(enabled_modules=[], registrations=())

    assert app.title == "task_ambi"


def test_h1_health_returns_200_with_exactly_four_keys() -> None:
    """H-1・US1 AC2: 200 で本文の鍵が 4 件ちょうど、`modules` が空（FR-015・FR-017）。"""
    config = make_root_config(app_name="task_ambi", env_label="test")
    with build_test_client(enabled_modules=[], config=config, registrations=()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert set(body) == HEALTH_BODY_KEYS
    assert body["status"] == "ok"
    assert body["app"] == "task_ambi"
    assert body["env_label"] == "test"
    assert body["modules"] == []


def test_h2_only_public_route_is_health() -> None:
    """H-2・US1 AC4・SC-002: 公開経路は生存確認の 1 本のみ。"""
    app = build_test_app(enabled_modules=[], registrations=())

    schema: dict[str, Any] = app.openapi()
    paths: dict[str, Any] = schema["paths"]
    api_routes = collect_api_routes(app)

    assert set(paths) == {"/health"}
    assert len(api_routes) == 1
    assert api_routes[0].path == "/health"


def test_h5_no_prefix_no_version_no_security() -> None:
    """H-5・FR-018: 接頭辞・版番号が無く、`security` が空（＝生成スキーマに現れない）。"""
    app = build_test_app(enabled_modules=[], registrations=())

    schema: dict[str, Any] = app.openapi()
    components: dict[str, Any] = schema.get("components", {})
    operation: dict[str, Any] = schema["paths"]["/health"]["get"]

    assert app.root_path == ""
    assert "security" not in schema
    assert "securitySchemes" not in components
    assert "security" not in operation
    for path in schema["paths"]:
        segments = [segment for segment in str(path).split("/") if segment]
        assert segments == ["health"], f"接頭辞・版番号のある経路がある: {path}"
