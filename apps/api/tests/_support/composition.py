"""統合テストの共通の準備手段（FR-036）。実配線 `create_app` を**そのまま呼ぶ**薄い包み。

**手配線をしない**（憲章 §8・D14-e）。ここが `create_app` を経由しなくなった時点で、
統合テストは「実際の composition root の噛み合い」を検証しなくなる。
`integration/conftest.py` ではなく `_support/` に置くのは、クラスタ用サブディレクトリ
（`cluster_NN_<slug>/`）からも同じ import 文で使えるようにするため（research R-16）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from api.app import create_app
from common.kernel.config import LogConfig, RootConfig

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import TracebackType

    import httpx
    from fastapi import FastAPI
    from starlette.routing import BaseRoute

    from api.wiring import ModuleRegistration, ProtocolOverrides


class HttpClient(Protocol):
    """統合テストが使う `TestClient` の面。

    starlette の `testclient` は型検査時に `httpx2` を import する作りで（実測: 当該 module の
    `if TYPE_CHECKING: import httpx2 as httpx`）、`httpx2` を入れていない本リポジトリでは応答の型が
    解決できない。実行時に返るのは `httpx.Response`（`httpx` は `000` が dev グループに入れている）
    なので、テストが使う面だけをここで型として固定する。**依存は増やさない**。
    """

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """GET 要求を送る。"""
        ...

    def request(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """メソッドを**値で**受けて要求を送る（表駆動のテストが使う面）。"""
        ...

    def post(
        self,
        url: str,
        *,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """POST 要求を送る。"""
        ...

    def put(
        self,
        url: str,
        *,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """PUT 要求を送る。"""
        ...

    def patch(
        self,
        url: str,
        *,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """PATCH 要求を送る。"""
        ...

    def delete(self, url: str, *, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """DELETE 要求を送る。"""
        ...

    def __enter__(self) -> HttpClient:
        """lifespan を通してクライアントを返す。"""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """lifespan の終了処理を通す。"""
        ...


class ClientFactory(Protocol):
    """実配線で `TestClient` を組む呼び出し形（`integration/conftest.py` が fixture で配る）。"""

    def __call__(
        self,
        *,
        enabled_modules: Sequence[str] | None = None,
        overrides: ProtocolOverrides | None = None,
        config: RootConfig | None = None,
        registrations: Sequence[ModuleRegistration] | None = None,
    ) -> HttpClient:
        """実配線（`create_app`）で組んだクライアントを返す。"""
        ...


DEFAULT_APP_NAME = "task_ambi"
DEFAULT_ENV_LABEL = "test"


def make_root_config(
    *,
    app_name: str = DEFAULT_APP_NAME,
    env_label: str = DEFAULT_ENV_LABEL,
    enabled_modules: Sequence[str] | None = None,
    correlation_header: str | None = None,
) -> RootConfig:
    """テスト用のルート設定を組む（x3_notes §2 の階層 ⑧＝ build 引数。FR-004）。

    ルート `config.json` を書き換えずに値を差し替えるための唯一の手段。

    Args:
        app_name: アプリ名（CFG-14）。
        env_label: 環境ラベル（CFG-15）。
        enabled_modules: 有効 MOD 一覧（CFG-20）。`None` なら空。
        correlation_header: 相関 ID ヘッダ名（CFG-18）。`None` なら既定のまま。

    Returns:
        組んだ `RootConfig`。
    """
    log = (
        LogConfig()
        if correlation_header is None
        else LogConfig(correlation_header=correlation_header)
    )
    return RootConfig(
        app_name=app_name,
        env_label=env_label,
        enabled_modules=list(enabled_modules or ()),
        log=log,
    )


def build_test_app(
    *,
    enabled_modules: Sequence[str] | None = None,
    overrides: ProtocolOverrides | None = None,
    config: RootConfig | None = None,
    registrations: Sequence[ModuleRegistration] | None = None,
) -> FastAPI:
    """実配線で FastAPI アプリを組む。

    Args:
        enabled_modules: 有効化するモジュール名。
        overrides: 契約の型 → 差し替える実装。
        config: ルート設定。`None` なら `make_root_config()` の既定。
        registrations: 偽の登録集合。

    Returns:
        `create_app` が返した `FastAPI`。
    """
    return create_app(
        enabled_modules=enabled_modules,
        overrides=overrides,
        config=config if config is not None else make_root_config(),
        registrations=registrations,
    )


def build_test_client(
    *,
    enabled_modules: Sequence[str] | None = None,
    overrides: ProtocolOverrides | None = None,
    config: RootConfig | None = None,
    registrations: Sequence[ModuleRegistration] | None = None,
) -> HttpClient:
    """実配線で組んだアプリのクライアントを返す（`with` で lifespan を通す）。

    Args:
        enabled_modules: 有効化するモジュール名。
        overrides: 契約の型 → 差し替える実装。
        config: ルート設定。
        registrations: 偽の登録集合。

    Returns:
        `TestClient`（型は `HttpClient` として扱う）。
    """
    return TestClient(
        build_test_app(
            enabled_modules=enabled_modules,
            overrides=overrides,
            config=config,
            registrations=registrations,
        )
    )


def collect_api_routes(app: FastAPI) -> tuple[APIRoute, ...]:
    """アプリが持つ `APIRoute` を**入れ子ごと**集める（SC-002 の「公開経路の数」の数え方）。

    FastAPI 0.141 の `include_router` は取り込んだ router を `_IncludedRouter` に包み、
    `app.routes` を平坦化しない（2026-08-21 実測。research R-09 が想定した「`app.routes` を
    1 段見る」数え方では取り込んだ経路を数え落とす）。`routes` と `original_router.routes` を
    たどって数える。自動 doc 経路（`/openapi.json`・`/docs`・`/redoc`）は `APIRoute` では
    ないので数に入らない。

    Args:
        app: 対象の FastAPI アプリ。

    Returns:
        見つかった `APIRoute` のタプル。
    """
    found: list[APIRoute] = []
    _walk_routes(app.routes, found)
    return tuple(found)


def _walk_routes(routes: Sequence[BaseRoute], found: list[APIRoute]) -> None:
    """`routes` を再帰的にたどって `APIRoute` を `found` に集める。

    Args:
        routes: たどる経路の並び。
        found: 収集先。
    """
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        nested: Any = getattr(route, "routes", None)
        if nested is None:
            inner: Any = getattr(route, "original_router", None)
            nested = None if inner is None else getattr(inner, "routes", None)
        if nested is not None:
            _walk_routes(cast("Sequence[BaseRoute]", nested), found)
