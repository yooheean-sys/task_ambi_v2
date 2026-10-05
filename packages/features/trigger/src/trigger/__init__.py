"""trigger モジュールの公開口（設定 2 クラス・`TriggerDeps`・`TriggerHandle`・`build`）。

trigger は**設定ファイル領域 B（`<TRIGGER_ROOT>` 配下）の唯一の所有者**であり（憲章 §2
例外 (a)(b)）、**提供 Protocol を 1 本も持たない**（`p4_module_contract.csv` に「提供＝trigger」の
行が無い＝ FR-002）。したがって `packages/common/` を 1 行も変えない。

⚠ **UI ページ（`trigger/ui.py`）を export しない**（FR-070・CLAUDE.md）。export すると
`api.registry → trigger → trigger.ui → nicegui` の間接連鎖ができ、`nicegui-scope` 契約に
手当てが要る（`016` で `streamlit-scope` を `nicegui-scope` に置き換えた）。`apps/ui` は
`trigger.ui` を**直接** import する。
"""

from __future__ import annotations

import zoneinfo
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import ValidationError
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from common.kernel.config import find_repo_root
from trigger.exceptions import (
    TriggerConfigError,
    TriggerEnvError,
    TriggerRootError,
)
from trigger.repository import DefinitionsRepository, HistoryRepository
from trigger.router import create_trigger_router
from trigger.service import TriggerService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractAsyncContextManager

    from common.interfaces.orchestrator import KickoffRunner
    from trigger.service import TriggerService

__all__ = [
    "TriggerConfig",
    "TriggerDeps",
    "TriggerEnv",
    "TriggerHandle",
    "build",
    "config_path",
    "env_file_path",
]

