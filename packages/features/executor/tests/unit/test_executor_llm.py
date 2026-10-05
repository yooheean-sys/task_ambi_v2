"""`LlmFactory` と `InterceptorAdapter`（F02・T028・US1／US6 AC-5〜7）。**表駆動**で書く。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import (
    make_profile,
    make_request,
    make_response,
    make_stubs,
    use_env_file,
)

from common.interfaces.executor import ExecutorFailure
from common.interfaces.monitor import FailureKind, RunHalted
from common.interfaces.settings import ProviderKind
from executor.models import InterceptorAdapter, LlmFactory, env_file_path, read_env_values

if TYPE_CHECKING:
    from common.interfaces.settings import LLMProfile

SECRET = "sk-REAL-SECRET-VALUE"


def build_llm(profile: LLMProfile) -> object:
    """`LlmFactory.build` を通す（**構築だけ**。通信ゼロ）。"""
    stubs = make_stubs()
    adapter = InterceptorAdapter(hook=stubs.llm_http_hook)
    return LlmFactory().build(profile, adapter)


# --------------------------------------------------------------------------------------
# provider_kind の 2 分岐（F-30）
# --------------------------------------------------------------------------------------


def test_openai_compatible_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """互換提供者 → `model="openai/<id>"` ＋ `base_url` ＋ `extra_headers` ＋ `interceptor`。"""
    use_env_file(monkeypatch, tmp_path, "")
    stubs = make_stubs()
    adapter = InterceptorAdapter(hook=stubs.llm_http_hook)
    profile = make_profile(
        provider_kind=ProviderKind.openai_compatible,
        base_url="http://127.0.0.1:11434/v1",
        model_id="qwen3",
        extra_headers={"X-A": "b"},
    )
    llm = LlmFactory().build(profile, adapter)
    assert llm.base_url == "http://127.0.0.1:11434/v1"
    assert llm.interceptor is adapter
    assert llm.additional_params["extra_headers"] == {"X-A": "b"}


def test_openrouter_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """集約提供者 → `model="openrouter/<id>"`。**接続先は既定が自動で入る**（SD-02）。"""
    use_env_file(monkeypatch, tmp_path, f"LLM_API_KEY_P1={SECRET}\n")
    stubs = make_stubs()
    adapter = InterceptorAdapter(hook=stubs.llm_http_hook)
    profile = make_profile(
        provider_kind=ProviderKind.openrouter,
        base_url="",
        model_id="openai/gpt-4o-mini",
        api_key_ref="LLM_API_KEY_P1",
        extra_headers={"HTTP-Referer": "x"},
    )
    llm = LlmFactory().build(profile, adapter)
    assert llm.base_url == "https://openrouter.ai/api/v1"
    assert llm.interceptor is adapter
    assert llm.additional_params["extra_headers"] == {"HTTP-Referer": "x"}


# --------------------------------------------------------------------------------------
# 鍵の解決 4 通り（FR-023・FR-023a・SC-023）
# --------------------------------------------------------------------------------------


def test_empty_ref_does_not_read_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`api_key_ref=""` → `.env` を**引かず** `api_key=""`。"""
    path = use_env_file(monkeypatch, tmp_path, f"LLM_API_KEY_P1={SECRET}\n")
    read: list[Path] = []
    original = read_env_values

    def spy() -> dict[str, str | None]:
        read.append(path)
        return original()

    monkeypatch.setattr("executor.models.read_env_values", spy)
    assert LlmFactory().resolve_api_key(make_profile(api_key_ref="")) == ""
    assert read == []


def test_missing_key_is_a_build_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """参照名があり `.env` に**キーが無い** → `ExecutorFailure(phase="build")`（SC-023）。"""
    use_env_file(monkeypatch, tmp_path, "OTHER=1\n")
    with pytest.raises(ExecutorFailure) as excinfo:
        LlmFactory().resolve_api_key(make_profile(api_key_ref="LLM_API_KEY_P1"))
    assert excinfo.value.phase == "build"
    assert "LLM_API_KEY_P1" in str(excinfo.value)


