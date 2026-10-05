"""US5: HTTP 境界の型（`schemas.py`）が塞ぐ 3 つの壊れ方。data-model §2-6・SC-026・FR-079a。

**B-16**（`project.yaml`・`llm_profiles.yaml`・`limits.yaml` の未知キー／必須キー欠け）・
**B-17**（`hooks.yaml` のトップレベルキーの綴り違い）・**DIFF-18**（権限表の行の種別が
`p`／`g` 以外）が **3 通りとも 422** で拒否され、**いずれも領域 A に到達しない**。

⚠ **起動時検査の規則は 1 つも足さない**ので、**人が直接編集した領域 A に残る同じ穴は 4 件とも
開いたまま**である（010 SD-10）。型で防げない **B-18**（他 PJ のメンバを主体にした権限行）は
`test_settings_ai_config.py` が「書けて保存も通り永久に効かない」ことを固定している（B-46）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from settings_testing import (
    StubBoardReader,
    StubVaultReader,
    area_bytes,
    make_client,
    make_crud,
    write_minimal_area_a,
)

from settings.schemas import HooksConfigSchema, LimitSpec, LlmProfileSpec, PolicyRow, ProjectSpec

if TYPE_CHECKING:
    from pathlib import Path

HTTP_UNPROCESSABLE = 422

# -- B-16: 未知キー／必須キー欠け（`extra="forbid"` ＋ 必須フィールド） -------------------

UNKNOWN_KEY_CASES: tuple[tuple[str, type[BaseModel], dict[str, object]], ...] = (
    (
        "project_extra",
        ProjectSpec,
        {
            "project_key": "k",
            "name": "n",
            "folders": {"pj_input_dir": "a", "role_dirs": {}, "outcome_dir": "c"},
            "nickname": "未知",
        },
    ),
    (
        "profile_extra",
        LlmProfileSpec,
        {
            "name": "p",
            "provider_kind": "openai_compatible",
            "base_url": "u",
            "model_id": "m",
            "context_window": 100,
            "api_key_ref": "R",
            "typo_field": 1,
        },
    ),
    (
        "limit_extra",
        LimitSpec,
        {"max_requests_per_kickoff": 1, "max_cost_per_kickoff": 1.0, "x": 1},
    ),
    (
        "repos_extra",
        ProjectSpec,
        {
            "project_key": "k",
            "name": "n",
            "folders": {"pj_input_dir": "a", "role_dirs": {}, "outcome_dir": "c"},
            "repos": {"remote_url": "u", "remote": "綴り違い"},
        },
    ),
)

MISSING_KEY_CASES: tuple[tuple[str, type[BaseModel], dict[str, object]], ...] = (
    ("project_missing_folders", ProjectSpec, {"project_key": "k", "name": "n"}),
    ("profile_missing_model_id", LlmProfileSpec, {"name": "p", "provider_kind": "openrouter"}),
    ("limit_missing_cost", LimitSpec, {"max_requests_per_kickoff": 1}),
)


@pytest.mark.parametrize(
    ("label", "model", "body"), UNKNOWN_KEY_CASES, ids=[case[0] for case in UNKNOWN_KEY_CASES]
)
def test_b16_unknown_keys_are_refused(
    label: str, model: type[BaseModel], body: dict[str, object]
) -> None:
    """B-16 (a)・FR-079a: 未知の項目名は `extra="forbid"` が締め出す。"""
    del label
    with pytest.raises(ValueError, match="[Ee]xtra"):
        model.model_validate(body)


@pytest.mark.parametrize(
    ("label", "model", "body"), MISSING_KEY_CASES, ids=[case[0] for case in MISSING_KEY_CASES]
)
def test_b16_missing_required_keys_are_refused(
    label: str, model: type[BaseModel], body: dict[str, object]
) -> None:
    """B-16 (b)・FR-079a: 必須の項目の欠けは型が締め出す。"""
    del label
    with pytest.raises(ValueError, match="[Ff]ield required"):
        model.model_validate(body)


def test_repos_is_optional_on_the_project_spec() -> None:
    """CFG-74・運用要件: `repos` は**任意**（省略・`null`・両方ありの 3 形が通る）。"""
    base: dict[str, object] = {
        "project_key": "k",
        "name": "n",
        "folders": {"pj_input_dir": "a", "role_dirs": {}, "outcome_dir": "c"},
    }

    assert ProjectSpec.model_validate(base).repos is None
    assert ProjectSpec.model_validate({**base, "repos": None}).repos is None

    filled = ProjectSpec.model_validate(
        {**base, "repos": {"remote_url": "https://example.com/r.git", "local_path": "/r"}}
    )
    assert filled.repos is not None
    assert filled.repos.remote_url == "https://example.com/r.git"
    assert filled.repos.local_path == "/r"


def test_b17_a_hooks_top_level_typo_is_refused() -> None:
    """B-17: `hooks.yaml` のトップレベルキーの綴り違い（`hook` など）を型が締め出す。"""
    with pytest.raises(ValueError, match="[Ee]xtra"):
        HooksConfigSchema.model_validate({"hook": []})
    with pytest.raises(ValueError, match="[Ff]ield required"):
        HooksConfigSchema.model_validate({})


def test_diff18_a_row_type_outside_p_and_g_is_refused() -> None:
    """DIFF-18: 権限表の行の種別が `p`／`g` 以外の形は列挙が締め出す。"""
    with pytest.raises(ValueError, match="'p' or 'g'"):
        PolicyRow.model_validate({"ptype": "q", "fields": ["a", "b"]})


def test_the_threshold_is_lower_case(settings_root: Path) -> None:
    """A-10・B-43: `threshold_t` は**小文字**で返る（正本の `threshold_T` に揃えない）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader())

    payload = crud.get_llm_profile("default").model_dump()

    assert "threshold_t" in payload
    assert "threshold_T" not in payload


# -- SC-026: 3 通りとも 422 で、いずれも領域 A に到達しない ------------------------------


@pytest.mark.parametrize(
    ("label", "path", "body"),
    (
        (
            "b16_unknown_key",
            "/settings/projects",
            {
                "project_key": "newpj",
                "name": "n",
                "folders": {"pj_input_dir": "a", "role_dirs": {}, "outcome_dir": "c"},
                "nickname": "未知",
            },
        ),
        ("b17_hooks_typo", "/settings/hooks/pj", {"hook": []}),
        ("diff18_row_type", "/settings/policies/pj", {"rows": [{"ptype": "q", "fields": ["a"]}]}),
    ),
    ids=["b16_unknown_key", "b17_hooks_typo", "diff18_row_type"],
)
def test_sc026_all_three_are_422_and_never_reach_area_a(
    label: str, path: str, body: dict[str, object], settings_root: Path
) -> None:
    """SC-026・FR-079a: 3 通りとも **422**、かつ**領域 A のバイト列が変わらない**。"""
    del label
    write_minimal_area_a(settings_root)
    client = make_client(make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader()))
    before = area_bytes(settings_root)

    response = client.request("POST" if path == "/settings/projects" else "PUT", path, json=body)

    assert response.status_code == HTTP_UNPROCESSABLE
    assert area_bytes(settings_root) == before, "口の型で落ちたのに領域 A に届いている"
