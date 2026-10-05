"""クラスタ 03 の保管庫読取（IF-33・IF-34・IF-35）。§6 #4・#5・#6。

**実 executor の Tool × 実 vault のフォルダ木**。観測は 2 段にする。

1. **鎖**: 台本 T3 が道具を呼び、`events` に `tool_called` が積まれる（executor → vault へ
   実際に届いたこと）。道具は例外を外へ出さず戻り値の文言に翻訳するので、**契約例外そのものは
   `CrewOutcome` に現れない**（`tools/base.py` の規約）。
2. **契約**: 例外の種別（`VaultNotFound(role)`・`VaultPathViolation(kind)`）は、実 vault の
   `VaultReader` を**実 settings が返した `ProjectFolders`** で直接呼んで確かめる。ここも
   実物×実物であり、スタブは 1 つも介在しない。

`server.call_count` は全テストで assert する（R-04）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, script_tool_calls
from _support.cluster_stores import (
    OUTSIDE_NAME,
    VAULT_INPUT_NAME,
    VAULT_OUTCOME_NAME,
    seed_issue,
)

from api.wiring import require
from common.interfaces.executor import CrewEventKind, CrewExecutor
from common.interfaces.settings import ProjectFolders, SettingsReader
from common.interfaces.vault import (
    FileRef,
    VaultArea,
    VaultNotFound,
    VaultPathViolation,
    VaultReader,
)
from common.kernel.enums import KindLabel, Labels, PersonaKey, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewSpec

pytestmark = pytest.mark.requires_bd

MISSING_NAME = "no_such_input.md"
"""実 vault に無い入力ファイル名。"""

MISSING_OUTCOME = "no_such_outcome.md"
"""実 vault の `outcome/` に無いファイル名。"""


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


def _folders(runtime: ClusterRuntime) -> ProjectFolders:
    """実 settings が返した `ProjectFolders`（見本を自前で作らない）。"""
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    return settings_reader.get_project(runtime.stores.project_key).folders


@pytest.mark.contract_if("IF-33")
@pytest.mark.cluster_case("03#4")
def test_cluster_03_list_inputs_returns_the_real_tree_and_reports_an_unknown_role(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #4 / IF-33: PJ 入力・役割フォルダが返り、宣言に無い役割は `VaultNotFound(role)`。

    期待（§6 #4）: PJ 入力・役割・兄弟成果物が返る／role が `role_dirs` に無い →
    `VaultNotFound(role)`。

    台本 T3 の 1 呼び出し目で `list_input_files` を呼ばせ、道具が**実 vault まで届いた**ことを
    `tool_called` で見る。返る中身と例外の種別は `VaultReader` を実 `ProjectFolders` で直接
    呼んで確かめる（道具は例外を戻り値の文言に翻訳するので、種別は道具越しには見えない）。
    """
    script = script_tool_calls([make_tool_call(1, ToolName.list_input_files, {})])
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    vault_reader: VaultReader = require(runtime.wiring.provided, VaultReader)
    folders = _folders(runtime)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    called = [event for event in outcome.events if event.kind is CrewEventKind.tool_called]
    assert any(event.detail.get("tool") == ToolName.list_input_files.value for event in called), (
        f"`list_input_files` が実 vault まで届いていない: {[e.detail for e in called]}"
    )

    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        "policy の前段で拒否されている（道具が実物まで届いていない）:"
        f" {[event.detail for event in denied]}"
    )

    listed = vault_reader.list_inputs(folders, runtime.stores.persona)
    names = {ref.name for ref in listed}
    areas = {ref.area for ref in listed}
    assert VAULT_INPUT_NAME in names, f"PJ 入力が返っていない: {sorted(names)}"
    assert VaultArea.pj_input in areas, f"領域が付いていない: {areas}"
    assert all(isinstance(ref, FileRef) for ref in listed)

    with pytest.raises(VaultNotFound) as caught:
        vault_reader.list_inputs(folders, PersonaKey.analyst)
    assert caught.value.role is PersonaKey.analyst, caught.value.role

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.contract_if("IF-34")
@pytest.mark.cluster_case("03#5")
def test_cluster_03_read_input_rejects_missing_and_outside_paths(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #5 / IF-34: 不在は `VaultNotFound`、外縁外は `VaultPathViolation(kind)`。

    期待（§6 #5）: 不在／外縁外／非 UTF-8／サイズ超過 → `VaultNotFound`／
    `VaultPathViolation(kind)`（P5D-011）。Tool は指摘で返す。

    外縁外は**宣言そのものが vault ルートの外を指す**形で作る（`make_temp_vault` が用意した
    `outside_folders`）。実 vault の外に置いた実ファイルを名指しして読ませても、`rel_path` の
    正規化で `outside_root` に落ちることを見る。
    """
    script = script_tool_calls(
        [make_tool_call(1, ToolName.read_input_file, {"filename": MISSING_NAME})]
    )
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    vault_reader: VaultReader = require(runtime.wiring.provided, VaultReader)
    folders = _folders(runtime)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))
    assert outcome.halted is False, "読取の失敗は停止ではない（道具が指摘で返す）"
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        "policy の前段で拒否されている（道具が実物まで届いていない）:"
        f" {[event.detail for event in denied]}"
    )

    listed = vault_reader.list_inputs(folders, runtime.stores.persona)
    present = next(ref for ref in listed if ref.name == VAULT_INPUT_NAME)
    body = vault_reader.read_input(folders, present)
    assert body, "実在する入力の本文が空（実 vault を読んでいない疑い）"

    absent: FileRef = FileRef(
        area=VaultArea.pj_input,
        rel_path=f"{present.rel_path.rsplit('/', 1)[0]}/{MISSING_NAME}",
        name=MISSING_NAME,
        size_bytes=0,
        mtime=_mtime_of(present),
    )
    with pytest.raises(VaultNotFound):
        vault_reader.read_input(folders, absent)

    outside: FileRef = FileRef(
        area=VaultArea.pj_input,
        rel_path=f"../outside/{OUTSIDE_NAME}",
        name=OUTSIDE_NAME,
        size_bytes=0,
        mtime=_mtime_of(present),
    )
    with pytest.raises(VaultPathViolation) as violation:
        vault_reader.read_input(folders, outside)
    assert violation.value.kind in {"outside_root", "outside_area"}, violation.value.kind

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


def _mtime_of(ref: FileRef) -> datetime:
    """`FileRef` の更新時刻（見本を組むときに実物の値を使い回す）。"""
    return ref.mtime


@pytest.mark.contract_if("IF-35")
@pytest.mark.cluster_case("03#6")
def test_cluster_03_read_outcome_raises_instead_of_returning_empty(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #6 / IF-35: `outcome/` に無いファイルは `VaultNotFound`（黙って空を返さない）。

    期待（§6 #6）: 不在 → `VaultNotFound`（FN-13）。

    「空文字を返す」実装との差を見るのが要点なので、**実在する成果物では本文が返る**ことも
    対にして確かめる（`make_temp_vault(with_existing_outcome=True)` が 1 件置いている）。
    """
    script = script_tool_calls(
        [make_tool_call(1, ToolName.read_outcome_file, {"filename": MISSING_OUTCOME})]
    )
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    vault_reader: VaultReader = require(runtime.wiring.provided, VaultReader)
    folders = _folders(runtime)

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))
    assert outcome.halted is False, "読取の失敗は停止ではない（道具が指摘で返す）"
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        "policy の前段で拒否されている（道具が実物まで届いていない）:"
        f" {[event.detail for event in denied]}"
    )

    body = vault_reader.read_outcome_file(folders, VAULT_OUTCOME_NAME)
    assert body, "実在する成果物の本文が空（実 vault を読んでいない疑い）"

    with pytest.raises(VaultNotFound) as caught:
        vault_reader.read_outcome_file(folders, MISSING_OUTCOME)
    assert MISSING_OUTCOME in caught.value.rel_path, caught.value.rel_path

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )
