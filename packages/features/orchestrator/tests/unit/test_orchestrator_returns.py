"""差し戻しの勘定と 3 種のコメントの印（FR-037・FR-038・FR-042・SC-019・SC-020a）。

正本は `specs/009-orchestrator/contracts/result-mapping.md` §4（印）・§1（写像表）。
"""

from __future__ import annotations

from typing import Literal, get_args

import pytest
from conftest import make_comment, make_issue_run

from common.interfaces.board import Comment
from common.interfaces.orchestrator import IssueRunSummary
from orchestrator.models import (
    HANDOFF_LABEL,
    RECOVER_LABEL,
    RESULT_OF,
    RETURN_LABEL,
    IssueDisposition,
    count_return_comments,
    recover_comment,
    return_comment,
    return_prefix,
)
from orchestrator.service.loop import (
    _summary,  # pyright: ignore[reportPrivateUsage]  # 出口そのものを測る（SC-030a）ため私有に触れる
)

MARKER = "[orchestrator]"
SYSTEM = "system"
HUMAN = "alice"


# --------------------------------------------------------------------------------------
# 印 3 種の本文書式（SD-03）
# --------------------------------------------------------------------------------------


def test_return_comment_body_format() -> None:
    body = return_comment(MARKER, 1, 2, "形の検査", "受入条文が空")
    assert body == "[orchestrator] 差し戻し（1/2 回目）[形の検査]: 受入条文が空"


def test_recover_comment_body_format() -> None:
    assert recover_comment(MARKER, "残骸を回収した") == "[orchestrator] 回収: 残骸を回収した"


def test_return_prefix_is_the_single_source_of_the_match_key() -> None:
    """前方一致の鍵は `return_prefix()` の 1 箇所から出る（FR-038）。"""
    prefix = return_prefix(MARKER)
    assert prefix == "[orchestrator] 差し戻し"
    assert return_comment(MARKER, 1, 2, "分類", "理由").startswith(prefix)


@pytest.mark.parametrize("label", [RECOVER_LABEL, HANDOFF_LABEL])
def test_other_markers_do_not_hit_the_return_prefix(label: str) -> None:
    """回収・停止の頭は差し戻しの前方一致に掛からない（SD-02・SD-03）。

    本文の完全な形は `test_orchestrator_handoff.py`（停止）が見る。ここは**語の衝突**だけを見る。
    """
    assert label != RETURN_LABEL
    assert not f"{MARKER} {label}".startswith(return_prefix(MARKER))
    assert not recover_comment(MARKER, "回収した").startswith(return_prefix(MARKER))


# --------------------------------------------------------------------------------------
# 勘定の表（印の種類 × actor × 件数）— contracts/result-mapping.md §6 の #7〜#9
# --------------------------------------------------------------------------------------


def _returns(n: int, actor: str = SYSTEM) -> list[Comment]:
    return [
        make_comment("bd-1", actor, return_comment(MARKER, i + 1, 2, "分類", "理由"), f"r-{i}")
        for i in range(n)
    ]


def _recovers(n: int) -> list[Comment]:
    return [
        make_comment("bd-1", SYSTEM, recover_comment(MARKER, "回収"), f"v-{i}") for i in range(n)
    ]


