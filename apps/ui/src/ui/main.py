"""統合した起動点（contracts/ui-shell.md §2・data-model.md §8）。

app（FastAPI）を組み立て、その**同じプロセス・同じ待受**に NiceGUI の UI を `/ui` で載せる
（憲章 2.0.0 §4・D23）。起動コマンドは `uv run python -m ui.main` である。

**業務ロジックを持たない**（contracts/ui-shell.md §1・`002` FR-029）。モジュールの操作は、
画面が app の HTTP API を呼ぶ形で行う（EIF-10）。

⚠ 守ること（ui-shell §2）。
- ルート設定を読むのは 1 回だけで、app の組み立てとページの選別に**同じ値**を使う（FR-020）。
- `LoggerFactory.configure` を呼ばない —— ログ基盤の設定は `create_app` の中の 1 回だけである
  （FR-017a）。
- `storage_secret` を渡さない・`app.storage` を使わない（FR-021・憲章 2.0.0 §6）。
- 自前の lifespan を足さない —— lifespan は `run_with` の包み方に任せる（FR-019）。
- 例外を握りつぶさない。組み立てが失敗したら待受を始めない（FR-018）。
- import しただけでは何もしない（`if __name__ == "__main__":` の中でだけ `main()` を呼ぶ）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import uvicorn
from nicegui import ui as nui

from api import create_app
from api.config import load_root_config
from ui.config import UiEnvSettings
from ui.pages import UI_PAGE_REGISTRATIONS, select_pages
from ui.shell import register_pages

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

    from api.wiring import ModuleRegistration, ProtocolOverrides
    from common.kernel.config import RootConfig

UI_MOUNT_PATH = "/ui"
"""UI を載せる接頭辞（公開 URL は `/ui/{slug}`＝ ui-shell §4・FR-016）。"""


def create_ui_app(
    *,
    config: RootConfig | None = None,
    overrides: ProtocolOverrides | None = None,
    registrations: Sequence[ModuleRegistration] | None = None,
) -> FastAPI:
    """入口アプリを組み立て、UI を `/ui` に載せて返す（data-model §8 の 6 段）。

    Args:
        config: ルート設定。`None` なら `load_root_config()` で**1 回だけ**読む。
        overrides: 契約の型 → 差し替える実装（`create_app` にそのまま渡す）。
        registrations: 登録集合（`create_app` にそのまま渡す）。

    Returns:
        `/health` と各モジュールの経路に加えて、`/ui` に UI を載せた `FastAPI`。
        待受は開始していない。

    Raises:
        ConfigurationError: ルート設定の不備（`load_root_config` 由来）。
        WiringError: 配線の不備（`create_app` 由来）。
    """
    resolved = config if config is not None else load_root_config()
    app = create_app(config=resolved, overrides=overrides, registrations=registrations)
    selected = select_pages(UI_PAGE_REGISTRATIONS, resolved.enabled_modules)
    base_url = UiEnvSettings().resolved_app_base_url
    register_pages(
        [registration.build_page() for registration in selected],
        app_name=resolved.app_name,
        base_url=base_url,
    )
    nui.run_with(  # pyright: ignore[reportUnknownMemberType]  # nicegui 3.17.1 の `run_with` は `root`・`gzip_middleware_factory` を素の `Callable` で注釈しており、引数の型が解決しきれない（py.typed 付きだが注釈が不完全）
        app,
        mount_path=UI_MOUNT_PATH,
        title=resolved.app_name,
        show_welcome_message=False,
    )
    return app


def main() -> None:
    """組み立てた app で待受を始める（`uv run python -m ui.main`）。

    待受のホストとポートは `UiEnvSettings` の `app_host`・`app_port` から取る（ui-shell §3）。
    組み立てで例外が上がったら、待受を始めずにそのまま上げる（FR-018）。
    """
    app = create_ui_app()
    env = UiEnvSettings()
    uvicorn.run(app, host=env.app_host, port=env.app_port)


if __name__ == "__main__":
    main()
