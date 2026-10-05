"""記録つき契約準拠スタブ 15 種（FR-011・FR-012・FR-025。14・15 種目は D25 の repo）。

依存 Protocol の実装を偽装し、**呼び出しの引数と順序を記録**する。`common.interfaces` の
Protocol にのみ依存し、**他 feature を import しない**（FR-011）。

規約（[contracts/recording-stubs.md](../../../../specs/013-protocol-contracts/contracts/recording-stubs.md)
§3）:

1. **既定は無害**: 一覧は空、単体は `None`、真偽は偽を返す。
2. **差し替えはコンストラクタ引数**（振る舞いを変える口を `setattr` にしない）。
3. **呼ばれないはずのメソッドは `NotImplementedError`**（契約の外の呼び出しが起きたら赤くなる）。
   戻り値に「無害な既定」が作れない面（`Issue`・`Member`・`CrewOutcome` 等）は、値を渡されて
   いなければこの形に倒す。
4. **例外を投げる口を持つ**（`raises=` で契約例外を送出させられる）。
5. **状態をモジュール大域に持たない**（FR-028）。

受け側には必ず Protocol 型を注釈する（FR-012・憲章 §3）。Protocol は構造的部分型であり、
**型注釈付きの代入がなければ適合検査が発火しない**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from common.interfaces.board import Comment, Issue, IssueNotFound
from common.interfaces.orchestrator import KickoffResult, KickoffStatus
from common.interfaces.repo import RepoNotFound
from common.interfaces.settings import MemberNotFound, ProjectNotFound, UnknownCommand
from common.interfaces.vault import FolderCheck, VaultNotFound

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    import httpx

    from common.interfaces.board import ClaimFilter, IssueSpec
    from common.interfaces.executor import CrewOutcome, CrewSpec
    from common.interfaces.monitor import RunVerdict
    from common.interfaces.orchestrator import ExecutionContext
    from common.interfaces.settings import (
        HooksConfig,
        LimitPolicy,
        Member,
        Project,
        ProjectFolders,
        ProjectRepos,
        Rules,
        TaskTemplates,
    )
    from common.interfaces.stages import (
        ContextRefs,
        GateResult,
        PromptBundle,
        Proposal,
        StageSpec,
    )
    from common.interfaces.vault import FileRef, WrittenPath
    from common.kernel.enums import PersonaKey

# --------------------------------------------------------------------------------------
# 1. `CallLog` — スタブ横断で 1 本の順序つき記録
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallEntry:
    """呼び出し 1 件（位置引数も**名前に正規化して**積む）。"""

    protocol: str
    method: str
    kwargs: Mapping[str, object]


@dataclass(slots=True)
class CallLog:
    """呼ばれた順に並ぶ追記専用の記録。**スタブ横断で 1 本を共有する**。

    IF-39 の内包写像（FR-025）は「`BoardWriter` の写像と `BoardReader` の確認読取が
    `finish` の中で、この順で起きた」を主張する。2 つのスタブの呼び出しが**同一の時系列**に
    並ばないと順序を照合できない。
    """

    entries: list[CallEntry] = field(default_factory=list["CallEntry"])

    def record(self, protocol: str, method: str, kwargs: Mapping[str, object]) -> None:
        """呼び出しを 1 件積む。"""
        self.entries.append(CallEntry(protocol=protocol, method=method, kwargs=dict(kwargs)))

    def names(self) -> list[tuple[str, str]]:
        """`[("BoardWriter", "comment"), ("BoardReader", "show"), …]` を返す（順序つき）。"""
        return [(entry.protocol, entry.method) for entry in self.entries]

    def of(self, protocol: str, method: str) -> list[CallEntry]:
        """特定の Protocol・メソッドの呼び出しだけを取り出す。"""
        return [
            entry for entry in self.entries if entry.protocol == protocol and entry.method == method
        ]


# --------------------------------------------------------------------------------------
# 2. board（`BoardReader`・`BoardWriter`）
# --------------------------------------------------------------------------------------

_BOARD_READER = "BoardReader"
_BOARD_WRITER = "BoardWriter"


class StubBoardReader:
    """`BoardReader` の偽物（IF-01〜09 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        issues: Mapping[str, Issue] | None = None,
        children: Mapping[str, list[Issue]] | None = None,
        open_question: Issue | None = None,
        working: Sequence[Issue] = (),
        comments: Mapping[str, list[Comment]] | None = None,
        has_open: bool = False,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける（`raises` は全メソッド共通の送出口）。"""
        self._log = log
        self._issues = dict(issues or {})
        self._children = dict(children or {})
        self._open_question = open_question
        self._working = list(working)
        self._comments = dict(comments or {})
        self._has_open = has_open
        self._raises = raises

    def _record(self, method: str, kwargs: Mapping[str, object]) -> None:
        self._log.record(_BOARD_READER, method, kwargs)
        if self._raises is not None:
            raise self._raises

    def show(self, issue_id: str) -> Issue:
        """課題 1 件を返す（渡されていなければ `IssueNotFound`）。"""
        self._record("show", {"issue_id": issue_id})
        found = self._issues.get(issue_id)
        if found is None:
            msg = f"スタブに渡されていない課題を読もうとした: {issue_id}"
            raise IssueNotFound(msg, issue_id)
        return found

    def children_of(self, parent_id: str) -> list[Issue]:
        """子課題の一覧（既定は空）。"""
        self._record("children_of", {"parent_id": parent_id})
        return list(self._children.get(parent_id, []))

    def open_question_of(self, issue_id: str) -> Issue | None:
        """未回答の質問（既定は `None`）。"""
        self._record("open_question_of", {"issue_id": issue_id})
        return self._open_question

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """`state:working` の残骸（既定は空）。"""
        self._record("list_working", {"claim_filter": claim_filter})
        return list(self._working)

    def comments(self, issue_id: str) -> list[Comment]:
        """コメントの一覧（既定は空）。"""
        self._record("comments", {"issue_id": issue_id})
        return list(self._comments.get(issue_id, []))

    def has_open_issues(self, project_key: str) -> bool:
        """open な課題の有無（既定は偽）。"""
        self._record("has_open_issues", {"project_key": project_key})
        return self._has_open


class StubBoardWriter:
    """`BoardWriter` の偽物（IF-10〜22 の消費側として渡す）。

    **書込は記録するだけで成功したふりをする**。IF-39 の `MappingUnverified` は、この形と
    「写像前の姿を返す `StubBoardReader`」の組み合わせで踏む（contracts/recording-stubs.md §6）。
    """

    def __init__(
        self,
        log: CallLog,
        *,
        created_id: str = "stub-created-1",
        claimed: Issue | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._created_id = created_id
        self._claimed = claimed
        self._raises = raises

    def _record(self, method: str, kwargs: Mapping[str, object]) -> None:
        self._log.record(_BOARD_WRITER, method, kwargs)
        if self._raises is not None:
            raise self._raises

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """次の 1 件を確保する（既定は `None`）。"""
        self._record("claim_next", {"claim_filter": claim_filter, "actor": actor})
        return self._claimed

    def create(self, spec: IssueSpec, actor: str) -> str:
        """起票して新しい issue_id を返す。"""
        self._record("create", {"spec": spec, "actor": actor})
        return self._created_id

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """兄弟依存を張る（記録のみ）。"""
        self._record("add_dependency", {"from_id": from_id, "to_id": to_id, "actor": actor})

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """依存元を付け替える（記録のみ）。"""
        self._record("move_dependents", {"old_id": old_id, "new_id": new_id, "actor": actor})

    def release(self, issue_id: str, actor: str) -> None:
        """確保を解放する（記録のみ）。"""
        self._record("release", {"issue_id": issue_id, "actor": actor})

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """コメントを付ける（記録のみ）。"""
        self._record("comment", {"issue_id": issue_id, "body": body, "actor": actor})

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """課題を close する（記録のみ）。"""
        self._record("close", {"issue_id": issue_id, "reason": reason, "actor": actor})

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """ラベルを付け外しする（記録のみ）。"""
        self._record(
            "set_labels",
            {"issue_id": issue_id, "add": list(add), "remove": list(remove), "actor": actor},
        )


# --------------------------------------------------------------------------------------
# 3. vault（`VaultReader`・`OutcomeWriter`）
# --------------------------------------------------------------------------------------

_VAULT_READER = "VaultReader"
_OUTCOME_WRITER = "OutcomeWriter"


class StubVaultReader:
    """`VaultReader` の偽物（IF-31〜36 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        folder_check: FolderCheck | None = None,
        inputs: Sequence[FileRef] = (),
        input_body: str = "",
        outcome_body: str = "",
        outcome_exists: bool = False,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._folder_check = folder_check
        self._inputs = list(inputs)
        self._input_body = input_body
        self._outcome_body = outcome_body
        self._outcome_exists = outcome_exists
        self._raises = raises

    def _record(self, method: str, kwargs: Mapping[str, object]) -> None:
        self._log.record(_VAULT_READER, method, kwargs)
        if self._raises is not None:
            raise self._raises

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck:
        """フォルダの存在確認（既定は「すべて在る」）。"""
        self._record("check_project_folders", {"folders": folders})
        if self._folder_check is not None:
            return self._folder_check
        return FolderCheck(missing=[], ok=True)

    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]:
        """入力の一覧（既定は空）。"""
        self._record("list_inputs", {"folders": folders, "role": role})
        return list(self._inputs)

    def read_input(self, folders: ProjectFolders, ref: FileRef) -> str:
        """入力の本文（既定は空文字）。"""
        self._record("read_input", {"folders": folders, "ref": ref})
        return self._input_body

    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str:
        """成果物の本文（渡されていなければ `VaultNotFound`）。"""
        self._record("read_outcome_file", {"folders": folders, "filename": filename})
        if not self._outcome_body:
            msg = f"スタブに渡されていない成果物を読もうとした: {filename}"
            raise VaultNotFound(msg, rel_path=filename, role=None)
        return self._outcome_body

    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool:
        """成果物の存在（既定は偽）。"""
        self._record("outcome_exists", {"folders": folders, "filename": filename})
        return self._outcome_exists


