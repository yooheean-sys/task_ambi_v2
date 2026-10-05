"""F05・F06 — 前処理と人間の指示・回答の収集。**自分では 1 回も書き込まない**（C-3）。

正確には「**成功経路では盤面へ 1 回も書かない**。失敗したときも自分では書かず、人間へ渡す
1 経路（F12＝`service/handoff.py`）へ委ねる」である（`_hand_off`・`_folder_hand_off` は
`handoff.hand_off` を呼ぶので、盤面には F12 の 3 手が出る）。**この 1 ファイルに盤面への
書込の呼び出しは 1 つも無い**ことが機械的な不変条件（2026-08-26 に文言を実態へ是正）。

呼ぶ順は US1 AC-3 が固定する: `show` → 段の解決 → 主体の解決 → 設定 5 種 → フォルダ検証。
**設定 5 種は課題ごとに 1 回**読み、1 課題の処理中（差し戻しの再実行を含む）は読み直さない
（FR-025・DIFF-21。5 メソッドが 5 つとも `project_key` を取るため「kickoff の開始時に 1 回」は
組めない）。段テンプレートの既定担当（D16-f）を見るときだけ `get_tasks` を主体の解決の前に
呼ぶが、**呼ぶ回数は 1 課題につき 1 回のまま**である。

4 つの失敗（段・主体・プロジェクト・フォルダ）はいずれも**人間へ渡す同一の 3 手**へ倒れる
（SC-011）。段・主体が未解決のまま倒れた場合は処理結果の一覧に載せられないので、`PrepareFailure`
がそれを呼び手（周回）へ伝える（FR-071a と同じ構造的理由）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.interfaces.board import IssueStatus
from common.interfaces.settings import MemberNotFound, ProjectNotFound
from common.interfaces.stages import UnknownStage
from common.interfaces.vault import VaultNotFound, VaultPathViolation
from common.kernel.enums import KindLabel, LabelDim, Labels, PersonaKey
from orchestrator.models import IssueRun
from orchestrator.service import handoff

if TYPE_CHECKING:
    from common.interfaces.board import Issue, IssueRef
    from common.interfaces.settings import Member, TaskTemplates
    from common.interfaces.stages import StageSpec
    from common.kernel.enums import StageName
    from orchestrator.models import IssueDisposition, Ports


@dataclass(frozen=True, slots=True)
class PrepareFailure:
    """前処理が人間へ渡して終わった（周回は次の課題へ進む）。

    `stage`・`persona` が揃っていれば処理結果の一覧に 1 行載せられる。片方でも未解決なら
    `IssueRunSummary` を組めないので `KickoffResult.notes` に落とす（FR-071a と同じ理屈）。
    """

    disposition: IssueDisposition
    stage: StageName | None = None
    persona: PersonaKey | None = None


type PrepareResult = IssueRun | PrepareFailure
"""前処理の戻り（成功なら 1 課題の処理状態、失敗なら人間へ渡した記録）。"""


def prepare_issue(ports: Ports, issue_id: str) -> PrepareResult:
    """確保した課題を読み直し、段・主体・設定・フォルダを揃えて `IssueRun` を組む。

    Args:
        ports: 10 契約と自分の設定。
        issue_id: 確保した課題の識別子。

    Returns:
        組み上がった `IssueRun`、または人間へ渡した記録。

    Raises:
        BoardUnavailable: 盤面に届かない（周回が打ち切る）。
        IssueNotFound: 課題を読み直せない（同上）。
    """
    issue = ports.deps.board_reader.show(issue_id)

    project_key = _project_key(issue)
    if project_key is None:
        return _hand_off(ports, issue_id, "ProjectLabelMissing", "課題に project: ラベルが無い")

    settings = ports.deps.settings_reader
    stage: StageSpec
    try:
        stage = ports.deps.stage_catalog.resolve_stage(
            _kind_labels(issue), issue.parent is not None
        )
    except UnknownStage as exc:
        return _hand_off(ports, issue_id, "UnknownStage", str(exc))

    templates: TaskTemplates | None = None
    persona = _persona_label(issue)
    if persona is None:  # D16-f: 段テンプレートの既定担当 → 従来既定
        try:
            templates = settings.get_tasks(project_key)
        except ProjectNotFound as exc:
            return _hand_off(ports, issue_id, "ProjectNotFound", str(exc), stage.name)
        persona = _template_agent(templates, stage.name) or PersonaKey.general

    member: Member
    try:
        member = ports.deps.member_resolver.resolve(project_key, persona)
    except MemberNotFound as exc:
        return _hand_off(ports, issue_id, "MemberNotFound", str(exc), stage.name, persona)

    try:
        project = settings.get_project(project_key)
        limits = settings.get_limits(project_key)
        rules = settings.get_rules(project_key)
        hooks = settings.get_hooks(project_key)
        if templates is None:
            templates = settings.get_tasks(project_key)
    except ProjectNotFound as exc:
        return _hand_off(ports, issue_id, "ProjectNotFound", str(exc), stage.name, persona)

    try:  # フォルダ検証は**課題ごとに毎回**（FN-26・SC-012）
        folders = ports.deps.vault_reader.check_project_folders(project.folders)
    except (VaultPathViolation, VaultNotFound) as exc:
        return _folder_hand_off(ports, issue_id, [str(exc)], stage.name, persona)
    if not folders.ok:
        return _folder_hand_off(ports, issue_id, folders.missing, stage.name, persona)

    return IssueRun(
        issue=issue,
        spec=stage,
        member=member,
        project=project,
        limits=limits,
        rules=rules,
        hooks=hooks,
        templates=templates,
        human_guidance=collect_human_guidance(ports, issue),
        returns=0,
        outcome=None,
    )


def collect_human_guidance(ports: Ports, issue: Issue) -> tuple[str, ...]:
    """F06 — 人間が書いた本文だけを材料として集める（FR-028〜FR-032）。

    **システム actor と AI メンバの書き込みを混ぜない**（FR-029。いずれも台帳内＝人間ではない）。
    未回答の質問が無く、過去に質問があった場合は**閉じられた質問のコメント**からも集める
    （FR-030。質問は親子ではなく依存で張るので `issue.dependencies` から辿る＝ P5D-059 (2)）。

    **この 1 回の読取が材料のすべて**である（FR-032。kickoff の最中に付いたコメントは
    次の周回で読む）。

    Args:
        ports: 10 契約と自分の設定。
        issue: 確保した課題（読み直したもの）。

    Returns:
        人間の本文（`is_human` が真の actor のものだけ）。

    Raises:
        BoardUnavailable: コメントを読めない（**指示なしで先へ進まない**＝ FR-031・SC-014）。
    """
    reader = ports.deps.board_reader
    bodies = _human_bodies(ports, issue.id)
    if reader.open_question_of(issue.id) is None:
        for reference in _closed_questions(issue):
            bodies.extend(_human_bodies(ports, reference.id))
    return tuple(bodies)


def _human_bodies(ports: Ports, issue_id: str) -> list[str]:
    """1 課題のコメントから人間の本文だけを抜く。"""
    resolver = ports.deps.member_resolver
    return [
        comment.body
        for comment in ports.deps.board_reader.comments(issue_id)
        if resolver.is_human(comment.actor)
    ]


def _closed_questions(issue: Issue) -> list[IssueRef]:
    """閉じられた質問の依存（`kind:question` かつ `closed`）を列挙する。"""
    question = Labels.kind(KindLabel.question)
    return [
        reference
        for reference in issue.dependencies
        if question in reference.labels and reference.status is IssueStatus.closed
    ]


def _project_key(issue: Issue) -> str | None:
    """`project:` ラベルからプロジェクトキーを取り出す（無ければ `None`）。"""
    for label in issue.labels:
        parsed = Labels.parse(label)
        if parsed is not None and parsed[0] is LabelDim.project and parsed[1]:
            return parsed[1]
    return None


def _kind_labels(issue: Issue) -> set[str]:
    """`kind:` ラベルの集合（**接頭辞込み**。段の解決にそのまま渡す）。

    `Labels.kind(...)` へ**再構成しない** —— 段表が参照しない値（`kind:bogus` 等）は
    生の文字列のまま渡し、`UnknownStage` へ倒すのは stages の事前検査（SD-41）の役目。
    """
    labels: set[str] = set()
    for label in issue.labels:
        parsed = Labels.parse(label)
        if parsed is not None and parsed[0] is LabelDim.kind:
            labels.add(label)
    return labels


def _persona_label(issue: Issue) -> PersonaKey | None:
    """`persona:` ラベルからペルソナを取り出す（未知の値・不在なら `None`）。"""
    for label in issue.labels:
        parsed = Labels.parse(label)
        if parsed is None or parsed[0] is not LabelDim.persona:
            continue
        try:
            return PersonaKey(parsed[1])
        except ValueError:
            return None
    return None


def _template_agent(templates: TaskTemplates, stage: StageName) -> PersonaKey | None:
    """段テンプレートの既定担当（`agent:`）。無ければ `None`。"""
    template = templates.templates.get(stage.value)
    return template.agent if template is not None else None


def _hand_off(
    ports: Ports,
    issue_id: str,
    kind: str,
    detail: str,
    stage: StageName | None = None,
    persona: PersonaKey | None = None,
) -> PrepareFailure:
    """設定欠落として人間へ渡す（DIFF-12 の裁定で 4 種を `config_missing` に寄せる）。"""
    disposition = handoff.hand_off(ports, issue_id, handoff.config_missing(kind, detail))
    return PrepareFailure(disposition=disposition, stage=stage, persona=persona)


def _folder_hand_off(
    ports: Ports,
    issue_id: str,
    missing: list[str],
    stage: StageName | None,
    persona: PersonaKey | None,
) -> PrepareFailure:
    """フォルダ不在として人間へ渡す（**不在のパス**を本文に載せる＝ D07-g）。"""
    disposition = handoff.hand_off(ports, issue_id, handoff.folder_missing(missing))
    return PrepareFailure(disposition=disposition, stage=stage, persona=persona)
