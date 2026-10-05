"""monitor モジュールの公開口（`MonitorConfig`・`MonitorDeps`・`MonitorHandle`・`build`）。

monitor-F12。

monitor は**外部ストアを持たず `.env` を読まない**。I/O は `build()` 時の `config.json` 読取
1 回だけで、盤面へは board の Protocol 経由でのみ触れる（`bd` を直接呼ばない）。HTTP 経路も
UI ページも持たないため `router.py`・`ui.py`・`schemas.py`・`dependencies.py` を置かない（FR-002）。

`exceptions.py` を置かない理由: 契約例外（`RunHalted`・`MappingUnverified`）は
`common/interfaces/monitor.py` が正本であり monitor では再定義しない（FR-002a）。内部例外は
起動時の `MonitorStartupError` 1 つだけなので、公開口である本ファイルに同居させる（stages と同型）。
"""

from __future__ import annotations

import importlib.util
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

from common.interfaces.monitor import FailureKind, Grade
from monitor.repository import HttpHook
from monitor.service import MonitorService

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter
    from common.interfaces.monitor import LLMHttpHook, RunMonitor

__all__ = [
    "MonitorConfig",
    "MonitorDeps",
    "MonitorHandle",
    "MonitorStartupError",
    "build",
    "config_path",
]

