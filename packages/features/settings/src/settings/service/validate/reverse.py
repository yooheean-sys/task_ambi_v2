"""逆方向の入口（F19。孤児ポリシーの検出）。V-21〜V-23。contracts/validation-rules.md §3。

**V-19（メンバ ⊆ policy 主体）と V-21（policy 主体 ⊆ 台帳）は別の検査**であり、片方だけでは
乖離が残る。列数の検査は validate ではなく判定器の構築直後に行う（→ contracts/authorizer.md §3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from settings.service.resolve import casbin_subject_of, member_id_of
from settings.service.validate.forward import merged_members
from settings.service.validate.problems import VAULT_PREFIX, problem

if TYPE_CHECKING:
    from collections.abc import Iterable

    from settings.models import AreaAData, ValidationProblem


def validate_reverse(data: AreaAData) -> list[ValidationProblem]:
    """逆方向の検査（F19。孤児ポリシーの検出）。V-21〜V-23。"""
    problems: list[ValidationProblem] = []
    known = {
        casbin_subject_of(member_id_of(key, persona))
        for key in data.agents_raw
        for persona in merged_members(key, data)
    }
    for key in sorted(data.policy_rows):
        rows = data.policy_rows[key]
        where = f"projects/{key}/policy.csv"
        roles = frozenset(row[2] for row in rows if row[0] == "g" and len(row) >= 3)
        p_subjects = frozenset(row[1] for row in rows if row[0] == "p" and len(row) >= 2)
        problems.extend(_check_orphan_subjects(rows, known, roles, where))
        problems.extend(
            problem(where, "orphan", f"g 行の役割に p 行が 1 つも無い: {role}")
            for role in sorted(roles - p_subjects)
        )
        problems.extend(_check_vault_objects(rows, where))
    return problems


def _check_orphan_subjects(
    rows: Iterable[tuple[str, ...]],
    known: frozenset[str] | set[str],
    roles: frozenset[str],
    where: str,
) -> list[ValidationProblem]:
    """V-21 `p`／`g` 行の**主体**が台帳に居る（役割名は V-22 が見る）。

    引用符は casbin が剥がさないため（M-09）、`p, "member:x", …` の主体は `'"member:x"'` と
    いう文字どおりの値になり、台帳のどれにも一致せず**ここで孤児として落ちる**。
    """
    return [
        problem(where, "orphan", f"台帳に居ない主体: {row[1]}")
        for row in rows
        if len(row) >= 2 and row[1] not in known and row[1] not in roles
    ]


def _check_vault_objects(rows: Iterable[tuple[str, ...]], where: str) -> list[ValidationProblem]:
    """V-23 `vault:` 対象が形式として読める（続く文字列が空でない）。"""
    return [
        problem(where, "orphan", f"vault: 対象の領域名が空: {row[2]}")
        for row in rows
        if row[0] == "p" and len(row) >= 3 and row[2] == VAULT_PREFIX
    ]
