"""US4: AI 設定の残り 5 系統（settings-F13〜F17）。plan (ii)。

検証行: S-17（`global:` を書き出さない）・SC-008（鍵の実値）・SC-013（規則を 2 箇所に書かない）
＋ FR-053〜FR-070。表は**契約からの写し**である（V-01〜V-28 が正本。期待値を発明しない）。

⚠ **`POST /settings/commands` は V-24／V-25 の相互ロックで原理的に到達不能**（**B-40**・
DIFF-17）。**契約を 1 行も変えず規則どおり実装し、この行き止まりをそのまま固定する**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    StubBoardReader,
    StubVaultReader,
    make_crud,
    write_commands,
    write_llm_profiles,
    write_minimal_area_a,
    write_policy,
    write_tasks,
)

from common.interfaces.settings import ProjectNotFound, UnknownCommand
from settings.exceptions import SettingsItemNotFound, SettingsValidationError
from settings.schemas import (
    CommandDefSchema,
    HookRefSchema,
    HooksConfigSchema,
    HumansView,
    LimitSpec,
    LlmProfileSpec,
    PolicyRow,
    PolicySpec,
    TaskTemplatesView,
    TextBody,
)

if TYPE_CHECKING:
    from pathlib import Path

    from settings.service.crud import SettingsCrudService

TWO_PROFILES = """\
profiles:
  default:
    provider_kind: openai_compatible
    base_url: http://127.0.0.1:8080/v1
    model_id: demo-model
    context_window: 32768
    api_key_ref: DEMO_API_KEY_REF
  spare:
    provider_kind: openrouter
    base_url: https://openrouter.ai/api/v1
    model_id: spare-model
    context_window: 16384
    api_key_ref: SPARE_API_KEY_REF
default: default
"""


def _crud(root: Path) -> SettingsCrudService:
    """一時領域 A から CRUD サービスを組む（スタブは既定の素通し）。"""
    return make_crud(root, board=StubBoardReader(), vault=StubVaultReader())


def _profile(name: str, *, window: int = 32768, default: bool = False) -> LlmProfileSpec:
    """LLM プロファイルの本文（**鍵の実値を持たない**＝ `api_key_ref` は参照名）。"""
    return LlmProfileSpec(
        name=name,
        provider_kind="openai_compatible",  # pyright: ignore[reportArgumentType]  # ProviderKind は StrEnum
        base_url="http://127.0.0.1:8080/v1",
        model_id="m",
        context_window=window,
        api_key_ref=f"{name.upper()}_API_KEY_REF",
        is_default=default,
    )


# -- T054 F13 LLM プロファイルの拒否表 --------------------------------------------------


def test_deleting_the_default_profile_is_refused(settings_root: Path) -> None:
    """C-5・010 SD-13: **既定プロファイルの削除は拒否**（`kind` は `llm_ref`）。

    保存時だけの **3 つ目**の規則である —— メンバ 0 件のとき V-14 は違反 0 件（`005` R-17）
    なので、前半の 28 規則に受け皿が無い。
    """
    write_minimal_area_a(settings_root)
    write_llm_profiles(settings_root, TWO_PROFILES)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.delete_llm_profile("default")

    assert [item.kind for item in caught.value.problems] == ["llm_ref"]


def test_deleting_a_referenced_profile_is_refused_by_v15(settings_root: Path) -> None:
    """V-15: メンバが参照中のプロファイルの削除は拒否される（**前半の規則がそのまま効く**）。"""
    write_minimal_area_a(settings_root)
    write_llm_profiles(settings_root, TWO_PROFILES)
    from settings_testing import write_agents  # noqa: PLC0415

    write_agents(settings_root, "pj", {"programmer": " {llm: spare}"})
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.delete_llm_profile("spare")

    assert "llm_ref" in {item.kind for item in caught.value.problems}


def test_a_context_window_at_or_below_the_reserve_is_refused_by_v16(settings_root: Path) -> None:
    """V-16: `context_window ≤ 予備トークン` は拒否（**SD-31 で前倒し済み。再実装しない**）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.create_llm_profile(_profile("tiny", window=10))

    assert "llm_ref" in {item.kind for item in caught.value.problems}


