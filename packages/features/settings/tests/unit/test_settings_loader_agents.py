"""`agents.yaml` の ④→⑤ merge（D16-d・FR-030）と台帳・LLM 解決の導出（US1 AC-4〜AC-8）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    collect_problems,
    dump_mapping,
    kinds,
    make_snapshot,
    write_agents,
    write_global_agents,
    write_llm_profiles,
    write_minimal_area_a,
    write_policy,
    write_project,
)

from common.kernel.enums import PersonaKey, ToolName
from settings.service.resolve import (
    bd_actor_of,
    casbin_subject_of,
    derive_threshold_t,
    member_id_of,
    merge_agent_fields,
    resolve_llm_name,
)

if TYPE_CHECKING:
    from pathlib import Path

GLOBAL_PROGRAMMER: dict[str, object] = {
    "role": "プログラマ",
    "goal": "実装を読み解く",
    "backstory": "既定の背景",
    "tools": ["read_issue", "write_outcome"],
    "skills": ["python"],
    "max_iter": 20,
}


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        pytest.param({}, {"role": "プログラマ", "tools": ["read_issue", "write_outcome"]}, id="空"),
        pytest.param(
            {"role": "PJ 専用"},
            {"role": "PJ 専用", "goal": "実装を読み解く"},
            id="フィールド単位で上書き",
        ),
        pytest.param(
            {"tools": ["read_issue"]},
            {"tools": ["read_issue"], "skills": ["python"]},
            id="リストは丸ごと置換（和集合にしない）",
        ),
        pytest.param({"max_iter": 5}, {"max_iter": 5}, id="数値の上書き"),
    ],
)
def test_merge_is_field_wise_and_lists_are_replaced(
    override: dict[str, object], expected: dict[str, object]
) -> None:
    """global を土台に PJ 側の**在るフィールドだけ**を上書きし、リストは丸ごと置換する。"""
    merged = merge_agent_fields(GLOBAL_PROGRAMMER, override)
    for key, value in expected.items():
        assert merged[key] == value


def test_merge_keeps_defaults_when_project_entry_is_empty() -> None:
    """US1 AC-6: PJ 側の項目が空（`{}` も値なしの `None` も）なら既定のまま。"""
    assert merge_agent_fields(GLOBAL_PROGRAMMER, {}) == GLOBAL_PROGRAMMER
    assert merge_agent_fields(GLOBAL_PROGRAMMER, None) == GLOBAL_PROGRAMMER


def test_member_is_the_project_side_key_set(settings_root: Path) -> None:
    """US1 AC-5: PJ 側 `agents.yaml` の**キーの集合がメンバの集合**である。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    snapshot = make_snapshot(settings_root)
    members = snapshot.members["pj"]
    assert set(members) == {PersonaKey.programmer}


def test_project_side_only_persona_without_global_is_reported(settings_root: Path) -> None:
    """global に無い `PersonaKey` が PJ 側に在ると `AGENT_GLOBAL_REQUIRED` の欠落になる。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    agents = {"programmer": GLOBAL_PROGRAMMER}
    write_global_agents(settings_root, dump_mapping(agents))
    write_project(settings_root, "pj", ("programmer", "planner"))
    write_agents(settings_root, "pj", {"programmer": " {}", "planner": " {}"})
    write_policy(
        settings_root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.planner, tool:read_issue, execute",
        ],
    )
    assert "var_required" in kinds(collect_problems(settings_root))


def test_llm_resolution_order_reaches_the_default(settings_root: Path) -> None:
    """US1 AC-8: `llm` は **PJ → global → `default`** の順で 1 件に解決される。"""
    assert resolve_llm_name({"llm": "pj-side"}, "fallback") == "pj-side"
    assert resolve_llm_name({}, "fallback") == "fallback"
    assert resolve_llm_name({}, None) is None
    write_minimal_area_a(settings_root, personas=("programmer",))
    member = make_snapshot(settings_root).members["pj"][PersonaKey.programmer]
    assert member.llm_profile.name == "default"


def test_threshold_is_derived_from_context_window(settings_root: Path) -> None:
    """US1 AC-8: `threshold_t = context_window − 予備トークン`（P5D-013・CFG-37）。"""
    assert derive_threshold_t(32768, 4096) == 28672
    write_minimal_area_a(settings_root, personas=("programmer",))
    member = make_snapshot(settings_root).members["pj"][PersonaKey.programmer]
    assert member.llm_profile.threshold_t == 32768 - 4096


def test_registry_values_are_derived_not_declared(settings_root: Path) -> None:
    """US1 AC-7: 台帳の 3 値は**導出**であり、どの設定ファイルにも書かれていない。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    member = make_snapshot(settings_root).members["pj"][PersonaKey.programmer]
    member_id = member_id_of("pj", PersonaKey.programmer)
    assert member.member_id == member_id == "pj.programmer"
    assert member.bd_actor == bd_actor_of(member_id) == "pj.programmer"
    assert member.casbin_subject == casbin_subject_of(member_id) == "member:pj.programmer"
    for rel in ("global/agents.yaml", "projects/pj/agents.yaml", "projects/pj/project.yaml"):
        text = (settings_root / rel).read_text(encoding="utf-8")
        assert "member:" not in text


def test_project_side_narrows_the_tool_set(settings_root: Path) -> None:
    """D16-d: リスト型は丸ごと置換なので PJ 側で装備を**絞れる**。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    write_global_agents(settings_root, dump_mapping({"programmer": GLOBAL_PROGRAMMER}))
    write_agents(settings_root, "pj", {"programmer": "\n  tools:\n    - read_issue"})
    member = make_snapshot(settings_root).members["pj"][PersonaKey.programmer]
    assert member.tools == [ToolName.read_issue]


def test_profiles_carry_reference_names_only(settings_root: Path) -> None:
    """FR-049: プロファイルは**参照名だけ**を運ぶ（実値を持たない）。"""
    write_minimal_area_a(settings_root, personas=("programmer",))
    write_llm_profiles(settings_root)
    profile = make_snapshot(settings_root).llm_profiles["default"]
    assert profile.api_key_ref == "DEMO_API_KEY_REF"
    assert not hasattr(profile, "api_key")
