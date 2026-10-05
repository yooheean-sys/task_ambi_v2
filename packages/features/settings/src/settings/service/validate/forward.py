"""順方向の入口（F18）。ファイル単位の検査（`agents.py`・`tasks.py`）を領域 A 全体へ広げる。

V-14（LLM が解決順の終端まで届く）・V-16（閾値）・V-17（メンバ ⊆ role_dirs）・V-19（メンバ ⊆
policy 主体）・V-20（行為者名の衝突）・V-27（上限の値域）・V-28（`role_dirs` のキー）。

**V-28 は V-17 より先に当てる**（綴り違いを V-17 が「`agents.yaml` のメンバが役割フォルダに
無い」と**誤診**するのを防ぐ＝ A1）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from common.interfaces.settings import HookRef
from common.kernel.enums import PersonaKey
from settings.models import AGENT_GLOBAL_REQUIRED
from settings.service.resolve import (
    casbin_subject_of,
    human_actor_names,
    malformed_human_actors,
    member_id_of,
    merge_agent_fields,
    normalize_mapping,
    resolve_llm_name,
)
from settings.service.validate.agents import check_agents_mapping
from settings.service.validate.problems import (
    HOOK_POINTS,
    check_secret_keys,
    problem,
    str_items,
)
from settings.service.validate.tasks import check_tasks_mapping

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from settings.models import AreaAData, ValidationProblem


def validate_forward(
    data: AreaAData, *, context_reserve_tokens: int, system_actor: str
) -> list[ValidationProblem]:
    """順方向の検査（F18）。V-01〜V-20 ＋ V-27 ＋ V-28。"""
    profile_names = frozenset(normalize_mapping(data.profiles_raw))
    problems: list[ValidationProblem] = []

    problems.extend(
        check_agents_mapping(
            data.agents_global,
            where="global/agents.yaml",
            require_global_fields=True,
            profile_names=profile_names,
        )
    )
    problems.extend(
        check_tasks_mapping(data.tasks_global, where="global/tasks.yaml", require_all_keys=True)
    )
    problems.extend(_check_profiles(data, context_reserve_tokens))
    problems.extend(_check_limits(data))

    for key in sorted(data.projects_raw):
        problems.extend(_check_project_folders(key, data))
        problems.extend(
            check_agents_mapping(
                data.agents_raw.get(key, {}),
                where=f"projects/{key}/agents.yaml",
                require_global_fields=False,
                profile_names=profile_names,
            )
        )
        pj_tasks = data.tasks_raw.get(key)
        if pj_tasks is not None:
            problems.extend(
                check_tasks_mapping(
                    pj_tasks, where=f"projects/{key}/tasks.yaml", require_all_keys=False
                )
            )
        problems.extend(_check_hooks(key, data))
        problems.extend(_check_members(key, data))
    problems.extend(_check_actor_collisions(data, system_actor))
    return problems


def _check_profiles(data: AreaAData, context_reserve_tokens: int) -> list[ValidationProblem]:
    """V-06（秘密語）・V-15（`default` の実在）・V-16（`context_window` > 予備）。"""
    where = "global/llm_profiles.yaml"
    profiles = normalize_mapping(data.profiles_raw)
    # V-06 は**ファイルの生トップレベル**から走査する（FR-050）。`profiles:` の部分木だけを
    # 見ると、`api_key:` をファイル直下に置いた場合に素通りする。
    problems: list[ValidationProblem] = list(check_secret_keys(data.profiles_file_raw, where))
    if data.default_profile is not None and data.default_profile not in profiles:
        problems.append(
            problem(
                f"{where}:default",
                "llm_ref",
                f"既定プロファイルが profiles に無い: {data.default_profile}",
            )
        )
    for name, entry in profiles.items():
        window = normalize_mapping(entry).get("context_window")
        if isinstance(window, bool) or not isinstance(window, int):
            problems.append(
                problem(f"{where}:{name}.context_window", "llm_ref", "context_window は整数で書く")
            )
            continue
        if window <= context_reserve_tokens:
            problems.append(
                problem(
                    f"{where}:{name}.context_window",
                    "llm_ref",
                    f"閾値が 0 以下（window={window} ≦ 予備 {context_reserve_tokens}）",
                )
            )
    return problems


def _check_limits(data: AreaAData) -> list[ValidationProblem]:
    """V-27 `limits.yaml` の上限が**いずれも 0 より大きい**（裁定 2026-08-24・CHK034）。"""
    if data.limits_raw is None:
        return []
    where = "global/limits.yaml"
    problems: list[ValidationProblem] = list(check_secret_keys(data.limits_raw, where))
    targets: list[tuple[str, object]] = [("global", data.limits_raw.get("global"))]
    overrides = normalize_mapping(data.limits_raw.get("overrides"))
    targets.extend((f"overrides.{key}", value) for key, value in sorted(overrides.items()))
    for label, entry in targets:
        if entry is None:
            continue
        fields = normalize_mapping(entry)
        for name in ("max_requests_per_kickoff", "max_cost_per_kickoff"):
            value = fields.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(
                    problem(f"{where}:{label}.{name}", "forward", f"{name} は数値で書く")
                )
                continue
            if value <= 0:
                problems.append(
                    problem(
                        f"{where}:{label}.{name}", "forward", f"上限が 0 以下になっている: {value}"
                    )
                )
    return problems


def _check_project_folders(key: str, data: AreaAData) -> list[ValidationProblem]:
    """V-28 `folders.role_dirs` のキーが `PersonaKey` に在る（**V-17 より先に当てる**）。

    V-28 が無いと、綴り違いを V-17 が「`agents.yaml` のメンバが役割フォルダに無い」と
    **誤診**し、原因（`project.yaml`）と違う場所を指す（裁定 2026-08-24・A1）。
    """
    where = f"projects/{key}/project.yaml"
    raw = data.projects_raw.get(key, {})
    problems: list[ValidationProblem] = list(check_secret_keys(raw, where))
    role_dirs = normalize_mapping(normalize_mapping(raw.get("folders")).get("role_dirs"))
    problems.extend(
        problem(
            f"{where}:folders.role_dirs.{name}",
            "agent_ref",
            f"共有語彙に無いペルソナ: {name}",
        )
        for name in sorted(role_dirs)
        if name not in PersonaKey.__members__
    )
    return problems


def _check_hooks(key: str, data: AreaAData) -> list[ValidationProblem]:
    """V-18 `hooks.yaml` の**宣言の形式**（`name`・`points`）。**登録も接続もしない**。"""
    raw = data.hooks_raw.get(key)
    if raw is None:
        return []
    where = f"projects/{key}/hooks.yaml"
    problems: list[ValidationProblem] = list(check_secret_keys(raw, where))
    entries = raw.get("hooks")
    if entries is None:
        return problems
    if not isinstance(entries, list):
        problems.append(problem(f"{where}:hooks", "unresolvable", "hooks は宣言の一覧で書く"))
        return problems
    for index, item in enumerate(cast("list[object]", entries)):
        spot = f"{where}:hooks[{index}]"
        if not isinstance(item, dict):
            problems.append(problem(spot, "unresolvable", "hooks の要素は mapping で書く"))
            continue
        fields = normalize_mapping(cast("dict[object, object]", item))
        if not isinstance(fields.get("name"), str):
            problems.append(problem(f"{spot}.name", "unresolvable", "name は文字列で書く"))
        points = fields.get("points")
        if not isinstance(points, str) or points not in HOOK_POINTS:
            problems.append(
                problem(f"{spot}.points", "unresolvable", f"points が許可値の外: {points!r}")
            )
        agents = fields.get("agents")
        if agents is not None and str_items(agents) is None:
            problems.append(problem(f"{spot}.agents", "unresolvable", "agents は文字列の一覧"))
    return problems


def merged_members(key: str, data: AreaAData) -> dict[PersonaKey, dict[str, object]]:
    """PJ 側 `agents.yaml` のキー集合＝メンバ集合に、④→⑤ merge を掛ける（D16-d）。"""
    merged: dict[PersonaKey, dict[str, object]] = {}
    for name, entry in data.agents_raw.get(key, {}).items():
        if name not in PersonaKey.__members__:
            continue
        persona = PersonaKey(name)
        base = normalize_mapping(data.agents_global.get(name))
        merged[persona] = merge_agent_fields(base, normalize_mapping(entry))
    return merged


def _check_members(key: str, data: AreaAData) -> list[ValidationProblem]:
    """V-04 の PJ 側の裏返し（V-14 LLM の終端・V-17 役割フォルダ・V-19 policy 主体）。

    V-17 は **V-28（`role_dirs` のキーが共有語彙に在る）が当該 PJ で通っているときだけ**当てる
    （契約 §7 行 17・裁定 2026-08-24・A1）。
    """
    problems: list[ValidationProblem] = []
    members = merged_members(key, data)
    role_dirs = normalize_mapping(
        normalize_mapping(data.projects_raw.get(key, {}).get("folders")).get("role_dirs")
    )
    # V-28 が当該 PJ で違反を出しているとき、V-17 は**当てない**（裁定 2026-08-24・A1）。
    # 綴り違いの原因は `project.yaml` 側にあり、`agents.yaml` を指す V-17 の報告は誤診に
    # なる。「先に当てる」を出力順だけで済ませると誤診が併記されたまま残る。
    role_dirs_are_sound = all(name in PersonaKey.__members__ for name in role_dirs)
    subjects = _policy_subjects(data.policy_rows.get(key, ()))
    agents_where = f"projects/{key}/agents.yaml"
    for persona, fields in sorted(members.items(), key=lambda pair: pair[0].value):
        spot = f"{agents_where}:{persona.value}"
        if resolve_llm_name(fields, data.default_profile) is None:
            problems.append(
                problem(f"{spot}.llm", "llm_ref", "LLM が解決順の終端まで届かない（既定も無い）")
            )
        for name in sorted(AGENT_GLOBAL_REQUIRED):
            if not isinstance(fields.get(name), str):
                problems.append(
                    problem(f"{spot}.{name}", "var_required", f"merge 後も欠けている: {name}")
                )
        if role_dirs_are_sound and persona.value not in role_dirs:
            problems.append(
                problem(
                    spot,
                    "forward",
                    f"メンバのペルソナが project.yaml の role_dirs に無い: {persona.value}",
                )
            )
        subject = casbin_subject_of(member_id_of(key, persona))
        if subject not in subjects:
            problems.append(problem(spot, "forward", f"policy に主体の行が 1 つも無い: {subject}"))
    return problems


def _policy_subjects(rows: Iterable[tuple[str, ...]]) -> frozenset[str]:
    """`p`／`g` 行の主体を集める（行は先頭に `"p"`／`"g"` の区分を持つ）。"""
    return frozenset(row[1] for row in rows if len(row) >= 2)


def _check_actor_collisions(data: AreaAData, system_actor: str) -> list[ValidationProblem]:
    """V-20 人間の宣言・AI メンバ・システムの行為者が**互いに衝突しない**（3 対すべて）。

    順序に依存した黙った勝敗を作らないため、衝突そのものを拒否する（D06-c）。
    """
    where = "global/humans.yaml:human_actors"
    problems: list[ValidationProblem] = list(
        check_secret_keys(data.humans_raw, "global/humans.yaml")
    )
    # 宣言そのものが読めない形（リストでない・要素が文字列でない）を**先に違反にする**。
    # ここで黙って空集合に落とすと、写像側（`human_actor_names`）は残りを拾うため、衝突が
    # 違反 0 件のままスナップショットに残る（R-01 が禁じる「検査と写像の別経路」）。
    malformed = malformed_human_actors(data.humans_raw)
    if malformed is None:
        problems.append(problem(where, "forward", "human_actors はリストで書く"))
    else:
        problems.extend(
            problem(where, "forward", f"human_actors[{index}] が文字列でない")
            for index in malformed
        )
    humans = frozenset(human_actor_names(data.humans_raw))
    bd_actors = {
        member_id_of(key, persona)
        for key in data.agents_raw
        for persona in merged_members(key, data)
    }
    systems = frozenset({system_actor})
    problems.extend(
        problem(where, "forward", f"人間の宣言と AI メンバの行為者名が衝突: {name}")
        for name in sorted(humans & bd_actors)
    )
    problems.extend(
        problem(where, "forward", f"人間の宣言とシステムの行為者名が衝突: {name}")
        for name in sorted(humans & systems)
    )
    problems.extend(
        problem(
            "settings/config.json:system_actor",
            "forward",
            f"システムの行為者名と AI メンバの行為者名が衝突: {name}",
        )
        for name in sorted(systems & bd_actors)
    )
    return problems


def build_hook_refs(raw: Mapping[str, object] | None) -> list[HookRef]:
    """検査を通った `hooks.yaml` を契約の `HookRef` へ写す（構築はここでしか行わない）。"""
    if raw is None:
        return []
    entries = raw.get("hooks")
    if not isinstance(entries, list):
        return []
    refs: list[HookRef] = []
    for item in cast("list[object]", entries):
        fields = normalize_mapping(item)
        points = fields.get("points")
        if not isinstance(points, str) or points not in HOOK_POINTS:
            continue
        refs.append(
            HookRef(
                name=str(fields.get("name", "")),
                points="PRE_TOOL_CALL" if points == "PRE_TOOL_CALL" else "POST_TOOL_CALL",
                agents=str_items(fields.get("agents")) or [],
            )
        )
    return refs
