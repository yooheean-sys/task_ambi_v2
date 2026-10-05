"""殻（`ui.shell`）と統合した起動点の検証（S-1・S-5・S-9・S-12・S-13・N-18 の SH 分）。

手段は NiceGUI の `user` フィクスチャ（ブラウザを立てない＝憲章 2.0.0 §8・FR-031）。

⚠ 規約 `contracts/ui-test-conventions.md` §3 を守る。
1. 使う名前は**モジュール先頭**で束ねる（`user` フィクスチャの後始末が `ui`・`ui.shell` を
   `sys.modules` から消すため、`ui.shell.X` の属性連鎖は使えない）。
2. 文字列経路の `monkeypatch.setattr("ui.shell.X", ...)` を使わない。
3. ページ関数を定義してよいのはテストモジュールと `ui.shell` だけである（§2）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
import uvicorn
from nicegui import ui as nui

from common.kernel.config import RootConfig
from ui import main as ui_main
from ui.pages import UiPageRegistration, select_pages
from ui.shell import NO_PAGES_MESSAGE, register_pages

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from nicegui.testing import User

APP_NAME = "task_ambi 試験"
"""殻に渡すアプリ名（実 `config.json` の値と取り違えない形）。"""

BASE_URL = "http://shell-contract.invalid:9999"
"""殻に渡す base URL（実運用の `.env` の値ではないことが一目で分かる形）。"""

WORDING_PATH = pathlib.Path(__file__).with_name("ui_shell_wording.json")
"""文言の一覧（`1dd3f09` の `apps/ui/src/ui/main.py` から抜き出した表示文字列）。"""

SLOW_RENDER_SECONDS = 3.5
"""S-13・J-8: NiceGUI の `response_timeout`（既定 3 秒）を**超える**描画の待ち。"""


@dataclass(frozen=True, slots=True)
class FakePage:
    """殻に渡す偽のページ部品（構造型 `UiPage` に構造で適合する）。

    Attributes:
        title: ページのタイトル。
        slug: 経路スラグ。
        render: 中身を描く非同期関数。
    """

    title: str
    slug: str
    render: Callable[[], Awaitable[None]]


def _labeling(text: str) -> Callable[[], Awaitable[None]]:
    """ラベル 1 つを描くだけの `render` を作る。

    Args:
        text: 描くラベルの文字。

    Returns:
        `UiPage.render` の形の非同期関数。
    """

    async def _render() -> None:
        nui.label(text)

    return _render


def _slow_labeling(text: str, seconds: float) -> Callable[[], Awaitable[None]]:
    """待ってからラベルを描く `render` を作る（S-13 の遅い読取の代わり）。

    Args:
        text: 描くラベルの文字。
        seconds: 描く前に待つ秒数。

    Returns:
        `UiPage.render` の形の非同期関数。
    """

    async def _render() -> None:
        await asyncio.sleep(seconds)
        nui.label(text)

    return _render


def _fake_page(title: str, slug: str, *, body: str | None = None) -> FakePage:
    """偽のページ部品を 1 つ作る。

    Args:
        title: タイトル。
        slug: 経路スラグ。
        body: 中身に描くラベル（省略時はタイトルに `の中身` を足したもの）。

    Returns:
        偽のページ部品。
    """
    return FakePage(title=title, slug=slug, render=_labeling(body or f"{title}の中身"))


def _fake_registration(module: str, title: str, slug: str) -> UiPageRegistration:
    """偽の登録を 1 件作る（S-5 の選別を実 `select_pages` に通すため）。

    Args:
        module: 所属モジュール名。
        title: タイトル。
        slug: 経路スラグ。

    Returns:
        偽の登録。
    """
    return UiPageRegistration(
        module=module, title=title, build_page=lambda: _fake_page(title, slug)
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


def _mappings_of(value: object) -> list[dict[object, object]]:
    """一覧の中の写像だけを取り出す（表の列・行を読むため）。

    Args:
        value: 調べる値。

    Returns:
        写像の並び。
    """
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast("dict[object, object]", item) for item in items if isinstance(item, dict)]


def _texts_on(user: User) -> set[str]:
    """画面の部品の文字を集める（T009 (8)・N-18 の照合範囲）。

    research R-10 の `ElementFilter` が見る属性（`text`・`label`・`placeholder`・`value`・
    `error-message`・`content`）に、選択肢と表の列名・セルを加えた集合である。

    Args:
        user: `user` フィクスチャ。

    Returns:
        画面に現れている文字の集合。
    """
    found: set[str] = set()
    for element in user.current_layout.descendants():
        props: dict[str, object] = dict(element.props)
        for key in ("text", "label", "placeholder", "value", "error-message"):
            found.update(_strings_of(props.get(key)))
        for attribute in ("text", "content"):
            found.update(_strings_of(getattr(element, attribute, None)))
        found.update(_strings_of(getattr(element, "options", None)))
        for column in _mappings_of(getattr(element, "columns", None)):
            found.update(_strings_of(column.get("label")))
        for row in _mappings_of(getattr(element, "rows", None)):
            for cell in row.values():
                found.update(_strings_of(cell))
    return found


def _link_titles(user: User) -> list[str]:
    """ナビゲーションのリンクの文字を、画面に現れた順に返す。

    Args:
        user: `user` フィクスチャ。

    Returns:
        `nui.link` の文字の並び。
    """
    return [
        element.text
        for element in user.current_layout.descendants()
        if isinstance(element, nui.link)
    ]


# --------------------------------------------------------------------------------------
# S-1・§4: 入口とナビゲーション
# --------------------------------------------------------------------------------------


async def test_shell_registers_every_page_with_its_slug(user: User) -> None:
    """S-1・§4: 登録した各ページが `/{slug}` で開き、見出しとナビゲーションが出る。"""
    pages = [_fake_page("AI 設定", "settings-ai"), _fake_page("トリガー", "triggers")]
    register_pages(pages, app_name=APP_NAME, base_url=BASE_URL)

    for page in pages:
        await user.open(f"/{page.slug}")
        await user.should_see(APP_NAME)
        await user.should_see(f"{page.title}の中身")
        assert _link_titles(user) == [item.title for item in pages], (
            "ナビゲーションのリンクが登録順に全ページ分出ていない"
        )


async def test_shell_entry_draws_the_first_page(user: User) -> None:
    """§4: 入口 `/` は、ページが 1 枚以上なら最初のページの中身を描く。"""
    register_pages(
        [_fake_page("AI 設定", "settings-ai"), _fake_page("トリガー", "triggers")],
        app_name=APP_NAME,
        base_url=BASE_URL,
    )

    await user.open("/")

    await user.should_see("AI 設定の中身")
    await user.should_not_see("トリガーの中身")


async def test_shell_entry_says_so_when_no_pages_are_enabled(user: User) -> None:
    """S-1・`002/U-1`・US3 AS-4: 0 枚なら案内と接続先を出す（駆動 `shell:zero-pages`）。"""
    register_pages((), app_name=APP_NAME, base_url=BASE_URL)

    await user.open("/")

    texts = _texts_on(user)
    assert APP_NAME in texts
    assert NO_PAGES_MESSAGE in texts
    assert f"接続先 app: {BASE_URL}" in texts
    assert not _link_titles(user), "ページ 0 枚でリンクが出ている"


# --------------------------------------------------------------------------------------
# S-5: 有効化一覧による選別（実 `select_pages` を通す）
# --------------------------------------------------------------------------------------


async def test_shell_shows_only_the_pages_of_enabled_modules(user: User) -> None:
    """S-5・US3 AS-5: settings だけを有効にすると「トリガー」のリンクが無い。"""
    registrations = (
        _fake_registration("settings", "AI 設定", "settings-ai"),
        _fake_registration("settings", "プロジェクト", "settings-projects"),
        _fake_registration("trigger", "トリガー", "triggers"),
    )

    selected = select_pages(registrations, ["settings"])
    register_pages(
        [registration.build_page() for registration in selected],
        app_name=APP_NAME,
        base_url=BASE_URL,
    )

    await user.open("/")

    assert _link_titles(user) == ["AI 設定", "プロジェクト"]


# --------------------------------------------------------------------------------------
# S-13・J-8: `response_timeout` を超える描画
# --------------------------------------------------------------------------------------


async def test_shell_opens_before_a_slow_page_body_is_ready(user: User) -> None:
    """S-13・FR-012a・J-8: 3 秒を超えて待つページでも誤りの画面にならず、中身が後から出る。

    殻は共通の見出しを描いた後に接続を待つので、ページの組み立てが `response_timeout`
    （既定 3 秒）にかからない。`user.open` が 200 以外なら `assert` で落ちる。
    """
    slow = FakePage(
        title="遅いページ",
        slug="slow",
        render=_slow_labeling("遅い中身", SLOW_RENDER_SECONDS),
    )
    register_pages([slow], app_name=APP_NAME, base_url=BASE_URL)

    await user.open("/slow")

    await user.should_see(APP_NAME)
    # 3.5 秒の待ちを越えるだけの再試行（1 回 0.1 秒）を与える
    await user.should_see("遅い中身", retries=60)


# --------------------------------------------------------------------------------------
# S-12: import しただけでは経路を登録しない
# --------------------------------------------------------------------------------------

_IMPORT_ONLY_PROBE = """
import json
import sys

