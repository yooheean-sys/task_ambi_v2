"""(d2) 領域 A の読取。構文誤り・重複キー・必須ファイル不在を**打ち切らずに集める**。

集めた失敗はまとめて `SettingsAreaError` にする（US3 AC-5・AC-13）。構文が通らないファイルに
対して整合検査を走らせても意味が無いため、(d3) の手前で打ち切る（R-08）。
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from settings.exceptions import SettingsAreaError
from settings.models import AreaAData
from settings.repository import (
    COMMANDS_REL,
    GLOBAL_AGENTS_REL,
    GLOBAL_TASKS_REL,
    HUMANS_REL,
    LIMITS_REL,
    LLM_PROFILES_REL,
    MODEL_CONF_REL,
    AreaAReader,
    YamlError,
)
from settings.service.resolve import normalize_mapping

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def read_area(reader: AreaAReader) -> AreaAData:
    """領域 A を 1 回で読み切る（構文誤り・必須ファイル不在を**一覧で**集める）。

    Args:
        reader: 領域 A の読取アダプタ。

    Returns:
        生の構造を運ぶ入れ物。

    Raises:
        SettingsAreaError: 構文誤り・重複キー・必須ファイル不在が 1 件以上あった
            （**見に行った絶対パス**を載せる＝ FR-062・R-08）。
    """
    issues: list[str] = []
    if not reader.exists(MODEL_CONF_REL):
        issues.append(f"必須ファイルが無い: {reader.path_of(MODEL_CONF_REL)}")

    agents_global = _guard(issues, reader, GLOBAL_AGENTS_REL, reader.read_global_agents)
    tasks_global = _guard(issues, reader, GLOBAL_TASKS_REL, reader.read_global_tasks)
    profiles_file = _guard(issues, reader, LLM_PROFILES_REL, reader.read_llm_profiles)
    limits_raw = _guard_optional(issues, reader, LIMITS_REL, reader.read_limits)
    commands_file = _guard(issues, reader, COMMANDS_REL, reader.read_commands)
    humans_raw = _guard(issues, reader, HUMANS_REL, reader.read_humans)

    projects_raw: dict[str, Mapping[str, object]] = {}
    agents_raw: dict[str, Mapping[str, object]] = {}
    tasks_raw: dict[str, Mapping[str, object] | None] = {}
    hooks_raw: dict[str, Mapping[str, object] | None] = {}
    rules_raw: dict[str, str | None] = {}
    policy_rows: dict[str, tuple[tuple[str, ...], ...]] = {}

    for key in reader.list_project_keys():
        projects_raw[key] = _guard(
            issues,
            reader,
            reader.project_rel(key, "project.yaml"),
            partial(reader.read_project, key),
        )
        agents_raw[key] = _guard(
            issues,
            reader,
            reader.project_rel(key, "agents.yaml"),
            partial(reader.read_project_agents, key),
        )
        tasks_raw[key] = _guard_optional(
            issues,
            reader,
            reader.project_rel(key, "tasks.yaml"),
            partial(reader.read_project_tasks, key),
        )
        hooks_raw[key] = _guard_optional(
            issues,
            reader,
            reader.project_rel(key, "hooks.yaml"),
            partial(reader.read_hooks, key),
        )
        rules_raw[key] = _guard_rules(issues, reader, key)
        policy_rows[key] = _guard_policy(issues, reader, key)

    if issues:
        raise SettingsAreaError("領域 A を読めない（一覧を参照）", issues)

    return AreaAData(
        agents_global=agents_global,
        tasks_global=tasks_global,
        profiles_raw=normalize_mapping(profiles_file.get("profiles")),
        profiles_file_raw=profiles_file,
        default_profile=_as_optional_str(profiles_file.get("default")),
        limits_raw=limits_raw,
        commands_raw=normalize_mapping(commands_file.get("commands")),
        commands_file_raw=commands_file,
        humans_raw=humans_raw,
        projects_raw=projects_raw,
        agents_raw=agents_raw,
        tasks_raw=tasks_raw,
        hooks_raw=hooks_raw,
        rules_raw=rules_raw,
        policy_rows=policy_rows,
    )


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _guard(
    issues: list[str], reader: AreaAReader, rel: str, read: Callable[[], dict[str, object]]
) -> dict[str, object]:
    """必須ファイルを読む。失敗は一覧へ集めて `{}` を返す（**打ち切らない**）。"""
    try:
        return read()
    except OSError:
        issues.append(f"必須ファイルが無い: {reader.path_of(rel)}")
    except YamlError as exc:
        issues.append(f"構文誤り（{reader.path_of(rel)}）: {exc}")
    return {}


def _guard_optional(
    issues: list[str],
    reader: AreaAReader,
    rel: str,
    read: Callable[[], dict[str, object] | None],
) -> dict[str, object] | None:
    """任意ファイルを読む。不在は `None`（失敗ではない）。構文誤りだけを一覧へ集める。"""
    try:
        return read()
    except OSError:
        issues.append(f"読めないファイルがある: {reader.path_of(rel)}")
    except YamlError as exc:
        issues.append(f"構文誤り（{reader.path_of(rel)}）: {exc}")
    return None


def _guard_rules(issues: list[str], reader: AreaAReader, key: str) -> str | None:
    """`rules.md` を読む（任意。不在は `None`＝失敗にしない）。"""
    rel = reader.project_rel(key, "rules.md")
    try:
        return reader.read_rules(key)
    except OSError:
        issues.append(f"読めないファイルがある: {reader.path_of(rel)}")
    return None


def _guard_policy(issues: list[str], reader: AreaAReader, key: str) -> tuple[tuple[str, ...], ...]:
    """`policy.csv` の行を読む（PJ ごと必須。不在は**起動中断**＝ R-09・R-12）。

    不在を空ファイルやアダプタ無しの判定器で代替しない。D16-h (9) が「登録時に空 `policy.csv`
    を生成する」と定める以上、**登録を通った PJ には必ず在る**（不在は人が壊した状態）。
    """
    rel = reader.project_rel(key, "policy.csv")
    if not reader.exists(rel):
        issues.append(f"必須ファイルが無い: {reader.path_of(rel)}")
        return ()
    if not reader.exists(MODEL_CONF_REL):
        return ()
    try:
        return reader.read_policy(key)
    except TypeError as exc:
        # M-07: `[role_definition]` を欠く `model.conf` では `get_grouping_policy()` が
        # `TypeError: 'NoneType' object is not subscriptable` になる。原因は PJ の
        # `policy.csv` ではなく**共通の権限モデル**なので、そちらの絶対パスで報告する
        # （FR-062。PJ の数だけ無関係なファイルを名指ししない＝同じ文面は 1 件に畳む）。
        issue = f"権限モデルが壊れている（{reader.path_of(MODEL_CONF_REL)}）: {exc}"
        if issue not in issues:
            issues.append(issue)
    except (OSError, RuntimeError, ValueError) as exc:
        issues.append(f"権限ファイルを読めない（{reader.path_of(rel)}）: {exc}")
    return ()
