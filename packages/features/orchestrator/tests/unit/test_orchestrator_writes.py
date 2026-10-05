"""起票と close の唯一の呼び出し箇所（`service/writes.py`。SC-027・D04-b・P5D-037）。

正本は `specs/009-orchestrator/contracts/result-mapping.md` §5。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Stubs, make_issue, make_ports, make_unavailable

import orchestrator as orchestrator_package
from common.interfaces.board import (
    BoardUnavailable,
    BoardWriteUnverified,
    IssueSpec,
    IssueStatus,
)
from orchestrator.service import writes


def _spec(parent: str | None = "bd-1") -> IssueSpec:
    return IssueSpec(
        title="子",
        description="本文",
        labels=["kind:child", "project:alpha"],
        parent=parent,
        acceptance="outcome/child.md",
    )


def test_create_issue_calls_create_once_and_passes_spec_and_actor(stubs: Stubs) -> None:
    ports = make_ports(stubs)
    spec = _spec()

    issue_id = writes.create_issue(ports, spec, "ai-planner")

    assert stubs.log.count("BoardWriter.create") == 1
    call = stubs.log.of("BoardWriter.create")[0]
    assert call.kwargs["spec"] is spec
    assert call.kwargs["actor"] == "ai-planner"
    assert issue_id.startswith("bd-")


def test_create_issue_does_not_read_back(stubs: Stubs) -> None:
    """起票の確認読取は**呼び手側**（分解の一括確認）が行う。ここでは読まない。"""
    ports = make_ports(stubs)

    writes.create_issue(ports, _spec(), "ai-planner")

    assert stubs.log.only("BoardReader.show", "BoardReader.children_of") == []


def test_close_issue_closes_then_reads_back(stubs: Stubs) -> None:
    """close → show の順で 1 回ずつ（D04-b。閉じたら必ず読み直す）。"""
    stubs.state.add(make_issue("bd-1"))
    ports = make_ports(stubs)

    issue = writes.close_issue(ports, "bd-1", "分解完了 2 件", "ai-planner")

    assert stubs.log.only("BoardWriter.close", "BoardReader.show") == [
        "BoardWriter.close",
        "BoardReader.show",
    ]
    assert stubs.log.of("BoardWriter.close")[0].kwargs == {
        "issue_id": "bd-1",
        "reason": "分解完了 2 件",
        "actor": "ai-planner",
    }
    assert issue.status is IssueStatus.closed


@pytest.mark.parametrize(
    "error",
    [
        BoardWriteUnverified("読み直せない", issue_id="bd-1", mismatch="status"),
        make_unavailable("BoardWriter.create"),
    ],
)
def test_create_issue_lets_contract_errors_through(stubs: Stubs, error: BaseException) -> None:
    """握り潰さず呼び出し元へ素通しする（差し戻しにも handoff にもここでは変換しない）。"""
    stubs.board_writer.failures.fail("BoardWriter.create", 1, error)
    ports = make_ports(stubs)

    with pytest.raises(type(error)):
        writes.create_issue(ports, _spec(), "ai-planner")


@pytest.mark.parametrize("target", ["BoardWriter.close", "BoardReader.show"])
def test_close_issue_lets_board_unavailable_through(stubs: Stubs, target: str) -> None:
    stubs.state.add(make_issue("bd-1"))
    if target == "BoardWriter.close":
        stubs.board_writer.failures.fail(target, 1)
    else:
        stubs.board_reader.failures.fail(target, 1)
    ports = make_ports(stubs)

    with pytest.raises(BoardUnavailable):
        writes.close_issue(ports, "bd-1", "理由", "ai-planner")


def test_actor_has_no_default(stubs: Stubs) -> None:
    """`actor` は引数で受けたものをそのまま使う（P5D-037 の 2 系統をこのファイルが決めない）。"""
    stubs.state.add(make_issue("bd-1"))
    ports = make_ports(stubs)

    writes.create_issue(ports, _spec(), "actor-A")
    writes.close_issue(ports, "bd-1", "理由", "actor-B")

    assert stubs.log.of("BoardWriter.create")[0].kwargs["actor"] == "actor-A"
    assert stubs.log.of("BoardWriter.close")[0].kwargs["actor"] == "actor-B"
    assert ports.config.system_actor not in {"actor-A", "actor-B"}


# --------------------------------------------------------------------------------------
# 呼び出し箇所がそれぞれ 1 箇所（SC-027・plan の C-2）
# --------------------------------------------------------------------------------------


def _src_files() -> list[Path]:
    """`src/orchestrator/` の全 `.py`（**読むだけ**。他モジュールへは触れない）。"""
    root = Path(orchestrator_package.__file__).parent
    return sorted(root.rglob("*.py"))


@pytest.mark.parametrize(("method", "owner"), [("create", "writes.py"), ("close", "writes.py")])
def test_the_write_method_is_called_from_exactly_one_place(method: str, owner: str) -> None:
    """`board_writer.create`／`board_writer.close` の呼び出しが**モジュール全体で 1 箇所**。

    これまで守っていたのは quickstart S7 の**手動 grep** だけで、2 箇所目を作っても
    テストは緑だった（変異実測）。`src` を読むだけの静的走査なので、モジュール境界
    （憲章 §1〜§3）にも `apps/api` への依存にも触れない。
    """
    needle = f"board_writer.{method}("
    hits = [
        (path.name, line)
        for path in _src_files()
        for line in path.read_text(encoding="utf-8").splitlines()
        if needle in line
    ]

    assert len(hits) == 1, f"{needle} の呼び出しが {len(hits)} 箇所: {hits}"
    assert hits[0][0] == owner
