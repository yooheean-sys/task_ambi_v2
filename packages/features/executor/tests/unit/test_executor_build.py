"""`build()` の起動時検査（F12・T036・US6）。**表駆動**で書く。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from conftest import DEFAULT_EVENT_ALLOWLIST, make_config, make_stubs, use_config_file

from common.interfaces.executor import CrewExecutor, ExecutorFailure
from common.kernel.errors import AppError
from executor import ExecutorConfig, ExecutorHandle, ExecutorStartupError, build, config_path

SHIPPED = {
    "run_command_timeout_sec": 30,
    "max_iter": 20,
    "max_rpm": None,
    "event_allowlist": list(DEFAULT_EVENT_ALLOWLIST),
    "hook_catalog": [],
    "disable_crewai_telemetry": True,
}


def write_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict[str, Any]) -> Path:
    """一時 `config.json` を書いて差し替える。"""
    return use_config_file(monkeypatch, tmp_path, json.dumps(payload, ensure_ascii=False))


# --------------------------------------------------------------------------------------
# 起動が中断する 5 通り（SC-024）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({**SHIPPED, "unknown_key": 1}, "unknown_key"),
        ({k: v for k, v in SHIPPED.items() if k != "max_iter"}, "max_iter"),
        ({**SHIPPED, "run_command_timeout_sec": "30"}, "run_command_timeout_sec"),
        ({**SHIPPED, "event_allowlist": ["NoSuchEvent"]}, "NoSuchEvent"),
    ],
    ids=["unknown-key", "missing-value", "wrong-type", "unknown-event-class"],
)
def test_startup_aborts_and_names_the_absolute_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict[str, Any], needle: str
) -> None:
    """①〜④ は**見に行った絶対パス**を示して中断する（SC-024）。"""
    path = write_config(monkeypatch, tmp_path, payload)
    with pytest.raises(ExecutorStartupError) as excinfo:
        build(make_stubs().deps())
    message = str(excinfo.value)
    assert str(path) in message
    assert needle in message


def test_startup_aborts_when_a_builder_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """⑤ `TOOL_BUILDERS` の欠けは**欠けた名前を列挙**して中断する（FR-031・SC-011）。"""
    from common.kernel.enums import ToolName
    from executor.tools import TOOL_BUILDERS

    partial = {name: TOOL_BUILDERS[name] for name in list(TOOL_BUILDERS)[:5]}
    monkeypatch.setattr("executor.TOOL_BUILDERS", partial)
    with pytest.raises(ExecutorStartupError) as excinfo:
        build(make_stubs().deps(), config=make_config())
    message = str(excinfo.value)
    for name in ToolName:
        if name not in partial:
            assert name.value in message


def test_only_the_first_failing_stage_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**最初に失敗した段だけ**を報告する（設定が読めない段で止まる）。"""
    write_config(monkeypatch, tmp_path, {**SHIPPED, "unknown_key": 1, "event_allowlist": ["X"]})
    with pytest.raises(ExecutorStartupError) as excinfo:
        build(make_stubs().deps())
    assert "unknown_key" in str(excinfo.value)


def test_allowlist_entry_without_a_mapping_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """実在するが**対応する種類がコード側に無い**名前も中断する（FR-059。黙って捨てない）。"""
    write_config(
        monkeypatch, tmp_path, {**SHIPPED, "event_allowlist": ["AgentExecutionStartedEvent"]}
    )
    with pytest.raises(ExecutorStartupError) as excinfo:
        build(make_stubs().deps())
    assert "AgentExecutionStartedEvent" in str(excinfo.value)


def test_startup_error_is_not_in_the_contract_hierarchy() -> None:
    """`ExecutorStartupError` は `RuntimeError` 継承で契約例外を継承しない（FR-071・DIFF-06）。"""
    assert issubclass(ExecutorStartupError, RuntimeError)
    assert not issubclass(ExecutorStartupError, AppError)
    assert not issubclass(ExecutorStartupError, ExecutorFailure)