def test_a_profile_never_carries_a_secret_value(settings_root: Path) -> None:
    """FR-053・SC-008: 保存されるのは `api_key_ref`（参照名）だけで**実値を持たない**。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    view, outcome = crud.create_llm_profile(_profile("extra"))

    body = (settings_root / "global/llm_profiles.yaml").read_text(encoding="utf-8")
    assert "EXTRA_API_KEY_REF" in body
    assert "api_key:" not in body
    assert view.threshold_t == 32768 - crud.config.context_reserve_tokens
    assert outcome.generation == 2


def test_setting_a_new_default_moves_the_default_key(settings_root: Path) -> None:
    """`is_default` は **`profiles[<名前>]` ではなくファイルの `default:`** へ写る。"""
    write_minimal_area_a(settings_root)
    write_llm_profiles(settings_root, TWO_PROFILES)
    crud = _crud(settings_root)

    crud.update_llm_profile("spare", _profile("spare", default=True))

    assert crud.list_llm_profiles().default == "spare"
    body = (settings_root / "global/llm_profiles.yaml").read_text(encoding="utf-8")
    assert "is_default" not in body, "写像モデルが受け付けないキーを書いている"


def test_us4_ac1_deleting_a_non_default_profile_actually_removes_it(settings_root: Path) -> None:
    """US4 AC-1 の**前半**: 既定でも参照中でもないプロファイルを消すと**消える**（T125）。

    現物には後半（拒否）の 3 本（既定・V-15 の参照中・404）しか無く、成功経路が 1 度も
    通されていなかった。V-14／V-15 は**メンバ → プロファイル**の一方向で、`llm_profiles.yaml`
    の側に V-25 のような「死んだプロファイル」の規則が無いため、ここは到達可能である
    （**B-51 のコマンド定義とは違う**）。
    """
    write_minimal_area_a(settings_root)
    write_llm_profiles(settings_root, TWO_PROFILES)
    crud = _crud(settings_root)
    assert {item.name for item in crud.list_llm_profiles().profiles} == {"default", "spare"}

    outcome = crud.delete_llm_profile("spare")

    listed = crud.list_llm_profiles()
    assert [item.name for item in listed.profiles] == ["default"]
    assert listed.default == "default", "既定は常に 1 つ（FR-054）"
    assert outcome.generation == 2, "世代はちょうど 1 つ増える（SC-002）"


def test_an_unknown_profile_name_is_not_found(settings_root: Path) -> None:
    """FR-075: 不明なプロファイル名は **404**。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsItemNotFound):
        crud.get_llm_profile("居ない")
    with pytest.raises(SettingsItemNotFound):
        crud.delete_llm_profile("居ない")


# -- T055 F14 上限の拒否表 ＋ S-17 ------------------------------------------------------


def test_zero_or_negative_limits_are_refused_by_the_dto(settings_root: Path) -> None:
    """V-27 と口の型の**二重**（data-model §2-4）: 0・負値は口でも候補でも落ちる。"""
    del settings_root
    with pytest.raises(ValueError, match="greater than or equal"):
        LimitSpec(max_requests_per_kickoff=0, max_cost_per_kickoff=1.0)
    with pytest.raises(ValueError, match="greater than or equal"):
        LimitSpec(max_requests_per_kickoff=1, max_cost_per_kickoff=-1.0)


