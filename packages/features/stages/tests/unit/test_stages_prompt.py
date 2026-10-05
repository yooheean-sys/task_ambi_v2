"""プロンプト組立（`build_prompt`。IF-45・stages-F02／F03・US2）。

正本は [contracts/prompt-rendering.md](../../../../specs/006-stages/contracts/prompt-rendering.md)。
**本文もペルソナ本文も持たない**（FR-013・D16-g）ので、テンプレートはすべてテスト側で組む。
"""

from __future__ import annotations

from typing import cast

import pytest
from conftest import (
    make_ctx,
    make_member,
    make_project_rules,
    make_service,
    make_template,
    make_templates,
    stage_spec,
)

from common.interfaces.stages import PromptPurpose, UnknownPersona, UnknownStage
from common.kernel.enums import PersonaKey, StageName, ToolName
from stages.models import (
    ALLOWED_VARS,
    PERSONA_GROUP,
    PERSONA_GUIDE_HEADER,
    REQUIRED_VARS,
    render,
)

# 出荷時既定の担当（出典: `_archive/p5r1/p5r1_notes.md` §3。
# `packages/features/settings/defaults/` は読まない＝ plan (vi) の禁止事項）。
SHIPPED_OWNER: dict[StageName, PersonaKey] = {
    StageName.program: PersonaKey.planner,
    StageName.project: PersonaKey.project_lead,
    StageName.subrequest: PersonaKey.system_engineer,
}

TEMPLATE_KEY_CASES: tuple[tuple[StageName, PromptPurpose, bool, str], ...] = (
    (StageName.program, PromptPurpose.work, False, "program"),
    (StageName.project, PromptPurpose.work, False, "project"),
    (StageName.subrequest, PromptPurpose.work, False, "subrequest"),
    (StageName.child, PromptPurpose.work, False, "child"),
    (StageName.plain, PromptPurpose.work, False, "plain"),
    (StageName.child, PromptPurpose.review, False, "review_normal"),
    (StageName.child, PromptPurpose.review, True, "review_final"),
)

TEMPLATE_KEY_IDS = (
    "work-program",
    "work-project",
    "work-subrequest",
    "work-child",
    "work-plain",
    "review-normal",
    "review-final",
)


def _member_for(stage: StageName) -> PersonaKey:
    """その段でプロンプトを組めるペルソナ（分解 3 段は出荷時既定、実行段は実行側）。"""
    return SHIPPED_OWNER.get(stage, PersonaKey.general)


@pytest.mark.parametrize(
    ("stage", "purpose", "is_final_review", "expected_key"),
    TEMPLATE_KEY_CASES,
    ids=TEMPLATE_KEY_IDS,
)
def test_template_key_selection(
    stage: StageName, purpose: PromptPurpose, is_final_review: bool, expected_key: str
) -> None:
    """§1: 用途と段からテンプレートキーが一意に決まる（FR-023）。"""
    templates = make_templates(
        {expected_key: make_template(description=f"KEY={expected_key} {{acceptance}}")}
    )
    bundle = make_service().build_prompt(
        stage_spec(stage),
        make_member(persona_key=_member_for(stage)),
        make_project_rules(),
        templates,
        make_ctx(purpose=purpose, is_final_review=is_final_review, acceptance="受入"),
        None,
    )
    assert bundle.task_description.startswith(f"KEY={expected_key}")


def test_missing_template_key_raises_unknown_stage() -> None:
    """US2 AC-10・FR-029: キーが無ければ **`UnknownStage`**（素のテンプレートへ落ちない）。"""
    with pytest.raises(UnknownStage):
        make_service().build_prompt(
            stage_spec(StageName.program),
            make_member(persona_key=PersonaKey.planner),
            make_project_rules(),
            make_templates({"child": make_template()}),
            make_ctx(),
            None,
        )


