"""同梱の出荷時既定が**自分の検査を通る**こと（US6・FR-058・SC-004）。plan (viii) の 8 項目。

**領域 A も外部ツールも要らない**（`defaults/` を直接読んで検査に掛ける）。

⚠ **PoC のファイルを読まない**（裁定 2026-08-24 ＝ SD-26）。`pocs/*` は `.gitmodules` を持たない
**gitlink（mode 160000）**であり、新規 clone と CI（`before_script` は `uv sync --frozen` のみ）
では中身が存在せず、照合テストは**必ず落ちる**。移植元との一致は clarify 段階の実測
（M-04・M-06・M-07）で確認済みで、以後守るべきは「`defaults/` が勝手に変わらないこと」である。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import APIRouter
from settings_testing import default_agents, default_tasks, stub_deps

from common.kernel.enums import PersonaKey, ToolName
from settings import build, config_path, defaults_path
from settings.models import SECRET_KEY_WORDS, TASK_KEYS
from settings.service.validate import check_agents_mapping, check_tasks_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

COMMON_TOOLS = frozenset(
    {
        "read_issue",
        "record_progress",
        "list_input_files",
        "read_input_file",
        "list_outcome_files",
        "read_outcome_file",
        "escalate_to_human",
    }
)
"""共通 7（`p5r1_notes.md` §7・FR-054）。"""

DECOMPOSE_PERSONAS = frozenset({"planner", "project_lead", "system_engineer", "doc_lead"})
"""分解系 4（F-05）。共通 7 ＋ 分解案の提出。"""

EXECUTE_PERSONAS = frozenset({"analyst", "writer", "general", "architect", "programmer"})
"""実行系 5（F-05）。共通 7 ＋ 成果物の書込・完了申告・コマンド実行。"""

MODEL_SECTIONS = (
    "[request_definition]",
    "[policy_definition]",
    "[role_definition]",
    "[policy_effect]",
    "[matchers]",
)
"""`model.conf` の 5 節（FR-048a）。"""

ROLE_DEFINITION_LINE = "g = _, _"
"""**落としてはならない** 1 行（M-06・M-07。落とすと `g` 行が黙って捨てられる）。"""

MATCHER_LINE = "m = g(r.sub, p.sub) && keyMatch(r.obj, p.obj) && r.act == p.act"
"""照合式（M-04 が V4 obj 語彙 16 ケース全件で期待どおりと確認した形）。"""

SECRET_VALUE_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]")
"""秘密の実値らしき様式（OpenAI 系の API キー）。"""


def _tool_names(entry: Mapping[str, object]) -> set[str]:
    """1 ペルソナの `tools` を名前の集合として読む。"""
    tools = entry["tools"]
    assert isinstance(tools, list)
    return {str(item) for item in cast("list[object]", tools)}


def _secret_key_hits(text: str) -> list[str]:
    """生テキストから**秘密語のキー名**を拾う（YAML の `key:` と JSON の `"key":`）。

    判定は**完全一致**である。部分一致にすると `api_key_ref`（FR-049 が定める参照名）と
    `context_reserve_tokens`（CFG-37）が違反になり、規則そのものが成立しない
    （FR-050 の文言も「…という**名前のキー**」である）。
    """
    pattern = re.compile(r'^\s*"?([A-Za-z_][A-Za-z0-9_\-]*)"?\s*:', re.MULTILINE)
    return [name for name in pattern.findall(text) if name.lower() in SECRET_KEY_WORDS]


# -- (1)(2) 同梱の雛形が自分の検査を通る -----------------------------------------------


def test_1_bundled_agents_pass_the_forward_checks() -> None:
    """(1) `defaults/agents.yaml` の違反が **0 件**。"""
    problems = check_agents_mapping(
        default_agents(),
        where="defaults/agents.yaml",
        require_global_fields=True,
        profile_names=frozenset(),
    )
    assert problems == []


def test_2_bundled_tasks_pass_the_forward_checks() -> None:
    """(2) `defaults/tasks.yaml` の違反が **0 件**。"""
    problems = check_tasks_mapping(
        default_tasks(), where="defaults/tasks.yaml", require_all_keys=True
    )
    assert problems == []


# -- (3)(4) キー集合が過不足なく一致する -----------------------------------------------


def test_3_persona_keys_match_the_shared_vocabulary() -> None:
    """(3) US6 AC-3: キー集合 == `set(PersonaKey)`（**9 件・過不足なし**）。"""
    assert set(default_agents()) == {key.value for key in PersonaKey}


def test_4_stage_keys_match_the_seven() -> None:
    """(4) US6 AC-4: キー集合 == 7 キー（過不足なし）。"""
    assert set(default_tasks()) == set(TASK_KEYS)


# -- (5) Tool の既定 --------------------------------------------------------------------


def test_5_tool_defaults_split_by_persona_kind() -> None:
    """(5) US6 AC-6・FR-054: 分解系／実行系で異なる集合、いずれも `ToolName` の名前だけ。"""
    known = {name.value for name in ToolName}
    for key, entry in default_agents().items():
        names = _tool_names(entry)
        assert names <= known, f"{key} が共有語彙の外の Tool を持つ"
        assert names >= COMMON_TOOLS, f"{key} に共通 7 が揃っていない"
        if key in DECOMPOSE_PERSONAS:
            assert names - COMMON_TOOLS == {"propose_breakdown"}
        else:
            assert key in EXECUTE_PERSONAS
            assert names - COMMON_TOOLS == {"write_outcome", "complete_issue", "run_command"}


def test_5b_submit_review_is_only_on_the_review_stages() -> None:
    """D16-a: `submit_review` は**どのペルソナにも載せない**（段側が Agent 側を上書きする）。

    review 段の宣言は置換（FR-030）なので、本文の手順 1〜4 が呼ぶ read 系 5 Tool を
    段側で揃えて宣言する（人格側から継承されないため）。
    """
    for entry in default_agents().values():
        assert "submit_review" not in _tool_names(entry)
    tasks = default_tasks()
    review_tools = {
        "read_issue",
        "list_input_files",
        "read_input_file",
        "list_outcome_files",
        "read_outcome_file",
        "submit_review",
    }
    for key in ("review_normal", "review_final"):
        names = cast("list[str]", tasks[key]["tools"])
        assert set(names) == review_tools


def test_5c_llm_is_not_declared_in_the_bundled_defaults() -> None:
    """`llm` を書かない（存在しないプロファイル名を指すと `llm_ref` の違反になる＝ R-17）。"""
    for entry in default_agents().values():
        assert "llm" not in entry


def test_5d_stage_agent_defaults() -> None:
    """`p5r1_notes.md` §3: 段の既定ペルソナは 3 段だけが持つ。"""
    defaults: dict[str, str | None] = {}
    for key, entry in default_tasks().items():
        value = entry.get("agent")
        defaults[key] = None if value is None else str(value)
    assert defaults == {
        "program": "planner",
        "project": "project_lead",
        "subrequest": "system_engineer",
        "child": None,
        "plain": None,
        "review_normal": None,
        "review_final": None,
    }


# -- (6) model.conf ---------------------------------------------------------------------


def test_6_model_conf_has_all_five_sections_and_the_key_lines() -> None:
    """(6) FR-048a: 5 節を持ち、`g = _, _` と照合式が**テスト内のリテラルと一致**する。"""
    text = (defaults_path() / "model.conf").read_text(encoding="utf-8")
    for section in MODEL_SECTIONS:
        assert section in text, f"{section} が無い"
    assert ROLE_DEFINITION_LINE in text
    assert MATCHER_LINE in text


def test_6b_no_test_reads_the_frozen_poc() -> None:
    """SD-26: **テストが凍結 PoC のパスを参照しない**（CI・新規 clone に中身が無いため）。

    照合先は `defaults/` 自身とテスト内リテラルの 2 つに一本化してある（`model.conf` の本文を
    `models.py` の定数にもすると 3 重管理になる＝ CHK142）。
    """
    needle = "pocs" + "/crewai"  # 本ファイル自身が引っかからないよう分けて組む
    package = defaults_path().parent
    for folder in ("tests", "src"):
        for path in sorted((package / folder).rglob("*.py")):
            assert needle not in path.read_text(encoding="utf-8"), path


# -- (7) 空の領域 A から 2 回組み立てる -------------------------------------------------


def test_7_two_builds_from_an_empty_area_a_succeed(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(7) US6 AC-5・SC-003: **生成物が自分の検査を通る**ことの直接の観測。"""
    monkeypatch.setenv("SETTINGS_ROOT", str(settings_root))
    assert isinstance(build(stub_deps()).router, APIRouter)
    assert isinstance(build(stub_deps()).router, APIRouter)


