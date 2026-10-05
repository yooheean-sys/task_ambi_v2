"""US1: `/health` の生成スキーマが `docs/design/phase6/openapi.yaml` と一致する。

検証行: H-3・H-4・H-6・H-8（SC-002・SC-009）。設計文書を**直接読んで**突き合わせるので、
設計側が変われば本テストが追随する（手書きの期待値を埋めない。research R-10）。

pydantic v2 は `Literal["ok"]` を `{"const": "ok"}` として出力し `enum: ["ok"]` にしないため、
比較の前に **`const: X` を `enum: [X]` に正規化**する（research R-09 の罠 2）。
"""

from __future__ import annotations

import builtins
import pathlib
import socket
import subprocess
import tomllib
from collections.abc import Callable
from typing import Any, cast

import pytest
import yaml
from _support.composition import build_test_app, build_test_client

SCHEMA_KEYS = ("type", "enum", "format")


def _repo_root() -> pathlib.Path:
    """`[tool.uv.workspace]` を持つ `pyproject.toml` を上方向に探す（cwd 非依存）。"""
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            data: dict[str, Any] = tomllib.loads(candidate.read_text(encoding="utf-8"))
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return parent
    raise AssertionError("workspace ルートが見つからない")


def _design_document() -> dict[str, Any]:
    """設計側の正本 `docs/design/phase6/openapi.yaml` を読む。"""
    path = _repo_root() / "docs" / "design" / "phase6" / "openapi.yaml"
    assert path.is_file(), f"設計側の正本が見つからない: {path}"
    loaded: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"openapi.yaml が写像として読めない: {path}"
    return cast("dict[str, Any]", loaded)


def _normalize(schema: dict[str, Any]) -> dict[str, Any]:
    """`const: X` を `enum: [X]` に正規化し、比較に使う鍵だけを残す。"""
    normalized = dict(schema)
    if "const" in normalized:
        normalized["enum"] = [normalized.pop("const")]
    return {key: normalized[key] for key in SCHEMA_KEYS if key in normalized}


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """`$ref` を 1 段だけたどる（本 feature の対象は `#/components/schemas/<name>` のみ）。"""
    ref = schema.get("$ref")
    if ref is None:
        return schema
    assert isinstance(ref, str) and ref.startswith("#/"), f"想定外の $ref: {ref}"
    resolved: Any = document
    for part in ref.removeprefix("#/").split("/"):
        resolved = resolved[part]
    assert isinstance(resolved, dict), f"$ref の解決先が写像でない: {ref}"
    return cast("dict[str, Any]", resolved)


@pytest.fixture(scope="module")
def design() -> dict[str, Any]:
    """設計側の openapi.yaml（module スコープで 1 回だけ読む）。"""
    return _design_document()


@pytest.fixture(scope="module")
def generated() -> dict[str, Any]:
    """実装が生成する OpenAPI スキーマ。"""
    app = build_test_app(enabled_modules=[], registrations=())
    schema: dict[str, Any] = app.openapi()
    return schema


def test_h3_path_method_operation_id_tags_and_media_type(
    design: dict[str, Any], generated: dict[str, Any]
) -> None:
    """H-3・SC-002: path・method・`operationId`・`tags`・200 の media type が一致する。"""
    design_operation: dict[str, Any] = design["paths"]["/health"]["get"]
    generated_paths: dict[str, Any] = generated["paths"]
    assert "/health" in generated_paths, "実装に /health が無い"
    assert set(generated_paths["/health"]) == {"get"}, "実装の /health に GET 以外がある"

    generated_operation: dict[str, Any] = generated_paths["/health"]["get"]
    assert generated_operation["operationId"] == design_operation["operationId"]
    assert generated_operation["tags"] == design_operation["tags"]

    design_media = set(design_operation["responses"]["200"]["content"])
    generated_media = set(generated_operation["responses"]["200"]["content"])
    assert generated_media == design_media


