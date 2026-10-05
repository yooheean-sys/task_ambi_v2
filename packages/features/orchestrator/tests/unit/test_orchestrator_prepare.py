"""前処理（F05）と人間の指示・回答の収集（F06）。US1 AC-3・SC-011〜SC-014。"""

from __future__ import annotations

import pytest
from conftest import (
    Stubs,
    make_comment,
    make_issue,
    make_member,
    make_ports,
    make_stage_spec,
    make_templates,
    persona_label,
    project_label,
)

from common.interfaces.board import BoardUnavailable, IssueRef, IssueStatus
from common.interfaces.settings import (
    MemberNotFound,
    ProjectNotFound,
    TaskTemplate,
    TaskTemplates,
)
from common.interfaces.stages import UnknownStage
from common.interfaces.vault import FolderCheck, VaultPathViolation
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName
from orchestrator.models import IssueDisposition, IssueRun
from orchestrator.service import prepare
from orchestrator.service.prepare import PrepareFailure

CANONICAL_ORDER = (
    "BoardReader.show",
    "StageCatalog.resolve_stage",
    "MemberResolver.resolve",
    "SettingsReader.get_project",
    "SettingsReader.get_limits",
    "SettingsReader.get_rules",
    "SettingsReader.get_hooks",
    "SettingsReader.get_tasks",
    "VaultReader.check_project_folders",
)

WRITES = (
    "BoardWriter.claim_next",
    "BoardWriter.create",
    "BoardWriter.add_dependency",
    "BoardWriter.move_dependents",
    "BoardWriter.release",
    "BoardWriter.comment",
    "BoardWriter.close",
    "BoardWriter.set_labels",
)


def _claimed(stubs: Stubs, *, labels: list[str] | None = None, issue_id: str = "bd-1") -> str:
    issue = make_issue(
        issue_id,
        labels=labels
        if labels is not None
        else [project_label(), Labels.kind(KindLabel.child), persona_label()],
    )
    stubs.state.add(issue)
    return issue.id


# --------------------------------------------------------------------------------------
# 呼び出し順（US1 AC-3）と「1 回も書かない」不変条件（C-3）
# --------------------------------------------------------------------------------------


def test_canonical_call_order(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, IssueRun)
    assert stubs.log.only(*CANONICAL_ORDER) == list(CANONICAL_ORDER)


def test_prepare_never_writes(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, _claimed(stubs))

    assert [name for name in stubs.log.targets() if name in WRITES] == []


def test_each_setting_is_read_once_per_issue(stubs: Stubs) -> None:
    """設定 5 種は**課題ごとに 1 回**（FR-025・DIFF-21）。"""
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, _claimed(stubs))

    for name in (
        "SettingsReader.get_project",
        "SettingsReader.get_limits",
        "SettingsReader.get_rules",
        "SettingsReader.get_hooks",
        "SettingsReader.get_tasks",
    ):
        assert stubs.log.count(name) == 1


def test_folder_check_runs_for_every_issue(stubs: Stubs) -> None:
    """フォルダ検証は**課題ごとに毎回**（SC-012・FR-026）。"""
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, _claimed(stubs, issue_id="bd-1"))
    prepare.prepare_issue(ports, _claimed(stubs, issue_id="bd-2"))

    assert stubs.log.count("VaultReader.check_project_folders") == 2


def test_stage_is_resolved_from_kind_labels_and_parent(stubs: Stubs) -> None:
    issue = make_issue(
        "bd-1",
        labels=[project_label(), Labels.kind(KindLabel.child), persona_label()],
        parent="bd-0",
    )
    stubs.state.add(issue)
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, "bd-1")

    call = stubs.log.of("StageCatalog.resolve_stage")[0]
    assert call.kwargs["kind_labels"] == {Labels.kind(KindLabel.child)}
    assert call.kwargs["has_parent"] is True


def test_unknown_kind_label_is_passed_through_verbatim(stubs: Stubs) -> None:
    """段表が参照しない `kind` 値は**生の文字列のまま** `resolve_stage` へ渡す（B-12・B-14）。

    `Labels.kind(...)` へ再構成すると `KindLabel("bogus")` が `ValueError` で倒れ、未知値を
    `UnknownStage` に倒す stages の事前検査（SD-41）へ届かなくなる。
    """
    issue = make_issue(
        "bd-1",
        labels=[project_label(), "kind:bogus"],
        parent="bd-0",
    )
    stubs.state.add(issue)
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, "bd-1")

    call = stubs.log.of("StageCatalog.resolve_stage")[0]
    assert call.kwargs["kind_labels"] == {"kind:bogus"}


# --------------------------------------------------------------------------------------
# 主体の解決（FR-024・D16-f）
# --------------------------------------------------------------------------------------


