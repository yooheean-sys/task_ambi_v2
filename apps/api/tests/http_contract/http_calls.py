"""要求の recipe（data-model §2）。**データだけ**を持ち、振る舞いを持たない（憲章 §7-2）。

各行は「どの土台（`fixture`）で・どの URL を・どう叩くと・どの宣言 status が返るか」を書いた
値である。実際の呼び出しはテスト側が行う。

- `template` は**正本のパステンプレート**（`/settings/projects/{project_key}`）。
  宣言と突き合わせる鍵。
- `url` は実値を埋めたもの。
- `effect` は settings の書込だけが持つ（`"immediate"` ／ `"next-kickoff"`。P5D-016・D16）。

**不変条件**: `(method, template, status)` が recipe 集合の中で一意
（module 読み込み時に自己検査する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from http_asserts import EFFECT_IMMEDIATE, EFFECT_NEXT_KICKOFF

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ALL_CALLS",
    "call_of",
    "CONFLICT_REPRESENTATIVES",
    "EFFECT_IMMEDIATE",
    "EFFECT_NEXT_KICKOFF",
    "HAPPY_CALLS",
    "HAPPY_OPERATION_IDS",
    "HttpCall",
]


@dataclass(frozen=True, slots=True)
class HttpCall:
    """1 回の要求の作り方。"""

    operation_id: str
    """`pytest` の `id` に使う（`deleteProject`・`getProject_404` のように status で分ける）。"""

    method: str
    template: str
    """**正本のパステンプレート**（宣言と突合する鍵）。"""

    url: str
    """実際に叩く URL（`{project_key}` を実値へ埋めたもの）。"""

    body: object | None
    """送る JSON。GET・DELETE は `None`。"""

    fixture: str
    """使う土台の名前（`conftest.py` が配る fixture 名と 1 対 1）。"""

    status: str
    """期待する宣言 status。"""

    effect: str | None = None
    """settings の書込のみ。`"immediate"` ／ `"next-kickoff"`。"""

    @property
    def pair(self) -> tuple[str, str, str]:
        """(操作, status) 対の鍵。"""
        return (self.method, self.template, self.status)


def _assert_unique(calls: Sequence[HttpCall]) -> None:
    """`(method, template, status)` が一意であることを確かめる（data-model §2 の不変条件）。

    Args:
        calls: 検査する recipe の並び。

    Raises:
        ValueError: 同じ対が 2 度現れた。
    """
    seen: dict[tuple[str, str, str], str] = {}
    duplicated: list[str] = []
    for call in calls:
        previous = seen.get(call.pair)
        if previous is not None:
            duplicated.append(f"{call.pair} は {previous} と {call.operation_id} で重複")
            continue
        seen[call.pair] = call.operation_id
    if duplicated:
        message = "recipe の (method, template, status) が一意でない:\n" + "\n".join(duplicated)
        raise ValueError(message)


# --------------------------------------------------------------------------------------
# 提供実装別の recipe（各フェーズで追記する）
# --------------------------------------------------------------------------------------

HEALTH_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="getHealth",
        method="GET",
        template="/health",
        url="/health",
        body=None,
        fixture="health_topology_app",
        status="200",
    ),
)

ORCHESTRATOR_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="createKickoff",
        method="POST",
        template="/kickoffs",
        url="/kickoffs",
        body=None,
        fixture="kickoffs_app",
        status="202",
    ),
    HttpCall(
        operation_id="createKickoff_409",
        method="POST",
        template="/kickoffs",
        url="/kickoffs",
        body=None,
        fixture="gated_kickoffs_app",
        status="409",
    ),
    HttpCall(
        operation_id="getCurrentKickoff",
        method="GET",
        template="/kickoffs/current",
        url="/kickoffs/current",
        body=None,
        fixture="kickoffs_app",
        status="200",
    ),
    HttpCall(
        operation_id="getKickoff",
        method="GET",
        template="/kickoffs/{kickoff_id}",
        url="/kickoffs/{kickoff_id}",
        body=None,
        fixture="kickoffs_app",
        status="200",
    ),
    HttpCall(
        operation_id="getKickoff_404",
        method="GET",
        template="/kickoffs/{kickoff_id}",
        url="/kickoffs/居ない",
        body=None,
        fixture="kickoffs_app",
        status="404",
    ),
)

TRIGGER_SPEC: dict[str, object] = {
    "name": "http-contract",
    "description": "層 5 の HTTP 契約テストが作る",
    "type": "interval",
    "spec": "300",
    "condition": {"labels": ["role:executor"], "project_key": None},
    "enabled": True,
}
"""`TriggerSpec` の最小形（正本の required 5 欄をすべて埋める）。"""

FILE_TRIGGER_SPEC: dict[str, object] = {**TRIGGER_SPEC, "type": "file", "spec": "."}
"""`file` 種別（`spec` は `DOWNLOADS_DIR` 相対の監視ディレクトリ）。"""

TRIGGER_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="listTriggers",
        method="GET",
        template="/triggers",
        url="/triggers",
        body=None,
        fixture="trigger_app",
        status="200",
    ),
    HttpCall(
        operation_id="createTrigger",
        method="POST",
        template="/triggers",
        url="/triggers",
        body=TRIGGER_SPEC,
        fixture="trigger_app",
        status="201",
    ),
    HttpCall(
        operation_id="createTrigger_422",
        method="POST",
        template="/triggers",
        url="/triggers",
        body={**TRIGGER_SPEC, "spec": "0"},
        fixture="trigger_app",
        status="422",
    ),
    HttpCall(
        operation_id="getTrigger",
        method="GET",
        template="/triggers/{trigger_id}",
        url="/triggers/{trigger_id}",
        body=None,
        fixture="trigger_app",
        status="200",
    ),
    HttpCall(
        operation_id="getTrigger_404",
        method="GET",
        template="/triggers/{trigger_id}",
        url="/triggers/居ない",
        body=None,
        fixture="trigger_app",
        status="404",
    ),
    HttpCall(
        operation_id="updateTrigger",
        method="PUT",
        template="/triggers/{trigger_id}",
        url="/triggers/{trigger_id}",
        body=TRIGGER_SPEC,
        fixture="trigger_app",
        status="200",
    ),
    HttpCall(
        operation_id="updateTrigger_404",
        method="PUT",
        template="/triggers/{trigger_id}",
        url="/triggers/居ない",
        body=TRIGGER_SPEC,
        fixture="trigger_app",
        status="404",
    ),
    HttpCall(
        operation_id="updateTrigger_422",
        method="PUT",
        template="/triggers/{trigger_id}",
        url="/triggers/{default_trigger_id}",
        body={**TRIGGER_SPEC, "name": "別名にする"},
        fixture="trigger_app",
        status="422",
    ),
    HttpCall(
        operation_id="toggleTrigger",
        method="PATCH",
        template="/triggers/{trigger_id}",
        url="/triggers/{trigger_id}",
        body={"enabled": False},
        fixture="trigger_app",
        status="200",
    ),
    HttpCall(
        operation_id="toggleTrigger_404",
        method="PATCH",
        template="/triggers/{trigger_id}",
        url="/triggers/居ない",
        body={"enabled": False},
        fixture="trigger_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteTrigger",
        method="DELETE",
        template="/triggers/{trigger_id}",
        url="/triggers/{trigger_id}",
        body=None,
        fixture="trigger_app",
        status="204",
    ),
    HttpCall(
        operation_id="deleteTrigger_404",
        method="DELETE",
        template="/triggers/{trigger_id}",
        url="/triggers/居ない",
        body=None,
        fixture="trigger_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteTrigger_422",
        method="DELETE",
        template="/triggers/{trigger_id}",
        url="/triggers/{default_trigger_id}",
        body=None,
        fixture="trigger_app",
        status="422",
    ),
    HttpCall(
        operation_id="runTrigger",
        method="POST",
        template="/triggers/{trigger_id}/run",
        url="/triggers/{trigger_id}/run",
        body=None,
        fixture="trigger_app",
        status="202",
    ),
    HttpCall(
        operation_id="runTrigger_404",
        method="POST",
        template="/triggers/{trigger_id}/run",
        url="/triggers/居ない/run",
        body=None,
        fixture="trigger_app",
        status="404",
    ),
    HttpCall(
        operation_id="listHistory",
        method="GET",
        template="/history",
        url="/history",
        body=None,
        fixture="trigger_app",
        status="200",
    ),
    HttpCall(
        operation_id="getWatcher",
        method="GET",
        template="/watcher",
        url="/watcher",
        body=None,
        fixture="trigger_app",
        status="200",
    ),
    HttpCall(
        operation_id="postFileHook",
        method="POST",
        template="/hooks/file",
        url="/hooks/file",
        body={"path": "{dropped_path}", "source": "hook"},
        fixture="trigger_app",
        status="202",
    ),
    HttpCall(
        operation_id="postFileHook_422",
        method="POST",
        template="/hooks/file",
        url="/hooks/file",
        body={"path": "{outside_path}", "source": "hook"},
        fixture="trigger_app",
        status="422",
    ),
)

SETTINGS_READ_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="listAgents",
        method="GET",
        template="/settings/agents",
        url="/settings/agents",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getGlobalAgent",
        method="GET",
        template="/settings/agents/global/{persona_key}",
        url="/settings/agents/global/programmer",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getAgent",
        method="GET",
        template="/settings/agents/{project_key}/{persona_key}",
        url="/settings/agents/pj/programmer",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="listCommands",
        method="GET",
        template="/settings/commands",
        url="/settings/commands",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getCommand",
        method="GET",
        template="/settings/commands/{name}",
        url="/settings/commands/run_tests",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getHooks",
        method="GET",
        template="/settings/hooks/{project_key}",
        url="/settings/hooks/pj",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getHumans",
        method="GET",
        template="/settings/humans",
        url="/settings/humans",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getLimits",
        method="GET",
        template="/settings/limits",
        url="/settings/limits",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="listLlmProfiles",
        method="GET",
        template="/settings/llm-profiles",
        url="/settings/llm-profiles",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getLlmProfile",
        method="GET",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/default",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getPolicy",
        method="GET",
        template="/settings/policies/{project_key}",
        url="/settings/policies/pj",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="listProjects",
        method="GET",
        template="/settings/projects",
        url="/settings/projects",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getProject",
        method="GET",
        template="/settings/projects/{project_key}",
        url="/settings/projects/pj",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="listProjectRoles",
        method="GET",
        template="/settings/projects/{project_key}/roles",
        url="/settings/projects/pj/roles",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getProjectRules",
        method="GET",
        template="/settings/rules/projects/{project_key}",
        url="/settings/rules/projects/pj",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getGlobalTasks",
        method="GET",
        template="/settings/tasks/global",
        url="/settings/tasks/global",
        body=None,
        fixture="settings_app",
        status="200",
    ),
    HttpCall(
        operation_id="getProjectTasks",
        method="GET",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/pj",
        body=None,
        fixture="settings_app",
        status="200",
    ),
)

AGENT_SPEC: dict[str, object] = {
    "role": "実装担当",
    "goal": "受け取った課題を実装する",
    "backstory": "あなたは実装担当です。",
}
"""`AgentSpec`（global は role・goal・backstory 必須。未知キー拒否＝ D16-d）。"""

PROJECT_AGENT_SPEC: dict[str, object] = {}
"""PJ 側のメンバは全欄任意（空 object ＝ global 既定のまま参加）。"""

PROJECT_SPEC: dict[str, object] = {
    "project_key": "pj",
    "name": "デモ（更新後）",
    "folders": {
        "pj_input_dir": "pj",
        "role_dirs": {"programmer": "pj/roles/programmer"},
        "outcome_dir": "pj/outcome",
    },
}
"""`ProjectSpec`（`role_dirs` はメンバを作るペルソナ全員分が必須＝ P5D-018）。"""

NEW_PROJECT_SPEC: dict[str, object] = {
    "project_key": "newpj",
    "name": "新規",
    "folders": {
        "pj_input_dir": "newpj",
        "role_dirs": {"programmer": "newpj/roles/programmer"},
        "outcome_dir": "newpj/outcome",
    },
}

LLM_PROFILE_SPEC: dict[str, object] = {
    "name": "spare",
    "provider_kind": "openai_compatible",
    "base_url": "http://127.0.0.1:8080/v1",
    "model_id": "spare-model-2",
    "context_window": 16384,
    "api_key_ref": "DEMO_API_KEY_REF",
    "is_default": False,
}

NEW_LLM_PROFILE_SPEC: dict[str, object] = {
    "name": "added",
    "provider_kind": "openai_compatible",
    "base_url": "http://127.0.0.1:8080/v1",
    "model_id": "added-model",
    "context_window": 8192,
    "api_key_ref": "DEMO_API_KEY_REF",
    "is_default": False,
}

LIMIT_SPEC: dict[str, object] = {"max_requests_per_kickoff": 120, "max_cost_per_kickoff": 12.0}

POLICY_SPEC: dict[str, object] = {
    "rows": [{"ptype": "p", "fields": ["member:pj.programmer", "tool:read_issue", "execute"]}]
}
"""`cmd:` 行を持たない（コマンド定義が空の変種と対で使う＝ V-25 を外す）。"""

COMMAND_DEF: dict[str, object] = {"name": "run_tests", "argv": ["ls", "-l"]}

NEW_COMMAND_DEF: dict[str, object] = {"name": "added_cmd", "argv": ["ls"]}
"""参照されない定義（V-25 に触れて 422 になる＝行き止まりの表明）。"""

_TASK_BODY = (
    "担当 issue: {issue_id}\nタイトル: {title}\n\n{description}\n\n"
    "人間からの指示: {human_guidance}\n差し戻し理由: {feedback}\n\n受け入れ条件:\n{acceptance}\n"
)
_GUIDED_TASK_BODY = _TASK_BODY + "\n担当一覧:\n{persona_guide}\n"

GLOBAL_TASKS: dict[str, object] = {
    "program": {"description": _GUIDED_TASK_BODY, "expected_output": "分解の一覧"},
    "project": {"description": _GUIDED_TASK_BODY, "expected_output": "分解の一覧"},
    "subrequest": {"description": _GUIDED_TASK_BODY, "expected_output": "分解の一覧"},
    "child": {"description": _TASK_BODY, "expected_output": "成果物の要約"},
    "plain": {"description": _TASK_BODY, "expected_output": "成果物の要約"},
    "review_normal": {"description": _TASK_BODY, "expected_output": "レビュー結果"},
    "review_final": {"description": _TASK_BODY, "expected_output": "レビュー結果"},
}
"""global は 7 キー必須（D16-a）。段別必須変数（`persona_guide`・`acceptance`）を満たす。"""

PROJECT_TASKS: dict[str, object] = {
    "plain": {"description": _TASK_BODY, "expected_output": "成果物の要約（PJ 側）"}
}
"""PJ 側は任意のキーのみ。"""

SETTINGS_WRITE_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="putGlobalAgent",
        method="PUT",
        template="/settings/agents/global/{persona_key}",
        url="/settings/agents/global/programmer",
        body=AGENT_SPEC,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putAgent",
        method="PUT",
        template="/settings/agents/{project_key}/{persona_key}",
        url="/settings/agents/pj/programmer",
        body=PROJECT_AGENT_SPEC,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="updateCommand",
        method="PUT",
        template="/settings/commands/{name}",
        url="/settings/commands/run_tests",
        body=COMMAND_DEF,
        fixture="settings_app",
        status="200",
        effect=EFFECT_IMMEDIATE,
    ),
    HttpCall(
        operation_id="putHooks",
        method="PUT",
        template="/settings/hooks/{project_key}",
        url="/settings/hooks/pj",
        body={"hooks": []},
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putHumans",
        method="PUT",
        template="/settings/humans",
        url="/settings/humans",
        body={"human_actors": ["taro"]},
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putGlobalLimits",
        method="PUT",
        template="/settings/limits",
        url="/settings/limits",
        body=LIMIT_SPEC,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putProjectLimits",
        method="PUT",
        template="/settings/limits/{project_key}",
        url="/settings/limits/pj",
        body=LIMIT_SPEC,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="updateLlmProfile",
        method="PUT",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/spare",
        body=LLM_PROFILE_SPEC,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putPolicy",
        method="PUT",
        template="/settings/policies/{project_key}",
        url="/settings/policies/pj",
        body=POLICY_SPEC,
        fixture="settings_app_without_commands",
        status="200",
        effect=EFFECT_IMMEDIATE,
    ),
    HttpCall(
        operation_id="updateProject",
        method="PUT",
        template="/settings/projects/{project_key}",
        url="/settings/projects/pj",
        body=PROJECT_SPEC,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putProjectRules",
        method="PUT",
        template="/settings/rules/projects/{project_key}",
        url="/settings/rules/projects/pj",
        body={"body": "# 更新後の規約\n"},
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putGlobalTasks",
        method="PUT",
        template="/settings/tasks/global",
        url="/settings/tasks/global",
        body=GLOBAL_TASKS,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="putProjectTasks",
        method="PUT",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/pj",
        body=PROJECT_TASKS,
        fixture="settings_app",
        status="200",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="createProject",
        method="POST",
        template="/settings/projects",
        url="/settings/projects",
        body=NEW_PROJECT_SPEC,
        fixture="settings_app",
        status="201",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="createLlmProfile",
        method="POST",
        template="/settings/llm-profiles",
        url="/settings/llm-profiles",
        body=NEW_LLM_PROFILE_SPEC,
        fixture="settings_app",
        status="201",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="deleteProject",
        method="DELETE",
        template="/settings/projects/{project_key}",
        url="/settings/projects/keep",
        body=None,
        fixture="settings_app_two_projects",
        status="204",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="deleteProjectTasks",
        method="DELETE",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/pj",
        body=None,
        fixture="settings_app",
        status="204",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="deleteLlmProfile",
        method="DELETE",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/spare",
        body=None,
        fixture="settings_app",
        status="204",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="deleteProjectLimits",
        method="DELETE",
        template="/settings/limits/{project_key}",
        url="/settings/limits/pj",
        body=None,
        fixture="settings_app",
        status="204",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="createCommand_422",
        method="POST",
        template="/settings/commands",
        url="/settings/commands",
        body=NEW_COMMAND_DEF,
        fixture="settings_app",
        status="422",
        effect=EFFECT_IMMEDIATE,
    ),
    HttpCall(
        operation_id="deleteAgent_422",
        method="DELETE",
        template="/settings/agents/{project_key}/{persona_key}",
        url="/settings/agents/pj/programmer",
        body=None,
        fixture="settings_app",
        status="422",
        effect=EFFECT_NEXT_KICKOFF,
    ),
    HttpCall(
        operation_id="deleteCommand_422",
        method="DELETE",
        template="/settings/commands/{name}",
        url="/settings/commands/run_tests",
        body=None,
        fixture="settings_app",
        status="422",
        effect=EFFECT_IMMEDIATE,
    ),
)

MISSING_KEY = "居ない"
"""登録されていないキー（404 を踏む）。⚠ 未知の `persona_key` は path の enum に当たって
FastAPI 内蔵の 422 になるので**404 の踏み方に使わない**（FR-026）。"""

MISSING_PROJECT_SPEC: dict[str, object] = {**NEW_PROJECT_SPEC, "project_key": MISSING_KEY}

DUPLICATE_PROJECT_SPEC: dict[str, object] = {**NEW_PROJECT_SPEC, "project_key": "pj"}
"""既存キーでの登録（`key_rule`）。"""

PROJECT_SPEC_WITHOUT_ROLE_DIRS: dict[str, object] = {
    **PROJECT_SPEC,
    "folders": {"pj_input_dir": "pj", "role_dirs": {}, "outcome_dir": "pj/outcome"},
}
"""メンバの役割フォルダが無い（`forward`）。"""

ORPHAN_POLICY_SPEC: dict[str, object] = {
    "rows": [
        {"ptype": "p", "fields": ["member:pj.居ない", "tool:read_issue", "execute"]},
    ]
}
"""台帳に居ない主体（`orphan`）。"""

ZERO_LIMIT_SPEC: dict[str, object] = {
    "max_requests_per_kickoff": 1,
    "max_cost_per_kickoff": 0,
}
"""上限が 0（`forward`）。"""

AGENT_SPEC_WITH_UNKNOWN_LLM: dict[str, object] = {**AGENT_SPEC, "llm": "居ないプロファイル"}

NARROW_LLM_PROFILE_SPEC: dict[str, object] = {**LLM_PROFILE_SPEC, "context_window": 1}
"""閾値 T が 0 以下になる（`llm_ref`）。"""

GLOBAL_TASKS_WITHOUT_ACCEPTANCE: dict[str, object] = {
    key: {"description": _GUIDED_TASK_BODY.replace("{acceptance}", "—"), "expected_output": "要約"}
    if key in ("program", "project", "subrequest")
    else {"description": _TASK_BODY.replace("{acceptance}", "—"), "expected_output": "要約"}
    for key in GLOBAL_TASKS
}
"""段別の必須変数 `{acceptance}` を落とした（`var_required`）。"""

PROJECT_TASKS_WITHOUT_ACCEPTANCE: dict[str, object] = {
    "plain": {
        "description": _TASK_BODY.replace("{acceptance}", "—"),
        "expected_output": "要約",
    }
}

SETTINGS_NOT_FOUND_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="getGlobalAgent_404",
        method="GET",
        template="/settings/agents/global/{persona_key}",
        url="/settings/agents/global/analyst",
        body=None,
        fixture="settings_app_sparse_global_agents",
        status="404",
    ),
    HttpCall(
        operation_id="getAgent_404",
        method="GET",
        template="/settings/agents/{project_key}/{persona_key}",
        url="/settings/agents/居ない/programmer",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteAgent_404",
        method="DELETE",
        template="/settings/agents/{project_key}/{persona_key}",
        url="/settings/agents/居ない/programmer",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getCommand_404",
        method="GET",
        template="/settings/commands/{name}",
        url="/settings/commands/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="updateCommand_404",
        method="PUT",
        template="/settings/commands/{name}",
        url="/settings/commands/居ない",
        body={"name": "居ない", "argv": ["ls"]},
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteCommand_404",
        method="DELETE",
        template="/settings/commands/{name}",
        url="/settings/commands/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getHooks_404",
        method="GET",
        template="/settings/hooks/{project_key}",
        url="/settings/hooks/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="putHooks_404",
        method="PUT",
        template="/settings/hooks/{project_key}",
        url="/settings/hooks/居ない",
        body={"hooks": []},
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteProjectLimits_404",
        method="DELETE",
        template="/settings/limits/{project_key}",
        url="/settings/limits/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="putProjectLimits_404",
        method="PUT",
        template="/settings/limits/{project_key}",
        url="/settings/limits/居ない",
        body=LIMIT_SPEC,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getLlmProfile_404",
        method="GET",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="updateLlmProfile_404",
        method="PUT",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/居ない",
        body=LLM_PROFILE_SPEC,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteLlmProfile_404",
        method="DELETE",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getPolicy_404",
        method="GET",
        template="/settings/policies/{project_key}",
        url="/settings/policies/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="putPolicy_404",
        method="PUT",
        template="/settings/policies/{project_key}",
        url="/settings/policies/居ない",
        body=POLICY_SPEC,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getProject_404",
        method="GET",
        template="/settings/projects/{project_key}",
        url="/settings/projects/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="listProjectRoles_404",
        method="GET",
        template="/settings/projects/{project_key}/roles",
        url="/settings/projects/居ない/roles",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="updateProject_404",
        method="PUT",
        template="/settings/projects/{project_key}",
        url="/settings/projects/居ない",
        body=MISSING_PROJECT_SPEC,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteProject_404",
        method="DELETE",
        template="/settings/projects/{project_key}",
        url="/settings/projects/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getProjectRules_404",
        method="GET",
        template="/settings/rules/projects/{project_key}",
        url="/settings/rules/projects/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="putProjectRules_404",
        method="PUT",
        template="/settings/rules/projects/{project_key}",
        url="/settings/rules/projects/居ない",
        body={"body": "# 規約\n"},
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="getProjectTasks_404",
        method="GET",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="putProjectTasks_404",
        method="PUT",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/居ない",
        body=PROJECT_TASKS,
        fixture="settings_app",
        status="404",
    ),
    HttpCall(
        operation_id="deleteProjectTasks_404",
        method="DELETE",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/居ない",
        body=None,
        fixture="settings_app",
        status="404",
    ),
)

SETTINGS_CONFLICT_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="deleteProject_409",
        method="DELETE",
        template="/settings/projects/{project_key}",
        url="/settings/projects/keep",
        body=None,
        fixture="settings_app_with_open_issues",
        status="409",
    ),
)

SETTINGS_REJECTED_CALLS: tuple[HttpCall, ...] = (
    HttpCall(
        operation_id="createProject_422",
        method="POST",
        template="/settings/projects",
        url="/settings/projects",
        body=DUPLICATE_PROJECT_SPEC,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="updateProject_422",
        method="PUT",
        template="/settings/projects/{project_key}",
        url="/settings/projects/pj",
        body=PROJECT_SPEC_WITHOUT_ROLE_DIRS,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putHumans_422",
        method="PUT",
        template="/settings/humans",
        url="/settings/humans",
        body={"human_actors": ["pj.programmer"]},
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putPolicy_422",
        method="PUT",
        template="/settings/policies/{project_key}",
        url="/settings/policies/pj",
        body=ORPHAN_POLICY_SPEC,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putGlobalLimits_422",
        method="PUT",
        template="/settings/limits",
        url="/settings/limits",
        body=ZERO_LIMIT_SPEC,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putProjectLimits_422",
        method="PUT",
        template="/settings/limits/{project_key}",
        url="/settings/limits/pj",
        body=ZERO_LIMIT_SPEC,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putGlobalAgent_422",
        method="PUT",
        template="/settings/agents/global/{persona_key}",
        url="/settings/agents/global/programmer",
        body=AGENT_SPEC_WITH_UNKNOWN_LLM,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putAgent_422",
        method="PUT",
        template="/settings/agents/{project_key}/{persona_key}",
        url="/settings/agents/pj/programmer",
        body={"llm": "居ないプロファイル"},
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="createLlmProfile_422",
        method="POST",
        template="/settings/llm-profiles",
        url="/settings/llm-profiles",
        body=NARROW_LLM_PROFILE_SPEC,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="updateLlmProfile_422",
        method="PUT",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/spare",
        body=NARROW_LLM_PROFILE_SPEC,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="deleteLlmProfile_422",
        method="DELETE",
        template="/settings/llm-profiles/{name}",
        url="/settings/llm-profiles/default",
        body=None,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putGlobalTasks_422",
        method="PUT",
        template="/settings/tasks/global",
        url="/settings/tasks/global",
        body=GLOBAL_TASKS_WITHOUT_ACCEPTANCE,
        fixture="settings_app",
        status="422",
    ),
    HttpCall(
        operation_id="putProjectTasks_422",
        method="PUT",
        template="/settings/tasks/{project_key}",
        url="/settings/tasks/pj",
        body=PROJECT_TASKS_WITHOUT_ACCEPTANCE,
        fixture="settings_app",
        status="422",
    ),
)

SETTINGS_ERROR_CALLS: tuple[HttpCall, ...] = (
    SETTINGS_NOT_FOUND_CALLS + SETTINGS_CONFLICT_CALLS + SETTINGS_REJECTED_CALLS
)

ALL_CALLS: tuple[HttpCall, ...] = (
    HEALTH_CALLS
    + ORCHESTRATOR_CALLS
    + TRIGGER_CALLS
    + SETTINGS_READ_CALLS
    + SETTINGS_WRITE_CALLS
    + SETTINGS_ERROR_CALLS
)

HAPPY_OPERATION_IDS: tuple[str, ...] = (
    # apps/api（1）
    "getHealth",
    # orchestrator（3）
    "createKickoff",
    "getCurrentKickoff",
    "getKickoff_404",
    # trigger（10）—— 実値を実行時に決めずに済む代表を選ぶ
    "listTriggers",
    "createTrigger",
    "getTrigger_404",
    "updateTrigger_404",
    "toggleTrigger_404",
    "deleteTrigger_404",
    "runTrigger_404",
    "listHistory",
    "getWatcher",
    "postFileHook_422",
    # settings 読取（17。D26 の listProjectRoles を含む）
    "listAgents",
    "getGlobalAgent",
    "getAgent",
    "listCommands",
    "getCommand",
    "getHooks",
    "getHumans",
    "getLimits",
    "listLlmProfiles",
    "getLlmProfile",
    "getPolicy",
    "listProjects",
    "getProject",
    "listProjectRoles",
    "getProjectRules",
    "getGlobalTasks",
    "getProjectTasks",
    # settings 書込（22。行き止まり 3 は代表が 422 になる）
    "putGlobalAgent",
    "putAgent",
    "updateCommand",
    "putHooks",
    "putHumans",
    "putGlobalLimits",
    "putProjectLimits",
    "updateLlmProfile",
    "putPolicy",
    "updateProject",
    "putProjectRules",
    "putGlobalTasks",
    "putProjectTasks",
    "createProject",
    "createLlmProfile",
    "deleteProject",
    "deleteProjectTasks",
    "deleteLlmProfile",
    "deleteProjectLimits",
    "createCommand_422",
    "deleteAgent_422",
    "deleteCommand_422",
)
"""53 操作それぞれの**代表 1 本**（status は問わない）。相関 ID の往復（US5）が使う。

