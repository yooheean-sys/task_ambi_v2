"""US3: メンバ CRUD（settings-F12）。plan (ii) F12-a〜c。

検証行: SC-012（`role_dirs` 未登録は 100% 拒否）・FR-046〜FR-051 ＋ P4・P5D-018。

⚠ **`PUT`／`DELETE /settings/agents/{pj}/{persona}` は「新規作成」と「削除」については原理的に
到達不能**である（**B-49**。B-40 と同型の相互ロック）—— **V-19**（メンバ ⊆ policy 主体）と
**V-21**（policy 主体 ⊆ 台帳）が同じ重さで候補全体に当たり、口が触る領域 A のファイルは
`agents.yaml` **だけ**（contracts/settings-http.md §1 #10・#11）なので、メンバと権限行を同じ
候補に含められない。**契約を 1 行も変えず規則どおり実装し、行き止まりをそのまま固定する**
（B-40 に対する裁定と同じ扱い）。到達可能なのは**既に居るメンバの項目の編集**である。

基名は既存の `test_settings_members.py`（前半）と**重複させない**（FR-105）。
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

import pytest
from settings_testing import (
    make_crud,
    make_data,
    make_snapshot,
    write_agents,
    write_minimal_area_a,
    write_policy,
    write_project,
)

from common.interfaces.settings import MemberNotFound, ProjectNotFound
from common.kernel.enums import PersonaKey
from settings.schemas import AgentSpec, McpSpecSchema
from settings.service import members as member_service
from settings.service.validate import validate_forward, validate_reverse

if TYPE_CHECKING:
    from pathlib import Path

    from settings.models import AreaAData, ValidationProblem


def _problems(root: Path, candidate: AreaAData) -> list[ValidationProblem]:
    """候補に前半の検査をそのまま当てる（保存経路が当てるのと同じ関数＝ SC-013）。"""
    from settings_testing import config_for_tests  # noqa: PLC0415

    del root
    config = config_for_tests()
    problems = validate_forward(
        candidate,
        context_reserve_tokens=config.context_reserve_tokens,
        system_actor=config.system_actor,
    )
    problems.extend(validate_reverse(candidate))
    return problems


def _two_member_area(root: Path) -> None:
    """`programmer` と `analyst` の 2 人が居る一時領域 A（権限行つき）。"""
    write_minimal_area_a(root, personas=("programmer", "analyst"))


# -- T047 `role_dirs` 拒否の表（P5D-018・FR-049・SC-012・P4） ----------------------------


def test_sc012_a_member_without_a_role_dir_is_always_refused(settings_root: Path) -> None:
    """SC-012・FR-049・P5D-018: 役割フォルダが未登録のペルソナのメンバは **100% 拒否**。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    data = make_data(settings_root)
    candidate, _changes = member_service.build_put(
        data, "pj", PersonaKey.analyst, AgentSpec(role="a", goal="b", backstory="c")
    )

    reasons = [item.reason for item in _problems(settings_root, candidate)]

    assert any("role_dirs に無い" in reason for reason in reasons), reasons


def test_the_same_rule_fires_when_a_role_dir_is_dropped(settings_root: Path) -> None:
    """P4・C-07: 編集で `role_dirs` から外すときも**同じ V-17** が落とす（新規則を足さない）。"""
    _two_member_area(settings_root)
    data = make_data(settings_root)
    raw = dict(data.projects_raw["pj"])
    folders = dict(cast("dict[str, object]", raw["folders"]))
    folders["role_dirs"] = {"programmer": "pj/roles/programmer"}
    candidate = dataclasses.replace(data, projects_raw={"pj": {**raw, "folders": folders}})

    reasons = [item.reason for item in _problems(settings_root, candidate)]

    assert any("role_dirs に無い" in reason and "analyst" in reason for reason in reasons)


# -- T048 許可項目の表（V-03・V-05・V-06・ペルソナ 9 種） -------------------------------

FORBIDDEN_BODY_CASES: tuple[tuple[str, dict[str, object]], ...] = (
    ("unknown_field", {"nickname": "x"}),
    ("forbidden_field", {"allow_delegation": True}),
    ("secret_key", {"api_key": "sk-x"}),
    ("unknown_persona_field", {"persona_key": "programmer"}),
)


@pytest.mark.parametrize(
    ("label", "body"), FORBIDDEN_BODY_CASES, ids=[case[0] for case in FORBIDDEN_BODY_CASES]
)
def test_the_dto_refuses_anything_outside_the_ten_allowed_fields(
    label: str, body: dict[str, object]
) -> None:
    """FR-079a・US3 AC-4: 許可 10 の外・禁止キー・秘密語は**口の型が 422 で締め出す**。"""
    del label
    with pytest.raises(ValueError, match="[Ee]xtra"):
        AgentSpec.model_validate(body)


