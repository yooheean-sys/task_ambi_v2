"""人間へ渡す 1 経路（F12・FR-047〜FR-050・SC-011・SC-028）。

正本は `specs/009-orchestrator/contracts/result-mapping.md` §3・§6 の #5／#6。
"""

from __future__ import annotations

import logging

import pytest
from conftest import Stubs, failing_label, make_issue, make_ports, working_label

from common.kernel.enums import Labels, StateLabel
from orchestrator.models import HandOff, HandOffReason, IssueDisposition
from orchestrator.service import handoff

MARKER = "[orchestrator]"

FIVE_REASONS: list[tuple[HandOffReason, str, tuple[str, ...]]] = [
    (HandOffReason.guard_children, "既に子がいるので分解も再投入もしない", ("子: bd-2, bd-3",)),
    (
        HandOffReason.returns_exceeded,
        "差し戻しが上限に達した",
        ("件数: 2", "上限: 2", "最後の指摘: 空"),
    ),
    (
        HandOffReason.unsafe,
        "分解の途中で盤面への書込が失敗した。自動では再試行しない",
        ("作成済みの子: bd-121", "張れた依存: なし", "付け替え: 未実行"),
    ),
    (HandOffReason.folder_missing, "プロジェクトのフォルダが実在しない", ("pj/alpha/input",)),
    (
        HandOffReason.config_missing,
        "段・主体・プロジェクト・レビュー担当が決まらない",
        ("UnknownStage: kind から段を決められない",),
    ),
]


# --------------------------------------------------------------------------------------
# 本文の形（HandOff.render）
# --------------------------------------------------------------------------------------


def test_render_puts_the_marker_and_reason_on_the_first_line() -> None:
    body = HandOff(
        reason=HandOffReason.unsafe,
        summary="分解の途中で失敗した",
        details=("作成済みの子: bd-121", "付け替え: 未実行"),
    ).render(MARKER)

    assert body.splitlines() == [
        "[orchestrator] 停止[unsafe]: 分解の途中で失敗した",
        "- 作成済みの子: bd-121",
        "- 付け替え: 未実行",
    ]


def test_render_without_details_is_a_single_line() -> None:
    body = HandOff(reason=HandOffReason.guard_children, summary="子がいる", details=()).render(
        MARKER
    )
    assert body == "[orchestrator] 停止[guard_children]: 子がいる"


# --------------------------------------------------------------------------------------
# 3 手の順・回数・actor（SC-011・SC-028）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "summary", "details"), FIVE_REASONS, ids=[r[0].value for r in FIVE_REASONS]
)
def test_three_steps_in_order_once_each(
    stubs: Stubs, reason: HandOffReason, summary: str, details: tuple[str, ...]
) -> None:
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    ports = make_ports(stubs)

    handoff.hand_off(ports, "bd-1", HandOff(reason=reason, summary=summary, details=details))

    assert stubs.log.targets() == [
        "BoardWriter.comment",
        "BoardWriter.set_labels",
        "BoardWriter.release",
    ]


@pytest.mark.parametrize(
    ("reason", "summary", "details"), FIVE_REASONS, ids=[r[0].value for r in FIVE_REASONS]
)
def test_body_head_and_details_are_carried(
    stubs: Stubs, reason: HandOffReason, summary: str, details: tuple[str, ...]
) -> None:
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    ports = make_ports(stubs)

    handoff.hand_off(ports, "bd-1", HandOff(reason=reason, summary=summary, details=details))

    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    lines = body.splitlines()
    assert lines[0] == f"{MARKER} 停止[{reason.value}]: {summary}"
    assert lines[1:] == [f"- {d}" for d in details]


def test_labels_swap_working_for_failing(stubs: Stubs) -> None:
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    ports = make_ports(stubs)

    handoff.hand_off(
        ports, "bd-1", HandOff(reason=HandOffReason.unsafe, summary="途中失敗", details=())
    )

    call = stubs.log.of("BoardWriter.set_labels")[0]
    assert call.kwargs["add"] == [Labels.state(StateLabel.failing)]
    assert call.kwargs["remove"] == [Labels.state(StateLabel.working)]
    assert stubs.state.issues["bd-1"].labels == [failing_label()]


