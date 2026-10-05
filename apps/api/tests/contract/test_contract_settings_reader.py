"""settings 提供の `SettingsReader`（IF-23〜26・IF-50）の適合検証。US3。

一時の設定ファイル領域 A の**実物**に対して、5 メソッドが台帳どおりの型を返し、
未登録の project_key で `ProjectNotFound` を投げることを確かめる。

IF-24（`get_limits`）は台帳の例外欄が `—` なので、**正常入力で例外が出ないことを素直に
呼んで戻り値の型を主張する**形で満たす（FR-018・R-17）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from contract_stores import (
    AREA_A_HOOK_AGENTS,
    AREA_A_HOOK_NAME,
    AREA_A_HOOK_POINTS,
    AREA_A_LIMITS_DEFAULT_COST,
    AREA_A_LIMITS_DEFAULT_REQUESTS,
    AREA_A_LIMITS_GLOBAL_COST,
    AREA_A_LIMITS_GLOBAL_REQUESTS,
    AREA_A_LIMITS_PJ_COST,
    AREA_A_LIMITS_PJ_REQUESTS,
    AREA_A_TASK_OVERRIDE_KEY,
    AREA_A_TASK_PJ_DESCRIPTION,
    AREA_A_TASK_PJ_EXPECTED_OUTPUT,
)

from common.interfaces.settings import (
    HookRef,
    HooksConfig,
    LimitPolicy,
    Project,
    ProjectNotFound,
    Rules,
    TaskTemplates,
)

if TYPE_CHECKING:
    from contract_stores import TempAreaA

    from common.interfaces.settings import SettingsReader

UNREGISTERED_PROJECT = "no-such-project"
"""領域 A に**未登録**の project_key（`ProjectNotFound` を踏む）。"""


@pytest.mark.contract_if("IF-23")
def test_contract_settings_get_project_returns_project(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-23 正常系: DM-01/02 —— フォルダ対応付けと outcome 出力先を持つ `Project`。"""
    project = settings_reader.get_project(area_a.project_key)

    assert isinstance(project, Project)
    assert project.project_key == area_a.project_key
    assert project.folders.pj_input_dir == "pj"
    assert project.folders.outcome_dir == "pj/outcome"
    assert project.folders.role_dirs[area_a.persona] == f"pj/roles/{area_a.persona}"
    assert project.is_reserved is False


@pytest.mark.contract_if("IF-23")
def test_contract_settings_get_project_raises_project_not_found(
    settings_reader: SettingsReader,
) -> None:
    """IF-23 例外系: 未登録の project_key で `ProjectNotFound`（`None` に倒さない）。"""
    with pytest.raises(ProjectNotFound):
        settings_reader.get_project(UNREGISTERED_PROJECT)