def test_present_but_empty_key_continues(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """キーがあり値が**空** → `""` のまま続行（ローカル LLM は鍵を要さない＝ FR-023a）。"""
    use_env_file(monkeypatch, tmp_path, "LLM_API_KEY_P1=\n")
    assert LlmFactory().resolve_api_key(make_profile(api_key_ref="LLM_API_KEY_P1")) == ""


def test_present_key_returns_the_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """キーがあり値がある → その実値。"""
    use_env_file(monkeypatch, tmp_path, f"LLM_API_KEY_P1={SECRET}\n")
    assert LlmFactory().resolve_api_key(make_profile(api_key_ref="LLM_API_KEY_P1")) == SECRET


def test_os_environ_is_never_consulted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """**OS 環境変数を鍵の解決に使わない**（FR-022・憲章 §5・SD-11）。"""
    use_env_file(monkeypatch, tmp_path, "LLM_API_KEY_P1=from-dotenv\n")
    monkeypatch.setenv("LLM_API_KEY_P1", "from-os-environ")
    assert LlmFactory().resolve_api_key(make_profile(api_key_ref="LLM_API_KEY_P1")) == "from-dotenv"


def test_factory_does_not_retain_the_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """鍵を**フィールドに保持しない**（その場で読んで捨てる）。"""
    use_env_file(monkeypatch, tmp_path, f"LLM_API_KEY_P1={SECRET}\n")
    factory = LlmFactory()
    factory.resolve_api_key(make_profile(api_key_ref="LLM_API_KEY_P1"))
    assert SECRET not in repr(factory)
    assert not [
        name
        for name in dir(factory)
        if not name.startswith("_") and SECRET in str(getattr(factory, name, ""))
    ]


def test_env_file_path_is_the_repo_root_dotenv() -> None:
    """`.env` の位置は規約の固定相対パス（`.env` をルートに探す）。"""
    path = env_file_path()
    assert path.name == ".env"
    assert (path.parent / "pyproject.toml").exists()


def test_secret_never_appears_in_failure_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """例外に出るのは**参照名だけ**（実値は 0 件＝ FR-024・SC-022）。"""
    use_env_file(monkeypatch, tmp_path, f"OTHER={SECRET}\n")
    with pytest.raises(ExecutorFailure) as excinfo:
        LlmFactory().resolve_api_key(make_profile(api_key_ref="LLM_API_KEY_P1"))
    rendered = f"{excinfo.value} {excinfo.value.reason}"
    assert SECRET not in rendered


# --------------------------------------------------------------------------------------
# `InterceptorAdapter` の 4 メソッド（FR-021）
# --------------------------------------------------------------------------------------


def test_adapter_delegates_all_four_methods() -> None:
    """4 メソッドすべてが注入された `LLMHttpHook` へ委譲する（非同期は `asyncio.run`）。"""
    stubs = make_stubs()
    adapter = InterceptorAdapter(hook=stubs.llm_http_hook)
    request, response = make_request(), make_response()
    assert adapter.on_outbound(request) is request
    assert adapter.on_inbound(response) is response
    assert asyncio.run(adapter.aon_outbound(request)) is request
    assert asyncio.run(adapter.aon_inbound(response)) is response
    assert stubs.log.targets() == [
        "LLMHttpHook.on_outbound",
        "LLMHttpHook.on_inbound",
        "LLMHttpHook.aon_outbound",
        "LLMHttpHook.aon_inbound",
    ]


def test_adapter_holds_no_state() -> None:
    """**判定・計測・状態を持たない**（FR-021）。"""
    stubs = make_stubs()
    adapter = InterceptorAdapter(hook=stubs.llm_http_hook)
    fields = set(getattr(type(adapter), "__dataclass_fields__", {}))
    assert fields == {"hook"}


@pytest.mark.parametrize("method", ["on_outbound", "aon_outbound"], ids=["sync", "async"])
def test_adapter_does_not_swallow_run_halted(method: str) -> None:
    """上限停止は**握らず素通り**させる（`RunHalted` は `BaseException` 直系）。"""
    stubs = make_stubs()
    stubs.llm_http_hook.outbound_raises = RunHalted(
        kind=FailureKind.limit_requests, measured=11.0, limit=10.0, request_no=11
    )
    adapter = InterceptorAdapter(hook=stubs.llm_http_hook)
    request = make_request()
    with pytest.raises(RunHalted):
        if method == "on_outbound":
            adapter.on_outbound(request)
        else:
            asyncio.run(adapter.aon_outbound(request))
