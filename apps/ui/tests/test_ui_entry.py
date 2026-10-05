"""入口とページ選別（U-1・U-5・S-1・FR-020。旧 `test_main_apptest.py` の移し先）。

U-1 系は NiceGUI の `user` フィクスチャで、U-5 系は素の Python で確かめる（test-mapping E）。
規約は `contracts/ui-test-conventions.md` §3（モジュール先頭で束ねる・文字列経路の差し替えを
使わない・属性連鎖を使わない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest
import uvicorn
from fastapi import FastAPI
from nicegui import ui as nui

from common.kernel.config import RootConfig
from ui import main as ui_main
from ui.config import UiEnvSettings
from ui.pages import UiPageRegistration, select_pages
from ui.shell import NO_PAGES_MESSAGE, register_pages

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from nicegui.testing import User

APP_NAME = "task_ambi 試験"
"""殻に渡すアプリ名。"""

OTHER_APP_NAME = "別名アプリ"
"""U-1: アプリ名がルート設定から来ることを示すための、実 `config.json` とは違う名前。"""


@dataclass(frozen=True, slots=True)
class FakePage:
    """偽のページ部品（構造型 `UiPage` に構造で適合する）。

    Attributes:
        title: ページのタイトル。
        slug: 経路スラグ。
        render: 中身を描く非同期関数。
    """

    title: str
    slug: str
    render: Callable[[], Awaitable[None]]


async def _nothing() -> None:
    """何も描かない `render`（選別だけを見るテストで使う）。"""


def _fake_page(title: str, slug: str) -> FakePage:
    """偽のページ部品を 1 つ作る。

    Args:
        title: タイトル。
        slug: 経路スラグ。

    Returns:
        偽のページ部品。
    """
    return FakePage(title=title, slug=slug, render=_nothing)


def _fake_registration(module: str, title: str) -> UiPageRegistration:
    """偽の登録を 1 件作る。

    ⚠ 旧 `test_main_apptest.py` は `build_page=lambda: object()` と書いていたが、`build_page` の
    注釈が `Callable[[], UiPage]` になった（data-model §1）ため `object()` では型が通らない
    （design-sync B-10）。**観点（選別の意味）は変えず**、返す値だけを構造型に適合させる。

    Args:
        module: 所属モジュール名。
        title: タイトル。

    Returns:
        偽の登録。
    """
    return UiPageRegistration(
        module=module, title=title, build_page=lambda: _fake_page(title, module)
    )


def _strings_of(value: object) -> list[str]:
    """任意の値から、表示になりうる文字だけを取り出す。

    UI 基盤の属性（`props`・`options`・表の行）は型を持たないので、narrow をこの 1 箇所に閉じる。

    Args:
        value: 調べる値。

    Returns:
        空でない文字の並び（入れ子の一覧・写像は中身まで辿る）。
    """
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        # JSON と UI 基盤の属性は型を持たない。narrow は本関数に閉じる
        items = cast("list[object]", value)
        return [text for item in items for text in _strings_of(item)]
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        return [text for item in mapping.values() for text in _strings_of(item)]
    return []


def _texts_on(user: User) -> set[str]:
    """画面の部品の文字を集める（完全一致の表明に使う）。

    Args:
        user: `user` フィクスチャ。

    Returns:
        画面に現れている文字の集合。
    """
    found: set[str] = set()
    for element in user.current_layout.descendants():
        props: dict[str, object] = dict(element.props)
        for key in ("text", "label", "value"):
            found.update(_strings_of(props.get(key)))
        found.update(_strings_of(getattr(element, "text", None)))
    return found


# --------------------------------------------------------------------------------------
# U-1・S-1・US3 AS-4: 0 枚の入口
# --------------------------------------------------------------------------------------


async def test_u1_entry_says_so_with_zero_pages(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U-1・S-1・US3 AS-4: 0 枚でも入口が開き、案内と導出された接続先が完全一致で出る。"""
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "8000")

    register_pages((), app_name=APP_NAME, base_url=UiEnvSettings().resolved_app_base_url)
    await user.open("/")

    texts = _texts_on(user)
    assert APP_NAME in texts
    assert NO_PAGES_MESSAGE in texts
    assert "接続先 app: http://127.0.0.1:8000" in texts


# --------------------------------------------------------------------------------------
# U-1・FR-020: 統合した起動点の組み立て（ui-shell §2）
# --------------------------------------------------------------------------------------


class _RecordingCreateApp:
    """`create_app` の差し替え（受けた引数を記録し、素の `FastAPI` を返す）。"""

    def __init__(self) -> None:
        """初期化する。"""
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FastAPI:
        """受けた引数を記録して素の app を返す。"""
        self.calls.append(kwargs)
        return FastAPI()


class _RecordingRegisterPages:
    """`register_pages` の差し替え（受けたページと引数を記録する）。"""

    def __init__(self) -> None:
        """初期化する。"""
        self.pages: list[object] = []
        self.kwargs: dict[str, object] = {}
        self.calls = 0

    def __call__(self, pages: Sequence[object], **kwargs: object) -> None:
        """受けたページと引数を記録する。"""
        self.calls += 1
        self.pages = list(pages)
        self.kwargs = kwargs


