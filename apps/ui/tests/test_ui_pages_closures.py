"""実 `UI_PAGE_REGISTRATIONS` の閉包が実際にページを生成すること（US5・FR-022〜FR-024）。

GAP-5 を埋める。`page.build_page()` の呼び出しはリポジトリ全体で `apps/ui/src/ui/main.py` の
**1 箇所だけ**であり、テストからは 1 度も呼ばれていなかった（`test_ui_entry.py` の U-1 系は
ページ 0 枚で入口を開き、U-5 系は偽の登録 `alpha`／`beta` を使う）。

⚠ **止め木は環境変数 `APP_BASE_URL`** で行う（FR-023・spec §0-7 (10)）。`UiEnvSettings` は明示
`__init__` を持たず `env_file` をクラス定義時に評価するので **`_env_file` を渡せない**
（`test_ui_config.py` の `_neutralize_base_url` と同じ理由）。環境変数は `.env` より優先される。

⚠ **`apps/ui/tests/conftest.py` を作らない**（research R-08）。ここに autouse fixture を持つ
`conftest.py` を新設すると、**無変更のはずの既存 3 ファイルの実行環境が変わる**（とくに
`test_ui_config.py` は `APP_BASE_URL` の有無そのものを検証している）。止め木は本ファイルの
**ローカル fixture** に閉じる。

⚠ **apps/api 側と 1 ファイルに混ぜない**（FR-004）。apps/api のテストから `ui.pages` を import
しない。両者は別プロセスであり（憲章 §4）、`UI_PAGE_REGISTRATIONS` は apps/api の配線に載らない。

ページ部品は素の frozen dataclass（data-model §2）なので、`title`・`slug` はここで読める
（移行前は中空の `st.Page` が返るため読めず、`AppTest.from_function` の中で観測していた＝ WD-23）。
base URL が描画に届くことは `test_ui_pages_objects.py` が `user` フィクスチャで担う。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ui import pages as ui_pages
from ui.pages import UI_PAGE_REGISTRATIONS, UiPageRegistration

if TYPE_CHECKING:
    from collections.abc import Callable

UI_PAGE_COVERS = (
    ("settings", "AI 設定"),
    ("settings", "プロジェクト"),
    ("trigger", "トリガー"),
)
"""本ファイルが担う UI 組（被覆検査 C-1／C-2 が読む。同定子は `(module, title)`）。

宣言は**この 1 ファイルに 1 本化する**（`test_ui_pages_objects.py` は `UI_PAGE_COVERS`
を書かない）。
"""

STUB_BASE_URL = "http://wiring-contract.invalid:9999"
"""止め木で与える base URL（実運用の `.env` の値ではないことが一目で分かる形）。"""

OTHER_BASE_URL = "http://wiring-contract-2.invalid:8888"
"""**呼び出し時導出**を示すために途中で差し替える 2 つ目の値（`010` SD-05）。"""

PAGE_BUILDER_NAMES = {
    ("settings", "AI 設定"): "build_ai_settings_page",
    ("settings", "プロジェクト"): "build_projects_page",
    ("trigger", "トリガー"): "build_triggers_page",
}
"""登録の同定子 → **`ui.pages` 側**の生成関数名（差し替え点）。

