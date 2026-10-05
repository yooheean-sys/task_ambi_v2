"""前段（`GuardedTool`）の検証（T016・US2）。**表駆動**で書く。"""

from __future__ import annotations

import re
from typing import Any

import pytest
from conftest import (
    AuthorizerMode,
    make_bundle,
    make_config,
    make_folders,
    make_member,
    make_stubs,
)
from pydantic import BaseModel

from common.interfaces.executor import CrewEventKind
from common.interfaces.vault import VaultArea
from common.kernel.enums import ToolName
from executor.models import RunSession
from executor.tools.base import (
    ACT_EXECUTE,
    ACT_READ,
    ACT_WRITE,
    GuardedTool,
    Preparation,
    ToolContext,
    obj_for_cmd,
    obj_for_tool,
    obj_for_vault,
)

OBJ_PATTERN = re.compile(r"^(tool|cmd|vault):")


class _ProbeArgs(BaseModel):
    """検証用の道具の引数。"""


class ProbeTool(GuardedTool):
    """1 段だけの検証用の道具（`_execute` が呼ばれた回数を数える）。"""

    executed: int = 0

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation()

    def _execute(self, payload: object | None, **kwargs: Any) -> str:
        self.executed += 1
        return "OK"


class TwoStageProbeTool(ProbeTool):
    """二段判定の検証用（2 段目は `vault:outcome`／`write`）。"""

    def _prepare(self, **kwargs: Any) -> Preparation:
        return Preparation(checks=((obj_for_vault(VaultArea.outcome), ACT_WRITE),))


def make_context(
    mode: AuthorizerMode = "allow", deny_objs: frozenset[str] = frozenset()
) -> tuple[ToolContext, Any]:
    """`ToolContext` とスタブ束を作る。"""
    stubs = make_stubs(authorizer_mode=mode, deny_objs=deny_objs)
    member = make_member()
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders())
    context = ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(),
    )
    return context, stubs


def make_probe(
    mode: AuthorizerMode = "allow",
    deny_objs: frozenset[str] = frozenset(),
    *,
    two_stage: bool = False,
) -> tuple[ProbeTool, Any]:
    """検証用の道具とスタブ束を作る。"""
    context, stubs = make_context(mode, deny_objs)
    cls = TwoStageProbeTool if two_stage else ProbeTool
    tool = cls(
        name="probe",
        description="検証用",
        args_schema=_ProbeArgs,
        ctx=context,
        tool_name=ToolName.read_issue,
    )
    return tool, stubs


# --------------------------------------------------------------------------------------
# 語彙 3 系統（SC-008）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", list(ToolName))
def test_obj_for_tool_is_well_formed(tool_name: ToolName) -> None:
    """`tool:<ToolName の値>` の形が 16 種すべてで成立する。"""
    obj = obj_for_tool(tool_name)
    assert obj == f"tool:{tool_name.value}"
    assert OBJ_PATTERN.match(obj)


@pytest.mark.parametrize("area", list(VaultArea))
def test_obj_for_vault_is_well_formed(area: VaultArea) -> None:
    """`vault:<VaultArea の値>` の形が 3 領域すべてで成立する。"""
    obj = obj_for_vault(area)
    assert obj == f"vault:{area.value}"
    assert OBJ_PATTERN.match(obj)


@pytest.mark.parametrize("name", ["pytest", "ruff", "a-b_c.d"])
def test_obj_for_cmd_is_well_formed(name: str) -> None:
    """`cmd:<登録名>` の形が成立する。"""
    obj = obj_for_cmd(name)
    assert obj == f"cmd:{name}"
    assert OBJ_PATTERN.match(obj)


def test_acts_are_exactly_three_words() -> None:
    """`act` は `execute`／`read`／`write` の 3 語だけである。"""
    assert (ACT_EXECUTE, ACT_READ, ACT_WRITE) == ("execute", "read", "write")


# --------------------------------------------------------------------------------------
# 拒否（SC-004・SC-005・SC-006）
# --------------------------------------------------------------------------------------


DENIAL_CASES: list[tuple[AuthorizerMode, frozenset[str], bool, int]] = [
    ("deny", frozenset(), False, 1),
    ("raise", frozenset(), False, 1),
    ("deny_objs", frozenset({"tool:read_issue"}), False, 1),
    ("deny_objs", frozenset({"tool:read_issue"}), True, 1),
    ("deny_objs", frozenset({"vault:outcome"}), True, 2),
]


