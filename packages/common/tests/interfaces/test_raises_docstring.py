"""FR-033c: 47 メソッドの `Raises:` 節が台帳の「例外」列と 1 対 1。"""

from __future__ import annotations

from pathlib import Path

from _support.declarations import all_protocol_methods
from _support.design_docs import fold_contract_rows, load_contract_rows


def test_every_method_has_a_docstring() -> None:
    declared = all_protocol_methods()
    assert len(declared) == 47
    missing = [f"{p}.{n}" for (p, n), d in declared.items() if not d.doc]
    assert missing == []


def test_raises_section_matches_the_ledger(design_root: Path) -> None:
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    mismatched = [
        (
            f"{m.protocol}.{m.method}",
            sorted(m.exceptions),
            sorted(declared[m.protocol, m.method].raises),
        )
        for m in folded
        if sorted(declared[m.protocol, m.method].raises) != sorted(m.exceptions)
    ]
    assert mismatched == []


def test_methods_without_exceptions_have_no_raises_section(design_root: Path) -> None:
    """台帳の「例外」列が `—` の行には `Raises:` 節を置かない。"""
    folded = fold_contract_rows(load_contract_rows(design_root))
    declared = all_protocol_methods()
    offenders = [
        f"{m.protocol}.{m.method}"
        for m in folded
        if not m.exceptions and declared[m.protocol, m.method].raises
    ]
    assert offenders == []
    # 9 = 47 メソッド中で台帳の例外列が `—` の行。IF-31/32（check_project_folders）は
    # 2026-08-24（`004` vault・design-sync B-4）に `VaultPathViolation` を補記したため 9 から 8 に
    # 減り、D25 で IF-56（`extract_repo_tokens`）が加わって 9 に戻った
    assert sum(1 for m in folded if not m.exceptions) == 9
