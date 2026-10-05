"""monitor 提供の `RunMonitor`（IF-38・IF-39）の適合検証。US5。

IF-39 は台帳の出力欄が**副作用**まで明文化している唯一の行である ——
「F/W は**本メソッド内で** board へ写像し確認読取まで行う」。したがって戻り値の型だけでは
足りず、`finish` の**中で**どの順に board が呼ばれたかを照合する（FR-025・SC-004）。

照合は**「含む」ではなく「並び」**で見る。期待の並びは monitor の現物
（`service.py` の `_map_failure`／`_map_warning`／`_verify_mapping`）を読んで確定した。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from _support.contract_stubs import StubBoardReader, StubBoardWriter
from conftest import (
    MONITOR_ACTOR,
    MONITOR_ISSUE_ID,
    MONITOR_KICKOFF_ID,
    MONITOR_THRESHOLD_T,
    make_llm_script,
    make_monitor_envelope_comment,
    make_monitor_issue,
    monitor_envelope_marker,
)

from common.interfaces.board import BoardUnavailable, IssueStatus
from common.interfaces.executor import CrewOutcome
from common.interfaces.monitor import FailureKind, Grade, MappingUnverified, RunVerdict
from common.interfaces.settings import LimitPolicy
from common.interfaces.stages import Proposal, ProposalItem
from common.kernel.enums import Labels, StateLabel

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.contract_stubs import CallLog
    from _support.fake_llm_server import FakeLlmServer

    from common.interfaces.monitor import LLMHttpHook, RunMonitor
    from monitor import MonitorHandle

LIMITS = LimitPolicy(scope="global", max_requests_per_kickoff=10, max_cost_per_kickoff=10.0)

UNATTRIBUTED_SENDS = 2
"""`begin` を呼ばないまま `on_outbound` を通す回数。

`test_contract_monitor_hook.py` の `ONE_REQUEST_LIMITS`（`max_requests_per_kickoff=1`）を
`begin` で張っていれば 2 回目で `RunHalted` になる回数である。`begin` が無いと**上限を渡す
機会そのものが無い**ので、同じ回数を通しても素通しになる（`repository.py:249-252`）。
"""

FAILING_OUTCOME = CrewOutcome(
    proposal=None,
    task_output=None,  # 観測点 6 の `no_output`（`grade_map` で **F**）
    completed=False,
    completion_reason="no output",
    review=None,
    escalation=None,
    written=[],
    events=[],
    halted=False,
    halted_reason=None,
    progress_notes=0,
)

BENIGN_OUTCOME = CrewOutcome(
    proposal=None,
    task_output="契約テストの出力",  # 失敗を 1 件も生まない（写像も確認読取も起きない）
    completed=True,
    completion_reason="done",
    review=None,
    escalation=None,
    written=[],
    events=[],
    halted=False,
    halted_reason=None,
    progress_notes=0,
)

HALTED_OUTCOME = CrewOutcome(
    proposal=None,
    task_output="契約テストの出力",  # 空にすると `no_output`（F）が立って I 級にならない
    completed=False,
    completion_reason="halted",
    review=None,
    escalation=None,
    written=[],
    events=[],
    halted=True,  # **種類を伴わない停止**（`halted_reason` は `None` のまま）
    halted_reason=None,
    progress_notes=0,
)
"""`RunVerdict.halted` を真にするためだけの実行結果。

