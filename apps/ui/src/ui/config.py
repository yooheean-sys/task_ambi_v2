"""apps/ui が読む環境依存値と app の base URL の導出（contracts/ui-shell.md §3）。

導出規則は `002` contracts/ui-startup.md §2 のまま変えない（spec Edge Cases「接続先 base URL」）。
読むのはルート `.env` の `APP_BASE_URL`・`APP_HOST`・`APP_PORT` だけ。**秘密・各モジュールの
config.json・宣言的設定ファイルは読まない**（FR-021）。**UI 専用の待受ポート（CFG-13）は読まない**
—— 016 で UI が app の同じ待受に同居したため廃止された（D23-e。当該キー名の文字列を
`apps/ui/src` に置かない）。待受のホストとポートは `app_host`・`app_port` である。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from common.kernel.config import find_repo_root


class UiEnvSettings(BaseSettings):
    """ui が読む `.env` の環境依存値（CFG-12・CFG-10・CFG-11。contracts/ui-shell.md §3）。

    フィールドの定義は `002` data-model.md §9 のまま。
    `extra="ignore"` は必須（`.env` には他モジュールのキーが同居するため）。
    """

    model_config = SettingsConfigDict(
        env_file=find_repo_root() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_base_url: str | None = None
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    @property
    def resolved_app_base_url(self) -> str:
        """接続先 app の base URL（FR-015・`002` FR-032）。

        導出は**本プロパティ 1 箇所に閉じる**（D15-f）。

        Returns:
            `APP_BASE_URL`（CFG-12）があればその値。無ければ `APP_HOST`／`APP_PORT` から
            組んだ `http://{host}:{port}`。
        """
        if self.app_base_url:
            return self.app_base_url
        return f"http://{self.app_host}:{self.app_port}"
