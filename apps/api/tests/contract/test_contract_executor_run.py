"""executor 提供の IF-48（`CrewExecutor.run`）の適合検証。US6。

**実 CrewAI** を偽 LLM サーバへ通す（FR-010。外部の実 LLM API へは 1 度も通信しない）。
台本の `content` を `Final Answer:` 形にすると 1 往復で確定する（R-09 の実測）。
**実 kickoff は 2 本に限る**（K-4）—— 正常系と、CrewAI 由来の失敗を `ExecutorFailure` へ
翻訳する 1 本。後者は台本の応答を 5xx に振るので往復が確定せず終わる。残る 2 本
（`hook_catalog` 照合の `ExecutorFailure` と `RunHalted` の伝播）は kickoff の前・入口で
決着するので実行時間を押し上げない。

Z-6 の **executor 面**（FR-024・SC-004）: `RunHalted` は `BaseException` 直系なので、
`service.py` の中間の `except Exception`（129・276・330 行）に握られずに executor の境界まで
届き、`CrewOutcome(halted=True)` になる。**`ExecutorFailure` に翻訳されない**。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from _support.contract_stubs import (
    CallLog,
    StubAuthorizer,
    StubLLMHttpHook,
    StubOutcomeWriter,
)
from conftest import (
    EXECUTOR_API_KEY_REF,
    FINAL_ANSWER_CONTENT,
    make_llm_profile,
    make_llm_script,
    make_member,
    make_project_folders,
)

from common.interfaces.executor import (
    CrewEvent,
    CrewEventKind,
    CrewOutcome,
    CrewSpec,
    ExecutorFailure,
)
from common.interfaces.monitor import FailureKind, RunHalted
from common.interfaces.settings import HookRef, HooksConfig, LLMProfile
from common.interfaces.stages import PromptBundle, PromptPurpose
from common.interfaces.vault import WrittenPath
from common.kernel.enums import PersonaKey, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.fake_llm_server import FakeLlmServer
    from conftest import ExecutorFactory

    from common.interfaces.executor import CrewExecutor

ISSUE_ID = "contractbd-000001"
ANSWER = "契約テストの出力"
HTTP_SERVER_ERROR = 500
"""偽 LLM サーバに返させる異常応答の状態行（kickoff 段の失敗を作る）。"""


def _spec(
    profile: LLMProfile,
    *,
    hooks: HooksConfig | None = None,
    tools: list[ToolName] | None = None,
) -> CrewSpec:
    """`run` に渡す `CrewSpec`（既定は **1 Agent・1 Task・道具なし**の最小形）。

    Args:
        profile: 偽 LLM サーバを指す LLM 設定。
        hooks: Hook の宣言（既定は 0 件）。
        tools: 段側の道具宣言（`PromptBundle.task_tools`）。**既定は `None`**＝人格側の
            宣言（見本は空）を使う。値を渡すのは申告経路を踏む 1 本だけである。

    Returns:
        `run` に渡す入力。
    """
    member = make_member(PersonaKey.programmer, profile=profile)
    bundle = PromptBundle(
        persona_key=PersonaKey.programmer,
        role=member.role,
        goal=member.goal,
        backstory=member.backstory,
        task_description="契約テストの作業指示",
        expected_output="契約テストの期待成果",
        task_tools=tools,
        purpose=PromptPurpose.work,
        proposal_schema="Proposal",
    )
    return CrewSpec(
        issue_id=ISSUE_ID,
        mode="execute",
        purpose=PromptPurpose.work,
        bundle=bundle,
        member=member,
        hooks=hooks if hooks is not None else HooksConfig(hooks=[]),
        folders=make_project_folders(),
        parent_acceptance=None,
    )


@pytest.mark.contract_if("IF-48")
def test_contract_executor_run_returns_crew_outcome(
    crew_executor: CrewExecutor,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-48 正常系: 台帳の出力欄どおりの `CrewOutcome` が返る（FR-015・FR-016）。

    出力欄が名指しする 4 群を確かめる —— `proposals`（提案）・`task_output`・
    `events`（**中立型**であって crewai の型ではない）・`halted`。
    """
    server = fake_llm_server(make_llm_script(content=FINAL_ANSWER_CONTENT))
    profile = make_llm_profile(base_url=server.base_url, api_key_ref=EXECUTOR_API_KEY_REF)

    outcome = crew_executor.run(_spec(profile))

    assert isinstance(outcome, CrewOutcome)
    assert outcome.task_output == ANSWER
    assert outcome.proposal is None  # mode="execute" なので提案は無い
    assert outcome.halted is False
    assert outcome.halted_reason is None
    assert outcome.events, "中立型イベントが 1 件も積まれていない"
    assert all(isinstance(event, CrewEvent) for event in outcome.events)
    assert server.call_count == 1, "偽 LLM サーバへ 1 往復で確定する（R-09）"


