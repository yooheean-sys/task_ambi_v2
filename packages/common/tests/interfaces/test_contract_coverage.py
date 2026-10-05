"""SC-001: 契約台帳 56 行 → 47 メソッドと宣言が 1 対 1（D25 で IF-51〜56 を追加）。

突合対象はメソッド名・引数の個数・戻り型・例外・同期／非同期。**引数名そのものは
pyright が強制する**ため pytest 側の突合対象にしない（design-sync.md の判断記録）。
"""

from __future__ import annotations

from pathlib import Path

from _support.declarations import all_protocol_methods, declared_protocols
from _support.design_docs import fold_contract_rows, load_contract_rows


def test_ledger_has_fifty_six_rows(design_root: Path) -> None:
    assert len(load_contract_rows(design_root)) == 56


def test_protocol_count_matches(design_root: Path) -> None:
    folded = fold_contract_rows(load_contract_rows(design_root))
    assert {m.protocol for m in folded} == set(declared_protocols())
    assert len(declared_protocols()) == 16


def test_method_count_matches(design_root: Path) -> None:
    folded = fold_contract_rows(load_contract_rows(design_root))
    assert len(folded) == 47
    assert len(all_protocol_methods()) == 47


def test_no_ledger_method_is_missing(design_root: Path) -> None:
    """台帳にあって宣言が無い＝0 件。"""
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    missing = [
        f"{m.protocol}.{m.method} ({'/'.join(m.if_ids)})"
        for m in folded
        if (m.protocol, m.method) not in declared
    ]
    assert missing == []


def test_no_declared_method_is_extra(design_root: Path) -> None:
    """宣言にあって台帳に無い＝0 件（FR-035 の先取り禁止）。"""
    folded = fold_contract_rows(load_contract_rows(design_root))
    expected = {(m.protocol, m.method) for m in folded}
    extra = [f"{p}.{n}" for (p, n) in all_protocol_methods() if (p, n) not in expected]
    assert extra == []


def test_arg_count_matches(design_root: Path) -> None:
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    mismatched = [
        (f"{m.protocol}.{m.method}", m.arg_count, declared[m.protocol, m.method].arg_count)
        for m in folded
        if declared[m.protocol, m.method].arg_count != m.arg_count
    ]
    assert mismatched == []


def test_return_type_matches(design_root: Path) -> None:
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    mismatched = [
        (
            f"{m.protocol}.{m.method}",
            m.return_type,
            declared[m.protocol, m.method].return_annotation,
        )
        for m in folded
        if declared[m.protocol, m.method].return_annotation != m.return_type
    ]
    assert mismatched == []


def test_sync_async_matches(design_root: Path) -> None:
    """41 メソッド中、非同期は `aon_outbound`・`aon_inbound` の 2 件のみ（FR-033）。"""
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    mismatched = [
        f"{m.protocol}.{m.method}"
        for m in folded
        if declared[m.protocol, m.method].is_async != m.is_async
    ]
    assert mismatched == []
    assert sorted(n for (_, n), d in declared.items() if d.is_async) == [
        "aon_inbound",
        "aon_outbound",
    ]


def test_exceptions_match(design_root: Path) -> None:
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    mismatched = [
        (f"{m.protocol}.{m.method}", set(m.exceptions), set(declared[m.protocol, m.method].raises))
        for m in folded
        if set(declared[m.protocol, m.method].raises) != set(m.exceptions)
    ]
    assert mismatched == []