def test_every_step_uses_the_system_actor(stubs: Stubs) -> None:
    """3 手はすべて**自分の行為**なのでシステム actor（SC-028・P5D-037）。"""
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    ports = make_ports(stubs, system_actor="sysbot")

    handoff.hand_off(
        ports, "bd-1", HandOff(reason=HandOffReason.folder_missing, summary="不在", details=())
    )

    assert [call.kwargs["actor"] for call in stubs.log.calls] == ["sysbot", "sysbot", "sysbot"]


# --------------------------------------------------------------------------------------
# 結果の分岐（contracts §1 の行 4・行 5・行 7）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (HandOffReason.guard_children, IssueDisposition.handed_off),
        (HandOffReason.returns_exceeded, IssueDisposition.handed_off),
        (HandOffReason.folder_missing, IssueDisposition.handed_off),
        (HandOffReason.config_missing, IssueDisposition.handed_off),
        (HandOffReason.unsafe, IssueDisposition.handed_off_unsafe),
    ],
)
def test_disposition_by_reason(
    stubs: Stubs, reason: HandOffReason, expected: IssueDisposition
) -> None:
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    ports = make_ports(stubs)

    assert (
        handoff.hand_off(ports, "bd-1", HandOff(reason=reason, summary="要約", details=()))
        is expected
    )


def test_comment_unreachable_leaves_a_stop_trace_and_writes_nothing(
    stubs: Stubs, caplog: pytest.LogCaptureFixture
) -> None:
    """①が `BoardUnavailable` なら盤面に痕跡を残せない → ログだけ残して `halted`（FR-049）。"""
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    stubs.board_writer.failures.fail("BoardWriter.comment", 1)
    ports = make_ports(stubs)

    with caplog.at_level(logging.ERROR):
        result = handoff.hand_off(
            ports, "bd-1", HandOff(reason=HandOffReason.unsafe, summary="途中失敗", details=())
        )

    assert result is IssueDisposition.halted
    assert stubs.log.count("BoardWriter.set_labels") == 0
    assert stubs.log.count("BoardWriter.release") == 0
    assert [record.getMessage() for record in caplog.records] == [
        "orchestrator.handoff.board_unavailable"
    ]
    assert getattr(caplog.records[0], "issue_id", None) == "bd-1"


@pytest.mark.parametrize("target", ["BoardWriter.set_labels", "BoardWriter.release"])
def test_later_steps_unreachable_also_halt(stubs: Stubs, target: str) -> None:
    """②③ が落ちた場合も同じ扱い（コメントは既に残っている）。"""
    stubs.state.add(make_issue("bd-1", labels=[working_label()]))
    stubs.board_writer.failures.fail(target, 1)
    ports = make_ports(stubs)

    assert (
        handoff.hand_off(
            ports, "bd-1", HandOff(reason=HandOffReason.unsafe, summary="途中失敗", details=())
        )
        is IssueDisposition.halted
    )


# --------------------------------------------------------------------------------------
# 5 区分を組み立てるヘルパ（詳細欄の出所を 1 箇所に閉じる）
# --------------------------------------------------------------------------------------


def test_builders_fill_the_details_of_each_reason() -> None:
    assert handoff.guard_children(["bd-2", "bd-3"]).details == ("既存の子: bd-2, bd-3",)
    assert handoff.returns_exceeded(2, 2, "受入条文が空").details == (
        "差し戻しの件数: 2",
        "上限: 2",
        "最後の指摘: 受入条文が空",
    )
    assert handoff.unsafe(("作成済みの子: bd-2", "失敗した処理: create")).details == (
        "作成済みの子: bd-2",
        "失敗した処理: create",
    )
    assert handoff.folder_missing(["a", "b"]).details == ("不在のパス: a", "不在のパス: b")
    assert handoff.config_missing("UnknownStage", "段を決められない").details == (
        "UnknownStage: 段を決められない",
    )
    assert handoff.unsafe(()).reason is HandOffReason.unsafe