def test_h4_response_schema_matches_design(
    design: dict[str, Any], generated: dict[str, Any]
) -> None:
    """H-4・FR-015: 200 応答スキーマの required・プロパティ・type・enum・items.type が一致。"""
    design_schema = _resolve(
        design,
        design["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ],
    )
    generated_schema = _resolve(
        generated,
        generated["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ],
    )

    design_props: dict[str, Any] = design_schema["properties"]
    generated_props: dict[str, Any] = generated_schema["properties"]

    assert set(generated_schema["required"]) == set(design_schema["required"])
    assert set(generated_props) == set(design_props)

    for name in sorted(design_props):
        expected = _normalize(design_props[name])
        actual = _normalize(generated_props[name])
        assert actual == expected, f"プロパティ {name} のスキーマが設計と食い違う"
        if expected.get("type") == "array":
            expected_items = _normalize(design_props[name]["items"])
            actual_items = _normalize(generated_props[name]["items"])
            assert actual_items == expected_items, f"プロパティ {name} の items が食い違う"


def test_h6_health_is_200_without_any_external_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-6・US1 AC3: 外部ツールが 1 つも用意されていない環境でも 200。

    `PATH` を空にし `BD_BIN` を外すと、`bd` を含むどの外部コマンドも解決できなくなる
    （`apps/api/tests/conftest.py` の `bd_bin` fixture が探す 2 経路の両方を潰す）。
    """
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("BD_BIN", raising=False)

    with build_test_client(enabled_modules=[], registrations=()) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_h8_health_touches_no_external_process_file_or_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-8・SC-009: 生存確認の処理中に外部プロセス起動・外部ファイル読み取り・外部 HTTP が 0 回。

    先に 1 回叩いて遅延 import（モジュールの読み込み＝ファイル読み取り）を済ませてから
    番兵を仕掛ける。仕掛けた後の 1 要求で 3 種いずれの記録も 0 件であることを確かめる。
    """
    with build_test_client(enabled_modules=[], registrations=()) as client:
        assert client.get("/health").status_code == 200

        processes: list[str] = []
        opened: list[str] = []
        connections: list[str] = []

        # `open` は多数のオーバーロードを持ち、`*args` 経由の呼び出しでは戻り値の型が
        # 解決できない。番兵は素通しするだけなので、呼び出し可能として受ける。
        original_open: Callable[..., Any] = builtins.open
        original_read_text = pathlib.Path.read_text
        original_read_bytes = pathlib.Path.read_bytes

        def record_popen(*args: Any, **kwargs: Any) -> Any:
            processes.append(f"subprocess.Popen{args!r}")
            raise AssertionError("生存確認が外部プロセスを起動した")

        def record_open(*args: Any, **kwargs: Any) -> object:
            opened.append(f"open{args!r}")
            return original_open(*args, **kwargs)

        def record_read_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
            opened.append(f"Path.read_text({self})")
            return original_read_text(self, *args, **kwargs)

        def record_read_bytes(self: pathlib.Path) -> bytes:
            opened.append(f"Path.read_bytes({self})")
            return original_read_bytes(self)

        def record_connect(self: socket.socket, address: Any) -> None:
            connections.append(f"socket.connect({address!r})")
            raise AssertionError("生存確認が外部 HTTP（TCP 接続）を行った")

        def record_create_connection(*args: Any, **kwargs: Any) -> Any:
            connections.append(f"socket.create_connection{args!r}")
            raise AssertionError("生存確認が外部 HTTP（TCP 接続）を行った")

        monkeypatch.setattr(subprocess, "Popen", record_popen)
        monkeypatch.setattr(subprocess, "run", record_popen)
        monkeypatch.setattr(builtins, "open", record_open)
        monkeypatch.setattr(pathlib.Path, "read_text", record_read_text)
        monkeypatch.setattr(pathlib.Path, "read_bytes", record_read_bytes)
        monkeypatch.setattr(socket.socket, "connect", record_connect)
        monkeypatch.setattr(socket, "create_connection", record_create_connection)

        response = client.get("/health")

    assert response.status_code == 200
    assert processes == [], f"外部プロセスの起動があった: {processes}"
    assert opened == [], f"外部ファイルの読み取りがあった: {opened}"
    assert connections == [], f"外部 HTTP があった: {connections}"


def test_design_document_declares_health_as_the_only_apps_api_route(
    design: dict[str, Any],
) -> None:
    """設計側でも `/health` が apps/api 所有の唯一の経路であることを確かめる（前提の固定）。"""
    paths: dict[str, Any] = design["paths"]
    owned: list[str] = []
    for path, operations in paths.items():
        candidates: dict[str, Any] = operations
        if any(
            isinstance(operation, dict)
            and cast("dict[str, Any]", operation).get("x-mod") == "apps/api"
            for operation in candidates.values()
        ):
            owned.append(str(path))
    assert owned == ["/health"], f"apps/api 所有の経路が /health 以外にある: {owned}"


def test_yaml_parser_is_available() -> None:
    """照合が「読めなかったので緑」にならないことを確かめる（pyyaml が dev に入っている）。"""
    parsed: Any = yaml.safe_load("a: 1\n")
    assert parsed == {"a": 1}
