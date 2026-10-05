"""クラスタ 04 の提案ゲートと差し戻し（IF-46・IF-07・IF-45・IF-04）。§6 #6 ×3・#8。

B-12 は 2026-09-21 に修正済み（orchestrator が `kind:` ラベルを**接頭辞込み**で
`resolve_stage` へ渡すようになり、propose 段は実配線で到達可能になった）。ただし
propose 段の鎖 E2E への格上げは**ユーザー裁定で行わない**（design-sync の B 表 B-12）ため、
観測は従来どおり 2 段にする（`test_cluster_03_authz_and_command.py` の `UnknownCommand` と
同型）。

1. **鎖**: 差し戻しの回数（IF-07）は `mode="execute"` の経路でも起きるので、実 `bd` の
   `comments` を数える形でそのまま見られる。
2. **契約**: 提案の検査（IF-46）とプロンプト組立（IF-45）は、実 `ProposalGate`・実
   `StageCatalog` を**実配線から取り出して**直接呼ぶ。スタブは 1 つも介在しない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.wiring import require
from common.interfaces.board import BoardReader
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.settings import MemberResolver, SettingsReader
from common.interfaces.stages import (
    ContextRefs,
    PromptPurpose,
    Proposal,
    ProposalGate,
    ProposalItem,
    StageCatalog,
)
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName
from orchestrator import OrchestratorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from _support.cluster_stores import ClusterStores
    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.executor import CrewOutcome
    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

REVIEW_PERSONA = PersonaKey(OrchestratorConfig().review_persona)
"""レビュー段の担当（実物の設定から引く）。"""


def _subrequest_spec(stage_catalog: StageCatalog) -> object:
    """`subrequest` 段の構造属性を**接頭辞込み**の入力で引く（鎖 E2E 格上げは裁定で見送りの段）。"""
    return stage_catalog.resolve_stage({Labels.kind(KindLabel.subrequest)}, has_parent=True)


@pytest.mark.contract_if("IF-46")
@pytest.mark.cluster_case("04#6")
def test_cluster_04_proposal_gate_reports_problems_from_the_real_stages(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """クラスタ 04 #6 / IF-46: 実 `ProposalGate` が提案の不備を `problems` に載せて返す。

    期待（§6 #6）: 提案に指摘（`validate_proposal`）→ 差し戻し loop。

    propose 段の鎖 E2E は裁定で見送り（module docstring）なので、実 `ProposalGate` を配線から
    取り出して直接呼ぶ。段の構造属性も**実 `StageCatalog`** から引く（見本を自前で作らない）。

    受け入れ条件が**プレースホルダ**（`stages/config.json` の `placeholder_patterns`）の提案を
    渡すと、ゲートが不合格にして理由を返す。合格する提案も対にして渡し、「何を渡しても
    不合格」でないことまで見る —— 合格側の受け入れ条件には **`outcome/<ファイル名>` を書く**
    （`subrequest` 段は `require_outcome_path=True`。2026-09-01 に実測）。
    """
    runtime = cluster([make_crew_outcome()])
    proposal_gate: ProposalGate = require(runtime.wiring.provided, ProposalGate)
    stage_catalog: StageCatalog = require(runtime.wiring.provided, StageCatalog)
    spec = _subrequest_spec(stage_catalog)

    bad = Proposal(
        items=[
            ProposalItem(
                title="不備のある子",
                summary="要約",
                description="説明",
                acceptance=["TBD"],
                depends_on=[],
                persona=None,
            )
        ]
    )
    good = Proposal(
        items=[
            ProposalItem(
                title="まっとうな子",
                summary="要約",
                description="説明",
                acceptance=["- [ ] 成果物 outcome/report.md を書く"],
                depends_on=[],
                persona=PersonaKey.programmer,
            )
        ]
    )

    rejected = proposal_gate.validate_proposal(bad, spec, [])  # pyright: ignore[reportArgumentType]  # 実 StageCatalog の戻り
    accepted = proposal_gate.validate_proposal(good, spec, [])  # pyright: ignore[reportArgumentType]  # 同上

    assert rejected.ok is False, f"プレースホルダの受け入れ条件が通っている: {rejected}"
    assert rejected.problems, "指摘が 1 件も無い（`problems` が空）"
    assert accepted.ok is True, f"まっとうな提案が通らない: {accepted.problems}"
    assert accepted.order, "起票順が返っていない"


@pytest.mark.contract_if("IF-07")
@pytest.mark.cluster_case("04#6")
def test_cluster_04_return_count_is_read_from_the_board_comments(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
    context: ExecutionContext,
) -> None:
    """クラスタ 04 #6 / IF-07: 差し戻しの回数は `[orchestrator]` コメントを `comments` で数える。

    期待（§6 #6）: 回数は `[orchestrator]` コメントを `comments` で数える（**再起動で消えない**）。

    完了を申告しない `CrewOutcome` を返し続けると、orchestrator は `max_returns` 回まで
    差し戻してから人間へ渡す。**差し戻しの記録が盤面に残る**ので、周回をまたいでも回数が
    復元できる —— これが「インメモリのカウンタではない」ことの観測点である。

    件数は `OrchestratorConfig().max_returns` から導く（テストへ直書きしない）。
    """
    marker = OrchestratorConfig().comment_marker
    max_returns = OrchestratorConfig().max_returns
    parent = seed(KindLabel.subrequest, ready=False, title="親")
    issue_id = seed(KindLabel.child, parent=parent, title="差し戻される課題")
    runtime = cluster([make_crew_outcome(completed=False) for _ in range(max_returns + 1)])
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    result = runner.run(context)

    assert result.issues, "1 件も処理していない"
    assert result.issues[0].returns >= 1, (
        f"差し戻しが 1 度も起きていない: {result.issues[0].returns}"
    )
    comments = board_reader.comments(issue_id)
    orchestrator_comments = [c for c in comments if c.body.startswith(marker)]
    assert orchestrator_comments, (
        f"差し戻しが盤面に残っていない（印={marker!r}）: {[c.body[:40] for c in comments]}"
    )


@pytest.mark.contract_if("IF-45")
@pytest.mark.cluster_case("04#6")
def test_cluster_04_build_prompt_uses_the_real_templates_and_rules(
    cluster: Callable[..., ClusterRuntime],
    make_crew_outcome: Callable[..., CrewOutcome],
    stores: ClusterStores,
) -> None:
    """クラスタ 04 #6 / IF-45: 実 `StageCatalog.build_prompt` が実 settings の材料で組む。

    期待（§6 #6）: `max_returns` 到達 → 人間へ・`build_prompt`。

    propose 段の鎖 E2E は裁定で見送りなので、`build_prompt` を配線から取り出して直接呼ぶ。
    渡す `Member`・`Rules`・`TaskTemplates` は**実 settings** が返したものである（見本を自前で
    作ると「実物同士」でなくなる）。

    組み上がったプロンプトに、領域 A の `tasks.yaml` が定めた本文が反映されていることを見る
    （テンプレートを無視して既定文を返す実装との差）。
    """
    runtime = cluster([make_crew_outcome()])
    stage_catalog: StageCatalog = require(runtime.wiring.provided, StageCatalog)
    settings_reader: SettingsReader = require(runtime.wiring.provided, SettingsReader)
    member_resolver: MemberResolver = require(runtime.wiring.provided, MemberResolver)
    project_key = stores.project_key
    member = member_resolver.resolve(project_key, stores.persona)
    rules = settings_reader.get_rules(project_key)
    templates = settings_reader.get_tasks(project_key)
    spec = stage_catalog.resolve_stage(set(), has_parent=True)
    refs = ContextRefs(
        issue_id="cl04-prompt",
        title="プロンプトの確認",
        description="説明",
        acceptance="- [ ] 受け入れ条件",
        parent_title=None,
        parent_acceptance=None,
        human_guidance=[],
        input_refs=[],
        existing_children=[],
        purpose=PromptPurpose.work,
        is_final_review=False,
    )

    bundle = stage_catalog.build_prompt(spec, member, rules, templates, refs, None)

    assert bundle.persona_key is member.persona_key, bundle.persona_key
    assert bundle.role == member.role, "メンバの役割が反映されていない"
    child_template = templates.templates[str(StageName.child)]
    assert (
        "クラスタ 04" in bundle.task_description
        or child_template.description.split("{")[0].strip() in bundle.task_description
    ), f"段テンプレートの本文が反映されていない: {bundle.task_description!r}"


@pytest.mark.contract_if("IF-04")
@pytest.mark.cluster_case("04#8")
def test_cluster_04_existing_children_are_read_back_from_the_real_board(
    cluster: Callable[..., ClusterRuntime],
    seed: Callable[..., str],
    make_crew_outcome: Callable[..., CrewOutcome],
) -> None:
    """クラスタ 04 #8 / IF-04: `children_of` が実 `bd` から既存の子を読み戻す。

    期待（§6 #8）: 既に子がいる（クラッシュ窓の再入）→ 人間へ（自動再投入しない D01-b）。

    分解層の再入ガードは propose 段に居るので鎖 E2E では踏まない（module docstring）。ここでは
    ガードの材料である `children_of` が**実 `bd` の親子関係を読み戻せる**ことを確かめる ——
    子が居るのに空を返す実装なら、ガードは黙って素通りしてしまう。

    起動時の拾い直し側の同じガードは
    `test_cluster_04_recover_and_claim.py::test_cluster_04_recover_hands_off_a_leftover_that_already_has_children`
    が鎖で踏んでいる（D01 の二重ガード）。
    """
    parent = seed(KindLabel.subrequest, ready=False, title="子を持つ親")
    child = seed(KindLabel.child, parent=parent, ready=False, title="既にいる子")
    runtime = cluster([make_crew_outcome()])
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)

    children = board_reader.children_of(parent)

    assert [issue.id for issue in children] == [child], (
        f"実 `bd` の子を読み戻せていない: {[issue.id for issue in children]}"
    )
    assert not board_reader.children_of(child), "子の子は居ないはず"
