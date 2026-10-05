"""兄弟衝突の検査（`validate_proposal` の §5。US3 AC-10／AC-11・FR-043／FR-044）。

正本は [contracts/proposal-gate.md](../../../../specs/006-stages/contracts/proposal-gate.md) §5。

**coverage（旧 §6・stages-F07）はここに無い**。凍結契約 `validate_proposal(proposal, spec,
existing_children)` に親の受入条文を渡す引数が無く、検査が構造的に成立しないためである
（CHK013 の裁定。coverage は呼び手＝ orchestrator の責務へ移した。design-sync **B-25**）。
"""

from __future__ import annotations

from conftest import make_item, make_proposal, make_service, stage_spec

from common.kernel.enums import StageName

PROJECT = stage_spec(StageName.project)


def _problems(items: list[str], *, existing: list[str] | None = None) -> list[str]:
    # 成果物名は件ごとに変える（題名の衝突だけを見るため）。
    proposal = make_proposal(
        [
            make_item(title=t, acceptance=[f"- [ ] outcome/f{i}.md にまとめる"])
            for i, t in enumerate(items)
        ]
    )
    return make_service().validate_proposal(proposal, PROJECT, existing or []).problems


def test_duplicate_title_within_proposal() -> None:
    """US3 AC-10（**PoC に無い新規**）: 提案内の題名の完全一致を指摘する。"""
    problems = _problems(["同じ題名です", "別の題名です", "同じ題名です"])
    assert any("3件目 の title" in p and "重複" in p for p in problems)


def test_similar_titles_are_not_reported() -> None:
    """完全一致だけを見る（似ているだけの題名は指摘しない）。"""
    assert not any("重複" in p for p in _problems(["設計を書く", "設計を書く 2"]))


def test_duplicate_title_against_existing_children() -> None:
    """US3 AC-11（**新規**）: 既存の子の題名と一致すれば指摘する。"""
    problems = _problems(["既にある題名"], existing=["既にある題名"])
    assert any("1件目 の title" in p and "既存" in p for p in problems)


def test_existing_children_similar_title_is_not_reported() -> None:
    assert not any("既存" in p for p in _problems(["新しい題名"], existing=["別の題名"]))


def test_duplicate_outcome_filename_within_proposal() -> None:
    """§5: 提案内の成果物**ファイル名**の重なりを指摘する（キーは `outcome_filename()` の結果）。"""
    items = [
        make_item(title="a", acceptance=["- [ ] outcome/report.md にまとめる"]),
        make_item(title="b", acceptance=["- [ ]  outcome/report.md  にまとめる"]),
    ]
    problems = make_service().validate_proposal(make_proposal(items), PROJECT, []).problems
    assert any("2件目 の acceptance" in p and "重複" in p for p in problems)


def test_distinct_outcome_filenames_are_not_reported() -> None:
    items = [
        make_item(title="a", acceptance=["- [ ] outcome/a.md にまとめる"]),
        make_item(title="b", acceptance=["- [ ] outcome/b.md にまとめる"]),
    ]
    problems = make_service().validate_proposal(make_proposal(items), PROJECT, []).problems
    assert not any("重複" in p for p in problems)


def test_non_canonical_tokens_are_skipped_in_collision() -> None:
    """§5: 正準形でないトークンは衝突の照合から飛ばす（形式違反は別に指摘済み）。"""
    items = [
        make_item(title="a", acceptance=["- [ ] ../outcome/x.md にまとめる"]),
        make_item(title="b", acceptance=["- [ ] ../outcome/x.md にまとめる"]),
    ]
    problems = make_service().validate_proposal(make_proposal(items), PROJECT, []).problems
    assert not any("重複" in p for p in problems)
    assert sum(1 for p in problems if "形ではありません" in p) == 2


def test_existing_children_are_matched_by_title_only() -> None:
    """§5: 既存の子と照合できるのは**題名だけ**（条文は渡らないので成果物名は照合できない）。"""
    items = [make_item(title="新しい題名", acceptance=["- [ ] outcome/x.md にまとめる"])]
    problems = (
        make_service().validate_proposal(make_proposal(items), PROJECT, ["outcome/x.md"]).problems
    )
    assert not any("重複" in p for p in problems)


def test_same_item_declaring_one_outcome_twice_is_not_reported() -> None:
    """§5・FR-043: 見るのは**提案どうし**の重なり。1 件の中の再掲は衝突ではない。"""
    items = [
        make_item(
            title="a",
            acceptance=[
                "- [ ] outcome/report.md にまとめる",
                "- [ ] outcome/report.md をレビューする",
            ],
        )
    ]
    result = make_service().validate_proposal(make_proposal(items), PROJECT, [])
    assert not any("重複" in p for p in result.problems)
    assert result.ok


def test_two_items_sharing_one_outcome_are_still_reported() -> None:
    """件ごとの重複除去を入れても、**別の件**どうしの重なりは指摘し続ける（退行の防止）。"""
    items = [
        make_item(
            title="a",
            acceptance=[
                "- [ ] outcome/report.md にまとめる",
                "- [ ] outcome/report.md をレビューする",
            ],
        ),
        make_item(title="b", acceptance=["- [ ] outcome/report.md にも書く"]),
    ]
    problems = make_service().validate_proposal(make_proposal(items), PROJECT, []).problems
    assert [p for p in problems if "重複" in p] == [
        "2件目 の acceptance: 成果物 report.md が 1件目 と重複しています。"
    ]
