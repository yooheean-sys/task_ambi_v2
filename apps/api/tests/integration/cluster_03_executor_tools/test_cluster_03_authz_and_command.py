"""クラスタ 03 の認可とコマンド実行（IF-29・IF-30）。§6 #1・#2・#3。

**実 casbin の判定器 × 実 CrewAI の道具**。拒否は台本ではなく**領域 A の `policy.csv` の行の
有無**で作る（plan (iv)。casbin は行の無い組を暗黙に拒否する）。

台本は `finish_reason="tool_calls"` ＋ `message.tool_calls` の形（R-03。実 CrewAI は native
function calling で道具を呼ぶ）。**すべての台本テストで `server.call_count` を assert する**
（偽サーバは最終要素を繰り返すので、往復数の読み違いが「無限に正常応答」で緑になりうる＝ R-04）。
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, script_tool_calls
from _support.cluster_stores import seed_issue

from api.wiring import require
from common.interfaces.executor import CrewEventKind, CrewExecutor
from common.interfaces.settings import CommandRegistry, UnknownCommand
from common.kernel.enums import KindLabel, Labels, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewSpec

pytestmark = pytest.mark.requires_bd

ALLOWED_WITH_COMMAND: tuple[ToolName, ...] = (
    ToolName.read_issue,
    ToolName.record_progress,
    ToolName.list_input_files,
    ToolName.read_input_file,
    ToolName.read_outcome_file,
    ToolName.write_outcome,
    ToolName.run_command,
)
"""`run_command` まで allow した `policy.csv`（#2・#3 が前段を通るために要る）。"""

UNKNOWN_COMMAND_NAME = "no_such_command"
"""`commands.yaml` に**無い**登録名（`UnknownCommand` を踏む）。"""


def _seed(runtime: ClusterRuntime) -> str:
    """実 `bd` に課題を 1 件仕込み、その issue_id を返す。"""
    return seed_issue(
        runtime.stores.beads,
        title="クラスタ 03 の課題",
        labels=[
            Labels.project(runtime.stores.project_key),
            Labels.kind(KindLabel.child),
            Labels.persona(runtime.stores.persona),
        ],
    )