def test_persona_label_wins(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    prepare.prepare_issue(
        ports,
        _claimed(stubs, labels=[project_label(), persona_label(PersonaKey.writer)]),
    )

    assert stubs.log.of("MemberResolver.resolve")[0].kwargs["persona_key"] is PersonaKey.writer


def test_stage_template_agent_is_used_when_no_label(stubs: Stubs) -> None:
    """`persona:` ラベルが無ければ段テンプレートの `agent:` を使う（D16-f）。"""
    stubs.stage_catalog.spec = make_stage_spec(name=StageName.child)
    stubs.settings_reader.templates = TaskTemplates(
        generation=1,
        templates={
            "child": TaskTemplate(
                description="d", expected_output="o", agent=PersonaKey.analyst, tools=None
            )
        },
    )
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, _claimed(stubs, labels=[project_label()]))

    assert stubs.log.of("MemberResolver.resolve")[0].kwargs["persona_key"] is PersonaKey.analyst
    assert stubs.log.count("SettingsReader.get_tasks") == 1


def test_fallback_persona_when_template_has_no_agent(stubs: Stubs) -> None:
    stubs.settings_reader.templates = make_templates()
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, _claimed(stubs, labels=[project_label()]))

    assert stubs.log.of("MemberResolver.resolve")[0].kwargs["persona_key"] is PersonaKey.general
    assert stubs.log.count("SettingsReader.get_tasks") == 1


# --------------------------------------------------------------------------------------
# 4 つの失敗が同一の 3 手に倒れる（SC-011・DIFF-12）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "error", "reason", "detail"),
    [
        (
            "StageCatalog.resolve_stage",
            UnknownStage("段を決められない"),
            "config_missing",
            "UnknownStage",
        ),
        (
            "MemberResolver.resolve",
            MemberNotFound("台帳に無い"),
            "config_missing",
            "MemberNotFound",
        ),
        (
            "SettingsReader.get_project",
            ProjectNotFound("PJ が無い"),
            "config_missing",
            "ProjectNotFound",
        ),
    ],
    ids=["UnknownStage", "MemberNotFound", "ProjectNotFound"],
)
def test_config_failures_fall_into_the_same_three_steps(
    stubs: Stubs, target: str, error: BaseException, reason: str, detail: str
) -> None:
    owner = {
        "StageCatalog.resolve_stage": stubs.stage_catalog.failures,
        "MemberResolver.resolve": stubs.member_resolver.failures,
        "SettingsReader.get_project": stubs.settings_reader.failures,
    }[target]
    owner.fail(target, 1, error)
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, PrepareFailure)
    assert result.disposition is IssueDisposition.handed_off
    assert stubs.log.only(*WRITES) == [
        "BoardWriter.comment",
        "BoardWriter.set_labels",
        "BoardWriter.release",
    ]
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert f"停止[{reason}]" in body
    assert detail in body


def test_missing_folders_hand_off_with_the_paths(stubs: Stubs) -> None:
    stubs.vault_reader.folder_check = FolderCheck(missing=["pj/alpha/input"], ok=False)
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, PrepareFailure)
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "停止[folder_missing]" in body
    assert "pj/alpha/input" in body


def test_folder_path_violation_also_hands_off(stubs: Stubs) -> None:
    stubs.vault_reader.failures.fail(
        "VaultReader.check_project_folders",
        1,
        VaultPathViolation("ルート外", kind="outside_root", detail="/etc"),
    )
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, PrepareFailure)
    assert result.disposition is IssueDisposition.handed_off


def test_missing_project_label_hands_off(stubs: Stubs) -> None:
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs, labels=[persona_label()]))

    assert isinstance(result, PrepareFailure)
    body: str = stubs.log.of("BoardWriter.comment")[0].kwargs["body"]
    assert "停止[config_missing]" in body
    assert stubs.log.count("StageCatalog.resolve_stage") == 0


def test_failure_before_stage_resolution_has_no_stage_or_persona(stubs: Stubs) -> None:
    """段・主体が未解決なら一覧に載せられない（FR-071a と同じ構造的理由）。"""
    stubs.stage_catalog.failures.fail(
        "StageCatalog.resolve_stage", 1, UnknownStage("段を決められない")
    )
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, PrepareFailure)
    assert result.stage is None
    assert result.persona is None


def test_failure_after_stage_resolution_carries_stage_and_persona(stubs: Stubs) -> None:
    stubs.settings_reader.failures.fail(
        "SettingsReader.get_project", 1, ProjectNotFound("PJ が無い")
    )
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, PrepareFailure)
    assert result.stage is StageName.child
    assert result.persona is PersonaKey.planner


# --------------------------------------------------------------------------------------
# 組み上がった IssueRun
# --------------------------------------------------------------------------------------


