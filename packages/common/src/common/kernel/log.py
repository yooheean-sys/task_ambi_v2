"""ログ基盤（相関コンテキスト・共通項目・伏字フィルタ・ロガ工場）。

正本は `docs/design/phase5/common/p5_common_data_model.mmd` の
`namespace common_kernel_log_py`、契約は contracts/kernel.md §3。

相関 ID は `ContextVar` で持ち、**Protocol の引数には現れない**（FR-020・D08-d／D09-e）。
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from common.kernel.config import RootConfig

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
"""相関 ID の実体。触れてよいのは `CorrelationContext` の 3 メソッドだけ。"""

UNBOUND_CORRELATION_ID = "-"
"""未束縛のときに整形器が詰める値。"""

REDACTED = "***"
"""伏字の置換文字列。"""

EXTERNAL_LOGGERS = ("crewai", "litellm", "httpx")
"""水準を `max(WARNING, 設定水準)` に固定する外部ライブラリのロガ名（FR-025）。

**これらのライブラリを import しない**。ロガ名の文字列だけで設定する。
"""

_MAX_REDACT_DEPTH = 12
"""伏字の再帰の深さ上限。循環参照と極端な入れ子で落ちないための止め木。"""

_HANDLER_MARK = "_task_ambi_handler"
"""`configure` が自分で付けたハンドラを見分ける印（冪等性のため）。"""

# 「キー = 値」対。research R-12 が実測した骨格。
# glob を正規表現へ直訳しない（`.*` が貪欲に食い、1 行に複数の秘密があると先頭側が漏れる）。
_PAIR_RE = re.compile(
    r"""(["']?)([A-Za-z_][A-Za-z0-9_.\-]*)\1(\s*[=:]\s*)"""
    r"""('[^']*'|"[^"]*"|(?:Bearer|Basic|Token|Digest)\s+\S+|[^\s,;{}\(\)\[\]"']+)""",
    re.IGNORECASE,
)

_RESERVED_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)
"""`logging` が自分で詰めるキー。追加項目として扱わない。"""

_PROMOTED_RECORD_KEYS = ("kickoff_id", "issue_id")
"""`LogFields` の専用項目に昇格させる追加項目。"""


class CorrelationContext:
    """相関 ID の束縛（`@staticmethod` だけの名前空間クラス）。"""

    @staticmethod
    def bind(correlation_id: str) -> Token[str | None]:
        """相関 ID を束縛し、`reset` に渡す `Token` を返す。"""
        return _correlation_id.set(correlation_id)

    @staticmethod
    def reset(token: Token[str | None]) -> None:
        """`bind` が返した `Token` で直前の値へ戻す。"""
        _correlation_id.reset(token)

    @staticmethod
    def current() -> str | None:
        """現在の相関 ID。未束縛なら `None`（落ちない）。"""
        return _correlation_id.get()


class LogFields(BaseModel):
    """構造化ログの共通項目（FR-022）。"""

    model_config = ConfigDict(frozen=True)

    ts: datetime
    level: str
    module: str
    correlation_id: str
    event: str
    kickoff_id: str | None = None
    issue_id: str | None = None
    extra: dict[str, object] = {}


