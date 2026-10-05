"""F13〜F17 の候補づくり（LLM プロファイル・上限・コマンド定義・権限・ルール／Hook／人間）。

いずれも「`AreaAData` の 1 フィールドを差し替えるだけ」の短い純関数である（plan (ix)）。
`service/{projects,members}.py` を import しない。

**保存時だけの新規規則はここに 1 つだけ**ある —— **既定 LLM プロファイルの削除の拒否**
（`kind` は `llm_ref`。C-5・010 SD-13）。メンバ 0 件のとき V-14 は違反 0 件（`005` R-17）で
あり、`default` が指すプロファイルを消しても既存 28 規則のどれも発火しないためである。
**V-16（閾値）と V-27（上限の値域）は `005` SD-31 で起動時へ前倒し済み**なので、ここでは
**再実装しない**（候補に `validate_forward` を当てれば自動的に効く＝ SC-013）。
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from common.interfaces.settings import ProjectNotFound, UnknownCommand
from settings.exceptions import SettingsItemNotFound, validation_error
from settings.models import ChangeSet, FileWrite, ValidationProblem
from settings.repository import dump_policy_rows, dump_text, dump_yaml_mapping
from settings.service.resolve import normalize_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

    from settings.models import AreaAData
    from settings.schemas import (
        CommandDefSchema,
        HooksConfigSchema,
        HumansView,
        LimitSpec,
        LlmProfileSpec,
        PolicySpec,
        TaskTemplatesView,
        TextBody,
    )

GLOBAL_TASKS_REL, LLM_PROFILES_REL = "global/tasks.yaml", "global/llm_profiles.yaml"
LIMITS_REL, COMMANDS_REL, HUMANS_REL = (
    "global/limits.yaml",
    "global/commands.yaml",
    "global/humans.yaml",
)


# -- F17 段テンプレート ------------------------------------------------------------------


def build_put_global_tasks(
    current: AreaAData, view: TaskTemplatesView
) -> tuple[AreaAData, ChangeSet]:
    """全体の段テンプレートの候補を組む（**V-08** が 7 キー必須を見る）。"""
    raw = {key: spec.model_dump(mode="json", exclude_unset=True) for key, spec in view.root.items()}
    candidate = dataclasses.replace(current, tasks_global=raw)
    return candidate, _one(GLOBAL_TASKS_REL, dump_yaml_mapping(raw))


def build_put_project_tasks(
    current: AreaAData, project_key: str, view: TaskTemplatesView
) -> tuple[AreaAData, ChangeSet]:
    """PJ の段テンプレート上書きの候補を組む（キー単位の丸ごと置換）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
    """
    _require_project(current, project_key)
    raw = {key: spec.model_dump(mode="json", exclude_unset=True) for key, spec in view.root.items()}
    candidate = dataclasses.replace(current, tasks_raw={**current.tasks_raw, project_key: raw})
    return candidate, _one(f"projects/{project_key}/tasks.yaml", dump_yaml_mapping(raw))


def build_delete_project_tasks(current: AreaAData, project_key: str) -> tuple[AreaAData, ChangeSet]:
    """PJ の上書きを全解除する候補を組む（ファイルを**消す**＝ FR-070。全体の 7 キーに戻る）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
    """
    _require_project(current, project_key)
    candidate = dataclasses.replace(current, tasks_raw={**current.tasks_raw, project_key: None})
    return candidate, ChangeSet(removals=(f"projects/{project_key}/tasks.yaml",))


# -- F13 LLM プロファイル ----------------------------------------------------------------


def build_create_profile(current: AreaAData, spec: LlmProfileSpec) -> tuple[AreaAData, ChangeSet]:
    """LLM プロファイル追加の候補を組む。

    同じ名前が既に在るときの受け皿は正本にも 28 規則にも無い（`POST` の応答は 201／422 だけ）。
    **規則を足さない**方針（FR-005・SC-013）に従い、**同名は差し替えになる**。
    """
    return _put_profile(current, spec.name, spec)


def build_update_profile(
    current: AreaAData, name: str, spec: LlmProfileSpec
) -> tuple[AreaAData, ChangeSet]:
    """LLM プロファイル編集の候補を組む（**パスの `name` が正本**。本文の同名は使わない）。

    Raises:
        SettingsItemNotFound: 未登録のプロファイル名（→ 404）。
    """
    _require_profile(current, name)
    return _put_profile(current, name, spec)


def build_delete_profile(current: AreaAData, name: str) -> tuple[AreaAData, ChangeSet]:
    """LLM プロファイル削除の候補を組む。

    **保存時だけの 3 つ目の規則**（C-5・010 SD-13）: **既定プロファイルの削除は拒否**する
    （メンバ 0 件のとき V-14 は違反 0 件＝ `005` R-17 なので前半に受け皿が無い）。参照中の
    プロファイルの削除は **V-15** が落とす。

    Raises:
        SettingsItemNotFound: 未登録のプロファイル名（→ 404）。
        SettingsValidationError: 既定プロファイルを消そうとした（`llm_ref` → 422）。
    """
    _require_profile(current, name)
    if current.default_profile == name:
        raise validation_error(
            [
                ValidationProblem(
                    where=f"{LLM_PROFILES_REL}:default",
                    kind="llm_ref",
                    reason=f"既定に指定されている LLM プロファイルは削除できない: {name}",
                )
            ]
        )
    profiles = {key: value for key, value in current.profiles_raw.items() if key != name}
    return _profiles_candidate(current, profiles, current.default_profile)


# -- F14 上限 -----------------------------------------------------------------------------


def build_put_global_limits(current: AreaAData, spec: LimitSpec) -> tuple[AreaAData, ChangeSet]:
    """全体既定の上限の候補を組む（**ここだけが `global:` を書き出す**＝ plan D-l）。

    書き出した時点で **CFG-39 の ③ 段**（`config.json` の `limits_defaults`）は役目を終える
    （利用者が明示的に全体既定を決めたときだけ起こる）。0・負値は **V-27** が落とす。
    """
    raw: dict[str, object] = {
        "global": spec.model_dump(mode="json"),
        "overrides": _overrides(current),
    }
    return _limits_candidate(current, raw)


def build_put_project_limits(
    current: AreaAData, project_key: str, spec: LimitSpec
) -> tuple[AreaAData, ChangeSet]:
    """PJ 上書きの上限の候補を組む（**`overrides` だけ**を差し替える＝ plan D-l・010 SD-16）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404。C-6。**規則ではなく口が落とす**）。
    """
    _require_project(current, project_key)
    overrides = {**_overrides(current), project_key: spec.model_dump(mode="json")}
    return _limits_candidate(current, _limits_raw(current, overrides))


def build_delete_project_limits(
    current: AreaAData, project_key: str
) -> tuple[AreaAData, ChangeSet]:
    """PJ 上書きの解除の候補を組む（同上。**`global:` を書き出さない**）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
    """
    _require_project(current, project_key)
    overrides = {key: value for key, value in _overrides(current).items() if key != project_key}
    return _limits_candidate(current, _limits_raw(current, overrides))


# -- F15 コマンド定義 --------------------------------------------------------------------


def build_create_command(current: AreaAData, spec: CommandDefSchema) -> tuple[AreaAData, ChangeSet]:
    """コマンド定義追加の候補を組む。

    ⚠ **新規名では原理的に到達不能**である（**B-40**・DIFF-17）—— V-25（死に定義の禁止）と
    V-24（policy の `cmd:` ⊆ 定義）が同じ重さで候補全体に当たるため、定義だけの候補は V-25 で
    必ず落ちる。**ただし既に定義済みの名前なら通る** —— 上の「同名は差し替えになる」の帰結で、
    参照行が既に在るので V-25 も V-24 も発火せず **201 が返る**（実測 2026-08-27。B-40 の範囲の
    訂正であり、`PUT /settings/commands/{name}` と同じ効果が `POST` でも得られる）。
    **契約を 1 行も変えず規則どおり実装する**。
    """
    return _put_command(current, spec.name, spec)


def build_update_command(
    current: AreaAData, name: str, spec: CommandDefSchema
) -> tuple[AreaAData, ChangeSet]:
    """コマンド定義編集の候補を組む（**パスの `name` が正本**。本文の同名は使わない）。

    Raises:
        UnknownCommand: 定義に無い名前（→ 404）。
    """
    _require_command(current, name)
    return _put_command(current, name, spec)


def build_delete_command(current: AreaAData, name: str) -> tuple[AreaAData, ChangeSet]:
    """コマンド定義削除の候補を組む。

    ⚠ **この経路も原理的に到達不能**である（**B-51**。`build_create_command` の **B-40** と同型）
    —— 本関数は `commands_raw` から 1 キーを落とすだけで **policy 行を触らない**。V-25（死に
    定義の禁止）が成立している領域 A からしか削除は始まらないので、削除後の候補では**必ず
    V-24**（policy の `cmd:` ⊆ 定義）が発火する。逆に policy 行を先に消す候補は **V-25** で
    落ちる。**契約を 1 行も変えず規則どおり実装する**。

    Raises:
        UnknownCommand: 定義に無い名前（→ 404）。
    """
    _require_command(current, name)
    commands = {key: value for key, value in current.commands_raw.items() if key != name}
    return _commands_candidate(current, commands)


# -- F16 権限ポリシー --------------------------------------------------------------------


def build_put_policy(
    current: AreaAData, project_key: str, spec: PolicySpec
) -> tuple[AreaAData, ChangeSet]:
    """権限表の候補を組む（構文検査は `save.py` 段 2-4 の `build_enforcer` に乗る＝ R-05）。

    **自前で構文検査を書かない**（列数 `p`=3・`g`=2 も空打ちも担保 2・3 が見る）。**値を引用符で
    囲まない**（FR-029。囲むと V-21 が孤児として拒否する）。行の種別が `p`／`g` 以外の形は
    **`PolicyRow.ptype` の列挙**が口で締め出す（DIFF-18）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
    """
    _require_project(current, project_key)
    rows = tuple((row.ptype, *row.fields) for row in spec.rows)
    candidate = dataclasses.replace(current, policy_rows={**current.policy_rows, project_key: rows})
    return candidate, _one(f"projects/{project_key}/policy.csv", dump_policy_rows(rows))


# -- F17 ルール・Hook・人間 --------------------------------------------------------------


def build_put_rules(
    current: AreaAData, project_key: str, body: TextBody
) -> tuple[AreaAData, ChangeSet]:
    """共通ルールの候補を組む（**空文字も可**＝ FR-065。本文を見る規則が 1 つも無い）。

    `DELETE /settings/rules/...` は正本に無く、**空文字の `PUT` で代替する**（B-44 (b)）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
    """
    _require_project(current, project_key)
    candidate = dataclasses.replace(
        current, rules_raw={**current.rules_raw, project_key: body.body}
    )
    return candidate, _one(f"projects/{project_key}/rules.md", dump_text(body.body))


def build_put_hooks(
    current: AreaAData, project_key: str, config: HooksConfigSchema
) -> tuple[AreaAData, ChangeSet]:
    """Hook 宣言の候補を組む（**V-18** は形式検査だけ。接続も PATH 解決もしない＝ REC-02）。

    ⚠ **口の `points` は一覧、領域 A の `points` は 1 つ**である（正本 `openapi.yaml` の
    `HookRef.points: array` と、現物 `validate/forward.py` が単一文字列を見る形の差）。
    **地点ごとに 1 宣言へ展開して書く** —— 読取側を 1 行も変えずに正本の形を受ける写像であり、
    **新しい規則を足さない**。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
    """
    _require_project(current, project_key)
    entries: list[dict[str, object]] = []
    for ref in config.hooks:
        for point in ref.points:
            entry: dict[str, object] = {"name": ref.name, "points": point}
            if ref.agents is not None:
                entry["agents"] = list(ref.agents)
            entries.append(entry)
    raw: dict[str, object] = {"hooks": entries}
    candidate = dataclasses.replace(current, hooks_raw={**current.hooks_raw, project_key: raw})
    return candidate, _one(f"projects/{project_key}/hooks.yaml", dump_yaml_mapping(raw))


def build_put_humans(current: AreaAData, view: HumansView) -> tuple[AreaAData, ChangeSet]:
    """人間の行為者の候補を組む（**V-20** が 3 対の衝突を見る）。"""
    raw: dict[str, object] = {"human_actors": list(view.human_actors)}
    candidate = dataclasses.replace(current, humans_raw=raw)
    return candidate, _one(HUMANS_REL, dump_yaml_mapping(raw))


# -- 内部 -------------------------------------------------------------------------------


def _one(rel: str, text: str) -> ChangeSet:
    """1 ファイルだけを書く `ChangeSet`。"""
    return ChangeSet(writes=(FileWrite(rel=rel, text=text),))


def _require_project(current: AreaAData, project_key: str) -> None:
    """未登録キーを **404** の契約例外にする（FR-075）。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー。
    """
    if project_key not in current.projects_raw:
        raise ProjectNotFound(project_key)


def _require_profile(current: AreaAData, name: str) -> None:
    """未登録のプロファイル名を **404** にする。

    Raises:
        SettingsItemNotFound: 定義に無い名前。
    """
    if name not in current.profiles_raw:
        msg = f"LLM プロファイルが無い: {name}"
        raise SettingsItemNotFound(msg)


def _require_command(current: AreaAData, name: str) -> None:
    """未登録のコマンド名を **404** の契約例外にする。

    Raises:
        UnknownCommand: 定義に無い名前。
    """
    if name not in current.commands_raw:
        raise UnknownCommand(name)


def _put_profile(
    current: AreaAData, name: str, spec: LlmProfileSpec
) -> tuple[AreaAData, ChangeSet]:
    """プロファイル 1 件を差し替える（`is_default` は**ファイルの `default:` へ写す**）。

    `is_default` と `name` を `profiles[<name>]` に書かない —— 領域 A の写像
    `LlmProfileRecord` は `extra="forbid"` であり、`name` はキーから注入される。
    **`is_default=False` で既定を降ろさない**（`default` を宙に浮かせないため。降ろすのは
    別のプロファイルを既定にするか、そのプロファイルごと消すときだけ）。
    """
    fields = spec.model_dump(mode="json", exclude={"name", "is_default"})
    profiles = {**current.profiles_raw, name: fields}
    default = name if spec.is_default else current.default_profile
    return _profiles_candidate(current, profiles, default)


def _profiles_candidate(
    current: AreaAData, profiles: Mapping[str, object], default: str | None
) -> tuple[AreaAData, ChangeSet]:
    """`global/llm_profiles.yaml` を組み直した候補（**未知のトップレベルキーを落とさない**）。"""
    raw: dict[str, object] = {
        key: value
        for key, value in current.profiles_file_raw.items()
        if key not in ("profiles", "default")
    }
    raw["profiles"] = dict(profiles)
    raw["default"] = default
    candidate = dataclasses.replace(
        current,
        profiles_raw=dict(profiles),
        profiles_file_raw=raw,
        default_profile=default,
    )
    return candidate, _one(LLM_PROFILES_REL, dump_yaml_mapping(raw))


def _overrides(current: AreaAData) -> dict[str, object]:
    """`global/limits.yaml` の `overrides`（不在なら空）。"""
    raw = current.limits_raw
    return normalize_mapping(raw.get("overrides")) if raw is not None else {}


def _limits_raw(current: AreaAData, overrides: Mapping[str, object]) -> dict[str, object]:
    """`overrides` だけを差し替えた生の構造（**`global:` を新たに書き出さない**＝ plan D-l）。

    現物に `global:` が在るときだけ引き継ぐ。無いまま書き出すと **CFG-39 の ③ 段**が以後
    永久に効かなくなる（前半が `limits.yaml` を雛形生成の対象から外している理由そのもの）。
    """
    raw: dict[str, object] = {}
    current_raw = current.limits_raw
    if current_raw is not None and current_raw.get("global") is not None:
        raw["global"] = current_raw["global"]
    raw["overrides"] = dict(overrides)
    return raw


def _limits_candidate(current: AreaAData, raw: Mapping[str, object]) -> tuple[AreaAData, ChangeSet]:
    """`global/limits.yaml` を差し替えた候補。"""
    candidate = dataclasses.replace(current, limits_raw=dict(raw))
    return candidate, _one(LIMITS_REL, dump_yaml_mapping(raw))


def _put_command(
    current: AreaAData, name: str, spec: CommandDefSchema
) -> tuple[AreaAData, ChangeSet]:
    """コマンド定義 1 件を差し替える（形は前半が確定した `commands: {<名前>: {argv: [...]}}`）。"""
    commands = {**current.commands_raw, name: {"argv": list(spec.argv)}}
    return _commands_candidate(current, commands)


def _commands_candidate(
    current: AreaAData, commands: Mapping[str, object]
) -> tuple[AreaAData, ChangeSet]:
    """`global/commands.yaml` を組み直した候補（**未知のトップレベルキーを落とさない**）。"""
    raw: dict[str, object] = {
        key: value for key, value in current.commands_file_raw.items() if key != "commands"
    }
    raw["commands"] = dict(commands)
    candidate = dataclasses.replace(current, commands_raw=dict(commands), commands_file_raw=raw)
    return candidate, _one(COMMANDS_REL, dump_yaml_mapping(raw))
