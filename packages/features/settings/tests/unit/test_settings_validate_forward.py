"""順方向の整合検査（F18）。contracts/validation-rules.md §2・§7 の 18 行をそのまま表にする。

SC-008 の「10 通り以上」はこの表で満たす。**14 行目を外さない** — 実測 M-15 (b) が
明らかにした「**失敗せずに型が変わる**」経路であり、この 1 行が抜けると validate は素通りする。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    PROFILE_BODY,
    Raw,
    collect_problems,
    default_agents,
    default_tasks,
    dump_mapping,
    kinds,
    write,
    write_agents,
    write_humans,
    write_minimal_area_a,
    write_policy,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

BASE_PERSONA = "planner"
BASE_ROWS = ["p, member:pj.planner, tool:read_issue, execute"]


def _base(root: Path) -> None:
    """検査を通る最小の領域 A（メンバは `planner` 1 人）。"""
    write_minimal_area_a(root, personas=(BASE_PERSONA,))


def _patch_global_agents(root: Path, persona: str, field: str, value: object) -> None:
    agents = default_agents()
    agents[persona][field] = value
    write(root, "global/agents.yaml", dump_mapping(agents))


def _patch_global_tasks(root: Path, stage: str, field: str, value: object) -> None:
    tasks = default_tasks()
    tasks[stage][field] = value
    write(root, "global/tasks.yaml", dump_mapping(tasks))


def _drop_global_task_field(root: Path, stage: str, field: str) -> None:
    tasks = default_tasks()
    del tasks[stage][field]
    write(root, "global/tasks.yaml", dump_mapping(tasks))


# -- §7 の 18 行（仕込む壊れ方 → 期待する kind） ---------------------------------------


def _row01(root: Path) -> None:
    _patch_global_agents(root, "planner", "nickname", "x")


def _row02(root: Path) -> None:
    _patch_global_agents(root, "planner", "allow_delegation", True)  # noqa: FBT003


def _row03(root: Path) -> None:
    _patch_global_agents(root, "planner", "api_key", "sk-XXXX")


def _row04(root: Path) -> None:
    write_agents(root, "pj", {"planner": " {}", "wizard": " {}"})


def _row05(root: Path) -> None:
    write_agents(root, "pj", {"planner": "\n  tools:\n    - teleport"})


def _row06(root: Path) -> None:
    write_agents(root, "pj", {"planner": "\n  llm: ghost"})


def _row07(root: Path) -> None:
    _patch_global_tasks(root, "child", "description", "{unknown_var} {acceptance}")


def _row08(root: Path) -> None:
    _patch_global_tasks(root, "program", "description", "{acceptance} のみ")


def _row09(root: Path) -> None:
    write_policy(root, "pj", [*BASE_ROWS, "p, member:pj.ghost, tool:read_issue, execute"])


def _row10(root: Path) -> None:
    write(root, "global/commands.yaml", "commands:\n  pytest:\n    argv:\n      - pytest\n")


def _row11(root: Path) -> None:
    write(
        root,
        "projects/pj/project.yaml",
        "name: デモ\nis_reserved: false\nfolders:\n"
        "  pj_input_dir: pj\n  role_dirs: {}\n  outcome_dir: pj/outcome\n",
    )


def _row12(root: Path) -> None:
    write(
        root,
        "global/llm_profiles.yaml",
        "profiles:\n  default:\n    provider_kind: openai_compatible\n"
        "    base_url: http://127.0.0.1:8080/v1\n    model_id: demo\n"
        "    context_window: 1024\n    api_key_ref: DEMO_REF\ndefault: default\n",
    )


def _row13(root: Path) -> None:
    write_humans(root, ["pj.planner"])


def _row14(root: Path) -> None:
    tasks = default_tasks()
    tasks["child"]["description"] = Raw("{title}")
    write(root, "global/tasks.yaml", dump_mapping(tasks))


def _row15(root: Path) -> None:
    tasks = default_tasks()
    tasks["extra"] = {"description": "{acceptance}", "expected_output": "x"}
    write(root, "global/tasks.yaml", dump_mapping(tasks))


def _row16(root: Path) -> None:
    write(
        root,
        "global/limits.yaml",
        "global:\n  max_requests_per_kickoff: 0\n  max_cost_per_kickoff: 1.0\n",
    )


def _row17(root: Path) -> None:
    """`role_dirs` の唯一のキーを綴り違いに**置き換える**（追記ではない）。

    `planner:` を残したまま `programer:` を足す形だと、メンバ（`planner`）は役割フォルダに
    在るので **V-17 が構造的に発火しえない**。それでは §7 行 17 の注記「V-17 の誤診が出ない
    こと」を確かめたことにならない（V-28 を先に当てる意味が観測できない）。
    """
    write(
        root,
        "projects/pj/project.yaml",
        "name: デモ\nis_reserved: false\nfolders:\n  pj_input_dir: pj\n  role_dirs:\n"
        "    programer: pj/roles/programer\n"
        "  outcome_dir: pj/outcome\n",
    )


def _row18(root: Path) -> None:
    _drop_global_task_field(root, "child", "expected_output")


@pytest.mark.parametrize(
    ("mutate", "expected_kind"),
    [
        pytest.param(_row01, "unknown_field", id="01 未知フィールド"),
        pytest.param(_row02, "forbidden_key", id="02 禁止キー"),
        pytest.param(_row03, "forbidden_key", id="03 秘密語キー"),
        pytest.param(_row04, "agent_ref", id="04 共有語彙に無いペルソナ"),
        pytest.param(_row05, "tool_ref", id="05 共有語彙に無い Tool"),
        pytest.param(_row06, "llm_ref", id="06 参照切れの LLM"),
        pytest.param(_row07, "var_unknown", id="07 未知変数"),
        pytest.param(_row08, "var_required", id="08 段別必須変数の欠落"),
        pytest.param(_row09, "orphan", id="09 孤児ポリシー"),
        pytest.param(_row10, "cmd_ref", id="10 死に定義"),
        pytest.param(_row11, "forward", id="11 メンバのペルソナが role_dirs に無い"),
        pytest.param(_row12, "llm_ref", id="12 閾値が 0 以下"),
        pytest.param(_row13, "forward", id="13 行為者名の衝突"),
        pytest.param(_row14, "unknown_field", id="14 素の flow mapping（V-10b）"),
        pytest.param(_row15, "var_required", id="15 8 つ目の段キー（V-08）"),
        pytest.param(_row16, "forward", id="16 上限が 0（V-27）"),
        pytest.param(_row17, "agent_ref", id="17 role_dirs の綴り違い（V-28）"),
        pytest.param(_row18, "var_required", id="18 必須フィールドの欠落（V-10a）"),
    ],
)
def test_broken_area_a_is_rejected(
    settings_root: Path, mutate: Callable[[Path], None], expected_kind: str
) -> None:
    """§7 の 18 行。**仕込んだ壊れ方ごとに期待する `kind` が出る**。"""
    _base(settings_root)
    mutate(settings_root)
    problems = collect_problems(settings_root)
    assert problems, "壊したのに違反が 0 件になっている"
    assert expected_kind in kinds(problems)


def test_a_healthy_area_a_has_no_problems(settings_root: Path) -> None:
    """表の土台そのものは違反 0 件である（表が「常に赤い」形になっていないこと）。"""
    _base(settings_root)
    assert collect_problems(settings_root) == []


# -- 表の注記が求める「出てはいけない kind」 --------------------------------------------


def test_eighth_stage_key_does_not_report_unknown_field(settings_root: Path) -> None:
    """15 行目: 8 つ目の段キーは **V-08 の `var_required`** で、`unknown_field` は出さない。"""
    _base(settings_root)
    _row15(settings_root)
    assert "unknown_field" not in kinds(collect_problems(settings_root))


def test_missing_required_field_does_not_report_unknown_field(settings_root: Path) -> None:
    """18 行目: 必須フィールドの**欠落**は `var_required` であり `unknown_field` は出さない。

    `p5r1_notes.md` §5 の明文（「必須フィールド欠落」＝ `var_required`）との食い違いを塞ぐ
    裁定（2026-08-24・CHK070）の観測点である。
    """
    _base(settings_root)
    _row18(settings_root)
    problems = [item for item in collect_problems(settings_root) if "child" in item.where]
    assert {item.kind for item in problems} == {"var_required"}


def test_role_dirs_typo_does_not_misdiagnose_agents_yaml(settings_root: Path) -> None:
    """17 行目: **V-28 は V-17 より先に当てる**（誤診が出ないことを併せて確かめる＝ A1）。

    `_row17` は唯一の役割フォルダのキーを綴り違いに**置き換える**ので、抑止が無ければ V-17 が
    `projects/pj/agents.yaml:planner` を指す `forward` を出す。それが**出ない**ことが観測点。
    """
    _base(settings_root)
    _row17(settings_root)
    problems = collect_problems(settings_root)
    assert "agent_ref" in kinds(problems)
    assert not [item for item in problems if item.kind == "forward" and "agents.yaml" in item.where]


def test_role_dirs_typo_still_reports_the_project_yaml(settings_root: Path) -> None:
    """抑止するのは V-17 だけで、V-28 の報告（原因の側）は残る。"""
    _base(settings_root)
    _row17(settings_root)
    spots = [item.where for item in collect_problems(settings_root) if item.kind == "agent_ref"]
    assert spots == ["projects/pj/project.yaml:folders.role_dirs.programer"]


# -- 「一覧で返す」「安全側に倒さない」 -------------------------------------------------


def test_problems_are_collected_not_truncated(settings_root: Path) -> None:
    """US3 AC-13・FR-046: 違反は**一覧**で返り、最初の 1 件で打ち切らない。"""
    _base(settings_root)
    _row01(settings_root)
    _row05(settings_root)
    _row13(settings_root)
    problems = collect_problems(settings_root)
    assert len(problems) >= 3
    assert {"unknown_field", "tool_ref", "forward"} <= kinds(problems)


def test_forbidden_key_wins_over_unknown_field(settings_root: Path) -> None:
    """R-20: **禁止キー判定を先に当てる**（順序を逆にすると明示エラー文言が消える）。"""
    _base(settings_root)
    _row02(settings_root)
    found = collect_problems(settings_root)
    problems = [item for item in found if "allow_delegation" in item.where]
    assert [item.kind for item in problems] == ["forbidden_key"]


def test_secret_key_check_matches_names_exactly(settings_root: Path) -> None:
    """V-06 は**キー名の完全一致**である（`api_key_ref` は秘密ではない＝ FR-049）。"""
    _base(settings_root)
    assert "DEMO_API_KEY_REF" in (settings_root / "global/llm_profiles.yaml").read_text("utf-8")
    assert collect_problems(settings_root) == []


def test_secret_key_at_the_top_level_of_llm_profiles_is_rejected(settings_root: Path) -> None:
    """V-06 は**ファイルの生トップレベル**まで届く（FR-050）。

    `profiles:` の部分木だけを走査していると、`api_key` の実値が最も置かれやすい
    `llm_profiles.yaml` のファイル直下が素通りする。
    """
    _base(settings_root)
    write(settings_root, "global/llm_profiles.yaml", "api_key: 実値は書かない\n" + PROFILE_BODY)
    problems = collect_problems(settings_root)
    assert [item.where for item in problems if item.kind == "forbidden_key"] == [
        "global/llm_profiles.yaml:api_key"
    ]


def test_secret_key_where_uses_a_colon_at_the_first_level(settings_root: Path) -> None:
    """V-06 の `where` も「1 段目は `:`、以降は `.`」（data-model §4 の例示に揃える）。"""
    _base(settings_root)
    _row03(settings_root)
    spots = [item.where for item in collect_problems(settings_root) if item.kind == "forbidden_key"]
    assert spots == ["global/agents.yaml:planner.api_key"]


def test_non_string_human_actor_does_not_silence_the_collision_check(
    settings_root: Path,
) -> None:
    """V-20: 非文字列が 1 件混ざっても**衝突検査が空振りしない**（R-01）。

    検査側だけが「1 件でも非文字列ならリスト全体を捨てる」読み方をすると、写像側は残りを
    拾うため、衝突が違反 0 件のままスナップショットに残る。
    """
    _base(settings_root)
    write(settings_root, "global/humans.yaml", "human_actors:\n  - 1\n  - pj.planner\n")
    problems = collect_problems(settings_root)
    reasons = [item.reason for item in problems if item.kind == "forward"]
    assert "human_actors[0] が文字列でない" in reasons
    assert "人間の宣言と AI メンバの行為者名が衝突: pj.planner" in reasons


def test_human_actors_that_is_not_a_list_is_reported(settings_root: Path) -> None:
    """V-20: `human_actors` がリストでない形も黙って人間 0 名にしない。"""
    _base(settings_root)
    write(settings_root, "global/humans.yaml", "human_actors: pj.planner\n")
    reasons = [item.reason for item in collect_problems(settings_root) if item.kind == "forward"]
    assert "human_actors はリストで書く" in reasons


def test_zero_members_means_zero_llm_problems(settings_root: Path) -> None:
    """R-17・clarify 裁定: メンバ 0 件なら既定プロファイルが無くても違反 0 件。"""
    _base(settings_root)
    write(settings_root, "global/llm_profiles.yaml", "profiles: {}\ndefault: null\n")
    write_agents(settings_root, "pj", {})
    write_policy(settings_root, "pj", [])
    assert collect_problems(settings_root) == []


def test_member_without_any_profile_is_reported(settings_root: Path) -> None:
    """V-14: 解決順の**終端まで届かないメンバだけ**を違反にする。"""
    _base(settings_root)
    write(settings_root, "global/llm_profiles.yaml", "profiles: {}\ndefault: null\n")
    assert "llm_ref" in kinds(collect_problems(settings_root))


def test_folder_missing_and_key_rule_never_fire(settings_root: Path) -> None:
    """§5: 前半では `folder_missing` と `key_rule` を**発火させない**。"""
    _base(settings_root)
    for mutate in (_row01, _row04, _row07, _row11, _row17):
        _base(settings_root)
        mutate(settings_root)
        assert not ({"folder_missing", "key_rule"} & kinds(collect_problems(settings_root)))