class StubOutcomeWriter:
    """`OutcomeWriter` の偽物（IF-37 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        written: WrittenPath | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._written = written
        self._raises = raises

    def write_outcome(self, folders: ProjectFolders, filename: str, content: str) -> WrittenPath:
        """成果物を書いたことにして通過した実パスを返す。"""
        self._log.record(
            _OUTCOME_WRITER,
            "write_outcome",
            {"folders": folders, "filename": filename, "content": content},
        )
        if self._raises is not None:
            raise self._raises
        if self._written is None:
            msg = "StubOutcomeWriter に written= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._written


_REPO_READER = "RepoReader"
_REPO_WRITER = "RepoWriter"


class StubRepoReader:
    """`RepoReader` の偽物（IF-51〜53 の消費側として渡す。D25）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        files: Sequence[str] = (),
        body: str = "",
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける（`files` は在るとみなすパスの一覧）。"""
        self._log = log
        self._files = list(files)
        self._body = body
        self._raises = raises

    def _record(self, method: str, kwargs: Mapping[str, object]) -> None:
        self._log.record(_REPO_READER, method, kwargs)
        if self._raises is not None:
            raise self._raises

    def list_files(self, repos: ProjectRepos, subdir: str) -> list[str]:
        """一覧（既定は空）。"""
        self._record("list_files", {"repos": repos, "subdir": subdir})
        return [path for path in self._files if path.startswith(subdir)]

    def read_file(self, repos: ProjectRepos, path: str) -> str:
        """本文（渡されていなければ `RepoNotFound`）。"""
        self._record("read_file", {"repos": repos, "path": path})
        if not self._body:
            msg = f"スタブに渡されていないファイルを読もうとした: {path}"
            raise RepoNotFound(msg, rel_path=path)
        return self._body

    def exists(self, repos: ProjectRepos, path: str) -> bool:
        """存在（`files` に在れば真。既定は偽）。"""
        self._record("exists", {"repos": repos, "path": path})
        return path in self._files


class StubRepoWriter:
    """`RepoWriter` の偽物（IF-54・55 の消費側として渡す。D25）。"""

    def __init__(self, log: CallLog, *, raises: BaseException | None = None) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._raises = raises

    def write_file(self, repos: ProjectRepos, path: str, content: str) -> bool:
        """書いたことにして「新規」（偽）を返す。"""
        self._log.record(
            _REPO_WRITER, "write_file", {"repos": repos, "path": path, "content": content}
        )
        if self._raises is not None:
            raise self._raises
        return False

    def delete_file(self, repos: ProjectRepos, path: str) -> None:
        """消したことにする。"""
        self._log.record(_REPO_WRITER, "delete_file", {"repos": repos, "path": path})
        if self._raises is not None:
            raise self._raises


# --------------------------------------------------------------------------------------
# 4. settings（`SettingsReader`・`MemberResolver`・`Authorizer`・`CommandRegistry`）
# --------------------------------------------------------------------------------------

_SETTINGS_READER = "SettingsReader"
_MEMBER_RESOLVER = "MemberResolver"
_AUTHORIZER = "Authorizer"
_COMMAND_REGISTRY = "CommandRegistry"


class StubSettingsReader:
    """`SettingsReader` の偽物（IF-23〜26・IF-50 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        project: Project | None = None,
        limits: LimitPolicy | None = None,
        rules: Rules | None = None,
        hooks: HooksConfig | None = None,
        tasks: TaskTemplates | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._project = project
        self._limits = limits
        self._rules = rules
        self._hooks = hooks
        self._tasks = tasks
        self._raises = raises

    def _record(self, method: str, project_key: str) -> None:
        self._log.record(_SETTINGS_READER, method, {"project_key": project_key})
        if self._raises is not None:
            raise self._raises

    def get_project(self, project_key: str) -> Project:
        """プロジェクトを返す（渡されていなければ `ProjectNotFound`）。"""
        self._record("get_project", project_key)
        if self._project is None:
            msg = f"スタブに渡されていないプロジェクトを読もうとした: {project_key}"
            raise ProjectNotFound(msg)
        return self._project

    def get_limits(self, project_key: str) -> LimitPolicy:
        """上限を返す（渡されていなければ呼ばれないはずの面）。"""
        self._record("get_limits", project_key)
        if self._limits is None:
            msg = "StubSettingsReader に limits= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._limits

    def get_rules(self, project_key: str) -> Rules:
        """共通ルールを返す（渡されていなければ `ProjectNotFound`）。"""
        self._record("get_rules", project_key)
        if self._rules is None:
            msg = f"スタブに渡されていないルールを読もうとした: {project_key}"
            raise ProjectNotFound(msg)
        return self._rules

    def get_hooks(self, project_key: str) -> HooksConfig:
        """Hook 設定を返す（渡されていなければ `ProjectNotFound`）。"""
        self._record("get_hooks", project_key)
        if self._hooks is None:
            msg = f"スタブに渡されていない Hook 設定を読もうとした: {project_key}"
            raise ProjectNotFound(msg)
        return self._hooks

    def get_tasks(self, project_key: str) -> TaskTemplates:
        """段テンプレートを返す（渡されていなければ `ProjectNotFound`）。"""
        self._record("get_tasks", project_key)
        if self._tasks is None:
            msg = f"スタブに渡されていない段テンプレートを読もうとした: {project_key}"
            raise ProjectNotFound(msg)
        return self._tasks


