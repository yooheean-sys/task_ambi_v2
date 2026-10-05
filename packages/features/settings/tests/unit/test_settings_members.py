"""`MemberResolver` の 2 メソッド（IF-27・IF-28。settings-F07・F08）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    SYSTEM_ACTOR,
    collect_problems,
    dump_mapping,
    kinds,
    make_service,
    write_agents,
    write_global_agents,
    write_humans,
    write_minimal_area_a,
    write_policy,
    write_project,
)

from common.interfaces.settings import MemberNotFound
from common.kernel.enums import PersonaKey, ToolName

if TYPE_CHECKING:
    from pathlib import Path

GLOBAL_AGENTS: dict[str, object] = {
    "programmer": {
        "role": "プログラマ",
        "goal": "実装を読み解く",
        "backstory": "既定の背景",
        "tools": ["read_issue", "write_outcome"],
        "max_iter": 20,
    },
    "planner": {
        "role": "計画担当",
        "goal": "分解する",
        "backstory": "既定の背景",
        "tools": ["read_issue"],
    },
}


def _two_member_area(root: Path) -> None:
    write_minimal_area_a(root, personas=("programmer",))
    write_global_agents(root, dump_mapping(GLOBAL_AGENTS))
    write_project(root, "pj", ("programmer", "planner"))
    write_agents(root, "pj", {"programmer": " {}", "planner": "\n  goal: PJ 専用の目的"})
    write_policy(
        root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.planner, tool:read_issue, execute",
        ],
    )


# -- resolve（IF-27・F07） -------------------------------------------------------------


def test_resolve_returns_the_merged_member(settings_root: Path) -> None:
    """US1 AC-4: フィールド単位で重ねた結果が返る。"""
    _two_member_area(settings_root)
    member = make_service(settings_root).resolve("pj", PersonaKey.planner)
    assert member.role == "計画担当"
    assert member.goal == "PJ 専用の目的"
    assert member.tools == [ToolName.read_issue]


def test_resolve_returns_defaults_when_the_project_entry_is_empty(settings_root: Path) -> None:
    """US1 AC-6: キーが在って中身が空なら既定のままのメンバ。"""
    _two_member_area(settings_root)
    member = make_service(settings_root).resolve("pj", PersonaKey.programmer)
    assert member.goal == "実装を読み解く"
    assert member.max_iter == 20
    assert member.tools == [ToolName.read_issue, ToolName.write_outcome]


def test_resolve_raises_when_the_project_side_key_is_absent(settings_root: Path) -> None:
    """US1 AC-5: PJ 側にキーが無ければ**ペルソナ既定だけでメンバを作らない**。"""
    _two_member_area(settings_root)
    with pytest.raises(MemberNotFound):
        make_service(settings_root).resolve("pj", PersonaKey.writer)


def test_resolve_raises_for_unknown_project(settings_root: Path) -> None:
    """`resolve` の契約例外は 1 種だけ（PJ 未登録も `MemberNotFound`）。"""
    _two_member_area(settings_root)
    with pytest.raises(MemberNotFound):
        make_service(settings_root).resolve("nope", PersonaKey.programmer)


def test_resolve_carries_the_registry_values(settings_root: Path) -> None:
    """US1 AC-7: 台帳の 3 値が導出されている。"""
    _two_member_area(settings_root)
    member = make_service(settings_root).resolve("pj", PersonaKey.programmer)
    assert (member.member_id, member.bd_actor, member.casbin_subject) == (
        "pj.programmer",
        "pj.programmer",
        "member:pj.programmer",
    )


def test_resolve_carries_one_resolved_profile(settings_root: Path) -> None:
    """US1 AC-8: LLM が 1 件に解決され `threshold_t` が載る。"""
    _two_member_area(settings_root)
    member = make_service(settings_root).resolve("pj", PersonaKey.programmer)
    assert member.llm_profile.name == "default"
    assert member.llm_profile.threshold_t == 32768 - 4096


def test_same_persona_in_two_projects_are_distinct_members(settings_root: Path) -> None:
    """Edge Cases: 同じペルソナが 2 つの PJ に居れば**別のメンバ**（ID が違う）。"""
    write_minimal_area_a(settings_root, projects=("pj", "other"))
    service = make_service(settings_root)
    first = service.resolve("pj", PersonaKey.programmer)
    second = service.resolve("other", PersonaKey.programmer)
    assert first.member_id != second.member_id
    assert {first.bd_actor, second.bd_actor} == {"pj.programmer", "other.programmer"}


# -- is_human（IF-28・F08） ------------------------------------------------------------


def test_is_human_for_ai_member(settings_root: Path) -> None:
    """US5 AC-1: 台帳の AI メンバは「人間でない」。"""
    write_minimal_area_a(settings_root)
    assert make_service(settings_root).is_human("pj.programmer") is False


def test_is_human_for_declared_human(settings_root: Path) -> None:
    """US5 AC-2: 宣言された人間は「人間である」。"""
    write_minimal_area_a(settings_root)
    write_humans(settings_root, ["owner"])
    assert make_service(settings_root).is_human("owner") is True


def test_is_human_for_system_actor_leaves_no_mark(
    settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """US5 AC-3・P5D-037: システム自身は「人間でない」＋**台帳外の印は残らない**。"""
    write_minimal_area_a(settings_root)
    service = make_service(settings_root)
    with caplog.at_level(logging.WARNING, logger="settings.service"):
        assert service.is_human(SYSTEM_ACTOR) is False
    assert "settings.is_human.unknown_actor" not in caplog.text


def test_is_human_for_unknown_actor_leaves_a_mark(
    settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """US5 AC-4・D06-c: どれにも当たらないなら「人間でない」＋**台帳外の印**。"""
    write_minimal_area_a(settings_root)
    service = make_service(settings_root)
    with caplog.at_level(logging.WARNING, logger="settings.service"):
        assert service.is_human("誰か") is False
    assert "settings.is_human.unknown_actor" in caplog.text
    assert any(getattr(record, "actor", None) == "誰か" for record in caplog.records)


def test_is_human_with_an_empty_declaration_file(settings_root: Path) -> None:
    """US5 AC-5（裁定 2026-08-24）: 雛形が置いた**空の**宣言ファイルなら人間 0 名。

    「ファイルが無い」ではない — CFG-81 が必須と定め、雛形生成が必ず作るため、**不在は
    人が消した状態**として起動を中断する（`test_settings_build.py` が観測する）。
    """
    write_minimal_area_a(settings_root)
    body = (settings_root / "global/humans.yaml").read_text(encoding="utf-8")
    assert body.strip() == "human_actors: []"
    assert make_service(settings_root).is_human("誰か") is False


def test_is_human_never_raises(settings_root: Path) -> None:
    """FR-034: 例外を送出しない（4 分岐のいずれでも）。"""
    write_minimal_area_a(settings_root)
    service = make_service(settings_root)
    for actor in ("pj.programmer", SYSTEM_ACTOR, "", "member:pj.programmer"):
        assert isinstance(service.is_human(actor), bool)


def test_actor_name_collision_is_rejected_at_startup(settings_root: Path) -> None:
    """V-20: 3 集合の衝突そのものを拒否するので、判定順序に依存した勝敗を作らない。"""
    write_minimal_area_a(settings_root)
    write_humans(settings_root, ["pj.programmer"])
    assert "forward" in kinds(collect_problems(settings_root))