class RedactFilter(logging.Filter):
    """`redact_keys` に一致するキーの値を本文と追加項目の双方で伏字にする（R-12）。"""

    def __init__(self, redact_keys: list[str]) -> None:
        """伏字対象のパターンを受け取る（**設定から読む**。コードに直書きしない）。"""
        super().__init__()
        self._patterns = tuple(p.lower() for p in redact_keys if p)

    def _is_secret(self, key: str) -> bool:
        """キー名を `fnmatch` で照合する（小文字化＝大文字小文字を区別しない）。"""
        lowered = key.lower()
        return any(fnmatch.fnmatch(lowered, pattern) for pattern in self._patterns)

    def _redact_text(self, text: str) -> str:
        """本文の「キー = 値」対のうち、キーが一致するものの値を伏字にする。"""

        def replace(match: re.Match[str]) -> str:
            quote, key, separator = match.group(1), match.group(2), match.group(3)
            if not self._is_secret(key):
                return match.group(0)
            return f"{quote}{key}{quote}{separator}{REDACTED}"

        return _PAIR_RE.sub(replace, text)

    def _redact_value(self, key: str, value: object, depth: int) -> object:
        """追加項目を再帰的に伏字化する。深さ上限で打ち切る（循環参照の止め木）。"""
        if self._is_secret(key):
            return REDACTED
        if depth >= _MAX_REDACT_DEPTH:
            return value
        if isinstance(value, dict):
            items = cast("dict[object, object]", value)
            return {k: self._redact_value(str(k), v, depth + 1) for k, v in items.items()}
        if isinstance(value, list):
            entries = cast("list[object]", value)
            return [self._redact_value(key, v, depth + 1) for v in entries]
        if isinstance(value, str):
            return self._redact_text(value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        """レコードを伏字化する。常に `True`（レコードを落とさない）。"""
        record.msg = self._redact_text(record.getMessage())
        record.args = None
        for key in list(vars(record)):
            if key in _RESERVED_RECORD_KEYS:
                continue
            setattr(record, key, self._redact_value(key, getattr(record, key), 0))
        return True


class LoggerFactory:
    """全モジュールの唯一のログ入口（`@staticmethod` だけの名前空間クラス）。"""

    @staticmethod
    def configure(config: RootConfig) -> None:
        """起動時に 1 回呼ぶ。冪等（2 回呼んでもハンドラが増えない）。"""
        root = logging.getLogger()
        root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]

        handler = logging.StreamHandler(sys.stdout)
        setattr(handler, _HANDLER_MARK, True)
        handler.setFormatter(_JsonFormatter() if config.log.format == "json" else _TextFormatter())
        handler.addFilter(RedactFilter(config.log.redact_keys))
        root.addHandler(handler)

        level = logging.getLevelNamesMapping()[config.log.level]
        root.setLevel(level)
        for name in EXTERNAL_LOGGERS:
            logging.getLogger(name).setLevel(max(logging.WARNING, level))

    @staticmethod
    def get_logger(module: str) -> logging.Logger:
        """モジュール名でロガを取得する。"""
        return logging.getLogger(module)


def _to_fields(record: logging.LogRecord) -> LogFields:
    """`LogRecord` を共通項目へ写す。**相関 ID は整形器側で読む**（フィルタではない）。"""
    extra = {
        key: value
        for key, value in vars(record).items()
        if key not in _RESERVED_RECORD_KEYS and key not in _PROMOTED_RECORD_KEYS
    }
    promoted = {key: getattr(record, key, None) for key in _PROMOTED_RECORD_KEYS}
    return LogFields(
        ts=datetime.fromtimestamp(record.created, tz=UTC),
        level=record.levelname,
        module=record.name,
        correlation_id=CorrelationContext.current() or UNBOUND_CORRELATION_ID,
        event=record.getMessage(),
        kickoff_id=_as_optional_str(promoted["kickoff_id"]),
        issue_id=_as_optional_str(promoted["issue_id"]),
        extra=extra,
    )


def _as_optional_str(value: object) -> str | None:
    return None if value is None else str(value)


class _JsonFormatter(logging.Formatter):
    """`LogFields` を JSON 1 行にする。"""

    def format(self, record: logging.LogRecord) -> str:
        """1 レコードを JSON 1 行へ整形する。"""
        fields = _to_fields(record)
        return json.dumps(fields.model_dump(mode="json"), ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    """`LogFields` を人が読む 1 行にする。"""

    def format(self, record: logging.LogRecord) -> str:
        """1 レコードを人が読む 1 行へ整形する。"""
        fields = _to_fields(record)
        parts = [
            fields.ts.isoformat(),
            fields.level,
            fields.module,
            f"cid={fields.correlation_id}",
            fields.event,
        ]
        if fields.kickoff_id is not None:
            parts.append(f"kickoff_id={fields.kickoff_id}")
        if fields.issue_id is not None:
            parts.append(f"issue_id={fields.issue_id}")
        if fields.extra:
            parts.append(json.dumps(fields.extra, ensure_ascii=False, default=str))
        return " ".join(parts).replace("\n", " ")
