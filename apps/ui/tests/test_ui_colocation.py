"""同居（統合した起動点）の検証（S-7・S-8・S-10・S-11・S-1 の同居分。FR-034）。

`create_ui_app` が組んだ **1 つの app** で、`/health` と `/ui/...` の両方が応答することを
`TestClient` で確かめる。

⚠ 準備は `apps/api/tests/_support` を **import だけで**再利用する（規約 §5・ND-15。`_support` に
1 行も追記しない）。`apps/ui/tests/conftest.py` は作らない（`015` §2）ので、準備は本ファイルの
ローカル fixture に閉じる。

⚠ 各テストは `nicegui_reset_globals()` の中で組み、`create_ui_app` を **1 テストで 1 回だけ**
呼ぶ（規約 §5）。NiceGUI は大域状態（経路・既定の classes・クライアント）を持つため。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from _support.cluster_env import assert_env_path_under, isolate_env
from _support.cluster_stores import make_temp_area_a, make_temp_area_b
from _support.composition import make_root_config
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubKickoffRunner,
    StubVaultReader,
)
from fastapi.testclient import TestClient
from nicegui.testing.general import nicegui_reset_globals
from starlette.routing import Mount

from api import create_app
from common.interfaces.board import BoardReader
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.vault import VaultReader
from settings import SettingsEnv
from trigger import TriggerEnv
from trigger.service import TriggerService
from ui.main import create_ui_app

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from types import TracebackType

    import httpx
    from fastapi import FastAPI

    from api.wiring import ProtocolOverrides


class ColocationClient(Protocol):
    """同居テストが使う `TestClient` の面。

    `_support.composition.HttpClient` に **`follow_redirects`** を足したものである（`/ui` の 307 を
    見るのに要る）。`_support` には 1 行も追記しない（ND-15）ので、本ファイルに置く。starlette の
    `testclient` は型検査時に `httpx2` を import する作りで、`httpx2` を入れていない本リポジトリでは
    応答の型が解決できない（`_support` の同名 Protocol と同じ理由）。
    """

    def get(self, url: str, *, follow_redirects: bool = True) -> httpx.Response:
        """GET 要求を送る。"""
        ...

    def __enter__(self) -> ColocationClient:
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


def _client(app: FastAPI) -> ColocationClient:
    """app のクライアントを返す（`with` で lifespan を通す）。

    Args:
        app: 対象の app。

    Returns:
        同居テストが使う面だけを型として固定したクライアント。
    """
    return TestClient(app)


STUB_BASE_URL = "http://colocation.invalid:9999"
"""画面が読む接続先の止め木（実運用の `.env` を読ませない）。"""

TIMER_INTERVAL_SEC = 60
"""一時領域 B に置く `interval` トリガーの秒数。

実物の `config.json`（`min_interval_sec=60`・`misfire_grace_sec=30`）のまま通る最小の値である。
同居の観点は常駐の**起動と後始末**なので、発火そのものは観測しない（一時 `config.json` へ
向けなくてよい＝ `point_config_at` を使わない）。
"""

UI_PAGE_PATHS = ("/ui/settings-ai", "/ui/settings-projects", "/ui/triggers")
"""公開 URL（ui-shell §4）。"""


@pytest.fixture(autouse=True)
def colocation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse）＋画面の接続先の止め木。

    Returns:
        差し替え先の一時 `.env` の絶対パス。
    """
    monkeypatch.setenv("APP_BASE_URL", STUB_BASE_URL)
    return isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def area_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, colocation_env: Path) -> Path:
    """settings が読む一時領域 A（`build()` の前に指し直す）。

    Returns:
        `SETTINGS_ROOT` に渡した値。
    """
    area = make_temp_area_a(tmp_path)
    monkeypatch.setenv("SETTINGS_ROOT", str(area.root))
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=colocation_env)
    return area.root


