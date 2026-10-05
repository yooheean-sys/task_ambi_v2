"""SC-006: `redact_keys` に一致するキーの値が本文と追加項目の双方で伏字になる。

**過剰マスクがない**ことも検査する（伏字漏れ 0 件だけでは片側しか見ない）。
パターンは**設定から読む**（コードに直書きしない＝FR-024・CFG-19 が正本）。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from common.kernel.config import RootConfig
from common.kernel.log import REDACTED, RedactFilter

PATTERNS = ["*_API_KEY*", "Authorization", "api_key"]


def _record(message: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord("mod", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _filtered(message: str, **extra: Any) -> logging.LogRecord:
    record = _record(message, **extra)
    assert RedactFilter(PATTERNS).filter(record) is True
    return record


# --- (a) 本文 ---------------------------------------------------------------


def test_message_secret_is_redacted() -> None:
    assert "sk-ABCDEF123" not in _filtered("api_key=sk-ABCDEF123").getMessage()


def test_message_keeps_the_key_name() -> None:
    assert f"api_key={REDACTED}" in _filtered("api_key=sk-ABCDEF123").getMessage()


def test_lazy_percent_formatting_is_redacted() -> None:
    """`%s` 遅延整形でも伏字になる（整形後の本文を判定する）。"""
    record = logging.LogRecord("mod", logging.INFO, __file__, 1, "api_key=%s", ("sk-1",), None)
    assert RedactFilter(PATTERNS).filter(record) is True
    assert "sk-1" not in record.getMessage()


# --- (b) 追加項目 -----------------------------------------------------------


def test_extra_secret_is_redacted() -> None:
    record = _filtered("呼び出し", api_key="sk-ABCDEF123")
    assert getattr(record, "api_key") == REDACTED  # noqa: B009  # LogRecord の追加項目は動的属性


def test_nested_dict_two_levels_is_redacted() -> None:
    record = _filtered("呼び出し", payload={"outer": {"api_key": "sk-1", "keep": "ok"}})
    payload: Any = getattr(record, "payload")  # noqa: B009  # LogRecord の追加項目は動的属性
    assert payload["outer"]["api_key"] == REDACTED
    assert payload["outer"]["keep"] == "ok"


def test_dict_inside_list_is_redacted() -> None:
    record = _filtered("呼び出し", items=[{"api_key": "sk-1"}])
    items: Any = getattr(record, "items")  # noqa: B009  # LogRecord の追加項目は動的属性
    assert items[0]["api_key"] == REDACTED


# --- (c) 大文字小文字を区別しない -------------------------------------------


@pytest.mark.parametrize("key", ["API_KEY", "Api_Key", "authorization", "AUTHORIZATION"])
def test_case_insensitive_key_matching(key: str) -> None:
    assert "secret" not in _filtered(f"{key}=secret").getMessage()


# --- (e) 認証スキームつきの値 -----------------------------------------------


def test_bearer_token_body_is_redacted() -> None:
    """`Authorization: Bearer <token>` の**トークン本体**が残らない。"""
    message = _filtered("Authorization: Bearer zzz-secret").getMessage()
    assert "zzz-secret" not in message


def test_quoted_key_inside_a_dict_literal_is_redacted() -> None:
    message = _filtered('headers={"Authorization": "Bearer zzz-secret"}').getMessage()
    assert "zzz-secret" not in message


# --- (f) 1 行に複数の秘密 ---------------------------------------------------


def test_multiple_secrets_on_one_line_are_all_redacted() -> None:
    """先頭側も伏字になる（glob を正規表現へ直訳すると先頭側が漏れる＝R-12 の実測）。"""
    message = _filtered("api_key=sk-ABCDEF123 と LLM_API_KEY_LOCAL='qqq' を使う").getMessage()
    assert "sk-ABCDEF123" not in message
    assert "qqq" not in message


# --- 過剰マスクがないこと ---------------------------------------------------


def test_non_secret_pairs_are_untouched() -> None:
    message = _filtered("issue_id=bd-12 kickoff_id=k-1 stage=child").getMessage()
    assert message == "issue_id=bd-12 kickoff_id=k-1 stage=child"


def test_line_without_secrets_is_untouched() -> None:
    assert _filtered("crew を起動した").getMessage() == "crew を起動した"


def test_non_secret_extra_is_untouched() -> None:
    record = _filtered("呼び出し", issue_id="bd-12")
    assert getattr(record, "issue_id") == "bd-12"  # noqa: B009  # LogRecord の追加項目は動的属性


# --- 設定から読むこと -------------------------------------------------------


def test_patterns_come_from_configuration() -> None:
    """コード直書きでない。空リストを渡せば何も伏字にならない。"""
    record = _record("api_key=sk-1")
    assert RedactFilter([]).filter(record) is True
    assert "sk-1" in record.getMessage()


def test_custom_pattern_is_honoured() -> None:
    record = _record("my_token=abc")
    assert RedactFilter(["my_token"]).filter(record) is True
    assert "abc" not in record.getMessage()


def test_default_patterns_match_the_root_config() -> None:
    """既定パターンの正本は CFG-19（ルート `config.json`）。"""
    assert RootConfig().log.redact_keys == PATTERNS


def test_empty_and_duplicate_patterns_are_tolerated() -> None:
    record = _record("api_key=sk-1")
    assert RedactFilter(["api_key", "api_key", ""]).filter(record) is True
    assert "sk-1" not in record.getMessage()


def test_deeply_nested_structure_terminates() -> None:
    """入れ子の深さ上限を超えても落ちない。"""
    deep: dict[str, Any] = {"api_key": "sk-1"}
    for _ in range(50):
        deep = {"level": deep}
    record = _filtered("呼び出し", payload=deep)
    assert getattr(record, "payload") is not None  # noqa: B009  # LogRecord の追加項目は動的属性


def test_self_referencing_structure_terminates() -> None:
    """循環参照でも落ちない。"""
    loop: dict[str, Any] = {"api_key": "sk-1"}
    loop["self"] = loop
    record = _record("呼び出し", payload=loop)
    assert RedactFilter(PATTERNS).filter(record) is True
