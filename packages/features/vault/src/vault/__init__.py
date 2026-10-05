"""vault モジュールの公開口（設定 2 クラス・`VaultDeps`・`VaultHandle`・`build`）。vault-F10。

vault は Obsidian vault 領域の**唯一のアダプタ**である（憲章 §2）。HTTP 経路も UI も持たない
ため `router.py`・`ui.py`・`schemas.py`・`dependencies.py` を置かない（FR-001・FR-068）。
設定クラス（`VaultEnv`・`VaultConfig`）は「公開口の一部」として本ファイルに同居させる。
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

from common.kernel.config import RootConfig, find_repo_root
from common.kernel.config import config_path as root_config_path
from vault.exceptions import VaultConfigError, VaultEnvError, VaultRootError
from vault.repository import FsAdapter
from vault.service import VaultService

if TYPE_CHECKING:
    from common.interfaces.vault import OutcomeWriter, VaultReader

__all__ = [
    "VaultConfig",
    "VaultDeps",
    "VaultEnv",
    "VaultHandle",
    "build",
    "config_path",
    "env_file_path",
]

CONFIG_FILENAME = "config.json"
"""vault 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""


def config_path() -> Path:
    """`packages/features/vault/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


def env_file_path() -> Path:
    """リポジトリルートの `.env` の絶対パス（cwd 非依存）。

    `build()` は毎回この関数で解決するため、テストはここを差し替えて `.env` を隔離できる
    （実リポジトリの `.env` が `VAULT_ROOT` を実値で持つ＝ FR-019・SC-015）。
    """
    return find_repo_root() / ".env"


class VaultEnv(BaseSettings):
    """`.env` の環境依存値（CFG-04）。vault だけが読む。

    解決順序は **init > OS 環境変数 > `.env`**（pydantic-settings の既定＝⑥→⑦）。`.env` には
    他モジュールのキーが同居するため **`extra="ignore"` が必須**である。
    """

    model_config = SettingsConfigDict(
        env_file=env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    VAULT_ROOT: Path

    def __init__(self, **overrides: Any) -> None:
        """`.env` の所在（`_env_file`）と init 引数の差し替えを受ける。

        pydantic が合成する `__init__` はフィールドしか受け付けないため、`BaseSettings` の
        `_env_file` を渡すには明示的な `__init__` が要る。`model_config` の
        `env_file=env_file_path()` は**クラス定義時に 1 回しか評価されない**ので、本メソッドと
        `build()` の `VaultEnv(_env_file=env_file_path())` が**対で**初めて、テストの
        autouse 止め木（`monkeypatch.setattr("vault.env_file_path", …)`）が効く。どちらかを
        落とすと止め木は**黙って無効**になり、テストが実 `.env` の `VAULT_ROOT`＝実運用の
        Obsidian vault を掴む（FR-019・SC-015 の担保はこの 1 点に依存する）。
        """
        super().__init__(**overrides)


class VaultConfig(BaseSettings):
    """`packages/features/vault/config.json` の挙動設定（CFG-27〜35）。

    ソースを `(init_settings, JsonConfigSettingsSource)` **だけ**に絞るため、環境変数で
    上書きできない（x3_notes §2「`*Config` は env ソースを持たない」＝ FR-058）。キーは
    スコープ接頭辞を剥がした平坦形。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    forbidden_chars: list[str] = [  # noqa: RUF012  pydantic がフィールド既定として扱う（共有されない）
        "\\",
        "/",
        ":",
        "*",
        "?",
        '"',
        "<",
        ">",
        "|",
    ]
    """CFG-27。**Obsidian 1.12.7 のハードブロック集合と完全一致**（M-22・M-23）。"""

    forbidden_obsidian: list[str] = ["#", "^", "[", "]", "|", "%%"]  # noqa: RUF012  同上
    """CFG-28。既定 5 種はアプリの `HT` と完全一致。`%%` を足す（FR-055）。"""

    reserved_names: list[str] = [  # noqa: RUF012  同上
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    ]
    """CFG-29。大文字小文字を問わず一致（M-14）。語幹＝**最初のドットより前**（多重拡張子も含む）。"""

    forbid_trailing_dot_space: bool = True
    """CFG-30。"""

    max_filename_bytes: int = 200
    """CFG-31。"""

    max_outcome_bytes: int = 1048576
    """CFG-32。"""

    archive_dir: str = "_archive"
    """CFG-33。**退避先名の唯一の出所**（FR-030・R-10）。"""

    list_exclude_prefixes: list[str] = ["."]  # noqa: RUF012  同上
    """CFG-34。既定から `_archive` を落とす（DIFF-09）。`archive_dir` は導出で加わる。"""

    encoding: str | None = None
    """CFG-35。`None` ＝ルートの `defaults.encoding` を継承（実効値は `build()` が解決＝ R-11）。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る（① → ③ → ⑧）。"""
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path()))

    def __init__(self, **overrides: Any) -> None:
        """`⑧ build() の init 引数`による差し替えを受ける（FR-057・DIFF-08）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class VaultDeps:
    """vault が他モジュールから受け取る依存。**空**（FR-003）。"""


@dataclass(frozen=True, slots=True)
class VaultHandle:
    """`build()` の戻り。`vault_reader` と `outcome_writer` は**同一インスタンス**（FR-005）。"""

    vault_reader: VaultReader
    outcome_writer: OutcomeWriter
    router: None = None


def build(deps: VaultDeps, *, config: VaultConfig | None = None) -> VaultHandle:
    """Vault を組み立てる（起動時の 4 検査を順に行う。FR-052・FR-053）。

    検査順は a（`VAULT_ROOT` 未設定）→ b（実在しない）→ c（ディレクトリでない）→
    d（`config.json` の必須値欠け・未知キー／ルート設定が読めない）。**最初に失敗したものだけ**
    を報告する（原因が 1 つに定まる方が追いやすい）。

    **`VAULT_ROOT` を `realpath` して保持しない**（R-05）。b・c の検査は**その時点の**実在確認
    であり、実行時の保証ではない（`FsAdapter` が操作のたびに解決する）。

    Args:
        deps: vault が受け取る依存（空。FR-003）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む。FR-057）。

    Returns:
        組み立て済みの `VaultHandle`（`router` は `None`）。`vault_reader is outcome_writer`。

    Raises:
        VaultEnvError: `VAULT_ROOT` が設定されていない。
        VaultRootError: `VAULT_ROOT` が実在しない／ディレクトリでない。
        VaultConfigError: `config.json` の必須値が欠けている／未知キーがある／ルート設定が読めない。
    """
    del deps  # vault は他モジュールの契約に依存しない（FR-003）
    env = _load_env()  # (a)
    _check_root(env.VAULT_ROOT)  # (b)(c)
    settings = config if config is not None else _load_config()  # (d)
    encoding = _effective_encoding(settings)  # (d の後段。R-11)

    adapter = FsAdapter(vault_root=env.VAULT_ROOT, config=settings, encoding=encoding)
    service = VaultService(adapter, config=settings, encoding=encoding)
    vault_reader: VaultReader = service  # 憲章 §3: Protocol 型を必ず注釈する（同一インスタンス）
    outcome_writer: OutcomeWriter = service
    return VaultHandle(vault_reader=vault_reader, outcome_writer=outcome_writer, router=None)


def _load_env() -> VaultEnv:
    """(a) `.env` と OS 環境変数から `VAULT_ROOT` を読む。

    `VaultEnv(_env_file=env_file_path())` の形で**毎回**渡す。`VaultEnv` の明示 `__init__` と
    対で初めて、テストの autouse 止め木が効く（FR-019・SC-015）。**`VaultEnv()` の形に
    「整理」しない。**

    Returns:
        読み込み済みの環境依存値。

    Raises:
        VaultEnvError: `VAULT_ROOT` が無い（必要なキー名と読んだ `.env` の絶対パスを載せる）。
    """
    env_file = env_file_path()
    try:
        return VaultEnv(_env_file=env_file)
    except ValidationError as exc:
        msg = (
            f"vault の起動に必要な環境依存値が読めない: {exc}"
            " / 必要なキー: VAULT_ROOT（Obsidian vault ルートの絶対パス）"
            f" / 読んだ .env: {env_file}"
        )
        raise VaultEnvError(msg) from exc


def _check_root(raw: Path) -> None:
    """(b)(c) `VAULT_ROOT` が実在し、ディレクトリであることを確かめる。

    `is_dir()` は「不在」にも「ファイル」にも偽を返すので、`exists()` で先に分けて
    **b と c を別のメッセージ**にする（SC-010）。**解決結果を返さない**（保持しない＝ R-05）。

    Raises:
        VaultRootError: 実在しない／ディレクトリでない（**見に行った絶対パス**を載せる）。
    """
    root = raw.expanduser().resolve()
    if not root.exists():
        msg = f"VAULT_ROOT が実在しない: {root}"
        raise VaultRootError(msg)
    if not root.is_dir():
        msg = f"VAULT_ROOT がディレクトリでない: {root}"
        raise VaultRootError(msg)


def _load_config() -> VaultConfig:
    """(d) `config.json` を読む。

    Returns:
        読み込み済みの挙動設定。

    Raises:
        VaultConfigError: 必須値が欠けている／未知キーがある。
    """
    path = config_path()
    try:
        return VaultConfig()
    except ValidationError as exc:
        msg = f"vault の設定を読めない（{path}）: {exc}"
        raise VaultConfigError(msg) from exc


def _effective_encoding(settings: VaultConfig) -> str:
    """文字コードの実効値を解決する（`None` ならルートの `defaults.encoding`＝ R-11）。

    Returns:
        読み書きに使う文字コード。

    Raises:
        VaultConfigError: ルート設定を読めない（**見に行った絶対パス**を載せる＝ FR-054・
            SC-010 (d)。`data-model.md` §4 はこの経路を検査 (d) の一部と定めている）。
    """
    if settings.encoding is not None:
        return settings.encoding
    try:
        return RootConfig().defaults.encoding
    except (ValidationError, OSError, ValueError) as exc:
        # パスは 3 経路のうち 2 つ（`FileNotFoundError`・JSON 不正の `ValueError`）では元例外
        # が既に持っているが、**スキーマ不一致の `ValidationError` は持たない**（VD-45）。
        # 条件付きで足すと「載る場合と載らない場合」が生まれるので、無条件に載せる。
        msg = f"ルート設定の defaults.encoding を読めない（{root_config_path()}）: {exc}"
        raise VaultConfigError(msg) from exc
