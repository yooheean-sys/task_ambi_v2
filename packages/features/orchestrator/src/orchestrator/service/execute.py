"""F09・F10 — 完了ゲートとレビュー／エスカレーション。

**orchestrator が持つ正準形の知識は `OUTCOME_PREFIX` の定数 1 つだけ**（FR-051・FR-061）。
接頭辞より後ろの名前規則は保管庫の名前ゲートに委ね、規則違反が返っても**呼び手へ例外を
漏らさず**差し戻しの指摘に載せる（FR-052）。**受けるのは保管庫の契約例外 2 種**
（`VaultPathViolation`・`VaultNotFound`）で、前処理（`prepare._prepare` のフォルダ検証）と
**同じ組**である —— 上流（`kickoff.run_issue`・`loop._process`）はどちらも受けないので、
ここで落とさないと `OrchestratorService.run` から素通しになる（FR-003・SC-003）。

質問で待たせるのは**依存の追加**であり、**親子の指定を使わない**（FR-064・P5D-059 (2)。
親子で張ると `open_question_of` が拾えなくなる）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.board import (
    BoardUnavailable,
    BoardWriteUnverified,
    IssueNotFound,
    IssueSpec,
)
from common.interfaces.executor import CrewSpec
from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.interfaces.settings import MemberNotFound
from common.interfaces.stages import ContextRefs, PromptPurpose
from common.interfaces.vault import VaultNotFound, VaultPathViolation
from common.kernel.enums import KindLabel, Labels, PersonaKey
from orchestrator.models import (
    CloseIntent,
    IssueDisposition,
    Retry,
    Settled,
    StepResult,
    outcome_key,
    repo_key,
)
from orchestrator.service import handoff, writes

if TYPE_CHECKING:
    from common.interfaces.board import Issue
    from orchestrator.models import IssueRun, Ports

RETRY_GATE = "完了ゲート"
"""差し戻しコメントの `[分類]`（成果物の実在）。"""

RETRY_REVIEW = "レビュー"
"""差し戻しコメントの `[分類]`（レビューの却下）。"""

RETRY_NO_REPORT = "申告なし"
"""差し戻しコメントの `[分類]`（完了の申告も失敗も無い）。"""

QUESTION_TITLE_PREFIX = "[要判断]"
"""質問課題の題名の頭（FR-063）。"""


def handle(ports: Ports, run: IssueRun) -> StepResult:
    """遂行の段を 1 回進める（F09・F10）。

    Args:
        ports: 10 契約と自分の設定。
        run: 前処理で組んだ 1 課題の処理状態（`outcome` に実行結果が入っている）。

    Returns:
        `CloseIntent`（閉じたい）／`Retry`（差し戻す）／`Settled`（質問待ち・人間へ渡した）。
    """
    outcome = run.outcome
    if outcome is not None and outcome.escalation is not None:
        return _escalate(ports, run)
    if outcome is None or not outcome.completed:
        return Retry(category=RETRY_NO_REPORT, feedback="完了の申告も失敗の申告も無い")
    return _complete(ports, run)


# --------------------------------------------------------------------------------------
# F09 — 完了ゲートとレビュー
# --------------------------------------------------------------------------------------


def _complete(ports: Ports, run: IssueRun) -> StepResult:
    """成果物の実在を 1 件ずつ確かめ、通ればレビューへ進む（FR-051〜FR-055）。"""
    problems = _outcome_gaps(ports, run)
    if problems:
        return Retry(
            category=RETRY_GATE,
            feedback="成果物を確かめられなかった: " + " / ".join(problems),
        )
    return _review(ports, run)


def _outcome_gaps(ports: Ports, run: IssueRun) -> list[str]:
    """受入条文の成果物のうち、実在を確かめられなかったものを列挙する。

    Args:
        ports: 10 契約と自分の設定。
        run: 1 課題の処理状態。

    Returns:
        不在・非正準・規則違反の説明（空なら全て実在）。
    """
    tokens = ports.deps.proposal_gate.extract_outcome_tokens(run.issue.acceptance)
    folders = run.project.folders
    problems: list[str] = []
    for token in tokens:
        filename = outcome_key(token)
        if filename is None:  # 接頭辞で始まらない → 実在確認を呼ばずに不合格（SC-023a）
            problems.append(f"{token}: 正準形の接頭辞で始まっていない")
            continue
        try:
            exists = ports.deps.vault_reader.outcome_exists(folders, filename)
        except (VaultPathViolation, VaultNotFound) as exc:  # 漏らさず指摘に載せる（FR-052）
            problems.append(f"{filename}: {_vault_gap(exc)}")
            continue
        if not exists:
            problems.append(f"{filename}: 実在しない")
    problems.extend(_repo_gaps(ports, run))
    return problems


def _repo_gaps(ports: Ports, run: IssueRun) -> list[str]:
    """受入条文のリポジトリの成果物（`repo/<パス>`）のうち、実在を確かめられなかったものを列挙する。

    D25-b。トークンが無ければ repo に問い合わせない（リポジトリ未登録の PJ は従来どおり）。
    未登録・未 clone・規則違反は**漏らさず指摘に載せる**（`_outcome_gaps` と同じ規律）。

    Args:
        ports: 10 契約と自分の設定。
        run: 1 課題の処理状態。

    Returns:
        不在・未登録・規則違反の説明（空なら全て実在）。
    """
    tokens = ports.deps.proposal_gate.extract_repo_tokens(run.issue.acceptance)
    if not tokens:
        return []
    repos = run.project.repos
    if repos is None or repos.local_path is None:
        return [f"{token}: このプロジェクトにはリポジトリが登録されていない" for token in tokens]
    problems: list[str] = []
    for token in tokens:
        path = repo_key(token)
        if path is None:  # pragma: no cover - 段の抽出は必ず接頭辞から始まる
            problems.append(f"{token}: 正準形の接頭辞で始まっていない")
            continue
        try:
            exists = ports.deps.repo_reader.exists(repos, path)
        except RepoNotFound as exc:
            problems.append(f"{token}: {exc}")
            continue
        except RepoPathViolation as exc:
            problems.append(f"{token}: {exc.kind}: {exc.detail}")
            continue
        if not exists:
            problems.append(f"{token}: 実在しない")
    return problems


def _vault_gap(exc: VaultPathViolation | VaultNotFound) -> str:
    """保管庫の契約例外を差し戻しの指摘 1 行へ写す。

    **`VaultNotFound` は `not exists` と同じ帰結**（「成果物が無い」）なので、指摘の語も
    実在しなかった場合に揃える。`VaultPathViolation` だけは違反の種類と詳細を載せる
    （名前規則の正本は保管庫側にあり、差し戻された側が直せるのは詳細を見たときだけ）。

    Args:
        exc: `outcome_exists` が送出した保管庫の契約例外。

    Returns:
        差し戻しの指摘に載せる説明（ファイル名は呼び手が前置する）。
    """
    if isinstance(exc, VaultPathViolation):
        return f"{exc.kind}: {exc.detail}"
    return f"実在しない（{exc.rel_path}）"


def _review(ports: Ports, run: IssueRun) -> StepResult:
    """レビュー担当を立てて読ませ、承認されて初めて `CloseIntent` を返す（FR-054・FR-055）。"""
    try:
        reviewer = ports.deps.member_resolver.resolve(
            run.project.project_key, PersonaKey(ports.config.review_persona)
        )
    except MemberNotFound as exc:  # FR-057
        return Settled(
            handoff.hand_off(
                ports, run.issue.id, handoff.config_missing("MemberNotFound", str(exc))
            )
        )

    parent = _parent_of(ports, run.issue)
    parent_acceptance = parent.acceptance if parent is not None else None
    repos = run.project.repos  # CFG-74・D24: 未登録なら None（→ 空文字で埋まる）
    ctx = ContextRefs(
        issue_id=run.issue.id,
        title=run.issue.title,
        description=run.issue.description,
        acceptance=run.issue.acceptance,
        parent_title=parent.title if parent is not None else None,
        parent_acceptance=parent_acceptance,
        human_guidance=list(run.human_guidance),
        input_refs=[],  # orchestrator は入力を列挙しない（DIFF-18）
        existing_children=[],
        purpose=PromptPurpose.review,
        is_final_review=bool(parent_acceptance),  # D16-a
        repository_remote_url=repos.remote_url if repos is not None else None,
        repository_local_path=repos.local_path if repos is not None else None,
    )
    bundle = ports.deps.stage_catalog.build_prompt(
        run.spec, reviewer, run.rules, run.templates, ctx, None
    )
    outcome = ports.deps.crew_executor.run(
        CrewSpec(
            issue_id=run.issue.id,
            mode=run.spec.mode,
            purpose=PromptPurpose.review,
            bundle=bundle,
            member=reviewer,
            hooks=run.hooks,
            folders=run.project.folders,
            parent_acceptance=parent_acceptance,
            repos=run.project.repos,
        )
    )
    verdict = outcome.review
    if verdict is None:
        return Retry(category=RETRY_REVIEW, feedback="レビューの結果が返っていない")
    if not verdict.approved:
        return Retry(category=RETRY_REVIEW, feedback=verdict.feedback)
    return CloseIntent(reason=f"レビュー承認: {verdict.feedback}", actor=run.member.bd_actor)


def _parent_of(ports: Ports, issue: Issue) -> Issue | None:
    """親課題を読む（無ければ `None`）。**キャッシュしない**（FR-088）。"""
    if issue.parent is None:
        return None
    return ports.deps.board_reader.show(issue.parent)


# --------------------------------------------------------------------------------------
# F10 — エスカレーション（UC-04）
# --------------------------------------------------------------------------------------


def _escalate(ports: Ports, run: IssueRun) -> StepResult:
    """未回答の質問を再利用するか起票し、依存で待たせて解放する（FR-062〜FR-065）。

    **盤面の契約例外を呼び手へ漏らさない**（FR-003・SC-003）—— `BoardWriter.create` は
    `BoardWriteUnverified` を宣言するが、上流（`kickoff.run_issue`・`loop._process`）が受ける
    のは `(BoardUnavailable, IssueNotFound)` だけなので、ここで受けないと
    `OrchestratorService.run` から素通しになる。分解の段（`propose.py`）と**同じ 3 例外**を
    同じ場所で受け、人間へ渡す 1 経路（F12）へ倒す。

    受けを **1 つの `try` にまとめる**のは、質問だけ起票されて依存が張れていない状態が
    「途中で失敗した」＝ `unsafe` に相当するためである（FR-046・D01-b）。**自動では
    再試行しない**。
    """
    issue_id = run.issue.id
    question_id: str | None = None
    linked = False
    try:
        existing = ports.deps.board_reader.open_question_of(issue_id)
        if existing is not None:
            question_id = existing.id  # 再利用（起票 0 回＝ SC-025）
        else:
            question_id = writes.create_issue(ports, _question_spec(run), run.member.bd_actor)

        # 待たせるのは依存の追加。**`--parent`（親子の指定）を使わない**（SC-026）
        ports.deps.board_writer.add_dependency(issue_id, question_id, run.member.bd_actor)
        linked = True
        ports.deps.board_writer.release(issue_id, ports.config.system_actor)
    except (BoardUnavailable, BoardWriteUnverified, IssueNotFound) as exc:
        return Settled(
            handoff.hand_off(
                ports, issue_id, handoff.unsafe(_escalation_done(question_id, linked, exc))
            )
        )
    return Settled(IssueDisposition.escalated)


def _escalation_done(question_id: str | None, linked: bool, exc: Exception) -> tuple[str, ...]:
    """人間へ渡す本文に載せる「済んだ処理」の一覧（FN-49。`propose._done` と同じ形）。"""
    return (
        f"質問課題: {question_id if question_id is not None else 'なし'}",
        f"依存: {'張れた' if linked else '未実行'}",
        f"失敗した処理: {type(exc).__name__}: {exc}",
    )


def _question_spec(run: IssueRun) -> IssueSpec:
    """質問課題の起票仕様（`kind:question`・`[要判断]`・3 項目の本文・**親を持たせない**）。"""
    outcome = run.outcome
    escalation = outcome.escalation if outcome is not None else None
    body = "\n".join(
        [
            "## 試したこと",
            escalation.what_i_tried if escalation is not None else "",
            "## 詰まった理由",
            escalation.why_stuck if escalation is not None else "",
            "## 決めてほしいこと",
            escalation.decision_needed if escalation is not None else "",
        ]
    )
    return IssueSpec(
        title=f"{QUESTION_TITLE_PREFIX} {run.issue.title}",
        description=body,
        labels=[Labels.kind(KindLabel.question), Labels.project(run.project.project_key)],
        parent=None,
        acceptance="",
    )
