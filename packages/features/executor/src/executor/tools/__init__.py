"""16 道具の構築子表と装備の解決（executor-F04）。

`ToolRegistry` と `TOOL_BUILDERS` は**本ファイルに置く**（C1）。`resolve` の戻り
（`list[GuardedTool]`）と `ToolBuilder` がいずれも `tools/` の型であり、`models.py` に置くと
**型注釈だけでも `models.py` → `tools/` の参照が生じる**ため。

**段や用途ごとの道具集合をコードに持たない**（FR-032・SC-010）。持つのは
「登録名 → 実体」の対応表だけで、どれを装備するかは設定の宣言（`Member.tools`／
`PromptBundle.task_tools`）が決める（D16-a・P5D-048）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from common.kernel.enums import ToolName
from executor.tools.base import GuardedTool, ToolContext
from executor.tools.board_tools import build_read_issue, build_record_progress
from executor.tools.command_tools import build_run_command
from executor.tools.declare_tools import (
    build_complete_issue,
    build_escalate_to_human,
    build_propose_breakdown,
    build_submit_review,
)
from executor.tools.repo_tools import (
    build_delete_repo_file,
    build_list_repo_files,
    build_read_repo_file,
    build_write_repo_file,
)
from executor.tools.vault_tools import (
    build_list_input_files,
    build_list_outcome_files,
    build_read_input_file,
    build_read_outcome_file,
    build_write_outcome,
)

type ToolBuilder = Callable[[ToolContext], GuardedTool]

TOOL_BUILDERS: Mapping[ToolName, ToolBuilder] = {
    ToolName.read_issue: build_read_issue,
    ToolName.record_progress: build_record_progress,
    ToolName.list_input_files: build_list_input_files,
    ToolName.read_input_file: build_read_input_file,
    ToolName.list_outcome_files: build_list_outcome_files,
    ToolName.read_outcome_file: build_read_outcome_file,
    ToolName.write_outcome: build_write_outcome,
    ToolName.run_command: build_run_command,
    ToolName.propose_breakdown: build_propose_breakdown,
    ToolName.complete_issue: build_complete_issue,
    ToolName.escalate_to_human: build_escalate_to_human,
    ToolName.submit_review: build_submit_review,
    ToolName.list_repo_files: build_list_repo_files,
    ToolName.read_repo_file: build_read_repo_file,
    ToolName.write_repo_file: build_write_repo_file,
    ToolName.delete_repo_file: build_delete_repo_file,
}
"""登録名 → 実体の対応表。**鍵の集合は `ToolName` の 16 種と完全一致**（FR-029・SC-011）。"""


@dataclass(frozen=True, slots=True)
class ToolRegistry:
    """装備の解決（executor-F04）。構築子表は**構築時に受け取る**。"""

    builders: Mapping[ToolName, ToolBuilder]

    def resolve(
        self,
        member_tools: Sequence[ToolName],
        task_tools: Sequence[ToolName] | None,
        context: ToolContext,
    ) -> list[GuardedTool]:
        """宣言から装備を組み立てる。

        段側の宣言（`task_tools`）が `None`（＝「宣言が無い」）なら人格側を使い、そうでなければ
        **置換する**（足し算ではない＝ FR-030・D16-a）。`[]` は「0 件を宣言した」であり `None`
        とは別物である（F-05）。
        """
        names = member_tools if task_tools is None else task_tools
        return [self.builders[name](context) for name in names]
