"""US2: クラスタ文書 §6 の観点表 72 行に 1 行残らず検証があることの機械検査。FR-020〜023a。

**期待値をテストコードに書き写さない**（FR-023）。72・内訳 11/16/15/17/13 —— いずれも 5 つの
クラスタ文書から導く。文書ファイル名も `x2_cluster_index.csv` の「文書」列から引く
（ハードコードしない）。

**抽出が壊れたときに「空 vs 空」で緑にならない**（FR-023a）。`014` が踏んだ形（アンカーの
書き間違いで 0 行を抽出し、空同士の比較が偽の緑になる）を再発させないため、健全性検査を
併置する —— 5 文書とも 1 行以上・行番号が 1 起点の連番。

本ファイルは観点対応の検査そのものを置くファイルなので `contract_if` を書かない（規約 §7）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _support.cluster_sources import (
    CaseDeclaration,
    ClusterAssignment,
    cluster_index_path,
    collect_case_declarations,
    load_case_rows,
    load_cluster_assignments,
)

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from pathlib import Path

CONTRACT_META = True
"""観点対応の検査そのものを置くファイル（`collect_declarations` の走査対象から外す）。"""

CLUSTERS_ROOT_REL = "apps/api/tests/integration"

NOT_EXERCISED_CASES = ("01#11", "04#17")
"""**振る舞いを踏まない行**（FR-021。増やしてはならない）。

01 #11 は openrouter 経路（偽サーバでは模擬しない＝ `NX-01`）、04 #17 は `MappingUnverified`
（本クラスタでは `RunMonitor` がスタブ＝ `NX-02`）。この 2 件は「行を消さずに非実行と宣言する」
ものであり、**個数と対象の両方**を固定する（黙って増やせない）。
"""

CONDITIONAL_CASES = ("01#10", "03#14", "05#13")
"""**条件付きの行**（FR-022。増やしてはならない）。

