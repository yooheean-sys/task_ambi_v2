"""board モジュールの公開口（設定 2 クラス・`BoardDeps`・`BoardHandle`・`build`）。board-F17。

board は beads（`bd` CLI）の**唯一のアダプタ**である（憲章 §2・EIF-01）。HTTP 経路も UI も
持たないため `router.py`・`ui.py`・`schemas.py`・`dependencies.py` を置かない（FR-001・FR-004）。
設定クラス（`BoardEnv`・`BoardConfig`）は「公開口の一部」として本ファイルに同居させる（R-12。
6 つ目の `config.py` を作らない）。
"""

from __future__ import annotations

import os
import shutil
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

from board.exceptions import (
    BoardBinaryError,
    BoardConfigError,
    BoardEnvError,
    BoardWorkspaceError,
)
from board.repository import BdCli
from board.service import BoardService
from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from board.repository import BdRunner
    from common.interfaces.board import BoardReader, BoardWriter

__all__ = [
    "BoardConfig",
    "BoardDeps",
    "BoardEnv",
    "BoardHandle",
    "build",
]

BEADS_CONFIG_FILENAME = "config.yaml"
"""`BEADS_DIR` 直下にあるべきファイル（`.beads` の体裁の目印。D19-b）。"""

CONFIG_FILENAME = "config.json"
"""board 固有の非機密設定。位置は規約の固定相対パス（憲章 §5・R-13）。"""


