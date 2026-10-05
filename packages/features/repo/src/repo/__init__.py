"""repo モジュールの公開口（`RepoConfig`・`RepoDeps`・`RepoHandle`・`build`）。

repo はプロジェクトのローカルリポジトリ（`ProjectRepos.local_path` の git 作業ツリー）に触れる
**唯一のアダプタ**である（憲章 §2・D25-a）。HTTP 経路も UI も持たないため `router.py`・`ui.py`・
`schemas.py`・`dependencies.py` を置かない。`.env` も読まない（ルートは呼び出しごとに
`ProjectRepos` で渡され、プロジェクトごとに違う）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from common.kernel.config import RootConfig
from common.kernel.config import config_path as root_config_path
from repo.exceptions import RepoConfigError
from repo.repository import FsAdapter
from repo.service import RepoService

if TYPE_CHECKING:
    from common.interfaces.repo import RepoReader, RepoWriter

__all__ = [
    "RepoConfig",
    "RepoDeps",
    "RepoHandle",
    "build",
    "config_path",
]

CONFIG_FILENAME = "config.json"
"""repo 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""


def config_path() -> Path:
    """`packages/features/repo/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


class RepoConfig(BaseSettings):
    """`packages/features/repo/config.json` の挙動設定。

    ソースを `(init_settings, JsonConfigSettingsSource)` **だけ**に絞るため、環境変数で
    上書きできない（vault の `VaultConfig` と同じ流儀）。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    max_read_bytes: int = 262144
    """読取の上限（超えたら `too_large`。LLM へ渡す量を抑える）。"""

    max_write_bytes: int = 1048576
    """書込の上限。"""

    max_list_entries: int = 2000
    """一覧の上限（超えたら `too_large`。`subdir` で絞らせる）。"""

    excluded_dirs: list[str] = [  # noqa: RUF012  pydantic がフィールド既定として扱う（共有されない）
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    ]
    """一覧で降りずに刈り込むディレクトリ名（`.git` は設定に関わらず常に刈り込む）。"""

    deny_patterns: list[str] = [  # noqa: RUF012  同上
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "id_rsa*",
        "id_ed25519*",
    ]
    """読み書き・一覧を拒む名前（`fnmatch`。パスの構成要素ごとに当てる）。秘密を LLM に送らない。"""

    encoding: str | None = None
    """`None` ＝ルートの `defaults.encoding` を継承（実効値は `build()` が解決する）。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る。"""
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path()))

    def __init__(self, **overrides: Any) -> None:
        """`build()` の init 引数による差し替えを受ける。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class RepoDeps:
    """repo が他モジュールから受け取る依存。**空**。"""


@dataclass(frozen=True, slots=True)
class RepoHandle:
    """`build()` の戻り。`repo_reader` と `repo_writer` は**同一インスタンス**。"""

    repo_reader: RepoReader
    repo_writer: RepoWriter
    router: None = None


def build(deps: RepoDeps, *, config: RepoConfig | None = None) -> RepoHandle:
    """Repo を組み立てる。

    起動時に見るのは `config.json` とルートの文字コードだけである。**リポジトリの実在は見ない**
    （プロジェクトごとに違い、未登録・未 clone のプロジェクトも正常に動く。不在は操作のたびに
    `RepoNotFound` で返す）。

    Args:
        deps: repo が受け取る依存（空）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む）。

    Returns:
        組み立て済みの `RepoHandle`（`router` は `None`）。`repo_reader is repo_writer`。

    Raises:
        RepoConfigError: `config.json` の必須値が欠けている／未知キーがある／ルート設定が読めない。
    """
    del deps  # repo は他モジュールの契約に依存しない
    settings = config if config is not None else _load_config()
    encoding = _effective_encoding(settings)
    adapter = FsAdapter(excluded_dirs=frozenset(settings.excluded_dirs))
    service = RepoService(
        adapter,
        encoding=encoding,
        max_read_bytes=settings.max_read_bytes,
        max_write_bytes=settings.max_write_bytes,
        max_list_entries=settings.max_list_entries,
        deny_patterns=tuple(settings.deny_patterns),
    )
    repo_reader: RepoReader = service  # 憲章 §3: Protocol 型を必ず注釈する（同一インスタンス）
    repo_writer: RepoWriter = service
    return RepoHandle(repo_reader=repo_reader, repo_writer=repo_writer, router=None)


def _load_config() -> RepoConfig:
    """`config.json` を読む。

    Raises:
        RepoConfigError: 必須値が欠けている／未知キーがある。
    """
    path = config_path()
    try:
        return RepoConfig()
    except ValidationError as exc:
        msg = f"repo の設定を読めない（{path}）: {exc}"
        raise RepoConfigError(msg) from exc


def _effective_encoding(settings: RepoConfig) -> str:
    """文字コードの実効値を解決する（`None` ならルートの `defaults.encoding`）。

    Raises:
        RepoConfigError: ルート設定を読めない（見に行った絶対パスを載せる）。
    """
    if settings.encoding is not None:
        return settings.encoding
    try:
        return RootConfig().defaults.encoding
    except (ValidationError, OSError, ValueError) as exc:
        msg = f"ルート設定の defaults.encoding を読めない（{root_config_path()}）: {exc}"
        raise RepoConfigError(msg) from exc
