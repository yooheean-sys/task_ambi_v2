"""リポジトリの 4 道具（D25-a）。**表駆動**で書く。

二段許可（`tool:<name>` → `repo:files` の read／write）・未登録の扱い・契約例外の翻訳を見る。
作業ツリーはスタブ（辞書）で模し、実ファイルシステムに触れない。
"""

from __future__ import annotations

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

from common.interfaces.repo import RepoPathViolation
from common.interfaces.settings import ProjectRepos
from common.kernel.enums import ToolName
from executor.models import RunSession
from executor.tools import TOOL_BUILDERS
from executor.tools.base import GuardedTool, ToolContext

REPOS = ProjectRepos(remote_url="https://example.com/x.git", local_path="/work/x")
FILES = {"README.md": "# readme\n", "src/app.py": "print(1)\n"}

READ_TOOLS = (ToolName.list_repo_files, ToolName.read_repo_file)
WRITE_TOOLS = (ToolName.write_repo_file, ToolName.delete_repo_file)

ARGS: dict[ToolName, dict[str, Any]] = {
    ToolName.list_repo_files: {"subdir": ""},
    ToolName.read_repo_file: {"path": "README.md"},
    ToolName.write_repo_file: {"path": "src/new.py", "content": "x = 1\n"},
    ToolName.delete_repo_file: {"path": "src/app.py"},
}


def build_tool(
    name: ToolName,
    *,
    repos: ProjectRepos | None = REPOS,
    authorizer_mode: AuthorizerMode = "allow",
    deny_objs: frozenset[str] = frozenset(),
) -> tuple[GuardedTool, Any]:
    """道具 1 つとスタブ束を組む。"""
    stubs = make_stubs(authorizer_mode=authorizer_mode, deny_objs=deny_objs, repo_files=FILES)
    member = make_member()
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders(), repos=repos)
    context = ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(),
    )
    return TOOL_BUILDERS[name](context), stubs


# --------------------------------------------------------------------------------------
# 二段許可
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [*READ_TOOLS, *WRITE_TOOLS])
def test_second_stage_uses_repo_files_with_read_or_write(name: ToolName) -> None:
    tool, stubs = build_tool(name)
    tool.run(**ARGS[name])
    checks = [(c.kwargs["obj"], c.kwargs["act"]) for c in stubs.log.of("Authorizer.enforce")]
    act = "read" if name in READ_TOOLS else "write"
    assert checks == [(f"tool:{name.value}", "execute"), ("repo:files", act)]


@pytest.mark.parametrize("name", [*READ_TOOLS, *WRITE_TOOLS])
@pytest.mark.parametrize("denied", ["tool", "repo:files"])
def test_denial_at_either_stage_skips_the_repository(name: ToolName, denied: str) -> None:
    obj = f"tool:{name.value}" if denied == "tool" else "repo:files"
    tool, stubs = build_tool(name, authorizer_mode="deny_objs", deny_objs=frozenset({obj}))
    result = tool.run(**ARGS[name])
    assert "許可されていない" in result
    assert not [t for t in stubs.log.targets() if t.startswith("Repo")]


# --------------------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------------------


def test_list_returns_paths() -> None:
    tool, stubs = build_tool(ToolName.list_repo_files)
    assert tool.run(subdir="src") == "src/app.py"
    assert stubs.log.of("RepoReader.list_files")[0].kwargs == {"repos": REPOS, "subdir": "src"}


def test_list_without_subdir_lists_everything() -> None:
    tool, _ = build_tool(ToolName.list_repo_files)
    assert tool.run() == "README.md\nsrc/app.py"


def test_read_returns_content() -> None:
    tool, _ = build_tool(ToolName.read_repo_file)
    assert tool.run(path="src/app.py") == "print(1)\n"


def test_read_missing_is_reported_not_empty() -> None:
    """不在を成功（空文字）に倒さない。"""
    tool, _ = build_tool(ToolName.read_repo_file)
    result = tool.run(path="src/none.py")
    assert result.startswith("ERROR: ")
    assert "ファイルが無い" in result


def test_write_reports_created_then_overwritten() -> None:
    tool, stubs = build_tool(ToolName.write_repo_file)
    assert tool.run(path="src/new.py", content="a").startswith("作成した: src/new.py")
    assert tool.run(path="src/new.py", content="b").startswith("上書きした: src/new.py")
    assert stubs.repo_files.files["src/new.py"] == "b"


def test_delete_removes_and_reports() -> None:
    tool, stubs = build_tool(ToolName.delete_repo_file)
    assert tool.run(path="src/app.py") == "削除した: src/app.py"
    assert "src/app.py" not in stubs.repo_files.files


@pytest.mark.parametrize("name", [*READ_TOOLS, *WRITE_TOOLS])
def test_unregistered_repository_is_reported_without_calling_repo(name: ToolName) -> None:
    tool, stubs = build_tool(name, repos=None)
    result = tool.run(**ARGS[name])
    assert "登録されていない" in result
    assert not [t for t in stubs.log.targets() if t.startswith("Repo")]


@pytest.mark.parametrize("name", [*READ_TOOLS, *WRITE_TOOLS])
def test_contract_exceptions_become_reasons(name: ToolName) -> None:
    tool, stubs = build_tool(name)
    stubs.repo_files.raises = RepoPathViolation(
        "作業ツリーの外を指している: ../x", "outside_repo", "../x"
    )
    result = tool.run(**ARGS[name])
    assert result.startswith("ERROR: 作業ツリーの外を指している")
    assert "repo/ は付けない" in result
