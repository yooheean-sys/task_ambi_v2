"""F08 — 分解の段（ガード・検査・被覆・起票順序の不変条件）。

**起票順序の不変条件**（FR-045）: 起票 → 兄弟の依存 → 親に依存していた側の付け替え →
盤面を読み直して確かめる → 子が 1 件なら観測の印 → （親を閉じる）。**親を閉じるのは
`service/kickoff.py`** が計測の締めの後に行う（C-1）ので、ここは `CloseIntent` を返すに留める。

**途中で失敗したら自動で再試行しない**（FR-046・FN-49・D01-b）。既に済んだ処理を添えて
人間へ渡す 1 経路に倒す。

親条文の被覆検査は**検査に合格したあと・起票の前**に置く（DIFF-01・B-25）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.board import (
    BoardUnavailable,
    BoardWriteUnverified,
    IssueNotFound,
    IssueSpec,
)
from common.kernel.enums import Labels
from orchestrator.models import (
    SINGLE_CHILD_NOTE,
    SINGLE_CHILD_OBSERVATION,
    CloseIntent,
    Retry,
    Settled,
    StepResult,
    outcome_key,
    repo_key,
)
from orchestrator.service import handoff, writes

if TYPE_CHECKING:
    from common.interfaces.stages import Proposal
    from orchestrator.models import IssueRun, Ports

RETRY_GATE = "形の検査"
"""差し戻しコメントの `[分類]`（提案の形）。"""

RETRY_COVERAGE = "被覆"
"""差し戻しコメントの `[分類]`（親条文の被覆）。"""


def handle(ports: Ports, run: IssueRun) -> StepResult:
    """分解の段を 1 回進める（F08）。

    Args:
        ports: 10 契約と自分の設定。
        run: 前処理で組んだ 1 課題の処理状態（`outcome` に提案が入っている）。

    Returns:
        `CloseIntent`（親を閉じたい）／`Retry`（差し戻す）／`Settled`（人間へ渡した）。
    """
    issue_id = run.issue.id
    reader = ports.deps.board_reader

    children = reader.children_of(issue_id)  # D01 二重ガードの分解層側（FR-040）
    if children:
        return Settled(
            handoff.hand_off(
                ports, issue_id, handoff.guard_children([child.id for child in children])
            )
        )

    outcome = run.outcome
    proposal = outcome.proposal if outcome is not None else None
    if proposal is None:
        return Retry(category=RETRY_GATE, feedback="分解の提案が返っていない")

    gate = ports.deps.proposal_gate.validate_proposal(
        proposal, run.spec, [child.title for child in children]
    )
    if not gate.ok:
        return Retry(category=RETRY_GATE, feedback="\n".join(gate.problems))

    missing = _coverage_gaps(ports, run, proposal)
    if missing:
        return Retry(
            category=RETRY_COVERAGE,
            feedback="親の受入条文にある成果物が子で覆われていない: " + ", ".join(missing),
        )

    if not gate.order:
        return Retry(category=RETRY_GATE, feedback="起票順を決められない（依存が循環している）")

    return _create_children(ports, run, proposal, gate.order, gate.observations)


def _coverage_gaps(ports: Ports, run: IssueRun, proposal: Proposal) -> list[str]:
    """親の受入条文の成果物のうち、**どの子にも現れないもの**を返す（FR-058〜FR-061）。

    突合の鍵は**接頭辞を剥がした残り**（`models.outcome_key`）。接頭辞で始まらない親の
    トークンは対象にしない。親に正準形のトークンが 1 つも無ければ**何も言わない**。
    リポジトリの成果物（`repo/<パス>`。D25-b）も同じく突合し、鍵は接頭辞込みにする（`_keys`）。

    Args:
        ports: 10 契約と自分の設定。
        run: 1 課題の処理状態（親の受入条文を持つ）。
        proposal: 検査に合格した提案。

    Returns:
        覆われていない成果物名（outcome は接頭辞を剥がした残り・repo は接頭辞込み）の一覧。
    """
    parent_keys = _keys(ports, run.issue.acceptance)
    if not parent_keys:
        return []
    child_keys: set[str] = set()
    for item in proposal.items:
        child_keys |= _keys(ports, "\n".join(item.acceptance))
    return sorted(parent_keys - child_keys)


def _keys(ports: Ports, acceptance: str) -> set[str]:
    """条文から成果物の鍵の集合を作る（outcome は接頭辞を剥がした名前・repo は接頭辞込み）。

    リポジトリの成果物（D25-b）は**接頭辞込みのトークン**を鍵にする。剥がすと `outcome/a.md`
    と `repo/a.md` が同じ鍵 `a.md` になり、別物どうしで被覆が成立してしまう。
    """
    gate = ports.deps.proposal_gate
    keys = {
        key
        for key in (outcome_key(token) for token in gate.extract_outcome_tokens(acceptance))
        if key is not None
    }
    keys |= {token for token in gate.extract_repo_tokens(acceptance) if repo_key(token) is not None}
    return keys


def _create_children(
    ports: Ports,
    run: IssueRun,
    proposal: Proposal,
    order: list[int],
    observations: list[str],
) -> StepResult:
    """起票順序の不変条件を守って子を作る。途中で失敗したら**やり直さず**人間へ渡す。

    Args:
        ports: 10 契約と自分の設定。
        run: 1 課題の処理状態。
        proposal: 検査に合格した提案。
        order: 検査が返した起票順（提案内 index）。
        observations: 検査の観測（`single_child` があれば観測の印を付ける）。

    Returns:
        `CloseIntent`（親を閉じたい）または `Settled`（途中失敗で人間へ渡した）。
    """
    issue_id = run.issue.id
    actor = run.member.bd_actor  # 子の起票は AI の行為（FR-046a・P5D-037）
    writer = ports.deps.board_writer
    created: dict[int, str] = {}
    edges: list[str] = []
    moved: list[str] = []
    try:
        for index in order:
            created[index] = writes.create_issue(ports, _child_spec(run, proposal, index), actor)
        for index in order:
            for dependency in proposal.items[index].depends_on:
                target = created.get(dependency)
                if target is None:
                    continue
                writer.add_dependency(created[index], target, actor)
                edges.append(f"{created[index]} blocked_by {target}")
        for index in order:
            writer.move_dependents(issue_id, created[index], actor)
            moved.append(created[index])  # 逐次積む（部分成功を落とさない＝ T092）
        confirmed = {child.id for child in ports.deps.board_reader.children_of(issue_id)}
        if not set(created.values()) <= confirmed:
            return Settled(
                handoff.hand_off(
                    ports,
                    issue_id,
                    handoff.unsafe(_done(created, edges, moved, "確認読取: 子が読み戻せない")),
                )
            )
        if SINGLE_CHILD_OBSERVATION in observations:
            writer.set_labels(issue_id, add=[SINGLE_CHILD_NOTE], remove=[], actor=actor)
    except (BoardUnavailable, BoardWriteUnverified, IssueNotFound) as exc:
        return Settled(
            handoff.hand_off(
                ports,
                issue_id,
                handoff.unsafe(
                    _done(created, edges, moved, f"失敗した処理: {type(exc).__name__}: {exc}")
                ),
            )
        )
    return CloseIntent(reason=f"分解完了 {len(created)} 件", actor=actor)


def _child_spec(run: IssueRun, proposal: Proposal, index: int) -> IssueSpec:
    """子 1 件の起票仕様を組む（ラベルは**呼び手が全て載せる**＝ FN-05・FR-044）。"""
    item = proposal.items[index]
    persona = item.persona if item.persona is not None else run.member.persona_key
    labels = [
        *run.spec.child_labels,
        Labels.project(run.project.project_key),
        Labels.persona(persona),
        Labels.role_executor(),
    ]
    return IssueSpec(
        title=item.title,
        description=item.description,
        labels=labels,
        parent=run.issue.id,
        acceptance="\n".join(item.acceptance),
    )


def _done(
    created: dict[int, str], edges: list[str], moved: list[str], failure: str
) -> tuple[str, ...]:
    """人間へ渡す本文に載せる「済んだ処理」の一覧（FN-49・contracts §3）。

    **付け替えは部分成功まで表す**（T092）—— 全件を回し切ってから真偽 1 つで表すと、
    2 件目で失敗したときに「1 件目は済んでいる」事実が落ち、引き継ぐ人間が同じ処理を
    やり直す危険がある。`created`・`edges` と同じく逐次積んだ実測を出す。
    """
    return (
        f"作成済みの子: {', '.join(created.values()) if created else 'なし'}",
        f"張れた依存: {', '.join(edges) if edges else 'なし'}",
        f"付け替え: {_moved_note(created, moved)}",
        failure,
    )


def _moved_note(created: dict[int, str], moved: list[str]) -> str:
    """付け替えの成否（全件は `実行済み`・皆無は `未実行`・途中までは `一部のみ`）。"""
    if not moved:
        return "未実行"
    if len(moved) == len(created):
        return "実行済み"
    return f"一部のみ: {', '.join(moved)}"