@pytest.fixture
def area_b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, colocation_env: Path) -> Path:
    """trigger が読む一時領域 B と一時 DL フォルダ（`build()` の前に指し直す）。

    Returns:
        `TRIGGER_ROOT` に渡した値。
    """
    area = make_temp_area_b(tmp_path, timer_interval_sec=TIMER_INTERVAL_SEC)
    monkeypatch.setenv("TRIGGER_ROOT", str(area.root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(area.downloads_dir))
    assert_env_path_under(TriggerEnv, "TRIGGER_ROOT", tmp_path, env_file=colocation_env)
    assert_env_path_under(TriggerEnv, "DOWNLOADS_DIR", tmp_path, env_file=colocation_env)
    return area.root


@pytest.fixture
def nicegui_globals() -> Iterator[None]:
    """NiceGUI の大域状態を、このテストの中だけに閉じる（規約 §5）。

    Yields:
        なし。
    """
    with nicegui_reset_globals():
        yield


@pytest.fixture
def stub_overrides() -> ProtocolOverrides:
    """settings と trigger が要求する契約のスタブ（registry の `requires` と過不足なく一致）。

    Returns:
        契約の型 → スタブ実装。
    """
    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)
    vault_reader: VaultReader = StubVaultReader(log)
    kickoff_runner: KickoffRunner = StubKickoffRunner(log)
    return {
        BoardReader: board_reader,
        VaultReader: vault_reader,
        KickoffRunner: kickoff_runner,
    }


def _settings_overrides(overrides: ProtocolOverrides) -> ProtocolOverrides:
    """settings だけを有効にするときの override（`KickoffRunner` を外す）。

    Args:
        overrides: 3 件のスタブ。

    Returns:
        `BoardReader`・`VaultReader` だけの写像（余分な override は
        `ExtraneousOverrideError` になる）。
    """
    return {key: value for key, value in overrides.items() if key is not KickoffRunner}


def _trigger_overrides(overrides: ProtocolOverrides) -> ProtocolOverrides:
    """trigger だけを有効にするときの override（`KickoffRunner` だけ）。

    Args:
        overrides: 3 件のスタブ。

    Returns:
        `KickoffRunner` だけの写像。
    """
    return {key: value for key, value in overrides.items() if key is KickoffRunner}


def _mount_paths(app: FastAPI) -> list[str]:
    """親 app のトップレベルの `Mount` の経路を返す。

    Args:
        app: 対象の app。

    Returns:
        `Mount` の経路の並び（並び順のまま）。
    """
    return [route.path for route in app.routes if isinstance(route, Mount)]


def _top_level_paths(app: FastAPI) -> list[str]:
    """親 app のトップレベルの経路を返す（入れ子は辿らない）。

    Args:
        app: 対象の app。

    Returns:
        経路の並び。

    Note:
        `collect_api_routes` を使わない —— UI の `Mount` は `APIRoute` ではないので、
        入れ子を辿る数え方では差分そのものが見えない（T014 (1)）。
    """
    return [getattr(route, "path", "") for route in app.routes]


# --------------------------------------------------------------------------------------
# S-7・SC-006: 親 app に足されるのは `/ui` の 1 件だけ
# --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("area_a", "nicegui_globals")
def test_s7_mounting_the_ui_adds_only_one_route_and_keeps_the_openapi(
    stub_overrides: ProtocolOverrides,
) -> None:
    """S-7・SC-006・FR-016: OpenAPI 文書が UI の無い app と全体で一致し、差分は `/ui` だけ。"""
    config = make_root_config(enabled_modules=["settings"])
    overrides = _settings_overrides(stub_overrides)
    plain = create_app(config=config, overrides=overrides)

    with_ui = create_ui_app(config=config, overrides=overrides)

    assert with_ui.openapi() == plain.openapi(), "UI を載せると OpenAPI 文書が変わる"
    added = [path for path in _top_level_paths(with_ui) if path not in _top_level_paths(plain)]
    assert added == ["/ui"], f"親 app に足された経路が `/ui` の 1 件だけでない: {added}"
    assert _mount_paths(with_ui) == ["/ui"]
    assert [middleware.cls for middleware in with_ui.user_middleware] == [
        middleware.cls for middleware in plain.user_middleware
    ], "UI を載せると親 app のミドルウェアが変わる"
    assert set(with_ui.exception_handlers) == set(plain.exception_handlers), (
        "UI を載せると親 app の例外ハンドラが変わる"
    )


