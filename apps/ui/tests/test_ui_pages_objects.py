"""登録の閉包が返すページ部品の実値（旧 `test_ui_pages_apptest.py` の 5 本の移し先）。

移行前は `st.Page` が script run context の外では中空になるため `AppTest.from_function` の中で
観測していた（WD-23）。移行後のページ部品は素の frozen dataclass なので、`title`・`slug` は
そのまま読める（data-model §2）。`render` が届ける base URL だけは、`user` フィクスチャで
実際に描いて確かめる。

⚠ **止め木（`APP_BASE_URL`）は本ファイルのローカル fixture に置く**（規約 §4-4・`015` §4-4）。
`apps/ui/tests/conftest.py` を作らない（`test_ui_config.py` が `APP_BASE_URL` の有無そのものを
検証しているため）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from nicegui import ui as nui

from settings import ui as settings_ui
from trigger import ui as trigger_ui
from ui.pages import UI_PAGE_REGISTRATIONS

if TYPE_CHECKING:
    from nicegui.testing import User

    from ui.pages import UiPage

STUB_BASE_URL = "http://wiring-contract.invalid:9999"
"""止め木で与える base URL（`test_ui_pages_closures.py` と同じ値）。"""

EXPECTED_PAGES: dict[str, tuple[str, str]] = {
    "AI 設定": ("settings", "settings-ai"),
    "プロジェクト": ("settings", "settings-projects"),
    "トリガー": ("trigger", "triggers"),
}
"""登録のタイトル → `(module, 経路スラグ)`（ui-pages §1・data-model §2）。"""


@pytest.fixture
def stubbed_base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """`UiEnvSettings` が実運用の `.env` を読まない止め木（FR-015・規約 §4-4）。

    Returns:
        止め木で与えた base URL。
    """
    monkeypatch.setenv("APP_BASE_URL", STUB_BASE_URL)
    return STUB_BASE_URL


def _pages_of(module: str) -> list[UiPage]:
    """実 `UI_PAGE_REGISTRATIONS` の当該モジュールの閉包を呼び、ページ部品を返す。

    Args:
        module: モジュール名。

    Returns:
        ページ部品（登録順）。
    """
    return [
        registration.build_page()
        for registration in UI_PAGE_REGISTRATIONS
        if registration.module == module
    ]


@pytest.mark.usefixtures("stubbed_base_url")
def test_ui_settings_pages_expose_real_title_and_slug() -> None:
    """組 apps/ui ⇄ settings.ui: settings の 2 枚がタイトルと経路スラグを実値で持つ。"""
    pages = _pages_of("settings")

    assert [page.title for page in pages] == ["AI 設定", "プロジェクト"]
    assert [page.slug for page in pages] == ["settings-ai", "settings-projects"]


@pytest.mark.usefixtures("stubbed_base_url")
def test_ui_trigger_page_exposes_real_title_and_slug() -> None:
    """組 apps/ui ⇄ trigger.ui: trigger の 1 枚がタイトルと経路スラグを実値で持つ。"""
    pages = _pages_of("trigger")

    assert len(pages) == 1
    assert pages[0].title == "トリガー"
    assert pages[0].slug == "triggers"


@pytest.mark.usefixtures("stubbed_base_url")
def test_ui_every_registration_title_matches_the_generated_page_title() -> None:
    """FR-006: 登録の `title` と生成したページ部品の `title` が 3 件とも一致する。

    ⚠ `title` は登録側とページ部品側の両方に同じ文字列が書かれており、**片方だけを変えても
    機械的には落ちない**（spec §0-3b）——本テストがその一致を見る。件数もリテラルも書かず
    実登録から導く。
    """
    mismatched = [
        (registration.module, registration.title, registration.build_page().title)
        for registration in UI_PAGE_REGISTRATIONS
        if registration.title != registration.build_page().title
    ]

    assert not mismatched, f"登録の title と生成ページの title が食い違う: {mismatched}"


@pytest.mark.usefixtures("stubbed_base_url")
def test_ui_every_generated_page_has_a_distinct_slug() -> None:
    """FR-006: 3 件の経路スラグが互いに異なり、契約の表と一致する。"""
    slugs = [registration.build_page().slug for registration in UI_PAGE_REGISTRATIONS]

    assert all(slugs)
    assert len(set(slugs)) == len(slugs)
    assert {
        registration.title: (registration.module, registration.build_page().slug)
        for registration in UI_PAGE_REGISTRATIONS
    } == EXPECTED_PAGES


async def _drive(user: User, page: UiPage) -> None:
    """ページ部品の中身だけを描いて開く（殻を通さない）。

    Args:
        user: `user` フィクスチャ。
        page: 描くページ部品。
    """

    @nui.page("/")
    async def _page() -> None:  # pyright: ignore[reportUnusedFunction]  # `@nui.page` が経路に登録するので、名前は参照されない
        await page.render()

    await user.open("/")


async def test_ui_settings_page_render_uses_the_stubbed_base_url(
    user: User, monkeypatch: pytest.MonkeyPatch, stubbed_base_url: str
) -> None:
    """FR-015・`010` SD-05: settings の閉包が導出した base URL が、描画の最初の要求に届く。

    ⚠ この 1 本が無いと、ほかの 4 本は止め木が失効しても**緑のまま**である（`title`・`slug` は
    base URL に依存しない）。差し替え点は feature の `ui.py` の `call_api`（唯一の差し替え点＝
    ui-pages §3）で、`fetch` がスレッドの中でこれを呼ぶ。
    """
    recorded: list[str] = []

    def _fake_call_api(
        base_url: str, _method: str, _path: str, _body: object = None
    ) -> settings_ui.ApiResponse:
        recorded.append(base_url)
        return settings_ui.ApiResponse(status=200, payload=[])

    monkeypatch.setattr(settings_ui, "call_api", _fake_call_api)

    await _drive(user, _pages_of("settings")[0])

    assert recorded, "描画で 1 度も `call_api` が呼ばれていない"
    assert recorded[0] == stubbed_base_url, (
        f"閉包が渡した base URL が止め木の値でない: {recorded[0]!r}"
    )


async def test_ui_trigger_page_render_uses_the_stubbed_base_url(
    user: User, monkeypatch: pytest.MonkeyPatch, stubbed_base_url: str
) -> None:
    """FR-015・`010` SD-05: trigger の閉包が導出した base URL が、描画の最初の要求に届く。"""
    recorded: list[str] = []

    def _fake_call_api(
        base_url: str, _method: str, _path: str, _body: object = None
    ) -> trigger_ui.ApiResponse:
        recorded.append(base_url)
        return trigger_ui.ApiResponse(status=200, payload=[])

    monkeypatch.setattr(trigger_ui, "call_api", _fake_call_api)

    await _drive(user, _pages_of("trigger")[0])

    assert recorded, "描画で 1 度も `call_api` が呼ばれていない"
    assert recorded[0] == stubbed_base_url, (
        f"閉包が渡した base URL が止め木の値でない: {recorded[0]!r}"
    )