# -- (8) 秘密を持たない担保（FR-052・SD-32） -------------------------------------------


def test_8_no_secret_keys_in_the_bundled_files() -> None:
    """(8) `config.json` と `defaults/` の**生テキスト**に秘密語のキーと `sk-` 様式が 0 件。

    **構築済みモデルではなくファイルをテキストとして読む** — `SettingsConfig` は
    `extra="forbid"` なのでトップレベルの秘密語キーは (c) 段で先に落ち、`llm_presets` の
    入れ子は構築時に捨てられて見えないため、validate（V-06）では**原理的に到達できない**。
    これが `x3_notes.md` §4 (7) の「pytest の静的走査」を、変更してはならない
    `packages/common/tests`・`apps/api/tests` に触れずに満たす経路である。
    """
    targets = [config_path(), *sorted(defaults_path().iterdir())]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert _secret_key_hits(text) == [], f"{path.name} に秘密語のキーがある"
        assert not SECRET_VALUE_PATTERN.search(text), f"{path.name} に秘密の実値らしき文字列がある"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("api_key: sk-XXXX\n", ["api_key"], id="秘密語キーは拾う"),
        pytest.param('"token": "x"\n', ["token"], id="JSON の秘密語キーも拾う"),
        pytest.param("api_key_ref: DEMO\n", [], id="参照名は秘密ではない（FR-049）"),
        pytest.param('"context_reserve_tokens": 4096\n', [], id="CFG-37 は秘密ではない"),
    ],
)
def test_8b_secret_key_scan_matches_names_exactly(text: str, expected: list[str]) -> None:
    """走査は**キー名の完全一致**である（部分一致だと規則そのものが成立しない）。"""
    assert _secret_key_hits(text) == expected