@pytest.mark.contract_if("IF-29")
@pytest.mark.cluster_case("03#1")
def test_cluster_03_denied_tool_returns_a_notice_instead_of_raising(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #1 / IF-29: policy で許可されていない道具は**例外にせず戻り値で指摘**する。

    期待（§6 #1）: `enforce` が False → 例外にせず戻り値で指摘・`events` に `tool_denied`
    （フェイルクローズ P5D-032）。

    拒否は**領域 A の `policy.csv` に `tool:run_command` の行を置かない**ことで作る（既定の
    `ALLOWED_TOOLS` がそうなっている）。`agents.yaml` の `tools` には `run_command` が在るので
    道具そのものは装備され、前段の判定だけが落ちる形になる。

    台本 T2 は `run_command` を 1 回呼んで 2 往復目で確定する。
    """
    script = script_tool_calls(
        [make_tool_call(1, ToolName.run_command, {"name": "demo"})],
    )
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert denied, f"`tool_denied` が積まれていない: {[e.kind for e in outcome.events]}"
    assert outcome.halted is False, "拒否は停止ではない（例外にせず戻り値で指摘する）"
    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.contract_if("IF-30")
@pytest.mark.cluster_case("03#2")
def test_cluster_03_unknown_command_is_reported_as_a_notice(
    cluster: Callable[..., ClusterRuntime],
    make_spec: Callable[..., CrewSpec],
    tmp_path: Path,
) -> None:
    """クラスタ 03 #2 / IF-30: 台帳に無い登録名は `UnknownCommand`（道具側は前段で止まる）。

    期待（§6 #2）: `UnknownCommand` → Tool が指摘で返す。

    ⚠ **実配線では道具越しに `UnknownCommand` へ到達できない**（2026-09-01 に実測。
    design-sync の B 表に記録する）。理由は 2 つの門が先に閉じているためである。

    - `run_command` の 2 段目は `cmd:<AI が渡した名前>` を判定するので、台帳に無い名前は
      **`resolve_command` を呼ぶ前に** `tool_denied` になる。
    - では policy に `cmd:ghost` を足せばよいかというと、settings の V-24（policy の cmd 参照
      ⊆ `commands.yaml` の定義）が**起動を中断させる**ので、そんな領域 A は組めない。

    したがって観測を 2 段にする（`test_cluster_03_vault_reads.py` と同型）。

    1. **鎖**: 道具は呼ばれ、2 段目の認可で止まる（`tool_denied` が積まれ、登録済みコマンドの
       副作用は起きない）。実物の判定器がここまで効いていることの観測である。
    2. **契約**: `UnknownCommand` そのものは実 settings の `CommandRegistry` を直接呼んで見る。
    """
    marker = tmp_path / "should_not_exist.txt"
    touch = shutil.which("touch")
    assert touch is not None, "前提: `touch` が PATH 上にある"
    script = script_tool_calls(
        [make_tool_call(1, ToolName.run_command, {"name": UNKNOWN_COMMAND_NAME})],
    )
    runtime = cluster(script, allowed_tools=ALLOWED_WITH_COMMAND, command_argv=(touch, str(marker)))
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    command_registry: CommandRegistry = require(runtime.wiring.provided, CommandRegistry)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.halted is False, "未定義コマンドは停止ではない（戻り値で指摘する）"
    called = [event for event in outcome.events if event.kind is CrewEventKind.tool_called]
    assert any(event.detail.get("tool") == ToolName.run_command.value for event in called), (
        f"`run_command` が呼ばれていない（1 段目で拒否された疑い）: {[e.detail for e in called]}"
    )
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert any(
        event.detail.get("reason", "").startswith(f"cmd:{UNKNOWN_COMMAND_NAME}") for event in denied
    ), f"2 段目（`cmd:<名前>`）で止まっていない: {[event.detail for event in denied]}"
    assert not marker.exists(), (
        "台帳に無い名前なのに登録済みコマンドが起動された"
        f" / 登録: {runtime.stores.area_a.command_argv}"
    )

    with pytest.raises(UnknownCommand):
        command_registry.resolve_command(UNKNOWN_COMMAND_NAME)
    assert command_registry.resolve_command(runtime.stores.area_a.command_name) == [
        *runtime.stores.area_a.command_argv
    ], "台帳に在る名前は登録どおりの argv を返す（分かち書きのまま）"

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.contract_if("IF-30")
@pytest.mark.cluster_case("03#3")
def test_cluster_03_defined_command_runs_the_registered_argv_without_a_shell(
    cluster: Callable[..., ClusterRuntime],
    make_spec: Callable[..., CrewSpec],
    tmp_path: Path,
) -> None:
    """クラスタ 03 #3 / IF-30: 台帳に在る名前は**登録された argv のまま**起動される。

    期待（§6 #3）: `argv` のまま `subprocess.run(shell=False)`。**AI の引数は渡らない**
    （D07-e。道具の入力スキーマは登録名 1 つだけで、余分な引数は受け取り口が無い）。

    登録 argv には**副作用のあるもの**（`touch <marker>`）を使う —— 「呼ばれた」イベントだけでは
    「登録どおりの並びで実際に起動したか」が分からない。台本には `args` を混ぜて渡すが、道具の
    入力スキーマは登録名 1 つだけなので **argv は変わらない**（注入された語を含むファイルが
    できないことまで見る）。
    """
    marker = tmp_path / "ran_marker.txt"
    touch = shutil.which("touch")
    assert touch is not None, "前提: `touch` が PATH 上にある"
    script = script_tool_calls(
        [
            make_tool_call(
                1, ToolName.run_command, {"name": "demo", "args": ["; rm -rf /", "INJECTED"]}
            )
        ],
    )
    runtime = cluster(script, allowed_tools=ALLOWED_WITH_COMMAND, command_argv=(touch, str(marker)))
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.halted is False
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        "policy の前段で拒否されている（道具が実物まで届いていない）:"
        f" {[event.detail for event in denied]}"
    )
    called = [event for event in outcome.events if event.kind is CrewEventKind.tool_called]
    assert any(event.detail.get("tool") == ToolName.run_command.value for event in called), (
        f"`run_command` が呼ばれていない: {[e.detail for e in called]}"
    )
    assert marker.is_file(), (
        "登録された argv がそのまま起動されていない（痕跡のファイルが無い）"
        f" / 登録: {runtime.stores.area_a.command_argv}"
    )
    injected = [path.name for path in tmp_path.iterdir() if "INJECTED" in path.name]
    assert not injected, f"AI が渡した引数が argv に混ざっている: {injected}"
    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )
