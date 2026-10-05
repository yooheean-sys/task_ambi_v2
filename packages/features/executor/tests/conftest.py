"""executor のテスト共通部品（9 契約スタブ・AI 偽装の 4 層・組立ヘルパ）。

**実 LLM・実 `.env` の実値・実 `bd`・実 vault・ネットワーク・`apps/api/tests/_support` を使わない**
（FR-081・SC-032）。差し込みアダプタは台本 AI 経由では 1 度も呼ばれない（F-31・FR-081b）ので、
`httpx.Request`／`httpx.Response` を素で組んで直接呼ぶ層を別に持つ。

`tests/integration/` を持たないため `conftest` は 1 つだけで、`from conftest import ...` の
解決先は一意である。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import httpx
import pytest
from crewai.llms.base_llm import BaseLLM
from pydantic import Field

from common.interfaces.board import (
    BoardReader,
    BoardWriter,
    ClaimFilter,
    Comment,
    Issue,
    IssueSpec,
    IssueStatus,
)
from common.interfaces.executor import CrewSpec
from common.interfaces.monitor import FailureKind, LLMHttpHook, RunHalted
from common.interfaces.repo import RepoNotFound, RepoReader, RepoWriter
from common.interfaces.settings import (
    Authorizer,
    CommandRegistry,
    HookRef,
    HooksConfig,
    LLMProfile,
    McpSpec,
    Member,
    ProjectFolders,
    ProjectRepos,
    ProviderKind,
    UnknownCommand,
)
from common.interfaces.stages import PromptBundle, PromptPurpose
from common.interfaces.vault import (
    FileRef,
    FolderCheck,
    OutcomeWriter,
    VaultArea,
    VaultReader,
    WrittenPath,
)
from common.kernel.enums import PersonaKey, ToolName
from executor import ExecutorConfig, ExecutorDeps

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

FIXED_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
"""時刻を要する場所で使う固定値（テストが時計に依存しないようにする）。"""

DEFAULT_EVENT_ALLOWLIST: tuple[str, ...] = (
    "LLMCallStartedEvent",
    "LLMCallCompletedEvent",
    "LLMCallFailedEvent",
    "ToolUsageStartedEvent",
    "ToolUsageFinishedEvent",
    "ToolUsageErrorEvent",
    "LLMGuardrailStartedEvent",
    "LLMGuardrailCompletedEvent",
    "CrewKickoffCompletedEvent",
    "CrewKickoffFailedEvent",
)
"""出荷時既定の翻訳対象（**テスト側に直書きする**。実装の `config.json` を読まない）。"""


class _NotCalledError(AssertionError):
    """executor が呼ばないはずの契約メソッドが呼ばれた。"""

    def __init__(self, name: str) -> None:
        """呼ばれた手の名前を載せる。"""
        super().__init__(f"executor は {name} を呼ばない")


# --------------------------------------------------------------------------------------
# (0) 呼び出しの記録 — 7 契約すべてで共有し、**順序と引数の両方**を保つ（CHK018）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Call:
    """契約メソッド 1 回の呼び出し。"""

    target: str
    """`"BoardReader.show"` の形。"""

    kwargs: dict[str, Any]
    """渡された引数（すべてキーワードに正規化して積む）。"""


@dataclass(slots=True)
class CallLog:
    """9 契約に共通の呼び出し記録（**順序を保つ**）。"""

    calls: list[Call] = field(default_factory=list[Call])

    def record(self, target: str, **kwargs: Any) -> None:
        """1 回分を末尾に積む。"""
        self.calls.append(Call(target=target, kwargs=kwargs))

    def targets(self) -> list[str]:
        """呼ばれた順の対象名。"""
        return [call.target for call in self.calls]

    def of(self, target: str) -> list[Call]:
        """対象名で絞った呼び出し（順序を保つ）。"""
        return [call for call in self.calls if call.target == target]

    def count(self, target: str) -> int:
        """対象名の呼び出し回数。"""
        return len(self.of(target))


# --------------------------------------------------------------------------------------
# (a) 盤面（IF-02・IF-17）
# --------------------------------------------------------------------------------------


def make_issue(issue_id: str = "bd-1", *, title: str = "題", labels: Sequence[str] = ()) -> Issue:
    """`Issue` を組む（`read_issue` の戻りの材料）。"""
    return Issue(
        id=issue_id,
        title=title,
        description="本文",
        status=IssueStatus.open,
        labels=list(labels),
        parent=None,
        assignee=None,
        acceptance="受け入れ条件",
        dependencies=[],
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )


@dataclass(slots=True)
class StubBoardReader:
    """`BoardReader` のスタブ（executor が呼ぶのは `show` だけ）。"""

    log: CallLog
    issue: Issue = field(default_factory=make_issue)
    raises: BaseException | None = None
    """`show` が送出する例外（`IssueNotFound`／`BoardUnavailable` を差し込む）。"""

    def show(self, issue_id: str) -> Issue:
        """課題 1 件を返す。"""
        self.log.record("BoardReader.show", issue_id=issue_id)
        if self.raises is not None:
            raise self.raises
        return self.issue

    def children_of(self, parent_id: str) -> list[Issue]:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardReader.children_of")

    def open_question_of(self, issue_id: str) -> Issue | None:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardReader.open_question_of")

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardReader.list_working")

    def comments(self, issue_id: str) -> list[Comment]:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardReader.comments")

    def has_open_issues(self, project_key: str) -> bool:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardReader.has_open_issues")


@dataclass(slots=True)
class StubBoardWriter:
    """`BoardWriter` のスタブ（executor が呼ぶのは `comment` だけ＝ FR-043）。"""

    log: CallLog
    raises: BaseException | None = None
    """`comment` が送出する例外。"""

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardWriter.claim_next")

    def create(self, spec: IssueSpec, actor: str) -> str:
        """executor は呼ばない（起票しない＝ FR-052）。"""
        raise _NotCalledError("BoardWriter.create")

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardWriter.add_dependency")

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardWriter.move_dependents")

    def release(self, issue_id: str, actor: str) -> None:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardWriter.release")

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """進捗コメントを 1 件付ける。"""
        self.log.record("BoardWriter.comment", issue_id=issue_id, body=body, actor=actor)
        if self.raises is not None:
            raise self.raises

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """executor は呼ばない（完了させない＝ FR-051）。"""
        raise _NotCalledError("BoardWriter.close")

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """executor は呼ばない。"""
        raise _NotCalledError("BoardWriter.set_labels")


# --------------------------------------------------------------------------------------
# (b) 保管庫（IF-33〜35・IF-37）
# --------------------------------------------------------------------------------------


def make_file_ref(name: str, area: VaultArea = VaultArea.pj_input) -> FileRef:
    """`FileRef` を組む。"""
    return FileRef(
        area=area,
        rel_path=f"{area.value}/{name}",
        name=name,
        size_bytes=len(name),
        mtime=FIXED_NOW,
    )


@dataclass(slots=True)
class StubVaultReader:
    """`VaultReader` のスタブ（呼ぶのは `list_inputs`・`read_input`・`read_outcome_file`）。"""

    log: CallLog
    files: list[FileRef] = field(default_factory=list[FileRef])
    contents: dict[str, str] = field(default_factory=dict[str, str])
    list_raises: BaseException | None = None
    read_input_raises: BaseException | None = None
    read_outcome_raises: BaseException | None = None

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck:
        """executor は呼ばない。"""
        raise _NotCalledError("VaultReader.check_project_folders")

    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]:
        """3 領域をまとめて返す（領域を選ぶ引数を持たない＝ SD-12）。"""
        self.log.record("VaultReader.list_inputs", folders=folders, role=role)
        if self.list_raises is not None:
            raise self.list_raises
        return list(self.files)

    def read_input(self, folders: ProjectFolders, ref: FileRef) -> str:
        """入力ファイルを読む（**参照を要求する**＝ SD-13）。"""
        self.log.record("VaultReader.read_input", folders=folders, ref=ref)
        if self.read_input_raises is not None:
            raise self.read_input_raises
        return self.contents.get(ref.name, "")

    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str:
        """成果物を読む。"""
        self.log.record("VaultReader.read_outcome_file", folders=folders, filename=filename)
        if self.read_outcome_raises is not None:
            raise self.read_outcome_raises
        return self.contents.get(filename, "")

    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool:
        """executor は呼ばない（完了ゲートは呼び手の責務）。"""
        raise _NotCalledError("VaultReader.outcome_exists")


@dataclass(slots=True)
class StubOutcomeWriter:
    """`OutcomeWriter` のスタブ。"""

    log: CallLog
    overwritten: bool = False
    raises: BaseException | None = None

    def write_outcome(self, folders: ProjectFolders, filename: str, content: str) -> WrittenPath:
        """成果物を書いた体で `WrittenPath` を返す。"""
        self.log.record(
            "OutcomeWriter.write_outcome", folders=folders, filename=filename, content=content
        )
        if self.raises is not None:
            raise self.raises
        return WrittenPath(
            rel_path=f"outcome/{filename}",
            bytes=len(content.encode("utf-8")),
            overwritten=self.overwritten,
            archived_to=None,
        )


@dataclass(slots=True)
class StubRepoFiles:
    """`RepoReader`・`RepoWriter` のスタブ（D25。作業ツリーを辞書で模す）。"""

    log: CallLog
    files: dict[str, str] = field(default_factory=dict[str, str])
    raises: BaseException | None = None

    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]:
        """`subdir` で始まるパスを昇順で返す。"""
        self.log.record("RepoReader.list_files", repos=repos, subdir=subdir)
        if self.raises is not None:
            raise self.raises
        return sorted(path for path in self.files if path.startswith(subdir))

    def read_file(self, repos: ProjectRepos, path: str) -> str:
        """中身を返す（無ければ `RepoNotFound`）。"""
        self.log.record("RepoReader.read_file", repos=repos, path=path)
        if self.raises is not None:
            raise self.raises
        if path not in self.files:
            msg = f"ファイルが無い: {path}"
            raise RepoNotFound(msg, path)
        return self.files[path]

    def exists(self, repos: ProjectRepos, path: str) -> bool:
        """executor は呼ばない（完了ゲートは呼び手の責務）。"""
        raise _NotCalledError("RepoReader.exists")

    def write_file(self, repos: ProjectRepos, path: str, content: str) -> bool:
        """書いた体で辞書を更新し、上書きだったかを返す。"""
        self.log.record("RepoWriter.write_file", repos=repos, path=path, content=content)
        if self.raises is not None:
            raise self.raises
        overwritten = path in self.files
        self.files[path] = content
        return overwritten

    def delete_file(self, repos: ProjectRepos, path: str) -> None:
        """辞書から消す（無ければ `RepoNotFound`）。"""
        self.log.record("RepoWriter.delete_file", repos=repos, path=path)
        if self.raises is not None:
            raise self.raises
        if path not in self.files:
            msg = f"ファイルが無い: {path}"
            raise RepoNotFound(msg, path)
        del self.files[path]


# --------------------------------------------------------------------------------------
# (c) 認可（IF-29）— **4 挙動**を切り替える（FR-080・FR-081c）
# --------------------------------------------------------------------------------------

AuthorizerMode = Literal["allow", "deny", "deny_objs", "raise"]


class StubAuthorizerError(RuntimeError):
    """`enforce` が送出する例外（**拒否側に倒れる**ことの検証用＝ FR-036）。

    メッセージに秘密めいた文字列を入れて、`tool_denied` の `detail` に混ざらないことを測る。
    """


@dataclass(slots=True)
class StubAuthorizer:
    """`Authorizer` のスタブ（`enforce` の 4 挙動）。"""

    log: CallLog
    mode: AuthorizerMode = "allow"
    deny_objs: frozenset[str] = frozenset()
    """`mode="deny_objs"` のときに否と答える `obj` の集合。"""

    def enforce(self, subject: str, obj: str, act: str) -> bool:
        """認可を判定する。"""
        self.log.record("Authorizer.enforce", subject=subject, obj=obj, act=act)
        if self.mode == "raise":
            msg = "判定器が壊れた: secret-in-exception-message"
            raise StubAuthorizerError(msg)
        if self.mode == "deny":
            return False
        if self.mode == "deny_objs":
            return obj not in self.deny_objs
        return True


# --------------------------------------------------------------------------------------
# (d) コマンド台帳（IF-30）
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StubCommandRegistry:
    """`CommandRegistry` のスタブ。"""

    log: CallLog
    argv_by_name: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    known_names: tuple[str, ...] = ()
    """`UnknownCommand` の文言に載せる「利用可能な名前」。"""

    def resolve_command(self, name: str) -> list[str]:
        """登録名から固定 argv を解決する。

        Raises:
            UnknownCommand: 台帳に無い。
        """
        self.log.record("CommandRegistry.resolve_command", name=name)
        if name not in self.argv_by_name:
            msg = f"'{name}' は台帳に無い / 利用可能: {list(self.known_names)}"
            raise UnknownCommand(msg)
        return list(self.argv_by_name[name])


# --------------------------------------------------------------------------------------
# (e) LLM HTTP の前後フック（IF-40〜43）
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StubLLMHttpHook:
    """`LLMHttpHook` のスタブ（**4 メソッドすべて**）。"""

    log: CallLog
    outbound_raises: BaseException | None = None
    """`on_outbound`／`aon_outbound` が送出する例外（`RunHalted` を差し込む）。"""

    def on_outbound(self, request: httpx.Request) -> httpx.Request:
        """送信前。"""
        self.log.record("LLMHttpHook.on_outbound", request=request)
        if self.outbound_raises is not None:
            raise self.outbound_raises
        return request

    def on_inbound(self, response: httpx.Response) -> httpx.Response:
        """応答。"""
        self.log.record("LLMHttpHook.on_inbound", response=response)
        return response

    async def aon_outbound(self, request: httpx.Request) -> httpx.Request:
        """送信前（非同期）。"""
        self.log.record("LLMHttpHook.aon_outbound", request=request)
        if self.outbound_raises is not None:
            raise self.outbound_raises
        return request

    async def aon_inbound(self, response: httpx.Response) -> httpx.Response:
        """応答（非同期）。"""
        self.log.record("LLMHttpHook.aon_inbound", response=response)
        return response


# --------------------------------------------------------------------------------------
# (f) 9 契約を束ねる
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class Stubs:
    """9 契約スタブと共通の記録を 1 個にまとめた束（repo の 2 契約は 1 つのスタブ）。"""

    log: CallLog
    board_reader: StubBoardReader
    board_writer: StubBoardWriter
    vault_reader: StubVaultReader
    outcome_writer: StubOutcomeWriter
    authorizer: StubAuthorizer
    command_registry: StubCommandRegistry
    llm_http_hook: StubLLMHttpHook
    repo_files: StubRepoFiles

    def deps(self) -> ExecutorDeps:
        """`ExecutorDeps` へ写す（**9 本すべてに Protocol 型を注釈する**＝憲章 §3）。"""
        board_reader: BoardReader = self.board_reader
        board_writer: BoardWriter = self.board_writer
        vault_reader: VaultReader = self.vault_reader
        outcome_writer: OutcomeWriter = self.outcome_writer
        authorizer: Authorizer = self.authorizer
        command_registry: CommandRegistry = self.command_registry
        llm_http_hook: LLMHttpHook = self.llm_http_hook
        repo_reader: RepoReader = self.repo_files
        repo_writer: RepoWriter = self.repo_files
        return ExecutorDeps(
            board_reader=board_reader,
            board_writer=board_writer,
            vault_reader=vault_reader,
            outcome_writer=outcome_writer,
            authorizer=authorizer,
            command_registry=command_registry,
            llm_http_hook=llm_http_hook,
            repo_reader=repo_reader,
            repo_writer=repo_writer,
        )


def make_stubs(
    *,
    authorizer_mode: AuthorizerMode = "allow",
    deny_objs: frozenset[str] = frozenset(),
    files: Sequence[FileRef] = (),
    contents: dict[str, str] | None = None,
    argv_by_name: dict[str, list[str]] | None = None,
    known_names: Sequence[str] = (),
    repo_files: dict[str, str] | None = None,
) -> Stubs:
    """9 契約スタブを 1 つの記録に束ねて作る。"""
    log = CallLog()
    return Stubs(
        log=log,
        board_reader=StubBoardReader(log=log),
        board_writer=StubBoardWriter(log=log),
        vault_reader=StubVaultReader(log=log, files=list(files), contents=dict(contents or {})),
        outcome_writer=StubOutcomeWriter(log=log),
        authorizer=StubAuthorizer(log=log, mode=authorizer_mode, deny_objs=deny_objs),
        command_registry=StubCommandRegistry(
            log=log, argv_by_name=dict(argv_by_name or {}), known_names=tuple(known_names)
        ),
        llm_http_hook=StubLLMHttpHook(log=log),
        repo_files=StubRepoFiles(log=log, files=dict(repo_files or {})),
    )


# --------------------------------------------------------------------------------------
# (g) AI 偽装の 4 層（Clarifications 2026-08-25・FR-081a）
# --------------------------------------------------------------------------------------
#
#   (1) 台本 AI            … `BaseLLM` を継承し `call` が台本を順に返す
#   (2) 停止する台本 AI     … 同じ形で `call` から `RunHalted` を送出する
#   (3) `httpx` 素組み      … 差し込みアダプタを**直接**呼ぶ（台本 AI 経由では 1 度も呼ばれない）
#   (4) 組立ヘルパ          … `CrewSpec` ほかと `.env`／`ExecutorConfig` の差し替え
#
# **実 LLM・ネットワークを使わない**（SC-032）。


class ScriptedLLM(BaseLLM):
    """(1) 台本 AI。`call` が台本を順に返す（通信ゼロ）。

    **`supports_function_calling()` を `False` で明示的に定義する**（P-12）。1.15.17 の `BaseLLM`
    はこの手を**持たない**（crewai 側は `hasattr` で見る）ため、定義しないと版が上がって基底に
    生えた瞬間に黙って形が変わる。
    """

    script: list[str] = Field(default_factory=list[str])
    """返す文字列の並び。尽きたら最後の 1 つを返し続ける。"""

    call_count: int = 0
    """`call` が呼ばれた回数（テストの観測点）。"""

    def __init__(self, script: Sequence[str] | None = None, **data: Any) -> None:
        """台本を与えて組む（`script` は基底の `__init__` が宣言しないので代入で入れる）。"""
        super().__init__(model="scripted/test", **data)
        self.script = list(script or ["最終回答"])

    def call(  # noqa: PLR0913  # 基底の署名に合わせる（減らすと override が壊れる）
        self,
        messages: Any,
        tools: Any = None,
        callbacks: Any = None,
        available_functions: Any = None,
        from_task: Any = None,
        from_agent: Any = None,
        response_model: Any = None,
    ) -> str:
        """台本の次の 1 行を返す。"""
        index = min(self.call_count, len(self.script) - 1)
        self.call_count += 1
        return self.script[index]

    def supports_function_calling(self) -> bool:
        """関数呼び出しに対応しないと明示する（P-12）。"""
        return False


class HaltingLLM(ScriptedLLM):
    """(2) `call` から上限停止（`RunHalted`）を送出する台本 AI。

    実系では上限停止は差し込み（HTTP トランスポート層）から飛ぶが、台本 AI では差し込みが
    1 度も呼ばれない（F-31）。**`kickoff()` の内側から `RunHalted` が飛ぶ**という一点だけを
    再現するために `call` から送出する。
    """

    halt_kind: FailureKind = FailureKind.limit_requests
    halt_measured: float = 11.0
    halt_limit: float = 10.0
    halt_request_no: int = 11
    calls_before_halt: int = 0
    """この回数だけ普通に返してから停止する（0 なら 1 回目で停止）。"""

    def call(  # noqa: PLR0913  # 基底の署名に合わせる
        self,
        messages: Any,
        tools: Any = None,
        callbacks: Any = None,
        available_functions: Any = None,
        from_task: Any = None,
        from_agent: Any = None,
        response_model: Any = None,
    ) -> str:
        """`calls_before_halt` 回だけ台本を返し、そのあと `RunHalted` を送出する。"""
        if self.call_count < self.calls_before_halt:
            return super().call(
                messages,
                tools,
                callbacks,
                available_functions,
                from_task,
                from_agent,
                response_model,
            )
        self.call_count += 1
        raise RunHalted(
            kind=self.halt_kind,
            measured=self.halt_measured,
            limit=self.halt_limit,
            request_no=self.halt_request_no,
        )


def make_request(url: str = "https://example.invalid/v1/chat/completions") -> httpx.Request:
    """(3) `httpx.Request` を素で組む（クライアントを 1 個も作らない）。"""
    return httpx.Request("POST", url, json={"messages": []})


def make_response(status_code: int = 200) -> httpx.Response:
    """(3) `httpx.Response` を素で組む。"""
    return httpx.Response(status_code, json={"choices": []}, request=make_request())


# --------------------------------------------------------------------------------------
# (h) 組立ヘルパ（`CrewSpec` ほか）と差し替え点
# --------------------------------------------------------------------------------------


def make_profile(
    *,
    provider_kind: ProviderKind = ProviderKind.openai_compatible,
    base_url: str = "http://127.0.0.1:11434/v1",
    model_id: str = "local-model",
    api_key_ref: str = "",
    extra_headers: dict[str, str] | None = None,
) -> LLMProfile:
    """`LLMProfile` を組む。"""
    return LLMProfile(
        name="p1",
        provider_kind=provider_kind,
        base_url=base_url,
        model_id=model_id,
        context_window=8192,
        api_key_ref=api_key_ref,
        extra_headers=dict(extra_headers or {}),
        threshold_t=4096,
    )


def make_member(
    *,
    persona_key: PersonaKey = PersonaKey.programmer,
    tools: Sequence[ToolName] = (),
    skills: Sequence[str] = (),
    mcps: Sequence[McpSpec] = (),
    max_iter: int | None = None,
    max_rpm: int | None = None,
    max_execution_time: int | None = None,
    profile: LLMProfile | None = None,
) -> Member:
    """`Member` を組む（人格の 3 欄は `PromptBundle` 側を採るので**別の文言**にしておく）。"""
    return Member(
        member_id="m1",
        project_key="pj",
        persona_key=persona_key,
        role="MEMBER-ROLE",
        goal="MEMBER-GOAL",
        backstory="MEMBER-BACKSTORY",
        llm_profile=profile if profile is not None else make_profile(),
        tools=list(tools),
        skills=list(skills),
        mcps=list(mcps),
        max_iter=max_iter,
        max_rpm=max_rpm,
        max_execution_time=max_execution_time,
        bd_actor="ai:programmer",
        casbin_subject="programmer",
    )


def make_bundle(
    *,
    persona_key: PersonaKey = PersonaKey.programmer,
    task_tools: Sequence[ToolName] | None = None,
    purpose: PromptPurpose = PromptPurpose.work,
    proposal_schema: str | None = None,
) -> PromptBundle:
    """`PromptBundle` を組む（人格の 3 欄は `BUNDLE-` 接頭で識別する＝ C4 の観測点）。"""
    return PromptBundle(
        persona_key=persona_key,
        role="BUNDLE-ROLE",
        goal="BUNDLE-GOAL",
        backstory="BUNDLE-BACKSTORY",
        task_description="やること",
        expected_output="期待する出力",
        task_tools=None if task_tools is None else list(task_tools),
        purpose=purpose,
        proposal_schema=proposal_schema,
    )


def make_folders(*, role_dirs: dict[PersonaKey, str] | None = None) -> ProjectFolders:
    """`ProjectFolders` を組む（役割の絞りの検証用に**複数の役割**を既定で載せる）。"""
    if role_dirs is None:
        role_dirs = {
            PersonaKey.programmer: "roles/programmer",
            PersonaKey.architect: "roles/architect",
            PersonaKey.writer: "roles/writer",
        }
    return ProjectFolders(
        pj_input_dir="pj/input", role_dirs=dict(role_dirs), outcome_dir="pj/outcome"
    )


def make_spec(
    *,
    issue_id: str = "bd-1",
    mode: Literal["propose", "execute"] = "execute",
    purpose: PromptPurpose = PromptPurpose.work,
    bundle: PromptBundle | None = None,
    member: Member | None = None,
    hooks: Sequence[HookRef] = (),
    folders: ProjectFolders | None = None,
    parent_acceptance: str | None = None,
) -> CrewSpec:
    """`CrewSpec` を組む（`run` の入力）。"""
    return CrewSpec(
        issue_id=issue_id,
        mode=mode,
        purpose=purpose,
        bundle=bundle if bundle is not None else make_bundle(),
        member=member if member is not None else make_member(),
        hooks=HooksConfig(hooks=list(hooks)),
        folders=folders if folders is not None else make_folders(),
        parent_acceptance=parent_acceptance,
    )


def make_config(
    *,
    run_command_timeout_sec: int = 30,
    max_iter: int = 20,
    max_rpm: int | None = None,
    event_allowlist: Sequence[str] | None = None,
    hook_catalog: Sequence[str] = (),
    disable_crewai_telemetry: bool = True,
) -> ExecutorConfig:
    """`ExecutorConfig` を **6 キーすべて明示**で組む（実装の `config.json` を読まない）。"""
    return ExecutorConfig(
        run_command_timeout_sec=run_command_timeout_sec,
        max_iter=max_iter,
        max_rpm=max_rpm,
        event_allowlist=(
            list(DEFAULT_EVENT_ALLOWLIST) if event_allowlist is None else list(event_allowlist)
        ),
        hook_catalog=list(hook_catalog),
        disable_crewai_telemetry=disable_crewai_telemetry,
    )


def use_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> Path:
    """`.env` を一時ファイルに差し替える（**実運用の `.env` に触れない**）。

    差し替え点は **`executor.models.env_file_path`** である。`executor.env_file_path`
    （再エクスポート）を差し替えても**効かない** —— `read_env_values()` は自分の module 大域を
    引くので、再エクスポート先の名前を替えても参照先は変わらない。
    """
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr("executor.models.env_file_path", lambda: path)
    return path


def use_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> Path:
    """`config.json` を一時ファイルに差し替える（差し替え点は `executor.models.config_path`）。"""
    path = tmp_path / "config.json"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr("executor.models.config_path", lambda: path)
    return path