`halted_reason` を伴わない `halted=True` は現物では「起こりえない組合せ」で、monitor は
**種類を捏造せず警告だけを残す**（`service.py:215`）。したがって失敗は 1 件も増えず、
等級は応答側の観測（`usage_missing`＝ I）だけで決まる —— `halted` の経路を等級から
切り離して見られる。
"""

WARNING_OUTCOME = CrewOutcome(
    proposal=Proposal(  # 観測点 5 の `structured_conversion`（`grade_map` で **W**）
        items=[
            ProposalItem(
                title="子",
                summary="要約",
                description="説明",
                acceptance=["- [ ] 受け入れ条件"],
                depends_on=[],
                persona=None,
            )
        ]
    ),
    task_output="提案を出した",
    completed=True,
    completion_reason="done",
    review=None,
    escalation=None,
    written=[],
    events=[],
    halted=False,
    halted_reason=None,
    progress_notes=0,
)


def _bd_unreachable() -> BoardUnavailable:
    """`bd` 不達（台帳 IF-39 の例外欄が名指しする 2 条件の片方）を表す例外。

    board の現物が組む形（実行した argv・終了コード・stderr の先頭・試行回数）に揃える。
    """
    return BoardUnavailable(
        "bd に届かない（契約テストの反例）",
        argv=["bd", "comment"],
        rc=1,
        stderr_head="no beads database found",
        attempts=1,
    )


@pytest.mark.contract_if("IF-38")
def test_contract_monitor_begin_attributes_the_measurement_to_the_kickoff(
    run_monitor: RunMonitor,
    llm_http_hook: LLMHttpHook,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-38: 台帳の入力欄（kickoff_id・`LimitPolicy`・threshold_T）どおりに計測が帰属する。

    `begin` を**呼ばない**間の送信は帰属先が無く `unattributed_requests` に数えられ、
    `begin` のあとの送信は当該 kickoff の `requests` に数えられる。
    台帳の例外欄は `—` なので、どちらの経路でも例外は送出されない（FR-018）。
    """
    server = fake_llm_server(make_llm_script(turns=2))
    payload = {"model": "demo-model", "messages": [{"role": "user", "content": "問い"}]}

    with httpx.Client() as client:
        before = client.build_request("POST", f"{server.base_url}/chat/completions", json=payload)
        llm_http_hook.on_outbound(before)  # begin より前 ＝ 帰属先が無い
        client.send(before)

        assert run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T) is None

        after = client.build_request("POST", f"{server.base_url}/chat/completions", json=payload)
        llm_http_hook.on_outbound(after)  # begin のあと ＝ この kickoff に帰属
        client.send(after)

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )

    assert verdict.grade is None, "失敗が無いので写像も確認読取も起きない"
    assert verdict.measurement.kickoff_id == MONITOR_KICKOFF_ID
    assert verdict.measurement.requests == 1
    assert verdict.measurement.unattributed_requests == 1


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_without_begin_measures_nothing_and_leaves_the_board_untouched(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
) -> None:
    """IF-39: **`begin` を呼ばずに `finish` を呼ぶ**経路（spec US5 の受入シナリオ 7 の片側）。

    受入シナリオ 7 は「`begin` を呼ばずに `finish` を呼ぶ／`begin` を呼んでから `finish` を
    呼ぶ、**それぞれ**実行する」と 2 分岐を課すが、層 4 の `begin`／`finish` の呼び出しは
    **15 箇所すべてが `begin` を先に呼ぶ**（B-31）。上の
    `test_contract_monitor_begin_attributes_the_measurement_to_the_kickoff` が見ているのは
    「`begin` **より前の送信**が `unattributed_requests` に落ちる」ことであって、
    「`begin` が 1 度も無いまま `finish` する」経路ではない。

    観点は 3 つ ——
    (1) **送出しない**。`begin` が無くても `RunVerdict` が返る（`repository.py:489-506` の
        `state is None` 分岐＝ FR-036）。`kickoff_id` は `begin` が無い以上 **`finish` の引数**
        以外に出所が無く、計測は空（`requests` が 0）になる。
    (2) **盤面を 1 度も触らない**。失敗が 1 件も無いので等級が付かず、写像も確認読取も
        起きない（`service.py:142`）。
    (3) **上限判定が働かない**。帰属先が無ければ `on_outbound` は素通しする
        （`repository.py:249-252`）ので、`LimitPolicy`・threshold_T は `begin` を呼ばない限り
        1 度も効かない —— 台帳 IF-38 の出力欄（`以後の Hook 呼び出しをこの kickoff に帰属`）の
        裏側の主張である。通した分は `finish` の `unattributed_requests` に載る。

    宣言が **IF-38 ではなく IF-39** なのは、宣言の真正性の機械検査
    （`test_contract_declarations_actually_reference_the_declared_method`）が「宣言した IF の
    メソッド名がテスト本体に現れること」を要求するためである。本テストが実際に呼ぶのは
    `finish`（IF-39）であって `begin`（IF-38）ではない。
    """
    reader = StubBoardReader(call_log)
    writer = StubBoardWriter(call_log)
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    hook: LLMHttpHook = handle.llm_http_hook

    with httpx.Client() as client:
        for _ in range(UNATTRIBUTED_SENDS):
            # 送信はしない（`on_outbound` は要求を組めば通る）。上限に落ちれば `RunHalted`。
            request = client.build_request(
                "POST",
                "http://127.0.0.1:1/chat/completions",
                json={"model": "demo-model", "messages": [{"role": "user", "content": "問い"}]},
            )
            assert hook.on_outbound(request) is request

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, BENIGN_OUTCOME
    )

    assert isinstance(verdict, RunVerdict)
    assert verdict.grade is None
    assert verdict.measurement.kickoff_id == MONITOR_KICKOFF_ID, "`finish` の引数以外に出所が無い"
    assert verdict.measurement.requests == 0
    assert verdict.measurement.unattributed_requests == UNATTRIBUTED_SENDS
    assert call_log.names() == [], "写像も確認読取も起きない"


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_maps_failure_inside_the_method(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
) -> None:
    """IF-39（F 級）: 写像と確認読取が **`finish` の中で、この順に**起きる（FR-025・SC-004）。

    現物（`_map_failure` → `_verify_mapping`）から確定した並び ——
    `comment` → `set_labels` → `release` → `show` → `comments`。

    並びとラベルだけでは**どの課題へ・誰として・何を書いたか**が抜ける（B-23）——
    別の課題 ID へ書く実装・`actor` を無視して固定値を使う実装・封筒でない本文を書く実装
    でも緑になってしまう。台帳 IF-39 の入力欄は `kickoff_id・issue_id・actor・CrewOutcome`
    を名指しし、記録つきスタブは `issue_id`・`actor`・`body` を kwargs へ積んでいるので、
    5 手すべての宛先・3 手の行為者・封筒の本文まで照合する。
    """
    reader = StubBoardReader(
        call_log,
        issues={
            MONITOR_ISSUE_ID: make_monitor_issue(
                labels=[Labels.state(StateLabel.failing)],
                status=IssueStatus.open,
                assignee=None,
            )
        },
        comments={MONITOR_ISSUE_ID: [make_monitor_envelope_comment(Grade.F)]},
    )
    writer = StubBoardWriter(call_log)
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T)

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, FAILING_OUTCOME
    )

    assert isinstance(verdict, RunVerdict)
    assert verdict.grade is Grade.F
    assert verdict.closed is False
    assert call_log.names() == [
        ("BoardWriter", "comment"),
        ("BoardWriter", "set_labels"),
        ("BoardWriter", "release"),
        ("BoardReader", "show"),
        ("BoardReader", "comments"),
    ]
    comment = call_log.of("BoardWriter", "comment")[0]
    labels = call_log.of("BoardWriter", "set_labels")[0]
    release = call_log.of("BoardWriter", "release")[0]
    show = call_log.of("BoardReader", "show")[0]
    readback = call_log.of("BoardReader", "comments")[0]

    assert labels.kwargs["add"] == [Labels.state(StateLabel.failing)]
    assert labels.kwargs["remove"] == [Labels.state(StateLabel.working)]

    # (a) 写像 3 手と確認読取 2 手が**渡された課題**へ向く
    assert [entry.kwargs["issue_id"] for entry in (comment, labels, release, show, readback)] == [
        MONITOR_ISSUE_ID
    ] * 5
    # (b) 書込 3 手が**渡された行為者**として打たれる
    assert [entry.kwargs["actor"] for entry in (comment, labels, release)] == [MONITOR_ACTOR] * 3
    # (c) 盤面へ書いた本文が**封筒**であり、戻りの `envelope` と同一である
    body = comment.kwargs["body"]
    assert isinstance(body, str)
    assert body == verdict.envelope, "盤面へ書いた本文と戻りの封筒が食い違わない"
    assert body.startswith(f"{monitor_envelope_marker()} grade={Grade.F.value} ")


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_maps_warning_inside_the_method(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
) -> None:
    """IF-39（W 級）: `close` ＋封筒の写像と確認読取が `finish` の中で起きる。

    現物（`_map_warning` → `_verify_mapping`）から確定した並び ——
    `comment` → `close` → `show` → `comments`。**封筒の `comment` が `close` より先**である
    （contracts/recording-stubs.md §5 の「close ＋封筒 comment」は順序を定めていない＝ SD-03）。

    F 級と同じく**宛先・行為者・封筒の本文**まで照合する（B-23）。W 級だけの欄として
    `close` の理由が空でないこと（現物は封筒の 1 行目を渡す）も見る。
    """
    reader = StubBoardReader(
        call_log,
        issues={MONITOR_ISSUE_ID: make_monitor_issue(status=IssueStatus.closed)},
        comments={MONITOR_ISSUE_ID: [make_monitor_envelope_comment(Grade.W)]},
    )
    writer = StubBoardWriter(call_log)
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T)

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, WARNING_OUTCOME
    )

    assert verdict.grade is Grade.W
    assert verdict.closed is True
    assert call_log.names() == [
        ("BoardWriter", "comment"),
        ("BoardWriter", "close"),
        ("BoardReader", "show"),
        ("BoardReader", "comments"),
    ]

    comment = call_log.of("BoardWriter", "comment")[0]
    closed = call_log.of("BoardWriter", "close")[0]
    show = call_log.of("BoardReader", "show")[0]
    readback = call_log.of("BoardReader", "comments")[0]

    # (a) 写像 2 手と確認読取 2 手が**渡された課題**へ向く
    assert [entry.kwargs["issue_id"] for entry in (comment, closed, show, readback)] == [
        MONITOR_ISSUE_ID
    ] * 4
    # (b) 書込 2 手が**渡された行為者**として打たれる
    assert [entry.kwargs["actor"] for entry in (comment, closed)] == [MONITOR_ACTOR] * 2
    # (c) 盤面へ書いた本文が**封筒**であり、戻りの `envelope` と同一である
    body = comment.kwargs["body"]
    assert isinstance(body, str)
    assert body == verdict.envelope, "盤面へ書いた本文と戻りの封筒が食い違わない"
    assert body.startswith(f"{monitor_envelope_marker()} grade={Grade.W.value} ")
    # (d) close の理由が空でない（現物は封筒の 1 行目を渡す）
    reason = closed.kwargs["reason"]
    assert isinstance(reason, str)
    assert reason.startswith(monitor_envelope_marker())


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_returns_grade_i_with_halted_and_envelope(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> None:
    """IF-39（I 級）: `RunVerdict` の **I 級・`halted`・`envelope`** を踏む（FR-016）。

    台帳の出力欄は `RunVerdict（grade F/W/I・closed・halted・envelope・Measurement DM-20）`
    と 5 項目を名指しするが、上の 2 本が踏むのは F 級・W 級・`closed`・`measurement` だけで、
    **I 級は一度も作られず** `envelope`・`halted` への assert は 0 件である。

    I 級は「記録するが盤面を動かさない」等級である（`config.json` の `grade_map` が
    `retry_succeeded`・`usage_missing`・`summarization` を `I` に写す）。ここでは応答から
    `usage.cost` を落として `usage_missing` を立てる。**I 級は写像も確認読取も行わない**
    （`service.py:141` の注記）ので、F/W との差は `call_log` が**空のまま**であることに出る。

    `halted` は等級とは独立の欄で、`measurement.halted_reason` か `outcome.halted` の
    どちらかが立てば真になる。ここは後者だけを立てて（`HALTED_OUTCOME`）、等級を I に
    保ったまま `halted` が真になることを見る。

    `envelope` の印は**直書きせず** `config.json` の実値（`monitor_envelope_marker()`）から
    取る。等級・種類・`kickoff_id` が載ることまで主張する。
    """
    server = fake_llm_server(make_llm_script(cost=None))  # `usage.cost` 欠落 → `usage_missing`
    reader = StubBoardReader(call_log)
    writer = StubBoardWriter(call_log)
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    hook: LLMHttpHook = handle.llm_http_hook
    run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T)

    with httpx.Client() as client:
        request = client.build_request(
            "POST",
            f"{server.base_url}/chat/completions",
            json={"model": "demo-model", "messages": [{"role": "user", "content": "問い"}]},
        )
        hook.on_inbound(client.send(hook.on_outbound(request)))

    verdict = run_monitor.finish(
        MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, HALTED_OUTCOME
    )

    assert verdict.grade is Grade.I
    assert verdict.kinds == [FailureKind.usage_missing]
    assert verdict.closed is False, "I 級は close しない"
    assert call_log.names() == [], "I 級は写像も確認読取も行わない（F/W との差はここに出る）"

    assert verdict.halted is True

    marker = monitor_envelope_marker()
    assert verdict.envelope.startswith(f"{marker} grade={Grade.I.value} ")
    assert f"kinds={FailureKind.usage_missing.value}" in verdict.envelope
    assert f"kickoff_id={MONITOR_KICKOFF_ID}" in verdict.envelope
    assert verdict.measurement.cost_missing_count == 1


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_raises_mapping_unverified(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
) -> None:
    """IF-39 例外系: 写像が読めなければ `MappingUnverified`（R-11）。

    `StubBoardWriter` は書込を記録して**成功したふりをする**が、`StubBoardReader` は
    **写像前の姿**（封筒コメントを返さず `state:failing` も付いていない）を返す。
    **外部ツールの終了コードを成功の証拠にしない**という設計が、この組み合わせで反例になる。
    """
    reader = StubBoardReader(
        call_log,
        issues={
            MONITOR_ISSUE_ID: make_monitor_issue(
                labels=[Labels.state(StateLabel.working)],
                status=IssueStatus.in_progress,
                assignee=MONITOR_ACTOR,
            )
        },
        comments={MONITOR_ISSUE_ID: []},  # 封筒が読めない ＝ 写像前の姿
    )
    writer = StubBoardWriter(call_log)
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T)

    with pytest.raises(MappingUnverified) as caught:
        run_monitor.finish(MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, FAILING_OUTCOME)

    assert caught.value.issue_id == MONITOR_ISSUE_ID
    assert caught.value.expected
    assert caught.value.actual
    assert ("BoardWriter", "comment") in call_log.names(), "書込は成功したふりをしている"


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_raises_mapping_unverified_when_the_mapping_write_cannot_reach_bd(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
) -> None:
    """IF-39 例外系: **写像側が `bd` に届かない**ときも `MappingUnverified`（FR-017）。

    台帳の例外欄は `MappingUnverified（写像が読めない／**bd 不達**→停止痕跡を残して停止 P3）`
    と 2 条件を名指しする。上のテストが踏むのは前者（写像が読めない＝不一致）だけで、`bd`
    不達は現物の**別分岐**（`service.py:318` の `_run_steps`）にある。

    ここでは `StubBoardWriter` の `raises=` 口に `BoardUnavailable` を渡し、F 級写像の
    1 手目（`comment`）で不達を起こす。観点は 2 つ ——
    (1) 不達を**その場で送出せず**以後の手を打ち切る（`set_labels`・`release` を打たない）
    (2) 打ち切ったあとも**確認読取は試み**、部分的にしか書けていないことを成功に倒さず
        `MappingUnverified` にする。
    """
    reader = StubBoardReader(
        call_log,
        issues={
            MONITOR_ISSUE_ID: make_monitor_issue(
                labels=[Labels.state(StateLabel.working)],
                status=IssueStatus.in_progress,
                assignee=MONITOR_ACTOR,
            )
        },
        comments={MONITOR_ISSUE_ID: []},
    )
    writer = StubBoardWriter(call_log, raises=_bd_unreachable())
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T)

    with pytest.raises(MappingUnverified) as caught:
        run_monitor.finish(MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, FAILING_OUTCOME)

    assert caught.value.issue_id == MONITOR_ISSUE_ID
    assert call_log.names() == [
        ("BoardWriter", "comment"),  # (1) 1 手目で不達 → 以後を打ち切る
        ("BoardReader", "show"),  # (2) 打ち切っても確認読取は試みる
        ("BoardReader", "comments"),
    ]


