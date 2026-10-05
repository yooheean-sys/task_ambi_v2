"""US5: 相関 ID の発番・受信値の正規化・HTTP を通らない入口での束縛。

検証行: X-8・X-9（FR-024・FR-047・FR-048）。正規化の規則は contracts/correlation-id.md §2。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import pytest

from api.correlation import (
    MAX_CORRELATION_ID_LENGTH,
    correlation_scope,
    new_correlation_id,
    normalize_correlation_id,
)
from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from collections.abc import Iterator

CORRELATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
"""FR-047: UUID v4 を 32 桁の小文字 16 進（ハイフン無し）で表した形式。"""


class _CorrelationRecordingHandler(logging.Handler):
    """1 レコードごとに、そのとき束縛されている相関 ID を控える。

    整形器（`common.kernel.log`）が読むのと同じ `CorrelationContext.current()` を見るので、
    「ログ行に載る値」をそのまま観測できる。
    """

    def __init__(self) -> None:
        """記録用の空の一覧を用意する。"""
        super().__init__()
        self.seen: list[str | None] = []

    def emit(self, record: logging.LogRecord) -> None:
        """束縛中の相関 ID を控える（レコード自体は捨てる）。"""
        self.seen.append(CorrelationContext.current())


@pytest.fixture
def recording_handler() -> Iterator[_CorrelationRecordingHandler]:
    """ルートロガに記録用ハンドラを付ける（終了時に外す）。"""
    handler = _CorrelationRecordingHandler()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.fixture(autouse=True)
def ensure_unbound_correlation_context() -> Iterator[None]:
    """各テストの前後で相関 ID が未束縛であることを担保する。"""
    assert CorrelationContext.current() is None
    yield
    assert CorrelationContext.current() is None


def test_x8_new_correlation_id_matches_the_declared_format() -> None:
    """X-8・FR-047: `^[0-9a-f]{32}$` に一致する。"""
    assert CORRELATION_ID_PATTERN.match(new_correlation_id()) is not None


def test_x8_new_correlation_id_does_not_repeat_in_100_draws() -> None:
    """X-8: 100 回連続で重複しない。"""
    drawn = [new_correlation_id() for _ in range(100)]

    assert len(set(drawn)) == 100


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "a" * (MAX_CORRELATION_ID_LENGTH + 1),
        "bad\nvalue",
        "bad\rvalue",
        "bad\tvalue",
        "bad\x00value",
        "bad\x7fvalue",
    ],
    ids=["none", "empty", "too-long", "lf", "cr", "tab", "nul", "del"],
)
def test_rejected_values_are_replaced_by_a_new_id(raw: str | None) -> None:
    """contracts §2・FR-048: 空・128 文字超・制御文字を含む値は引き継がず新規発番する。"""
    normalized = normalize_correlation_id(raw)

    assert CORRELATION_ID_PATTERN.match(normalized) is not None
    assert normalized != raw


@pytest.mark.parametrize(
    "raw",
    [
        "abc-123",
        "ABC-123",
        "with space",
        "日本語の識別子",
        "a" * MAX_CORRELATION_ID_LENGTH,
        "0123456789abcdef0123456789abcdef",
    ],
    ids=["hyphenated", "uppercase", "with-space", "non-ascii", "at-limit", "hex32"],
)
def test_accepted_values_are_carried_over_as_is(raw: str) -> None:
    """FR-048: 形式の一致は求めない。空白を含む値も落とさない（`str.isprintable()` を使わない）。"""
    assert normalize_correlation_id(raw) == raw


def test_length_is_counted_in_characters_not_bytes() -> None:
    """contracts §2: 長さは**文字数**で数える。"""
    multibyte = "あ" * MAX_CORRELATION_ID_LENGTH

    assert len(multibyte.encode("utf-8")) > MAX_CORRELATION_ID_LENGTH
    assert normalize_correlation_id(multibyte) == multibyte


def test_x9_correlation_scope_binds_and_releases(
    recording_handler: _CorrelationRecordingHandler,
) -> None:
    """X-9・FR-024: HTTP を通らずに同じ ID がログへ載り、抜けると解除される。"""
    logger = logging.getLogger("test.correlation")

    with correlation_scope() as correlation_id:
        assert CORRELATION_ID_PATTERN.match(correlation_id) is not None
        assert CorrelationContext.current() == correlation_id
        logger.info("最初の行")
        logger.info("2 行目")

    assert CorrelationContext.current() is None
    assert recording_handler.seen == [correlation_id, correlation_id]


def test_correlation_scope_accepts_a_given_id() -> None:
    """FR-024: 与えた ID をそのまま束縛する（発番し直さない）。"""
    with correlation_scope("given-id") as correlation_id:
        assert correlation_id == "given-id"
        assert CorrelationContext.current() == "given-id"


def test_correlation_scope_releases_even_on_exception() -> None:
    """例外が出た経路でも束縛が解除される（`finally` を通る）。"""
    with pytest.raises(RuntimeError), correlation_scope():
        raise RuntimeError("処理中の失敗")

    assert CorrelationContext.current() is None


def test_correlation_scope_nests_and_restores_the_outer_id() -> None:
    """入れ子にしても外側の ID に戻る（`Token` で戻すため）。"""
    with correlation_scope("outer") as outer:
        with correlation_scope("inner") as inner:
            assert CorrelationContext.current() == inner
        assert CorrelationContext.current() == outer
