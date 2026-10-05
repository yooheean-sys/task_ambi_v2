"""役割ごとの実効設定（D26。`GET /settings/projects/{project_key}/roles` の中身）。

権限は 4 か所に分かれて宣言される —— 装備（`agents.yaml` の `tools`。全体の既定に PJ を
重ねた結果）・許可（`policy.csv`）・段の既定担当（`tasks.yaml` の `agent:`）・段の装備の
置き換え（`tasks.yaml` の `tools:`）。本モジュールはそれらを**役割単位に並べるだけ**で、
新しい規則を足さない（重ね合わせは `SettingsService.resolve`／`get_tasks`、許可の判定は
`SettingsService.enforce` ＝判定器に任せる。keyMatch・`g` 行の継承を再実装しない）。

⚠ 対象の書式 `tool:<名前>` と動作 `execute` は executor（`tools/base.py`）の 1 段目の判定と
**同じ約束を二重に持つ**（feature 間 import 禁止のため）。どちらかを変えるときは両方を直す
（D26）。

判定器（casbin）は判定 1 件ごとにログを出し、拒否は WARNING で出る。16 種すべてを判定すると、
表示 1 回で無関係な拒否が大量に並ぶので、**判定するのは装備している Tool と、権限表に名前で
書かれた Tool だけ**にする（`_candidates`）。名前でない対象（`tool:*` などのパターン）が 1 行でも
あれば、照合は判定器にしか解けないので全種を判定する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.kernel.enums import PersonaKey, ToolName
from settings.schemas import (
    PolicyGrant,
    RoleStageView,
    RolesView,
    RoleToolView,
    RoleView,
    StageToolsView,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from common.interfaces.settings import Member, TaskTemplate
    from settings.models import AreaAData
    from settings.service import SettingsService

TOOL_OBJECT_PREFIX = "tool:"
"""Tool の許可の対象の接頭辞（`policy.csv` の `p, <主体>, tool:<名前>, execute`）。"""

EXECUTE_ACT = "execute"
"""Tool の許可の動作。"""

_POLICY = "p"
_GROUPING = "g"
_P_COLUMNS = 4
_G_COLUMNS = 3


def build_roles_view(service: SettingsService, data: AreaAData, project_key: str) -> RolesView:
    """PJ のメンバ全員の実効設定を組む（PJ の実在は呼び手が確かめておく）。

    Args:
        service: 重ね合わせ・判定を引く読取側（`resolve`・`get_tasks`・`enforce`）。
        data: 領域 A の生の構造（メンバの一覧と権限表の行を引く）。
        project_key: 登録済みのプロジェクトキー。

    Returns:
        メンバを `PersonaKey` の列挙順に並べた一覧と、担当未指定の段の装備。
    """
    present = data.agents_raw.get(project_key) or {}
    templates = service.get_tasks(project_key).templates
    rows = data.policy_rows.get(project_key, ())
    roles = [
        _role_view(service, service.resolve(project_key, persona), templates, rows)
        for persona in PersonaKey
        if persona.value in present
    ]
    stage_tools = [
        StageToolsView(stage=stage, tools=list(template.tools))
        for stage, template in templates.items()
        if template.agent is None and template.tools is not None
    ]
    return RolesView(project_key=project_key, roles=roles, stage_tools=stage_tools)


def _role_view(
    service: SettingsService,
    member: Member,
    templates: Mapping[str, TaskTemplate],
    rows: Iterable[tuple[str, ...]],
) -> RoleView:
    """メンバ 1 人の実効設定。

    Args:
        service: 許可の判定に使う読取側。
        member: 重ね合わせ・解決済みのメンバ。
        templates: 重ね合わせ済みの段テンプレート（7 キー）。
        rows: PJ の権限表の行（先頭要素が区分）。

    Returns:
        装備と許可の対照・2 段目の許可・既定担当の段。
    """
    subject = member.casbin_subject
    listed = tuple(rows)
    holders = _holders(subject, listed)
    verdicts: dict[ToolName, bool] = {}

    def allowed(tool: ToolName) -> bool:
        """`tool:<名前>` の `execute` が許されるか（判定器の結果。1 Tool につき 1 回だけ判定）。"""
        if tool not in verdicts:
            verdicts[tool] = service.enforce(
                subject, f"{TOOL_OBJECT_PREFIX}{tool.value}", EXECUTE_ACT
            )
        return verdicts[tool]

    equipped = set(member.tools)
    tools = [
        RoleToolView(tool=tool, equipped=tool in equipped, allowed=allowed(tool))
        for tool in _candidates(equipped, holders, listed)
    ]
    tools = [row for row in tools if row.equipped or row.allowed]
    stages = [
        RoleStageView(
            stage=stage,
            replaces_tools=template.tools is not None,
            tools=[
                RoleToolView(tool=tool, equipped=True, allowed=allowed(tool))
                for tool in template.tools or ()
            ],
        )
        for stage, template in templates.items()
        if template.agent is member.persona_key
    ]
    return RoleView(
        persona_key=member.persona_key,
        member_id=member.member_id,
        casbin_subject=subject,
        role=member.role,
        goal=member.goal,
        llm_profile=member.llm_profile.name,
        stages=stages,
        tools=tools,
        grants=_grants(holders, listed),
    )


def _candidates(
    equipped: set[ToolName], holders: set[str], rows: tuple[tuple[str, ...], ...]
) -> list[ToolName]:
    """判定に掛ける Tool（装備 ＋ 権限表に名前で書かれたもの。パターンがあれば全種）。

    Args:
        equipped: 装備している Tool。
        holders: 主体と、`g` 行で継ぐ役割。
        rows: PJ の権限表の行（先頭要素が区分）。

    Returns:
        `ToolName` の列挙順の候補。
    """
    named: set[ToolName] = set()
    for row in _policy_rows(holders, rows):
        if not row[2].startswith(TOOL_OBJECT_PREFIX):
            continue
        try:
            named.add(ToolName(row[2].removeprefix(TOOL_OBJECT_PREFIX)))
        except ValueError:
            return list(ToolName)
    return [tool for tool in ToolName if tool in equipped or tool in named]


def _policy_rows(holders: set[str], rows: tuple[tuple[str, ...], ...]) -> list[tuple[str, ...]]:
    """主体またはその継ぐ役割に当たる `p` 行（記載順）。"""
    return [
        row for row in rows if row[0] == _POLICY and len(row) >= _P_COLUMNS and row[1] in holders
    ]


def _grants(holders: set[str], rows: tuple[tuple[str, ...], ...]) -> list[PolicyGrant]:
    """`tool:` 以外の `p` 行のうち、主体またはその継ぐ役割（`g` 行を辿る）に当たるもの。

    Args:
        holders: 主体と、`g` 行で継ぐ役割。
        rows: PJ の権限表の行（先頭要素が区分）。

    Returns:
        記載順の許可（重複は 1 つにまとめる）。
    """
    grants: list[PolicyGrant] = []
    for row in _policy_rows(holders, rows):
        grant = PolicyGrant(obj=row[2], act=row[3])
        if not row[2].startswith(TOOL_OBJECT_PREFIX) and grant not in grants:
            grants.append(grant)
    return grants


def _holders(subject: str, rows: tuple[tuple[str, ...], ...]) -> set[str]:
    """主体と、`g` 行で（推移的に）継ぐ役割の集合。"""
    holders = {subject}
    frontier = [subject]
    while frontier:
        current = frontier.pop()
        for row in rows:
            if row[0] == _GROUPING and len(row) >= _G_COLUMNS and row[1] == current:
                role = row[2]
                if role not in holders:
                    holders.add(role)
                    frontier.append(role)
    return holders
