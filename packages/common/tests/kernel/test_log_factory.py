"""`LoggerFactory` の約束（contracts/kernel.md §3-3）。"""

from __future__ import annotations

import ast
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO, cast

import pytest

from common.kernel.config import RootConfig
from common.kernel.log import (
    EXTERNAL_LOGGERS,
    UNBOUND_CORRELATION_ID,
    CorrelationContext,
    LoggerFactory,
)


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """ルートロガの状態を各テストの前後で戻す（`configure` は大域状態を触るため）。"""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    external = {name: logging.getLogger(name).level for name in EXTERNAL_LOGGERS}
    yield
    root.handlers = handlers
    root.setLevel(level)
    for name, saved in external.items():
        logging.getLogger(name).setLevel(saved)


def _config(**overrides: Any) -> RootConfig:
    base = RootConfig()
    log = base.log.model_copy(update=overrides) if overrides else base.log
    return base.model_copy(update={"log": log})


def _emit(capsys: pytest.CaptureFixture[str], message: str, **extra: Any) -> str:
    LoggerFactory.get_logger("demo").info(message, extra=extra)
    for handler in logging.getLogger().handlers:
        handler.flush()
    return capsys.readouterr().out


# --- (a) 相関 ID が自動で載る -----------------------------------------------


def test_correlation_id_is_attached(capsys: pytest.CaptureFixture[str]) -> None:
    LoggerFactory.configure(_config())
    token = CorrelationContext.bind("c-1")
    try:
        payload = json.loads(_emit(capsys, "started").strip())
    finally:
        CorrelationContext.reset(token)
    assert payload["correlation_id"] == "c-1"


def test_unbound_correlation_id_is_a_dash(capsys: pytest.CaptureFixture[str]) -> None:
    """未束縛は整形器が `"-"` を詰める（例外にしない）。"""
    LoggerFactory.configure(_config())
    payload = json.loads(_emit(capsys, "started").strip())
    assert payload["correlation_id"] == UNBOUND_CORRELATION_ID


def test_common_fields_are_present(capsys: pytest.CaptureFixture[str]) -> None:
    LoggerFactory.configure(_config())
    payload = json.loads(_emit(capsys, "started", issue_id="bd-1").strip())
    assert set(payload) == {
        "ts",
        "level",
        "module",
        "correlation_id",
        "event",
        "kickoff_id",
        "issue_id",
        "extra",
    }
    assert payload["module"] == "demo"
    assert payload["event"] == "started"
    assert payload["issue_id"] == "bd-1"


# --- (b) 水準・形式が設定から読まれる ---------------------------------------


def test_format_text_is_a_single_line(capsys: pytest.CaptureFixture[str]) -> None:
    LoggerFactory.configure(_config(format="text"))
    out = _emit(capsys, "started").strip()
    assert out.count("\n") == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert "started" in out


def test_level_is_read_from_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    LoggerFactory.configure(_config(level="WARNING"))
    LoggerFactory.get_logger("demo").info("見えない")
    assert capsys.readouterr().out == ""
    LoggerFactory.get_logger("demo").warning("見える")
    assert "見える" in capsys.readouterr().out


def test_secrets_are_redacted_on_the_way_out(capsys: pytest.CaptureFixture[str]) -> None:
    LoggerFactory.configure(_config())
    assert "sk-1" not in _emit(capsys, "api_key=sk-1")


# --- (c) configure は冪等 ---------------------------------------------------


def test_configure_is_idempotent() -> None:
    LoggerFactory.configure(_config())
    first = len(logging.getLogger().handlers)
    LoggerFactory.configure(_config())
    LoggerFactory.configure(_config())
    assert len(logging.getLogger().handlers) == first


def test_configure_writes_to_stdout() -> None:
    LoggerFactory.configure(_config())
    handlers = [
        cast("logging.StreamHandler[TextIO]", handler)
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
    ]
    assert any(handler.stream is sys.stdout for handler in handlers)


# --- (d) 外部ライブラリのロガ -----------------------------------------------


@pytest.mark.parametrize("name", EXTERNAL_LOGGERS)
def test_external_loggers_are_pinned_to_warning(name: str) -> None:
    LoggerFactory.configure(_config(level="DEBUG"))
    assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.parametrize("name", EXTERNAL_LOGGERS)
def test_external_child_loggers_inherit_the_pin(name: str) -> None:
    """子ロガ（`crewai.tools` 等）にも効く。"""
    LoggerFactory.configure(_config(level="DEBUG"))
    assert logging.getLogger(f"{name}.child").isEnabledFor(logging.DEBUG) is False


@pytest.mark.parametrize("name", EXTERNAL_LOGGERS)
def test_external_loggers_never_go_below_warning(name: str) -> None:
    LoggerFactory.configure(_config(level="ERROR"))
    assert logging.getLogger(name).level == logging.ERROR


# --- (e) 3 ライブラリを import していない -----------------------------------


def test_kernel_does_not_import_the_external_libraries(repo_root: Path) -> None:
    """ロガ名の文字列だけで設定する（FR-025・contracts/kernel.md §3-3 約束 4）。

    対象は `LoggerFactory` の所在である `common/kernel/`。`common/interfaces/monitor.py`
    の `httpx` は FR-036 が認める**型検査時のみ**の参照で、別の受け入れ条件
    （`tests/interfaces/test_boundaries.py`）が担う。
    """
    kernel = repo_root / "packages/common/src/common/kernel"
    banned = {"crewai", "litellm", *EXTERNAL_LOGGERS}
    offenders: list[str] = []
    for path in kernel.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".", 1)[0]}
            else:
                continue
            offenders.extend(f"{path.relative_to(kernel)}: {n}" for n in sorted(roots & banned))
    assert offenders == []


def test_logger_factory_names_the_external_loggers_as_strings() -> None:
    """3 件は文字列定数として持つ（import ではない）。"""
    assert EXTERNAL_LOGGERS == ("crewai", "litellm", "httpx")