def config_path() -> Path:
    """`packages/features/board/config.json` の絶対パス（cwd 非依存＝ R-13）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


def env_file_path() -> Path:
    """リポジトリルートの `.env` の絶対パス（cwd 非依存）。

    `build()` は毎回この関数で解決するため、単体テストはここを差し替えて `.env` を隔離できる
    （実リポジトリの `.env` が `BEADS_DIR` を持つので、差し替えないと検査 a を再現できない）。
    """
    return find_repo_root() / ".env"


class BoardEnv(BaseSettings):
    """`.env` の環境依存値（CFG-07・08）。board だけが読む（FR-045）。

    解決順序は **init > OS 環境変数 > `.env`**（pydantic-settings の既定）。`.env` には他
    モジュールのキーが同居するため **`extra="ignore"` が必須**である。
    """

    model_config = SettingsConfigDict(
        env_file=env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    BEADS_DIR: Path
    BD_BIN: str = "bd"

    def __init__(self, **overrides: Any) -> None:
        """`.env` の所在（`_env_file`）と init 引数の差し替えを受ける。

        pydantic が合成する `__init__` はフィールドしか受け付けないため、`BaseSettings` の
        `_env_file` を渡すには明示的な `__init__` が要る（`build()` が `env_file_path()` で
        解決した `.env` を毎回渡す＝ 単体テストが `.env` を隔離できる）。
        """
        super().__init__(**overrides)


class BoardConfig(BaseSettings):
    """`packages/features/board/config.json` の挙動設定（CFG-22〜26）。

    ソースを `(init_settings, JsonConfigSettingsSource)` **だけ**に絞るため、環境変数で上書き
    できない（x3_notes §2「`*Config` は env ソースを持たない」）。キーはスコープ接頭辞を剥がした
    平坦形（R-13）。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    timeout_sec: int = 120
    lock_wait_max_attempts: int = 3
    lock_wait_base_sec: float = 0.5
    lock_hints: list[str] = [  # noqa: RUF012  pydantic がフィールド既定として扱う（共有されない）
        "lock",
        "locked",
        "resource temporarily unavailable",
        "busy",
    ]
    list_limit_none: bool = True

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
        """`⑧ build() の init 引数`による差し替えを受ける（FR-049・DIFF-08）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class BoardDeps:
    """board が他モジュールから受け取る依存。**空**（FR-003・D11-f）。"""


@dataclass(frozen=True, slots=True)
class BoardHandle:
    """`build()` の戻り。`board_reader` と `board_writer` は**同一インスタンス**（FR-005）。

    不変条件: `board_reader is board_writer`（`BdCli` のロックが割れないことの担保＝ R-07）。
    """

    board_reader: BoardReader
    board_writer: BoardWriter
    router: None = None


def build(deps: BoardDeps, *, config: BoardConfig | None = None) -> BoardHandle:
    """Board を組み立てる（起動時の 5 検査を順に行う。FR-047）。

    検査順は a（`BEADS_DIR` 未設定）→ b（実在しない）→ c（直下に `config.yaml` が無い）→
    d（`BD_BIN` を解決・実行できない）→ e（`config.json` の必須値欠け・未知キー）。
    **最初に失敗したものだけ**を報告する。**ホワイトリスト外のサブコマンドを実行しない**
    （`bd version` を打たず `shutil.which` ＋ `os.access` で確かめる＝ FR-048・R-15）。

    Args:
        deps: board が受け取る依存（空）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む。FR-049）。

    Returns:
        組み立て済みの `BoardHandle`（`router` は `None`）。

    Raises:
        BoardEnvError: `BEADS_DIR` が設定されていない。
        BoardWorkspaceError: `BEADS_DIR` が実在しない／直下に `config.yaml` が無い。
        BoardBinaryError: `BD_BIN` を解決できない／実行できない。
        BoardConfigError: `config.json` の必須値が欠けている／未知キーがある。
    """
    del deps  # board は他モジュールの契約に依存しない（FR-003）
    env = _load_env()  # (a)
    beads_dir = _checked_workspace(env.BEADS_DIR)  # (b)(c)
    bd_bin = _resolved_bd_bin(env.BD_BIN)  # (d)
    settings = config if config is not None else _load_config()  # (e)

    runner: BdRunner = BdCli(bd_bin=bd_bin, beads_dir=beads_dir, config=settings)  # 憲章 §3
    service = BoardService(runner, list_limit_none=settings.list_limit_none)
    board_reader: BoardReader = service  # 同一インスタンス（FR-005・R-07）
    board_writer: BoardWriter = service
    return BoardHandle(board_reader=board_reader, board_writer=board_writer, router=None)


def _load_env() -> BoardEnv:
    """(a) `.env` と OS 環境変数から 2 キーを読む。

    Raises:
        BoardEnvError: `BEADS_DIR` が無い（必要なキー名と読んだ `.env` の絶対パスを載せる）。
    """
    env_file = env_file_path()
    try:
        return BoardEnv(_env_file=env_file)
    except ValidationError as exc:
        msg = (
            f"board の起動に必要な環境依存値が読めない: {exc}"
            " / 必要なキー: BEADS_DIR（`.beads` 自身の絶対パス）"
            f" / 読んだ .env: {env_file}"
        )
        raise BoardEnvError(msg) from exc


def _checked_workspace(raw: Path) -> Path:
    """(b)(c) `BEADS_DIR` が実在し、直下に `config.yaml` があることを確かめる。

    Raises:
        BoardWorkspaceError: 実在しない／`.beads` の体裁でない。
    """
    beads_dir = raw.expanduser().resolve()
    if not beads_dir.is_dir():
        msg = f"BEADS_DIR が実在するディレクトリでない: {beads_dir}"
        raise BoardWorkspaceError(msg)
    marker = beads_dir / BEADS_CONFIG_FILENAME
    if not marker.is_file():
        msg = (
            f"BEADS_DIR が beads ワークスペースの体裁でない（{marker} が無い）: {beads_dir}"
            " / BEADS_DIR は `.beads` 自身であって、それを持つ親ではない（D19-a・D19-b）"
        )
        raise BoardWorkspaceError(msg)
    return beads_dir


def _resolved_bd_bin(raw: str) -> str:
    """(d) `BD_BIN` を絶対パスへ解決する。**`bd version` を打たない**（FR-048・R-15）。

    Raises:
        BoardBinaryError: 解決できない／実行権が無い。
    """
    resolved = shutil.which(raw)
    if resolved is None:
        msg = (
            f"BD_BIN を解決できない: {raw!r}"
            " / shutil.which が見つけられなかった（PATH 上に無いか、絶対パスが実在しない）"
        )
        raise BoardBinaryError(msg)
    if not os.access(resolved, os.X_OK):
        msg = f"BD_BIN に実行権が無い: {raw!r} → {resolved}（os.access(X_OK) が偽）"
        raise BoardBinaryError(msg)
    return resolved


def _load_config() -> BoardConfig:
    """(e) `config.json` を読む。

    Raises:
        BoardConfigError: 必須値が欠けている／未知キーがある。
    """
    path = config_path()
    try:
        return BoardConfig()
    except ValidationError as exc:
        msg = f"board の設定を読めない（{path}）: {exc}"
        raise BoardConfigError(msg) from exc