def test_an_override_for_an_unknown_project_is_not_found(settings_root: Path) -> None:
    """FR-057・C-6: 未登録 PJ の上書きは**口が 404 で落とす**（規則ではない）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(ProjectNotFound):
        crud.put_project_limits(
            "居ない", LimitSpec(max_requests_per_kickoff=1, max_cost_per_kickoff=1.0)
        )
    with pytest.raises(ProjectNotFound):
        crud.delete_project_limits("居ない")


def test_limits_have_no_stage_or_persona_granularity() -> None:
    """FR-056: 段・ペルソナ単位の上限を持たない（`LimitSpec` は 2 項目だけ）。"""
    assert set(LimitSpec.model_fields) == {"max_requests_per_kickoff", "max_cost_per_kickoff"}


def test_s17_a_project_override_never_writes_the_global_key(settings_root: Path) -> None:
    """S-17・FR-056・plan D-l・010 SD-16: **CFG-39 の ③ 段を殺さない**。

    上限ファイルが**不在**の状態から `PUT /settings/limits/{k}` を 1 回通すと、書かれた
    `global/limits.yaml` に **`global` キーが無く**、`get_limits` は**モジュール設定の出荷時
    既定を返し続ける**。
    """
    write_minimal_area_a(settings_root)
    assert not (settings_root / "global/limits.yaml").exists()
    crud = _crud(settings_root)
    defaults = crud.config.limits_defaults

    crud.put_project_limits("pj", LimitSpec(max_requests_per_kickoff=7, max_cost_per_kickoff=0.25))

    body = (settings_root / "global/limits.yaml").read_text(encoding="utf-8")
    assert "global:" not in body, "③ 段を永久に殺す `global:` を書き出している"
    view = crud.get_limits()
    assert view.global_.max_requests_per_kickoff == defaults.max_requests_per_kickoff
    assert view.global_.max_cost_per_kickoff == defaults.max_cost_per_kickoff
    assert view.overrides["pj"].max_requests_per_kickoff == 7


def test_only_the_global_put_writes_the_global_key(settings_root: Path) -> None:
    """S-17 の対: `PUT /settings/limits`（#23）**だけ**が `global:` を書く。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    crud.put_global_limits(LimitSpec(max_requests_per_kickoff=99, max_cost_per_kickoff=2.5))

    body = (settings_root / "global/limits.yaml").read_text(encoding="utf-8")
    assert "global:" in body
    assert crud.get_limits().global_.max_requests_per_kickoff == 99


def test_a_project_override_keeps_an_existing_global_key(settings_root: Path) -> None:
    """`global:` が**既に在る**ときは落とさない（利用者が決めた全体既定を消さない）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)
    crud.put_global_limits(LimitSpec(max_requests_per_kickoff=99, max_cost_per_kickoff=2.5))

    crud.put_project_limits("pj", LimitSpec(max_requests_per_kickoff=7, max_cost_per_kickoff=0.25))

    assert crud.get_limits().global_.max_requests_per_kickoff == 99


# -- T056 F15 コマンド定義の拒否表（**B-40 の行き止まりを固定する**） -------------------


def test_an_empty_or_non_string_argv_is_refused_by_the_dto() -> None:
    """V-26・D07-e: `argv` が空・文字列でない形は口の型が落とす。"""
    with pytest.raises(ValueError, match="at least 1 item"):
        CommandDefSchema(name="c", argv=[])
    with pytest.raises(ValueError, match="valid string"):
        CommandDefSchema.model_validate({"name": "c", "argv": [1]})


def test_b40_adding_a_command_is_always_refused_by_v25(settings_root: Path) -> None:
    """**B-40**・DIFF-17: 死に定義の追加は **V-25** で落ちる（`POST` は**新規名では**到達不能）。

    ⚠ 到達不能なのは**新規名のときだけ**である（実測 2026-08-27 の範囲の訂正）—— 既に定義済みの
    名前を `POST` すると同名の差し替えになり **201** が返る。
    """
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.create_command(CommandDefSchema(name="run_tests", argv=["uv", "run", "pytest"]))

    assert [item.kind for item in caught.value.problems] == ["cmd_ref"]
    assert "死に定義" in caught.value.problems[0].reason


def test_b40_adding_the_referencing_row_first_is_refused_by_v24(settings_root: Path) -> None:
    """**B-40** の相互ロックの片側: 参照行だけの候補は **V-24** で落ちる。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.put_policy(
            "pj",
            PolicySpec(
                rows=[
                    PolicyRow(
                        ptype="p", fields=["member:pj.programmer", "tool:read_issue", "execute"]
                    ),
                    PolicyRow(
                        ptype="p", fields=["member:pj.programmer", "cmd:run_tests", "execute"]
                    ),
                ]
            ),
        )

    assert "cmd_ref" in {item.kind for item in caught.value.problems}