# --------------------------------------------------------------------------------------
# S-8・SC-010・FR-019: 常駐の起動と後始末
# --------------------------------------------------------------------------------------


def _counting(
    method: Callable[[TriggerService], None], counter: list[int]
) -> Callable[[TriggerService], None]:
    """常駐の起動・停止を数える包みを作る（実処理はそのまま呼ぶ）。

    Args:
        method: 元のメソッド。
        counter: 呼ばれた回数を積む先。

    Returns:
        包んだメソッド。
    """

    def _wrapped(service: TriggerService) -> None:
        counter.append(1)
        method(service)

    return _wrapped


@pytest.mark.usefixtures("area_b", "nicegui_globals")
def test_s8_trigger_scheduler_starts_and_stops_once(
    monkeypatch: pytest.MonkeyPatch, stub_overrides: ProtocolOverrides
) -> None:
    """S-8・SC-010・FR-019: `run_with` が包んだ lifespan でも常駐が 1 回ずつ起動・停止する。"""
    starts: list[int] = []
    stops: list[int] = []
    monkeypatch.setattr(TriggerService, "start", _counting(TriggerService.start, starts))
    monkeypatch.setattr(TriggerService, "stop", _counting(TriggerService.stop, stops))
    app = create_ui_app(
        config=make_root_config(enabled_modules=["trigger"]),
        overrides=_trigger_overrides(stub_overrides),
    )

    with _client(app) as client:
        assert client.get("/health").status_code == 200
        assert len(starts) == 1, f"常駐の起動が 1 回でない: {len(starts)}"
        assert not stops

    assert len(starts) == 1
    assert len(stops) == 1, f"常駐の後始末が 1 回でない: {len(stops)}"


# --------------------------------------------------------------------------------------
# S-10・FR-020: 有効化一覧が app と UI で同じ
# --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("area_a", "nicegui_globals")
def test_s10_the_same_enabled_modules_drive_the_app_and_the_ui(
    stub_overrides: ProtocolOverrides,
) -> None:
    """S-10・FR-020: settings だけを有効にすると、`/health` も UI のページも settings だけ。"""
    app = create_ui_app(
        config=make_root_config(enabled_modules=["settings"]),
        overrides=_settings_overrides(stub_overrides),
    )

    with _client(app) as client:
        payload: object = client.get("/health").json()
        assert isinstance(payload, dict)
        assert payload["modules"] == ["settings"]  # pyright: ignore[reportUnknownArgumentType]  # JSON は型を持たない
        assert client.get("/ui/settings-ai").status_code == 200
        assert client.get("/ui/settings-projects").status_code == 200
        assert client.get("/ui/triggers").status_code == 404, (
            "有効化していないモジュールのページが応答している"
        )


# --------------------------------------------------------------------------------------
# S-11・SC-004・US3 AS-1: 1 つの待受で `/health` と 3 ページ
# --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("area_a", "area_b", "nicegui_globals")
def test_s11_one_app_serves_health_and_every_ui_page(
    stub_overrides: ProtocolOverrides,
) -> None:
    """S-11・SC-004・US3 AS-1: 同じ app で `/health`・`/ui/`・3 ページが 200、`/ui` が 307。"""
    app = create_ui_app(
        config=make_root_config(enabled_modules=["settings", "trigger"]),
        overrides=stub_overrides,
    )

    with _client(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ui/").status_code == 200
        for path in UI_PAGE_PATHS:
            assert client.get(path).status_code == 200, f"{path} が 200 でない"
        redirect = client.get("/ui", follow_redirects=False)
        assert redirect.status_code == 307, f"`/ui` が 307 でない: {redirect.status_code}"
        assert redirect.headers["location"].endswith("/ui/")


# --------------------------------------------------------------------------------------
# S-1 の同居分: 有効化一覧が空でも入口が開く
# --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("nicegui_globals")
def test_s1_the_entry_opens_even_with_no_enabled_modules() -> None:
    """S-1・US3 AS-4: 有効化一覧が空でも `/ui/` が 200 で応答する（override も 0 件）。"""
    app = create_ui_app(config=make_root_config(enabled_modules=[]))

    with _client(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ui/").status_code == 200
        for path in UI_PAGE_PATHS:
            assert client.get(path).status_code == 404