`createCommand`・`deleteAgent`・`deleteCommand` は代表が **422** になる（到達可能な成功系が
無いため）。`listHistory`・`getCurrentKickoff`・`getHealth` は 200。
"""

DROPPED_PATH_PLACEHOLDER = "{dropped_path}"
OUTSIDE_PATH_PLACEHOLDER = "{outside_path}"
"""投下ファイルのパスは実行時にしか決まらない（テスト側が一時ディレクトリで埋める）。"""


def call_of(operation_id: str) -> HttpCall:
    """`operation_id` から recipe を 1 件引く（データ引きのアクセサ）。

    Args:
        operation_id: recipe の `operation_id`（status で分けた `getProject_404` を含む）。

    Returns:
        該当する recipe。

    Raises:
        KeyError: 該当が無い。
    """
    for call in ALL_CALLS:
        if call.operation_id == operation_id:
            return call
    message = f"recipe に無い operation_id: {operation_id}"
    raise KeyError(message)


_assert_unique(ALL_CALLS)

HAPPY_CALLS: tuple[HttpCall, ...] = tuple(call_of(name) for name in HAPPY_OPERATION_IDS)

CONFLICT_OPERATION_IDS: tuple[str, ...] = (
    "deleteProject_409",
    "createKickoff_409",
)
"""**409 を踏む代表**（US5 AC4・裁定 J-1）。

`HAPPY_OPERATION_IDS` は 1 操作 1 代表なので、`deleteProject`（204）・`createKickoff`（202）は
どちらも**成功系**が代表になっている。その結果、代表集合の status 分布に **409 が 1 件も無い**。
US5 AC4 が主張する「エラー応答（404・409・422）にも載る」の 409 をここで補う。

**`HAPPY_OPERATION_IDS` へ直接足してはならない** —— `deleteProject`・`createKickoff` が 2 度
現れ、`test_the_representative_set_covers_every_operation_exactly_once`（1 操作 1 代表）が落ちる。
対の所有（`http_op` の宣言）は各提供フェーズのままで、この集合は**何も宣言しない**。
"""

CONFLICT_REPRESENTATIVES: tuple[HttpCall, ...] = tuple(
    call_of(name) for name in CONFLICT_OPERATION_IDS
)
"""`CONFLICT_OPERATION_IDS` の recipe（相関 ID の 409 往復と status 網羅の判定が読む）。"""
