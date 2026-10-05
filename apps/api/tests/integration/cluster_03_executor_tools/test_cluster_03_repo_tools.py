"""クラスタ 03 の repo 系道具（IF-51・52・54・55）。§6 #16。D25-a。

実 CrewAI が native function calling で repo 系の道具を呼び、その道具が**実 settings の判定器**
（`tool:<name>` → `repo:files` の二段許可）を通って**実 repo の作業ツリー**まで届くことを見る。
作業ツリーは `tmp_path/repo_work` の実物（`.git` は空ディレクトリで足りる）で、領域 A の
`project.yaml#repos.local_path` に登録する。`CrewSpec.repos` は実 settings の `Project.repos` から
`make_spec` が詰める。

道具の戻り値は偽 LLM サーバが受け取った 2 往復目の要求（`role="tool"` のメッセージ）で見る。
`server.call_count` は全テストで assert する（R-04）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _support.cluster_scripts import make_tool_call, script_tool_calls
from _support.cluster_stores import seed_issue

from api.wiring import require
from common.interfaces.executor import CrewEventKind, CrewExecutor
from common.interfaces.repo import RepoPathViolation, RepoReader
from common.interfaces.settings import SettingsReader
from common.kernel.enums import KindLabel, Labels, ToolName

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewOutcome, CrewSpec

pytestmark = pytest.mark.requires_bd

REPO_TOOLS: tuple[ToolName, ...] = (
    ToolName.list_repo_files,
    ToolName.read_repo_file,
    ToolName.write_repo_file,
    ToolName.delete_repo_file,
)
"""装備し、`policy.csv` に allow 行を置く道具（D25 の 4 種だけ）。"""

SOURCE_PATH = "src/app.py"
SOURCE_BODY = "print('クラスタ 03')\n"


def _work_tree(tmp_path: Path) -> Path:
    """`.git/`・ソース 1 つ・秘密ファイル 1 つを持つ一時の作業ツリーを作る。"""
    root = tmp_path / "repo_work"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    (root / SOURCE_PATH).write_text(SOURCE_BODY, encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    return root


def _runtime(
    cluster: Callable[..., ClusterRuntime], work_tree: Path, script: list[dict[str, Any]]
) -> ClusterRuntime:
    return cluster(
        script,
        allowed_tools=REPO_TOOLS,
        declared_tools=REPO_TOOLS,
        repo_local_path=str(work_tree),
    )


def _seed(runtime: ClusterRuntime) -> str:
    """実 `bd` に課題を 1 件仕込み、その issue_id を返す。"""
    return seed_issue(
        runtime.stores.beads,
        title="クラスタ 03 の repo 課題",
        labels=[
            Labels.project(runtime.stores.project_key),
            Labels.kind(KindLabel.child),
            Labels.persona(runtime.stores.persona),
        ],
    )


def _run(runtime: ClusterRuntime, make_spec: Callable[..., CrewSpec]) -> CrewOutcome:
    crew_executor: CrewExecutor = require(runtime.wiring.provided, CrewExecutor)
    spec = make_spec(runtime, issue_id=_seed(runtime))
    assert spec.repos is not None, "前提: 実 settings の Project.repos が CrewSpec に載っている"
    outcome = crew_executor.run(spec)
    denied = [event for event in outcome.events if event.kind is CrewEventKind.tool_denied]
    assert not denied, (
        f"policy の前段で拒否されている（道具が実物まで届いていない）: {[e.detail for e in denied]}"
    )
    return outcome


def _tool_outputs(runtime: ClusterRuntime) -> list[str]:
    """2 往復目以降の要求に載った道具の戻り値（`role="tool"` の本文）。"""
    outputs: list[str] = []
    for request in runtime.llm.requests[1:]:
        for message in request.get("messages", []):
            if message.get("role") == "tool":
                outputs.append(str(message.get("content", "")))
    return outputs


@pytest.mark.contract_if("IF-51")
@pytest.mark.cluster_case("03#16")
def test_cluster_03_list_repo_files_returns_the_real_tree_without_secrets(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec], tmp_path: Path
) -> None:
    """クラスタ 03 #16 / IF-51: 実の作業ツリーの一覧が返り、`.git`・`.env` は載らない。"""
    script = script_tool_calls([make_tool_call(1, ToolName.list_repo_files, {"subdir": ""})])
    runtime = _runtime(cluster, _work_tree(tmp_path), script)

    _run(runtime, make_spec)

    outputs = _tool_outputs(runtime)
    assert any(SOURCE_PATH in output for output in outputs), outputs
    assert not any(".env" in output or ".git/" in output for output in outputs), outputs
    assert runtime.llm.call_count == len(script)


