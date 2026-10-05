"""stages 提供 4 IF の適合検証（IF-44〜47。US4）。

外部ストアもスタブも要らない最小の提供 MOD である（`StagesDeps` は空）。器（マーカ登録・
AST 走査・`build()` 直呼び・Protocol 型注釈）が実際に機能することを最短で実証する位置づけ。

台帳の例外欄が `—` の IF-46・IF-47 は、**正常入力で例外が出ないことを素直に呼んで戻り値の型を
主張する**形で満たす（FR-018・R-17。`try/except BaseException` は書かない）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import make_context_refs, make_member, make_rules, make_task_templates

from common.interfaces.stages import (
    GateResult,
    PromptBundle,
    Proposal,
    ProposalItem,
    StageSpec,
    UnknownPersona,
    UnknownStage,
)
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName, ToolName

if TYPE_CHECKING:
    from common.interfaces.stages import ProposalGate, StageCatalog

ACCEPTANCE_LINE = "- [ ] 契約どおりに振る舞うことを確認できる"
"""`- [ ] …` の形で、本文が最小長（既定 10 文字）以上（`config.json` の実効値に依存しない長さ）。"""

MALFORMED_ACCEPTANCE_LINE = ACCEPTANCE_LINE.removeprefix("- [ ] ")
"""`- [ ] …` の形**だけ**を満たさない条文。

本文は `ACCEPTANCE_LINE` と同一なので、最小長・穴埋め判定では落ちない（落ちる理由を
「1 行 1 条件の形ではない」1 つに絞れる）。
"""

FEEDBACK_TEMPLATE_BODY = "差し戻し理由:{feedback}"
"""`{feedback}` **だけ**を持つ段テンプレート本文（IF-45 の入力欄の 6 つ目を踏むため）。

