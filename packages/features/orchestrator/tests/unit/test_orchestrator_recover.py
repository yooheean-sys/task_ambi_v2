"""起動時の拾い直しの 3 分岐（F03・FR-013〜FR-018・SC-007〜SC-009）。"""

from __future__ import annotations

import logging

import pytest
from conftest import Stubs, make_issue, make_ports, make_unavailable, working_label

from common.interfaces.board import ClaimFilter, IssueNotFound
from orchestrator.models import count_return_comments
from orchestrator.service import recover

MARKER = "[orchestrator]"
SYSTEM = "system"
WRITES = (
    "BoardWriter.comment",
    "BoardWriter.set_labels",
    "BoardWriter.release",
    "BoardWriter.create",
    "BoardWriter.close",
    "BoardWriter.add_dependency",
    "BoardWriter.move_dependents",
)


def _filter() -> ClaimFilter:
    return ClaimFilter(project_key=None)


def _writes(stubs: Stubs) -> list[str]:
    return [name for name in stubs.log.targets() if name in WRITES]


# --------------------------------------------------------------------------------------
# 3 分岐（SC-007: 書込回数 0・3・2）
# --------------------------------------------------------------------------------------


def test_open_question_branch_writes_nothing(stubs: Stubs) -> None:
    """未回答の質問があれば**その課題へ 1 回も書き込まない**（FR-014・SC-008）。"""
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    stubs.state.questions["bd-1"] = make_issue("bd-9", labels=["kind:question"])
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    assert _writes(stubs) == []
    assert stubs.log.count("BoardReader.children_of") == 0
    assert report.stopped is False
    assert report.notes == ()


def test_children_branch_hands_off_with_three_writes_and_no_create(stubs: Stubs) -> None:
    """子がいれば人間へ渡す 3 手・**起票 0 回**（FR-015・SC-008）。"""
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    stubs.state.children["bd-1"] = [make_issue("bd-2"), make_issue("bd-3")]
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    assert _writes(stubs) == [
        "BoardWriter.comment",
        "BoardWriter.set_labels",
        "BoardWriter.release",
    ]
    assert stubs.log.count("BoardWriter.create") == 0
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert body.startswith(f"{MARKER} 停止[guard_children]")
    assert "bd-2, bd-3" in body
    assert report.stopped is False


def test_pure_orphan_branch_comments_and_releases(stubs: Stubs) -> None:
    """子も質問も無ければ回収コメント＋解放の 2 手（FR-016）。"""
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    assert _writes(stubs) == ["BoardWriter.comment", "BoardWriter.release"]
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert body.startswith(f"{MARKER} 回収:")
    assert report.notes == ()
    assert report.stopped is False


def test_recovery_does_not_add_the_failing_label(stubs: Stubs) -> None:
    """回収では `state:failing` を付けない（PoC の目視用の装飾を継承しない）。"""
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    ports = make_ports(stubs)

    recover.recover_orphans(ports, _filter())

    assert stubs.log.count("BoardWriter.set_labels") == 0


def test_recovery_comment_is_not_counted_as_a_return(stubs: Stubs) -> None:
    """回収コメントは差し戻しの勘定に入らない（FR-037・SD-02）。"""
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    ports = make_ports(stubs)

    recover.recover_orphans(ports, _filter())

    assert count_return_comments(stubs.state.comments["bd-1"], MARKER, SYSTEM) == 0


def test_recovery_uses_the_system_actor(stubs: Stubs) -> None:
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    ports = make_ports(stubs, system_actor="sysbot")

    recover.recover_orphans(ports, _filter())

    assert {call.kwargs["actor"] for call in stubs.log.of("BoardWriter.comment")} == {"sysbot"}
    assert {call.kwargs["actor"] for call in stubs.log.of("BoardWriter.release")} == {"sysbot"}


def test_three_branches_in_one_pass(stubs: Stubs) -> None:
    """3 通りが混在しても、それぞれの書込回数が 0・3・2 になる（SC-007）。"""
    orphans = [
        make_issue("bd-1", labels=[working_label()]),
        make_issue("bd-2", labels=[working_label()]),
        make_issue("bd-3", labels=[working_label()]),
    ]
    for issue in orphans:
        stubs.state.add(issue)
    stubs.state.working = orphans
    stubs.state.questions["bd-1"] = make_issue("bd-9")
    stubs.state.children["bd-2"] = [make_issue("bd-8")]
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    per_issue = {
        issue_id: len(
            [
                call
                for call in stubs.log.calls
                if call.target in WRITES and call.kwargs.get("issue_id") == issue_id
            ]
        )
        for issue_id in ("bd-1", "bd-2", "bd-3")
    }
    assert per_issue == {"bd-1": 0, "bd-2": 3, "bd-3": 2}
    assert report.stopped is False


# --------------------------------------------------------------------------------------
# 盤面に届かない（FR-017・SC-009）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [make_unavailable("list_working"), IssueNotFound("無い", issue_id="bd-1")],
    ids=["BoardUnavailable", "IssueNotFound"],
)
def test_list_working_failure_stops_the_cycle(
    stubs: Stubs, caplog: pytest.LogCaptureFixture, error: BaseException
) -> None:
    """拾い直しを**省いて先へ進まず**打ち切る（`IssueNotFound` も同じ扱い）。"""
    stubs.board_reader.failures.fail("BoardReader.list_working", 1, error)
    ports = make_ports(stubs)

    with caplog.at_level(logging.ERROR):
        report = recover.recover_orphans(ports, _filter())

    assert report.stopped is True
    assert _writes(stubs) == []
    assert [record.getMessage() for record in caplog.records] == [
        "orchestrator.recover.board_unavailable"
    ]


def test_failure_in_the_middle_stops_the_cycle(stubs: Stubs) -> None:
    """2 件目の判定で落ちたら、1 件目まで処理して打ち切る。"""
    orphans = [
        make_issue("bd-1", labels=[working_label()]),
        make_issue("bd-2", labels=[working_label()]),
    ]
    for issue in orphans:
        stubs.state.add(issue)
    stubs.state.working = orphans
    stubs.board_reader.failures.fail("BoardReader.open_question_of", 2)
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    assert report.stopped is True
    assert _writes(stubs) == ["BoardWriter.comment", "BoardWriter.release"]


# --------------------------------------------------------------------------------------
# 一覧に載せず notes に 1 行（FR-071a・SD-34）
# --------------------------------------------------------------------------------------


def test_handed_off_orphan_is_reported_as_a_note_only(stubs: Stubs) -> None:
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    stubs.state.children["bd-1"] = [make_issue("bd-2")]
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    assert len(report.notes) == 1
    assert report.notes[0].startswith("skipped bd-1: ")


def test_unreachable_handoff_in_recovery_still_only_notes(stubs: Stubs) -> None:
    """拾い直しでコメントが不達でも `halted` の行を作らない（FR-071a が優先）。"""
    orphan = make_issue("bd-1", labels=[working_label()])
    stubs.state.add(orphan)
    stubs.state.working = [orphan]
    stubs.state.children["bd-1"] = [make_issue("bd-2")]
    stubs.board_writer.failures.fail("BoardWriter.comment", 1)
    ports = make_ports(stubs)

    report = recover.recover_orphans(ports, _filter())

    assert report.stopped is True
    assert len(report.notes) == 1
    assert report.notes[0].startswith("skipped bd-1: ")