⚠ **`settings.ui`／`trigger.ui` 側を差し替えても効かない** —— `pages.py` は
`from settings.ui import build_ai_settings_page` の形で名前を**束ねる**ため、閉包が見るのは
`ui.pages` の属性である（`013` の「executor だけ `executor.models` 側」と同型の罠＝ WD-25）。
"""


def _recording_builder(recorded: list[str]) -> Callable[[str], object]:
    """渡された base URL を記録して中身のないページを返す差し替え（`ui.pages` 側に差す）。

    Args:
        recorded: 渡された base URL を順に積む先。

    Returns:
        `build_*_page` と同じ形（`(str) -> object`）の差し替え関数。
    """

    def _build(base_url: str) -> object:
        recorded.append(base_url)
        return object()

    return _build


@pytest.fixture
def stubbed_base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """`UiEnvSettings` が実運用の `.env` を読まない止め木（FR-023）。

    Returns:
        止め木で与えた base URL。
    """
    monkeypatch.setenv("APP_BASE_URL", STUB_BASE_URL)
    return STUB_BASE_URL


def _entries_for(module: str) -> tuple[UiPageRegistration, ...]:
    """実 `UI_PAGE_REGISTRATIONS` から `module` のエントリを引く。

    Args:
        module: モジュール名。

    Returns:
        当該エントリ（登録順）。
    """
    return tuple(
        registration for registration in UI_PAGE_REGISTRATIONS if registration.module == module
    )


@pytest.mark.usefixtures("stubbed_base_url")
def test_ui_settings_closures_build_pages_without_error() -> None:
    """組 apps/ui ⇄ settings.ui: 登録エントリ越しに閉包を呼ぶとページが生成される（FR-022・US5
    AC-1）。

    見る欄: `UiPageRegistration.build_page`（`module == "settings"` の 2 件）。
    **偽の登録で代用しない**
    ——実 `UI_PAGE_REGISTRATIONS` から引いたエントリの `build_page()` をそのまま呼ぶ。

    **「ページを作る」の判定をページ部品の属性まで見る**（規約 §4 の B 群。`is not None` では
    中身の無い値でも通ってしまう）。
    """
    entries = _entries_for("settings")

    pages = [registration.build_page() for registration in entries]

    assert len(pages) == len(entries)
    assert [page.title for page in pages] == ["AI 設定", "プロジェクト"]
    assert [page.slug for page in pages] == ["settings-ai", "settings-projects"]
    assert all(callable(page.render) for page in pages)


@pytest.mark.usefixtures("stubbed_base_url")
def test_ui_trigger_closure_builds_a_page_without_error() -> None:
    """組 apps/ui ⇄ trigger.ui:
    登録エントリ越しに閉包を呼ぶとページが生成される（FR-022・US5 AC-1）。

    見る欄: `UiPageRegistration.build_page`（`module == "trigger"` の 1 件）。判定は
    ページ部品の属性まで見る（規約 §4 の B 群）。
    """
    entries = _entries_for("trigger")

    pages = [registration.build_page() for registration in entries]

    assert len(pages) == len(entries)
    assert [page.title for page in pages] == ["トリガー"]
    assert [page.slug for page in pages] == ["triggers"]
    assert all(callable(page.render) for page in pages)


def test_ui_settings_closures_pass_the_stubbed_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """組 apps/ui ⇄ settings.ui: 閉包が止め木の base URL を生成関数へ渡す（FR-023・US5 AC-2）。

    見る欄: `build_page`（閉包の中の `UiEnvSettings().resolved_app_base_url`）。差し替え点は
    **`ui.pages`（束ねた `ui_pages`）側**である（`settings.ui` 側を差し替えても効かない＝
    WD-25）。渡る値が実運用の `.env` の値ではなく止め木の値であることを、記録した実引数で示す。
    """
    monkeypatch.setenv("APP_BASE_URL", STUB_BASE_URL)
    recorded: list[str] = []
    for registration in _entries_for("settings"):
        key = (registration.module, registration.title)
        monkeypatch.setattr(ui_pages, PAGE_BUILDER_NAMES[key], _recording_builder(recorded))

    for registration in _entries_for("settings"):
        registration.build_page()

    assert recorded == [STUB_BASE_URL] * len(_entries_for("settings"))


def test_ui_trigger_closure_passes_the_stubbed_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """組 apps/ui ⇄ trigger.ui: 閉包が止め木の base URL を生成関数へ渡す（FR-023・US5 AC-2）。

    見る欄: `build_page`（閉包の中の `UiEnvSettings().resolved_app_base_url`）。差し替え点は
    **`ui.pages` 側**の `build_triggers_page` である（`trigger.ui` 側では効かない＝ WD-25）。
    """
    monkeypatch.setenv("APP_BASE_URL", STUB_BASE_URL)
    recorded: list[str] = []
    monkeypatch.setattr(ui_pages, "build_triggers_page", _recording_builder(recorded))

    for registration in _entries_for("trigger"):
        registration.build_page()

    assert recorded == [STUB_BASE_URL]


def test_ui_closures_derive_the_base_url_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """組 apps/ui ⇄ settings.ui／trigger.ui: base URL は**登録時ではなく呼び出し時**に導出される。

    FR-023・US5 AC-2・`010` SD-05。見る欄: `build_page`（閉包の導出時点）。止め木の値を途中で変えて
    同じ閉包を呼び直すと**渡る値も変わる**ことで示す——登録時に固定していればこの差は出ない。
    """
    recorded: list[str] = []
    for registration in UI_PAGE_REGISTRATIONS:
        key = (registration.module, registration.title)
        monkeypatch.setattr(ui_pages, PAGE_BUILDER_NAMES[key], _recording_builder(recorded))
    first = UI_PAGE_REGISTRATIONS[0]

    monkeypatch.setenv("APP_BASE_URL", STUB_BASE_URL)
    first.build_page()
    monkeypatch.setenv("APP_BASE_URL", OTHER_BASE_URL)
    first.build_page()

    assert recorded == [STUB_BASE_URL, OTHER_BASE_URL]


def test_ui_settings_registers_two_distinct_pages() -> None:
    """組 apps/ui ⇄ settings.ui: `module == "settings"` の登録が **2 件**で `title` が互いに異なる。

    FR-024・US5 AC-3・GAP-5。見る欄: `module`（"settings" が 2 件）・`title`（相異）。

    ⚠ **REG-8h は `module` の集合比較なので、エントリが 1 件に減っても 2 件のままでも通る**
    （spec §0-6 の除外表）。同じページを 2 回登録していないことも、この主張が守る。
    """
    entries = _entries_for("settings")
    titles = [registration.title for registration in entries]

    assert len(entries) == 2
    assert len(set(titles)) == len(titles)


def test_ui_trigger_registers_exactly_one_page() -> None:
    """組 apps/ui ⇄ trigger.ui: `module == "trigger"` の登録が **1 件**である（FR-024・US5 AC-4）。

    見る欄: `module`（"trigger" が 1 件）。
    """
    entries = _entries_for("trigger")

    assert len(entries) == 1


def test_ui_page_builder_map_covers_every_registration() -> None:
    """組 apps/ui ⇄ settings.ui／trigger.ui: 差し替え点の表が実登録を過不足なく覆う。

    見る欄: `module`・`title` × `PAGE_BUILDER_NAMES`。この表が古くなると、base URL の受け渡しを見る
    テストが**差し替えないまま緑**になりうる（空振り）ので、表そのものを実登録から検査する。
    """
    registered = {
        (registration.module, registration.title) for registration in UI_PAGE_REGISTRATIONS
    }

    assert set(PAGE_BUILDER_NAMES) == registered
    assert all(hasattr(ui_pages, name) for name in PAGE_BUILDER_NAMES.values())
