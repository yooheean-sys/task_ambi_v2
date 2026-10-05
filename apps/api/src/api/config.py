"""ルート `config.json` の翻訳と、apps/api の環境依存値（`.env`）の読み口。

読み口そのものは `001` の `common.kernel.config` をそのまま使い、apps/api 側に置くのは
**翻訳だけ**（plan (v)・research R-11・R-12）。`config_path()` は module 属性経由で呼ぶ
（`RootConfig` 側と同じ束縛を差し替えれば、翻訳の本文に出るパスも追随するため）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from api.exceptions import ConfigurationError
from common.kernel import config as kernel_config
from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from pathlib import Path

    from common.kernel.config import RootConfig


def load_root_config() -> RootConfig:
    """ルート `config.json` を読み、不備を `ConfigurationError` に翻訳する（FR-025）。

    Returns:
        読み込んだ `RootConfig`。

    Raises:
        ConfigurationError: 必須値の欠け・型不一致・ファイル不在・JSON として読めないとき。
            本文に欠けた／不正なキー名と読んだファイルの絶対パスを載せる（SC-007）。
    """
    path = kernel_config.config_path()
    try:
        return kernel_config.RootConfig()
    except ValidationError as exc:
        raise ConfigurationError(_validation_message(exc, path)) from exc
    except FileNotFoundError as exc:
        msg = f"ルート設定が読めない: {exc} / 読んだファイル: {path}"
        raise ConfigurationError(msg) from exc
    except ValueError as exc:
        msg = f"ルート設定が読めない: {exc} / 読んだファイル: {path}"
        raise ConfigurationError(msg) from exc


def _validation_message(exc: ValidationError, path: Path) -> str:
    """`ValidationError` を「欠けた／不正なキー名 + 読んだファイル」の記述へ写す。

    pydantic の既定メッセージにもキー名は出るが、**どのファイルを読んだか**が出ない。
    絶対パスを添えると追加調査が 0 で済む（SC-007）。

    Args:
        exc: `RootConfig()` が送出した検証エラー。
        path: 読んだルート設定の絶対パス。

    Returns:
        `ConfigurationError` の本文。
    """
    problems = [
        f"{'.'.join(str(part) for part in error['loc']) or '(全体)'}: {error['msg']}"
        for error in exc.errors()
    ]
    return (
        "ルート設定の必須値が欠けている、または値が不正: "
        + " / ".join(problems)
        + f" / 読んだファイル: {path}"
    )


class AppEnvSettings(BaseSettings):
    """apps/api が読む環境依存値（CFG-10・CFG-11）。**秘密のフィールドを持たない**（FR-028）。

    `extra="ignore"` は必須（`.env` には他モジュールのキーが 10 種以上同居するため）。
    """

    model_config = SettingsConfigDict(
        env_file=find_repo_root() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
