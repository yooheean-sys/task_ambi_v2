"""周回の骨格・single-flight・確保・記録（F01・F02・F04・F14）。

SC-001〜SC-006a・SC-010・SC-045・FR-021・FR-022a／b。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import NamedTuple

import pytest
from conftest import (
    Stubs,
    failing_label,
    make_config,
    make_ctx,
    make_escalation,
    make_issue,
    make_outcome,
    make_ports,
    make_review,
    make_stubs,
    persona_label,
    project_label,
    working_label,
)

from common.interfaces.board import (
    BoardUnavailable,
    BoardWriteUnverified,
    ClaimFilter,
    IssueNotFound,
)
from common.interfaces.executor import ExecutorFailure
from common.interfaces.monitor import MappingUnverified
from common.interfaces.orchestrator import KickoffBusy, KickoffStatus
from common.interfaces.settings import MemberNotFound, ProjectNotFound
from common.interfaces.stages import UnknownPersona, UnknownStage
from common.interfaces.vault import VaultNotFound, VaultPathViolation
from orchestrator.models import KickoffRecord, RecordStore, SingleFlight
from orchestrator.service import OrchestratorService

WRITES = (
    "BoardWriter.create",
    "BoardWriter.add_dependency",
    "BoardWriter.move_dependents",
    "BoardWriter.release",
    "BoardWriter.comment",
    "BoardWriter.close",
    "BoardWriter.set_labels",
)


def _service(stubs: Stubs, **overrides: object) -> OrchestratorService:
    config = make_config(**overrides)
    return OrchestratorService(
        ports=make_ports(stubs, **overrides),
        flight=SingleFlight(),
        records=RecordStore(config.recent_results_n),
    )


def _offer(stubs: Stubs, count: int) -> None:
    """確保で拾える課題を `count` 件積み、すぐ閉じられる台本にする。"""
    for index in range(count):
        stubs.state.offer(make_issue(f"bd-{index + 1}", labels=[project_label(), persona_label()]))
    stubs.proposal_gate.tokens[""] = []
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())


def _offer_closing(stubs: Stubs) -> None:
    """1 件をレビュー承認で閉じる台本（`CONTRACT_ERRORS` の既定）。"""
    _offer(stubs, 1)


def _offer_escalating(stubs: Stubs) -> None:
    """1 件を**判断を仰ぐ**台本（`BoardWriter.create` を必ず 1 回通す＝ SC-003）。"""
    stubs.state.offer(make_issue("bd-1", labels=[project_label(), persona_label()]))
    stubs.proposal_gate.tokens[""] = []
    stubs.crew_executor.default_outcome = make_outcome(escalation=make_escalation())


GATED_ACCEPTANCE = "outcome/report.md を出す"
"""完了ゲートを通す台本の受入条文（`tokens` の鍵は条文の全文＝ FR-061）。"""


def _offer_gated(stubs: Stubs) -> None:
    """1 件を**完了ゲート越しに**閉じる台本（`VaultReader.outcome_exists` を必ず 1 回通す）。

    受入条文に**正準形のトークンを 1 件**持たせる —— 既定の台本（`_offer_closing`）は
    トークンが 0 件なので実在確認を 1 回も呼ばず、完了ゲートに注入した例外が飛ばないまま
    緑になる（T079 と同じ空振り）。1 回目で例外を起こしたあとも周回が続けられるよう、
    成果物は**実在する側**に置く（差し戻し 1 回 → 再実行 → ゲート通過 → レビュー承認）。
    """
    stubs.state.offer(
        make_issue(
            "bd-1",
            labels=[project_label(), persona_label()],
            acceptance=GATED_ACCEPTANCE,
        )
    )
    stubs.proposal_gate.tokens[GATED_ACCEPTANCE] = ["outcome/report.md"]
    stubs.vault_reader.existing = {"report.md"}
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())


# --------------------------------------------------------------------------------------
# 確保の周回（SC-001・SC-002）
# --------------------------------------------------------------------------------------


def test_no_claimable_issue_is_a_noop(stubs: Stubs) -> None:
    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.claim_next") == 1
    assert [name for name in stubs.log.targets() if name in WRITES] == []
    assert result.status is KickoffStatus.noop
    assert result.issues == []


def test_three_issues_are_all_processed(stubs: Stubs) -> None:
    _offer(stubs, 3)

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.claim_next") == 4  # 件数 ＋ 1
    assert [summary.issue_id for summary in result.issues] == ["bd-1", "bd-2", "bd-3"]
    assert result.status is KickoffStatus.ok
    assert result.finished_at >= result.started_at


def test_claim_uses_the_context_filter_and_system_actor(stubs: Stubs) -> None:
    ctx = make_ctx(claim_filter=ClaimFilter(labels=frozenset({"role:executor"}), project_key="p"))

    _service(stubs, system_actor="sysbot").run(ctx)

    call = stubs.log.of("BoardWriter.claim_next")[0]
    assert call.kwargs["claim_filter"] is ctx.claim_filter
    assert call.kwargs["actor"] == "sysbot"
    # 拾い直しも**同じ**確保条件で列挙する（FR-013。無関係な条件に差し替えても他は緑になる）
    assert stubs.log.of("BoardReader.list_working")[0].kwargs["claim_filter"] is ctx.claim_filter


# --------------------------------------------------------------------------------------
# 失敗の印つき確保の即時解放（FR-021・SC-010・SC-045）
# --------------------------------------------------------------------------------------


def test_failing_issue_is_released_without_touching_anything(stubs: Stubs) -> None:
    stubs.state.offer(make_issue("bd-1", labels=[project_label(), failing_label()]))

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.release") == 1
    for name in (
        "StageCatalog.resolve_stage",
        "SettingsReader.get_project",
        "CrewExecutor.run",
        "BoardWriter.comment",
        "BoardWriter.set_labels",
    ):
        assert stubs.log.count(name) == 0
    assert result.issues == []
    assert any("skipped bd-1" in note for note in result.notes)


def test_a_single_failing_issue_ends_the_cycle_in_two_claims(stubs: Stubs) -> None:
    """再確保しても周回が終わる（SC-045・FR-022a）。"""
    stubs.board_writer.reclaim_on_release = True
    stubs.state.offer(make_issue("bd-1", labels=[project_label(), failing_label()]))

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.claim_next") <= 2
    assert result.status is KickoffStatus.noop


def test_recovered_issues_are_not_treated_as_released(stubs: Stubs) -> None:
    """回収で解放した課題は「手放した課題」に入れない（FR-022b・DIFF-19）。"""
    orphan = make_issue("bd-1", labels=[project_label(), persona_label(), working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    stubs.state.claimable.append(orphan)
    stubs.proposal_gate.tokens[""] = []
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())

    result = _service(stubs).run(make_ctx())

    assert [summary.issue_id for summary in result.issues] == ["bd-1"]
    assert result.status is KickoffStatus.ok


# --------------------------------------------------------------------------------------
# 周回レベルの打ち切り（SC-009・SC-014・SC-030）
# --------------------------------------------------------------------------------------


def test_an_unreachable_board_stops_before_claiming(stubs: Stubs) -> None:
    """拾い直しが盤面に届かないとき、**確保へ進まない**（SC-009・FR-017）。

    `recover` 単体の `RecoverReport.stopped` だけでは、`loop` 側の早期 `return` を落としても
    緑のままになる（変異実測）。ここは kickoff の面で「`claim_next` 0 回」を固定する。
    """
    _offer(stubs, 1)
    stubs.board_reader.failures.fail("BoardReader.list_working", 1)

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.claim_next") == 0
    assert result.status is KickoffStatus.stopped
    assert result.issues == []


def test_unreadable_comments_stop_the_cycle_without_running_the_executor(stubs: Stubs) -> None:
    """コメントが読めないとき**実行器を呼ばず**に周回を打ち切る（SC-014・FR-031）。

    `prepare` は元々実行器を呼ばないので、`prepare` 単体で `CrewExecutor.run == 0` を見ても
    恒真である。周回の面で「実行器 0 回・打ち切り・以降の確保なし」を固定する。
    """
    _offer(stubs, 2)
    stubs.board_reader.failures.fail("BoardReader.comments", 1)

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("CrewExecutor.run") == 0
    assert stubs.log.count("BoardWriter.claim_next") == 1
    assert result.status is KickoffStatus.stopped


def test_mapping_unverified_stops_the_cycle_and_reports_halted(stubs: Stubs) -> None:
    """写像不能は**確保を止め**、その課題を `halted` の 1 行で残す（SC-030・FR-070）。"""
    _offer(stubs, 2)
    stubs.run_monitor.failures.fail(
        "RunMonitor.finish",
        1,
        MappingUnverified("読み戻せない", issue_id="bd-1", expected="closed", actual="open"),
    )

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.claim_next") == 1
    assert result.status is KickoffStatus.stopped
    assert [(summary.issue_id, summary.result) for summary in result.issues] == [("bd-1", "halted")]
    assert [name for name in stubs.log.targets() if name in WRITES] == []


# --------------------------------------------------------------------------------------
# 「手放した課題」の 2 経路（FR-022b・FR-047 由来と FR-021 由来）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "target", "error", "listed"),
    [
        ("stage_catalog", "StageCatalog.resolve_stage", UnknownStage("段が無い"), False),
        (
            "stage_catalog",
            "StageCatalog.build_prompt",
            UnknownPersona("ペルソナが無い"),
            True,
        ),
    ],
    ids=["前処理で人間へ渡した", "段別処理で人間へ渡した"],
)
def test_issues_handed_to_a_human_count_as_released(
    stubs: Stubs, owner: str, target: str, error: BaseException, listed: bool
) -> None:
    """人間へ渡した課題を再確保したら**解放して周回を終える**（FR-022b・FR-022a）。

    押さえるのは FR-047 由来の 2 経路（前処理の失敗・段別処理の `handed_off`）である。
    FR-021 由来（失敗の印つき確保）は `test_a_single_failing_issue_ends_the_cycle_in_two_claims`
    が別に押さえている。
    """
    _offer(stubs, 1)
    stubs.board_writer.reclaim_on_release = True  # 解放したら次の確保で拾い直せる（実盤面と同じ）
    getattr(stubs, owner).failures.fail(target, 1, error)

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count("BoardWriter.claim_next") == 2
    assert any("再確保" in note for note in result.notes)
    assert len(result.issues) == (1 if listed else 0)


# --------------------------------------------------------------------------------------
# single-flight（FR-005〜FR-008・FR-012a・SC-005・SC-006a）
# --------------------------------------------------------------------------------------


def test_second_run_is_refused_without_waiting(stubs: Stubs) -> None:
    entered = threading.Event()
    resume = threading.Event()

    def gate() -> None:
        entered.set()
        resume.wait(5)

    stubs.log.hooks["BoardReader.list_working"] = gate
    service = _service(stubs)
    worker = threading.Thread(target=lambda: service.run(make_ctx()))
    worker.start()
    entered.wait(5)

    try:
        with pytest.raises(KickoffBusy) as caught:
            service.run(make_ctx())
    finally:
        resume.set()
        worker.join(5)

    assert caught.value.current_kickoff_id
    assert len(caught.value.current_kickoff_id) == 32


def test_refused_runs_leave_no_record(stubs: Stubs) -> None:
    """断られた起動は識別子を発番せず記録も残さない（FR-012a・SC-006a）。"""
    entered = threading.Event()
    resume = threading.Event()

    def gate() -> None:
        entered.set()
        resume.wait(5)

    stubs.log.hooks["BoardReader.list_working"] = gate
    service = _service(stubs)
    worker = threading.Thread(target=lambda: service.run(make_ctx()))
    worker.start()
    entered.wait(5)

    try:
        for _ in range(5):
            with pytest.raises(KickoffBusy):
                service.run(make_ctx())
        running = service.current_record()
        assert running is not None
    finally:
        resume.set()
        worker.join(5)

    assert service.record(running.kickoff_id) is not None
    assert service.current_record() is None


def test_the_flight_mark_is_released_even_on_failure(stubs: Stubs) -> None:
    """周回が例外で終わっても印は 100% 解放される（FR-008・SC-005）。"""

    def boom() -> None:
        raise RuntimeError("想定外")

    stubs.log.hooks["BoardReader.list_working"] = boom
    service = _service(stubs)

    with pytest.raises(RuntimeError):
        service.run(make_ctx())

    del stubs.log.hooks["BoardReader.list_working"]
    assert service.run(make_ctx()).status is KickoffStatus.noop


def test_kickoff_id_is_a_32_digit_hex(stubs: Stubs) -> None:
    result = _service(stubs).run(make_ctx())

    assert len(result.kickoff_id) == 32
    int(result.kickoff_id, 16)


# --------------------------------------------------------------------------------------
# 記録の保持（F14・FR-010〜FR-012・SC-006）
# --------------------------------------------------------------------------------------


def test_record_store_keeps_at_most_the_capacity(stubs: Stubs) -> None:
    service = _service(stubs, recent_results_n=3)

    ids = [service.run(make_ctx()).kickoff_id for _ in range(3 + 5)]

    kept = [kickoff_id for kickoff_id in ids if service.record(kickoff_id) is not None]
    assert len(kept) == 3
    assert kept == ids[-3:]


def test_record_moves_from_running_to_done(stubs: Stubs) -> None:
    service = _service(stubs)

    result = service.run(make_ctx())

    record = service.record(result.kickoff_id)
    assert record is not None
    assert record.status == "done"
    assert record.result is not None
    assert record.result.kickoff_id == result.kickoff_id
    assert service.current_record() is None


def test_complete_on_an_unknown_id_is_silent() -> None:
    store = RecordStore(2)

    store.complete("missing", None)  # type: ignore[arg-type]  # 見つからなければ触らないことだけを見る

    assert store.get("missing") is None


def test_current_returns_the_running_record(stubs: Stubs) -> None:
    ctx = make_ctx()
    store = RecordStore(2)
    store.open(KickoffRecord(kickoff_id="a", ctx=ctx, status="running", result=None))

    current = store.current()

    assert current is not None
    assert current.kickoff_id == "a"


def test_single_flight_mints_only_when_acquired() -> None:
    flight = SingleFlight()
    minted: list[str] = []

    def mint() -> str:
        minted.append(f"k-{len(minted)}")
        return minted[-1]

    first = flight.start(mint)
    second = flight.start(mint)

    assert first.acquired is True
    assert second.acquired is False
    assert second.kickoff_id == first.kickoff_id
    assert minted == ["k-0"]  # 断られた側は発番しない
    flight.end()
    assert flight.current() is None


# --------------------------------------------------------------------------------------
# 呼び手へ漏れる例外は KickoffBusy の 1 種だけ（SC-003・FR-003）
# --------------------------------------------------------------------------------------


class ContractCase(NamedTuple):
    """契約例外 1 種を「**実際に発火する台本**」つきで表す 1 行。"""

    owner: str
    """スタブの属性名（`stubs.<owner>.failures` に台本を積む）。"""

    target: str
    """`"<契約>.<手>"` の形の注入先。"""

    error: BaseException
    """1 回目の呼び出しで起こす契約例外。"""

    offer: Callable[[Stubs], None]
    """盤面と実行器の台本。**注入先を必ず 1 回は通すもの**を選ぶ。"""

    status: KickoffStatus
    """その例外を起こしたときに期待する周回の状態（行ごとに違う＝判別力を持たせる）。"""


CONTRACT_ERRORS: list[ContractCase] = [
    ContractCase(
        "board_reader",
        "BoardReader.list_working",
        BoardUnavailable("届かない", argv=["bd"], rc=1, stderr_head="x", attempts=1),
        _offer_closing,
        KickoffStatus.stopped,  # 拾い直しを省いて先へ進まない（FR-017）
    ),
    ContractCase(
        "board_reader",
        "BoardReader.show",
        IssueNotFound("無い", issue_id="bd-1"),
        _offer_closing,
        KickoffStatus.stopped,  # 前処理で盤面に届かず打ち切る（FR-024）
    ),
    ContractCase(
        "board_writer",
        "BoardWriter.create",
        BoardWriteUnverified("読み直せない", issue_id="bd-1", mismatch="x"),
        _offer_escalating,  # 既定の台本では `create` を 1 回も通らない（T079）
        KickoffStatus.ok,  # 人間へ渡して次の課題へ進む（FR-046・T078）
    ),
    ContractCase(
        "run_monitor",
        "RunMonitor.finish",
        MappingUnverified("読み戻せない", issue_id="bd-1", expected="closed", actual="open"),
        _offer_closing,
        KickoffStatus.stopped,  # 写像を確かめられない（FR-070・SC-030）
    ),
    ContractCase(
        "stage_catalog",
        "StageCatalog.resolve_stage",
        UnknownStage("段が無い"),
        _offer_closing,
        KickoffStatus.noop,  # 段が未解決なので一覧に載らない（FR-071a）
    ),
    ContractCase(
        "stage_catalog",
        "StageCatalog.build_prompt",
        UnknownPersona("ペルソナが無い"),
        _offer_closing,
        KickoffStatus.ok,  # 人間へ渡して次の課題へ進む（DIFF-12）
    ),
    ContractCase(
        "member_resolver",
        "MemberResolver.resolve",
        MemberNotFound("メンバが無い"),
        _offer_closing,
        KickoffStatus.ok,
    ),
    ContractCase(
        "settings_reader",
        "SettingsReader.get_project",
        ProjectNotFound("PJ が無い"),
        _offer_closing,
        KickoffStatus.ok,
    ),
    ContractCase(
        "vault_reader",
        "VaultReader.check_project_folders",
        VaultPathViolation("規則違反", kind="outside_root", detail="/etc"),
        _offer_closing,
        KickoffStatus.ok,
    ),
    ContractCase(
        "vault_reader",
        "VaultReader.check_project_folders",
        VaultNotFound("フォルダが無い", rel_path="pj/alpha", role=None),
        _offer_closing,
        KickoffStatus.ok,
    ),
    ContractCase(
        # orchestrator が保管庫に呼ぶ手は 2 つ（`check_project_folders`・`outcome_exists`）。
        # 前処理だけに注入すると完了ゲートの受けが**保険ごと抜ける**（T107 の実測）。
        "vault_reader",
        "VaultReader.outcome_exists",
        VaultNotFound("成果物が無い", rel_path="pj/alpha/outcome/report.md", role=None),
        _offer_gated,
        KickoffStatus.ok,  # 差し戻して再実行し、閉じて次へ進む（FR-052）
    ),
    ContractCase(
        "crew_executor",
        "CrewExecutor.run",
        ExecutorFailure("実行に失敗", phase="kickoff", reason="boom"),
        _offer_closing,
        KickoffStatus.ok,  # 締めに渡して次の課題へ進む（FR-039）
    ),
]


@pytest.mark.parametrize(
    "case",
    CONTRACT_ERRORS,
    ids=[f"{case.error.__class__.__name__}@{case.target}" for case in CONTRACT_ERRORS],
)
def test_no_contract_error_escapes_the_runner(case: ContractCase) -> None:
    """利用先の契約例外 **11 種**を 1 つずつ起こしても呼び手へ 1 件も漏れない。

    行は **12 行**（種は 11）—— `VaultNotFound` だけ 2 行あり、orchestrator が保管庫に呼ぶ
    **2 つの手**（`check_project_folders`＝前処理・`outcome_exists`＝完了ゲート）に別々に
    注入する。片方だけだと保険が半分しか張れず、完了ゲートの漏れを見逃す（T107 の実測）。

    **注入先を実際に通ったこと**を併せて固定する —— 通らない台本だと例外が飛ばないまま
    緑になり、漏れの検出が空振りする（T079 の実測: `BoardWriter.create` の行が呼出 0 回）。
    """
    stubs = make_stubs()
    case.offer(stubs)
    getattr(stubs, case.owner).failures.fail(case.target, 1, case.error)

    result = _service(stubs).run(make_ctx())

    assert stubs.log.count(case.target) >= 1
    assert result.status is case.status