def test_issue_run_carries_the_prepared_material(stubs: Stubs) -> None:
    stubs.member_resolver.members[PersonaKey.planner] = make_member(bd_actor="ai-x")
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, _claimed(stubs))

    assert isinstance(result, IssueRun)
    assert result.issue.id == "bd-1"
    assert result.member.bd_actor == "ai-x"
    assert result.project.project_key == "alpha"
    assert result.returns == 0
    assert result.outcome is None


# --------------------------------------------------------------------------------------
# F06 — 人間の指示と回答の収集（FR-028〜FR-032・SC-013・SC-014）
# --------------------------------------------------------------------------------------


def _with_comments(stubs: Stubs, comments: list[tuple[str, str]]) -> str:
    issue_id = _claimed(stubs)
    stubs.state.comments[issue_id] = [
        make_comment(issue_id, actor, body, f"c-{index}")
        for index, (actor, body) in enumerate(comments)
    ]
    return issue_id


def test_only_human_comments_become_material(stubs: Stubs) -> None:
    """人間 2・システム 1・AI メンバ 1 のうち材料に載るのは 2 件（SC-013・FR-028・FR-029）。"""
    stubs.member_resolver.humans = {"alice", "bob"}
    issue_id = _with_comments(
        stubs,
        [
            ("alice", "こう直して"),
            ("system", "[orchestrator] 差し戻し（1/2 回目）[分類]: 理由"),
            ("ai-planner", "作業しました"),
            ("bob", "この方針で"),
        ],
    )
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, issue_id)

    assert isinstance(result, IssueRun)
    assert result.human_guidance == ("こう直して", "この方針で")


def test_is_human_is_asked_for_every_actor(stubs: Stubs) -> None:
    issue_id = _with_comments(stubs, [("alice", "a"), ("system", "b"), ("ai-planner", "c")])
    ports = make_ports(stubs)

    prepare.prepare_issue(ports, issue_id)

    asked = [call.kwargs["actor"] for call in stubs.log.of("MemberResolver.is_human")]
    assert asked == ["alice", "system", "ai-planner"]


def test_closed_question_comments_are_collected(stubs: Stubs) -> None:
    """未回答の質問が無く、閉じた質問があればその本文も集める（FR-030）。"""
    stubs.member_resolver.humans = {"alice"}
    question = make_issue(
        "bd-9", labels=[Labels.kind(KindLabel.question)], status=IssueStatus.closed
    )
    stubs.state.add(question)
    stubs.state.comments["bd-9"] = [make_comment("bd-9", "alice", "答えはこれ", "q-1")]
    issue = make_issue(
        "bd-1",
        labels=[project_label(), Labels.kind(KindLabel.child), persona_label()],
    ).model_copy(
        update={
            "dependencies": [
                IssueRef(
                    id="bd-9",
                    title="[要判断] 決めて",
                    status=IssueStatus.closed,
                    labels=[Labels.kind(KindLabel.question)],
                    dependency_type="blocks",
                )
            ]
        }
    )
    stubs.state.add(issue)
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, "bd-1")

    assert isinstance(result, IssueRun)
    assert result.human_guidance == ("答えはこれ",)


def test_open_question_stops_the_closed_question_walk(stubs: Stubs) -> None:
    stubs.member_resolver.humans = {"alice"}
    stubs.state.questions["bd-1"] = make_issue("bd-8", labels=[Labels.kind(KindLabel.question)])
    issue_id = _with_comments(stubs, [("alice", "本文")])
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, issue_id)

    assert isinstance(result, IssueRun)
    assert result.human_guidance == ("本文",)
    assert stubs.log.count("BoardReader.comments") == 1


def test_unreadable_comments_stop_the_cycle_before_the_executor(stubs: Stubs) -> None:
    """コメントが読めなければ**指示なしで先へ進まない**（FR-031・SC-014）。"""
    stubs.board_reader.failures.fail("BoardReader.comments", 1)
    ports = make_ports(stubs)

    with pytest.raises(BoardUnavailable):
        prepare.prepare_issue(ports, _claimed(stubs))

    assert stubs.log.count("CrewExecutor.run") == 0


def test_comments_are_read_once_per_issue(stubs: Stubs) -> None:
    """kickoff の最中に付いたコメントは読み直さない（FR-032。材料はこの 1 回の読取から作る）。"""
    stubs.member_resolver.humans = {"alice"}
    issue_id = _with_comments(stubs, [("alice", "最初の指示")])
    ports = make_ports(stubs)

    result = prepare.prepare_issue(ports, issue_id)
    stubs.state.comments[issue_id].append(make_comment(issue_id, "alice", "あとから", "c-late"))

    assert isinstance(result, IssueRun)
    assert result.human_guidance == ("最初の指示",)
