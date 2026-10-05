"""コマンド突合の入口（F20）。V-24〜V-26。contracts/validation-rules.md §4。

**死に定義（V-25）も同じ重さで起動を中断する**（FR-047・clarify 裁定）。違反に 2 種の重さを
持ち込むと `ValidationProblem` に §5 の語彙に無い属性が要り、「起動は通ったが保存できない
設定」という第 3 の状態も残る。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from settings.service.resolve import normalize_mapping
from settings.service.validate.problems import CMD_PREFIX, check_secret_keys, problem, str_items

if TYPE_CHECKING:
    from settings.models import AreaAData, ValidationProblem


def validate_commands(data: AreaAData) -> list[ValidationProblem]:
    """コマンド突合の検査（F20）。V-24〜V-26。

    **死に定義（V-25）も同じ重さで起動を中断する**（FR-047・clarify 裁定）。違反に 2 種の
    重さを持ち込むと `ValidationProblem` に §5 の語彙に無い属性が要る。
    """
    where = "global/commands.yaml"
    defined = normalize_mapping(data.commands_raw)
    # V-06 は**ファイルの生トップレベル**から走査する（FR-050。`_check_profiles` と同型）。
    problems: list[ValidationProblem] = list(check_secret_keys(data.commands_file_raw, where))
    referenced: set[str] = set()
    for key in sorted(data.policy_rows):
        policy_where = f"projects/{key}/policy.csv"
        for row in data.policy_rows[key]:
            if row[0] != "p" or len(row) < 3 or not row[2].startswith(CMD_PREFIX):
                continue
            name = row[2][len(CMD_PREFIX) :]
            referenced.add(name)
            if name not in defined:
                problems.append(problem(policy_where, "cmd_ref", f"定義に無いコマンド名: {name}"))
    problems.extend(
        problem(f"{where}:{name}", "cmd_ref", f"policy から 1 行も参照されない死に定義: {name}")
        for name in sorted(set(defined) - referenced)
    )
    for name, entry in sorted(defined.items()):
        problems.extend(_check_argv(name, entry, f"{where}:{name}"))
    return problems


def _check_argv(name: str, entry: object, spot: str) -> list[ValidationProblem]:
    """V-26 `argv` が**空でない文字列のリスト**である（US2 AC-9。実行時に初めて壊れない）。"""
    fields = normalize_mapping(entry)
    if not fields:
        return [problem(spot, "cmd_ref", f"コマンド定義が空: {name}")]
    argv = str_items(fields.get("argv"))
    if argv is None:
        return [problem(f"{spot}.argv", "cmd_ref", "argv は文字列の一覧で書く")]
    if not argv:
        return [problem(f"{spot}.argv", "cmd_ref", "argv が空")]
    return []