class StubMemberResolver:
    """`MemberResolver` の偽物（IF-27・IF-28 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        member: Member | None = None,
        humans: Collection[str] = (),
    ) -> None:
        """返す値をコンストラクタで受ける（`humans` に居る actor だけが人間）。"""
        self._log = log
        self._member = member
        self._humans = frozenset(humans)

    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member:
        """メンバを返す（渡されていなければ `MemberNotFound`）。"""
        self._log.record(
            _MEMBER_RESOLVER, "resolve", {"project_key": project_key, "persona_key": persona_key}
        )
        if self._member is None:
            msg = f"スタブに渡されていないメンバを解決しようとした: {project_key}.{persona_key}"
            raise MemberNotFound(msg)
        return self._member

    def is_human(self, actor: str) -> bool:
        """人間かどうか（既定は偽＝台帳内の AI 扱い）。"""
        self._log.record(_MEMBER_RESOLVER, "is_human", {"actor": actor})
        return actor in self._humans


class StubAuthorizer:
    """`Authorizer` の偽物（IF-29 の消費側として渡す）。"""

    def __init__(self, log: CallLog, *, allow: bool = False) -> None:
        """判定の既定値をコンストラクタで受ける（既定は偽＝すべて拒否）。"""
        self._log = log
        self._allow = allow

    def enforce(self, subject: str, obj: str, act: str) -> bool:
        """認可を判定する（既定は偽）。"""
        self._log.record(_AUTHORIZER, "enforce", {"subject": subject, "obj": obj, "act": act})
        return self._allow


class StubCommandRegistry:
    """`CommandRegistry` の偽物（IF-30 の消費側として渡す）。"""

    def __init__(self, log: CallLog, *, argv: Mapping[str, list[str]] | None = None) -> None:
        """登録名 → argv の対応をコンストラクタで受ける。"""
        self._log = log
        self._argv = dict(argv or {})

    def resolve_command(self, name: str) -> list[str]:
        """argv を返す（渡されていなければ `UnknownCommand`）。"""
        self._log.record(_COMMAND_REGISTRY, "resolve_command", {"name": name})
        found = self._argv.get(name)
        if found is None:
            msg = f"スタブに渡されていないコマンドを解決しようとした: {name}"
            raise UnknownCommand(msg)
        return list(found)


# --------------------------------------------------------------------------------------
# 5. stages（`StageCatalog`・`ProposalGate`）
# --------------------------------------------------------------------------------------

_STAGE_CATALOG = "StageCatalog"
_PROPOSAL_GATE = "ProposalGate"


class StubStageCatalog:
    """`StageCatalog` の偽物（IF-44・IF-45 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        spec: StageSpec | None = None,
        bundle: PromptBundle | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._spec = spec
        self._bundle = bundle

    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec:
        """段を返す（渡されていなければ呼ばれないはずの面）。"""
        self._log.record(
            _STAGE_CATALOG,
            "resolve_stage",
            {"kind_labels": set(kind_labels), "has_parent": has_parent},
        )
        if self._spec is None:
            msg = "StubStageCatalog に spec= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._spec

    def build_prompt(
        self,
        spec: StageSpec,
        member: Member,
        rules: Rules,
        templates: TaskTemplates,
        ctx: ContextRefs,
        feedback: str | None,
    ) -> PromptBundle:
        """プロンプトを返す（渡されていなければ呼ばれないはずの面）。"""
        self._log.record(
            _STAGE_CATALOG,
            "build_prompt",
            {
                "spec": spec,
                "member": member,
                "rules": rules,
                "templates": templates,
                "ctx": ctx,
                "feedback": feedback,
            },
        )
        if self._bundle is None:
            msg = "StubStageCatalog に bundle= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._bundle


