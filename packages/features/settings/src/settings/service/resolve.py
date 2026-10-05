"""解決規則の純粋関数（contracts/area-a-loader.md §6・R-01）。

**build（`loader.py`）と実行時（`service/__init__.py`）の双方が呼ぶ唯一の規則の出所**である。
FR-043 の「各メンバの LLM が解決順の終端まで届く」検査は merge 済みの値にしか掛けられない
ため、検査する値と返す値を別経路で作らない（構造として一致を保証する）。

**ファイルシステムにも構造化ログにも触れない**（引数と戻りだけで完結する）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from common.interfaces.settings import LimitPolicy, LLMProfile
from settings.models import (
    MEMBER_ID_SEPARATOR,
    SCOPE_GLOBAL,
    SUBJECT_PREFIX,
    LimitsRecord,
    LlmProfileRecord,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from common.kernel.enums import PersonaKey


def normalize_mapping(value: object) -> dict[str, object]:
    """`None`（空ファイル・値なし）を `{}` に正規化する（R-13・M-13）。

    `programmer:`（値なし）は `{}` ではなく `None` になり、空ファイルも `None` になる。
    D16-d の「空 dict 可＝ global 既定のまま参加」（US1 AC-6）を成立させるには、ここで
    同じに扱うのが素直である。**トップレベルが dict でない場合は正規化しない**
    （構文誤りとして (d2) で中断する＝ `repository.load_yaml_mapping`）。
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        return {str(key): item for key, item in mapping.items()}
    return {}


def merge_agent_fields(
    base: Mapping[str, object] | None, override: Mapping[str, object] | None
) -> dict[str, object]:
    """`agents.yaml` の ④→⑤ フィールド単位 merge（D16-d・FR-030）。

    global 側を土台に、PJ 側に**在るフィールドだけ**を上書きする。**リスト型（`tools`・
    `skills`・`mcps`）は丸ごと置換**する（和集合にしない＝ PJ で装備を絞れる）。
    `dict.update` はこの 2 つを同時に満たす。

    **キーが在れば値が `None` でも上書きとして扱う**（「在るフィールド」の literal な読み）。
    PJ 側で `tools:` と書けば装備を空に絞れる。ペルソナの項目そのものが空
    （`programmer:` や `{}`）なら、そこにキーが 1 つも無いので既定のまま残る（US1 AC-6）。
    """
    merged: dict[str, object] = dict(base or {})
    merged.update(override or {})
    return merged


def resolve_llm_name(merged: Mapping[str, object], default_profile: str | None) -> str | None:
    """`llm` を **PJ → global → `llm_profiles.yaml` の `default`** の順で解決する（D16-h (1)）。

    PJ → global の段は `merge_agent_fields` が済ませてある。ここが担うのは終端の `default` で、
    **どれにも届かないメンバだけ**が `llm_ref` の違反になる（メンバ 0 件なら違反 0 件＝ R-17）。
    """
    value = merged.get("llm")
    if isinstance(value, str) and value:
        return value
    return default_profile


def derive_threshold_t(context_window: int, context_reserve_tokens: int) -> int:
    """送信前の閾値 `threshold_t = context_window − 予備`（P5D-013・CFG-37）。

    **綴りは現物の契約が正**（`LLMProfile.threshold_t` は小文字＝ DIFF-07）。0 以下になる
    プロファイルは V-16 が起動時に落とす。
    """
    return context_window - context_reserve_tokens


def resolve_profile(record: LlmProfileRecord, context_reserve_tokens: int) -> LLMProfile:
    """プロファイルの写像に `threshold_t` を導出して載せる。**実値は運ばない**（FR-049）。"""
    return LLMProfile(
        name=record.name,
        provider_kind=record.provider_kind,
        base_url=record.base_url,
        model_id=record.model_id,
        context_window=record.context_window,
        api_key_ref=record.api_key_ref,
        extra_headers=dict(record.extra_headers),
        threshold_t=derive_threshold_t(record.context_window, context_reserve_tokens),
    )


def member_id_of(project_key: str, persona_key: PersonaKey) -> str:
    """メンバ ID `<project_key>.<persona_key>`（D06-b）。"""
    return f"{project_key}{MEMBER_ID_SEPARATOR}{persona_key.value}"


def bd_actor_of(member_id: str) -> str:
    """盤面上の行為者名＝**メンバ ID そのまま**（D21-a が実測で確定。FR-037）。"""
    return member_id


def casbin_subject_of(member_id: str) -> str:
    """認可の主体名＝ `member:` ＋ メンバ ID（FR-037・FN-23）。"""
    return f"{SUBJECT_PREFIX}{member_id}"


def resolve_limits_global(
    *, default_requests: int, default_cost: float, record: LimitsRecord | None
) -> LimitPolicy:
    """上限の ①→③→④ を重ねる（CFG-39・D06-g (2)）。

    ③ はモジュール設定の出荷時既定、④ は `global/limits.yaml` の `global`。**`limits.yaml`
    不在なら ③ をそのまま使う**（settings-F04 が明示している唯一の既定）。`scope` は
    全体を表す固定文字列（R-18）。
    """
    values = record.global_ if record is not None else None
    if values is None:
        return LimitPolicy(
            scope=SCOPE_GLOBAL,
            max_requests_per_kickoff=default_requests,
            max_cost_per_kickoff=default_cost,
        )
    return LimitPolicy(
        scope=SCOPE_GLOBAL,
        max_requests_per_kickoff=values.max_requests_per_kickoff,
        max_cost_per_kickoff=values.max_cost_per_kickoff,
    )


def human_actor_names(humans_raw: Mapping[str, object]) -> list[str]:
    """`humans.yaml` の `human_actors` から**文字列だけ**拾う（P5D-019）。

    **V-20（検査）と写像の双方がこの 1 経路だけを使う**（R-01）。検査側が「1 件でも非文字列が
    あればリスト全体を捨てる」読み方をすると、衝突が違反 0 件のままスナップショットに残る。
    非文字列の要素そのものは `malformed_human_actors` が違反にする。
    """
    raw = humans_raw.get("human_actors")
    if not isinstance(raw, list):
        return []
    return [item for item in cast("list[object]", raw) if isinstance(item, str)]


def malformed_human_actors(humans_raw: Mapping[str, object]) -> list[int] | None:
    """`human_actors` の読めない箇所を返す（`None`＝リストですらない。`[]`＝正常）。

    不在（キーが無い）は正常＝ `[]`（空の宣言は人間 0 名＝ US5 AC-5）。
    """
    raw = humans_raw.get("human_actors")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    return [
        index for index, item in enumerate(cast("list[object]", raw)) if not isinstance(item, str)
    ]


def resolve_limits_overrides(record: LimitsRecord | None) -> dict[str, LimitPolicy]:
    """上限の ⑤（`overrides[project_key]`）を写す。`scope` は当該プロジェクトキー（R-18）。

    **段・ペルソナ単位の粒度を持たない**（D06-g (2)）。
    """
    if record is None:
        return {}
    return {
        key: LimitPolicy(
            scope=key,
            max_requests_per_kickoff=values.max_requests_per_kickoff,
            max_cost_per_kickoff=values.max_cost_per_kickoff,
        )
        for key, values in record.overrides.items()
    }
