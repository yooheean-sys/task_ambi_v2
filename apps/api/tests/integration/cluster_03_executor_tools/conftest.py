"""クラスタ 03（executor の Tool × 実 settings・実 vault・実 board・実 monitor）の準備手段。

**ダブルは偽 LLM サーバだけ** —— override は 1 件も無い（5 MOD で契約が閉じる）。実 CrewAI が
native function calling で道具を呼び、その道具が**実物の外部ツール**（casbin の判定器・実
`bd`・実 vault のフォルダ木・`subprocess`）まで届くことを見る。

fixture の順序に**依存がある** —— 偽 LLM サーバの URL は起動後に確定するので、
**サーバ → 領域 A（`llm_profiles.yaml` の `base_url`）→ `build()`** の順に組む。だから
「台本を渡すと実物一式を返す工場」の形にしてある。

止め木は 4 枚とも通す（規約 §5）。`.env` には `api_key_ref` の実値を **1 件だけ**書く
（`llm_profiles.yaml` が名指しするキー。実 LLM API の鍵は `LLM_API_KEY*` ごと消してある）。

fixture は**すべて関数スコープ**（FR-042）。`from conftest import ...` を使わない（FR-041a）
—— 型も工場も fixture で配る。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import pytest
from _support.cluster_env import assert_env_path_under, isolate_env
from _support.cluster_logs import CorrelationRecorder, recording_correlation
from _support.cluster_stores import (
    AREA_A_COMMAND_ARGV,
    ClusterStores,
    init_beads,
    make_temp_area_a,
    make_temp_vault,
    remove_beads,
    require_bd_or_skip,
)
from _support.cluster_wiring import ClusterRuntime, build_cluster_wiring

from api.wiring import require
from board import BoardEnv
from common.interfaces.executor import CrewSpec
from common.interfaces.settings import MemberResolver, SettingsReader
from common.interfaces.stages import PromptBundle, PromptPurpose
from common.kernel.enums import ToolName
from settings import SettingsEnv
from vault import VaultEnv

if TYPE_CHECKING:
    from pathlib import Path

    from _support.fake_llm_server import FakeLlmServer

BEADS_PROJECT_KEY = "cl03"
"""`bd init -p` に渡す接頭辞。"""

API_KEY_REF = "LLM_API_KEY_CLUSTER03"
"""`llm_profiles.yaml` が名指しする `.env` のキー名。"""

API_KEY_VALUE = "sk-cluster03-dummy"
"""`.env` に置く**偽の**鍵（偽 LLM サーバは値を検証しない）。"""

ALLOWED_TOOLS: tuple[ToolName, ...] = (
    ToolName.read_issue,
    ToolName.record_progress,
    ToolName.list_input_files,
    ToolName.read_input_file,
    ToolName.read_outcome_file,
    ToolName.write_outcome,
)
"""`policy.csv` に allow 行を置く道具。

**`run_command` を入れない** —— 03 #1（deny の道具）は「`policy.csv` に行が無い」ことで作る
（casbin は行の無い組を暗黙に拒否する）。台本ではなく領域 A の中身で拒否を作るのが plan (iv)。
"""

DECLARED_TOOLS: tuple[ToolName, ...] = (*ALLOWED_TOOLS, ToolName.run_command)
"""`agents.yaml` の `tools`（**allow していない `run_command` も宣言する**）。

宣言に無ければ道具そのものが装備されず `enforce` の前段に届かない。「装備はされているが
policy で拒否される」形を作るために、宣言と許可を**わざと食い違わせる**。
"""


@pytest.fixture
def bd_bin() -> str:
    """`bd` の絶対パス（無ければ **skip**）。"""
    return require_bd_or_skip()


@pytest.fixture(autouse=True)
def cluster_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse）。`.env` には `api_key_ref` の実値 1 件だけを書く。

    Returns:
        差し替え先の一時 `.env` の絶対パス。
    """
    return isolate_env(monkeypatch, tmp_path, entries={API_KEY_REF: API_KEY_VALUE})


