"""settings 提供の解決系 3 契約（IF-27〜30）の適合検証。US3。

`MemberResolver`（IF-27・28）・`Authorizer`（IF-29）・`CommandRegistry`（IF-30）を、
一時の設定ファイル領域 A の**実物**に対して確かめる。

IF-28・IF-29 は台帳の例外欄が `—` なので、**正常入力で例外が出ないことを素直に呼んで
戻り値の型を主張する**形で満たす（FR-018・R-17）。とくに IF-29 は「拒否」を例外ではなく
`False` で返すことが契約である。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from contract_stores import (
    AREA_A_COMMAND_ARGV,
    AREA_A_GLOBAL_BACKSTORY,
    AREA_A_GLOBAL_GOAL,
    AREA_A_GLOBAL_MAX_ITER,
    AREA_A_GLOBAL_MCP_NAME,
    AREA_A_GLOBAL_ROLE,
    AREA_A_GLOBAL_SKILL,
    AREA_A_GLOBAL_TOOLS,
    AREA_A_PJ_GOAL,
    AREA_A_PJ_TOOLS,
)

from common.interfaces.settings import (
    LLMProfile,
    McpSpec,
    Member,
    MemberNotFound,
    UnknownCommand,
)
from common.kernel.enums import PersonaKey, ToolName

if TYPE_CHECKING:
    from contract_stores import TempAreaA

    from common.interfaces.settings import Authorizer, CommandRegistry, MemberResolver

ABSENT_PERSONA = PersonaKey.planner
"""一時領域 A の `agents.yaml` に**宣言が無い**ペルソナ（`MemberNotFound` を踏む）。"""

UNKNOWN_COMMAND = "no-such-command"
"""`commands.yaml` に**無い**名前（`UnknownCommand` を踏む）。"""

ALLOWED_OBJECT = "tool:read_issue"
"""一時領域 A の `policy.csv` が `execute` を許している対象。"""

DENIED_OBJECT = "tool:write_outcome"
"""`policy.csv` に行が無い対象（**例外ではなく `False`** が返る）。"""


@pytest.mark.contract_if("IF-27")
def test_contract_settings_resolve_returns_member(
    member_resolver: MemberResolver, area_a: TempAreaA
) -> None:
    """IF-27 正常系: DM-03/07 —— 識別子・CrewAI フィールド・解決済み `llm_profile` を持つ。

    台帳の出力欄が名指しするのは「CrewAI フィールド（role・goal・backstory・llm_profile
    解決済み・tools・skills・mcps・max_iter・max_rpm・max_execution_time）。**agents.yaml
    ④→⑤ merge 済み**」である。非空チェックでは merge を観測できない（PJ 側が空上書きでも
    通る）ので、一時領域 A は**両側の値を区別できる形**で持つ ——

    - `global/agents.yaml`（④）: `role`・`goal`・`backstory`・`tools` 2 件・`skills`・
      `mcps`・`max_iter`
    - `projects/<pj>/agents.yaml`（⑤）: `goal` と `tools` **だけ**を上書き

    したがって主張は 4 群になる —— (1) 識別子 (2) 上書きした 2 つは PJ の値・上書きして
    いない 5 つは global の値 (3) リスト型は**丸ごと置換**（和集合にしない） (4) `llm_profile`
    が**解決済みの `LLMProfile`**（名前ではなく実体）であること。
    """
    member = member_resolver.resolve(area_a.project_key, area_a.persona)

    assert isinstance(member, Member)
    assert member.member_id == f"{area_a.project_key}.{area_a.persona}"
    assert member.project_key == area_a.project_key
    assert member.persona_key is area_a.persona
    assert member.bd_actor
    assert member.casbin_subject == f"member:{area_a.project_key}.{area_a.persona}"

    # (2) 上書きしたフィールドは PJ の値
    assert member.goal == AREA_A_PJ_GOAL
    assert member.goal != AREA_A_GLOBAL_GOAL
    # (2) 上書きしていないフィールドは global の値
    assert member.role == AREA_A_GLOBAL_ROLE
    assert member.backstory == AREA_A_GLOBAL_BACKSTORY
    assert member.max_iter == AREA_A_GLOBAL_MAX_ITER
    assert member.max_rpm is None, "宣言していないフィールドは値を捏造しない"
    assert member.max_execution_time is None

    # (3) リスト型は丸ごと置換（PJ が 1 件に絞ったので global の 2 件目は残らない）
    assert member.tools == [ToolName(name) for name in AREA_A_PJ_TOOLS]
    assert len(AREA_A_GLOBAL_TOOLS) > len(AREA_A_PJ_TOOLS), "置換と和集合を区別できる形にする"
    assert ToolName(AREA_A_GLOBAL_TOOLS[-1]) not in member.tools
    # 上書きしていないリストは global のまま（型も台帳の出力欄どおり）
    assert member.skills == [AREA_A_GLOBAL_SKILL]
    assert all(isinstance(item, str) for item in member.skills)
    assert all(isinstance(item, McpSpec) for item in member.mcps)
    assert [item.name for item in member.mcps] == [AREA_A_GLOBAL_MCP_NAME]

    # (4)
    assert isinstance(member.llm_profile, LLMProfile)
    assert member.llm_profile.context_window > 0


@pytest.mark.contract_if("IF-27")
def test_contract_settings_resolve_raises_member_not_found(
    member_resolver: MemberResolver, area_a: TempAreaA
) -> None:
    """IF-27 例外系: 台帳に無いペルソナで `MemberNotFound`（既定のメンバに倒さない）。"""
    with pytest.raises(MemberNotFound):
        member_resolver.resolve(area_a.project_key, ABSENT_PERSONA)


@pytest.mark.contract_if("IF-28")
def test_contract_settings_is_human_returns_bool(
    member_resolver: MemberResolver, area_a: TempAreaA
) -> None:
    """IF-28: 台帳照合の結果を `bool` で返す（例外欄が `—`＝ FR-018）。

    台帳の出力欄の注記は「**台帳内＝AI**」である。`humans.yaml` に宣言された actor が真、
    メンバ台帳側の `bd_actor` は偽になる。
    """
    human = member_resolver.is_human(area_a.human_actor)
    ai = member_resolver.is_human(f"{area_a.project_key}.{area_a.persona}")

    assert isinstance(human, bool)
    assert human is True
    assert ai is False


@pytest.mark.contract_if("IF-29")
def test_contract_settings_enforce_returns_bool_without_raising(
    authorizer: Authorizer, area_a: TempAreaA
) -> None:
    """IF-29: casbin の判定を `bool` で返す（例外欄が `—`＝ FR-018）。

    **拒否を例外に倒さない**ことが契約である —— Tool の前段判定は「許可されなかった」を
    普通の戻り値として受け取る。
    """
    subject = f"member:{area_a.project_key}.{area_a.persona}"

    allowed = authorizer.enforce(subject, ALLOWED_OBJECT, "execute")
    denied = authorizer.enforce(subject, DENIED_OBJECT, "execute")
    unknown_subject = authorizer.enforce("member:nobody", ALLOWED_OBJECT, "execute")

    assert isinstance(allowed, bool)
    assert allowed is True
    assert denied is False
    assert unknown_subject is False


@pytest.mark.contract_if("IF-30")
def test_contract_settings_resolve_command_returns_argv(
    command_registry: CommandRegistry, area_a: TempAreaA
) -> None:
    """IF-30 正常系: DM-26 —— 登録名から**宣言どおりに分かち書きされた** `argv` を返す。

    台帳の出力欄の注記は「`shell=False`・引数は AI に渡させない」である。返るのは**文字列の
    一覧**であって 1 本のコマンド行ではない。

    型・非空・要素が `str` だけでは、**1 本の文字列に連結して返す実装でも緑**になり
    （`["/bin/echo hello"]` は 3 つとも満たす）、`shell=False` の要点が未検証のまま残る。
    領域 A は `argv` を **2 要素**で宣言しているので、**同じ並び**が返ることまで主張する
    （FR-016）。期待値は宣言と同じ定数から取る（テストへ直書きしない）。
    """
    argv = command_registry.resolve_command(area_a.command_name)

    assert isinstance(argv, list)
    assert all(isinstance(item, str) for item in argv)
    assert argv == list(AREA_A_COMMAND_ARGV), "宣言した分かち書きが保たれていない"
    assert len(AREA_A_COMMAND_ARGV) > 1, "1 要素だと連結した実装と区別できない"


@pytest.mark.contract_if("IF-30")
def test_contract_settings_resolve_command_raises_unknown_command(
    command_registry: CommandRegistry,
) -> None:
    """IF-30 例外系: 台帳に無い名前で `UnknownCommand`（空の argv に倒さない）。"""
    with pytest.raises(UnknownCommand):
        command_registry.resolve_command(UNKNOWN_COMMAND)
