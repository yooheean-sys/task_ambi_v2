"""executor モジュールの公開口（`ExecutorDeps`・`ExecutorHandle`・`ExecutorConfig`・`build`）。

executor-F12。

executor は**外部ストアを持たず**、盤面・保管庫・設定へは注入された Protocol 経由でのみ触れる。
`.env` の実値を読む唯一の層だが、読むのは `LlmFactory`（`models.py`）だけで、読む時点は
**実行ごと**（`build()` では読まない）。HTTP 経路も UI ページも持たないため `router.py`・`ui.py`・
`schemas.py`・`dependencies.py` を置かない（FR-002・FR-011）。

`exceptions.py` を置かない理由: 契約例外（`ExecutorFailure`・`RunHalted`）は
`common/interfaces/` が正本であり executor では再定義しない（FR-002a）。内部例外は起動時の
`ExecutorStartupError` 1 つだけなので、公開口である本ファイルに同居させる（monitor と同型）。

**再エクスポート**（C1）: `ExecutorDeps`・`ExecutorConfig`・`config_path`・`env_file_path` の
定義は `models.py` にある。本ファイルからの再エクスポートは**読み口**であり、テストの
差し替え点にはならない（差し替えは `executor.models` 側を `monkeypatch` する）。
"""

from __future__ import annotations

import os

_ENVIRON_BEFORE_CREWAI = dict(os.environ)
"""crewai を import する**前**の環境の写し（本ファイルの他の import より前に評価される）。

`crewai.llm` は module 直下で `dotenv.load_dotenv()` を無条件に呼び、リポジトリの `.env` を
まるごと `os.environ` へ流し込む（実測 1.15.17。逃げ道の環境変数は無い）。これは 3 つの点で
executor の約束を破る:

1. **秘密の実値（`LLM_API_KEY_*`）がプロセス全体へ公開される**（D15-i・FR-024）。
2. 憲章 §5・FR-022 の「OS 環境変数に依存させない」が、executor を import しただけで
   崩れる（他モジュールの `BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT` が env に載る）。
3. 先行モジュールの試験の隔離が壊れる（`BEADS_DIR` が載ると board の
   `bd init` が一時ワークスペースではなく実領域を見て失敗する＝実測 33 件）。

crewai を独占する層の責務として、**import で足された分だけ**を本ファイルの末尾で取り除く
（`_restore_environment_after_crewai_import`）。**元からあった値には触れない**。
"""

# 以降の import は上の写しより**後**でなければ意味が無いため、E402 の抑制を各行に付ける。
from dataclasses import dataclass  # noqa: E402

import crewai.events as crewai_events  # noqa: E402
from crewai.events.base_events import BaseEvent  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from common.interfaces.executor import CrewExecutor  # noqa: E402
from common.kernel.enums import ToolName  # noqa: E402
from executor import models as executor_models  # noqa: E402
from executor.models import (  # noqa: E402
    ExecutorConfig,
    ExecutorDeps,
    LlmFactory,
    config_path,
    env_file_path,
)
from executor.service import EVENT_KIND_BY_CLASS, CrewExecutorImpl  # noqa: E402
from executor.tools import TOOL_BUILDERS, ToolRegistry  # noqa: E402

__all__ = [
    "ExecutorConfig",
    "ExecutorDeps",
    "ExecutorHandle",
    "ExecutorStartupError",
    "build",
    "config_path",
    "env_file_path",
]

TELEMETRY_ENV_NAMES = ("CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED")
"""採用版が読む telemetry の 2 つの印（CFG-56・D15-m・F-34）。"""


class ExecutorStartupError(RuntimeError):
    """起動時検査の失敗（FR-071）。**契約例外も `AppError` も `ExecutorFailure` も継承しない**。

    契約例外の系統は**モジュール間契約の失敗**を表す語彙であり、設定不備は契約の失敗ではない。
    層契約により executor は apps/api の起動時例外階層を参照できない（先行 5 モジュールと同一）。
    """


@dataclass(frozen=True, slots=True)
class ExecutorHandle:
    """`build()` の戻り。提供契約は `CrewExecutor` の 1 つだけ（IF-48）。"""

    crew_executor: CrewExecutor
    router: None = None


def build(deps: ExecutorDeps, *, config: ExecutorConfig | None = None) -> ExecutorHandle:
    """Executor を組み立てる（executor-F12）。

    **3 段の起動時検査**を順に通し、**最初に失敗した段だけ**を報告する
    （contracts/executor-config.md §2）。`.env` は**ここでは読まない**（読む時点は実行ごと
    ＝ FR-018・FR-008 (b)）。

    Args:
        deps: executor が受け取る 9 契約。
        config: 挙動設定の差し替え（省略時は `config.json` を読む）。

    Returns:
        組み立て済みの `ExecutorHandle`（`router` は `None`）。

    Raises:
        ExecutorStartupError: 設定が壊れている（FR-059・FR-069・FR-031）。
    """
    settings = config if config is not None else _load_config()  # (a)
    _check_event_allowlist(settings)  # (b)
    _check_tool_builders()  # (c)
    _apply_telemetry_defaults(settings)
    service = CrewExecutorImpl(
        deps=deps,
        config=settings,
        registry=ToolRegistry(builders=TOOL_BUILDERS),
        llm_factory=LlmFactory(),
    )
    crew_executor: CrewExecutor = service  # 憲章 §3: Protocol 型を必ず注釈する
    return ExecutorHandle(crew_executor=crew_executor, router=None)