CONFIG_FILENAME = "config.json"
"""monitor 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""


def config_path() -> Path:
    """`packages/features/monitor/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


ESTIMATORS = ("chars", "tiktoken")
"""`token_estimator` に許される 2 値（CFG-43）。**これ以外は起動中断**（FR-065）。"""


class MonitorStartupError(RuntimeError):
    """起動時検査の失敗（FR-062）。**契約例外も `AppError` も継承しない**。

    契約例外の系統は**モジュール間契約の失敗**を表す語彙であり、設定不備は契約の失敗ではない。
    層契約により monitor は apps/api の起動時例外階層を参照できない（先行 4 モジュールと同一）。
    """


class MonitorConfig(BaseSettings):
    """`packages/features/monitor/config.json` の挙動設定（CFG-41〜46）。

    **6 キーはすべて必須**（欠落は起動中断＝ FR-062）。フィールド既定を置くと「必須値が
    欠けている」で `ValidationError` が起きず、契約 §5 の段 (a) が到達不能になる（黙って
    `grade_map={}`・`truncation_phrases=[]` で起動し、全 12 種が `_grade_of()` の F 倒しに
    落ちて `truncated_answer` が永久に発火しない＝無症状の劣化）。

    **`grade_map` の 12 エントリの欠落は中断理由にしない**（FR-061b。`_grade_of()` が F に
    倒す）。2 つの「欠落」を混同しないこと —— 中断するのは**キーそのものの不在**である。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    grade_map: dict[str, str]
    """CFG-41。種類 → 等級。**コードに写しを作らない**（FR-043）。

    **型を `dict[FailureKind, Grade]` にしない**（stages R-07 と同型）。pydantic が未知の
    キー・値を素の `ValidationError` で弾くと、FR-062 が求める「見に行った絶対パスを示す
    起動中断」にならない。検査は `build()` が自分で行う。
    """

    envelope_marker: str
    """CFG-42。封筒の 1 行目の印。**確認読取の照合にも使う**（FR-054a）。"""

    token_estimator: str
    """CFG-43。送信前トークン推定の方式（**出荷時既定は `chars`**＝ SD-14）。"""

    token_encoding: str
    """CFG-44。`tiktoken` を選んだときのエンコーディング名。"""

    cost_field_path: str
    """CFG-45。応答 JSON から費用を読む経路（`.` 区切り）。"""

    truncation_phrases: list[str]
    """CFG-46。**送信前の `messages[-1]` 照合にのみ使う**（FR-040a・SD-03）。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る（FR-063）。

        `env_settings`・`dotenv_settings`・`file_secret_settings` を渡さないので、環境変数でも
        `.env` でも上書きできない（FR-010・US4 AC-5）。

        `config_path()` を**クラス定義時ではなく呼び出し時**に解決する。
        `JsonConfigSettingsSource(..., json_file=config_path())` をモジュールロード時に評価する
        形へ「整理」すると、テストの差し替えが**黙って無効**になり実ファイルを読む（stages の罠）。
        """
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path()))

    def __init__(self, **overrides: Any) -> None:
        """`build()` の init 引数による差し替えを受ける（FR-059）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class MonitorDeps:
    """monitor が他モジュールから受け取る依存（盤面の 2 契約）。"""

    board_reader: BoardReader
    board_writer: BoardWriter


@dataclass(frozen=True, slots=True)
class MonitorHandle:
    """`build()` の戻り。`run_monitor` と `llm_http_hook` は**別インスタンス**。"""

    run_monitor: RunMonitor
    llm_http_hook: LLMHttpHook
    router: None = None


def build(deps: MonitorDeps, *, config: MonitorConfig | None = None) -> MonitorHandle:
    """Monitor を組み立てる（monitor-F12）。

    Args:
        deps: monitor が受け取る依存（盤面の読取と書込）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む）。

    Returns:
        組み立て済みの `MonitorHandle`（`router` は `None`）。

    Raises:
        MonitorStartupError: 設定が壊れている（FR-061〜FR-065）。
    """
    settings = config if config is not None else _load_config()  # (1)
    _check_grade_map(settings)  # (2)  (b)(c)
    _check_token_estimator(settings)  # (3)  (d)
    hook = HttpHook(config=settings)  # (4)
    service = MonitorService(hook=hook, deps=deps, config=settings)  # (5)
    run_monitor: RunMonitor = service  # (6) 憲章 §3: Protocol 型を必ず注釈する
    llm_http_hook: LLMHttpHook = hook
    return MonitorHandle(  # (7)
        run_monitor=run_monitor, llm_http_hook=llm_http_hook, router=None
    )


def _load_config() -> MonitorConfig:
    """(a) `config.json` を読む。

    Returns:
        読み込み済みの挙動設定。

    Raises:
        MonitorStartupError: 未知キーがある／必須値の型が合わない（**絶対パス**を載せる）。
    """
    path = config_path()
    try:
        return MonitorConfig()
    except ValidationError as exc:
        msg = f"monitor の設定を読めない（{path}）: {exc}"
        raise MonitorStartupError(msg) from exc


def _check_grade_map(settings: MonitorConfig) -> None:
    """(b)(c) `grade_map` の値と キーを確かめる（contracts/monitor-config.md §5）。

    **キーの欠落は中断の理由にしない**（FR-061b）。欠けた種類は実行時に `_grade_of()` が
    **F に倒し警告**する（FR-045）。

    Raises:
        MonitorStartupError: 値が `F`／`W`／`I` 以外／キーが `FailureKind` の 12 種に無い。
    """
    path = config_path()
    allowed = [grade.value for grade in Grade]
    for key, value in sorted(settings.grade_map.items()):  # (b) 値を先に見る
        if value not in allowed:
            msg = (
                f"monitor の設定の grade_map に等級でない値がある（{path}）:"
                f" {key}={value!r} / 許される値: {allowed}"
            )
            raise MonitorStartupError(msg)
    known = [kind.value for kind in FailureKind]
    unknown = sorted(key for key in settings.grade_map if key not in known)
    if unknown:  # (c)
        msg = (
            f"monitor の設定の grade_map に知らない種類がある（{path}）:"
            f" {unknown} / 既知の種類: {known}"
        )
        raise MonitorStartupError(msg)


def _check_token_estimator(settings: MonitorConfig) -> None:
    """(d) 推定の方式が使えるかを確かめる（**黙って別の方式に落とさない**＝ FR-065）。

    `tiktoken` を指定したときだけ `importlib.util.find_spec` を見る。**`import tiktoken` を
    書かない** —— 書くと monitor が `tiktoken` に静的依存し、`grimp` の依存グラフと
    `pyproject.toml` の実態が食い違う（research R-10）。既定（`chars`）では `find_spec` すら
    呼ばない。

    Raises:
        MonitorStartupError: 方式が 2 値のいずれでもない／`tiktoken` を指定して未導入。
    """
    path = config_path()
    estimator = settings.token_estimator
    if estimator not in ESTIMATORS:
        msg = (
            f"monitor の設定の token_estimator が許される値でない（{path}）:"
            f" {estimator!r} / 許される値: {list(ESTIMATORS)}"
        )
        raise MonitorStartupError(msg)
    if estimator == "tiktoken" and importlib.util.find_spec("tiktoken") is None:
        msg = (
            f"monitor の設定が token_estimator=tiktoken を指定しているが"
            f" tiktoken が導入されていない（{path}）"
        )
        raise MonitorStartupError(msg)