@pytest.mark.parametrize(
    ("label", "body"), FORBIDDEN_BODY_CASES, ids=[case[0] for case in FORBIDDEN_BODY_CASES]
)
def test_the_candidate_rules_also_refuse_them(
    label: str, body: dict[str, object], settings_root: Path
) -> None:
    """V-03・V-05・V-06: 人が直接書いた同じ形は**候補の検査**が拒否する（規則は前半のまま）。"""
    del label
    write_minimal_area_a(settings_root)
    data = make_data(settings_root)
    candidate = dataclasses.replace(
        data,
        agents_raw={
            "pj": {
                "programmer": {
                    **cast("dict[str, object]", data.agents_raw["pj"]["programmer"]),
                    **body,
                }
            }
        },
    )

    kinds = {item.kind for item in _problems(settings_root, candidate)}

    assert kinds & {"unknown_field", "forbidden_key", "agent_ref"}, kinds


def test_an_unknown_persona_key_is_refused_by_the_enum(settings_root: Path) -> None:
    """US3 AC-4: 既知 9 種でないペルソナキーは `PersonaKey` の列挙が締め出す。"""
    del settings_root
    with pytest.raises(ValueError, match="qa"):
        PersonaKey("qa")


# -- T049 重ね合わせ（FR-046・FR-047・US3 AC-1・AC-2） -----------------------------------


def test_an_empty_member_inherits_the_global_defaults(settings_root: Path) -> None:
    """FR-046・US3 AC-1・AC-2: **キーの存在＝メンバ**。中身が空でも既定のまま参加する。"""
    write_minimal_area_a(settings_root)
    snapshot = make_snapshot(settings_root)

    member = snapshot.members["pj"][PersonaKey.programmer]
    default = snapshot.agents_global[PersonaKey.programmer]

    assert (member.role, member.goal, member.backstory) == (
        default.role,
        default.goal,
        default.backstory,
    )


def test_only_the_supplied_fields_override_the_defaults(settings_root: Path) -> None:
    """FR-047: 一部フィールドだけを持つメンバは**指定した項目だけ**が上書きされる。"""
    write_minimal_area_a(settings_root)
    data = make_data(settings_root)
    candidate, changes = member_service.build_put(
        data, "pj", PersonaKey.programmer, AgentSpec(role="上書きした役割")
    )

    written = cast("dict[str, object]", candidate.agents_raw["pj"]["programmer"])
    assert written == {"role": "上書きした役割"}, "送っていない項目まで書き出している"
    assert [write.rel for write in changes.writes] == ["projects/pj/agents.yaml"]


def test_list_fields_are_replaced_wholesale_not_merged(settings_root: Path) -> None:
    """FR-047・US3 AC-2: 一覧型（`tools`・`skills`・`mcps`）は **merge せず丸ごと置換**。"""
    write_minimal_area_a(settings_root)
    write_global_agents_with_tools(settings_root)
    data = make_data(settings_root)
    spec = AgentSpec(
        tools=["read_issue"],  # pyright: ignore[reportArgumentType]  # ToolName は StrEnum
        skills=["s"],
        mcps=[McpSpecSchema(name="m", transport="stdio", command="c", args=[])],
    )
    candidate, _changes = member_service.build_put(data, "pj", PersonaKey.programmer, spec)
    written = cast("dict[str, object]", candidate.agents_raw["pj"]["programmer"])

    assert written["tools"] == ["read_issue"], "global 側の tools と和集合になっている"
    assert written["skills"] == ["s"]
    assert written["mcps"] == [{"name": "m", "transport": "stdio", "command": "c", "args": []}]


def write_global_agents_with_tools(root: Path) -> None:
    """`global/agents.yaml` の `programmer` に `tools` を 2 件持たせる（置換の観測用）。"""
    from settings_testing import default_agents, dump_mapping, write  # noqa: PLC0415

    agents = default_agents()
    agents["programmer"]["tools"] = ["read_issue", "write_outcome"]
    write(root, "global/agents.yaml", dump_mapping(dict(agents.items())))


def test_editing_the_global_default_writes_only_the_global_file(settings_root: Path) -> None:
    """F12-c: 全体のペルソナ既定の編集は `global/agents.yaml` の 1 ファイルだけを触る。"""
    write_minimal_area_a(settings_root)
    data = make_data(settings_root)

    candidate, changes = member_service.build_put_global(
        data, PersonaKey.programmer, AgentSpec(role="r", goal="g", backstory="b")
    )

    assert [write.rel for write in changes.writes] == ["global/agents.yaml"]
    assert candidate.agents_global["programmer"] == {"role": "r", "goal": "g", "backstory": "b"}


def test_a_global_default_missing_the_three_required_fields_is_refused(settings_root: Path) -> None:
    """V-04: `global/agents.yaml` の各ペルソナに `role`・`goal`・`backstory` が要る。"""
    write_minimal_area_a(settings_root)
    data = make_data(settings_root)

    candidate, _changes = member_service.build_put_global(
        data, PersonaKey.programmer, AgentSpec(role="r")
    )

    kinds = {item.kind for item in _problems(settings_root, candidate)}
    assert "var_required" in kinds


# -- T050 削除と、その手前にある相互ロック（**B-49**） ----------------------------------