@pytest.mark.contract_if("IF-52")
@pytest.mark.cluster_case("03#16")
def test_cluster_03_read_repo_file_returns_text_and_rejects_outside_paths(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec], tmp_path: Path
) -> None:
    """クラスタ 03 #16 / IF-52: 本文が道具の戻り値に載る。外を指すパスは `RepoPathViolation`。

    違反の種別（`kind`）は道具が文言へ翻訳するので、`RepoReader` を実配線から直接呼んで見る。
    """
    script = script_tool_calls([make_tool_call(1, ToolName.read_repo_file, {"path": SOURCE_PATH})])
    runtime = _runtime(cluster, _work_tree(tmp_path), script)

    _run(runtime, make_spec)

    assert any(SOURCE_BODY.strip() in output for output in _tool_outputs(runtime))
    repo_reader: RepoReader = require(runtime.wiring.provided, RepoReader)
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    repos = settings_reader.get_project(runtime.stores.project_key).repos
    assert repos is not None
    with pytest.raises(RepoPathViolation) as caught:
        repo_reader.read_file(repos, "../outside.txt")
    assert caught.value.kind == "invalid", caught.value.kind
    assert runtime.llm.call_count == len(script)


@pytest.mark.contract_if("IF-54")
@pytest.mark.cluster_case("03#16")
def test_cluster_03_write_repo_file_creates_the_file_in_the_work_tree(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec], tmp_path: Path
) -> None:
    """クラスタ 03 #16 / IF-54: 実の作業ツリーにファイルが作られる（親ディレクトリも作る）。"""
    work_tree = _work_tree(tmp_path)
    body = "def added() -> int:\n    return 2\n"
    script = script_tool_calls(
        [make_tool_call(1, ToolName.write_repo_file, {"path": "src/pkg/added.py", "content": body})]
    )
    runtime = _runtime(cluster, work_tree, script)

    _run(runtime, make_spec)

    assert (work_tree / "src/pkg/added.py").read_text(encoding="utf-8") == body
    assert any("作成した: src/pkg/added.py" in output for output in _tool_outputs(runtime))
    assert runtime.llm.call_count == len(script)


@pytest.mark.contract_if("IF-55")
@pytest.mark.cluster_case("03#16")
def test_cluster_03_delete_repo_file_removes_only_the_file(
    cluster: Callable[..., ClusterRuntime], make_spec: Callable[..., CrewSpec], tmp_path: Path
) -> None:
    """クラスタ 03 #16 / IF-55: 実の作業ツリーからファイルが消える（ディレクトリは残る）。"""
    work_tree = _work_tree(tmp_path)
    script = script_tool_calls(
        [make_tool_call(1, ToolName.delete_repo_file, {"path": SOURCE_PATH})]
    )
    runtime = _runtime(cluster, work_tree, script)

    _run(runtime, make_spec)

    assert not (work_tree / SOURCE_PATH).exists()
    assert (work_tree / "src").is_dir()
    assert any(f"削除した: {SOURCE_PATH}" in output for output in _tool_outputs(runtime))
    assert runtime.llm.call_count == len(script)