@pytest.mark.parametrize(
    ("comments", "expected"),
    [
        pytest.param([], 0, id="空なら 0（P-01）"),
        pytest.param(_returns(1), 1, id="システム actor の差し戻し 1 件"),
        pytest.param(_returns(2), 2, id="システム actor の差し戻し 2 件"),
        pytest.param(_recovers(3), 0, id="回収コメント 3 件のみは数えない"),
        pytest.param(_returns(2, HUMAN), 0, id="人間 actor の差し戻し風 2 件は数えない"),
        pytest.param(
            [*_returns(2), *_recovers(3), *_returns(1, HUMAN)],
            2,
            id="混在しても自分の差し戻しだけを数える",
        ),
        pytest.param(
            [make_comment("bd-1", SYSTEM, "[monitor] 封筒", "m-1")],
            0,
            id="計測の封筒は数えない",
        ),
    ],
)
def test_count_return_comments(comments: list[Comment], expected: int) -> None:
    assert count_return_comments(comments, MARKER, SYSTEM) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, False), (1, False), (2, True), (3, True)],
)
def test_limit_rule_is_count_ge_max_returns(count: int, expected: bool) -> None:
    """勘定そのものを測る（`count_return_comments` が件数を正しく数えること）。

    **判定式（`件数 >= max_returns`）を実装で使っている箇所は測っていない** —— 下 2 行は
    このテスト自身のデータ上の算術であって `service/kickoff.py` を通らないため、凍結 PoC の
    `件数 + 1 >= 上限` へ変えてもここは赤くならない（変異実測）。FR-042・SC-020a の判定式は
    `test_orchestrator_kickoff.py` の
    `test_default_limit_runs_the_executor_three_times_with_two_comments` ほか 3 本が押さえる
    （同変異で赤くなることを実測済み）。
    """
    max_returns = 2
    measured = count_return_comments(_returns(count), MARKER, SYSTEM)
    assert measured == count
    assert (measured >= max_returns) is expected


def test_marker_change_moves_the_match_key_together() -> None:
    """印を変えると本文も鍵も一緒に動く（コード上 1 箇所＝ FR-038）。"""
    other = "[orch]"
    assert count_return_comments(_returns(2), other, SYSTEM) == 0
    body = return_comment(other, 1, 2, "分類", "理由")
    assert count_return_comments([make_comment("bd-1", SYSTEM, body, "x-1")], other, SYSTEM) == 1


# --------------------------------------------------------------------------------------
# 結果の語彙の写像（contracts/result-mapping.md §6 の #10・SC-030a）
# --------------------------------------------------------------------------------------


def test_result_map_covers_every_disposition_in_seven_rows() -> None:
    assert len(RESULT_OF) == 7
    assert set(RESULT_OF) == set(IssueDisposition)


def test_result_map_values_are_five_words_without_released() -> None:
    assert set(RESULT_OF.values()) == {"closed", "failing", "unsafe", "escalated", "halted"}
    assert "released" not in set(RESULT_OF.values())


def test_result_words_are_a_subset_of_the_shared_vocabulary() -> None:
    """語は共有側 `IssueRunSummary.result` の 6 値の部分である（写しを独り歩きさせない）。"""
    annotation = IssueRunSummary.model_fields["result"].annotation
    assert annotation is not None
    shared: set[str] = set(get_args(annotation))
    assert shared == {"closed", "released", "failing", "escalated", "unsafe", "halted"}
    assert set(RESULT_OF.values()) <= shared


def test_issue_result_alias_matches_the_five_reachable_words() -> None:
    from orchestrator.models import IssueResult

    assert set(get_args(IssueResult.__value__)) == set(RESULT_OF.values())
    assert get_args(Literal["closed"]) == ("closed",)


@pytest.mark.parametrize("disposition", list(IssueDisposition), ids=lambda d: d.value)
def test_the_summary_maps_every_disposition_through_result_of(
    disposition: IssueDisposition,
) -> None:
    """**出口**（`IssueRunSummary` を組む箇所）が写像表を通ることを固定する（SC-030a）。

    `RESULT_OF` の中身だけを見るテストでは、出口に**2 つ目の写像**を足しても緑のままで、
    `released` が漏れ出す余地が残っていた（変異実測）。7 分岐すべてを出口へ通す。
    """
    summary = _summary(make_issue_run(), disposition, None, 0)

    assert summary.result == RESULT_OF[disposition]
    assert summary.result in {"closed", "failing", "unsafe", "escalated", "halted"}
    assert summary.result != "released"  # FR-071a・DIFF-07