class _RecordingRunWith:
    """`nui.run_with` の差し替え（受けた引数を記録する）。"""

    def __init__(self) -> None:
        """初期化する。"""
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        """受けた引数を記録する。"""
        self.calls.append((args, kwargs))


def test_u1_create_ui_app_follows_the_six_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """ui-shell §2・FR-020: 渡した設定 1 つが app の組み立てとページ選別の両方に使われる。

    アプリ名は**ルート設定から**来る（U-1）。`run_with` は 1 回だけで、`/ui` に載せ、
    `storage_secret` を渡さない（FR-021）。
    """
    create_app = _RecordingCreateApp()
    register = _RecordingRegisterPages()
    run_with = _RecordingRunWith()
    monkeypatch.setattr(ui_main, "create_app", create_app)
    monkeypatch.setattr(ui_main, "register_pages", register)
    monkeypatch.setattr(nui, "run_with", run_with)
    monkeypatch.setattr(
        ui_main,
        "UI_PAGE_REGISTRATIONS",
        (
            _fake_registration("settings", "AI 設定"),
            _fake_registration("settings", "プロジェクト"),
            _fake_registration("trigger", "トリガー"),
        ),
    )
    config = RootConfig(app_name=OTHER_APP_NAME, enabled_modules=["trigger"])

    app = ui_main.create_ui_app(config=config)

    assert len(create_app.calls) == 1
    assert create_app.calls[0]["config"] is config, "同じ設定が `create_app` に渡っていない"
    assert register.calls == 1
    assert [getattr(page, "title", None) for page in register.pages] == ["トリガー"]
    assert register.kwargs["app_name"] == OTHER_APP_NAME
    assert len(run_with.calls) == 1
    args, kwargs = run_with.calls[0]
    assert args == (app,)
    assert kwargs["mount_path"] == "/ui"
    assert kwargs["title"] == OTHER_APP_NAME
    assert kwargs["show_welcome_message"] is False
    assert "storage_secret" not in kwargs, "`storage_secret` を渡している（FR-021）"


def test_u1_create_ui_app_reads_the_root_config_once_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ui-shell §2 の段 1: `config` を渡さなければ `load_root_config()` を 1 回だけ読む。"""
    calls: list[int] = []
    config = RootConfig(app_name=OTHER_APP_NAME, enabled_modules=[])

    def _load() -> RootConfig:
        calls.append(1)
        return config

    create_app = _RecordingCreateApp()
    monkeypatch.setattr(ui_main, "load_root_config", _load)
    monkeypatch.setattr(ui_main, "create_app", create_app)
    monkeypatch.setattr(ui_main, "register_pages", _RecordingRegisterPages())
    monkeypatch.setattr(nui, "run_with", _RecordingRunWith())

    ui_main.create_ui_app()

    assert len(calls) == 1
    assert create_app.calls[0]["config"] is config


def test_u1_main_listens_on_the_env_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """ui-shell §2: `main()` は `UiEnvSettings` の `app_host`・`app_port` で待受を始める。"""
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "8123")
    created = FastAPI()
    recorded: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _create_ui_app(**_kwargs: object) -> FastAPI:
        return created

    def _run(*args: Any, **kwargs: Any) -> None:
        recorded.append((args, kwargs))

    monkeypatch.setattr(ui_main, "create_ui_app", _create_ui_app)
    monkeypatch.setattr(uvicorn, "run", _run)

    ui_main.main()

    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args == (created,)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8123


# --------------------------------------------------------------------------------------
# U-5 系（旧 `test_main_apptest.py` :83・:93・:100・:108 を中身を変えずに写す）
# --------------------------------------------------------------------------------------


def test_u5_pages_are_filtered_by_enabled_modules() -> None:
    """U-5・FR-034: 偽のページ 2 件で `enabled_modules` が片方だけなら対象が 1 件になる。"""
    first = _fake_registration("alpha", "Alpha")
    second = _fake_registration("beta", "Beta")

    selected = select_pages((first, second), ["alpha"])

    assert selected == (first,)


def test_u5_no_enabled_modules_selects_nothing() -> None:
    """FR-034: 有効 MOD 一覧が空ならページは 0 枚。"""
    page = _fake_registration("alpha", "Alpha")

    assert select_pages((page,), []) == ()


def test_u5_selection_keeps_the_registration_order() -> None:
    """FR-034: 選別は元の並びを保つ（並べ替えない）。"""
    first = _fake_registration("alpha", "Alpha")
    second = _fake_registration("beta", "Beta")

    assert select_pages((first, second), ["beta", "alpha"]) == (first, second)


def test_u5_unknown_enabled_module_selects_nothing() -> None:
    """有効 MOD 一覧に登録の無い名前があってもページ選別は落ちない（落とすのは apps/api 側）。"""
    page = _fake_registration("alpha", "Alpha")

    assert select_pages((page,), ["gamma"]) == ()
