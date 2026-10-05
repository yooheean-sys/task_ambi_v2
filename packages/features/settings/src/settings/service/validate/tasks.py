"""`tasks.yaml` 1 ファイル分の検査（V-08〜V-13）。

**V-10 は 2 本に分ける**（裁定 2026-08-24・CHK070）。V-10a は必須フィールドの**欠落**
（`var_required`。`p5r1_notes.md` §5 の語義そのもの）、V-10b は値の `str` 性
（`unknown_field`。実測 M-15 (b) の「失敗せずに型が変わる」経路を塞ぐ）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.kernel.enums import PersonaKey
from settings.models import (
    ALLOWED_VARS,
    REQUIRED_VARS,
    TASK_FIELDS,
    TASK_KEYS,
    TASK_REQUIRED_FIELDS,
)
from settings.service.resolve import normalize_mapping
from settings.service.validate.agents import check_tool_names
from settings.service.validate.problems import check_secret_keys, problem
from settings.service.validate.vars import extract_variables

if TYPE_CHECKING:
    from collections.abc import Mapping

    from settings.models import ValidationProblem


def check_tasks_mapping(
    raw: Mapping[str, object], *, where: str, require_all_keys: bool
) -> list[ValidationProblem]:
    """`tasks.yaml` 1 ファイル分の検査（V-08〜V-13）。

    Args:
        raw: ファイルのトップレベル mapping（キー＝段の 7 種）。
        where: 領域 A ルート相対のファイルパス。
        require_all_keys: 7 キーを必須にするか（global のみ真。PJ 上書きは任意）。
    """
    problems: list[ValidationProblem] = list(check_secret_keys(raw, where))
    for key in raw:
        if key not in TASK_KEYS:
            problems.append(problem(f"{where}:{key}", "var_required", f"7 種の外の段キー: {key}"))
    if require_all_keys:
        problems.extend(
            problem(f"{where}:{key}", "var_required", f"共通定義に段が無い: {key}")
            for key in TASK_KEYS
            if key not in raw
        )
    for key, entry in raw.items():
        if key not in TASK_KEYS:
            continue
        problems.extend(_check_task_entry(key, normalize_mapping(entry), f"{where}:{key}"))
    return problems


def _check_task_entry(key: str, fields: Mapping[str, object], spot: str) -> list[ValidationProblem]:
    """1 段分の検査（V-09〜V-13）。"""
    problems: list[ValidationProblem] = []
    for name, value in fields.items():
        if name not in TASK_FIELDS:
            problems.append(
                problem(f"{spot}.{name}", "unknown_field", f"許可 4 の外のフィールド: {name}")
            )
            continue
        if name == "agent":
            problems.extend(_check_task_agent(value, f"{spot}.agent"))
        if name == "tools":
            problems.extend(check_tool_names(value, f"{spot}.tools"))
    problems.extend(_check_task_required_fields(fields, spot))
    problems.extend(_check_task_variables(key, fields, spot))
    return problems


def _check_task_agent(value: object, spot: str) -> list[ValidationProblem]:
    """V-11 `agent:` が `PersonaKey` に在る（**段との整合は検査しない**＝ D16-f）。"""
    if value is None:
        return []
    if not isinstance(value, str) or value not in PersonaKey.__members__:
        return [problem(spot, "agent_ref", f"共有語彙に無いペルソナ: {value!r}")]
    return []


def _check_task_required_fields(fields: Mapping[str, object], spot: str) -> list[ValidationProblem]:
    """V-10a 必須フィールドの**欠落**（`var_required`）と V-10b 値の `str` 性（`unknown_field`）。

    2 本に分けるのは `p5r1_notes.md` §5 が「必須フィールド欠落」を `var_required` と明記して
    いるためである（裁定 2026-08-24・CHK070）。V-10b は実測 M-15 (b) の
    「`description: {title}` が**失敗せずに** `{'title': None}` へ化ける」経路を塞ぐ。
    """
    problems: list[ValidationProblem] = []
    for name in TASK_REQUIRED_FIELDS:
        if name not in fields:
            problems.append(
                problem(f"{spot}.{name}", "var_required", f"必須フィールドが無い: {name}")
            )
            continue
        if not isinstance(fields[name], str):
            problems.append(
                problem(
                    f"{spot}.{name}",
                    "unknown_field",
                    f"{name} の値が文字列でない（本文はブロックスカラー `|` で書く＝ Y-6）",
                )
            )
    return problems


def _check_task_variables(
    key: str, fields: Mapping[str, object], spot: str
) -> list[ValidationProblem]:
    """V-12（許可 17 の内側）と V-13（段別必須変数）。

    段別必須は `description` と `expected_output` を**連結した集合**に対する部分集合判定で
    行う（D16-c・D16-f。`{acceptance}` がどちらに書かれても要求は満たされる）。
    許可集合は D16-c の 15 種に D24 の 2 種（`repository_remote_url`・`repository_local_path`）
    を足した 17 種（`ALLOWED_VARS` が正本）。
    """
    problems: list[ValidationProblem] = []
    found: set[str] = set()
    for name in TASK_REQUIRED_FIELDS:
        text = fields.get(name)
        if not isinstance(text, str):
            continue
        names, issues = extract_variables(text)
        found |= names
        problems.extend(problem(f"{spot}.{name}", "var_unknown", issue) for issue in issues)
        problems.extend(
            problem(f"{spot}.{name}", "var_unknown", f"許可 17 の外の変数: {{{item}}}")
            for item in sorted(names - ALLOWED_VARS)
        )
    missing = REQUIRED_VARS.get(key, frozenset()) - found
    problems.extend(
        problem(spot, "var_required", f"段別の必須変数が無い: {{{item}}}")
        for item in sorted(missing)
    )
    return problems