VALUE_CASES: tuple[tuple[str, str], ...] = (
    ("issue_id", "i-9"),
    ("title", "題"),
    ("description", "説"),
    ("acceptance", "受"),
    ("parent_title", "親題"),
    ("parent_acceptance", "親受"),
    ("human_guidance", "指示1\n指示2"),
    ("feedback", "差し戻し"),
    ("input_rules", ""),
    ("input_refs", "ref1\nref2"),
    ("existing_children", "子1\n子2"),
    ("project_key", "pj"),
    ("persona_key", "planner"),
    ("outcome_dir", "outcome"),
)

VALUE_IDS = tuple(name for name, _ in VALUE_CASES)


@pytest.mark.parametrize(("var", "expected"), VALUE_CASES, ids=VALUE_IDS)
def test_variable_values(var: str, expected: str) -> None:
    """§4: `{変数}` 15 種の値表（`persona_guide` は §5 で別に見る）。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner, project_key="pj"),
        make_project_rules(),
        make_templates({"program": make_template(description=f"[{{{var}}}]")}),
        make_ctx(
            issue_id="i-9",
            title="題",
            description="説",
            acceptance="受",
            parent_title="親題",
            parent_acceptance="親受",
            human_guidance=["指示1", "指示2"],
            input_refs=["ref1", "ref2"],
            existing_children=["子1", "子2"],
        ),
        "差し戻し",
    )
    assert bundle.task_description == f"[{expected}]"


NONE_CASES: tuple[tuple[str, str], ...] = (
    ("parent_title", "[]"),
    ("parent_acceptance", "[]"),
    ("feedback", "[]"),
    ("human_guidance", "[]"),
    ("input_refs", "[]"),
    ("existing_children", "[]"),
)


@pytest.mark.parametrize(("var", "expected"), NONE_CASES, ids=tuple(n for n, _ in NONE_CASES))
def test_absent_values_become_empty_string(var: str, expected: str) -> None:
    """US2 AC-6: `None`・空の一覧は**空文字**（日本語の既定文言を持たない＝ FR-013）。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates({"program": make_template(description=f"[{{{var}}}]")}),
        make_ctx(),
        None,
    )
    assert bundle.task_description == expected


def test_repository_vars_are_filled_from_the_context() -> None:
    """D24・CFG-74: `repository_*` の 2 変数は `ContextRefs` の値で充填される。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates(
            {
                "program": make_template(
                    description="[{repository_remote_url}][{repository_local_path}]"
                )
            }
        ),
        make_ctx(
            repository_remote_url="https://gitlab.example.com/g/r.git",
            repository_local_path="/home/demo/work/r",
        ),
        None,
    )
    assert bundle.task_description == "[https://gitlab.example.com/g/r.git][/home/demo/work/r]"


def test_repository_vars_become_empty_string_when_unregistered() -> None:
    """D24・FR-013 踏襲: リポジトリ未登録（`None`）のプロジェクトでは**空文字**。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates(
            {
                "program": make_template(
                    description="[{repository_remote_url}][{repository_local_path}]"
                )
            }
        ),
        make_ctx(),
        None,
    )
    assert bundle.task_description == "[][]"


def test_all_allowed_vars_leave_no_braces() -> None:
    """SC-004: 許可 17 種をすべて含む本文で、埋め残しの波括弧が 0 件。"""
    body = " ".join(f"{{{name}}}" for name in sorted(ALLOWED_VARS))
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates({"program": make_template(description=body, expected_output=body)}),
        make_ctx(),
        None,
    )
    assert "{" not in bundle.task_description
    assert "}" not in bundle.task_description
    assert "{" not in bundle.expected_output
    assert "}" not in bundle.expected_output


RENDER_CASES: tuple[tuple[str, str], ...] = (
    ("{foo}", ""),
    ("{0}", ""),
    ("{}", ""),
    ("{title!r}", "題"),
    ("{title:>10}", "題"),
    ("{{title}}", "{title}"),
    ("前{title}後", "前題後"),
    # 「破れの手前まで」＝ `Formatter().parse()` が **yield し終えたトークン**まで。
    # 破れを含むトークンの literal は取り出せない（実測。research R-02 の読み）。
    ("壊れた{title", ""),
    ("前{title}後、ここで壊れる{x", "前題"),
    ("先頭は残る{", ""),
)