def test_deleting_a_referenced_command_is_refused_by_v24(settings_root: Path) -> None:
    """V-24: 権限表から参照されている定義の削除は拒否される。"""
    write_minimal_area_a(settings_root)
    write_commands(settings_root, "commands:\n  run_tests:\n    argv: [uv, run, pytest]\n")
    write_policy(
        settings_root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.programmer, cmd:run_tests, execute",
        ],
    )
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.delete_command("run_tests")

    assert "cmd_ref" in {item.kind for item in caught.value.problems}


def test_b51_removing_the_referencing_row_first_is_refused_by_v25(settings_root: Path) -> None:
    """**B-51** の相互ロックのもう片側: 参照行を先に消す候補は **V-25** で落ちる（T126）。

    直前の `test_deleting_a_referenced_command_is_refused_by_v24` と**対**である —— 定義を先に
    消せば V-24（定義に無いコマンド名）、参照行を先に消せば V-25（死に定義）が発火するので、
    `DELETE /settings/commands/{name}` は **B-40 と同型に到達不能**である。
    """
    write_minimal_area_a(settings_root)
    write_commands(settings_root, "commands:\n  run_tests:\n    argv: [uv, run, pytest]\n")
    write_policy(
        settings_root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.programmer, cmd:run_tests, execute",
        ],
    )
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.put_policy(
            "pj",
            PolicySpec(
                rows=[
                    PolicyRow(
                        ptype="p", fields=["member:pj.programmer", "tool:read_issue", "execute"]
                    )
                ]
            ),
        )

    assert "cmd_ref" in {item.kind for item in caught.value.problems}
    assert [item.reason for item in caught.value.problems if "死に定義" in item.reason]


def test_the_command_file_shape_is_the_one_the_first_half_settled(settings_root: Path) -> None:
    """FR-059・T079 の継承: 形は `commands: {<名前>: {argv: [...]}}` のまま。"""
    write_minimal_area_a(settings_root)
    write_commands(settings_root, "commands:\n  run_tests:\n    argv: [uv, run, pytest]\n")
    write_policy(
        settings_root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.programmer, cmd:run_tests, execute",
        ],
    )
    crud = _crud(settings_root)

    _view, outcome = crud.update_command(
        "run_tests", CommandDefSchema(name="run_tests", argv=["make"])
    )

    body = (settings_root / "global/commands.yaml").read_text(encoding="utf-8")
    assert "commands:" in body
    assert "argv:" in body
    assert crud.get_command("run_tests").argv == ["make"]
    assert outcome.effect.value == "immediate", "コマンド定義は即時に効く（P5D-016）"


def test_an_unknown_command_name_is_not_found(settings_root: Path) -> None:
    """FR-075: 不明なコマンド名は **404**（契約例外 `UnknownCommand`）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(UnknownCommand):
        crud.get_command("居ない")
    with pytest.raises(UnknownCommand):
        crud.delete_command("居ない")


# -- T057 F16 権限ポリシーの拒否表 ------------------------------------------------------


def test_a_row_type_outside_p_and_g_is_refused_by_the_dto() -> None:
    """DIFF-18・FR-079a: 行の種別が `p`／`g` 以外の形は**口の列挙**が締め出す。"""
    with pytest.raises(ValueError, match="'p' or 'g'"):
        PolicyRow.model_validate({"ptype": "q", "fields": ["a", "b"]})


def test_a_column_count_violation_is_refused_as_orphan(settings_root: Path) -> None:
    """FR-063: 列数（`p`=3・`g`=2）違反は候補の判定器（担保 2）が `orphan` で落とす。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.put_policy(
            "pj",
            PolicySpec(
                rows=[
                    PolicyRow(
                        ptype="p",
                        fields=["member:pj.programmer", "tool:read_issue", "execute", "x"],
                    )
                ]
            ),
        )

    assert "orphan" in {item.kind for item in caught.value.problems}


