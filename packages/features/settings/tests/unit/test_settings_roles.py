"""D26: 役割ごとの実効設定（`GET /settings/projects/{project_key}/roles` の中身）。

装備（merge 済み `agents.yaml` の `tools`）と許可（`policy.csv` を判定器に掛けた結果）を
Tool ごとに並べ、2 段目の許可・段の既定担当・段の `tools:` を役割ごとに返す。
**閲覧のみ**で、領域 A を 1 バイトも書かない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from settings_testing import (
    area_bytes,
    default_agents,
    default_tasks,
    dump_mapping,
    make_crud,
    write,
    write_agents,
    write_minimal_area_a,
    write_policy,
    write_tasks,
)

from common.interfaces.settings import ProjectNotFound
from common.kernel.enums import PersonaKey, ToolName
from settings.schemas import PolicyGrant, RoleStageView, RoleToolView, StageToolsView
from settings.service import SettingsService

if TYPE_CHECKING:
    from pathlib import Path

    from settings.schemas import RolesView, RoleView

POLICY_ROWS = (
    "p, member:pj.programmer, tool:read_issue, execute",
    "p, member:pj.programmer, tool:write_repo_file, execute",
    "p, member:pj.programmer, vault:outcome, write",
    "p, member:pj.programmer, repo:files, read",
    "p, member:pj.system_engineer, tool:read_issue, execute",
    "g, member:pj.system_engineer, role:lead",
    "p, role:lead, tool:propose_breakdown, execute",
    "p, role:lead, vault:pj_input, read",
)
"""programmer は直接の行だけ、system_engineer は `g` 行で役割 `role:lead` を継ぐ。"""


def _area(root: Path) -> None:
    """programmer と system_engineer の 2 人が居る一時領域 A（上の権限行つき）。"""
    write_minimal_area_a(root, personas=("programmer", "system_engineer"))
    write_policy(root, "pj", POLICY_ROWS)


def _roles(root: Path, project_key: str = "pj") -> RolesView:
    """一時領域 A を読んで役割の一覧を返す。"""
    return make_crud(root).list_project_roles(project_key)


def _role(view: RolesView, persona: PersonaKey) -> RoleView:
    """一覧から 1 人を引く。"""
    return next(role for role in view.roles if role.persona_key is persona)


def _pairs(role: RoleView) -> dict[ToolName, tuple[bool, bool]]:
    """Tool → （装備, 許可）。"""
    return {row.tool: (row.equipped, row.allowed) for row in role.tools}


def test_members_are_listed_in_persona_order(settings_root: Path) -> None:
    """メンバ全員を `PersonaKey` の列挙順に返す（ファイルの記載順ではない）。"""
    _area(settings_root)

    view = _roles(settings_root)

    assert view.project_key == "pj"
    assert [role.persona_key for role in view.roles] == [
        PersonaKey.system_engineer,
        PersonaKey.programmer,
    ]


def test_equipped_and_allowed_are_compared_per_tool(settings_root: Path) -> None:
    """装備のみ・許可のみ・両方の 3 通りが見分けられ、どちらでもない Tool は出ない。"""
    _area(settings_root)

    pairs = _pairs(_role(_roles(settings_root), PersonaKey.programmer))

    assert pairs[ToolName.read_issue] == (True, True)
    assert pairs[ToolName.record_progress] == (True, False)
    assert pairs[ToolName.write_repo_file] == (False, True)
    assert ToolName.propose_breakdown not in pairs
    assert ToolName.submit_review not in pairs


def test_tools_follow_the_enum_order(settings_root: Path) -> None:
    """Tool の並びは `ToolName` の列挙順（画面の表が毎回同じ並びになる）。"""
    _area(settings_root)

    tools = [row.tool for row in _role(_roles(settings_root), PersonaKey.programmer).tools]

    assert tools == [tool for tool in ToolName if tool in tools]


def test_inherited_roles_count_as_allowed(settings_root: Path) -> None:
    """`g` 行で継いだ役割の許可も効く（判定器に任せるので keyMatch・継承を再実装しない）。"""
    _area(settings_root)

    role = _role(_roles(settings_root), PersonaKey.system_engineer)

    assert _pairs(role)[ToolName.propose_breakdown] == (True, True)
    assert PolicyGrant(obj="vault:pj_input", act="read") in role.grants


def test_grants_exclude_tool_rows(settings_root: Path) -> None:
    """2 段目の許可は `tool:` 以外の行だけ（記載順）。"""
    _area(settings_root)

    role = _role(_roles(settings_root), PersonaKey.programmer)

    assert role.grants == [
        PolicyGrant(obj="vault:outcome", act="write"),
        PolicyGrant(obj="repo:files", act="read"),
    ]


def test_member_fields_are_resolved(settings_root: Path) -> None:
    """role・goal・LLM は merge・解決済みの値（全体の既定から引かれる）。"""
    _area(settings_root)

    role = _role(_roles(settings_root), PersonaKey.programmer)

    assert role.member_id == "pj.programmer"
    assert role.casbin_subject == "member:pj.programmer"
    assert role.role == "プログラマ（子AI）"
    assert role.goal
    assert role.llm_profile


def test_default_stage_assignment_comes_from_the_templates(settings_root: Path) -> None:
    """段の既定担当は重ねた段テンプレートの `agent:`（出荷時既定では subrequest が SE）。"""
    _area(settings_root)

    view = _roles(settings_root)

    assert _role(view, PersonaKey.system_engineer).stages == [
        RoleStageView(stage="subrequest", replaces_tools=False, tools=[])
    ]
    assert _role(view, PersonaKey.programmer).stages == []


def test_stage_tools_replace_the_equipment(settings_root: Path) -> None:
    """段に `tools:` があれば、その段での装備（段の Tool）と許可の対照を返す。"""
    _area(settings_root)
    child = dict(default_tasks()["child"])
    child.update(agent="programmer", tools=["read_issue", "submit_review"])
    write_tasks(settings_root, "pj", dump_mapping({"child": child}))

    stages = _role(_roles(settings_root), PersonaKey.programmer).stages

    assert stages == [
        RoleStageView(
            stage="child",
            replaces_tools=True,
            tools=[
                RoleToolView(tool=ToolName.read_issue, equipped=True, allowed=True),
                RoleToolView(tool=ToolName.submit_review, equipped=True, allowed=False),
            ],
        )
    ]


def test_unassigned_stages_with_tools_are_listed_separately(settings_root: Path) -> None:
    """`agent:` を持たずに `tools:` を持つ段（出荷時既定では review の 2 段）は別に返す。"""
    _area(settings_root)

    view = _roles(settings_root)

    defaults = default_tasks()
    assert view.stage_tools == [
        StageToolsView(
            stage=key, tools=[ToolName(name) for name in cast("list[str]", defaults[key]["tools"])]
        )
        for key in ("review_normal", "review_final")
    ]


def test_a_project_without_members_has_no_roles(settings_root: Path) -> None:
    """メンバ 0 人の PJ は空の一覧（失敗にしない）。"""
    _area(settings_root)
    write(
        settings_root,
        "projects/empty/project.yaml",
        "name: 空\nis_reserved: false\nfolders:\n  pj_input_dir: empty\n  role_dirs: {}\n"
        "  outcome_dir: empty/outcome\n",
    )
    write_agents(settings_root, "empty", {})
    write_policy(settings_root, "empty", ())

    assert _roles(settings_root, "empty").roles == []


def test_an_unknown_project_is_not_found(settings_root: Path) -> None:
    """未登録のキーは `ProjectNotFound`（→ 404）。"""
    _area(settings_root)

    with pytest.raises(ProjectNotFound):
        _roles(settings_root, "nope")


def test_reading_roles_writes_nothing(settings_root: Path) -> None:
    """閲覧のみ: 領域 A のどのファイルも変わらない。"""
    _area(settings_root)
    before = area_bytes(settings_root)

    _roles(settings_root)

    assert area_bytes(settings_root) == before


def test_only_equipped_or_named_tools_are_judged(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 判定器に掛けるのは装備と、権限表に名前で書かれた Tool だけ（1 Tool につき 1 回）。

    判定器（casbin）は app の中では判定 1 件ごとにログを出し、拒否は WARNING になる。16 種すべてを
    判定すると、表示 1 回で実行時には起きない拒否が並ぶ。ログは単体テストでは無効なので
    （`casbin.Enforcer` が生成時に自分のロガーを止める）、判定の呼び出しそのものを数える。
    """
    _area(settings_root)
    crud = make_crud(settings_root)
    judged: list[str] = []
    original = SettingsService.enforce

    def spy(self: SettingsService, subject: str, obj: str, act: str) -> bool:
        if subject == "member:pj.programmer":
            judged.append(obj)
        return original(self, subject, obj, act)

    monkeypatch.setattr(SettingsService, "enforce", spy)

    crud.list_project_roles("pj")

    equipped = {
        f"tool:{name}" for name in cast("list[str]", default_agents()["programmer"]["tools"])
    }
    assert set(judged) == equipped | {"tool:write_repo_file"}
    assert len(judged) == len(set(judged)), "同じ Tool を 2 回判定している"


def test_a_pattern_row_falls_back_to_every_tool(settings_root: Path) -> None:
    """D26: 名前でない対象（`tool:*`）が 1 行でもあれば、照合は判定器に任せて全種を判定する。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    write_policy(settings_root, "pj", ("p, member:pj.programmer, tool:*, execute",))

    role = _role(_roles(settings_root), PersonaKey.programmer)

    assert [row.tool for row in role.tools] == list(ToolName)
    assert all(row.allowed for row in role.tools)
