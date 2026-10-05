"""保管庫の 5 道具（F06・T018・US4）。**表駆動**で書く。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from conftest import (
    make_bundle,
    make_config,
    make_file_ref,
    make_folders,
    make_member,
    make_stubs,
)

from common.interfaces.executor import CrewEventKind
from common.interfaces.vault import VaultArea, VaultNotFound, VaultPathViolation
from common.kernel.enums import PersonaKey, ToolName
from executor.models import RunSession
from executor.tools import TOOL_BUILDERS
from executor.tools.base import ToolContext
from executor.tools.vault_tools import narrow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from common.interfaces.vault import FileRef
    from executor.tools.base import GuardedTool

VAULT_TOOLS = (
    ToolName.list_input_files,
    ToolName.read_input_file,
    ToolName.list_outcome_files,
    ToolName.read_outcome_file,
    ToolName.write_outcome,
)


def build_tool(
    tool_name: ToolName,
    *,
    files: Sequence[FileRef] = (),
    contents: dict[str, str] | None = None,
    persona: PersonaKey = PersonaKey.programmer,
    role_dirs: dict[PersonaKey, str] | None = None,
    overwritten: bool = False,
    list_raises: BaseException | None = None,
    read_input_raises: BaseException | None = None,
    read_outcome_raises: BaseException | None = None,
    write_raises: BaseException | None = None,
) -> tuple[GuardedTool, Any]:
    """道具 1 つとスタブ束を組む。"""
    stubs = make_stubs(files=files, contents=contents)
    stubs.vault_reader.list_raises = list_raises
    stubs.vault_reader.read_input_raises = read_input_raises
    stubs.vault_reader.read_outcome_raises = read_outcome_raises
    stubs.outcome_writer.raises = write_raises
    stubs.outcome_writer.overwritten = overwritten
    member = make_member(persona_key=persona)
    session = RunSession(issue_id="bd-1", member=member, folders=make_folders(role_dirs=role_dirs))
    context = ToolContext(
        session=session,
        deps=stubs.deps(),
        config=make_config(),
        member=member,
        issue_id="bd-1",
        bundle=make_bundle(),
    )
    return TOOL_BUILDERS[tool_name](context), stubs


# --------------------------------------------------------------------------------------
# 役割の絞り（SC-012・FR-039・FR-039a）
# --------------------------------------------------------------------------------------


def test_narrow_keeps_exactly_one_role() -> None:
    """`narrow()` は `model_copy` で `role_dirs` を 1 件にする。"""
    folders = make_folders()
    assert len(folders.role_dirs) == 3
    narrowed = narrow(folders, PersonaKey.programmer)
    assert narrowed.role_dirs == {PersonaKey.programmer: "roles/programmer"}
    assert narrowed.pj_input_dir == folders.pj_input_dir
    assert narrowed.outcome_dir == folders.outcome_dir
    assert folders.role_dirs == make_folders().role_dirs  # 原本を壊さない


def test_narrow_of_unknown_role_is_empty_not_full() -> None:
    """役割が宣言に無ければ `role_dirs={}` を渡す（**空の一覧に倒さない**＝ FR-039b）。"""
    narrowed = narrow(make_folders(), PersonaKey.analyst)
    assert narrowed.role_dirs == {}


@pytest.mark.parametrize("tool_name", VAULT_TOOLS)
def test_every_vault_tool_narrows_role_dirs(tool_name: ToolName) -> None:
    """保管庫に触れる**すべて**の道具が絞りを通す（他役割が渡る事例 0 件＝ SC-012）。"""
    ref = make_file_ref("a.md", VaultArea.pj_input)
    outcome_ref = make_file_ref("out.md", VaultArea.outcome)
    tool, stubs = build_tool(
        tool_name, files=[ref, outcome_ref], contents={"a.md": "本文", "out.md": "成果"}
    )
    kwargs = {
        ToolName.read_input_file: {"filename": "a.md"},
        ToolName.read_outcome_file: {"filename": "out.md"},
        ToolName.write_outcome: {"filename": "out.md", "content": "書く"},
    }.get(tool_name, {})
    tool.run(**kwargs)

    passed = [
        call.kwargs["folders"]
        for call in stubs.log.calls
        if call.target.startswith(("VaultReader.", "OutcomeWriter."))
    ]
    assert passed, "保管庫の契約が 1 度も呼ばれていない"
    for folders in passed:
        assert set(folders.role_dirs) == {PersonaKey.programmer}


@pytest.mark.parametrize("tool_name", [ToolName.list_input_files, ToolName.read_input_file])
def test_unknown_role_passes_empty_role_dirs(tool_name: ToolName) -> None:
    """役割が宣言に無い → `role_dirs={}` を渡し、保管庫が返す理由を文言にする（FR-039b）。"""
    tool, stubs = build_tool(
        tool_name,
        persona=PersonaKey.analyst,
        list_raises=VaultNotFound("役割フォルダが宣言に無い", rel_path="", role=PersonaKey.analyst),
    )
    kwargs = {"filename": "a.md"} if tool_name is ToolName.read_input_file else {}
    message = tool.run(**kwargs)
    assert message.startswith("ERROR:")
    (call,) = stubs.log.of("VaultReader.list_inputs")
    assert call.kwargs["folders"].role_dirs == {}


# --------------------------------------------------------------------------------------
# 契約の呼び分け（SD-12・SD-13・F-36・F-37）
# --------------------------------------------------------------------------------------


def test_list_input_files_filters_to_input_and_role() -> None:
    """`list_input_files` は `list_inputs` の戻りを `pj_input`／`role` で絞る。"""
    files = [
        make_file_ref("a.md", VaultArea.pj_input),
        make_file_ref("b.md", VaultArea.role),
        make_file_ref("c.md", VaultArea.outcome),
    ]
    tool, stubs = build_tool(ToolName.list_input_files, files=files)
    result = tool.run()
    assert "a.md" in result
    assert "b.md" in result
    assert "c.md" not in result
    assert stubs.log.count("VaultReader.list_inputs") == 1


def test_list_outcome_files_uses_the_same_contract_method() -> None:
    """`list_outcome_files` は**同じ `list_inputs`** を呼び `outcome` で絞る（SD-12）。"""
    files = [
        make_file_ref("a.md", VaultArea.pj_input),
        make_file_ref("c.md", VaultArea.outcome),
    ]
    tool, stubs = build_tool(ToolName.list_outcome_files, files=files)
    result = tool.run()
    assert "c.md" in result
    assert "a.md" not in result
    assert stubs.log.targets() == [
        "Authorizer.enforce",
        "Authorizer.enforce",
        "VaultReader.list_inputs",
    ]


def test_read_input_file_resolves_name_to_file_ref() -> None:
    """名前 → `FileRef` に解決してから `read_input` を呼ぶ（SD-13・F-37）。"""
    ref = make_file_ref("a.md", VaultArea.role)
    tool, stubs = build_tool(ToolName.read_input_file, files=[ref], contents={"a.md": "本文"})
    assert tool.run(filename="a.md") == "本文"
    assert stubs.log.count("VaultReader.list_inputs") == 1
    (call,) = stubs.log.of("VaultReader.read_input")
    assert call.kwargs["ref"] == ref


def test_read_input_file_lists_available_names_when_unresolved() -> None:
    """一致 0 件なら利用可能な名前を列挙した文言（例外にしない）。"""
    tool, stubs = build_tool(
        ToolName.read_input_file, files=[make_file_ref("a.md"), make_file_ref("b.md")]
    )
    message = tool.run(filename="z.md")
    assert message.startswith("ERROR:")
    assert "a.md" in message
    assert "b.md" in message
    assert stubs.log.count("VaultReader.read_input") == 0


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "expected_obj"),
    [
        (ToolName.list_input_files, {}, ["vault:pj_input", "vault:role"]),
        (ToolName.list_outcome_files, {}, ["vault:outcome"]),
        (ToolName.read_outcome_file, {"filename": "out.md"}, ["vault:outcome"]),
        (ToolName.write_outcome, {"filename": "out.md", "content": "x"}, ["vault:outcome"]),
    ],
)
def test_second_stage_objects(
    tool_name: ToolName, kwargs: dict[str, str], expected_obj: list[str]
) -> None:
    """2 段目の `obj` が contracts/guarded-tool.md §3-2 の表どおり。"""
    tool, stubs = build_tool(
        tool_name,
        files=[make_file_ref("out.md", VaultArea.outcome)],
        contents={"out.md": "成果"},
    )
    tool.run(**kwargs)
    objs = [call.kwargs["obj"] for call in stubs.log.of("Authorizer.enforce")]
    assert objs[0] == f"tool:{tool_name.value}"
    assert objs[1:] == expected_obj


def test_read_input_file_second_stage_uses_resolved_area() -> None:
    """`read_input_file` の 2 段目は**解決した `FileRef.area`**（§3-2）。"""
    tool, stubs = build_tool(
        ToolName.read_input_file,
        files=[make_file_ref("a.md", VaultArea.role)],
        contents={"a.md": "本文"},
    )
    tool.run(filename="a.md")
    objs = [call.kwargs["obj"] for call in stubs.log.of("Authorizer.enforce")]
    assert objs == ["tool:read_input_file", "vault:role"]


# --------------------------------------------------------------------------------------
# 例外の翻訳（FR-041・FN-13・SC-013）
# --------------------------------------------------------------------------------------

VAULT_ERRORS: list[BaseException] = [
    VaultNotFound("そのファイルは無い", rel_path="pj/input/z.md", role=None),
    VaultPathViolation("範囲外を指した", kind="outside_root", detail=".."),
]


TRANSLATION_CASES: list[tuple[ToolName, dict[str, str], str]] = [
    (ToolName.list_input_files, {}, "list_raises"),
    (ToolName.list_outcome_files, {}, "list_raises"),
    (ToolName.read_input_file, {"filename": "a.md"}, "read_input_raises"),
    (ToolName.read_outcome_file, {"filename": "a.md"}, "read_outcome_raises"),
    (ToolName.write_outcome, {"filename": "a.md", "content": "x"}, "write_raises"),
]


@pytest.mark.parametrize("exc", VAULT_ERRORS, ids=["not-found", "path-violation"])
@pytest.mark.parametrize(("tool_name", "kwargs", "raise_kw"), TRANSLATION_CASES)
def test_vault_errors_become_return_values(
    exc: BaseException, tool_name: ToolName, kwargs: dict[str, str], raise_kw: str
) -> None:
    """`VaultNotFound`／`VaultPathViolation` は**すべて戻り値の理由**（呼び手へ 0 件）。"""
    raises: dict[str, BaseException | None] = dict.fromkeys(
        ("list_raises", "read_input_raises", "read_outcome_raises", "write_raises")
    )
    raises[raise_kw] = exc
    tool, _ = build_tool(
        tool_name,
        files=[make_file_ref("a.md", VaultArea.pj_input)],
        list_raises=raises["list_raises"],
        read_input_raises=raises["read_input_raises"],
        read_outcome_raises=raises["read_outcome_raises"],
        write_raises=raises["write_raises"],
    )
    message = tool.run(**kwargs)
    assert isinstance(message, str)
    assert message.startswith("ERROR:")


def test_missing_outcome_is_not_reported_as_success() -> None:
    """**不在を成功に倒さない**（FN-13）。"""
    tool, _ = build_tool(
        ToolName.read_outcome_file,
        read_outcome_raises=VaultNotFound("無い", rel_path="pj/outcome/x.md", role=None),
    )
    message = tool.run(filename="x.md")
    assert message.startswith("ERROR:")
    assert message != ""


def test_absolute_paths_are_not_leaked() -> None:
    """文言に保管庫の絶対パスを入れない（§4・OB-6）。"""
    tool, _ = build_tool(
        ToolName.read_outcome_file,
        read_outcome_raises=VaultNotFound("無い", rel_path="pj/outcome/x.md", role=None),
    )
    assert "/mnt/" not in tool.run(filename="x.md")


# --------------------------------------------------------------------------------------
# 書込（FR-040）
# --------------------------------------------------------------------------------------


def test_write_outcome_records_written_path() -> None:
    """戻りの `WrittenPath` が `session.written` に積まれる。"""
    tool, _ = build_tool(ToolName.write_outcome)
    tool.run(filename="out.md", content="本文")
    assert [w.rel_path for w in tool.ctx.session.written] == ["outcome/out.md"]
    assert tool.ctx.session.events == []


def test_write_outcome_records_overwrite_event() -> None:
    """`overwritten=True` なら `outcome_overwritten` を 1 件（FR-040）。"""
    tool, _ = build_tool(ToolName.write_outcome, overwritten=True)
    tool.run(filename="out.md", content="本文")
    kinds = [event.kind for event in tool.ctx.session.events]
    assert kinds == [CrewEventKind.outcome_overwritten]
    assert tool.ctx.session.events[0].detail["path"] == "outcome/out.md"