class StubProposalGate:
    """`ProposalGate` の偽物（IF-46・IF-47 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        gate_result: GateResult | None = None,
        tokens: Sequence[str] = (),
        repo_tokens: Sequence[str] = (),
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._gate_result = gate_result
        self._tokens = list(tokens)
        self._repo_tokens = list(repo_tokens)

    def validate_proposal(
        self, proposal: Proposal, spec: StageSpec, existing_children: list[str]
    ) -> GateResult:
        """検査の結果を返す（渡されていなければ呼ばれないはずの面）。"""
        self._log.record(
            _PROPOSAL_GATE,
            "validate_proposal",
            {
                "proposal": proposal,
                "spec": spec,
                "existing_children": list(existing_children),
            },
        )
        if self._gate_result is None:
            msg = "StubProposalGate に gate_result= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._gate_result

    def extract_outcome_tokens(self, acceptance: str) -> list[str]:
        """完了トークンを返す（既定は空）。"""
        self._log.record(_PROPOSAL_GATE, "extract_outcome_tokens", {"acceptance": acceptance})
        return list(self._tokens)

    def extract_repo_tokens(self, acceptance: str) -> list[str]:
        """リポジトリの成果物のトークンを返す（既定は空。D25）。"""
        self._log.record(_PROPOSAL_GATE, "extract_repo_tokens", {"acceptance": acceptance})
        return list(self._repo_tokens)


# --------------------------------------------------------------------------------------
# 6. executor・monitor（`CrewExecutor`・`RunMonitor`・`LLMHttpHook`）
# --------------------------------------------------------------------------------------

_CREW_EXECUTOR = "CrewExecutor"
_RUN_MONITOR = "RunMonitor"
_LLM_HTTP_HOOK = "LLMHttpHook"


class StubCrewExecutor:
    """`CrewExecutor` の偽物（IF-48 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        outcome: CrewOutcome | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._outcome = outcome
        self._raises = raises

    def run(self, spec: CrewSpec) -> CrewOutcome:
        """実行結果を返す（渡されていなければ呼ばれないはずの面）。"""
        self._log.record(_CREW_EXECUTOR, "run", {"spec": spec})
        if self._raises is not None:
            raise self._raises
        if self._outcome is None:
            msg = "StubCrewExecutor に outcome= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._outcome


