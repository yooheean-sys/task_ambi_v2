"""executor の内部モデル（`ExecutorDeps`・`ExecutorConfig`・`RunSession`・`LlmFactory` ほか）。

**置き場の裁定（C1）**: `ExecutorDeps`・`ExecutorConfig` の定義は本ファイルに置き、
`executor/__init__.py` は再エクスポートするだけにする。`tools/base.py` の `ToolContext` が
この 2 つをフィールドに持つため、`__init__.py` に定義すると `tools/base.py → executor/__init__.py`
の逆流になり、`__init__ → service → tools` と合わせて**実行時に循環する**。

**import の向き**: `__init__` → `service` → `tools/` → `models`。本ファイルは `tools/` を
import しない（実行時も `TYPE_CHECKING` 限定も 0 件）。
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 実行時 import はここまで（R-06 の 2 分規則）。`CrewEvent`・`WrittenPath` は `default_factory` の
# `list[...]` が実行時に評価されるため、`CrewOutcome` は `to_outcome()` が実体を作るため。
import httpx
from crewai import LLM
from crewai.llms.hooks.base import BaseInterceptor
from dotenv import dotenv_values
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from common.interfaces.executor import CrewEvent, CrewOutcome, ExecutorFailure
from common.interfaces.settings import ProviderKind
from common.interfaces.vault import WrittenPath
from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter
    from common.interfaces.executor import Escalation, ReviewVerdict
    from common.interfaces.monitor import FailureKind, LLMHttpHook, RunHalted
    from common.interfaces.repo import RepoReader, RepoWriter
    from common.interfaces.settings import (
        Authorizer,
        CommandRegistry,
        LLMProfile,
        Member,
        ProjectFolders,
        ProjectRepos,
    )
    from common.interfaces.stages import Proposal
    from common.interfaces.vault import OutcomeWriter, VaultReader

CONFIG_FILENAME = "config.json"
"""executor 固有の非機密設定。位置は規約の固定相対パス（憲章 §5）。"""

ENV_FILENAME = ".env"
"""秘密と環境依存値の宣言ファイル（ルート・gitignore）。位置は規約の固定相対パス。"""

REASON_HEAD_CHARS = 200
"""`ExecutorFailure.reason` に載せる先頭文字数（**crewai の型そのものは載せない**）。"""


def config_path() -> Path:
    """`packages/features/executor/config.json` の絶対パス（cwd 非依存）。

    **`__init__.py` ではなく本ファイルに置く**（C1）。`ExecutorConfig` の唯一の利用元が
    本ファイルであり、`__init__.py` に置くと `models.py → executor/__init__.py` の逆流に
    なって `import executor` が循環で落ちる。`__init__.py` は再エクスポートする。
    """
    return Path(__file__).resolve().parents[2] / CONFIG_FILENAME


class ExecutorConfig(BaseSettings):
    """`packages/features/executor/config.json` の挙動設定（CFG-51〜56）。

    **6 キーはすべて必須**（欠落は起動中断）。フィールド既定を置くと「必須値が欠けている」で
    `ValidationError` が起きず、`event_allowlist=[]` のまま黙って起動して**翻訳が 1 件も
    起きない無症状の劣化**になる（monitor の同型の罠）。
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True, strict=True)

    # **`strict=True` を付ける**。既定の緩い変換では `"30"` が黙って `30` に直り、契約
    # （contracts/executor-config.md §2 の起動中断 ③「型が合わない」）が発火しない。
    # JSON は型をそのまま持つので、正しい設定ファイルが読めなくなることはない。

    run_command_timeout_sec: int
    """CFG-51。`run_command` の打ち切り秒数（凍結 PoC 継承＝ P-11）。"""

    max_iter: int
    """CFG-52。反復上限の既定。**`Member.max_iter` が優先**する（D16-h）。"""

    max_rpm: int | None
    """CFG-53。毎分要求数の既定。**`null` は「無制限を宣言した」**でキーの不在とは別物。"""

    event_allowlist: list[str]
    """CFG-54。翻訳対象の crewai 実クラス名（**完全一致**。ワイルドカードを解釈しない）。"""

    hook_catalog: list[str]
    """CFG-55。登録を許す Hook 名の台帳（M1 は空でよい）。"""

    disable_crewai_telemetry: bool
    """CFG-56。真なら telemetry の 2 つの印を `setdefault` する（**上書きしない**）。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """ソースを `(init_settings, JsonConfigSettingsSource)` だけに絞る（FR-072）。

        `env_settings`・`dotenv_settings`・`file_secret_settings` を渡さないので、環境変数でも
        `.env` でも上書きできない（US6 AC-8）。

        `config_path()` を**クラス定義時ではなく呼び出し時**に解決する。モジュールロード時に
        評価する形へ「整理」すると、テストの差し替えが**黙って無効**になり実ファイルを読む
        （stages の罠）。
        """
        return (init_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path()))

    def __init__(self, **overrides: Any) -> None:
        """`build()` の init 引数による差し替えを受ける。"""
        super().__init__(**overrides)


@dataclass(frozen=True, slots=True)
class ExecutorDeps:
    """executor が他モジュールから受け取る依存（9 契約）。

    道具が使うのは 8 つで、`llm_http_hook` だけが差し込みアダプタ経由で使われる。
    `repo_reader`・`repo_writer` は D25 の repo 系 4 道具が使う。
    """

    board_reader: BoardReader
    board_writer: BoardWriter
    vault_reader: VaultReader
    outcome_writer: OutcomeWriter
    authorizer: Authorizer
    command_registry: CommandRegistry
    llm_http_hook: LLMHttpHook
    repo_reader: RepoReader
    repo_writer: RepoWriter


@dataclass(slots=True)
class RunSession:
    """1 回の `run` の可変状態（data-model §4）。

    **不変条件**:

    1. **1 回の `run` に 1 個**。`CrewExecutorImpl` は本型を**フィールドに持たない**
       （FR-015・FR-016）。
    2. **積む操作はすべて `_lock` の中**。イベントハンドラが別スレッドで走る（SD-19）ため、
       外から直接フィールドを書かず、本型のメソッド経由で積む。
    3. **申告は最後の呼び出しが勝つ**（FR-053a）。追記ではなく代入である。
    4. **`progress_notes` は成功回数**（FR-044a）。拒否・失敗は数えない。
    5. **`to_outcome()` は 11 欄すべてを埋める**（FR-017）。埋まらない欄は `None`／`0`／`""`。
    """

    issue_id: str
    member: Member
    folders: ProjectFolders
    """**原本**。役割の絞りは道具の側で都度 `narrow()` する（絞った結果を持ち回らない）。"""

    repos: ProjectRepos | None = None
    """リポジトリ対応付け（`CrewSpec.repos` の写し。D25）。未登録なら `None`。"""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    proposal: Proposal | None = None
    completed: bool = False
    completion_reason: str = ""
    escalation: Escalation | None = None
    review: ReviewVerdict | None = None
    written: list[WrittenPath] = field(default_factory=list[WrittenPath])
    events: list[CrewEvent] = field(default_factory=list[CrewEvent])
    halted: bool = False
    halted_reason: FailureKind | None = None
    progress_notes: int = 0
    unknown_events: Counter[str] = field(default_factory=Counter[str])
    """翻訳対象の一覧に無かった出来事の名前と件数。**器には写さない**（FR-060）。"""

    # -- 積む（すべてロックの中） ------------------------------------------------------

    def add_event(self, event: CrewEvent) -> None:
        """中立型の出来事を 1 件積む（**重複排除をしない**＝ FR-062）。"""
        with self._lock:
            self.events.append(event)

    def note_unknown_event(self, class_name: str) -> None:
        """翻訳対象に無い出来事の名前と件数を控える（器には載せない）。"""
        with self._lock:
            self.unknown_events[class_name] += 1

    def add_written(self, path: WrittenPath) -> None:
        """書込結果を 1 件積む。"""
        with self._lock:
            self.written.append(path)

    def count_progress_note(self) -> None:
        """進捗コメントの**成功**を 1 件数える（FR-044a）。"""
        with self._lock:
            self.progress_notes += 1

    # -- 申告（いずれも代入。最後が勝つ） ------------------------------------------------

    def set_proposal(self, proposal: Proposal) -> None:
        """分解提案を持ち帰る（盤面には 1 行も書かない＝ FR-049）。"""
        with self._lock:
            self.proposal = proposal

    def set_completion(self, reason: str) -> None:
        """完了の申告を持ち帰る（**完了させない**＝ FR-051）。"""
        with self._lock:
            self.completed = True
            self.completion_reason = reason

    def set_escalation(self, escalation: Escalation) -> None:
        """人への相談を持ち帰る（**起票しない**＝ FR-052）。"""
        with self._lock:
            self.escalation = escalation

    def set_review(self, review: ReviewVerdict) -> None:
        """レビュー判定を持ち帰る（FR-053）。"""
        with self._lock:
            self.review = review

    # -- 停止と写像 ---------------------------------------------------------------------

    def halt(self, exc: RunHalted) -> None:
        """上限停止を器へ写す。

        `exc` は 4 属性を持つが、`CrewOutcome` に欄があるのは**種類だけ**である。
        残る `measured`・`limit`・`request_no` は構造化ログの `extra` に残す（SD-16）。
        """
        with self._lock:
            self.halted = True
            self.halted_reason = exc.kind

    def to_outcome(self, task_output: str | None) -> CrewOutcome:
        """`CrewOutcome` の **11 欄すべて**を埋めて返す（FR-017）。"""
        with self._lock:
            return CrewOutcome(
                proposal=self.proposal,
                task_output=task_output,
                completed=self.completed,
                completion_reason=self.completion_reason,
                review=self.review,
                escalation=self.escalation,
                written=list(self.written),
                events=list(self.events),
                halted=self.halted,
                halted_reason=self.halted_reason,
                progress_notes=self.progress_notes,
            )


# --------------------------------------------------------------------------------------
# 秘密の読取（executor-F02 の一部）— **executor だけが `.env` の実値を読む**
# --------------------------------------------------------------------------------------


def env_file_path() -> Path:
    """ルート `.env` の絶対パス（cwd 非依存）。

    **テストの差し替え点はここ**（`executor.models.env_file_path`）である。
    `executor/__init__.py` の再エクスポートを差し替えても効かない —— `read_env_values()` は
    自分の module 大域を引くので、再エクスポート先の名前を替えても参照先が変わらない。
    """
    return find_repo_root() / ENV_FILENAME


def read_env_values() -> dict[str, str | None]:
    """`.env` を**対応表として直読み**する（FR-022a・SD-10）。

    `BaseSettings` 経由にしない —— 追加フィールドを許す設定クラスは**空の値をキーごと落とす**ため、
    「参照名が不在（→ `ExecutorFailure`）」と「空で存在（→ 空のまま続行）」を区別できない（F-32）。

    **OS 環境変数を見ない**（FR-022・憲章 §5。D15-b の ⑦ はこのキーに適用されない＝ SD-11）。
    """
    return dict(dotenv_values(env_file_path(), encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class InterceptorAdapter(BaseInterceptor[httpx.Request, httpx.Response]):
    """注入された `LLMHttpHook` へ委譲するだけの差し込み（executor-F02。FR-020・FR-021）。

    **executor で唯一 crewai の差し込み基底を継承する場所**。**判定・計測・状態を持たない**——
    上限の判定は monitor の責務であり、`RunHalted`（`BaseException` 直系）は握らず素通りさせる。

    引数名は基底に合わせて `message` にする（基底の 4 メソッドはすべてこの名前）。
    """

    hook: LLMHttpHook

    def on_outbound(self, message: httpx.Request) -> httpx.Request:
        """送信前を委譲する。

        Raises:
            RunHalted: 上限を超過した（**握らない**）。
        """
        return self.hook.on_outbound(message)

    def on_inbound(self, message: httpx.Response) -> httpx.Response:
        """応答を委譲する。"""
        return self.hook.on_inbound(message)

    async def aon_outbound(self, message: httpx.Request) -> httpx.Request:
        """送信前（非同期）を委譲する。

        Raises:
            RunHalted: 上限を超過した（**握らない**）。
        """
        return await self.hook.aon_outbound(message)

    async def aon_inbound(self, message: httpx.Response) -> httpx.Response:
        """応答（非同期）を委譲する。"""
        return await self.hook.aon_inbound(message)


@dataclass(frozen=True, slots=True)
class LlmFactory:
    """`LLMProfile` から `LLM` を組む（executor-F02。**鍵をフィールドに保持しない**）。

    読む時点は**実行ごと**（`build()` では読まない＝ FR-018・FR-008 (b)）。
    """

    def resolve_api_key(self, profile: LLMProfile) -> str:
        """`.env` から鍵の実値を引く（**その場で読んで捨てる**）。

        | `api_key_ref` | `.env` の状態 | 振る舞い |
        |---|---|---|
        | `""` | — | `.env` を**引かず** `""` |
        | 参照名あり | キーが**無い** | `ExecutorFailure(phase="build")` |
        | 参照名あり | キーがあり値が**空** | `""` のまま続行 |
        | 参照名あり | キーがあり値がある | その実値 |

        Returns:
            鍵の実値（無指定・空指定のときは空文字）。

        Raises:
            ExecutorFailure: 参照名があるのに `.env` にキーが無い（FR-023・SC-023）。
        """
        ref = profile.api_key_ref
        if not ref:
            return ""
        values = read_env_values()
        if ref not in values:
            msg = f"LLM の鍵の参照名 '{ref}' が {env_file_path()} に無い"
            raise ExecutorFailure(msg, phase="build", reason=f"KeyError: {ref}")
        return values[ref] or ""

    def build(self, profile: LLMProfile, adapter: InterceptorAdapter) -> LLM:
        """`provider_kind` で 2 分岐して `LLM` を組む（**構築だけ**。通信しない＝ F-30）。

        Returns:
            組み立て済みの `LLM`。

        Raises:
            ExecutorFailure: 鍵が解決できない／`LLM` の構築が失敗した（`phase="build"`）。
        """
        api_key = self.resolve_api_key(profile)
        if profile.provider_kind is ProviderKind.openrouter:
            model = f"openrouter/{profile.model_id}"
            base_url = None  # 接続先は既定が自動で入る（SD-02）
        else:
            model = f"openai/{profile.model_id}"
            base_url = profile.base_url
        # `extra_headers` は `LLM.model_fields` に**無い**が受理され `additional_params` に入る
        # （実測 2026-08-25）。宣言フィールドではないので辞書にまとめて渡す。
        params: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "interceptor": adapter,
            "extra_headers": dict(profile.extra_headers),
        }
        try:
            return LLM(**params)
        except Exception as exc:
            msg = (
                f"LLM を構築できない（プロファイル '{profile.name}'・"
                f"鍵の参照名 '{profile.api_key_ref}'）"
            )
            raise ExecutorFailure(
                msg,
                phase="build",
                reason=f"{type(exc).__name__}: {exc}"[:REASON_HEAD_CHARS],
                cause=exc,
            ) from exc