def test_deleting_a_member_whose_policy_rows_remain_is_refused_by_v21(settings_root: Path) -> None:
    """FR-050・FR-051・US3 AC-6: 権限表に主体の行が残っていれば **V-21** が落とす。"""
    _two_member_area(settings_root)
    data = make_data(settings_root)

    candidate, _changes = member_service.build_delete(data, "pj", PersonaKey.analyst)

    problems = _problems(settings_root, candidate)
    assert [item.kind for item in problems] == ["orphan"]
    assert "member:pj.analyst" in problems[0].reason


def test_b49_removing_the_policy_row_first_is_refused_by_v19(settings_root: Path) -> None:
    """**B-49**: 権限行を先に消す道も **V-19** で塞がっている（相互ロックの片側）。

    B-40（`POST /settings/commands` の V-24／V-25）と同型の行き止まりであり、**契約を 1 行も
    変えずに規則どおり実装した結果**である。ここではその行き止まりを**そのまま固定する**。
    """
    _two_member_area(settings_root)
    data = make_data(settings_root)
    kept = tuple(row for row in data.policy_rows["pj"] if "analyst" not in row[1])
    candidate = dataclasses.replace(data, policy_rows={"pj": kept})

    problems = _problems(settings_root, candidate)

    assert [item.kind for item in problems] == ["forward"]
    assert "policy に主体の行が 1 つも無い" in problems[0].reason


def test_b49_creating_a_member_is_locked_the_same_way(settings_root: Path) -> None:
    """**B-49**: 新規メンバの作成も同じ相互ロックで到達不能（**編集だけが通る**）。"""
    write_minimal_area_a(settings_root)
    write_project(settings_root, "pj", ("programmer", "analyst"))
    data = make_data(settings_root)

    candidate, _changes = member_service.build_put(data, "pj", PersonaKey.analyst, AgentSpec())

    problems = _problems(settings_root, candidate)
    assert [item.kind for item in problems] == ["forward"]
    assert "policy に主体の行が 1 つも無い" in problems[0].reason


def test_a_member_and_its_rows_removed_together_drops_the_registry_entries(
    settings_root: Path,
) -> None:
    """FR-050: **ファイルに書かず導出する**台帳から `bd_actor`・`casbin_subject` が消える。

    口からは B-49 の相互ロックで到達できない状態だが、**台帳が導出であること**（FR-037）の
    観測はここで行う（領域 A を直接この形に置いて `build_snapshot` を掛ける）。
    """
    _two_member_area(settings_root)
    before = make_snapshot(settings_root)
    assert "member:pj.analyst" in before.registry.subjects

    write_agents(settings_root, "pj", {"programmer": " {}"})
    write_policy(settings_root, "pj", ["p, member:pj.programmer, tool:read_issue, execute"])
    after = make_snapshot(settings_root)

    assert "member:pj.analyst" not in after.registry.subjects
    assert "pj.analyst" not in after.registry.bd_actors
    body = (settings_root / "projects/pj/agents.yaml").read_text(encoding="utf-8")
    assert "casbin_subject" not in body, "導出値をファイルに書いている"


def test_a_member_pointing_at_an_unknown_llm_profile_is_refused(settings_root: Path) -> None:
    """V-14・V-15・US3 AC-7: 存在しない LLM プロファイル名を指したメンバは拒否される。"""
    write_minimal_area_a(settings_root)
    data = make_data(settings_root)

    candidate, _changes = member_service.build_put(
        data, "pj", PersonaKey.programmer, AgentSpec(llm="居ないプロファイル")
    )

    kinds = {item.kind for item in _problems(settings_root, candidate)}
    assert "llm_ref" in kinds


# -- 404 の経路（FR-075・SC-015。**正本に 404 は無いが D-3 が定める**） ------------------


def test_an_unknown_project_is_not_found_on_put(settings_root: Path) -> None:
    """D-3・FR-075: 未登録 PJ への upsert は **404**。"""
    write_minimal_area_a(settings_root)

    with pytest.raises(ProjectNotFound):
        member_service.build_put(
            make_data(settings_root), "居ない", PersonaKey.programmer, AgentSpec()
        )


def test_an_unknown_member_is_not_found_on_delete(settings_root: Path) -> None:
    """FR-075: 未登録メンバの削除は **404**。"""
    write_minimal_area_a(settings_root)

    with pytest.raises(MemberNotFound):
        member_service.build_delete(make_data(settings_root), "pj", PersonaKey.analyst)


def test_editing_an_existing_member_actually_goes_through_the_port(settings_root: Path) -> None:
    """B-49 の裏返し: **既に居るメンバの編集は口から通る**（世代が 1 増える）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)

    view, outcome = crud.put_agent("pj", PersonaKey.programmer, AgentSpec(max_iter=5))

    assert view.max_iter == 5
    assert outcome.generation == 2
    body = (settings_root / "projects/pj/agents.yaml").read_text(encoding="utf-8")
    assert "max_iter: 5" in body