from nicegui import app
from starlette.routing import Route


def page_routes() -> list[str]:
    return sorted(
        route.path
        for route in app.routes
        if isinstance(route, Route) and not route.path.startswith("/_nicegui")
    )


before = page_routes()
import ui.pages  # noqa: F401
import ui.shell  # noqa: F401
import ui.main  # noqa: F401
after = page_routes()
print(json.dumps({"before": before, "after": after}))
sys.exit(0 if before == after else 1)
"""


def test_shell_import_does_not_register_any_page_route() -> None:
    """S-12・FR-023: `ui.pages`・`ui.shell`・`ui.main` の import で経路が 1 件も増えない。

    別プロセスで確かめる（同じプロセスでは、ほかのテストが既に import しているため
    「import した瞬間」を観測できない）。
    """
    completed = subprocess.run(
        [sys.executable, "-c", _IMPORT_ONLY_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        f"import だけで経路が増えた: {completed.stdout}\n{completed.stderr}"
    )
    observed: object = json.loads(completed.stdout)
    assert isinstance(observed, dict)


# --------------------------------------------------------------------------------------
# S-9・FR-018: 起動の失敗で待受を始めない
# --------------------------------------------------------------------------------------


class _RecordingRun:
    """`uvicorn.run` の差し替え（呼ばれた回数だけを数える）。"""

    def __init__(self) -> None:
        """初期化する。"""
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> None:
        """呼ばれた回数を数える。"""
        self.calls += 1


class _StartupFailureError(Exception):
    """起動の前段が投げる例外の代わり（`ConfigurationError`・`WiringError` の位置）。"""


@pytest.mark.parametrize("failing", ["load_root_config", "create_app"])
def test_shell_main_does_not_listen_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    """S-9・FR-018: ルート設定の読みか app の組み立てで例外なら `uvicorn.run` に到達しない。"""
    recording = _RecordingRun()
    monkeypatch.setattr(uvicorn, "run", recording)

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise _StartupFailureError(failing)

    monkeypatch.setattr(ui_main, failing, _raise)

    with pytest.raises(_StartupFailureError):
        ui_main.main()

    assert recording.calls == 0, "起動が失敗したのに待受を始めた"


# --------------------------------------------------------------------------------------
# FR-017a・FR-021: apps/ui はログ基盤と記憶に触らない
# --------------------------------------------------------------------------------------

FORBIDDEN_IN_UI_SOURCE = ("LoggerFactory", "storage_secret", "app.storage")
"""FR-017a・FR-021: apps/ui の src の**コード**に現れてはならない名前。

