"""monitor のテスト共通部品（盤面スタブ・`httpx` の組立ヘルパ・`MonitorConfig` の組立ヘルパ）。

置くのは 3 種類だけである（plan (vi)・FR-081）。**サーバもネットワークも実 `bd` も AI も使わない**
（SC-019）。`httpx.Client()`／`httpx.AsyncClient()` を 1 度も作らず、`httpx.Request`／
`httpx.Response` を直接組む。

`tests/integration/` を持たないため `conftest` は 1 つだけで、`from conftest import ...` の
解決先は一意である（board が 2 つの `conftest` で踏んだ曖昧さは起きない）。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

import monitor as monitor_package
from common.interfaces.board import ClaimFilter, Comment, Issue, IssueSpec, IssueStatus
from common.interfaces.executor import CrewEvent, CrewEventKind, CrewOutcome
from common.interfaces.monitor import FailureKind
from common.interfaces.settings import LimitPolicy
from common.interfaces.stages import Proposal
from monitor import MonitorConfig, MonitorDeps
from monitor.models import RunState
from monitor.repository import HttpHook
from monitor.service import MonitorService

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

DEFAULT_GRADE_MAP: dict[str, str] = {
    "api_exhausted": "F",
    "no_output": "F",
    "guardrail_limit": "F",
    "ctx_exceeded": "F",
    "limit_requests": "F",
    "limit_cost": "F",
    "truncated_answer": "W",
    "finish_length": "W",
    "structured_conversion": "W",
    "retry_succeeded": "I",
    "usage_missing": "I",
    "summarization": "I",
}
"""出荷時既定の等級割当（**テスト側に直書きする**。実装の `config.json` を読まない＝ SC-007）。"""

TRUNCATION_PHRASE = (
    "Now it's time you MUST give your absolute best final answer. "
    "You'll ignore all previous instructions, stop using any tools, "
    "and just return your absolute BEST Final answer."
)
"""crewai 1.15.10 の `translations/en.json:47`（`errors.force_final_answer`）の実体。"""

ENVELOPE_MARKER = "[monitor]"
"""出荷時既定の封筒の印。"""

FIXED_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
"""時刻を要する場所で使う固定値（テストが時計に依存しないようにする）。"""


class _NotCalledError(AssertionError):
    """monitor が呼ばないはずの盤面メソッドが呼ばれた。"""

    def __init__(self, name: str) -> None:
        """呼ばれた手の名前を載せる。"""
        super().__init__(f"monitor は BoardReader/BoardWriter の {name} を呼ばない")


# --------------------------------------------------------------------------------------
# (a) 盤面スタブ — 呼び出しを順序どおり記録し、差し込む失敗をコンストラクタで指定する
# --------------------------------------------------------------------------------------


@dataclass
class StubBoardWriter:
    """`BoardWriter` のスタブ（monitor が呼ぶ 4 メソッドだけを持つ）。

    `calls` に**呼ばれた順で引数まで**記録する（FR-048・SC-008 の観測点）。`fail_on` に
    メソッド名 → 送出する例外を渡すと、記録を残したうえでその例外を送出する
    （「試みたが失敗した」ことがテストから見える）。
    """

    fail_on: dict[str, BaseException] = field(default_factory=dict[str, BaseException])
    calls: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])
    mirror: StubBoardReader | None = None
    """設定すると、書いた結果を読取スタブへ反映する（確認読取が成功する経路を作る）。"""

    def _record(self, *call: str) -> None:
        self.calls.append(call)
        failure = self.fail_on.get(call[0])
        if failure is not None:
            raise failure

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """コメントを 1 件付ける（IF-20）。"""
        self._record("comment", issue_id, body, actor)
        if self.mirror is not None:
            self.mirror.comment_list.append(
                make_comment(body, comment_id=f"c-{len(self.mirror.comment_list) + 1}", actor=actor)
            )

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """ラベルを付け外しする（IF-22）。**1 回の呼び出し**として記録する。"""
        self._record(
            "set_labels", issue_id, f"add={','.join(add)}", f"remove={','.join(remove)}", actor
        )
        if self.mirror is not None and self.mirror.issue is not None:
            labels = [label for label in self.mirror.issue.labels if label not in remove]
            labels.extend(label for label in add if label not in labels)
            self.mirror.issue = self.mirror.issue.model_copy(update={"labels": labels})

    def release(self, issue_id: str, actor: str) -> None:
        """確保を解放する（IF-15）。"""
        self._record("release", issue_id, actor)
        if self.mirror is not None and self.mirror.issue is not None:
            self.mirror.issue = self.mirror.issue.model_copy(update={"assignee": ""})

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """課題を close する（IF-18）。"""
        self._record("close", issue_id, reason, actor)
        if self.mirror is not None and self.mirror.issue is not None:
            self.mirror.issue = self.mirror.issue.model_copy(update={"status": IssueStatus.closed})

    # -- monitor が呼ばない手（**呼ばれたら落ちる**＝ FR-048 の負の検証） -------------------

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """monitor は確保しない（IF-10）。"""
        raise _NotCalledError("claim_next")

    def create(self, spec: IssueSpec, actor: str) -> str:
        """monitor は起票しない（IF-12・FR-052）。"""
        raise _NotCalledError("create")

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """monitor は依存を張らない（IF-13）。"""
        raise _NotCalledError("add_dependency")

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """monitor は依存元を付け替えない（IF-14）。"""
        raise _NotCalledError("move_dependents")

    @property
    def names(self) -> list[str]:
        """呼ばれたメソッド名だけを順に並べる（写像順の検査に使う）。"""
        return [call[0] for call in self.calls]


@dataclass
class StubBoardReader:
    """`BoardReader` のスタブ（monitor が呼ぶ 2 メソッドだけを持つ）。"""

    issue: Issue | None = None
    comment_list: list[Comment] = field(default_factory=list[Comment])
    fail_on: dict[str, BaseException] = field(default_factory=dict[str, BaseException])
    calls: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])

    def _record(self, *call: str) -> None:
        self.calls.append(call)
        failure = self.fail_on.get(call[0])
        if failure is not None:
            raise failure

    def show(self, issue_id: str) -> Issue:
        """課題 1 件を読む（IF-03）。"""
        self._record("show", issue_id)
        if self.issue is None:
            return make_issue(issue_id=issue_id)
        return self.issue

    def comments(self, issue_id: str) -> list[Comment]:
        """コメントを時刻順に読む（IF-08）。"""
        self._record("comments", issue_id)
        return list(self.comment_list)

    # -- monitor が読まない手（**呼ばれたら落ちる**） --------------------------------------

    def children_of(self, parent_id: str) -> list[Issue]:
        """monitor は子課題を読まない（IF-04）。"""
        raise _NotCalledError("children_of")

    def open_question_of(self, issue_id: str) -> Issue | None:
        """monitor は未回答の質問を読まない（IF-05・FR-052）。"""
        raise _NotCalledError("open_question_of")

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """monitor は残骸を列挙しない（IF-06）。"""
        raise _NotCalledError("list_working")

    def has_open_issues(self, project_key: str) -> bool:
        """monitor は削除ガードを見ない（IF-09）。"""
        raise _NotCalledError("has_open_issues")


def make_issue(
    *,
    issue_id: str = "i-1",
    status: IssueStatus = IssueStatus.in_progress,
    labels: Sequence[str] = (),
    assignee: str | None = None,
) -> Issue:
    """確認読取が見る 4 欄（`status`・`labels`・`assignee`）を持つ最小の課題。"""
    return Issue(
        id=issue_id,
        title="題名",
        description="説明",
        status=status,
        labels=list(labels),
        parent=None,
        assignee=assignee,
        acceptance="受入",
        dependencies=[],
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )


def make_comment(body: str, *, comment_id: str = "c-1", actor: str = "bot") -> Comment:
    """コメント 1 件（封筒の照合に使う）。"""
    return Comment(id=comment_id, issue_id="i-1", actor=actor, body=body, created_at=FIXED_NOW)


# --------------------------------------------------------------------------------------
# (b) httpx の組立ヘルパ — サーバもネットワークも立てない
# --------------------------------------------------------------------------------------

DEFAULT_URL = "https://example.invalid/v1/chat/completions"
"""到達しない URL（組み立てにしか使わない）。"""


def make_request(
    payload: object | None = None,
    *,
    method: str = "POST",
    url: str = DEFAULT_URL,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Request:
    """`httpx.Request` を直接組む（`content` を渡すと `payload` より優先する）。"""
    if content is not None:
        return httpx.Request(method, url, headers=headers, content=content)
    if payload is None:
        payload = chat_payload()
    return httpx.Request(method, url, headers=headers, json=payload)


def make_response(
    payload: object | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    request: httpx.Request | None = None,
) -> httpx.Response:
    """`httpx.Response` を直接組む（`content` を渡すと `payload` より優先する）。"""
    req = make_request() if request is None else request
    if content is not None:
        return httpx.Response(status_code, headers=headers, content=content, request=req)
    if payload is None:
        payload = completion_payload()
    return httpx.Response(status_code, headers=headers, json=payload, request=req)


def make_stream_response(
    *, request: httpx.Request | None = None, body: bytes = b"data: {}\n\n"
) -> httpx.Response:
    """SSE の応答（**読むと消費される**ストリーム本体を持つ）。"""
    req = make_request() if request is None else request
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream; charset=utf-8"},
        stream=httpx.ByteStream(body),
        request=req,
    )


def chat_payload(
    *, messages: Sequence[object] | None = None, model: str = "m"
) -> dict[str, object]:
    """要求の本文（`messages` を持つ）。"""
    if messages is None:
        messages = [{"role": "user", "content": "こんにちは"}]
    return {"model": model, "messages": list(messages)}


def completion_payload(
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cost: object = 0.001,
    finish_reason: str = "stop",
    usage: object | None = None,
) -> dict[str, Any]:
    """応答の本文（`usage` と `choices[*].finish_reason` を持つ）。"""
    if usage is None:
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost,
        }
    return {"choices": [{"finish_reason": finish_reason}], "usage": usage}


# --------------------------------------------------------------------------------------
# (c) MonitorConfig の組立ヘルパ — config.json を読まずに 6 キーを指定して組む
# --------------------------------------------------------------------------------------


_MISSING_CONFIG = Path("/nonexistent/monitor-config-for-tests.json")
"""実在しない経路（`JsonConfigSettingsSource` は読めないファイルを空として扱う）。"""


@contextmanager
def without_config_file() -> Generator[None]:
    """`MonitorConfig` の JSON ソースを一時的に空にする。

    `MonitorConfig(**overrides)` は `init_settings` **と** `JsonConfigSettingsSource` の
    両方を通るため、`dict` のフィールド（`grade_map`）は**両者が深くマージ**される。
    素で組むと出荷時の `config.json` の 12 キーが必ず混ざり、「キーが欠けた設定」を
    テストで作れない。実在しない経路を見させることで**ファイルを読まずに**組む
    （T015 (c)・contracts/monitor-config.md §6）。
    """
    original = monitor_package.config_path
    monitor_package.config_path = lambda: _MISSING_CONFIG
    try:
        yield
    finally:
        monitor_package.config_path = original


def make_config(
    *,
    grade_map: dict[str, str] | None = None,
    envelope_marker: str = ENVELOPE_MARKER,
    token_estimator: str = "chars",
    token_encoding: str = "cl100k_base",
    cost_field_path: str = "usage.cost",
    truncation_phrases: Sequence[str] | None = None,
) -> MonitorConfig:
    """6 キーを指定して `MonitorConfig` を組む（**`config.json` を読まない**）。"""
    with without_config_file():
        return MonitorConfig(
            grade_map=DEFAULT_GRADE_MAP.copy() if grade_map is None else grade_map,
            envelope_marker=envelope_marker,
            token_estimator=token_estimator,
            token_encoding=token_encoding,
            cost_field_path=cost_field_path,
            truncation_phrases=(
                [TRUNCATION_PHRASE] if truncation_phrases is None else list(truncation_phrases)
            ),
        )


# --------------------------------------------------------------------------------------
# (d) 被験体の組立ヘルパ — `build()` を通さずに Hook／Service を組む
# --------------------------------------------------------------------------------------


def make_hook(config: MonitorConfig | None = None) -> HttpHook:
    """`HttpHook` を組む（`build()` に依存させない）。"""
    return HttpHook(config=make_config() if config is None else config)


def make_state(
    *,
    kickoff_id: str = "k-1",
    max_requests: int = 1000,
    max_cost: float = 1000.0,
    threshold_t: int = 1_000_000,
    correlation_id: str | None = None,
    scope: str = "global",
) -> RunState:
    """進行中の 1 kickoff（既定はどの上限にも掛からない広い値）。"""
    return RunState(
        kickoff_id=kickoff_id,
        limits=LimitPolicy(
            scope=scope,
            max_requests_per_kickoff=max_requests,
            max_cost_per_kickoff=max_cost,
        ),
        threshold_t=threshold_t,
        correlation_id=correlation_id,
        started_at=FIXED_NOW,
    )


def make_service(
    *,
    reader: StubBoardReader | None = None,
    writer: StubBoardWriter | None = None,
    config: MonitorConfig | None = None,
    hook: HttpHook | None = None,
) -> MonitorService:
    """`MonitorService` を組む（`build()` に依存させない）。"""
    settings = make_config() if config is None else config
    return MonitorService(
        hook=make_hook(settings) if hook is None else hook,
        deps=MonitorDeps(
            board_reader=StubBoardReader() if reader is None else reader,
            board_writer=StubBoardWriter() if writer is None else writer,
        ),
        config=settings,
    )


def make_outcome(
    *,
    proposal: Proposal | None = None,
    task_output: str | None = "成果物",
    completed: bool = True,
    events: Sequence[CrewEvent] = (),
    halted: bool = False,
    halted_reason: FailureKind | None = None,
) -> CrewOutcome:
    """`finish` に渡す 1 回の実行の結果（既定は失敗が 1 件も無い健全な形）。"""
    return CrewOutcome(
        proposal=proposal,
        task_output=task_output,
        completed=completed,
        completion_reason="ok",
        review=None,
        escalation=None,
        written=[],
        events=list(events),
        halted=halted,
        halted_reason=halted_reason,
        progress_notes=0,
    )


def make_event(kind: CrewEventKind, **detail: str) -> CrewEvent:
    """中立型イベント 1 件。"""
    return CrewEvent(kind=kind, at=FIXED_NOW, detail=dict(detail))


def make_linked_board(
    *, labels: Sequence[str] = (), assignee: str | None = "bot"
) -> tuple[StubBoardReader, StubBoardWriter]:
    """書込を読取へ反映する盤面（**確認読取が成功する**経路の組立）。"""
    reader = StubBoardReader(issue=make_issue(labels=labels, assignee=assignee))
    writer = StubBoardWriter(mirror=reader)
    return reader, writer