@pytest.fixture
def cluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_env: Path,
    bd_bin: str,
    fake_llm_server: Callable[[list[dict[str, Any]]], FakeLlmServer],
) -> Iterator[Callable[..., ClusterRuntime]]:
    """台本を渡すと**実物一式**を組んで返す工場（サーバ → 領域 A → `build()` の順）。

    工場にしているのは、偽 LLM サーバの URL が起動後にしか決まらないからである（領域 A の
    `llm_profiles.yaml` に実 URL を書いてから `build()` を呼ぶ必要がある）。

    `allowed_tools` を渡すと `policy.csv` の allow 行を差し替えられる —— 03 #1（deny）は
    「行が無い」ことで作り、#2・#3（`run_command`）は行を足して前段を通す。**deny は台本では
    なく領域 A の中身で作る**（plan (iv)）。

    Yields:
        台本 → 実物一式。
    """
    beads = init_beads(tmp_path, bd_bin, project_key=BEADS_PROJECT_KEY)
    vault = make_temp_vault(tmp_path, with_existing_outcome=True)
    built = 0

    def _build(
        script: list[dict[str, Any]],
        *,
        allowed_tools: tuple[ToolName, ...] = ALLOWED_TOOLS,
        command_argv: tuple[str, ...] = AREA_A_COMMAND_ARGV,
        repo_local_path: str | None = None,
        declared_tools: tuple[ToolName, ...] = DECLARED_TOOLS,
    ) -> ClusterRuntime:
        nonlocal built
        # 1 テストが土台を 2 つ組むことがある（帰属の有無を対にして見る形）。領域 A の置き場を
        # 呼び出しごとに分けないと 2 回目が `FileExistsError` になる。
        area_root = tmp_path / f"run{built}"
        area_root.mkdir(exist_ok=True)
        built += 1
        server = fake_llm_server(script)
        area_a = make_temp_area_a(
            area_root,
            allowed_tools=allowed_tools,
            declared_tools=declared_tools,
            base_url=server.base_url,
            api_key_ref=API_KEY_REF,
            with_hooks=False,
            command_argv=command_argv,
            repo_local_path=repo_local_path,
        )
        monkeypatch.setenv("BEADS_DIR", str(beads.beads_dir))
        monkeypatch.setenv("VAULT_ROOT", str(vault.root))
        monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))
        assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)
        assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=cluster_env)
        assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)
        return ClusterRuntime(
            wiring=build_cluster_wiring("03", {}),
            stores=ClusterStores(beads=beads, vault=vault, area_a=area_a),
            server=server,
        )

    yield _build

    remove_beads(beads)


@pytest.fixture
def make_spec() -> Callable[..., CrewSpec]:
    """`CrewSpec` を**実 settings から**組む工場を返す。

    `Member`・`HooksConfig`・`ProjectFolders` はいずれも実物の settings が返したものを使う
    （見本を自前で作ると「実物同士」でなくなる）。`PromptBundle` は stages が有効 MOD に
    無いのでテストが組む。

    Returns:
        `CrewSpec` を組む呼び出し可能。
    """

    def _make(
        runtime: ClusterRuntime,
        *,
        issue_id: str,
        tools: list[ToolName] | None = None,
        task_description: str = "クラスタ 03 の作業指示",
    ) -> CrewSpec:
        # 憲章 §3: Protocol 実装を受ける変数には該当 Protocol 型を必ず注釈する。
        member_resolver: MemberResolver = require(runtime.wiring.provided, MemberResolver)
        settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
        project_key = runtime.stores.project_key
        member = member_resolver.resolve(project_key, runtime.stores.persona)
        project = settings_reader.get_project(project_key)
        bundle = PromptBundle(
            persona_key=runtime.stores.persona,
            role=member.role,
            goal=member.goal,
            backstory=member.backstory,
            task_description=task_description,
            expected_output="クラスタ 03 の期待成果",
            task_tools=tools,
            purpose=PromptPurpose.work,
            proposal_schema="Proposal",
        )
        return CrewSpec(
            issue_id=issue_id,
            mode="execute",
            purpose=PromptPurpose.work,
            bundle=bundle,
            member=member,
            hooks=settings_reader.get_hooks(project_key),
            folders=project.folders,
            parent_acceptance=None,
            repos=project.repos,
        )

    return _make


@pytest.fixture
def missing_key_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bd_bin: str
) -> Iterator[ClusterRuntime]:
    """`api_key_ref` が `.env` に**無い**土台（#9 の `ExecutorFailure(build)` 用）。

    `build()` 以降を失敗させる観点は他と同居できない（クラスタ 03 §5）ので専用の fixture に
    する。`isolate_env` を `entries` なしで呼び直し、鍵の**行だけ**を落とす（偽 LLM サーバは
    起動しない —— 鍵の解決はサーバへ届く前に行われる）。

    Yields:
        鍵の無い状態で組んだ実物一式（`server` は `None`）。
    """
    env_file = isolate_env(monkeypatch, tmp_path)
    beads = init_beads(tmp_path, bd_bin, project_key=BEADS_PROJECT_KEY)
    vault = make_temp_vault(tmp_path)
    area_a = make_temp_area_a(tmp_path, api_key_ref=API_KEY_REF, with_hooks=False)
    monkeypatch.setenv("BEADS_DIR", str(beads.beads_dir))
    monkeypatch.setenv("VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=env_file)
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=env_file)
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=env_file)

    yield ClusterRuntime(
        wiring=build_cluster_wiring("03", {}),
        stores=ClusterStores(beads=beads, vault=vault, area_a=area_a),
    )

    remove_beads(beads)


@pytest.fixture
def correlation_recorder() -> Iterator[CorrelationRecorder]:
    """ルートロガへ観測点を足し、レコードごとの相関 ID を控える（#15 用）。

    Yields:
        観測結果を持つハンドラ。
    """
    yield from recording_correlation()