⚠ 素朴な文字列走査にしない —— 「これを呼ばない」と書いた docstring そのものが引っかかる
（実測）。**コードの識別子だけ**を集めて照合する。
"""


def _code_identifiers(source: str) -> set[str]:
    """コードに現れる識別子を集める（docstring・注記・文字列は見ない）。

    集めるのは変数名・属性名・点で連ねた属性の鎖・キーワード引数の名前である。

    Args:
        source: Python のソース。

    Returns:
        識別子の集合。
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
            chain: list[str] = [node.attr]
            current: ast.expr = node.value
            while isinstance(current, ast.Attribute):
                chain.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                chain.append(current.id)
                found.add(".".join(reversed(chain)))
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
    return found


def test_ui_source_does_not_configure_logging_or_use_storage() -> None:
    """FR-017a・FR-021・憲章 2.0.0 §6: apps/ui の src のコードに 3 つの名前が 0 件。"""
    source_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "ui"
    offenders: list[str] = []
    for path in sorted(source_dir.glob("*.py")):
        identifiers = _code_identifiers(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.name}: {token}" for token in FORBIDDEN_IN_UI_SOURCE if token in identifiers
        )

    assert not offenders, f"apps/ui の src に禁止の名前がある: {offenders}"


# --------------------------------------------------------------------------------------
# N-18（SH 分）: 文言の完全一致
# --------------------------------------------------------------------------------------