def _load_config() -> ExecutorConfig:
    """(a) `config.json` を読む。

    Returns:
        読み込み済みの挙動設定。

    Raises:
        ExecutorStartupError: 未知キー・必須値の欠け・型の不一致（**絶対パス**を載せる）。
    """
    path = executor_models.config_path()  # 差し替え点は models 側（再エクスポートを見ない）
    try:
        return ExecutorConfig()
    except ValidationError as exc:
        msg = f"executor の設定を読めない（{path}）: {exc}"
        raise ExecutorStartupError(msg) from exc


def _check_event_allowlist(settings: ExecutorConfig) -> None:
    """(b) 翻訳対象の各名前が**実在し・写像表に対応がある**ことを確かめる（FR-059）。

    **`dir(crewai.events)` を使わない** —— モジュール `__getattr__` による遅延読込で、
    読み込み前の `dir()` には Event 名が 1 件も出ない（実測＝ SD-18）。使うと全件
    「対応が無い」になって必ず起動中断する。存在しない名前の `getattr` は `AttributeError`
    になるので、これを捕まえて本段の失敗に翻訳する。

    Raises:
        ExecutorStartupError: 解決できない名前／対応の無い名前がある（**列挙**して載せる）。
    """
    path = executor_models.config_path()  # 差し替え点は models 側（再エクスポートを見ない）
    unresolved: list[str] = []
    unmapped: list[str] = []
    for name in settings.event_allowlist:
        try:
            candidate = getattr(crewai_events, name)
        except AttributeError:
            unresolved.append(name)
            continue
        if not (isinstance(candidate, type) and issubclass(candidate, BaseEvent)):
            unresolved.append(name)
        elif name not in EVENT_KIND_BY_CLASS:
            unmapped.append(name)
    if unresolved or unmapped:
        msg = (
            f"executor の設定の event_allowlist が現在の crewai と噛み合わない（{path}）:"
            f" 実在しない／出来事でない: {unresolved} /"
            f" 対応する種類がコード側に無い: {unmapped}"
        )
        raise ExecutorStartupError(msg)


def _check_tool_builders() -> None:
    """(c) 構築子表の鍵が `ToolName` の 16 種と一致することを確かめる（FR-031・SC-011）。

    Raises:
        ExecutorStartupError: 欠け・余分がある（**名前を列挙**して載せる）。
    """
    expected = {name.value for name in ToolName}
    actual = {name.value for name in TOOL_BUILDERS}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        msg = f"道具の構築子表が ToolName と一致しない: 欠け: {missing} / 余分: {extra}"
        raise ExecutorStartupError(msg)


def _apply_telemetry_defaults(settings: ExecutorConfig) -> None:
    """Telemetry の印を **`setdefault`** で置く（**上書きしない**＝ FR-070・SC-025）。

    executor が `os.environ` を**書き換える**のは、この 2 行と
    `_restore_environment_after_crewai_import` の削除（SD-26）の 2 箇所だけである。
    **読む**呼び出しは、その復元にある**走査除外の印を付けた 1 行**だけに限る
    （鍵は `.env` の直読み＝ FR-022・憲章 §5。印と走査は `SKILL.md` ③・⑦）。
    """
    if not settings.disable_crewai_telemetry:
        return
    for name in TELEMETRY_ENV_NAMES:
        os.environ.setdefault(name, "true")


def _restore_environment_after_crewai_import() -> list[str]:
    """`.env` から `os.environ` へ流し込まれた分を取り除く（上の写しの説明を参照）。

    取り除く対象は次の 2 つの和である。**import 順に依存させない**ため 2 本立てにする ——
    テストの `conftest` のように **executor より先に crewai を import する経路**があると、
    上の写しは既に汚れており 1 本目だけでは取りこぼす。

    1. **crewai の import 前に無かった名前**（写しとの差分）。
    2. **リポジトリ `.env` に定義があり、現在の値が `.env` の値と一致する名前**。
       値が一致する限り取り除いても振る舞いは変わらない —— 先行モジュールの設定は
       いずれも `env_file` 経由で**同じファイル**を読むためである（board の `BoardEnv`・
       vault の `VaultEnv`・settings の `SettingsEnv`）。

    **元からあって値が違う項目には触れない**（`load_dotenv` は既存値を上書きしないので、
    値が違うならそれは利用者が意図して置いたものである）。取り除いた名前は返すだけで、
    **値はログにも例外にも出さない**（D15-i・FR-024）。

    Returns:
        取り除いた環境変数の名前（値は含めない）。
    """
    added = {name for name in os.environ if name not in _ENVIRON_BEFORE_CREWAI}
    declared = executor_models.read_env_values()
    # 次の内包の 1 行だけが `os.environ` を**読む**。これは**鍵の解決ではなく汚染の除去**であり、
    # FR-022（憲章 §5）が禁じる「鍵の値を OS 環境変数から採ること」には当たらない —— 読んだ値は
    # `.env` の値との一致判定に使うだけで、どこへも渡さず記録にも出さない（SD-26）。
    # 走査を綴りで避けない（`name in os.environ` へ書き換えない）。番人が意味を失うためである。
    # 代わりに行末の印で明示的に除外し、**印の付いた行は 1 つだけ**に保つ。走査の形は `SKILL.md` ⑦。
    same_as_env_file = {
        name
        for name, value in declared.items()
        if value is not None and os.environ.get(name) == value  # allow-os-environ-read (SD-26)
    }
    removed = sorted(added | same_as_env_file)
    for name in removed:
        del os.environ[name]
    return removed


_RESTORED_ENVIRON_NAMES = _restore_environment_after_crewai_import()
"""crewai の import が足して、executor が取り除いた環境変数の名前（値は持たない）。"""
