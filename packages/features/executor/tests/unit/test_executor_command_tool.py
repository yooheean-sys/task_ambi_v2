"""コマンドの 1 道具（F09・T020・US4）。**表駆動**で書く。"""

from __future__ import annotations

import subprocess  # noqa: S404  # 検証で `subprocess.run` を差し替えるため型と例外を参照する
from typing import Any

import pytest
from conftest import AuthorizerMode, make_bundle, make_config, make_folders, make_member, make_stubs

from common.kernel.enums import ToolName
from executor.models import RunSession
from executor.tools import TOOL_BUILDERS
from executor.tools.base import GuardedTool, ToolContext

ARGV = {"pytest": ["uv", "run", "pytest"], "ruff": ["uv", "run", "ruff", "check", "."]}


def build_tool(
    *,
    authorizer_mode: AuthorizerMode = "allow",
    deny_objs: frozenset[str] = frozenset(),
    timeout_sec: int = 30,
) -> tuple[GuardedTool, Any]:
    """`run_command` とスタブ束を組む。"""
    stubs = make_stubs(
        authorizer_mode=authorizer_mode,
        deny_objs=deny_objs,
        argv_by_name=dict(ARGV),
        known_names=tuple(ARGV),
    )
    member = make_member()
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders())
    context = ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(run_command_timeout_sec=timeout_sec),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(),
    )
    return TOOL_BUILDERS[ToolName.run_command](context), stubs


class FakeCompleted:
    """`subprocess.run` の戻りの代わり。"""

    def __init__(self, returncode: int = 0, stdout: str = "OK", stderr: str = "") -> None:
        """終了コードと出力を保持する。"""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def patch_run(monkeypatch: pytest.MonkeyPatch, result: object) -> list[dict[str, Any]]:
    """`subprocess.run` を差し替えて呼び出しを記録する（**実プロセスを起こさない**）。"""
    seen: list[dict[str, Any]] = []

    def fake(argv: list[str], **kwargs: Any) -> object:
        seen.append({"argv": argv, **kwargs})
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr("executor.tools.command_tools.subprocess.run", fake)
    return seen


# --------------------------------------------------------------------------------------
# 二段許可（FR-037）
# --------------------------------------------------------------------------------------


DENIAL_CASES: list[tuple[AuthorizerMode, frozenset[str], int]] = [
    ("deny_objs", frozenset({"tool:run_command"}), 1),
    ("deny_objs", frozenset({"cmd:pytest"}), 2),
    ("deny", frozenset(), 1),
]


@pytest.mark.parametrize(
    ("mode", "deny_objs", "expected_enforce"),
    DENIAL_CASES,
    ids=["deny-tool", "deny-cmd", "deny-all"],
)
def test_denied_never_spawns_a_process(
    monkeypatch: pytest.MonkeyPatch,
    mode: AuthorizerMode,
    deny_objs: frozenset[str],
    expected_enforce: int,
) -> None:
    """**いずれか一方でも否なら `subprocess.run` が 0 回**（FR-037）。"""
    tool, stubs = build_tool(authorizer_mode=mode, deny_objs=deny_objs)
    seen = patch_run(monkeypatch, FakeCompleted())
    message = tool.run(name="pytest")
    assert message.startswith("ERROR:")
    assert seen == []
    assert stubs.log.count("Authorizer.enforce") == expected_enforce


def test_allowed_runs_with_the_resolved_fixed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve_command(name)` の**固定 argv** を `shell=False`・`timeout` 付きで実行する。"""
    tool, stubs = build_tool(timeout_sec=7)
    seen = patch_run(monkeypatch, FakeCompleted(stdout="通った"))
    assert "通った" in tool.run(name="pytest")
    (call,) = seen
    assert call["argv"] == ARGV["pytest"]
    assert call["shell"] is False
    assert call["timeout"] == 7
    assert call["capture_output"] is True
    assert call["text"] is True
    assert call["check"] is False
    objs = [entry.kwargs["obj"] for entry in stubs.log.of("Authorizer.enforce")]
    assert objs == ["tool:run_command", "cmd:pytest"]


# --------------------------------------------------------------------------------------
# 引数列を渡す手段が無い（FR-045・SC-014）
# --------------------------------------------------------------------------------------


def test_args_schema_has_only_name() -> None:
    """`args_schema` に `name` 以外の欄が **0 件**（引数列を渡す手段を持たない）。"""
    tool, _ = build_tool()
    assert set(tool.args_schema.model_fields) == {"name"}


# --------------------------------------------------------------------------------------
# 3 通りの異常はすべて戻り値（FR-047・SC-015）
# --------------------------------------------------------------------------------------


def test_unknown_command_lists_available_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """台帳に無い名前 → `ERROR: '<name>' は許可リストに無い。利用可能: …`。"""
    tool, _ = build_tool()
    seen = patch_run(monkeypatch, FakeCompleted())
    message = tool.run(name="rm")
    assert message.startswith("ERROR: 'rm' は許可リストに無い。")
    assert "pytest" in message
    assert seen == []


def test_timeout_is_reported_as_a_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """時間切れ → `ERROR: <N> 秒を超えたので打ち切った`（例外にしない）。"""
    tool, _ = build_tool(timeout_sec=5)
    patch_run(monkeypatch, subprocess.TimeoutExpired(cmd=ARGV["pytest"], timeout=5))
    assert tool.run(name="pytest") == "ERROR: 5 秒を超えたので打ち切った"


def test_nonzero_returncode_is_reported_as_a_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`rc != 0` → `ERROR: rc=<rc> / stderr(先頭): …`。"""
    tool, _ = build_tool()
    patch_run(monkeypatch, FakeCompleted(returncode=2, stdout="", stderr="壊れた"))
    message = tool.run(name="pytest")
    assert message.startswith("ERROR: rc=2 / stderr(先頭): ")
    assert "壊れた" in message


# --------------------------------------------------------------------------------------
# 出力の切り詰め（P-11）
# --------------------------------------------------------------------------------------


def test_stdout_is_truncated_at_2000_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """標準出力は**先頭 2000 バイト**で切る。"""
    tool, _ = build_tool()
    patch_run(monkeypatch, FakeCompleted(stdout="a" * 5000))
    message = tool.run(name="pytest")
    assert len(message.encode("utf-8")) <= 2000 + 200


def test_empty_stdout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """空なら `(出力なし)`。"""
    tool, _ = build_tool()
    patch_run(monkeypatch, FakeCompleted(stdout=""))
    assert tool.run(name="pytest") == "(出力なし)"