@pytest.mark.contract_if("IF-39")
def test_contract_monitor_finish_raises_mapping_unverified_when_the_readback_cannot_reach_bd(
    call_log: CallLog,
    monitor_factory: Callable[[StubBoardReader, StubBoardWriter], MonitorHandle],
) -> None:
    """IF-39 例外系: **確認読取が `bd` に届かない**ときも `MappingUnverified`（FR-017）。

    台帳が名指しする `bd` 不達のもう一方の分岐（`service.py:345` の `_verify_mapping`）。
    書込は成功したふりをするが、読み直しそのものが不達になる。**読めなかったことを
    「一致した」に倒さない**ことが観点で、受けた例外の名前が `actual` に載る。
    """
    reader = StubBoardReader(call_log, raises=_bd_unreachable())
    writer = StubBoardWriter(call_log)
    handle = monitor_factory(reader, writer)
    run_monitor: RunMonitor = handle.run_monitor
    run_monitor.begin(MONITOR_KICKOFF_ID, LIMITS, MONITOR_THRESHOLD_T)

    with pytest.raises(MappingUnverified) as caught:
        run_monitor.finish(MONITOR_KICKOFF_ID, MONITOR_ISSUE_ID, MONITOR_ACTOR, FAILING_OUTCOME)

    assert caught.value.issue_id == MONITOR_ISSUE_ID
    assert caught.value.actual == f"readback: {BoardUnavailable.__name__}"
    assert isinstance(caught.value.cause, BoardUnavailable), "受けた不達を原因として残す"
    assert call_log.names() == [
        ("BoardWriter", "comment"),  # 写像側は 3 手とも成功したふりをする
        ("BoardWriter", "set_labels"),
        ("BoardWriter", "release"),
        ("BoardReader", "show"),  # 読み直しで不達
    ]