def test_a_subject_outside_the_registry_is_refused_by_v21(settings_root: Path) -> None:
    """V-21: 台帳に居ない主体の行は拒否される。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.put_policy(
            "pj",
            PolicySpec(
                rows=[
                    PolicyRow(
                        ptype="p", fields=["member:pj.programmer", "tool:read_issue", "execute"]
                    ),
                    PolicyRow(
                        ptype="p", fields=["member:pj.analyst", "tool:read_issue", "execute"]
                    ),
                ]
            ),
        )

    assert "orphan" in {item.kind for item in caught.value.problems}


def test_written_rows_carry_no_quotes(settings_root: Path) -> None:
    """FR-029: 書出しに引用符を付けない（付けると V-21 が孤児として拒否する）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    crud.put_policy(
        "pj",
        PolicySpec(
            rows=[
                PolicyRow(ptype="p", fields=["member:pj.programmer", "tool:read_issue", "execute"])
            ]
        ),
    )

    body = (settings_root / "projects/pj/policy.csv").read_text(encoding="utf-8")
    assert '"' not in body
    assert body == "p, member:pj.programmer, tool:read_issue, execute\n"


def test_the_model_conf_is_returned_for_reference_but_never_written(settings_root: Path) -> None:
    """FR-064: `model.conf` は**参照用**であり CRUD 対象外（`g = _, _` を落とさない）。"""
    write_minimal_area_a(settings_root)
    before = (settings_root / "global/model.conf").read_bytes()
    crud = _crud(settings_root)

    view = crud.get_policy("pj")

    assert "[role_definition]" in view.model_conf
    crud.put_policy(
        "pj",
        PolicySpec(
            rows=[
                PolicyRow(ptype="p", fields=["member:pj.programmer", "tool:read_issue", "execute"])
            ]
        ),
    )
    assert (settings_root / "global/model.conf").read_bytes() == before


def test_b46_a_subject_from_another_project_is_writable_and_never_effective(
    settings_root: Path,
) -> None:
    """**B-46**（`005` B-18 の状態更新）: 他 PJ のメンバを主体にした行は**型でも規則でも防げない**。

    **塞がず**、「口から書けて保存も通り、永久に効かない」ことをそのまま固定して記録する
    （FR-079b。V-21 は `Registry.subjects` を **PJ 横断**で見るので候補の検査も通る）。
    """
    write_minimal_area_a(settings_root, projects=("pj", "other"))
    crud = _crud(settings_root)

    crud.put_policy(
        "pj",
        PolicySpec(
            rows=[
                PolicyRow(ptype="p", fields=["member:pj.programmer", "tool:read_issue", "execute"]),
                PolicyRow(
                    ptype="p", fields=["member:other.programmer", "tool:read_issue", "execute"]
                ),
            ]
        ),
    )

    # 書けて保存も通る。だが `enforce` は主体から PJ を台帳で引くので `other` の判定器を見る。
    assert crud.get_policy("pj").rows[1].fields[0] == "member:other.programmer"


# -- T058 F17 ルール・Hook・人間・段テンプレート -----------------------------------------


def test_rules_accept_an_empty_body(settings_root: Path) -> None:
    """FR-065・M-03: 共通ルールの本文は**空文字も可**（正本は 200／404 だけで 422 を持たない）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    view, _outcome = crud.put_project_rules("pj", TextBody(body=""))

    assert view.body == ""
    assert (settings_root / "projects/pj/rules.md").read_text(encoding="utf-8") == ""


def test_hooks_are_only_format_checked(settings_root: Path) -> None:
    """V-18・REC-02・B-41: Hook は**形式検査だけ**（接続も PATH 解決もしない）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    view, _outcome = crud.put_hooks(
        "pj",
        HooksConfigSchema(
            hooks=[HookRefSchema(name="どこにも実体が無い hook", points=["PRE_TOOL_CALL"])]
        ),
    )

    assert view.hooks[0].name == "どこにも実体が無い hook"
    body = (settings_root / "projects/pj/hooks.yaml").read_text(encoding="utf-8")
    assert "points: PRE_TOOL_CALL" in body, "領域 A の形は 1 宣言 1 地点"


