"""構造化ログの観測手段（相関 ID の貫通を見る）。憲章 §6・各クラスタ §6 の「構造化ログ」行。

**テストダブルではない**（規約 §4 の 3 種には数えない）。被検体の依存を差し替えるものではなく、
実配線がそのまま出したログを**傍で控える**だけの観測点である。

なぜ `caplog` で足りないか（現物の根拠）: `common/kernel/log.py` は相関 ID を `LogRecord` に
載せず、**整形器の側**で `CorrelationContext.current()` を読む（`_to_fields`）。したがって
`caplog.records` を見ても相関 ID は入っていない。レコードが**出た瞬間**に束縛されていた値を
控えるには、ルートロガへハンドラを 1 枚足すしかない（`integration/test_smoke_correlation.py`
の `_CorrelationRecordingHandler` と同じ形）。

4 クラスタ（02 #16・03 #15・04 #16・05 #12）が同じものを要るので `_support/` に集約する
（クラスタ内に補助 module を置かない＝規約 §2）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.kernel.log import CorrelationContext

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class LoggedLine:
    """1 レコードぶんの観測結果。"""

    logger: str
    """ロガ名（`settings.service`・`board.repository` 等）。"""

    event: str
    """本文（`LogFields.event` に写るもの）。"""

    correlation_id: str | None
    """そのレコードが出た瞬間に束縛されていた相関 ID（未束縛なら `None`）。"""


class CorrelationRecorder(logging.Handler):
    """レコードごとに「ロガ名・本文・束縛中の相関 ID」を控えるハンドラ。"""

    def __init__(self) -> None:
        """記録用の空の一覧を用意する。"""
        super().__init__()
        self.lines: list[LoggedLine] = []

    def emit(self, record: logging.LogRecord) -> None:
        """束縛中の相関 ID をロガ名・本文と一緒に控える。"""
        self.lines.append(
            LoggedLine(
                logger=record.name,
                event=record.getMessage(),
                correlation_id=CorrelationContext.current(),
            )
        )

    def of(self, logger_prefix: str) -> list[LoggedLine]:
        """ロガ名が接頭辞に一致する行だけを取り出す。

        Args:
            logger_prefix: `settings`・`board` のような接頭辞。

        Returns:
            一致した行（出た順）。
        """
        return [line for line in self.lines if line.logger.startswith(logger_prefix)]

    def correlation_ids(self, logger_prefix: str) -> set[str | None]:
        """ロガ名が接頭辞に一致する行の相関 ID の集合。"""
        return {line.correlation_id for line in self.of(logger_prefix)}


def recording_correlation() -> Iterator[CorrelationRecorder]:
    """ルートロガへ観測点を 1 枚足す（終了時に必ず外す）。

    fixture の本体としてそのまま使える形にしてある（`yield` を 1 回だけ行う）。

    Yields:
        観測結果を持つハンドラ。
    """
    recorder = CorrelationRecorder()
    root = logging.getLogger()
    root.addHandler(recorder)
    previous_level = root.level
    if previous_level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        yield recorder
    finally:
        root.removeHandler(recorder)
        root.setLevel(previous_level)