DECLARED_OUTCOME_NAME = "report.md"
WRITTEN = WrittenPath(
    rel_path=f"pj/outcome/{DECLARED_OUTCOME_NAME}", bytes=42, overwritten=False, archived_to=None
)
"""`write_outcome` 道具が保管庫の書き手から受け取る戻り（`CrewOutcome.written` へ積まれる）。"""

DECLARED_OUTCOME_BODY = "契約テストの成果物"
"""`write_outcome` 道具に書かせる本文（台本と assert で同じ値を使う）。"""

DECLARED_TOOLS = [
    ToolName.propose_breakdown,
    ToolName.submit_review,
    ToolName.escalate_to_human,
    ToolName.write_outcome,
]
"""1 回の kickoff で呼ばせる道具（申告 3 種 ＋ 書込 1 種）。"""


def _tool_call(index: int, name: ToolName, arguments: object) -> dict[str, Any]:
    """OpenAI 互換の道具呼び出し 1 件（`arguments` は**文字列**で渡す規約）。"""
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": name.value,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _declaring_script() -> list[dict[str, Any]]:
    """1 往復目で 4 道具を呼ばせ、2 往復目で確定させる台本。

    **実 CrewAI は native function calling で道具を呼ぶ**（本 feature の実測 —— 要求本文に
    `tools` と `tool_choice: auto` が載り、`Thought/Action` の本文解析ではない）。したがって
    台本側も `message.tool_calls` で返す。

    Returns:
        `FakeLlmServer` に渡す 2 往復の台本。
    """
    calls = _tool_call(
        1,
        ToolName.propose_breakdown,
        {
            "items": [
                {
                    "title": "契約テストの子課題",
                    "summary": "要約",
                    "description": "説明",
                    "acceptance": ["- [ ] 契約どおりに振る舞うことを確認できる"],
                    "depends_on": [],
                    "persona": None,
                }
            ]
        },
    )
    review = _tool_call(
        2,
        ToolName.submit_review,
        {"approved": True, "feedback": "契約テストの所見", "files_read": [DECLARED_OUTCOME_NAME]},
    )
    escalation = _tool_call(
        3,
        ToolName.escalate_to_human,
        {
            "what_i_tried": "試したこと",
            "why_stuck": "行き詰まった理由",
            "decision_needed": "人に決めてほしいこと",
        },
    )
    write = _tool_call(
        4,
        ToolName.write_outcome,
        {"filename": DECLARED_OUTCOME_NAME, "content": DECLARED_OUTCOME_BODY},
    )

    first = make_llm_script(content=None)[0]
    first["finish_reason"] = "tool_calls"
    first["tool_calls"] = [calls, review, escalation, write]
    return [first, make_llm_script(content=FINAL_ANSWER_CONTENT)[0]]


