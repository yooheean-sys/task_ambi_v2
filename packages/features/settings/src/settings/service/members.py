"""F12 メンバ CRUD の候補づくり（plan (ii)）。

`agents.yaml` の**キーの CRUD** だけを行い、台帳の再導出は `build_snapshot` に任せる（R-03。
導出規則は `service/loader/assemble.py` の中に 1 本だけある）。**共通既定との重ね方も前半に
既にある**ので再実装しない（SC-013）。

すべて `(current: AreaAData, …) -> tuple[AreaAData, ChangeSet]` の**純関数**である。
`service/{projects,ai_config}.py` を import しない。

**本ファイルは `ValidationProblem` を 1 件も作らない** —— メンバの規則はすべて前半の 28 規則
（V-03・V-04・V-05・V-06・V-14・V-15・V-17・V-19・V-21）が候補に当たることで効く（S-9・SC-013）。

⚠ **口から到達できるのは「既に居るメンバの編集」だけ**である（**B-49**）。新規作成は V-19
（メンバ ⊆ policy 主体）で、削除は V-21（policy 主体 ⊆ 台帳）で必ず落ちる —— この口が触る
領域 A のファイルは `agents.yaml` **だけ**（contracts/settings-http.md §1 #10・#11）なので、
メンバと権限行を同じ候補に含められない。**契約を 1 行も変えず規則どおり実装する**（B-40 と
同型の行き止まりであり、塞ぐには契約の改訂が要る）。
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from common.interfaces.settings import MemberNotFound, ProjectNotFound
from settings.models import ChangeSet, FileWrite
from settings.repository import dump_yaml_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

    from common.kernel.enums import PersonaKey
    from settings.models import AreaAData
    from settings.schemas import AgentSpec

GLOBAL_AGENTS_REL = "global/agents.yaml"
"""全体のペルソナ既定（F12-c が書く唯一のファイル）。"""


def build_put_global(
    current: AreaAData, persona_key: PersonaKey, spec: AgentSpec
) -> tuple[AreaAData, ChangeSet]:
    """F12-c 全体のペルソナ既定の編集の候補を組む。

    必須 3（`role`・`goal`・`backstory`）は**候補の検査 V-04** が見る（口では見ない —— PJ 側は
    全任意なので同じ型を使う）。

    Args:
        current: 保持している生の構造。
        persona_key: 対象のペルソナ。
        spec: 口が受けた本文。

    Returns:
        候補の生の構造と、`global/agents.yaml` の 1 件。
    """
    raw = {**current.agents_global, persona_key.value: _fields(spec)}
    candidate = dataclasses.replace(current, agents_global=raw)
    return candidate, ChangeSet(
        writes=(FileWrite(rel=GLOBAL_AGENTS_REL, text=dump_yaml_mapping(raw)),)
    )


def build_put(
    current: AreaAData, project_key: str, persona_key: PersonaKey, spec: AgentSpec
) -> tuple[AreaAData, ChangeSet]:
    """F12-a メンバの upsert の候補を組む（**キーの存在＝メンバ**）。

    **中身が空の dict でもよい**（US3 AC-1・AC-2。共通既定のまま参加する）。一覧型
    （`tools`・`skills`・`mcps`）は `merge_agent_fields` が**丸ごと置換**する（和集合にしない）。

    Args:
        current: 保持している生の構造。
        project_key: プロジェクトキー。
        persona_key: 対象のペルソナ。
        spec: 口が受けた本文。

    Returns:
        候補の生の構造と、`projects/<k>/agents.yaml` の 1 件。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404。D-3・FR-075）。
    """
    members = _require_members(current, project_key)
    raw = {**members, persona_key.value: _fields(spec)}
    return _with_members(current, project_key, raw)


def build_delete(
    current: AreaAData, project_key: str, persona_key: PersonaKey
) -> tuple[AreaAData, ChangeSet]:
    """F12-b メンバ削除の候補を組む（孤児の権限行は **V-21** が落とす＝ FR-051）。

    Args:
        current: 保持している生の構造。
        project_key: プロジェクトキー。
        persona_key: 対象のペルソナ。

    Returns:
        候補の生の構造と、`projects/<k>/agents.yaml` の 1 件。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        MemberNotFound: そのペルソナのキーが `agents.yaml` に無い（→ 404）。
    """
    members = _require_members(current, project_key)
    if persona_key.value not in members:
        raise MemberNotFound(f"{project_key}.{persona_key.value}")
    raw = {name: value for name, value in members.items() if name != persona_key.value}
    return _with_members(current, project_key, raw)


# -- 内部 -------------------------------------------------------------------------------


def _fields(spec: AgentSpec) -> dict[str, object]:
    """**送られた項目だけ**を領域 A の形へ写す（`exclude_unset`）。

    「送っていない」と「明示的に `null` を送った」を分ける —— `merge_agent_fields` は
    **キーが在れば値が `None` でも上書きとして扱う**（PJ 側で `tools:` と書けば装備を空に
    絞れる）。`exclude_none` にすると後者が表せなくなる。
    """
    return spec.model_dump(mode="json", exclude_unset=True)


def _require_members(current: AreaAData, project_key: str) -> Mapping[str, object]:
    """PJ の `agents.yaml` の現物を取り出す。

    Raises:
        ProjectNotFound: 未登録のプロジェクトキー。
    """
    if project_key not in current.projects_raw:
        raise ProjectNotFound(project_key)
    return current.agents_raw.get(project_key, {})


def _with_members(
    current: AreaAData, project_key: str, raw: Mapping[str, object]
) -> tuple[AreaAData, ChangeSet]:
    """PJ の `agents.yaml` を差し替えた候補と `ChangeSet` を組む。"""
    candidate = dataclasses.replace(current, agents_raw={**current.agents_raw, project_key: raw})
    rel = f"projects/{project_key}/agents.yaml"
    # メンバ 0 件でも読める形にする（`load_yaml_mapping` は空文字を `{}` に正規化するが、
    # 生成物の見た目を `AreaAScaffolder` の空の骨格に揃える）。
    text = dump_yaml_mapping(raw) if raw else "{}\n"
    return candidate, ChangeSet(writes=(FileWrite(rel=rel, text=text),))
