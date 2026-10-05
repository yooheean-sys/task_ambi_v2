"""settings モジュールの公開口（設定 2 クラス・`SettingsDeps`・`SettingsHandle`・`build`）。

settings は**設定ファイル領域 A（`<SETTINGS_ROOT>` 配下）の唯一の所有者**である
（憲章 §2 例外 (a)）。設定クラス（`SettingsEnv`・`SettingsConfig`）は「公開口の一部」として
本ファイルに同居させる（board・vault と同型）。

⚠ **UI ページ（`settings/ui.py`）を export しない**（010 SD-07）。export すると
`api.registry → settings → settings.ui → nicegui` の間接連鎖ができ、`nicegui-scope` 契約に
`allow_indirect_imports` の手当てが要る（`016` で `streamlit-scope` を `nicegui-scope` に
置き換えた）。`apps/ui` は `settings.ui` を**直接** import する
（`test_ui_boundaries.py` の U-2 が `<mod>.ui` だけを許す＝ F-05）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from common.interfaces.settings import ProviderKind
from common.kernel.config import find_repo_root
from settings.exceptions import (
    SettingsAreaError,
    SettingsConfigError,
    SettingsEnvError,
    SettingsRootError,
    SettingsValidationError,
)
from settings.repository import AreaAReader, AreaAScaffolder, AreaAWriter
from settings.router import create_settings_router
from settings.service import SettingsService
from settings.service.crud import SettingsCrudService
from settings.service.loader import build_snapshot, read_area
from settings.service.validate import validate_commands, validate_forward, validate_reverse

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader
    from common.interfaces.settings import (
        Authorizer,
        CommandRegistry,
        MemberResolver,
        SettingsReader,
    )
    from common.interfaces.vault import VaultReader
    from settings.models import AreaAData, ValidationProblem

__all__ = [
    "LimitsDefaults",
    "LlmPreset",
    "SettingsConfig",
    "SettingsDeps",
    "SettingsEnv",
    "SettingsHandle",
    "build",
    "config_path",
    "defaults_path",
    "env_file_path",
]

CONFIG_FILENAME = "config.json"
"""settings 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""

DEFAULTS_DIRNAME = "defaults"
"""出荷時既定の同梱フォルダ（D16-g・R-23）。"""


def config_path() -> Path:
    """`packages/features/settings/config.json` の絶対パス（cwd 非依存）。"""
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


def defaults_path() -> Path:
    """`packages/features/settings/defaults/` の絶対パス（cwd 非依存。R-23・M-27）。

    `importlib.resources` 等の新機構を持ち込まない。board・vault の `config_path()` と同型で、
    uv workspace の editable インストール＋非コンテナ常駐という配備形に乗る。
    """
    return Path(__file__).resolve().parents[2] / DEFAULTS_DIRNAME


def env_file_path() -> Path:
    """リポジトリルートの `.env` の絶対パス（cwd 非依存）。

    `build()` は毎回この関数で解決するため、テストはここを差し替えて `.env` を隔離できる
    （実リポジトリの `.env` が `SETTINGS_ROOT` を実値で持つ＝ FR-019・SC-015）。
    """
    return find_repo_root() / ".env"


