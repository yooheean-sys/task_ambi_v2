"""(d3) 写像・merge・台帳導出・世代の採番（生の読取結果 → `SettingsSnapshot`）。

**検査を通ってから写像する**（R-03）。merge・LLM 解決・台帳導出はいずれも `resolve.py` の規則を
**生の mapping に対して**呼ぶので、**validate が見た値と `resolve()` が返す値が構造として一致
する**（R-01）。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from common.interfaces.settings import (
    HooksConfig,
    Member,
    Project,
    ProjectFolders,
    ProjectRepos,
    Rules,
)
from common.kernel.enums import PersonaKey
from common.kernel.log import LoggerFactory
from settings.exceptions import SettingsAreaError
from settings.models import (
    AgentSpecRecord,
    AreaAData,
    CommandDef,
    LimitsRecord,
    LlmProfileRecord,
    PolicyEnforcer,
    PolicySet,
    ProjectYaml,
    Registry,
    SettingsSnapshot,
    TaskTemplateRecord,
)
from settings.repository import AreaAReader
from settings.service.loader.enforcer import build_enforcer
from settings.service.resolve import (
    bd_actor_of,
    casbin_subject_of,
    human_actor_names,
    member_id_of,
    merge_agent_fields,
    normalize_mapping,
    resolve_limits_global,
    resolve_limits_overrides,
    resolve_llm_name,
    resolve_profile,
)
from settings.service.validate import build_hook_refs

if TYPE_CHECKING:
    from collections.abc import Mapping

    from common.interfaces.settings import LLMProfile

INITIAL_GENERATION = 1
"""スナップショットの初期世代。**`SettingsService` ごとに 1 から始める**（SD-28）。"""

_logger = LoggerFactory.get_logger("settings.repository")


def build_snapshot(
    data: AreaAData,
    reader: AreaAReader,
    *,
    context_reserve_tokens: int,
    default_requests: int,
    default_cost: float,
    system_actor: str,
) -> SettingsSnapshot:
    """検査を通った生の構造を不変のスナップショットへ畳む。

    Args:
        data: (d2) が読み切った生の構造。
        reader: 領域 A の読取アダプタ（判定器へ渡すパスの解決に使う）。
        context_reserve_tokens: `threshold_t` の予備トークン（CFG-37）。
        default_requests: 上限の出荷時既定（CFG-39）。
        default_cost: 同上。
        system_actor: システム自身の行為者名（CFG-40）。

    Returns:
        世代 1 の不変スナップショット。

    Raises:
        SettingsAreaError: 検査規則の受け皿が無い形の破れで写像に失敗した（**安全網**。R-03 の
            前提「写像はそこで初めて落ちることは無い」が破れたときに、生の `ValidationError`
            を呼び手へ漏らさないための翻訳である）。
    """
    try:
        return _assemble(
            data,
            reader,
            context_reserve_tokens=context_reserve_tokens,
            default_requests=default_requests,
            default_cost=default_cost,
            system_actor=system_actor,
        )
    except ValidationError as exc:
        msg = f"領域 A を写像できない（{reader.root}）: {exc}"
        raise SettingsAreaError(msg, [msg]) from exc


def _assemble(
    data: AreaAData,
    reader: AreaAReader,
    *,
    context_reserve_tokens: int,
    default_requests: int,
    default_cost: float,
    system_actor: str,
) -> SettingsSnapshot:
    profiles = _llm_profiles(data, context_reserve_tokens)
    agents_global = _agent_records(data.agents_global)
    projects: dict[str, Project] = {}
    agents: dict[str, Mapping[PersonaKey, AgentSpecRecord]] = {}
    members: dict[str, Mapping[PersonaKey, Member]] = {}
    tasks: dict[str, Mapping[str, TaskTemplateRecord]] = {}
    hooks: dict[str, HooksConfig] = {}
    rules: dict[str, Rules] = {}
    policies: dict[str, PolicySet] = {}
    enforcers: dict[str, PolicyEnforcer] = {}
    bd_actors: dict[str, str] = {}
    subjects: dict[str, str] = {}

    for key in sorted(data.projects_raw):
        projects[key] = _to_project(key, data.projects_raw[key])
        agents[key] = _agent_records(data.agents_raw.get(key, {}))
        resolved = _members_of(key, data, profiles)
        members[key] = MappingProxyType(resolved)
        tasks[key] = _task_records(data.tasks_raw.get(key) or {})
        hooks[key] = HooksConfig(hooks=build_hook_refs(data.hooks_raw.get(key)))
        rules[key] = Rules(project_rules=data.rules_raw.get(key) or "")
        policies[key] = _to_policy_set(key, data.policy_rows.get(key, ()))
        enforcers[key] = build_enforcer(reader.model_conf_path(), reader.policy_csv_path(key))
        for member in resolved.values():
            bd_actors[member.bd_actor] = member.member_id
            subjects[member.casbin_subject] = key

    limits_record = (
        LimitsRecord.model_validate(dict(data.limits_raw)) if data.limits_raw is not None else None
    )
    registry = Registry(
        bd_actors=MappingProxyType(bd_actors),
        subjects=MappingProxyType(subjects),
        human_actors=frozenset(_human_actors(data)),
        system_actors=frozenset({system_actor}),
    )
    snapshot = SettingsSnapshot(
        generation=INITIAL_GENERATION,
        projects=MappingProxyType(projects),
        agents_global=agents_global,
        agents=MappingProxyType(agents),
        members=MappingProxyType(members),
        tasks_global=_task_records(data.tasks_global),
        tasks=MappingProxyType(tasks),
        registry=registry,
        llm_profiles=MappingProxyType(profiles),
        default_profile=data.default_profile,
        limits_global=resolve_limits_global(
            default_requests=default_requests, default_cost=default_cost, record=limits_record
        ),
        limits_overrides=MappingProxyType(resolve_limits_overrides(limits_record)),
        commands=MappingProxyType(_command_defs(data)),
        hooks=MappingProxyType(hooks),
        rules=MappingProxyType(rules),
        policies=MappingProxyType(policies),
        enforcers=MappingProxyType(enforcers),
    )
    _logger.info(
        "settings.build.completed",
        extra={
            "generation": snapshot.generation,
            "projects": len(projects),
            "members": sum(len(entry) for entry in members.values()),
        },
    )
    return snapshot


def _llm_profiles(data: AreaAData, context_reserve_tokens: int) -> dict[str, LLMProfile]:
    """`llm_profiles.yaml` を写し、`threshold_t` を導出して載せる（P5D-013）。"""
    return {
        name: resolve_profile(
            LlmProfileRecord.model_validate({"name": name, **normalize_mapping(entry)}),
            context_reserve_tokens,
        )
        for name, entry in data.profiles_raw.items()
    }


def _human_actors(data: AreaAData) -> list[str]:
    """`humans.yaml` の宣言（P5D-019。空の宣言なら人間 0 名＝ US5 AC-5）。

    読み方は `resolve.human_actor_names` に 1 本化する（V-20 が見た値と同じになる＝ R-01）。
    """
    return human_actor_names(data.humans_raw)


def _agent_records(raw: Mapping[str, object]) -> Mapping[PersonaKey, AgentSpecRecord]:
    """`agents.yaml` を写す（キーは V-01／V-02 が検査済みで `PersonaKey` に在る）。"""
    return MappingProxyType(
        {
            PersonaKey(name): AgentSpecRecord.model_validate(normalize_mapping(entry))
            for name, entry in raw.items()
            if name in PersonaKey.__members__
        }
    )


def _task_records(raw: Mapping[str, object]) -> Mapping[str, TaskTemplateRecord]:
    """`tasks.yaml` を写す（キーは V-08 が検査済みで 7 種の内側）。"""
    return MappingProxyType(
        {
            key: TaskTemplateRecord.model_validate(normalize_mapping(entry))
            for key, entry in raw.items()
        }
    )


def _to_project(key: str, raw: Mapping[str, object]) -> Project:
    """`project.yaml` を契約の `Project` へ写す（`role_dirs` のキーは V-28 が検査済み）。"""
    # ディレクトリ名を**後から**重ねる（`projects/<key>/` の `<key>` が正本。ファイル側に
    # `project_key` が書かれていても二重宣言を作らない）。
    record = ProjectYaml.model_validate({**dict(raw), "project_key": key})
    return Project(
        project_key=record.project_key,
        name=record.name,
        folders=ProjectFolders(
            pj_input_dir=record.folders.pj_input_dir,
            role_dirs=dict(record.folders.role_dirs),
            outcome_dir=record.folders.outcome_dir,
        ),
        is_reserved=record.is_reserved,
        repos=(
            ProjectRepos(
                remote_url=record.repos.remote_url,
                local_path=record.repos.local_path,
            )
            if record.repos is not None
            else None
        ),
    )


def _to_policy_set(key: str, rows: tuple[tuple[str, ...], ...]) -> PolicySet:
    """区分つきの行を `p`／`g` に割る（列数は担保 2 が済ませてある）。"""
    return PolicySet(
        project_key=key,
        p_rows=tuple(row[1:] for row in rows if row[0] == "p"),
        g_rows=tuple(row[1:] for row in rows if row[0] == "g"),
    )


def _command_defs(data: AreaAData) -> dict[str, CommandDef]:
    """`commands.yaml` を写す（`argv` の形は V-26 が検査済み）。"""
    defs: dict[str, CommandDef] = {}
    for name, entry in data.commands_raw.items():
        argv = normalize_mapping(entry).get("argv")
        items = cast("list[object]", argv) if isinstance(argv, list) else []
        defs[name] = CommandDef(name=name, argv=tuple(str(item) for item in items))
    return defs


def _members_of(
    key: str, data: AreaAData, profiles: Mapping[str, LLMProfile]
) -> dict[PersonaKey, Member]:
    """PJ 側 `agents.yaml` のキー集合＝メンバ集合に merge を掛けて `Member` を組む（R-06）。

    **PJ 側にキーが無ければメンバでない**（US1 AC-5）。キーが在って中身が空なら既定のままの
    メンバになる（AC-6）。merge・LLM 解決・台帳導出はいずれも `resolve.py` の規則を**生の
    mapping に対して**呼ぶので、**validate が見た値と `resolve()` が返す値が構造として一致
    する**（R-01）。
    """
    resolved: dict[PersonaKey, Member] = {}
    for name, entry in data.agents_raw.get(key, {}).items():
        if name not in PersonaKey.__members__:
            continue
        persona = PersonaKey(name)
        merged = merge_agent_fields(
            normalize_mapping(data.agents_global.get(name)), normalize_mapping(entry)
        )
        spec = AgentSpecRecord.model_validate(merged)
        profile_name = resolve_llm_name(merged, data.default_profile)
        if profile_name is None or profile_name not in profiles:
            msg = f"LLM が解決できないメンバが写像段まで来た（V-14 の破れ）: {key}.{name}"
            raise KeyError(msg)
        member_id = member_id_of(key, persona)
        resolved[persona] = Member(
            member_id=member_id,
            project_key=key,
            persona_key=persona,
            role=spec.role or "",
            goal=spec.goal or "",
            backstory=spec.backstory or "",
            llm_profile=profiles[profile_name],
            tools=list(spec.tools or []),
            skills=list(spec.skills or []),
            mcps=list(spec.mcps or []),
            max_iter=spec.max_iter,
            max_rpm=spec.max_rpm,
            max_execution_time=spec.max_execution_time,
            bd_actor=bd_actor_of(member_id),
            casbin_subject=casbin_subject_of(member_id),
        )
    return resolved
