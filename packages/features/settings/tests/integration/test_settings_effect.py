"""US1 AC-3・US4: 保存の**直後**に読取側の答えが変わる（S-3・SC-003・FR-018）。

`SettingsCrudService` と `SettingsService`（4 Protocol の実装）は**同じインスタンス**を共有する
（C-2）ので、差し替えは**同じ呼び出しの中**で効く。**再起動も再読込も要らない。**

統合テストに置くのは、判定器の再構築（`policy.csv` のパスから組み直す＝ D-b）と台帳の再導出が
**実際にファイルを書いた後**でなければ観測できないためである。
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from settings_testing import (
    StubBoardReader,
    StubVaultReader,
    make_crud,
    write_llm_profiles,
    write_minimal_area_a,
)

from common.interfaces.settings import Authorizer, CommandRegistry, MemberResolver
from settings.schemas import AgentSpec, PolicyRow, PolicySpec

if TYPE_CHECKING:
    from pathlib import Path

from common.kernel.enums import PersonaKey

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

SUBJECT = "member:pj.programmer"


def test_s3_a_policy_save_changes_the_verdict_in_the_same_call(settings_root: Path) -> None:
    """S-3・SC-003・US1 AC-3: 権限ポリシー保存の**直後**に `enforce` が新しい結果を返す。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader())
    authorizer: Authorizer = crud.service  # 憲章 §3: Protocol 型を必ず注釈する
    assert authorizer.enforce(SUBJECT, "tool:read_issue", "execute") is True
    assert authorizer.enforce(SUBJECT, "tool:write_outcome", "execute") is False

    crud.put_policy(
        "pj",
        PolicySpec(
            rows=[
                PolicyRow(ptype="p", fields=[SUBJECT, "tool:read_issue", "execute"]),
                PolicyRow(ptype="p", fields=[SUBJECT, "tool:write_outcome", "execute"]),
            ]
        ),
    )

    assert authorizer.enforce(SUBJECT, "tool:write_outcome", "execute") is True, (
        "判定器が再構築されていない（再起動を要する状態になっている）"
    )


def test_a_member_save_changes_the_resolution_in_the_same_call(settings_root: Path) -> None:
    """SC-003: メンバ保存の直後に `MemberResolver.resolve` が新しい構成を返す。"""
    write_minimal_area_a(settings_root)
    write_llm_profiles(settings_root, TWO_PROFILES)
    crud = make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader())
    resolver: MemberResolver = crud.service
    assert resolver.resolve("pj", PersonaKey.programmer).llm_profile.name == "default"

    crud.put_agent("pj", PersonaKey.programmer, AgentSpec(llm="spare", max_iter=3))

    member = resolver.resolve("pj", PersonaKey.programmer)
    assert member.llm_profile.name == "spare"
    assert member.max_iter == 3


def test_a_command_save_changes_the_registry_in_the_same_call(settings_root: Path) -> None:
    """P5D-016: コマンド定義は**即時**に効く（`resolve_command` が新しい argv を返す）。"""
    from settings_testing import write_commands, write_policy  # noqa: PLC0415

    write_minimal_area_a(settings_root)
    write_commands(settings_root, "commands:\n  run_tests:\n    argv: [uv, run, pytest]\n")
    write_policy(
        settings_root,
        "pj",
        [
            f"p, {SUBJECT}, tool:read_issue, execute",
            f"p, {SUBJECT}, cmd:run_tests, execute",
        ],
    )
    crud = make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader())
    registry: CommandRegistry = crud.service
    assert registry.resolve_command("run_tests") == ["uv", "run", "pytest"]

    from settings.schemas import CommandDefSchema  # noqa: PLC0415

    crud.update_command("run_tests", CommandDefSchema(name="run_tests", argv=["make", "test"]))

    assert registry.resolve_command("run_tests") == ["make", "test"]


def test_a_rejected_save_leaves_the_verdict_untouched(settings_root: Path) -> None:
    """FR-021・P7: 検査に落ちた保存は**差し替えを行わない**（世代だけ進んだ状態を作らない）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader())
    authorizer: Authorizer = crud.service  # 憲章 §3: Protocol 型を必ず注釈する
    before = crud.generation

    with contextlib.suppress(Exception):  # 拒否そのものは別のテストが見る
        crud.put_policy(
            "pj",
            PolicySpec(
                rows=[
                    PolicyRow(ptype="p", fields=["member:pj.analyst", "tool:read_issue", "execute"])
                ]
            ),
        )

    assert crud.generation == before
    assert authorizer.enforce(SUBJECT, "tool:read_issue", "execute") is True