class SettingsEnv(BaseSettings):
    """`.env` の環境依存値（CFG-05）。settings **だけ**がこれを読む。

    解決順序は **init > OS 環境変数 > `.env`**（pydantic-settings の既定＝⑥→⑦）。`.env` には
    他モジュールのキーが同居するため **`extra="ignore"` が必須**である。
    """

    model_config = SettingsConfigDict(
        env_file=env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    SETTINGS_ROOT: Path

    def __init__(self, **overrides: Any) -> None:
        """`.env` の所在（`_env_file`）と init 引数の差し替えを受ける。

        pydantic が合成する `__init__` はフィールドしか受け付けないため、`BaseSettings` の
        `_env_file` を渡すには明示的な `__init__` が要る。`model_config` の
        `env_file=env_file_path()` は**クラス定義時に 1 回しか評価されない**ので、本メソッドと
        `build()` の `SettingsEnv(_env_file=env_file_path())` が**対で**初めて、テストの
        autouse 止め木（`monkeypatch.setattr("settings.env_file_path", …)`）が効く。

        ⚠ **settings の build は書込を行う**（不在なら雛形を生成する）。どちらかを落とすと
        止め木は**黙って無効**になり、テストが**実運用の領域 A に雛形を書き込む**。読むだけの
        vault と違い **1 回で観測可能な被害が出る**（FR-019・SC-015 の担保はこの 1 点に依存する）。
        """
        super().__init__(**overrides)


class LlmPreset(BaseModel):
    """UI の新規プロファイル入力の雛形 1 件（CFG-38）。**`api_key` の実値を持たない**。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    provider_kind: ProviderKind
    base_url: str


class LimitsDefaults(BaseModel):
    """上限の出荷時既定（CFG-39 の ③ 段）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_requests_per_kickoff: int = 60
    max_cost_per_kickoff: float = 1.0


class SettingsConfig(BaseSettings):
    """`packages/features/settings/config.json` の挙動設定（CFG-36〜40）。

    ソースを `(init_settings, JsonConfigSettingsSource)` **だけ**に絞るため、環境変数で
    上書きできない（`x3_notes.md` §2「`*Config` は env ソースを持たない」＝ FR-061）。キーは
    スコープ接頭辞を剥がした平坦形。

    **入れ子にも `extra="forbid"` を付ける**（CHK062）。付けないと pydantic の既定は `ignore`
    であり、`llm_presets[].api_key` のような入れ子の秘密キーが**黙って捨てられて構築済み
    モデルからは見えない**。ただし (c) 段で落ちることは V-06 の到達不能を解消しないため、
    `config.json` の秘密語の最終的な担保は `tests/unit/test_settings_defaults.py` が
    **生テキストを走査**して行う（SD-32）。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    key_forbidden_chars: list[str] = Field(default_factory=lambda: [".", "/", "\\", ":", " "])
    """CFG-36。**前半では使わない**（キー規則の適用点は F11・F12＝後半）。持つだけ（FR-060）。"""

    context_reserve_tokens: int = 4096
    """CFG-37。`threshold_t = context_window − 本値`（P5D-013）。生成分を見込む。"""

    llm_presets: list[LlmPreset] = Field(default_factory=list[LlmPreset])
    """CFG-38。UI の新規プロファイル入力の雛形。**領域 A には書かない**（R-17）。"""

    limits_defaults: LimitsDefaults = Field(default_factory=LimitsDefaults)
    """CFG-39 の ③ 段。`limits.yaml` 不在時の既定（settings-F04 が明示する唯一の既定）。"""

    system_actor: str
    """CFG-40。**必須**（既定を持たない）。`.` を含まないので `member_id` と衝突しない。"""

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
        """`⑧ build() の init 引数`による差し替えを受ける（FR-063）。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class SettingsDeps:
    """settings が他モジュールから受け取る依存（SD-23 の予定どおり **2 件**）。

    `BoardReader`（IF-09）は **F11-c（プロジェクト解除）の削除ガード**でだけ、`VaultReader`
    （IF-32）は **F11-a・F11-b（登録・編集）のフォルダ実在確認**でだけ呼ぶ。**`BoardWriter` を
    要求しない**ので、盤面へ書く手段が構造的に無い（P5・FR-008・FR-045・D07-g）。
    """

    board_reader: BoardReader
    vault_reader: VaultReader


@dataclass(frozen=True, slots=True)
class SettingsHandle:
    """`build()` の戻り。4 契約は**同一インスタンス**（FR-005）。

    `router` は**既定値を持たない**（FR-081「組み立ての戻りは必ず口を持つ」）。
    """

    settings_reader: SettingsReader
    member_resolver: MemberResolver
    authorizer: Authorizer
    command_registry: CommandRegistry
    router: APIRouter


def build(deps: SettingsDeps, *, config: SettingsConfig | None = None) -> SettingsHandle:
    """Settings を組み立てる（起動時の 6 段を順に行う。FR-062・R-08）。

    段は (a) `SETTINGS_ROOT` が読めない → (b) 実在しない／ディレクトリでない →
    (c) `config.json` が読めない → (d1) 雛形生成 → (d2) 領域 A の読取 → (d3) 整合検査 3 種。
    **最初に失敗した段だけ**を報告する（原因が 1 つに定まる方が追いやすい）。

    **`SETTINGS_ROOT` を `realpath` して保持しない**。(b) は**その時点の**実在確認である。

    Args:
        deps: settings が受け取る依存（`BoardReader`＝ IF-09・`VaultReader`＝ IF-32）。
        config: 挙動設定の差し替え（省略時は `config.json` を読む。FR-063）。

    Returns:
        組み立て済みの `SettingsHandle`（4 契約は同一インスタンス。`router` は 39 操作を持つ）。

    Raises:
        SettingsEnvError: `SETTINGS_ROOT` が設定されていない（(a)）。
        SettingsRootError: `SETTINGS_ROOT` が実在しない／ディレクトリでない（(b)）。
        SettingsConfigError: `config.json` の必須値が欠けている／未知キーがある（(c)）。
        SettingsAreaError: 雛形生成に失敗した／領域 A に構文誤り・必須ファイル不在がある
            （(d1)(d2)。**一覧**を持つ）。
        SettingsValidationError: 整合検査 3 種の違反（(d3)。`problems` の**一覧**を持つ。
            **最初の 1 件で打ち切らない**）。
    """
    env = _load_env()  # (a)
    _check_root(env.SETTINGS_ROOT)  # (b)
    # **`realpath` した結果を保持しない**（vault の R-05 と同型）。(b) はその時点の実在確認で
    # あり、以後の読み書きは宣言された値（`~` だけ展開したもの）に対して行う。(b) が絶対パスを
    # 強制済みなので、この `root` は必ず絶対パス＝以後の失敗メッセージも絶対になる（FR-062）。
    root = env.SETTINGS_ROOT.expanduser()
    settings = config if config is not None else _load_config()  # (c)

    reader = AreaAReader(root)
    _scaffold(root)  # (d1)
    data = read_area(reader)  # (d2)
    _validate(data, settings)  # (d3)

    snapshot = build_snapshot(
        data,
        reader,
        context_reserve_tokens=settings.context_reserve_tokens,
        default_requests=settings.limits_defaults.max_requests_per_kickoff,
        default_cost=settings.limits_defaults.max_cost_per_kickoff,
        system_actor=settings.system_actor,
    )
    service = SettingsService(snapshot)
    settings_reader: SettingsReader = service  # 憲章 §3: Protocol 型を必ず注釈する
    member_resolver: MemberResolver = service  # 4 つとも同一インスタンス（FR-005）
    authorizer: Authorizer = service
    command_registry: CommandRegistry = service
    # CRUD は同じ `SettingsService` を差し替えの相手として持つ（C-2）。読取側と口が別々の
    # スナップショットを見ることは無いので、保存の差し替えは即時に効く（SC-003）。
    crud = SettingsCrudService(
        service=service,
        snapshot=snapshot,
        data=data,
        reader=reader,
        writer=AreaAWriter(root),
        board_reader=deps.board_reader,
        vault_reader=deps.vault_reader,
        config=settings,
    )
    return SettingsHandle(
        settings_reader=settings_reader,
        member_resolver=member_resolver,
        authorizer=authorizer,
        command_registry=command_registry,
        router=create_settings_router(crud),
    )


def _load_env() -> SettingsEnv:
    """(a) `.env` と OS 環境変数から `SETTINGS_ROOT` を読む。

    `SettingsEnv(_env_file=env_file_path())` の形で**毎回**渡す。`SettingsEnv` の明示
    `__init__` と対で初めて、テストの autouse 止め木が効く（FR-019・SC-015）。
    **`SettingsEnv()` の形に「整理」しない。**

    Returns:
        読み込み済みの環境依存値。

    Raises:
        SettingsEnvError: `SETTINGS_ROOT` が無い／`.env` が読めない（必要なキー名と読んだ
            `.env` の絶対パス）。`.env` が UTF-8 で読めない・開けない場合も同じ段に畳む
            （生の `UnicodeDecodeError`・`OSError` を build の外へ漏らさない）。
    """
    try:
        return SettingsEnv(_env_file=env_file_path())
    except (ValidationError, OSError, ValueError) as exc:
        msg = (
            f"settings の起動に必要な環境依存値が読めない: {type(exc).__name__}: {exc}"
            " / 必要なキー: SETTINGS_ROOT（設定ファイル領域 A のルートの絶対パス）"
            f" / 読んだ .env: {env_file_path()}"
        )
        raise SettingsEnvError(msg) from exc


def _check_root(raw: Path) -> None:
    """(b) `SETTINGS_ROOT` が絶対パスで、実在し、ディレクトリであることを確かめる。

    **絶対パスの検査を最初に置く**（CFG-05）。`SETTINGS_ROOT=`（空値）を pydantic は
    `Path(".")` として受理し、相対値もそのまま通るため、この検査が無いと `Path(".")` が
    cwd に解決されて `exists()`・`is_dir()` の両方が真になり、(d1) が**カレント
    ディレクトリに雛形 6 ファイルを書き込む**（FR-019・SC-015 が禁じている書込）。
    以後の失敗メッセージが絶対パスになること（FR-062）も、この検査が担保する。

    `is_dir()` は「不在」にも「ファイル」にも偽を返すので、`exists()` で先に分けて
    **2 つを別のメッセージ**にする（SC-009 b）。**settings は領域 A のルートを作らない。**

    **解決結果を返さない**（`realpath` して保持しない＝ vault の R-05 と同型）。本検査は
    **その時点の**実在確認であり、実行時の保証ではない。

    Raises:
        SettingsRootError: 絶対パスでない／実在しない／ディレクトリでない（**見に行った
            絶対パス**を載せる。絶対パスでない場合は宣言された値そのもの）。
    """
    if not raw.expanduser().is_absolute():
        msg = (
            f"SETTINGS_ROOT が絶対パスでない: 「{raw}」"
            " / 設定ファイル領域 A のルートは絶対パスで指定する（CFG-05）"
        )
        raise SettingsRootError(msg)
    root = raw.expanduser().resolve()
    if not root.exists():
        msg = f"SETTINGS_ROOT が実在しない: {root}"
        raise SettingsRootError(msg)
    if not root.is_dir():
        msg = f"SETTINGS_ROOT がディレクトリでない: {root}"
        raise SettingsRootError(msg)


def _load_config() -> SettingsConfig:
    """(c) `config.json` を読む。

    Returns:
        読み込み済みの挙動設定。

    Raises:
        SettingsConfigError: 必須値が欠けている／未知キーがある／**JSON として読めない**
            （**見に行った絶対パス**）。`JsonConfigSettingsSource` は `json.loads` を呼ぶので、
            構文誤り・空ファイルは `JSONDecodeError`、トップレベルが配列なら `TypeError` に
            なる。いずれも `ValidationError` ではないため、同じ段に畳んで翻訳する（FR-062）。
    """
    path = config_path()
    try:
        return SettingsConfig()
    except (ValidationError, OSError, ValueError, TypeError) as exc:
        msg = f"settings の設定を読めない（{path}）: {type(exc).__name__}: {exc}"
        raise SettingsConfigError(msg) from exc


def _scaffold(root: Path) -> None:
    """(d1) 不在の共通側ファイルだけを生成する（**既存を上書きしない**＝ FR-016）。

    Raises:
        SettingsAreaError: 生成に失敗した（**見に行った絶対パス**を載せる）。
    """
    try:
        AreaAScaffolder(root, defaults_path()).ensure()
    except OSError as exc:
        msg = f"領域 A の雛形を生成できない（{root}）: {exc}"
        raise SettingsAreaError(msg, [msg]) from exc


def _validate(data: AreaAData, settings: SettingsConfig) -> None:
    """(d3) 整合検査 3 種すべてを走らせる（FR-047・clarify 裁定）。

    一部を保存時（後半）だけに送る形は採らない。**死に定義も同じ重さで中断させる**。

    Raises:
        SettingsValidationError: 違反が 1 件以上あった（**一覧**を持つ。最初の 1 件で
            打ち切らない＝ US3 AC-13）。
    """
    problems: list[ValidationProblem] = validate_forward(
        data,
        context_reserve_tokens=settings.context_reserve_tokens,
        system_actor=settings.system_actor,
    )
    problems.extend(validate_reverse(data))
    problems.extend(validate_commands(data))
    if problems:
        lines = "\n".join(f"- [{item.kind}] {item.where}: {item.reason}" for item in problems)
        msg = f"領域 A の整合検査に違反がある（{len(problems)} 件）:\n{lines}"
        raise SettingsValidationError(msg, problems)