@pytest.mark.parametrize(
    ("mode", "deny_objs", "two_stage", "expected_enforce_calls"),
    DENIAL_CASES,
    ids=[
        "always-deny",
        "authorizer-raises",
        "deny-first-stage",
        "deny-first-of-two",
        "deny-second",
    ],
)
def test_denied_never_reaches_execute(
    mode: AuthorizerMode,
    deny_objs: frozenset[str],
    two_stage: bool,
    expected_enforce_calls: int,
) -> None:
    """否なら `_execute` は 0 回・呼び手へ飛ぶ例外 0 件・`tool_denied` は 1 件（SC-004〜006）。"""
    tool, stubs = make_probe(mode, deny_objs, two_stage=two_stage)
    result = tool.run()
    assert isinstance(result, str)
    assert result.startswith("ERROR:")
    assert tool.executed == 0
    assert stubs.log.count("Authorizer.enforce") == expected_enforce_calls
    denials = [e for e in tool.ctx.session.events if e.kind is CrewEventKind.tool_denied]
    assert len(denials) == 1


def test_allowed_two_stage_calls_enforce_twice_then_executes() -> None:
    """二段の道具は 2 回判定してから本体を呼ぶ（FR-037）。"""
    tool, stubs = make_probe("allow", two_stage=True)
    assert tool.run() == "OK"
    assert tool.executed == 1
    objs = [call.kwargs["obj"] for call in stubs.log.of("Authorizer.enforce")]
    assert objs == ["tool:read_issue", "vault:outcome"]
    acts = [call.kwargs["act"] for call in stubs.log.of("Authorizer.enforce")]
    assert acts == [ACT_EXECUTE, ACT_WRITE]
    assert tool.ctx.session.events == []


def test_subject_is_always_the_member_casbin_subject() -> None:
    """`subject` は常に `member.casbin_subject`（§1-3）。"""
    tool, stubs = make_probe("allow", two_stage=True)
    tool.run()
    subjects = {call.kwargs["subject"] for call in stubs.log.of("Authorizer.enforce")}
    assert subjects == {make_member().casbin_subject}


def test_denial_message_names_the_escalation_tool() -> None:
    """拒否文言は命令形で `escalate_to_human` を名指しする（contracts §4）。"""
    tool, _ = make_probe("deny")
    message = tool.run()
    assert message == (
        "ERROR: 道具 'read_issue' の実行は許可されていない。"
        "別の手段を使うか、escalate_to_human で人に相談してください。"
    )


DETAIL_CASES: list[tuple[AuthorizerMode, frozenset[str], bool, str]] = [
    ("deny", frozenset(), False, "tool:read_issue / execute"),
    ("raise", frozenset(), False, "tool:read_issue / execute"),
    ("deny_objs", frozenset({"vault:outcome"}), True, "vault:outcome / write"),
]


@pytest.mark.parametrize(
    ("mode", "deny_objs", "two_stage", "expected_reason"),
    DETAIL_CASES,
    ids=["deny", "raise", "second-stage"],
)
def test_tool_denied_detail_carries_obj_and_act_only(
    mode: AuthorizerMode, deny_objs: frozenset[str], two_stage: bool, expected_reason: str
) -> None:
    """`detail` は `tool` と `reason` だけ（判定器の例外文を入れない＝ §5）。"""
    tool, _ = make_probe(mode, deny_objs, two_stage=two_stage)
    tool.run()
    (event,) = [e for e in tool.ctx.session.events if e.kind is CrewEventKind.tool_denied]
    assert event.detail == {"tool": "read_issue", "reason": expected_reason}
    assert "secret-in-exception-message" not in str(event.detail)


def test_authorizer_exception_does_not_escape() -> None:
    """判定器が例外を投げても呼び手へ飛ばさず、拒否側に倒す（FR-036・SC-006）。"""
    tool, _ = make_probe("raise")
    assert tool.run().startswith("ERROR:")


def test_derived_tools_never_override_run() -> None:
    """派生は `_run` を override しない（§1-2）。"""
    # pyright の抑制理由: 「派生が `_run` を override していない」ことの検査そのものなので、
    # 保護された名前を外から見る必要がある（contracts/guarded-tool.md §1-2）。
    assert ProbeTool._run is GuardedTool._run  # pyright: ignore[reportPrivateUsage]
    assert TwoStageProbeTool._run is GuardedTool._run  # pyright: ignore[reportPrivateUsage]
