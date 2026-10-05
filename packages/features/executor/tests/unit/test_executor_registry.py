"""装備の解決（F04・T026・US3）。**表駆動**で書く。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import make_bundle, make_config, make_folders, make_member, make_stubs

from common.kernel.enums import ToolName
from executor.models import RunSession
from executor.tools import TOOL_BUILDERS, ToolRegistry
from executor.tools.base import GuardedTool, ToolContext

if TYPE_CHECKING:
    from collections.abc import Sequence

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "executor"

SEVEN = [
    ToolName.read_issue,
    ToolName.record_progress,
    ToolName.list_input_files,
    ToolName.read_input_file,
    ToolName.list_outcome_files,
    ToolName.read_outcome_file,
    ToolName.escalate_to_human,
]


def make_context() -> ToolContext:
    """`ToolContext` を 1 個作る。"""
    stubs = make_stubs()
    member = make_member()
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders())
    return ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(),
    )


@pytest.mark.parametrize(
    ("member_tools", "task_tools", "expected"),
    [
        (SEVEN, None, [name.value for name in SEVEN]),
        (SEVEN, [ToolName.write_outcome], ["write_outcome"]),
        (SEVEN, [], []),
        ([], None, []),
        ([], [], []),
    ],
    ids=["stage-silent", "stage-replaces", "stage-declares-zero", "member-empty", "both-empty"],
)
def test_resolve_uses_declarations_only(
    member_tools: Sequence[ToolName], task_tools: Sequence[ToolName] | None, expected: list[str]
) -> None:
    """段側の宣言が無ければ人格側・あれば**置換**（足し算ではない＝ FR-030・SC-009）。"""
    registry = ToolRegistry(builders=TOOL_BUILDERS)
    tools = registry.resolve(member_tools, task_tools, make_context())
    assert [tool.name for tool in tools] == expected


def test_none_and_empty_list_are_distinguished() -> None:
    """`None`（宣言が無い）と `[]`（0 件を宣言した）を区別する（F-05）。"""
    registry = ToolRegistry(builders=TOOL_BUILDERS)
    context = make_context()
    assert len(registry.resolve(SEVEN, None, context)) == len(SEVEN)
    assert registry.resolve(SEVEN, [], context) == []


def test_builder_keys_match_tool_name_exactly() -> None:
    """`TOOL_BUILDERS` の鍵の集合が `ToolName` の 16 種と**完全一致**（FR-029・SC-011）。"""
    assert set(TOOL_BUILDERS) == set(ToolName)
    assert len(TOOL_BUILDERS) == 16


@pytest.mark.parametrize("tool_name", list(ToolName))
def test_every_builder_returns_a_guarded_tool(tool_name: ToolName) -> None:
    """**すべての構築子が `GuardedTool` の派生を返す**（FR-038・SC-007）。"""
    tool = TOOL_BUILDERS[tool_name](make_context())
    assert isinstance(tool, GuardedTool)
    assert issubclass(type(tool), GuardedTool)
    assert tool.tool_name is tool_name
    assert tool.name == tool_name.value


@pytest.mark.parametrize("tool_name", list(ToolName))
def test_no_tool_overrides_run(tool_name: ToolName) -> None:
    """16 種のいずれも `_run` を override しない（§1-2）。"""
    tool = TOOL_BUILDERS[tool_name](make_context())
    # pyright の抑制理由: 「派生が `_run` を override していない」ことの検査そのものなので、
    # 保護された名前を外から見る必要がある（contracts/guarded-tool.md §1-2）。
    assert type(tool)._run is GuardedTool._run  # pyright: ignore[reportPrivateUsage]


def test_resolve_of_unknown_name_raises() -> None:
    """`ToolName` に無い名前は構築子表に無いので落ちる（起動時に `build()` が先に弾く）。"""
    registry = ToolRegistry(builders={ToolName.read_issue: TOOL_BUILDERS[ToolName.read_issue]})
    with pytest.raises(KeyError):
        registry.resolve([ToolName.write_outcome], None, make_context())


# --------------------------------------------------------------------------------------
# 段・用途ごとの集合をコードに持たない（FR-032・SC-010・US3 AC-4）
# --------------------------------------------------------------------------------------


def test_no_stage_or_purpose_tool_sets_in_code() -> None:
    """段や用途ごとの道具集合を表す**コード定数が 0 件**（P5D-031 をコードに戻していない）。"""
    banned = re.compile(
        r"(COMMON_TOOLS|PROPOSE_TOOLS|EXECUTE_TOOLS|REVIEW_TOOLS|TOOLS_BY_STAGE|TOOLS_BY_PURPOSE"
        r"|TOOLS_BY_MODE|DEFAULT_TOOLS)"
    )
    hits = [
        f"{path.name}:{index}"
        for path in SRC_DIR.rglob("*.py")
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if banned.search(line)
    ]
    assert hits == []


def test_tool_builders_is_the_only_tool_collection() -> None:
    """道具名を列挙する module 定数は `TOOL_BUILDERS` の 1 つだけである。"""
    literal_lists: list[str] = []
    for path in SRC_DIR.rglob("*.py"):
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.count("ToolName.") >= 2 and "TOOL_BUILDERS" not in line:
                literal_lists.append(f"{path.name}:{index}")
    assert literal_lists == []
