"""orchestrator モジュールの公開口（設定・依存・ハンドル・`build`）。orchestrator-F15。

orchestrator は**外部ストアを持たず `.env` を読まない**。実行時のファイル I/O は `build()` 時の
`config.json` 読取 1 回だけで（FR-085・SC-035）、盤面・保管庫・設定へは 10 契約の Protocol 経由。

`exceptions.py`・`ui.py`・`repository.py` を置かない（FR-089）: 契約例外 `KickoffBusy` は
`common/interfaces/orchestrator.py` が正本、内部例外は `OrchestratorStartupError` 1 つだけ、
UI ページ無し、外部ストア無し。
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

from common.kernel.enums import PersonaKey
from orchestrator.models import Ports, RecordStore, SingleFlight
from orchestrator.router import create_kickoff_router
from orchestrator.service import OrchestratorService

if TYPE_CHECKING:
    from fastapi import APIRouter

    from common.interfaces.board import BoardReader, BoardWriter
    from common.interfaces.executor import CrewExecutor
    from common.interfaces.monitor import RunMonitor
    from common.interfaces.orchestrator import KickoffRunner
    from common.interfaces.repo import RepoReader
    from common.interfaces.settings import MemberResolver, SettingsReader
    from common.interfaces.stages import ProposalGate, StageCatalog
    from common.interfaces.vault import VaultReader

__all__ = [
    "OrchestratorConfig",
    "OrchestratorDeps",
    "OrchestratorHandle",
    "OrchestratorStartupError",
    "build",
    "config_path",
]

CONFIG_FILENAME = "config.json"
"""固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""


def config_path() -> Path:
    """`packages/features/orchestrator/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


class OrchestratorStartupError(RuntimeError):
    """起動時検査の失敗（FR-081）。**契約例外も `AppError` も継承しない**。"""


class OrchestratorConfig(BaseSettings):
    """`orchestrator/config.json` の挙動設定（CFG-57〜61）。**5 キーはすべて必須**。

    フィールド既定を置くと「必須値が欠けている」で `ValidationError` が起きず、契約 §3 の
    段 (a)（起動中断＝ FR-081）が到達不能になる。
    """

    # `strict=True`: `"2"` のような**型の不一致**を黙って int へ寄せない（契約 §6 の #2）
    model_config = SettingsConfigDict(extra="forbid", frozen=True, strict=True)

    max_returns: int
    """CFG-57。差し戻しの上限（**語のとおりの勘定**＝件数 `N >= max_returns` で人間へ渡す）。"""

    recent_results_n: int
    """CFG-58。`GET /kickoffs/{id}` が遡れる保持件数。"""

    system_actor: str
    """CFG-59。確保・解放・コメントの actor（P5D-037）。差し戻しの勘定の絞り込みにも使う。"""

    comment_marker: str
    """CFG-60。3 種のコメントの印。**差し戻しの前方一致の鍵**（FR-038）。"""

    review_persona: str
    """CFG-61。レビュー担当。**`PersonaKey` にしない**（R-09。検査は `build()` が行う）。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る（FR-082）。

        `env_settings`・`dotenv_settings`・`file_secret_settings` を渡さないので、環境変数でも
        `.env` でも上書きできない。

        `config_path()` を**クラス定義時ではなく呼び出し時**に解決する。module ロード時に
        評価する形へ「整理」すると、テストの差し替えが**黙って無効**になり実ファイルを読む
        （stages・monitor が踏んだ罠＝ R-08）。
        """
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path()))

    def __init__(self, **overrides: Any) -> None:
        """`build()` の init 引数による差し替えを受ける（FR-083）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class OrchestratorDeps:
    """orchestrator が受け取る 10 契約（10 本目の `repo_reader` は D25 の完了ゲート用）。"""

    board_reader: BoardReader
    board_writer: BoardWriter
    settings_reader: SettingsReader
    member_resolver: MemberResolver
    vault_reader: VaultReader
    run_monitor: RunMonitor
    stage_catalog: StageCatalog
    proposal_gate: ProposalGate
    crew_executor: CrewExecutor
    repo_reader: RepoReader


@dataclass(frozen=True, slots=True)
class OrchestratorHandle:
    """`build()` の戻り。**`router` に既定値を置かない**（必ず持つため）。"""

    kickoff_runner: KickoffRunner
    router: APIRouter