RENDER_IDS = (
    "unknown-name-empty",
    "positional-index-empty",
    "positional-empty",
    "conversion-ignored",
    "format-spec-ignored",
    "escaped-braces",
    "literal-around",
    "broken-brace-drops-partial-token",
    "broken-brace-keeps-completed-tokens",
    "trailing-brace",
)


@pytest.mark.parametrize(("text", "expected"), RENDER_CASES, ids=RENDER_IDS)
def test_render_rules(text: str, expected: str) -> None:
    """§6: 充填は `string.Formatter` で行い、壊れた本文でも例外にしない（FR-027・FR-031）。"""
    assert render(text, {"title": "題"}) == expected


def test_persona_guide_lists_keys_in_enum_order() -> None:
    """§5・SD-51: 見出し ＋ `- <persona_key>` を **`PersonaKey` の定義順**で並べる。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.subrequest),
        make_member(persona_key=PersonaKey.system_engineer),
        make_project_rules(),
        make_templates({"subrequest": make_template(description="{persona_guide}")}),
        make_ctx(),
        None,
    )
    assert bundle.task_description == "\n".join(
        [
            PERSONA_GUIDE_HEADER,
            "- architect",
            "- programmer",
            "- analyst",
            "- writer",
            "- general",
        ]
    )


def test_persona_guide_has_no_role_or_goal() -> None:
    """SC-005: 担当一覧に役割・目標を載せない（stages はペルソナ本文を持たない）。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner, role="役割X", goal="目標X"),
        make_project_rules(),
        make_templates({"program": make_template(description="{persona_guide}")}),
        make_ctx(),
        None,
    )
    assert "役割X" not in bundle.task_description
    assert "目標X" not in bundle.task_description
    assert ":" not in bundle.task_description.removeprefix(PERSONA_GUIDE_HEADER)


