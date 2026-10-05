"""ルート `config.json` の型付き読み口（CFG-14〜21）。

`apps/api`・`apps/ui`・common の 3 者が同じ口から読む（DIFF-05・`000` plan (iv)）。
契約は contracts/kernel.md §4、属性の正本は data-model.md §4。

- ソースは `(init_settings, JsonConfigSettingsSource)` のみ。
  **環境変数で上書きできない**（FR-030）。
- ファイル位置は `__file__` から上方向に解決し、**作業ディレクトリに依存しない**（FR-029）。
- `extra="forbid"`・`frozen=True`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

WORKSPACE_MARKER = "[tool.uv.workspace]"
"""リポジトリルートの目印。`pyproject.toml` がこれを含む階層をルートとする。"""

CONFIG_FILENAME = "config.json"


def find_repo_root() -> Path:
    """`[tool.uv.workspace]` を持つ `pyproject.toml` を `__file__` から上方向に探す。

    Returns:
        リポジトリルートの絶対パス。

    Raises:
        RuntimeError: ルートが見つからないとき（探索した起点を含む）。
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and WORKSPACE_MARKER in pyproject.read_text(encoding="utf-8"):
            return candidate
    msg = f"workspace ルート（{WORKSPACE_MARKER} を持つ pyproject.toml）が {here} の上に無い"
    raise RuntimeError(msg)


def config_path() -> Path:
    """ルート `config.json` の絶対パス（cwd 非依存）。"""
    return find_repo_root() / CONFIG_FILENAME


def _ensure_readable(path: Path) -> None:
    """不在・破損を、**見に行ったパスが分かる形**で失敗させる（contracts/kernel.md §4 約束 6）。

    Raises:
        FileNotFoundError: ファイルが無い。
        ValueError: JSON として読めない。
    """
    if not path.is_file():
        msg = f"ルート設定が見つかりません: {path}"
        raise FileNotFoundError(msg)
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"ルート設定を JSON として読めません: {path}: {exc}"
        raise ValueError(msg) from exc


class LogConfig(BaseModel):
    """ログ設定（CFG-16〜19）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"
    correlation_header: str = "X-Correlation-ID"
    redact_keys: list[str] = ["*_API_KEY*", "Authorization", "api_key"]


class DefaultsConfig(BaseModel):
    """既定値（CFG-21）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    encoding: str = "utf-8"


class RootConfig(BaseSettings):
    """ルート `config.json` の全体（CFG-14〜21）。"""

    model_config = SettingsConfigDict(frozen=True, extra="forbid")

    app_name: str
    env_label: str
    log: LogConfig = LogConfig()
    enabled_modules: list[str]
    defaults: DefaultsConfig = DefaultsConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る。

        環境変数・.env・secrets のソースを**返さない**ため、非機密設定のキーは
        環境変数で上書きできない（FR-030・R-11）。
        """
        path = config_path()
        _ensure_readable(path)
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=path))

    def __init__(self, **overrides: Any) -> None:
        """`⑧ build() の init 引数`による差し替えを受ける（contracts/kernel.md §4 約束 7）。"""
        super().__init__(**overrides)