def test_a_hook_with_two_points_expands_to_two_declarations(settings_root: Path) -> None:
    """口の `points` は一覧・領域 A は 1 宣言 1 地点。**地点ごとに展開して書く**。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    view, _outcome = crud.put_hooks(
        "pj",
        HooksConfigSchema(
            hooks=[HookRefSchema(name="h", points=["PRE_TOOL_CALL", "POST_TOOL_CALL"])]
        ),
    )

    assert [ref.points for ref in view.hooks] == [["PRE_TOOL_CALL"], ["POST_TOOL_CALL"]]


def test_a_hook_top_level_key_typo_is_refused_by_the_dto() -> None:
    """B-17・FR-079a: `hooks.yaml` のトップレベルキーの綴り違いは**口の型**が締め出す。"""
    with pytest.raises(ValueError, match="[Ee]xtra"):
        HooksConfigSchema.model_validate({"hook": []})


def test_a_human_actor_colliding_with_a_member_is_refused_by_v20(settings_root: Path) -> None:
    """V-20: 人間の宣言が AI メンバ／システムの行為者と衝突すれば拒否（**3 対すべて**）。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        crud.put_humans(HumansView(human_actors=["pj.programmer"]))
    assert "forward" in {item.kind for item in caught.value.problems}

    with pytest.raises(SettingsValidationError):
        crud.put_humans(HumansView(human_actors=[crud.config.system_actor]))


def test_global_task_templates_require_all_seven_keys(settings_root: Path) -> None:
    """V-08: 全体の段テンプレートは **7 キー必須**。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)
    partial = TaskTemplatesView.model_validate(
        {"plain": {"description": "{acceptance}", "expected_output": "x"}}
    )

    with pytest.raises(SettingsValidationError) as caught:
        crud.put_global_tasks(partial)

    assert "var_required" in {item.kind for item in caught.value.problems}


def test_project_task_overrides_are_replaced_key_by_key_and_can_be_cleared(
    settings_root: Path,
) -> None:
    """FR-070・Edge Cases: PJ 側はキー単位の丸ごと置換。**全解除で全体に戻る**。"""
    write_minimal_area_a(settings_root)
    write_tasks(
        settings_root,
        "pj",
        "plain:\n  description: 「{acceptance}」を満たす\n  expected_output: 上書き\n",
    )
    crud = _crud(settings_root)
    assert set(crud.get_project_tasks("pj").root) == {"plain"}

    crud.put_project_tasks(
        "pj",
        TaskTemplatesView.model_validate(
            {"child": {"description": "{acceptance}", "expected_output": "子"}}
        ),
    )
    assert set(crud.get_project_tasks("pj").root) == {"child"}, "キー単位で丸ごと置換していない"

    crud.delete_project_tasks("pj")

    assert crud.get_project_tasks("pj").root == {}
    assert not (settings_root / "projects/pj/tasks.yaml").exists()


# -- T059 レベル付き一覧（FR-069・US4 AC-8・D-4） ----------------------------------------


def test_the_level_of_each_entry_is_readable_without_a_new_port(settings_root: Path) -> None:
    """FR-069・FR-072・SC-014: **専用の口を足さず**既存 `GET` の区別だけでレベルが引ける。"""
    write_minimal_area_a(settings_root)
    crud = _crud(settings_root)

    global_rows = crud.list_agents(None)
    project_rows = crud.list_agents("pj")
    limits = crud.get_limits()

    assert {row.project_key for row in global_rows} == {None}, "全体の既定行は project_key が null"
    assert {row.project_key for row in project_rows} == {"pj"}
    assert set(limits.overrides) == set()
    assert crud.get_global_tasks().root, "全体の段テンプレートは別の口で引ける"
