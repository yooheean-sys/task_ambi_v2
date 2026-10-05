"""相関 ID の発番と束縛（`trigger/correlation.py`。C-1・R-01・011 SD-21）。

plan (ix) の一覧に無い 1 本（tasks の「plan (ix) との差 1 件」）。憲章 §7-6 の「テスト → 本体」を
`correlation.py` に対しても成立させるために置く。

**`api.correlation` を import しない**（層契約: features は apps を参照できない）。同型だが
別実装であることの根拠は `correlation.py` の docstring にある。
"""

from __future__ import annotations

import pytest

from common.kernel.log import CorrelationContext
from trigger.correlation import correlation_scope, new_correlation_id

HEX32 = 32


def test_new_correlation_id_is_32_lowercase_hex_and_differs_every_time() -> None:
    """`uuid4().hex` と同じ形（32 桁の小文字 16 進）で、毎回異なる。"""
    values = [new_correlation_id() for _ in range(50)]

    for value in values:
        assert len(value) == HEX32
        assert all(char in "0123456789abcdef" for char in value), value
    assert len(set(values)) == len(values), "発番が重複した"


def test_correlation_scope_binds_and_always_resets() -> None:
    """`CorrelationContext.current()` に束縛し、**`finally` で必ず解く**（R-12）。"""
    before = CorrelationContext.current()

    with correlation_scope() as correlation_id:
        assert CorrelationContext.current() == correlation_id

    assert CorrelationContext.current() == before


def test_correlation_scope_resets_even_when_the_body_raises() -> None:
    """本体が落ちても束縛は解ける（**専用ワーカーはスレッドを使い回す**＝ R-12）。"""
    before = CorrelationContext.current()

    with pytest.raises(RuntimeError), correlation_scope():
        msg = "意図的な失敗"
        raise RuntimeError(msg)

    assert CorrelationContext.current() == before


def test_correlation_scope_accepts_an_explicit_id() -> None:
    """明示の `correlation_id` を渡せる（入口が既に値を持っている場合）。"""
    with correlation_scope("abc") as correlation_id:
        assert correlation_id == "abc"
        assert CorrelationContext.current() == "abc"


def test_correlation_scope_nests_and_restores_the_outer_value() -> None:
    """入れ子で内側が勝ち、抜けると外側が復元される。"""
    with correlation_scope("outer"):
        assert CorrelationContext.current() == "outer"
        with correlation_scope("inner"):
            assert CorrelationContext.current() == "inner"
        assert CorrelationContext.current() == "outer"
