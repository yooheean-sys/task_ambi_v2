"""追加の差し込みの登録と解除（F10・T032・US1）。"""

from __future__ import annotations

import pytest
from conftest import make_config
from crewai.hooks import InterceptionPoint, get_hooks

from common.interfaces.executor import ExecutorFailure
from common.interfaces.settings import HookRef
from executor.service import register_hooks, unregister_hooks

POINT = InterceptionPoint["PRE_TOOL_CALL"]


@pytest.fixture(autouse=True)
def _clean_hooks() -> None:  # pyright: ignore[reportUnusedFunction]  # autouse fixture は pytest が収集して呼ぶ
    """各検証の前後で登録を空にする（他の検証に漏らさない）。"""
    for point in InterceptionPoint:
        for hook in list(get_hooks(point)):
            from crewai.hooks import unregister_hook

            unregister_hook(point, hook)


def test_catalogued_hook_is_registered_and_unregistered() -> None:
    """台帳にある名前は登録され、解除で 0 件に戻る（FR-054）。"""
    config = make_config(hook_catalog=["audit"])
    refs = [HookRef(name="audit", points="PRE_TOOL_CALL", agents=["programmer"])]
    registered = register_hooks(refs, config)
    assert len(get_hooks(POINT)) == 1
    unregister_hooks(registered)
    assert len(get_hooks(POINT)) == 0


def test_same_name_twice_registers_and_unregisters_twice() -> None:
    """同名が 2 回なら 2 回登録・2 回解除（**HookRef ごとに新しい関数**＝ SD-20）。"""
    config = make_config(hook_catalog=["audit"])
    refs = [
        HookRef(name="audit", points="PRE_TOOL_CALL", agents=["programmer"]),
        HookRef(name="audit", points="PRE_TOOL_CALL", agents=["programmer"]),
    ]
    registered = register_hooks(refs, config)
    assert len(get_hooks(POINT)) == 2
    unregister_hooks(registered)
    assert len(get_hooks(POINT)) == 0


def test_unknown_name_is_a_build_failure() -> None:
    """台帳に無い名前 → `ExecutorFailure(phase="build")`（FR-056）。"""
    config = make_config(hook_catalog=["audit"])
    refs = [HookRef(name="not-in-catalog", points="PRE_TOOL_CALL", agents=[])]
    with pytest.raises(ExecutorFailure) as excinfo:
        register_hooks(refs, config)
    assert excinfo.value.phase == "build"
    assert "not-in-catalog" in str(excinfo.value)
    assert len(get_hooks(POINT)) == 0


def test_empty_catalog_means_nothing_registers() -> None:
    """台帳が空なら何も登録しない（M1 の出荷時既定）。"""
    assert register_hooks([], make_config()) == []
    assert len(get_hooks(POINT)) == 0


def test_both_points_are_supported() -> None:
    """2 点を扱う（`HookRef.points` は列挙の**メンバ名**＝ SD-23）。"""
    config = make_config(hook_catalog=["audit"])
    refs = [
        HookRef(name="audit", points="PRE_TOOL_CALL", agents=[]),
        HookRef(name="audit", points="POST_TOOL_CALL", agents=[]),
    ]
    registered = register_hooks(refs, config)
    assert len(get_hooks(InterceptionPoint["PRE_TOOL_CALL"])) == 1
    assert len(get_hooks(InterceptionPoint["POST_TOOL_CALL"])) == 1
    unregister_hooks(registered)
    assert len(get_hooks(InterceptionPoint["POST_TOOL_CALL"])) == 0


def test_hooks_carry_no_authorization_decision() -> None:
    """**権限の判定を hook 側に置かない**（FR-055。判定は `GuardedTool` の前段）。"""
    import inspect

    from executor import service

    source = inspect.getsource(service)
    hook_section = source[source.index("def register_hooks") :]
    assert "enforce" not in hook_section
