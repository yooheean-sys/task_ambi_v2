"""違反の生成と、検査に共通の小道具（`validate/` の土台。他の規則モジュールが import する）。

`kind` の語彙は `p5r1_notes.md` §5 の **13 語から増やさない**（FR-046）。`where` は領域 A ルート
相対のパス ＋ キーで、**絶対パスを載せない**（FR-051）。`reason` に**秘密の実値を載せない**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from settings.models import SECRET_KEY_WORDS, ProblemKind, ValidationProblem

if TYPE_CHECKING:
    from collections.abc import Mapping

HOOK_POINTS: frozenset[str] = frozenset({"PRE_TOOL_CALL", "POST_TOOL_CALL"})
"""`hooks.yaml` の `points` の許可値（契約 `HookRef.points` の `Literal` と同じ）。"""

MCP_FIELDS: tuple[str, ...] = ("name", "transport", "command", "args")
"""`mcps` の宣言に揃っているべき項目（V-18。**接続しない**＝ REC-02）。"""

VAULT_PREFIX = "vault:"
CMD_PREFIX = "cmd:"


def problem(where: str, kind: ProblemKind, reason: str) -> ValidationProblem:
    """違反 1 件を組み立てる。"""
    return ValidationProblem(where=where, kind=kind, reason=reason)


def as_mapping(value: object) -> Mapping[str, object] | None:
    """Mapping として読めるなら写す（生の YAML の narrow をここ 1 箇所に閉じる）。"""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def str_items(value: object) -> list[str] | None:
    """`list[str]` として読めるなら写す。読めなければ `None`（呼び手が違反にする）。"""
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for item in cast("list[object]", value):
        if not isinstance(item, str):
            return None
        items.append(item)
    return items


# --------------------------------------------------------------------------------------
# 共通: 秘密語（V-06）
# --------------------------------------------------------------------------------------


def check_secret_keys(value: object, where: str, *, sep: str = ":") -> list[ValidationProblem]:
    """V-06 秘密語 4 の**キー名**が現れないこと（FR-050）。入れ子まで再帰する。

    判定は**完全一致**である。部分一致にすると `api_key_ref`（FR-049 が定める参照名）と
    `context_reserve_tokens`（CFG-37）が違反になり、規則そのものが成立しない。

    `where` の区切りは他の 27 規則と同じく「**1 段目は `:`、以降は `.`**」（data-model §4 の
    例示 `global/agents.yaml:planner.tools`）。`sep` は再帰の内部で `.` に切り替わるだけで、
    呼び手はファイルの相対パスだけを渡す。
    """
    found: list[ValidationProblem] = []
    if isinstance(value, dict):
        for key, item in cast("dict[object, object]", value).items():
            name = str(key)
            spot = f"{where}{sep}{name}"
            if name.lower() in SECRET_KEY_WORDS:
                found.append(problem(spot, "forbidden_key", "秘密語のキーは領域 A に置けない"))
            found.extend(check_secret_keys(item, spot, sep="."))
    elif isinstance(value, list):
        for index, item in enumerate(cast("list[object]", value)):
            found.extend(check_secret_keys(item, f"{where}[{index}]", sep=sep))
    return found