class StubRunMonitor:
    """`RunMonitor` の偽物（IF-38・IF-39 の消費側として渡す）。"""

    def __init__(
        self,
        log: CallLog,
        *,
        verdict: RunVerdict | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """返す値をコンストラクタで受ける。"""
        self._log = log
        self._verdict = verdict
        self._raises = raises

    def begin(self, kickoff_id: str, limits: LimitPolicy, threshold_t: int) -> None:
        """計測開始（記録のみ）。"""
        self._log.record(
            _RUN_MONITOR,
            "begin",
            {"kickoff_id": kickoff_id, "limits": limits, "threshold_t": threshold_t},
        )

    def finish(
        self, kickoff_id: str, issue_id: str, actor: str, outcome: CrewOutcome
    ) -> RunVerdict:
        """判定を返す（渡されていなければ呼ばれないはずの面）。"""
        self._log.record(
            _RUN_MONITOR,
            "finish",
            {
                "kickoff_id": kickoff_id,
                "issue_id": issue_id,
                "actor": actor,
                "outcome": outcome,
            },
        )
        if self._raises is not None:
            raise self._raises
        if self._verdict is None:
            msg = "StubRunMonitor に verdict= を渡していない（呼ばれないはずの面）"
            raise NotImplementedError(msg)
        return self._verdict


class StubLLMHttpHook:
    """`LLMHttpHook` の偽物（IF-40〜43 の消費側として渡す）。

    `on_outbound`／`aon_outbound` は `outbound_raises=` で **`RunHalted` を送出できる**
    （FR-024。`RunHalted` は `BaseException` 直系なので中間の `except Exception` を素通りする）。
    """

    def __init__(
        self,
        log: CallLog,
        *,
        outbound_raises: BaseException | None = None,
        inbound_raises: BaseException | None = None,
    ) -> None:
        """送出させたい例外をコンストラクタで受ける（既定は素通し）。"""
        self._log = log
        self._outbound_raises = outbound_raises
        self._inbound_raises = inbound_raises

    def on_outbound(self, request: httpx.Request) -> httpx.Request:
        """送信前（既定は素通し）。"""
        self._log.record(_LLM_HTTP_HOOK, "on_outbound", {"request": request})
        if self._outbound_raises is not None:
            raise self._outbound_raises
        return request

    def on_inbound(self, response: httpx.Response) -> httpx.Response:
        """応答（既定は素通し）。"""
        self._log.record(_LLM_HTTP_HOOK, "on_inbound", {"response": response})
        if self._inbound_raises is not None:
            raise self._inbound_raises
        return response

    async def aon_outbound(self, request: httpx.Request) -> httpx.Request:
        """送信前（非同期。既定は素通し）。"""
        self._log.record(_LLM_HTTP_HOOK, "aon_outbound", {"request": request})
        if self._outbound_raises is not None:
            raise self._outbound_raises
        return request

    async def aon_inbound(self, response: httpx.Response) -> httpx.Response:
        """応答（非同期。既定は素通し）。"""
        self._log.record(_LLM_HTTP_HOOK, "aon_inbound", {"response": response})
        if self._inbound_raises is not None:
            raise self._inbound_raises
        return response


# --------------------------------------------------------------------------------------
# 15. `KickoffRunner` — orchestrator が提供する契約（IF-49。trigger が消費する）
# --------------------------------------------------------------------------------------

_KICKOFF_RUNNER = "KickoffRunner"

STUB_KICKOFF_ID = "stub-kickoff"
STUB_KICKOFF_STARTED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
STUB_KICKOFF_FINISHED_AT = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
"""時刻は**固定値**にする（テストの決定性。`now()` を使わない）。"""


class StubKickoffRunner:
    """1 回の kickoff（IF-49）。面は `run` の **1 メソッドのみ**。

    既定は無害 —— `status=ok`・`issues=[]`・`notes=[]` の `KickoffResult` を返す。
    `raises=` に `KickoffBusy` を渡すと送出する（規約 §3-4）。
    """

    def __init__(
        self,
        log: CallLog,
        *,
        result: KickoffResult | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """記録先と、差し替える戻り／送出する例外を受ける。"""
        self._log = log
        self._result = result
        self._raises = raises

    def run(self, ctx: ExecutionContext) -> KickoffResult:
        """実行文脈を受けて kickoff を 1 回まわす（既定は無害な要約を返す）。"""
        self._log.record(_KICKOFF_RUNNER, "run", {"ctx": ctx})
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return self._result
        return KickoffResult(
            kickoff_id=STUB_KICKOFF_ID,
            status=KickoffStatus.ok,
            issues=[],
            notes=[],
            started_at=STUB_KICKOFF_STARTED_AT,
            finished_at=STUB_KICKOFF_FINISHED_AT,
        )