def build(
    deps: OrchestratorDeps, *, config: OrchestratorConfig | None = None
) -> OrchestratorHandle:
    """Orchestrator を組み立てる（orchestrator-F15）。

    Args:
        deps: orchestrator が受け取る 10 契約。
        config: 挙動設定の差し替え（省略時は `config.json` を読む）。

    Returns:
        組み立て済みの `OrchestratorHandle`。

    Raises:
        OrchestratorStartupError: 設定が壊れている／台帳照合に失敗した（FR-081・FR-084a）。
    """
    settings = config if config is not None else _load_config()  # (a)
    _check_thresholds(settings)  # (b)
    _check_review_persona(settings)  # (c)
    _check_comment_marker(settings)  # (d)
    _check_system_actor(deps, settings)  # (e) FR-084a
    ports = Ports(deps=deps, config=settings)
    service = OrchestratorService(
        ports=ports,
        flight=SingleFlight(),  # 両入口で共有する 1 つ（FR-006）
        records=RecordStore(settings.recent_results_n),
    )
    kickoff_runner: KickoffRunner = service  # 憲章 §3: Protocol 型を必ず注釈する
    return OrchestratorHandle(kickoff_runner=kickoff_runner, router=create_kickoff_router(service))


def _load_config() -> OrchestratorConfig:
    """(a) `config.json` を読む。

    Returns:
        読み込み済みの挙動設定。

    Raises:
        OrchestratorStartupError: ファイル不在・未知キー・型の不一致・必須値欠け
            （**見に行った絶対パス**を載せる）。
    """
    path = config_path()
    if not path.is_file():
        msg = f"orchestrator の設定が見つからない（{path}）"
        raise OrchestratorStartupError(msg)
    try:
        return OrchestratorConfig()
    except ValidationError as exc:
        msg = f"orchestrator の設定を読めない（{path}）: {exc}"
        raise OrchestratorStartupError(msg) from exc


def _check_thresholds(settings: OrchestratorConfig) -> None:
    """(b) `max_returns`・`recent_results_n` が 1 以上であることを確かめる。

    Raises:
        OrchestratorStartupError: 0 以下のとき（キー名と実際の値を載せる）。
    """
    path = config_path()
    for key, value in (
        ("max_returns", settings.max_returns),
        ("recent_results_n", settings.recent_results_n),
    ):
        if value < 1:
            msg = (
                f"orchestrator の設定の {key} は 1 以上でなければならない（{path}）: {key}={value}"
            )
            raise OrchestratorStartupError(msg)


def _check_review_persona(settings: OrchestratorConfig) -> None:
    """(c) `review_persona` が `PersonaKey` の 9 値のいずれかであることを確かめる。

    Raises:
        OrchestratorStartupError: 9 値に無いとき（**許される 9 値を列挙**する）。
    """
    allowed = [persona.value for persona in PersonaKey]
    if settings.review_persona not in allowed:
        msg = (
            f"orchestrator の設定の review_persona が許される値でない（{config_path()}）:"
            f" {settings.review_persona!r} / 許される値: {allowed}"
        )
        raise OrchestratorStartupError(msg)


def _check_comment_marker(settings: OrchestratorConfig) -> None:
    """(d) `comment_marker` が空文字でないことを確かめる。

    Raises:
        OrchestratorStartupError: 空のとき（前方一致が全コメントに当たるため）。
    """
    if not settings.comment_marker:
        msg = (
            f"orchestrator の設定の comment_marker が空（{config_path()}）:"
            " 印が空だと差し戻しの前方一致が全コメントに当たる"
        )
        raise OrchestratorStartupError(msg)


def _check_system_actor(deps: OrchestratorDeps, settings: OrchestratorConfig) -> None:
    """(e) 台帳がシステム actor を**人間として知っていない**ことを確かめる（FR-084a）。

    他モジュールの設定値を直接読まず、`MemberResolver.is_human` を **1 回だけ**叩く
    （憲章 §2・FR-084b・SC-034a）。`is_human` は `bool` を返す契約で `Raises` を宣言しない
    ので、**`except Exception` で包まない**（黙って通すより落ちたほうがよい）。

    Raises:
        OrchestratorStartupError: 台帳が人間と判定したとき。
    """
    if deps.member_resolver.is_human(settings.system_actor):
        msg = (
            f"orchestrator の設定の system_actor を台帳が人間として知っている"
            f"（{config_path()}）: system_actor={settings.system_actor!r} /"
            " settings 側の system_actor と同値であるべき"
        )
        raise OrchestratorStartupError(msg)