@pytest.mark.contract_if("IF-24")
def test_contract_settings_get_limits_returns_limit_policy(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-24: DM-12 の `LimitPolicy` が返る（例外欄が `—`＝ FR-018）。

    型と、両方の上限が正の値であることを見る。**どの段が効いたか**の主張は下の
    `..._resolves_the_project_override` が持つ。
    """
    limits = settings_reader.get_limits(area_a.project_key)

    assert isinstance(limits, LimitPolicy)
    assert limits.max_requests_per_kickoff > 0
    assert limits.max_cost_per_kickoff > 0


@pytest.mark.contract_if("IF-24")
def test_contract_settings_get_limits_resolves_the_project_override(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-24: 台帳の出力欄が言う「global ＋ PJ 上書き**解決済み**」を踏む（FR-016）。

    一時領域 A は CFG-39 の 3 段すべてを**区別できる値**で持つ ——
    ③ モジュール設定の出荷時既定・④ `global/limits.yaml` の `global:`・
    ⑤ 同ファイルの `overrides[<pj>]`。登録済みの PJ には ⑤ が返り、`scope` が当該キーになる。
    それ以外の（`overrides` に無い）キーには ④ が返り、`scope` は全体を表す固定文字列になる。

    3 段が同じ値だと「解決済み」を主張したことにならないので、③ と一致しないことも見る。
    """
    resolved = settings_reader.get_limits(area_a.project_key)

    assert resolved.scope == area_a.project_key
    assert resolved.max_requests_per_kickoff == AREA_A_LIMITS_PJ_REQUESTS
    assert resolved.max_cost_per_kickoff == AREA_A_LIMITS_PJ_COST
    assert resolved.max_requests_per_kickoff != AREA_A_LIMITS_DEFAULT_REQUESTS
    assert resolved.max_requests_per_kickoff != AREA_A_LIMITS_GLOBAL_REQUESTS

    # IF-24 は 9 メソッドで唯一プロジェクトの実在を見ない（例外欄が `—`）。上書きの無いキー
    # には ④ が返る。
    fallback = settings_reader.get_limits(UNREGISTERED_PROJECT)

    assert fallback.scope != UNREGISTERED_PROJECT
    assert fallback.max_requests_per_kickoff == AREA_A_LIMITS_GLOBAL_REQUESTS
    assert fallback.max_cost_per_kickoff == AREA_A_LIMITS_GLOBAL_COST
    assert fallback.max_cost_per_kickoff != AREA_A_LIMITS_DEFAULT_COST


@pytest.mark.contract_if("IF-25")
def test_contract_settings_get_rules_returns_rules(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-25 正常系: DM-05 —— PJ 共通ルールのみを持つ `Rules`（役割ルールは含まない）。"""
    rules = settings_reader.get_rules(area_a.project_key)

    assert isinstance(rules, Rules)
    assert "PJ 共通ルール" in rules.project_rules


@pytest.mark.contract_if("IF-25")
def test_contract_settings_get_rules_raises_project_not_found(
    settings_reader: SettingsReader,
) -> None:
    """IF-25 例外系: 未登録の project_key で `ProjectNotFound`（空の本文に倒さない）。"""
    with pytest.raises(ProjectNotFound):
        settings_reader.get_rules(UNREGISTERED_PROJECT)


@pytest.mark.contract_if("IF-26")
def test_contract_settings_get_hooks_returns_hooks_config(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-26 正常系: DM-06 の `HooksConfig` が返る。"""
    hooks = settings_reader.get_hooks(area_a.project_key)

    assert isinstance(hooks, HooksConfig)
    assert isinstance(hooks.hooks, list)
    assert all(isinstance(ref, HookRef) for ref in hooks.hooks)


@pytest.mark.contract_if("IF-26")
def test_contract_settings_get_hooks_reads_the_declared_hook_refs(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-26: **宣言された値**が `HookRef` として返る（FR-016）。

    台帳 IF-26 の出力欄は `HooksConfig（DM-06）` だが、現物は宣言が無いプロジェクトでは
    防御的に空の `HooksConfig` へ落ちる（`snap.hooks.get(project_key, _EMPTY_HOOKS)`）。
    したがって領域 A の宣言が空だと、**「読み取った」と「既定に落ちた」を区別できない**
    —— `hooks_raw` を丸ごと捨てる実装でも緑になる。

    一時領域 A の `hooks.yaml` は非空の宣言を 1 件持つ（V-18 の形式検査を通る形）。
    期待値は `contract_stores.py` の定数から取り、テストへ直書きしない。
    """
    hooks = settings_reader.get_hooks(area_a.project_key)

    assert [ref.name for ref in hooks.hooks] == [AREA_A_HOOK_NAME], (
        "宣言した Hook が 1 件も返らない（既定の空へ落ちている）"
    )
    assert hooks.hooks[0].points == AREA_A_HOOK_POINTS
    assert hooks.hooks[0].agents == list(AREA_A_HOOK_AGENTS)


@pytest.mark.contract_if("IF-26")
def test_contract_settings_get_hooks_raises_project_not_found(
    settings_reader: SettingsReader,
) -> None:
    """IF-26 例外系: 未登録の project_key で `ProjectNotFound`（空の設定に倒さない）。"""
    with pytest.raises(ProjectNotFound):
        settings_reader.get_hooks(UNREGISTERED_PROJECT)


@pytest.mark.contract_if("IF-50")
def test_contract_settings_get_tasks_returns_task_templates(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-50 正常系: DM-27 —— 7 キーの段テンプレートと `generation` を持つ `TaskTemplates`。

    P5-R1 D16 で追加された行である（付2 の「49/49」は IF-50 を落とした古い記述＝ B-04）。
    """
    tasks = settings_reader.get_tasks(area_a.project_key)

    assert isinstance(tasks, TaskTemplates)
    assert len(tasks.templates) == 7, sorted(tasks.templates)
    assert tasks.generation >= 0


@pytest.mark.contract_if("IF-50")
def test_contract_settings_get_tasks_applies_the_project_override_per_key(
    settings_reader: SettingsReader, area_a: TempAreaA
) -> None:
    """IF-50: 台帳の出力欄が言う「global → PJ 上書き**適用済み**」を踏む（FR-016）。

    一時領域 A の PJ 側 `tasks.yaml` は **1 キーだけ**を持つ。上書きは**キー単位で丸ごと**で
    あり、フィールド単位の merge はしない（D16-a）ので ——
    (1) 上書きしたキーは `description`・`expected_output` とも PJ の値になる
    (2) 上書きしていない 6 キーは global の値のまま残る（7 キーが揃う）。

    上書きの有無で値が変わらなければ「適用済み」を主張したことにならないので、
    上書きしたキーの本文が他のキーと**異なる**ことまで見る。
    """
    tasks = settings_reader.get_tasks(area_a.project_key)

    overridden = tasks.templates[AREA_A_TASK_OVERRIDE_KEY]
    assert overridden.description == AREA_A_TASK_PJ_DESCRIPTION
    assert overridden.expected_output == AREA_A_TASK_PJ_EXPECTED_OUTPUT

    others = [
        template for key, template in tasks.templates.items() if key != AREA_A_TASK_OVERRIDE_KEY
    ]
    assert len(others) == 6, sorted(tasks.templates)
    assert all(template.description != AREA_A_TASK_PJ_DESCRIPTION for template in others)


@pytest.mark.contract_if("IF-50")
def test_contract_settings_get_tasks_raises_project_not_found(
    settings_reader: SettingsReader,
) -> None:
    """IF-50 例外系: 未登録の project_key で `ProjectNotFound`。"""
    with pytest.raises(ProjectNotFound):
        settings_reader.get_tasks(UNREGISTERED_PROJECT)