def test_persona_guide_of_empty_set_is_header_only() -> None:
    """`child`・`plain` は `allowed_personas` が空集合なので見出しだけ。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.child),
        make_member(persona_key=PersonaKey.general),
        make_project_rules(),
        make_templates({"child": make_template(description="{persona_guide}")}),
        make_ctx(),
        None,
    )
    assert bundle.task_description == PERSONA_GUIDE_HEADER


MISMATCH_CASES: tuple[tuple[StageName, PersonaKey], ...] = (
    (StageName.program, PersonaKey.programmer),
    (StageName.project, PersonaKey.writer),
    (StageName.subrequest, PersonaKey.analyst),
    (StageName.child, PersonaKey.planner),
    (StageName.plain, PersonaKey.doc_lead),
)


@pytest.mark.parametrize(
    ("stage", "persona"),
    MISMATCH_CASES,
    ids=tuple(f"{s}-x-{p}" for s, p in MISMATCH_CASES),
)
def test_stage_group_mismatch_raises(stage: StageName, persona: PersonaKey) -> None:
    """US2 AC-8・FR-028・SC-018: 段の別と食い違う担当は `UnknownPersona`（`work` のとき）。"""
    with pytest.raises(UnknownPersona):
        make_service().build_prompt(
            stage_spec(stage),
            make_member(persona_key=persona),
            make_project_rules(),
            make_templates(),
            make_ctx(purpose=PromptPurpose.work),
            None,
        )


def test_unknown_persona_key_raises() -> None:
    """`PersonaKey` 外（段所属表に無い）も `UnknownPersona`。"""
    member = make_member().model_copy(update={"persona_key": cast(PersonaKey, "unknown_persona")})
    with pytest.raises(UnknownPersona):
        make_service().build_prompt(
            stage_spec(StageName.program),
            member,
            make_project_rules(),
            make_templates(),
            make_ctx(),
            None,
        )


@pytest.mark.parametrize(
    ("stage", "persona"),
    MISMATCH_CASES,
    ids=tuple(f"review-{s}-x-{p}" for s, p in MISMATCH_CASES),
)
def test_review_does_not_check_stage_group(stage: StageName, persona: PersonaKey) -> None:
    """SD-50: `purpose == review` では段所属を照合しない（掛けると全レビューが落ちる）。"""
    bundle = make_service().build_prompt(
        stage_spec(stage),
        make_member(persona_key=persona),
        make_project_rules(),
        make_templates(),
        make_ctx(purpose=PromptPurpose.review),
        None,
    )
    assert bundle.purpose == PromptPurpose.review


@pytest.mark.parametrize("stage", tuple(SHIPPED_OWNER), ids=tuple(str(s) for s in SHIPPED_OWNER))
def test_shipped_owners_can_build_all_propose_stages(stage: StageName) -> None:
    """SC-018: 出荷時既定の担当で**分解 3 段すべて**が組める（`UnknownPersona` 0 件）。"""
    bundle = make_service().build_prompt(
        stage_spec(stage),
        make_member(persona_key=SHIPPED_OWNER[stage]),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    assert bundle.persona_key == SHIPPED_OWNER[stage]


def test_member_outside_allowed_personas_does_not_fail() -> None:
    """US2 AC-9: `spec.allowed_personas` に入らないだけのメンバは失敗しない（F-14）。"""
    spec = stage_spec(StageName.program)
    assert PersonaKey.planner not in spec.allowed_personas
    bundle = make_service().build_prompt(
        spec,
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    assert bundle.persona_key == PersonaKey.planner


BACKSTORY_CASES: tuple[tuple[str, str, str], ...] = (
    ("背景", "ルール", "背景\n\nルール"),
    ("", "ルール", "ルール"),
    ("背景", "", "背景"),
    ("", "", ""),
)


@pytest.mark.parametrize(
    ("backstory", "project_rules", "expected"),
    BACKSTORY_CASES,
    ids=("both", "member-empty", "rules-empty", "both-empty"),
)
def test_backstory_composition(backstory: str, project_rules: str, expected: str) -> None:
    """§3・FR-021: メンバの背景 ＋ 空行 ＋ 共通ルール（見出しを入れない）。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner, backstory=backstory),
        make_project_rules(project_rules),
        make_templates(),
        make_ctx(),
        None,
    )
    assert bundle.backstory == expected