01 #10・03 #14 は Hook の非同期経路（`CX-01`）、05 #13 は watcher 縮退（`CX-02`）。条件の成否は
テスト内で判定し、非実行になったら `pytest.skip(理由)` で出力に残す（人間の目視に委ねない）。
"""


def _clusters_root() -> Path:
    """クラスタディレクトリの親の絶対パス。"""
    return find_repo_root() / CLUSTERS_ROOT_REL


def _declarations_of(assignment: ClusterAssignment) -> tuple[CaseDeclaration, ...]:
    """1 クラスタのディレクトリを走査して観点行の宣言を集める（直下のみ・非再帰）。"""
    directory = _clusters_root() / assignment.directory_name
    if not directory.is_dir():
        return ()
    return collect_case_declarations(directory)


def test_cluster_case_coverage_matches_the_section_six_tables() -> None:
    """US2: §6 の全行に少なくとも 1 本のテストが対応している。

    1 本で 3 条件を突合する —— (a) 全行に**少なくとも 1 本**のテストが対応 (b) 宣言された行番号が
    §6 に実在 (c) 非実行として扱われている行が FR-021 の 2 件と完全一致。

    対応の無い行は**行番号つきで名指し**する。
    """
    assignments = load_cluster_assignments(cluster_index_path())
    expected: list[str] = []
    declared: dict[str, list[str]] = {}
    not_exercised: dict[str, list[str]] = {}

    for assignment in assignments:
        expected.extend(
            row.case_id for row in load_case_rows(assignment.document_path, assignment.cluster_id)
        )
        for declaration in _declarations_of(assignment):
            if declaration.case_id is None:
                continue
            where = f"{assignment.directory_name}/{declaration.file}::{declaration.test_name}"
            declared.setdefault(declaration.case_id, []).append(where)
            if declaration.not_exercised is not None:
                not_exercised.setdefault(declaration.case_id, []).append(
                    f"{where}（理由 ID={declaration.not_exercised}）"
                )

    uncovered = [case_id for case_id in expected if case_id not in declared]
    assert not uncovered, (
        "§6 の観点行に対応するテストが無い（FR-020）:\n"
        + "\n".join(f"  - {case_id}" for case_id in uncovered)
        + f"\n  §6 の総行数={len(expected)} / 対応済み={len(set(expected) & set(declared))}"
    )

    unknown = sorted(set(declared) - set(expected))
    assert not unknown, "§6 に実在しない観点行を宣言している:\n" + "\n".join(
        f"  - {case_id}: {declared[case_id]}" for case_id in unknown
    )

    assert sorted(not_exercised) == sorted(NOT_EXERCISED_CASES), (
        "非実行として扱われている観点行が固定の 2 件と一致しない（FR-021）:"
        f" 実際={sorted(not_exercised)} / 期待={sorted(NOT_EXERCISED_CASES)}"
        f" / 内訳={not_exercised}"
    )


def test_cluster_case_reader_extracts_every_document() -> None:
    """抽出器の健全性: 5 文書とも 1 行以上・行番号が 1 起点の連番（FR-023a）。

    `014` の「空 vs 空の偽の緑」を再発させない —— §6 の体裁が変わってアンカーが外れたら、
    上の突合が空同士の比較で緑になる前に**ここが赤くなる**。
    """
    assignments = load_cluster_assignments(cluster_index_path())
    assert assignments, "割当 csv が 1 行も読めない（抽出が壊れている）"

    for assignment in assignments:
        rows = load_case_rows(assignment.document_path, assignment.cluster_id)
        assert rows, (
            f"{assignment.document} の §6 から 1 行も抽出できない"
            "（`## 6.` 見出しか表の体裁が変わった可能性がある）"
        )
        numbers = [row.number for row in rows]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"{assignment.document} の §6 の行番号が 1 起点の連番でない: {numbers}"
        )


def test_cluster_case_declarations_are_well_formed() -> None:
    """各テストが `cluster_case` を**ちょうど 1 件**・文字列リテラルで持ち、番号が置き場と一致。

    `cluster_case` が 0 件・2 件以上・非リテラル引数のテストは `case_id` が `None` になるので、
    上の突合からは黙って外れてしまう。ここで名指しして落とす（FR-020 の「テスト → 行は 1 対 1」）。
    """
    offenders: list[str] = []
    for assignment in load_cluster_assignments(cluster_index_path()):
        for declaration in _declarations_of(assignment):
            where = f"{assignment.directory_name}/{declaration.file}::{declaration.test_name}"
            if declaration.count != 1 or declaration.case_id is None:
                offenders.append(
                    f"{where}: `cluster_case` の付け方が受理できない"
                    f"（個数={declaration.count}・文字列リテラル 1 個であること）"
                )
                continue
            prefix = f"{assignment.cluster_id}#"
            if not declaration.case_id.startswith(prefix):
                offenders.append(
                    f"{where}: 宣言 {declaration.case_id!r} のクラスタ番号が置き場と一致しない"
                    f"（期待する接頭辞={prefix!r}）"
                )
    assert not offenders, "観点行の宣言が規約に合わない（規約 §7）:\n" + "\n".join(
        f"  - {item}" for item in offenders
    )


def test_cluster_conditional_rows_are_exactly_the_three_fixed_rows() -> None:
    """条件付きの行が 01 #10・03 #14・05 #13 の 3 件と完全一致する（FR-022 の裏取り）。

    条件付きは「条件が成立しなければ `pytest.skip(理由)` で出力に残す」形なので、放っておくと
    黙って skip が増える。**個数と対象の両方**を固定して増殖を止める。
    """
    conditional: dict[str, list[str]] = {}
    for assignment in load_cluster_assignments(cluster_index_path()):
        for declaration in _declarations_of(assignment):
            if declaration.conditional is None or declaration.case_id is None:
                continue
            conditional.setdefault(declaration.case_id, []).append(
                f"{assignment.directory_name}/{declaration.file}::{declaration.test_name}"
                f"（理由 ID={declaration.conditional}）"
            )
    assert sorted(conditional) == sorted(CONDITIONAL_CASES), (
        "条件付きとして扱われている観点行が固定の 3 件と一致しない（FR-022）:"
        f" 実際={sorted(conditional)} / 期待={sorted(CONDITIONAL_CASES)}"
        f" / 内訳={conditional}"
    )
