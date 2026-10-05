"""面被覆の機械検査（C-1〜C-6。FR-003〜FR-005・SC-001〜SC-003）。

正本 `docs/design/phase6/openapi.yaml` の (パス, 操作, status) 対が、**宣言 ∪ 台帳**で
漏れなく重複なく覆われていることを見る。**判定側なので `http_op` を 1 件も宣言しない**
（規約 §5）。

**期待値をここに書かない**（FR-003）—— パス数・操作数・対の数・パス名・status の一覧は
いずれも正本から数える。正本にパスや操作が 1 件足されたら、本ファイルを 1 行も変えずに
`missing_operations`／`missing_pairs` に出て落ちる（SC-002）。

宣言の収集は **AST の静的走査**である（R-07）。`http_contract/` の一部だけを走らせても結果が
変わらない（FR-034）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _support.openapi_source import (
    CoverageDiff,
    Declaration,
    UnresolvedDeclaration,
    collect_declarations,
    compare,
    declared_pairs,
    load_document,
    openapi_path,
    operations,
)
from http_ledger import KNOWN_DIVERGENCES, UNREACHED, unreached_pairs

HTTP_CONTRACT_DIR = Path(__file__).parent


@pytest.fixture(scope="module")
def declarations() -> tuple[Declaration, ...]:
    """`http_contract/` 直下から静的に読めた宣言。"""
    found, _unresolved = collect_declarations(HTTP_CONTRACT_DIR)
    return found


@pytest.fixture(scope="module")
def unresolved() -> tuple[UnresolvedDeclaration, ...]:
    """静的に読めなかった宣言（1 件でも在れば C-0 が落とす）。"""
    _found, bad = collect_declarations(HTTP_CONTRACT_DIR)
    return bad


@pytest.fixture(scope="module")
def diff(declarations: tuple[Declaration, ...]) -> CoverageDiff:
    """正本の全対と「宣言 ∪ 台帳」の突合。"""
    doc = load_document(openapi_path())
    return compare(declared_pairs(doc), declarations, unreached_pairs())


def _format_pairs(pairs: tuple[tuple[str, ...], ...]) -> str:
    """失敗時の案内（1 行 1 件で並べる）。"""
    return "\n".join(f"    - {' '.join(pair)}" for pair in pairs)


def test_every_http_op_declaration_is_statically_readable(
    unresolved: tuple[UnresolvedDeclaration, ...],
) -> None:
    """C-0: `http_op` の引数が文字列リテラル 3 つでない宣言が 1 件も無い（規約 §5）。"""
    listed = "\n".join(f"    - {item.source}:{item.line} {item.reason}" for item in unresolved)
    assert not unresolved, "静的に読めない http_op 宣言がある（AST 走査の規約に反する）:\n" + listed


def test_every_declared_operation_is_covered(diff: CoverageDiff) -> None:
    """C-1: 正本の全操作が「宣言 ∪ 台帳」に含まれる（FR-004 前半・SC-001）。"""
    assert not diff.missing_operations, (
        "正本に在って検証も台帳も無い操作がある（SC-001）:\n"
        + _format_pairs(diff.missing_operations)
    )


def test_no_declaration_points_outside_the_source(diff: CoverageDiff) -> None:
    """C-2: 宣言・台帳が正本に無い操作を指していない（FR-004 後半）。"""
    assert not diff.unknown_operations, (
        "正本に無い操作を宣言または台帳が指している:\n" + _format_pairs(diff.unknown_operations)
    )


def test_every_declared_status_pair_is_covered(diff: CoverageDiff) -> None:
    """C-3: 正本の全 (操作, status) 対が「宣言 ∪ 台帳」に含まれる（FR-005・SC-003）。"""
    assert not diff.missing_pairs, (
        "正本に在って検証も踏まない理由も無い (操作, status) がある（SC-003）:\n"
        + _format_pairs(diff.missing_pairs)
    )


def test_no_status_pair_points_outside_the_source(diff: CoverageDiff) -> None:
    """C-4a: 宣言・台帳が正本に無い (操作, status) を指していない。"""
    assert not diff.unknown_pairs, (
        "正本に無い (操作, status) を宣言または台帳が指している:\n"
        + _format_pairs(diff.unknown_pairs)
    )


def test_declarations_and_the_unreached_ledger_do_not_overlap(diff: CoverageDiff) -> None:
    """C-4b: 同じ対を「検証した」と「踏まない」の両方に書いていない（規約 §8）。"""
    assert not diff.overlapping_pairs, (
        "同じ (操作, status) が宣言と UNREACHED の両方に在る（2 つの台帳を混ぜている）:\n"
        + _format_pairs(diff.overlapping_pairs)
    )


def test_every_known_divergence_has_a_declaration(
    declarations: tuple[Declaration, ...],
) -> None:
    """C-4c: 既知差分の各対に**宣言が在る**（xfail だけ残る事故を防ぐ。規約 §8）。"""
    declared = {item.pair for item in declarations}
    orphaned = tuple(sorted(row.pair for row in KNOWN_DIVERGENCES if row.pair not in declared))
    assert not orphaned, (
        "KNOWN_DIVERGENCES に在るのに検証の宣言が無い対がある"
        "（検証を書かずに xfail だけ残っている）:\n" + _format_pairs(orphaned)
    )


def test_every_unreached_row_states_a_reason() -> None:
    """C-5: 踏まない対の各行に**空でない理由**が在る（FR-005）。"""
    empty = tuple(row.pair for row in UNREACHED if not row.reason.strip())
    assert not empty, (
        "UNREACHED に理由の無い行がある（理由の無い未被覆は認めない）:\n" + _format_pairs(empty)
    )


def test_every_unreached_row_cites_evidence() -> None:
    """C-5b: 踏まない対の各行に根拠の ID が在る。"""
    bare = tuple(row.pair for row in UNREACHED if not row.evidence.strip())
    assert not bare, "UNREACHED に根拠 ID の無い行がある:\n" + _format_pairs(bare)


def test_coverage_closes_per_owning_module(declarations: tuple[Declaration, ...]) -> None:
    """C-6: `x-mod` 別の 4 群それぞれで被覆が閉じる（plan (v) C-6）。

    1 群だけ抜けたことを名指しで示す。群の一覧・件数は**正本から数える**（テストに書かない）。
    """
    doc = load_document(openapi_path())
    by_mod: dict[str, list[tuple[str, str, str]]] = {}
    for operation in operations(doc):
        by_mod.setdefault(operation.x_mod, []).extend(operation.pairs)
    ledger = unreached_pairs()
    failures: list[str] = []
    for x_mod, pairs in sorted(by_mod.items()):
        diff = compare(pairs, declarations, ledger)
        if diff.missing_operations or diff.missing_pairs:
            failures.append(
                f"  x-mod={x_mod}:\n"
                f"    未被覆の操作:\n{_format_pairs(diff.missing_operations)}\n"
                f"    未被覆の対:\n{_format_pairs(diff.missing_pairs)}"
            )
    assert not failures, "x-mod 別の内訳で被覆が閉じていない:\n" + "\n".join(failures)
