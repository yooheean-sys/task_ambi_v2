"""US1: 契約台帳 50 件が割り当てどおりに被覆されていることの機械検査。plan (v)・FR-011〜013。

**期待値をテストコードに書き写さない**（FR-011）。50・各クラスタの担当・重複 0 —— すべて
2 つの正本（`p4_module_contract.csv` と `x2_cluster_index.csv`）から導く。割当を書き換えると
テストコードを触らずに赤／緑が変わる。

**静的走査だけを行う**（FR-012）。実行中の pytest セッションを一切見ないので、`-k`・`-m`・
部分集合実行で結果が変わらない（SC-006・SC-007）。`bd` の無い環境で `-m "not requires_bd"`
を実行しても、02・03・01・04 のテストは**収集はされる**（deselect されるだけ）ので AST 走査に
影響しない。

本ファイルは被覆検査そのものを置くファイルなので `contract_if` を書かない（規約 §7）。
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from _support.cluster_sources import (
    REQUIRED_COLUMNS,
    ClusterAssignment,
    cluster_index_path,
    collect_contract_if_in_meta,
    header_columns,
    load_cluster_assignments,
)
from _support.contract_ledger import (
    CoverageDiff,
    IfDeclaration,
    collect_declarations,
    compare,
    contract_csv_path,
    load_contract_rows,
)

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from pathlib import Path

CONTRACT_META = True
"""被覆検査そのものを置くファイル（`collect_declarations` の走査対象から外す＝規約 §7）。"""

CLUSTERS_ROOT_REL = "apps/api/tests/integration"
"""クラスタディレクトリの親（`ClusterAssignment.directory_name` をここに繋ぐ）。"""


def _clusters_root() -> Path:
    """クラスタディレクトリの親の絶対パス。"""
    return find_repo_root() / CLUSTERS_ROOT_REL


def _declarations_of(assignment: ClusterAssignment) -> tuple[IfDeclaration, ...]:
    """1 クラスタのディレクトリを走査して宣言を集める（直下のみ・非再帰）。

    `collect_declarations` は**直下のみ**を見るので、5 クラスタを個別に走査して合算する
    （spec §0-6 #1）。
    """
    directory = _clusters_root() / assignment.directory_name
    if not directory.is_dir():
        return ()
    return collect_declarations(directory)


def _diff_message(
    diff: CoverageDiff,
    owner_of: dict[str, str],
    declared_where: dict[str, list[str]],
) -> str:
    """赤いときに**追加調査なしに原因を特定できる**本文を組む（FR-013）。

    名指しするのは 3 要素 —— 未被覆の IF・**想定クラスタ**・実際に宣言していたファイル名。
    """
    lines: list[str] = []
    if diff.missing:
        lines.append("未被覆の契約行（宣言が 1 件も無い）:")
        lines.extend(
            f"  - {if_id}: 想定クラスタ={owner_of.get(if_id, '（割当が無い）')}"
            f" / 実際に宣言していたファイル={declared_where.get(if_id) or 'なし'}"
            for if_id in sorted(diff.missing)
        )
    if diff.unknown:
        lines.append("台帳に無い IF を宣言している:")
        lines.extend(
            f"  - {if_id} / 宣言していたファイル={declared_where.get(if_id) or 'なし'}"
            for if_id in sorted(diff.unknown)
        )
    if diff.malformed:
        lines.append("`contract_if` の付け方が受理できない（0 件・2 件以上・非リテラル引数）:")
        lines.extend(
            f"  - {item.file}::{item.test_name} / contract_if の個数={item.count}"
            for item in diff.malformed
        )
    lines.append(f"台帳 {diff.ledger_total} 件 / 宣言された相異なる IF {diff.declared_total} 件")
    return "\n".join(lines)


def test_cluster_if_coverage_matches_the_ledger_and_the_assignment() -> None:
    """US1: 台帳 50 件が、割当どおりのクラスタのディレクトリ内で宣言されている。

    1 本で 4 条件を突合する —— (a) `missing` が空 (b) 各ディレクトリの宣言 ⊆ そのクラスタの
    `primary_ifs` (c) `unknown` が空 (d) `malformed` が空。

    期待値は 2 正本から導く（FR-011）。赤いときは未被覆の IF・想定クラスタ・実際に宣言して
    いたファイル名を名指しする（FR-013）。
    """
    rows = load_contract_rows(contract_csv_path())
    assignments = load_cluster_assignments(cluster_index_path())

    owner_of = {
        if_id: assignment.directory_name
        for assignment in assignments
        for if_id in assignment.primary_ifs
    }
    declared_where: dict[str, list[str]] = {}
    all_declarations: list[IfDeclaration] = []
    misplaced: list[str] = []

    for assignment in assignments:
        allowed = set(assignment.primary_ifs)
        for declaration in _declarations_of(assignment):
            all_declarations.append(declaration)
            if declaration.if_id is None:
                continue
            declared_where.setdefault(declaration.if_id, []).append(
                f"{assignment.directory_name}/{declaration.file}"
            )
            if declaration.if_id not in allowed:
                misplaced.append(
                    f"{assignment.directory_name}/{declaration.file}::{declaration.test_name}"
                    f" が {declaration.if_id} を宣言している"
                    f" / 割当上の担当は {owner_of.get(declaration.if_id, '（割当が無い）')}"
                )

    diff = compare(rows, all_declarations)
    assert not diff.missing, _diff_message(diff, owner_of, declared_where)
    assert not misplaced, "割当外の IF を宣言している:\n" + "\n".join(f"  - {m}" for m in misplaced)
    assert not diff.unknown, _diff_message(diff, owner_of, declared_where)
    assert not diff.malformed, _diff_message(diff, owner_of, declared_where)


def test_cluster_index_assignment_is_a_partition_of_the_ledger() -> None:
    """読み手の健全性: 割当 csv が台帳 50 件の**分割**になっている。

    1 行以上・読み手が要る列がヘッダに実在・主検証 IF の集合が台帳と一致・重複 0・
    **通過 IF と主検証 IF が交わらない**（通過を主検証として宣言させない＝ FR-010b の裏取り）。

    件数はいずれも**正本から導く**（`5` も `50` もテストに直書きしない —— 数えるのは csv の
    行と台帳の行である。列は「17 列」ではなく「読み手が要る 8 列が在るか」を見る ——
    正本に列が 1 つ増えただけで、読めているのに赤くなる形にしない）。
    """
    rows = load_contract_rows(contract_csv_path())
    assignments = load_cluster_assignments(cluster_index_path())
    ledger_ids = {row.if_id for row in rows}

    assert assignments, "割当 csv が 1 行も読めない（抽出が壊れている）"
    header = header_columns(cluster_index_path())
    missing_columns = [name for name in REQUIRED_COLUMNS if name not in header]
    assert not missing_columns, (
        "割当 csv に読み手が要る列が無い（列名が変わったら読み手の対応を見直すこと）:"
        f" 欠け={missing_columns} / 実際のヘッダ={list(header)}"
    )

    primary: list[str] = [if_id for a in assignments for if_id in a.primary_ifs]
    assert len(primary) == len(set(primary)), (
        "主検証 IF がクラスタ間で重複している（D14-c 違反）:"
        f" {sorted({i for i in primary if primary.count(i) > 1})}"
    )
    assert set(primary) == ledger_ids, (
        "主検証 IF の集合が台帳と一致しない:"
        f" 台帳のみ={sorted(ledger_ids - set(primary))}"
        f" / 割当のみ={sorted(set(primary) - ledger_ids)}"
    )
    for assignment in assignments:
        overlap = set(assignment.primary_ifs) & set(assignment.passthrough_ifs)
        assert not overlap, (
            f"{assignment.directory_name}: 通過 IF と主検証 IF が交わっている: {sorted(overlap)}"
        )


def test_cluster_meta_files_declare_no_contract_if() -> None:
    """`CONTRACT_META = True` のファイルに `contract_if` が 1 つも無い（B-11 の抜け道封じ）。

    META ファイルは `collect_declarations` の走査から外れるので、そこへ宣言を隠せば被覆検査から
    逃げられてしまう。逆に「宣言のあるファイルを META にした」場合は当該 IF が `missing` に
    落ちるので上のテストが自動で赤くなる。
    """
    offenders: list[str] = []
    for assignment in load_cluster_assignments(cluster_index_path()):
        directory = _clusters_root() / assignment.directory_name
        if not directory.is_dir():
            continue
        offenders.extend(
            f"{assignment.directory_name}/{file}::{test_name}"
            for file, test_name in collect_contract_if_in_meta(directory)
        )
    assert not offenders, (
        "`CONTRACT_META = True` のファイルに `contract_if` が付いている"
        "（被覆検査の走査から外れるので宣言を置いてはならない＝規約 §7）:\n"
        + "\n".join(f"  - {item}" for item in offenders)
    )


def test_cluster_if_coverage_is_static_and_survives_a_copy(tmp_path: Path) -> None:
    """数値を直書きしていないことの証明: 正本の**写し**を渡しても同じ結論が出る。

    台帳と割当 csv を `tmp_path` へ写して読み直し、件数と割当が一致することを確かめる。
    テストが 50 や 5 を持っていれば、写しの側を数えた結果と食い違わないことに意味は無い ——
    ここで見るのは「**読み手が正本を読んでいる**」ことである。
    """
    ledger_copy = tmp_path / "ledger.csv"
    index_copy = tmp_path / "index.csv"
    shutil.copyfile(contract_csv_path(), ledger_copy)
    shutil.copyfile(cluster_index_path(), index_copy)

    original_rows = load_contract_rows(contract_csv_path())
    copied_rows = load_contract_rows(ledger_copy)
    original_assignments = load_cluster_assignments(cluster_index_path())
    copied_assignments = load_cluster_assignments(index_copy)

    assert [row.if_id for row in copied_rows] == [row.if_id for row in original_rows]
    assert [a.primary_ifs for a in copied_assignments] == [
        a.primary_ifs for a in original_assignments
    ]
    assert [a.directory_name for a in copied_assignments] == [
        a.directory_name for a in original_assignments
    ]
