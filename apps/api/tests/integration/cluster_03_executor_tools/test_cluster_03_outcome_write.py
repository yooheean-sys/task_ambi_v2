"""クラスタ 03 の成果物書込（IF-37＝`OutcomeWriter.write_outcome`）。§6 #7・#8。

**書込は読取と違い副作用が残る**ので、道具越しの観測がそのまま成立する —— `CrewOutcome.written`
に実パスが載り、実 vault のフォルダ木にファイルが増える。したがってここは 2 段構えにせず
**道具経由だけ**で見る（`VaultPathViolation` の種別だけは道具が文言へ翻訳するので、その 1 点を
`OutcomeWriter` の直接呼び出しで確かめる）。

`server.call_count` は全テストで assert する（R-04）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, script_tool_calls
from _support.cluster_stores import VAULT_OUTCOME_NAME, seed_issue

from api.wiring import require
from common.interfaces.executor import CrewEventKind, CrewExecutor
from common.interfaces.settings import ProjectFolders, SettingsReader
from common.interfaces.vault import OutcomeWriter, VaultPathViolation
from common.kernel.enums import KindLabel, Labels, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewSpec

pytestmark = pytest.mark.requires_bd

FORBIDDEN_NAME = "bad/name.md"
"""`vault/config.json` の `forbidden_chars` に含まれる `/` を持つ名前。"""

NEW_BODY = "クラスタ 03 が書いた成果物\n"
"""上書きの前後を区別するための本文。"""


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


@pytest.mark.contract_if("IF-37")
@pytest.mark.cluster_case("03#7")
def test_cluster_03_write_outcome_rejects_forbidden_characters(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #7 / IF-37: 禁止文字を含む名前は `VaultPathViolation`（黙って置換しない）。

    期待（§6 #7）: `VaultPathViolation`（違反文字を明示。**黙って置換しない**）。

    「置換して書いてしまう」実装との差を見るのが要点なので、**実 vault に新しいファイルが
    1 つも増えていない**ことまで確かめる。違反の種別（`kind`）は道具が文言へ翻訳するので、
    `OutcomeWriter` を実 `ProjectFolders` で直接呼んで見る。
    """
    script = script_tool_calls(
        [
            make_tool_call(
                1, ToolName.write_outcome, {"filename": FORBIDDEN_NAME, "content": NEW_BODY}
            )
        ]
    )
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    outcome_writer: OutcomeWriter = require(runtime.wiring.provided, OutcomeWriter)
    outcome_dir = runtime.stores.vault.outcome_dir
    before = sorted(path.name for path in outcome_dir.iterdir())

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.halted is False, "書込の失敗は停止ではない（道具が指摘で返す）"
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        "policy の前段で拒否されている（道具が実物まで届いていない）:"
        f" {[event.detail for event in denied]}"
    )
    assert not outcome.written, f"拒否したはずの書込が `written` に載っている: {outcome.written}"
    after = sorted(path.name for path in outcome_dir.iterdir())
    assert after == before, f"禁止文字の名前が置換されて書かれている: {before} → {after}"

    with pytest.raises(VaultPathViolation) as caught:
        outcome_writer.write_outcome(_folders(runtime), FORBIDDEN_NAME, NEW_BODY)
    assert caught.value.kind == "forbidden_name", caught.value.kind
    assert "/" in caught.value.detail, f"違反した文字が明示されていない: {caught.value.detail}"

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )


@pytest.mark.contract_if("IF-37")
@pytest.mark.cluster_case("03#8")
def test_cluster_03_write_outcome_archives_the_existing_file_before_writing(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec]
) -> None:
    """クラスタ 03 #8 / IF-37: 既存ファイルは `outcome/_archive/` へ退避してから書き換える。

    期待（§6 #8）: `outcome/_archive/<filename>.<UTC stamp>` へ `os.replace` 退避（P5D-008）→
    書込 → `events` に `outcome_overwritten`。`WrittenPath` は**実パス**。

    `make_temp_vault(with_existing_outcome=True)` が置いた 1 件を上書きする。退避先に**旧本文が
    残っている**ことまで見る（消してから書く実装との差）。
    """
    script = script_tool_calls(
        [
            make_tool_call(
                1, ToolName.write_outcome, {"filename": VAULT_OUTCOME_NAME, "content": NEW_BODY}
            )
        ]
    )
    runtime = cluster(script)
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    outcome_dir = runtime.stores.vault.outcome_dir
    original = (outcome_dir / VAULT_OUTCOME_NAME).read_text(encoding="utf-8")

    outcome = crew_executor.run(make_spec(runtime, issue_id=_seed(runtime)))

    assert outcome.halted is False
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        "policy の前段で拒否されている（道具が実物まで届いていない）:"
        f" {[event.detail for event in denied]}"
    )
    assert outcome.written, "`written` に書込結果が載っていない"
    written = outcome.written[0]
    assert written.overwritten is True, f"上書きとして扱われていない: {written}"
    assert written.archived_to is not None, "退避先が記録されていない"
    assert VAULT_OUTCOME_NAME in written.rel_path, written.rel_path
    assert (runtime.stores.vault.root / written.rel_path).read_text(encoding="utf-8") == NEW_BODY

    archived = runtime.stores.vault.root / written.archived_to
    assert archived.is_file(), f"退避先の実ファイルが無い: {written.archived_to}"
    assert archived.read_text(encoding="utf-8") == original, "退避先に旧本文が残っていない"

    overwritten = [
        event for event in outcome.events if event.kind is CrewEventKind.outcome_overwritten
    ]
    assert overwritten, (
        f"`outcome_overwritten` が積まれていない: {[e.kind for e in outcome.events]}"
    )

    assert runtime.llm.call_count == len(script), (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 台本={len(script)}"
    )