CONFIG_FILENAME = "config.json"
"""trigger 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""


def config_path() -> Path:
    """`packages/features/trigger/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


def env_file_path() -> Path:
    """リポジトリルートの `.env` の絶対パス（cwd 非依存）。

    `build()` は毎回この関数で解決するため、テストはここを差し替えて `.env` を隔離できる。
    ⚠ **trigger の止め木は settings より重い** —— 外れると実運用の領域 B に雛形と履歴 csv が
    書かれ、**実運用の Windows Downloads に `PollingObserver` が張り付いて投下ファイルで
    実際に kickoff が起きうる**（plan (v)）。
    """
    return find_repo_root() / ".env"


class TriggerEnv(BaseSettings):
    """`.env` の環境依存値（CFG-06・CFG-09）。trigger **だけ**がこれを読む。

    解決順序は **init > OS 環境変数 > `.env`**（pydantic-settings の既定）。`.env` には他
    モジュールのキーが同居するため **`extra="ignore"` が必須**である。
    """

    model_config = SettingsConfigDict(
        env_file=env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    TRIGGER_ROOT: Path
    """設定ファイル領域 B のルート（**必須**。欠けたら起動中断＝ FR-073・憲章 §5）。"""

    DOWNLOADS_DIR: Path | None = None
    """DL フォルダ（**任意**）。未設定でも起動する（FR-074・SC-011）。`file` 種別の保存は
    拒否し（FR-025）、宣言に在る `file` は当該 1 件だけ登録を飛ばす（F-07 (b)）。"""

    def __init__(self, **overrides: Any) -> None:
        """`.env` の所在（`_env_file`）と init 引数の差し替えを受ける。

        pydantic が合成する `__init__` はフィールドしか受け付けないため、`BaseSettings` の
        `_env_file` を渡すには明示的な `__init__` が要る。`model_config` の
        `env_file=env_file_path()` は**クラス定義時に 1 回しか評価されない**ので、本メソッドと
        `build()` の `TriggerEnv(_env_file=env_file_path())` が**対で**初めて、テストの
        autouse 止め木（`monkeypatch.setattr("trigger.env_file_path", …)`）が効く（R-13）。
        **`TriggerEnv()` の形に「整理」しない。**
        """
        super().__init__(**overrides)


class TriggerConfig(BaseSettings):
    """`packages/features/trigger/config.json` の挙動設定（CFG-62〜68。**7 キーちょうど**）。

    ソースを `(init_settings, JsonConfigSettingsSource)` **だけ**に絞るため、環境変数で
    上書きできない（`x3_notes.md` §2）。`extra="forbid"` なので未知キーは起動中断になる。

    **不変条件は `build()` の段 (d) が検査する**（`TriggerConfigError`）。モデル側で検査しないのは、
    `build(deps, config=TriggerConfig(...))` の差し替え（CFG-67 の唯一の手段＝ R-13）でも同じ
    段で同じ例外に翻訳されるようにするためである。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    ready_interval_default_sec: int = 300
    """CFG-62。既定トリガー（`ready 巡回`）の初期間隔。**コードに直書きしない**（FR-020）。"""

    min_interval_sec: int = 60
    """CFG-63。`interval` 種別の下限（V-4 の拒否理由に載る値＝ FR-023）。"""

    misfire_grace_sec: int = 30
    """CFG-64。`add_job(misfire_grace_time=…)` に渡す猶予（FR-032）。"""

    poll_interval_sec: float = 5.0
    """CFG-65。`PollingObserver(timeout=…)`。実測 M-02・M-08。"""

    stable_seconds: int = 2
    """CFG-66。完了検知の安定判定の待ち。実測 M-03・M-08。"""

    watcher_enabled: bool = True
    """CFG-67。`false` なら observer を作らない（退避経路の完了検知は動く＝ S-4）。"""

    timezone: str | None = None
    """CFG-68。`None` はシステムローカル（D15-m）。`CronTrigger.from_crontab` へ渡す。"""

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
        """`⑧ build() の init 引数`による差し替えを受ける（CFG-67・R-13）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class TriggerDeps:
    """trigger が他モジュールから受け取る依存（**1 件だけ**＝ FR-015）。

    `KickoffRunner`（IF-49）は `fire()` の段 6 で**同期に**呼ぶ。盤面・vault・領域 A・LLM への
    経路は 1 本も持たない。
    """

    kickoff_runner: KickoffRunner


@dataclass(frozen=True, slots=True)
class TriggerHandle:
    """`build()` の戻り。**`router` も `lifespan` も既定値を持たない**（trigger は必ず両方を持つ）。

    `lifespan` は `api.wiring.LifespanFactory`（`Callable[[], AbstractAsyncContextManager[None]]`）
    に**構造的に適合**する（`api` を import しない＝層契約。R-03・SD-17）。
    """

    router: APIRouter
    lifespan: Callable[[], AbstractAsyncContextManager[None]]


def _make_lifespan(service: TriggerService) -> Callable[[], AbstractAsyncContextManager[None]]:
    """常駐の起動停止を `LifespanFactory` に適合する形で包む（R-03・SD-17）。

    **呼ぶたびに新しいコンテキストを返す**（`@asynccontextmanager` を付けた関数は呼ぶたびに
    新しい `_AsyncGeneratorContextManager` を作る）。土台の
    `test_smoke_lifespan.py::test_lifespan_factory_is_reentrant` が同じ登録で 2 回入って
    開始 2・停止 2 を固定しているので、**コンテキストを使い回してはならない**。

    ⚠ `service.stop()` の `shutdown(wait=True)` は**イベントループを塞ぐ**（実測 0.951 秒＝ C-4）。
    **同期のまま呼ぶ**（`asyncio.to_thread` に逃がさない）—— 停止中に他の要求を捌く必要が無く、
    逃がしても待ち時間は変わらないため。

    Args:
        service: 起動停止する常駐サービス。

    Returns:
        引数なしで呼べる非同期コンテキストマネージャの工場。
    """

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[None]:
        service.start()
        try:
            yield
        finally:
            service.stop()

    return lifespan


def build(deps: TriggerDeps, *, config: TriggerConfig | None = None) -> TriggerHandle:
    """Trigger を組み立てる（起動時の段 (a)〜(f) を順に行う。plan (v)）。

    **最初に失敗した段だけを報告する**（原因が 1 つに定まる方が追いやすい）。**スレッドを
    1 本も起こさない**（常駐部品は `start()` が毎回作る＝ SD-23）。

    Args:
        deps: trigger が受け取る依存（`KickoffRunner`＝ IF-49 の 1 件だけ）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む。CFG-67 の唯一の差し替え手段）。

    Returns:
        組み立て済みの `TriggerHandle`（6 パス・10 操作の router と常駐の lifespan）。

    Raises:
        TriggerEnvError: `TRIGGER_ROOT` が読めない（(a)）。
        TriggerRootError: `TRIGGER_ROOT` が絶対でない／実在しない／ディレクトリでない（(b)）、
            または `DOWNLOADS_DIR` が**設定されていて**同じ点に反する（(c)）。
        TriggerConfigError: `config.json` が読めない／不変条件違反（(d)）。
        TriggerDefinitionsError: `triggers.yaml` が壊れている／書けない（(e)）。
    """
    env = _load_env()  # (a)
    _check_dir("TRIGGER_ROOT", env.TRIGGER_ROOT)  # (b)
    root = env.TRIGGER_ROOT.expanduser()
    downloads_dir: Path | None = None
    if env.DOWNLOADS_DIR is not None:  # (c) **未設定は許容**（FR-074・SC-011）
        _check_dir("DOWNLOADS_DIR", env.DOWNLOADS_DIR)
        downloads_dir = env.DOWNLOADS_DIR.expanduser()
    settings = _load_config(config)  # (d)

    definitions = DefinitionsRepository(root)
    # (e) 領域 B を読む（無ければ雛形を生成し、あれば解釈できることを確かめる＝ F-10 (a)）。
    #     **build の結果を保持して使い回さない** —— lifespan で読み直す（D07-a）。
    definitions.ensure_template(settings.ready_interval_default_sec)
    definitions.load_definitions()

    # (f)
    service = TriggerService(
        deps=deps,
        config=settings,
        definitions=definitions,
        history=HistoryRepository(root),
        downloads_dir=downloads_dir,
    )
    return TriggerHandle(router=create_trigger_router(service), lifespan=_make_lifespan(service))


def _load_env() -> TriggerEnv:
    """(a) `.env` と OS 環境変数から `TRIGGER_ROOT`・`DOWNLOADS_DIR` を読む。

    `TriggerEnv(_env_file=env_file_path())` の形で**毎回**渡す。`TriggerEnv` の明示 `__init__`
    と対で初めてテストの autouse 止め木が効く（R-13）。**`TriggerEnv()` の形に「整理」しない。**

    Raises:
        TriggerEnvError: `TRIGGER_ROOT` が無い／`.env` が読めない（**必要なキー名と読んだ
            `.env` の絶対パス**を載せる）。
    """
    try:
        return TriggerEnv(_env_file=env_file_path())
    except (ValidationError, OSError, ValueError) as exc:
        msg = (
            f"trigger の起動に必要な環境依存値が読めない: {type(exc).__name__}: {exc}"
            " / 必要なキー: TRIGGER_ROOT（設定ファイル領域 B のルートの絶対パス）"
            f" / 読んだ .env: {env_file_path()}"
        )
        raise TriggerEnvError(msg) from exc


def _check_dir(key: str, raw: Path) -> None:
    """(b)(c) 絶対パス → 実在 → ディレクトリ の順に確かめる。

    ⚠ **絶対パスの検査を最初に置く** —— `TRIGGER_ROOT=`（空値）を pydantic は `Path(".")` として
    受理し、相対値もそのまま通る。この検査が無いと `Path(".")` が cwd に解決されて
    `exists()`・`is_dir()` の両方が真になり、段 (e) が**カレントディレクトリに雛形を書き込む**。

    **解決結果を返さない**（`realpath` して保持しない）。本検査は**その時点の**実在確認である。

    Raises:
        TriggerRootError: 絶対パスでない／実在しない／ディレクトリでない（**見に行った
            絶対パス**を載せる。絶対パスでない場合は宣言された値そのもの）。
    """
    if not raw.expanduser().is_absolute():
        msg = f"{key} が絶対パスでない: 「{raw}」 / 絶対パスで指定する"
        raise TriggerRootError(msg)
    path = raw.expanduser().resolve()
    if not path.exists():
        msg = f"{key} が実在しない: {path}"
        raise TriggerRootError(msg)
    if not path.is_dir():
        msg = f"{key} がディレクトリでない: {path}"
        raise TriggerRootError(msg)


def _load_config(config: TriggerConfig | None) -> TriggerConfig:
    """(d) `config.json` を読み、**不変条件**を検査する。

    不変条件は 3 つ（plan (v)）:

    1. `misfire_grace_sec < min_interval_sec <= ready_interval_default_sec`
    2. `poll_interval_sec > 0`・`stable_seconds >= 1`
    3. `timezone` が `None` でなければ `zoneinfo.ZoneInfo` で解決できる

    Raises:
        TriggerConfigError: 読めない／未知キー／不変条件違反（**見に行った絶対パス**）。
    """
    path = config_path()
    try:
        settings = config if config is not None else TriggerConfig()
    except (ValidationError, OSError, ValueError, TypeError) as exc:
        msg = f"trigger の設定を読めない（{path}）: {type(exc).__name__}: {exc}"
        raise TriggerConfigError(msg) from exc
    _check_invariants(settings, path)
    return settings


def _check_invariants(settings: TriggerConfig, path: Path) -> None:
    """(d) の不変条件 3 種（違反は起動中断＝ FR-075）。

    Raises:
        TriggerConfigError: いずれかの不変条件に反する。
    """
    problems: list[str] = []
    if not (
        settings.misfire_grace_sec
        < settings.min_interval_sec
        <= settings.ready_interval_default_sec
    ):
        problems.append(
            "misfire_grace_sec < min_interval_sec <= ready_interval_default_sec を満たさない:"
            f" {settings.misfire_grace_sec} / {settings.min_interval_sec}"
            f" / {settings.ready_interval_default_sec}"
        )
    if settings.poll_interval_sec <= 0:
        problems.append(f"poll_interval_sec は正の数にする: {settings.poll_interval_sec}")
    if settings.stable_seconds < 1:
        problems.append(f"stable_seconds は 1 以上にする: {settings.stable_seconds}")
    if settings.timezone is not None:
        try:
            zoneinfo.ZoneInfo(settings.timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            problems.append(f"timezone を解決できない: 「{settings.timezone}」（{exc}）")
    if problems:
        joined = " / ".join(problems)
        msg = f"trigger の設定が不変条件に反する（{path}）: {joined}"
        raise TriggerConfigError(msg)
