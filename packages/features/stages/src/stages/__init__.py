"""stages モジュールの公開口（`StagesConfig`・`StagesDeps`・`StagesHandle`・`build`）。stages-F08。

stages は**外部ストアを持たず `.env` を読まない**純粋 MOD である。I/O は `build()` 時の
`config.json` 読取 1 回だけで、4 メソッドはファイルにもネットワークにも触れない
（FR-008・FR-010）。HTTP 経路も UI ページも持たないため `router.py`・`ui.py`・`schemas.py`・
`dependencies.py` を置かない（FR-007・FR-063）。

`exceptions.py` を置かない理由: 契約例外（`UnknownStage`・`UnknownPersona`）は
`common/interfaces/stages.py` が正本であり stages では再定義しない（FR-002）。内部例外は
起動時の `StagesConfigError` 1 つだけなので、公開口である本ファイルに同居させる。
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

from common.kernel.enums import StageName
from stages.models import AcceptanceRules, build_stage_table
from stages.service import StagesService

if TYPE_CHECKING:
    from common.interfaces.stages import ProposalGate, StageCatalog

__all__ = [
    "StagesConfig",
    "StagesConfigError",
    "StagesDeps",
    "StagesHandle",
    "build",
    "config_path",
]

CONFIG_FILENAME = "config.json"
"""stages 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""


def config_path() -> Path:
    """`packages/features/stages/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


class StagesConfigError(RuntimeError):
    """起動時検査の失敗（FR-055・FR-056）。**契約例外も `AppError` も継承しない**。

    契約例外の系統（`NotFoundError`）は**モジュール間契約の失敗**を表す語彙であり、設定不備は
    契約の失敗ではない。加えて層契約により stages は apps/api の起動時例外階層を参照できない
    （board・vault・settings と同一の判断）。基底を `RuntimeError` に置くのも先行 3 モジュール
    （`VaultStartupError`・`SettingsStartupError`）に揃えたものである。
    """


class StagesConfig(BaseSettings):
    """`packages/features/stages/config.json` の挙動設定（CFG-47〜50）。

    ソースを `(init_settings, JsonConfigSettingsSource)` **だけ**に絞るため、環境変数で
    上書きできない（FR-057）。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    # noqa: RUF012 — pydantic がフィールド既定として扱う（インスタンス間で共有されない）
    min_items_override: dict[str, int] = {}  # noqa: RUF012
    """CFG-47。段名 → 最小件数の上書き。

    **型を `dict[StageName, int]` にしない**（research R-07）。pydantic が未知の段名を素の
    `ValidationError` で弾くと、FR-055 が求める「見に行った絶対パスを示す起動中断」に
    ならない。段名の検査は `build()` が自分で行う。
    """

    min_acceptance_len: int = 10
    """CFG-48。受入条文 1 件の最小長。"""

    placeholder_patterns: list[str] = []  # noqa: RUF012  同上
    """CFG-49。穴埋め文言の集合（出荷時既定は PoC の 11 値）。"""

    max_acceptance_default: int | None = None
    """CFG-50。受入条文の既定上限。**`None` ＝上限なし**。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る（FR-057）。

        `config_path()` を**クラス定義時ではなく呼び出し時**に解決する。
        `JsonConfigSettingsSource(..., json_file=config_path())` をモジュールロード時に
        評価する形へ「整理」すると、テストの差し替えが**黙って無効**になり実ファイルを読む。
        """
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path()))

    def __init__(self, **overrides: Any) -> None:
        """`build()` の init 引数による差し替えを受ける（FR-057）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class StagesDeps:
    """stages が他モジュールから受け取る依存。**空**（FR-006）。"""


@dataclass(frozen=True, slots=True)
class StagesHandle:
    """`build()` の戻り。`stage_catalog` と `proposal_gate` は**同一インスタンス**（FR-005）。"""

    stage_catalog: StageCatalog
    proposal_gate: ProposalGate
    router: None = None


def build(deps: StagesDeps, *, config: StagesConfig | None = None) -> StagesHandle:
    """Stages を組み立てる（T040 で実装）。

    Args:
        deps: stages が受け取る依存（空。FR-006）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む。FR-057）。

    Returns:
        組み立て済みの `StagesHandle`（`router` は `None`）。`stage_catalog is proposal_gate`。

    Raises:
        StagesConfigError: 設定が壊れている（FR-055・FR-055a・FR-056）。
    """
    del deps  # stages は他モジュールの契約に依存しない（FR-006）
    settings = config if config is not None else _load_config()  # (a)
    _check_stage_names(settings)  # (b)
    _check_thresholds(settings)  # (c)(d)(e)
    service = StagesService(
        rows=build_stage_table(settings), rules=AcceptanceRules.from_config(settings)
    )
    stage_catalog: StageCatalog = service  # 憲章 §3: Protocol 型を必ず注釈する（同一インスタンス）
    proposal_gate: ProposalGate = service
    return StagesHandle(stage_catalog=stage_catalog, proposal_gate=proposal_gate, router=None)


def _load_config() -> StagesConfig:
    """(a) `config.json` を読む。

    Returns:
        読み込み済みの挙動設定。

    Raises:
        StagesConfigError: 必須値が欠けている／未知キーがある（**見に行った絶対パス**を載せる）。
    """
    path = config_path()
    try:
        return StagesConfig()
    except ValidationError as exc:
        msg = f"stages の設定を読めない（{path}）: {exc}"
        raise StagesConfigError(msg) from exc


def _check_stage_names(settings: StagesConfig) -> None:
    """(b) `min_items_override` のキーが段表にある段名かを確かめる（FR-055）。

    **`StagesConfig` の validator に寄せない** —— 検査の主語は段表（`models.py`）であり、
    器がコード定数に依存すると設定だけを読む単体テストが段表の変更で落ちる。器の型を
    `dict[StageName, int]` にしないのも同じ理由で、pydantic が素の `ValidationError` を出すと
    FR-055 が求める「見に行った絶対パスを示す起動中断」にならない（research R-07）。

    Raises:
        StagesConfigError: 段表に無い段名がある（絶対パス ＋ 未知の段名 ＋ 既知 5 段を載せる）。
    """
    known = [str(name) for name in StageName]
    unknown = sorted(key for key in settings.min_items_override if key not in known)
    if unknown:
        msg = (
            f"stages の設定に段表が知らない段名がある（{config_path()}）:"
            f" min_items_override={unknown} / 既知の段: {known}"
        )
        raise StagesConfigError(msg)


def _check_thresholds(settings: StagesConfig) -> None:
    """(c)(d)(e) 閾値が 1 以上かを確かめる（FR-055a）。

    `max_acceptance_default` の `None`（＝上限なし）は許す。

    Raises:
        StagesConfigError: 1 未満の閾値がある（**見に行った絶対パス**と値を載せる）。
    """
    path = config_path()
    for stage_name, value in sorted(settings.min_items_override.items()):
        if value < 1:
            msg = f"stages の設定の min_items_override が 1 未満（{path}）: {stage_name}={value}"
            raise StagesConfigError(msg)
    if settings.min_acceptance_len < 1:
        msg = (
            f"stages の設定の min_acceptance_len が 1 未満（{path}）: {settings.min_acceptance_len}"
        )
        raise StagesConfigError(msg)
    if settings.max_acceptance_default is not None and settings.max_acceptance_default < 1:
        msg = (
            f"stages の設定の max_acceptance_default が 1 未満（{path}）:"
            f" {settings.max_acceptance_default}"
        )
        raise StagesConfigError(msg)