async def _drive_zero_pages(user: User) -> None:
    """`shell:zero-pages` の駆動（0 枚の入口を開く）。

    Args:
        user: `user` フィクスチャ。
    """
    register_pages((), app_name=APP_NAME, base_url=BASE_URL)
    await user.open("/")


SCENARIOS: dict[str, Callable[[User], Awaitable[None]]] = {
    "shell:zero-pages": _drive_zero_pages,
}
"""文言の一覧の `scenario` → その文言を画面に出す駆動関数。"""


def _wording() -> list[dict[str, object]]:
    """文言の一覧を読む。

    Returns:
        一覧の各件。
    """
    loaded: object = json.loads(WORDING_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    entries: list[dict[str, object]] = []
    for item in loaded:  # pyright: ignore[reportUnknownVariableType]  # JSON は型を持たない
        assert isinstance(item, dict)
        entries.append(item)  # pyright: ignore[reportUnknownArgumentType]  # 同上
    return entries


def _matches(entry: dict[str, object], texts: Sequence[str]) -> bool:
    """1 件の文言が画面の文字に完全一致で現れるかを見る。

    Args:
        entry: 文言の一覧の 1 件。
        texts: 画面の部品の文字。

    Returns:
        現れていれば True。
    """
    pattern = entry["pattern"]
    if isinstance(pattern, str):
        return any(re.fullmatch(pattern, text) for text in texts)
    return entry["text"] in texts


def test_shell_wording_entries_all_have_a_driving_scenario() -> None:
    """N-18・J-22: 一覧の全件の `scenario` が駆動関数の表にある（空振りを防ぐ）。"""
    entries = _wording()

    assert entries, "文言の一覧が空である"
    missing = sorted({str(entry["scenario"]) for entry in entries} - set(SCENARIOS))

    assert not missing, f"駆動関数の無い scenario がある: {missing}"
    assert all(str(entry["page"]) == "shell" for entry in entries)
    assert all(str(entry["scenario"]).startswith("shell:") for entry in entries)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
async def test_shell_wording_appears_verbatim(user: User, scenario: str) -> None:
    """N-18・FR-007・J-22: 一覧の各文言が部品の文字として完全一致で現れる。"""
    entries = [entry for entry in _wording() if entry["scenario"] == scenario]
    await SCENARIOS[scenario](user)

    texts = sorted(_texts_on(user))
    missing = [str(entry["id"]) for entry in entries if not _matches(entry, texts)]

    assert not missing, f"{scenario} で出ない文言がある: {missing}\n画面の文字: {texts}"


def test_shell_app_name_is_not_hardcoded_in_the_wording_list() -> None:
    """J-22: アプリ名はルート設定から来るので、一覧に値として載っていない（U-1）。"""
    texts = {str(entry["text"]) for entry in _wording()}

    assert RootConfig().app_name not in texts