# --------------------------------------------------------------------------------------
# 正常系と差し替え
# --------------------------------------------------------------------------------------


def test_build_returns_a_handle_with_no_router() -> None:
    """`ExecutorHandle.router` は `None`（FR-011）。提供契約は `CrewExecutor` の 1 つだけ。"""
    handle = build(make_stubs().deps(), config=make_config())
    assert isinstance(handle, ExecutorHandle)
    assert handle.router is None
    crew_executor: CrewExecutor = handle.crew_executor  # 憲章 §3: Protocol 型を必ず注釈する
    assert hasattr(crew_executor, "run")


def test_shipped_config_json_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """出荷時の `config.json` が実際に読めて起動できる（10 件の実クラス名が全件実在する）。"""
    handle = build(make_stubs().deps())
    assert handle.router is None


def test_config_override_wins() -> None:
    """`build(deps, config=...)` の差し替えが効く。"""
    from executor.service import CrewExecutorImpl

    handle = build(make_stubs().deps(), config=make_config(run_command_timeout_sec=9))
    service = handle.crew_executor
    assert isinstance(service, CrewExecutorImpl)
    assert service.config.run_command_timeout_sec == 9


def test_config_path_is_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`config_path()` は**呼び出し時**に解決する（クラス定義時だと差し替えが黙って無効になる）。"""
    payload = {**SHIPPED, "run_command_timeout_sec": 99}
    write_config(monkeypatch, tmp_path, payload)
    assert ExecutorConfig().run_command_timeout_sec == 99


def test_shipped_config_path_points_into_the_module() -> None:
    """既定の `config_path()` はモジュール直下の `config.json`。"""
    path = config_path()
    assert path.name == "config.json"
    assert path.parent.name == "executor"
    assert path.is_file()


# --------------------------------------------------------------------------------------
# 設定は環境変数・`.env` で上書きできない（FR-072・US6 AC-8）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["run_command_timeout_sec", "RUN_COMMAND_TIMEOUT_SEC", "max_iter"])
def test_environment_cannot_override_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    """環境変数で設定を上書きできない（ソースは init と JSON だけ）。"""
    write_config(monkeypatch, tmp_path, SHIPPED)
    monkeypatch.setenv(key, "12345")
    settings = ExecutorConfig()
    assert settings.run_command_timeout_sec == 30
    assert settings.max_iter == 20


# --------------------------------------------------------------------------------------
# telemetry の既定（FR-070・SC-025）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED"])
def test_telemetry_defaults_are_set_when_absent(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """印が無ければ `true` を入れる。"""
    monkeypatch.delenv(name, raising=False)
    build(make_stubs().deps(), config=make_config(disable_crewai_telemetry=True))
    assert os.environ[name] == "true"


@pytest.mark.parametrize("name", ["CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED"])
def test_telemetry_defaults_never_overwrite(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """**`setdefault` であって代入ではない**（既に値があれば上書きしない＝ SC-025）。"""
    monkeypatch.setenv(name, "false")
    build(make_stubs().deps(), config=make_config(disable_crewai_telemetry=True))
    assert os.environ[name] == "false"


def test_telemetry_untouched_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定が偽なら印に触れない。"""
    for name in ("CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    build(make_stubs().deps(), config=make_config(disable_crewai_telemetry=False))
    assert "CREWAI_DISABLE_TELEMETRY" not in os.environ
    assert "OTEL_SDK_DISABLED" not in os.environ


def test_build_does_not_read_the_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build()` では `.env` を読まない（読む時点は**実行ごと**＝ FR-018）。"""
    read: list[str] = []

    def spy() -> dict[str, str | None]:
        read.append("read")
        return {}

    monkeypatch.setattr("executor.models.read_env_values", spy)
    build(make_stubs().deps(), config=make_config())
    assert read == []