`conftest.make_task_templates()` の既定本文は `{title}`・`{acceptance}` しか持たないので、
`feedback` を捨てる実装でも既存テストは緑になる。区切りを置かないのは、`None` のときに
**空文字**で埋まることを完全一致で主張するためである（FR-013・SD-53）。
"""


def _proposal_item(title: str, *, acceptance: list[str] | None = None) -> ProposalItem:
    """段の検査を通る `ProposalItem` の見本（`acceptance` だけ差し替えられる）。

    Args:
        title: 題名（兄弟衝突の観測点。要約・説明もこの値から作る）。
        acceptance: 受入条文。`None` なら `ACCEPTANCE_LINE` 1 行。

    Returns:
        `ProposalItem` の見本。
    """
    return ProposalItem(
        title=title,
        summary=f"{title} の要約",
        description=f"{title} の説明",
        acceptance=[ACCEPTANCE_LINE] if acceptance is None else acceptance,
        depends_on=[],
        persona=None,
    )


@pytest.mark.contract_if("IF-44")
def test_contract_stages_resolve_stage_returns_stage_spec(stage_catalog: StageCatalog) -> None:
    """IF-44 正常系: `kind` ラベルの組と親の有無から `StageSpec` が返る（FR-015）。"""
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.program)}, has_parent=False)

    assert isinstance(spec, StageSpec)
    assert spec.name is StageName.program
    assert spec.mode == "propose"


@pytest.mark.contract_if("IF-44")
def test_contract_stages_resolve_stage_raises_unknown_stage(stage_catalog: StageCatalog) -> None:
    """IF-44 例外系: どの段にも当たらない `kind` ラベル集合で `UnknownStage`（FR-017）。

    1 つの IF に複数テストを置くのは可（FR-002。禁じられるのは 1 テストに 2 つ以上の宣言）。
    """
    with pytest.raises(UnknownStage):
        stage_catalog.resolve_stage({"kind:bogus"}, has_parent=False)


@pytest.mark.contract_if("IF-44")
def test_contract_stages_resolve_stage_distinguishes_mode_and_allowed_personas(
    stage_catalog: StageCatalog,
) -> None:
    """IF-44: `StageSpec` の **mode（propose/execute）と `allowed_personas`** が段ごとに決まる。

    台帳の出力欄は `StageSpec（段名・mode propose/execute・allowed_personas・min_items）` と
    4 項目を名指しするが、上の正常系は `program` 段（`mode="propose"`）1 つしか解決せず
    `spec.name`・`spec.mode` しか見ない —— **`allowed_personas` を落とす実装でも、
    `mode` を `propose` に固定する実装でも緑**になる。

    `child` 段は `mode="execute"` で `allowed_personas` が**空集合**（誰でも実行できる）、
    `program` 段は `mode="propose"` で `{project_lead}` に限られる。**2 段を並べて**、
    両項目が段ごとに違う値を取ることまで主張する（FR-016）。

    `child` 段は `require_labels` が**空**なので、`kind:child` ラベルではなく事前検査
    （`_check_declared_kind`）が親の有無を要求する側で決まる（`has_parent=True`）。
    """
    child = stage_catalog.resolve_stage({Labels.kind(KindLabel.child)}, has_parent=True)
    program = stage_catalog.resolve_stage({Labels.kind(KindLabel.program)}, has_parent=False)

    assert isinstance(child, StageSpec)
    assert child.name is StageName.child
    assert child.mode == "execute"
    assert child.allowed_personas == set()
    assert child.min_items == 1

    assert program.name is StageName.program
    assert program.mode == "propose"
    assert program.allowed_personas == {PersonaKey.project_lead}
    assert program.min_items == 1

    assert child.mode != program.mode, "mode が段によらず一定なら propose/execute の別が無い"
    assert child.allowed_personas != program.allowed_personas


@pytest.mark.contract_if("IF-45")
def test_contract_stages_build_prompt_returns_prompt_bundle(stage_catalog: StageCatalog) -> None:
    """IF-45 正常系: 段テンプレートとメンバ本文から `PromptBundle` が返る（FR-015）。"""
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.program)}, has_parent=False)
    rules = make_rules()
    ctx = make_context_refs()

    bundle = stage_catalog.build_prompt(
        spec,
        make_member(PersonaKey.project_lead),
        rules,
        make_task_templates([StageName.program]),
        ctx,
        None,
    )

    assert isinstance(bundle, PromptBundle)
    assert bundle.persona_key is PersonaKey.project_lead
    assert ctx.title in bundle.task_description
    assert rules.project_rules in bundle.backstory


@pytest.mark.contract_if("IF-45")
def test_contract_stages_build_prompt_copies_task_tools_and_member_texts(
    stage_catalog: StageCatalog,
) -> None:
    """IF-45: `PromptBundle` の **`task_tools`** がテンプレートの値の写しである（FR-016）。

    台帳の出力欄は `PromptBundle（tasks.yaml テンプレート＋Member の role/goal/backstory
    合成・task_tools。DM-08/27）` と名指しするが、上の正常系が使う見本テンプレートは
    `tools=None` 固定なので `task_tools` への assert が 1 つも無い —— **写し忘れて `None` を
    返す実装でも緑**になる。

    そこで**同じ段を 2 回組む** ——（1）道具を載せたテンプレートで `task_tools` がその値に
    なること（2）既定（`tools=None`）では `None` のままであること。2 つを並べて初めて
    「テンプレートから写している」ことと「定数を返している」ことを区別できる。

    あわせて `role`・`goal` が `Member` の値**そのまま**であることも主張する（現行は
    `backstory` への `Rules` 混入だけを見ており、role・goal は未検証だった）。
    """
    tools = [ToolName.read_issue, ToolName.propose_breakdown]
    member = make_member(PersonaKey.project_lead)
    rules = make_rules()
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.program)}, has_parent=False)

    with_tools = stage_catalog.build_prompt(
        spec,
        member,
        rules,
        make_task_templates([StageName.program], tools=tools),
        make_context_refs(),
        None,
    )
    without_tools = stage_catalog.build_prompt(
        spec,
        member,
        rules,
        make_task_templates([StageName.program]),
        make_context_refs(),
        None,
    )

    assert with_tools.task_tools == tools
    assert without_tools.task_tools is None, "既定を変えると既存テストの前提が動く"

    assert with_tools.role == member.role
    assert with_tools.goal == member.goal
    assert member.backstory in with_tools.backstory


@pytest.mark.contract_if("IF-45")
def test_contract_stages_build_prompt_fills_the_feedback_variable(
    stage_catalog: StageCatalog,
) -> None:
    """IF-45: 入力欄の **`feedback（差し戻し時）`** が本文へ充填される（FR-002a・FR-016）。

    台帳 IF-45 の入力欄は `StageSpec・Member・Rules・TaskTemplates・ContextRefs・
    **feedback（差し戻し時）**` の 6 材料だが、既存 3 本はいずれも第 6 引数が `None` で、
    見本テンプレートの本文も `{title}`・`{acceptance}` しか持たない —— **`feedback` を捨てる
    実装でも緑**になり、差し戻しの理由が Agent へ届かなくても検出できない。

    そこで `{feedback}` だけを持つ本文で**同じ段を 2 回組む** ——（1）差し戻し文言を渡すと
    `task_description` に載ること（2）`None` のときは**空文字**で埋まること。(2) は
    FR-013・SD-53 の「『(なし)』等の日本語の既定文言を持たない」を完全一致で主張する形で
    あり、文言を添えるのは呼び手の役目である。
    """
    feedback = "受入条文が検証できる形になっていないので書き直してください"
    member = make_member(PersonaKey.project_lead)
    rules = make_rules()
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.program)}, has_parent=False)
    templates = make_task_templates([StageName.program], description=FEEDBACK_TEMPLATE_BODY)

    returned = stage_catalog.build_prompt(
        spec, member, rules, templates, make_context_refs(), feedback
    )
    first_try = stage_catalog.build_prompt(
        spec, member, rules, templates, make_context_refs(), None
    )

    assert returned.task_description == f"差し戻し理由:{feedback}"
    assert first_try.task_description == "差し戻し理由:", (
        "`feedback` が無いときに日本語の既定文言を持ち込んでいる（FR-013・SD-53）"
    )
    assert returned.task_description != first_try.task_description


@pytest.mark.contract_if("IF-45")
def test_contract_stages_build_prompt_raises_unknown_persona(
    stage_catalog: StageCatalog,
) -> None:
    """IF-45 例外系: その段が許さないペルソナの `Member` で `UnknownPersona`（FR-017）。

    `program` 段は `mode="propose"` なので、実行側のペルソナ（`programmer`）は割り当てられない。
    """
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.program)}, has_parent=False)

    with pytest.raises(UnknownPersona):
        stage_catalog.build_prompt(
            spec,
            make_member(PersonaKey.programmer),
            make_rules(),
            make_task_templates([StageName.program]),
            make_context_refs(),
            None,
        )


@pytest.mark.contract_if("IF-46")
def test_contract_stages_validate_proposal_returns_gate_result(
    stage_catalog: StageCatalog, proposal_gate: ProposalGate
) -> None:
    """IF-46: 提案の検査が `GateResult` を返す（例外欄が `—`＝ FR-018）。"""
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.request)}, has_parent=False)
    proposal = Proposal(
        items=[
            ProposalItem(
                title=f"契約テストの子課題 {index}",
                summary=f"契約テストの要約 {index}",
                description=f"契約テストの説明 {index}",
                acceptance=[ACCEPTANCE_LINE],
                depends_on=[],
                persona=None,
            )
            for index in (1, 2)
        ]
    )

    result = proposal_gate.validate_proposal(proposal, spec, [])

    assert isinstance(result, GateResult)
    assert result.ok, result.problems
    assert result.order == [0, 1]


@pytest.mark.contract_if("IF-46")
def test_contract_stages_validate_proposal_marks_single_child_observation(
    stage_catalog: StageCatalog, proposal_gate: ProposalGate
) -> None:
    """IF-46: 提案が 1 件のとき `GateResult` に **single-child 観測印**が立つ（FR-016）。

    台帳の出力欄は `GateResult（合格／指摘一覧/single-child 観測印）` と 3 群を名指しする。
    合格・指摘一覧は上のテストが踏むが、観測印は**件数が段の最小と等しいとき**にしか立たない
    ので、2 件の提案では経路そのものを通らない。

    観測印は**指摘ではない** —— 1 件でも `ok` は真のままで、判断は呼び手（orchestrator）に
    委ねられる。この「合格させたうえで印だけ立てる」ことが台帳の規定である。
    """
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.request)}, has_parent=False)
    assert spec.min_items == 1, "この段の最小件数が 1 でないと観測印の経路に入らない"
    proposal = Proposal(
        items=[
            ProposalItem(
                title="契約テストの子課題（唯一）",
                summary="契約テストの要約",
                description="契約テストの説明",
                acceptance=[ACCEPTANCE_LINE],
                depends_on=[],
                persona=None,
            )
        ]
    )

    result = proposal_gate.validate_proposal(proposal, spec, [])

    assert result.observations == ["single_child"]
    assert result.ok, result.problems
    assert result.problems == []
    assert result.order == [0]


@pytest.mark.contract_if("IF-46")
def test_contract_stages_validate_proposal_reads_existing_children(
    stage_catalog: StageCatalog, proposal_gate: ProposalGate
) -> None:
    """IF-46: 入力欄の **`existing_children`（title 一覧）**が判定に効く（FR-002a・FR-016）。

    台帳 IF-46 の入力欄は `Proposal・StageSpec・existing_children（title 一覧）` の 3 材料だが、
    上の 2 本はどちらも第 3 引数が `[]` で、`result.ok` が真・`problems` が空であることしか
    主張しない —— **`existing_children` を捨てて `ok=True`・`problems=[]` を返すだけの実装でも
    緑**になり、受け取る口が一度も使われない。

    そこで**同じ提案を 2 回検査する** ——（1）`existing_children=[]` では合格すること
    （2）1 件目と同じ題名を渡すと `ok is False` になり、その題名を含む指摘が載ること。
    2 つを並べて初めて「引数を読んでいる」ことと「常に合格させている」ことを区別できる。

    現物は `stages/service.py` の `_collision_problems(items, existing_children)` が既存の子との
    題名一致を指摘に載せる（提案内の重複とは別の指摘である）。
    """
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.request)}, has_parent=False)
    proposal = Proposal(items=[_proposal_item(f"契約テストの子課題 {index}") for index in (1, 2)])
    colliding_title = proposal.items[0].title

    without_existing = proposal_gate.validate_proposal(proposal, spec, [])
    with_existing = proposal_gate.validate_proposal(proposal, spec, [colliding_title])

    assert without_existing.ok, without_existing.problems
    assert without_existing.problems == []

    assert isinstance(with_existing, GateResult)
    assert with_existing.ok is False, "既存の子と題名が衝突する提案を合格させている"
    assert any(colliding_title in problem for problem in with_existing.problems), (
        with_existing.problems
    )


@pytest.mark.contract_if("IF-46")
def test_contract_stages_validate_proposal_lists_problems_for_a_malformed_item(
    stage_catalog: StageCatalog, proposal_gate: ProposalGate
) -> None:
    """IF-46: 段の要求を満たさない提案で **指摘一覧（`problems`）**が非空になる（FR-016）。

    台帳の出力欄は `GateResult（合格／**指摘一覧**／single-child 観測印）` の 3 群だが、
    既存 2 本はいずれも合格側しか通らず `problems` への assert は `== []` だけである ——
    **常に `ok=True`・`problems=[]` を返す実装でも緑**になる。

    ここでは 2 件目の受入条文だけを `- [ ] …` の形から外す（本文の長さは変えないので、
    落ちる理由は「1 行 1 条件の形ではない」に絞られる）。**1 件目は正しいまま**なので、
    指摘が件ごとに立つこと（`2件目` を名指しすること）まで主張できる。
    """
    spec = stage_catalog.resolve_stage({Labels.kind(KindLabel.request)}, has_parent=False)
    proposal = Proposal(
        items=[
            _proposal_item("契約テストの子課題 1"),
            _proposal_item("契約テストの子課題 2", acceptance=[MALFORMED_ACCEPTANCE_LINE]),
        ]
    )

    result = proposal_gate.validate_proposal(proposal, spec, [])

    assert isinstance(result, GateResult)
    assert result.ok is False, "受入条文の形が崩れた提案を合格させている"
    assert result.problems, "不合格なのに指摘が 1 件も返らない（差し戻しの材料が無い）"
    assert all("1件目" not in problem for problem in result.problems), result.problems
    assert any("2件目" in problem for problem in result.problems), result.problems


@pytest.mark.contract_if("IF-47")
def test_contract_stages_extract_outcome_tokens_returns_list_of_str(
    proposal_gate: ProposalGate,
) -> None:
    """IF-47: 受入条文から完了ゲート鎖の入力となるトークン列が返る（例外欄が `—`＝ FR-018）。"""
    acceptance = "- [ ] outcome/report.md に結果を書く\n- [ ] outcome/report.md を再掲する（重複）"

    tokens = proposal_gate.extract_outcome_tokens(acceptance)

    assert isinstance(tokens, list)
    assert all(isinstance(token, str) for token in tokens)
    assert tokens == ["outcome/report.md"], tokens


@pytest.mark.contract_if("IF-56")
def test_contract_stages_extract_repo_tokens_returns_list_of_str(
    proposal_gate: ProposalGate,
) -> None:
    """IF-56: 受入条文からリポジトリの成果物（`repo/<パス>`）のトークン列が返る（例外欄が `—`）。

    D25-b。`outcome/` を途中に含む repo パスは outcome のトークンに数えない（両者を取り違えない）。
    """
    acceptance = "- [ ] repo/src/app.py を足す\n- [ ] repo/docs/outcome/x.md も更新する"

    tokens = proposal_gate.extract_repo_tokens(acceptance)

    assert isinstance(tokens, list)
    assert all(isinstance(token, str) for token in tokens)
    assert tokens == ["repo/src/app.py", "repo/docs/outcome/x.md"], tokens
    assert proposal_gate.extract_outcome_tokens(acceptance) == []
