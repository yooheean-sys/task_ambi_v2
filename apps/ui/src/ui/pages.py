"""UI ページの登録（土台側の 5 つ目の登録点。data-model.md §1・§2・contracts/ui-shell.md §1）。

UI ページを持つモジュールを足すときに **apps/ui 側で編集する唯一のファイル**。
`module` の集合は apps/api 側の `ModuleRegistration.has_ui_page=True` の登録名の集合と
一致すること（REG-8h が検査する）。

**import しただけでは何も登録しない**（FR-023）。本モジュールの import は `settings.ui`・
`trigger.ui`・`ui.config` を読むだけで、NiceGUI の経路もアプリも作らない。経路を登録するのは
殻（`ui.shell.register_pages`）が呼ばれた時点だけである。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from settings.ui import build_ai_settings_page, build_projects_page
from trigger.ui import build_triggers_page
from ui.config import UiEnvSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence


class UiPage(Protocol):
    """1 枚の UI ページ（data-model §2）。

    **NiceGUI に依存しない構造型**である。feature 側は各 `ui.py` の frozen dataclass
    （`SettingsPage`・`TriggerPage`）でこれに構造で適合する。登録の契約に UI 基盤の型を
    持ち込まないためである。
    """

    @property
    def title(self) -> str:
        """ページのタイトル（ブラウザのタブ名とナビゲーションの文字）。"""
        ...

    @property
    def slug(self) -> str:
        """経路スラグ。公開 URL は `/ui/{slug}`。"""
        ...

    @property
    def render(self) -> Callable[[], Awaitable[None]]:
        """現在の NiceGUI の文脈に**ページの中身だけ**を描く非同期関数。

        共通の見出しとナビゲーションは描かない（殻が描く）。
        """
        ...


@dataclass(frozen=True, slots=True)
class UiPageRegistration:
    """1 モジュールの UI ページ 1 枚の登録。"""

    module: str
    title: str
    build_page: Callable[[], UiPage]


def _settings_ai_page() -> UiPage:
    """AI 設定ページ（UC-07）。app の所在は登録時ではなく**呼び出し時**に導出する。

    `create_ui_app` が `UiEnvSettings().resolved_app_base_url` を読むのと**同じ実行時点**になる
    （010 SD-05）。導出は `UiEnvSettings.resolved_app_base_url` の **1 箇所のまま**（D15-f）で、
    feature 側は `.env` を読まない（FR-014・FR-021）。
    """
    return build_ai_settings_page(UiEnvSettings().resolved_app_base_url)


def _settings_projects_page() -> UiPage:
    """プロジェクトページ（UC-08）。base URL の受け渡しは AI 設定ページと同じ。"""
    return build_projects_page(UiEnvSettings().resolved_app_base_url)


def _trigger_page() -> UiPage:
    """トリガーページ（UC-09/10）。base URL の受け渡しは AI 設定ページと同じ（010 SD-05）。"""
    return build_triggers_page(UiEnvSettings().resolved_app_base_url)


UI_PAGE_REGISTRATIONS: tuple[UiPageRegistration, ...] = (
    UiPageRegistration(module="settings", title="AI 設定", build_page=_settings_ai_page),
    UiPageRegistration(module="settings", title="プロジェクト", build_page=_settings_projects_page),
    UiPageRegistration(module="trigger", title="トリガー", build_page=_trigger_page),
)
"""settings の **2 エントリ**（010 SD-04）＋ trigger の **1 エントリ**（011）。

REG-8h は `module` の**集合**比較なので、同じ `module="settings"` を 2 つ置いても通る（010 F-03）。
集合は `{settings}` → **`{settings, trigger}`**（011 SC-015）。
"""


def select_pages(
    registrations: Sequence[UiPageRegistration], enabled_modules: Sequence[str]
) -> tuple[UiPageRegistration, ...]:
    """有効 MOD 一覧に載るモジュールのページだけを残す（FR-020・`002` FR-034）。

    Args:
        registrations: UI ページの登録集合。
        enabled_modules: ルート設定の `enabled_modules`（CFG-20）。

    Returns:
        `module` が `enabled_modules` に含まれる登録だけを、元の並びのまま残したタプル。
    """
    enabled = set(enabled_modules)
    return tuple(page for page in registrations if page.module in enabled)
