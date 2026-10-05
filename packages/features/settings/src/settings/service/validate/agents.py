"""`agents.yaml` 1 ファイル分の検査（V-01〜V-07・V-15・V-18 の一部）。

`global` と PJ の両方を同じ入口で見る。必須性の差（global は `role`・`goal`・`backstory` が必須、
PJ は全任意）は引数で切り替える（D16-d）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from common.kernel.enums import PersonaKey, ToolName
from settings.models import AGENT_FIELDS, AGENT_GLOBAL_REQUIRED, FORBIDDEN_FIELDS
from settings.service.resolve import normalize_mapping
from settings.service.validate.problems import (
    MCP_FIELDS,
    check_secret_keys,
    problem,
    str_items,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from settings.models import ValidationProblem


def check_agents_mapping(
    raw: Mapping[str, object],
    *,
    where: str,
    require_global_fields: bool,
    profile_names: frozenset[str],
) -> list[ValidationProblem]:
    """`agents.yaml` 1 ファイル分の検査（V-01〜V-07・V-15・V-18）。

    Args:
        raw: ファイルのトップレベル mapping（キー＝ `PersonaKey`）。
        where: 領域 A ルート相対のファイルパス（`where` の接頭）。
        require_global_fields: `role`・`goal`・`backstory` を必須にするか（global のみ真）。
        profile_names: `llm_profiles.yaml` の名前集合（V-15 の照合先）。

    Returns:
        違反の一覧（**打ち切らない**）。
    """
    problems: list[ValidationProblem] = list(check_secret_keys(raw, where))
    for key, entry in raw.items():
        spot = f"{where}:{key}"
        if key not in PersonaKey.__members__:
            problems.append(problem(spot, "agent_ref", f"共有語彙に無いペルソナ: {key}"))
            continue
        fields = normalize_mapping(entry)
        problems.extend(_check_agent_fields(fields, spot, profile_names))
        if require_global_fields:
            problems.extend(_check_agent_required(fields, spot))
    return problems


def _check_agent_fields(
    fields: Mapping[str, object], spot: str, profile_names: frozenset[str]
) -> list[ValidationProblem]:
    """V-05（禁止キー）→ V-03（未知フィールド）の順に当て、値の規則を続ける（R-20）。"""
    problems: list[ValidationProblem] = []
    for name, value in fields.items():
        if name in FORBIDDEN_FIELDS:
            problems.append(
                problem(f"{spot}.{name}", "forbidden_key", f"禁止キー（D16-d）: {name}")
            )
            continue
        if name not in AGENT_FIELDS:
            problems.append(
                problem(f"{spot}.{name}", "unknown_field", f"許可 10 の外のフィールド: {name}")
            )
            continue
        problems.extend(_check_agent_value(name, value, spot, profile_names))
    return problems


def _check_agent_value(
    name: str, value: object, spot: str, profile_names: frozenset[str]
) -> list[ValidationProblem]:
    """フィールドごとの値の規則（V-07 tools・V-15 llm・V-18 skills／mcps）。"""
    if name == "tools":
        return check_tool_names(value, f"{spot}.tools")
    if name == "llm":
        return _check_llm_name(value, f"{spot}.llm", profile_names)
    if name == "skills":
        return _check_skills(value, f"{spot}.skills")
    if name == "mcps":
        return _check_mcps(value, f"{spot}.mcps")
    return []


def check_tool_names(value: object, spot: str) -> list[ValidationProblem]:
    """V-07 `tools:` の各名前が `ToolName`（12）に在る。"""
    if value is None:
        return []
    items = str_items(value)
    if items is None:
        return [problem(spot, "tool_ref", "tools は文字列の一覧で書く")]
    return [
        problem(spot, "tool_ref", f"共有語彙に無い Tool 名: {item}")
        for item in items
        if item not in ToolName.__members__
    ]


def _check_llm_name(
    value: object, spot: str, profile_names: frozenset[str]
) -> list[ValidationProblem]:
    """V-15 指した `llm` の名前が `llm_profiles.yaml` の `name` に在る。"""
    if value is None:
        return []
    if not isinstance(value, str):
        return [problem(spot, "llm_ref", "llm はプロファイル名（文字列）で書く")]
    if value not in profile_names:
        return [problem(spot, "llm_ref", f"プロファイルに無い名前: {value}")]
    return []


def _check_skills(value: object, spot: str) -> list[ValidationProblem]:
    """V-18 `skills` の宣言の形式（文字列の一覧）。**解決はしない**（REC-02）。"""
    if value is None:
        return []
    if str_items(value) is None:
        return [problem(spot, "unresolvable", "skills は文字列の一覧で書く")]
    return []


def _check_mcps(value: object, spot: str) -> list[ValidationProblem]:
    """V-18 `mcps` の宣言の形式（`name`・`transport`・`command`・`args`）。**接続しない**。"""
    if value is None:
        return []
    if not isinstance(value, list):
        return [problem(spot, "unresolvable", "mcps は宣言の一覧で書く")]
    problems: list[ValidationProblem] = []
    for index, item in enumerate(cast("list[object]", value)):
        place = f"{spot}[{index}]"
        if not isinstance(item, dict):
            problems.append(problem(place, "unresolvable", "mcps の要素は mapping で書く"))
            continue
        fields = normalize_mapping(cast("dict[object, object]", item))
        missing = [name for name in MCP_FIELDS if name not in fields]
        if missing:
            problems.append(
                problem(place, "unresolvable", f"mcps の宣言に欠けがある: {', '.join(missing)}")
            )
    return problems


def _check_agent_required(fields: Mapping[str, object], spot: str) -> list[ValidationProblem]:
    """V-04 `global/agents.yaml` の各ペルソナに `role`・`goal`・`backstory` が在る。"""
    return [
        problem(f"{spot}.{name}", "var_required", f"global の必須フィールドが無い: {name}")
        for name in sorted(AGENT_GLOBAL_REQUIRED)
        if not isinstance(fields.get(name), str)
    ]