def test_role_and_goal_pass_through() -> None:
    """FR-022: `role`・`goal` はメンバのものをそのまま載せる。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner, role="R", goal="G"),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    assert (bundle.role, bundle.goal) == ("R", "G")


def test_task_tools_follow_template() -> None:
    """FR-030: `task_tools` は `template.tools` そのまま（無ければ `None`）。"""
    service = make_service()
    with_tools = service.build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates({"program": make_template(tools=[ToolName.read_issue])}),
        make_ctx(),
        None,
    )
    without = service.build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates({"program": make_template(tools=None)}),
        make_ctx(),
        None,
    )
    assert with_tools.task_tools == [ToolName.read_issue]
    assert without.task_tools is None


def test_proposal_schema_only_for_propose_stages() -> None:
    """FR-030a: `proposal_schema` は `mode == propose` のときだけ型名。"""
    service = make_service()
    propose = service.build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    execute = service.build_prompt(
        stage_spec(StageName.child),
        make_member(persona_key=PersonaKey.general),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    assert propose.proposal_schema == "Proposal"
    assert execute.proposal_schema is None


def test_same_input_gives_same_bundle() -> None:
    """US2 AC-11: 同じ入力に同じ結果。"""
    args = (
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    service = make_service()
    assert service.build_prompt(*args) == service.build_prompt(*args)


def test_persona_group_covers_all_persona_keys() -> None:
    """DM-09: 段所属表は `PersonaKey` **9 種すべて**を網羅する。"""
    assert set(PERSONA_GROUP) == set(PersonaKey)
    assert sum(1 for v in PERSONA_GROUP.values() if v == "propose") == 4
    assert sum(1 for v in PERSONA_GROUP.values() if v == "execute") == 5


def test_expected_output_uses_same_values() -> None:
    """§6: `description` と `expected_output` の**両方**に同じ値表を適用する。"""
    bundle = make_service().build_prompt(
        stage_spec(StageName.program),
        make_member(persona_key=PersonaKey.planner),
        make_project_rules(),
        make_templates(
            {"program": make_template(description="{title}", expected_output="{title}")}
        ),
        make_ctx(title="同じ題"),
        None,
    )
    assert bundle.task_description == bundle.expected_output == "同じ題"


# --- `ALLOWED_VARS`・`REQUIRED_VARS` の実値の pin（FR-025・FR-026・US2 AC-5・§4／§8）---
#
# この 2 定数は `service.py` から 1 度も読まれない —— `render()` は `values.get(field, "")` で
# 埋めるだけで許可集合を参照しない。存在意義は「**正本として持つこと**」だけであり
# （contracts/prompt-rendering.md §8・D16-c）、settings 側の写しとの乖離を機械的に捕まえる
# 手段が無い（SD-42・B-22）ため、**ここでの pin が唯一の歯止め**になる。
# 改訂するときは **stages と settings の 2 モジュール同時**に行う（SKILL.md §5）。

EXPECTED_ALLOWED_VARS: frozenset[str] = frozenset(
    {
        "issue_id",
        "title",
        "description",
        "acceptance",
        "parent_title",
        "parent_acceptance",
        "persona_guide",
        "human_guidance",
        "feedback",
        "input_rules",
        "input_refs",
        "existing_children",
        "project_key",
        "persona_key",
        "outcome_dir",
    }
)


def test_allowed_vars_pins_the_fifteen_names() -> None:
    """FR-025・§4: 許可集合は **15 種ちょうど**（1 つ減っても増えても落ちる）。"""
    assert ALLOWED_VARS == EXPECTED_ALLOWED_VARS
    assert len(ALLOWED_VARS) == 15


# **キーは段名ではなくテンプレートキー**である。`review_normal`／`review_final` は
# `StageName` の値ではなく、§1 のキー決定表が `purpose`／`is_final_review` から導く名前である。
REQUIRED_VARS_CASES: tuple[tuple[str, frozenset[str]], ...] = (
    ("program", frozenset({"persona_guide", "acceptance"})),
    ("project", frozenset({"persona_guide", "acceptance"})),
    ("subrequest", frozenset({"persona_guide", "acceptance"})),
    ("child", frozenset({"acceptance"})),
    ("plain", frozenset({"acceptance"})),
    ("review_normal", frozenset({"acceptance"})),
    ("review_final", frozenset({"acceptance"})),
)

REQUIRED_VARS_IDS = tuple(key for key, _ in REQUIRED_VARS_CASES)


@pytest.mark.parametrize(("key", "expected"), REQUIRED_VARS_CASES, ids=REQUIRED_VARS_IDS)
def test_required_vars_pins_each_key(key: str, expected: frozenset[str]) -> None:
    """FR-026・§8: 分解 3 段は担当一覧＋受入条文、残る 4 キーは受入条文。"""
    assert REQUIRED_VARS[key] == expected


def test_required_vars_pins_exactly_seven_keys() -> None:
    """FR-026・§8: **7 キーちょうど**（余分なキー・欠けたキーのどちらでも落ちる）。"""
    assert set(REQUIRED_VARS) == set(REQUIRED_VARS_IDS)
    assert len(REQUIRED_VARS) == 7


def test_review_keys_are_not_stage_names() -> None:
    """§1: `review_normal`／`review_final` は段名ではないことを明示的に固定する。"""
    stage_values = {stage.value for stage in StageName}
    assert "review_normal" not in stage_values
    assert "review_final" not in stage_values