@pytest.mark.contract_if("IF-48")
def test_contract_executor_run_collects_tool_declarations_into_the_outcome(
    call_log: CallLog,
    executor_factory: ExecutorFactory,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-48: `CrewOutcome` の **`proposal`・`review`・`escalation`・`written`** を踏む。

    台帳の出力欄は `CrewOutcome（proposals｜task_output｜review verdict／escalate 要求・
    written outcomes・events（中立型）・halted）` だが、上の正常系が踏むのは `task_output`・
    `events`・`halted` だけで、残る 4 群は**いずれも `None`／空のまま**である（正常系は
    `assert outcome.proposal is None` と書いており、申告の経路を通らないことを自認している）。

    これらは **Tool 経由の申告**でしか埋まらない（`executor/models.py` の `set_proposal`・
    `set_review`・`set_escalation`・`add_written`）。実 CrewAI は native function calling で
    道具を呼ぶので、偽 LLM サーバの台本に `message.tool_calls` を 4 件載せて 1 往復目で
    4 道具を呼ばせ、2 往復目で確定させる。

    **実 kickoff を通すテストは本ファイルに 3 本ある**（正常系・`phase="kickoff"`・
    Tool 経由申告）。本テストはそのうち Tool 経由申告の 1 本である（plan K-4。2 本 → 3 本）。
    **増やすときは plan K-4 の読み替え（`design-sync.md` SD-10）を読んでから足す**。
    したがって 4 群を**1 回の実行にまとめて**踏む —— 段の別（`propose`／`execute`）で
    道具が変わる訳ではなく、装備は `PromptBundle.task_tools` の宣言だけで決まる（D16-a）。

    申告 4 道具は**盤面にも保管庫にも 1 行も書かない**（FR-049・FR-051〜053）。実際に外へ
    出るのは `write_outcome` の 1 件だけであることまで見る。

    道具の前段（`GuardedTool._enforce`）は既定で**すべて拒否**なので、認可判定器を
    「許可」に差し替える（差し替えなければ 4 件とも拒否文言を返して申告が 1 つも埋まらない）。

    保管庫へ渡す**中身**まで見る（B-23）—— 台帳 IF-48 の入力欄は `CrewSpec` の一部として
    `ProjectFolders` を名指しするので、`write_outcome` へ渡る `folders` が
    **`CrewSpec.folders` そのもの**であることを照合する（自前で組み直す実装・入力の
    フォルダを捨てる実装を弾く）。本文も台本で書かせた値と突き合わせる。
    """
    server = fake_llm_server(_declaring_script())
    profile = make_llm_profile(base_url=server.base_url, api_key_ref=EXECUTOR_API_KEY_REF)
    executor: CrewExecutor = executor_factory(
        StubLLMHttpHook(call_log),
        authorizer_stub=StubAuthorizer(call_log, allow=True),
        outcome_writer_stub=StubOutcomeWriter(call_log, written=WRITTEN),
    ).crew_executor

    outcome = executor.run(_spec(profile, tools=DECLARED_TOOLS))

    assert outcome.proposal is not None
    assert [item.title for item in outcome.proposal.items] == ["契約テストの子課題"]

    assert outcome.review is not None
    assert outcome.review.approved is True
    assert outcome.review.feedback == "契約テストの所見"
    assert outcome.review.files_read == [DECLARED_OUTCOME_NAME]

    assert outcome.escalation is not None
    assert outcome.escalation.decision_needed == "人に決めてほしいこと"

    assert outcome.written == [WRITTEN]

    # 道具を宣言した実行では CrewAI が `Final Answer:` の接頭辞を剥がさない（本 feature の
    # 実測。道具なしの正常系は剥がした本文だけを返す）。台帳が規定するのは「最終出力が
    # 返ること」なので、**含むこと**で主張して整形の差に縛られない。
    assert outcome.task_output is not None
    assert ANSWER in outcome.task_output
    assert outcome.halted is False
    assert any(event.kind is CrewEventKind.tool_called for event in outcome.events)

    written_calls = call_log.of("OutcomeWriter", "write_outcome")
    assert len(written_calls) == 1, "書くのは write_outcome の 1 件だけ"
    written_kwargs = written_calls[0].kwargs
    assert written_kwargs["filename"] == DECLARED_OUTCOME_NAME
    assert written_kwargs["folders"] == make_project_folders(), (
        "入力の `ProjectFolders` をそのまま渡す（自前で組み直さない）"
    )
    assert written_kwargs["content"] == DECLARED_OUTCOME_BODY
    assert not call_log.of("BoardWriter", "create"), "申告は盤面に 1 行も書かない（FR-049）"
    assert not call_log.of("BoardWriter", "close"), "完了は呼び手が判断する（FR-051）"


@pytest.mark.contract_if("IF-48")
def test_contract_executor_run_raises_executor_failure(
    crew_executor: CrewExecutor,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-48 例外系: CrewAI の組立／実行の失敗を `ExecutorFailure` に翻訳する（R-16）。

    `hook_catalog` に無い名前の Hook を 1 件入れて `run()` を呼ぶ（`config.json` の
    `hook_catalog` は空）。**主張するのは型（`ExecutorFailure`）の送出**である ——
    `phase` の値は台帳が定めていないので参考として記録に留める。
    """
    server = fake_llm_server(make_llm_script())
    profile = make_llm_profile(base_url=server.base_url, api_key_ref=EXECUTOR_API_KEY_REF)
    hooks = HooksConfig(hooks=[HookRef(name="no-such-hook", points="PRE_TOOL_CALL", agents=[])])

    with pytest.raises(ExecutorFailure) as caught:
        crew_executor.run(_spec(profile, hooks=hooks))

    assert caught.value.phase == "build"  # 参考（台帳は phase を定めていない）
    assert server.call_count == 0, "組立段で止まるので LLM へは 1 度も送らない"


@pytest.mark.contract_if("IF-48")
def test_contract_executor_run_translates_a_crewai_failure_into_executor_failure(
    crew_executor: CrewExecutor,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-48 例外系: **CrewAI 由来**の失敗を `ExecutorFailure` に翻訳する（US6/AC2・FR-017）。

    台帳の例外欄は `ExecutorFailure（**CrewAI 例外の翻訳**）` である。上のテストが踏むのは
    executor **自身の事前検査**（`hook_catalog` 照合＝ `phase="build"`）で、`call_count == 0`
    が示すとおり CrewAI には到達していない。ここは実 CrewAI を走らせ、偽 LLM サーバが
    異常応答を返す状況で `kickoff` 段（`service.py:278-280`）の翻訳を踏む。

    観点は 3 つ —— (1) crewai／litellm 由来の例外が**そのまま呼び手へ漏れない**
    (2) `ExecutorFailure` に翻訳される (3) `reason` に**元の例外の型名**が残る（原因の追跡が
    切れない）。

    **実 kickoff を通すテストは本ファイルに 3 本ある**（正常系・`phase="kickoff"`・
    Tool 経由申告）。本テストはそのうち `phase="kickoff"` の 1 本である（plan K-4）。
    **増やすときは plan K-4 の読み替え（`design-sync.md` SD-10）を読んでから足す**。
    """
    script = make_llm_script()
    script[0]["status"] = HTTP_SERVER_ERROR  # 台本の応答を 5xx に振る
    server = fake_llm_server(script)
    profile = make_llm_profile(base_url=server.base_url, api_key_ref=EXECUTOR_API_KEY_REF)

    with pytest.raises(ExecutorFailure) as caught:
        crew_executor.run(_spec(profile))

    assert caught.value.phase == "kickoff"  # 参考（台帳は phase を定めていない）
    assert caught.value.reason, "元の例外の型名を落とさない"
    assert caught.value.cause is not None
    assert server.call_count >= 1, "CrewAI に到達したうえでの失敗である"


@pytest.mark.contract_if("IF-48")
def test_contract_executor_run_propagates_run_halted_into_halted_outcome(
    call_log: CallLog,
    executor_factory: ExecutorFactory,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """`RunHalted` の**伝播**（Z-6 の executor 面。FR-024・SC-004）。

    `on_outbound` で `RunHalted` を送出する Hook スタブを `ExecutorDeps.llm_http_hook` へ渡し、
    3 点を確かめる ——
    (1) 中間の `except Exception`（`service.py:129`・`276`・`330`）に握られず境界まで届く
    (2) `CrewOutcome(halted=True)` として返る
    (3) **`ExecutorFailure` に翻訳されない**。
    """
    server = fake_llm_server(make_llm_script())
    profile = make_llm_profile(base_url=server.base_url, api_key_ref=EXECUTOR_API_KEY_REF)
    halted = RunHalted(FailureKind.limit_requests, measured=2.0, limit=1.0, request_no=2)
    hook = StubLLMHttpHook(call_log, outbound_raises=halted)
    crew_executor: CrewExecutor = executor_factory(hook).crew_executor

    outcome = crew_executor.run(_spec(profile))  # (3) ExecutorFailure に翻訳されない

    assert outcome.halted is True  # (2)
    assert outcome.halted_reason is FailureKind.limit_requests  # (1) 中身まで届いている
    assert call_log.of("LLMHttpHook", "on_outbound"), "Hook が実際に呼ばれた"
