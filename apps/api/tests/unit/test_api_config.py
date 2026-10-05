"""US4: 設定の不備で待受を開始せずに中断する。

検証: US4 AC1／AC2／AC5・SC-007・FR-025〜028（plan (v)・research R-11・R-12）。
**ルート `config.json` も `.env` も書き換えない**。読み先を一時ディレクトリへ差し替えて検査する
（`common.kernel.config.config_path` を差し替えると `RootConfig` と翻訳側の両方が追随する）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import uvicorn

from api import main as main_module
from api.config import AppEnvSettings, load_root_config
from api.exceptions import ConfigurationError
from common.kernel import config as kernel_config

if TYPE_CHECKING:
    import pathlib

SECRET_MARKERS = ("key", "token", "secret", "password", "credential")
"""`AppEnvSettings` のフィールド名に現れてはならない語（FR-028）。"""

REQUIRED_ROOT_KEYS = ("app_name", "env_label", "enabled_modules")
"""ルート設定の必須 3 キー（実測: `RootConfig` の既定値を持たないフィールド）。"""

VALID_ROOT_CONFIG: dict[str, Any] = {
    "app_name": "task_ambi",
    "env_label": "unit",
    "enabled_modules": [],
}


def _write_config(path: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
    """一時ディレクトリにルート設定を書き、そのパスを返す。"""
    target = path / "config.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


@pytest.mark.parametrize("missing", REQUIRED_ROOT_KEYS)
def test_us4_ac1_missing_required_key_names_the_key_and_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, missing: str
) -> None:
    """US4 AC1・SC-007: 欠けたキー名と**読んだファイルの絶対パス**が失敗の記述に出る。"""
    payload = {key: value for key, value in VALID_ROOT_CONFIG.items() if key != missing}
    target = _write_config(tmp_path, payload)
    monkeypatch.setattr(kernel_config, "config_path", lambda: target)

    with pytest.raises(ConfigurationError) as caught:
        load_root_config()

    message = str(caught.value)
    assert missing in message, f"欠けたキー名 {missing} が記述に無い"
    assert str(target) in message, "読んだファイルの絶対パスが記述に無い"


def test_load_root_config_succeeds_on_a_valid_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """負の検証の対: 妥当な設定なら翻訳器は素通しする（「常に落ちる」検査になっていない）。"""
    target = _write_config(tmp_path, VALID_ROOT_CONFIG)
    monkeypatch.setattr(kernel_config, "config_path", lambda: target)

    config = load_root_config()

    assert config.app_name == "task_ambi"
    assert config.env_label == "unit"
    assert config.enabled_modules == []


def test_missing_file_is_translated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """不在は `FileNotFoundError` ではなく `ConfigurationError` に寄せる（research R-11）。"""
    target = tmp_path / "config.json"
    monkeypatch.setattr(kernel_config, "config_path", lambda: target)

    with pytest.raises(ConfigurationError) as caught:
        load_root_config()

    assert str(target) in str(caught.value)


def test_broken_json_is_translated(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """JSON として読めない場合も `ConfigurationError` に寄せる。"""
    target = tmp_path / "config.json"
    target.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(kernel_config, "config_path", lambda: target)

    with pytest.raises(ConfigurationError) as caught:
        load_root_config()

    assert str(target) in str(caught.value)


def test_fr028_app_env_settings_declares_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-028: `AppEnvSettings` に秘密のフィールドが 1 つも無い。"""
    fields = set(AppEnvSettings.model_fields)

    assert fields == {"app_host", "app_port"}, f"宣言は 2 つだけ（FR-028）。現値: {sorted(fields)}"
    for name in fields:
        assert not any(marker in name.lower() for marker in SECRET_MARKERS)

    monkeypatch.setenv("LLM_API_KEY_LOCAL", "sk-should-not-be-read")
    settings = AppEnvSettings()

    assert not hasattr(settings, "llm_api_key_local")


def test_extra_ignore_lets_other_modules_keys_coexist(monkeypatch: pytest.MonkeyPatch) -> None:
    """research R-12: `.env` に他モジュールのキーが同居しても apps/api の起動が落ちない。"""
    monkeypatch.setenv("VAULT_ROOT", "/tmp/vault")
    monkeypatch.setenv("SETTINGS_ROOT", "/tmp/settings")
    monkeypatch.setenv("BEADS_DIR", "/tmp/beads")

    settings = AppEnvSettings()

    assert settings.app_host
    assert settings.app_port > 0


def test_defaults_are_optional() -> None:
    """CFG-10・CFG-11 はいずれも任意で既定値を持つ（本 feature の必須環境依存値は 0 件）。

    `.env` は実在し APP_HOST／APP_PORT を持つため、実値ではなく**宣言**（必須か・既定は何か）を
    見る。読み取りそのものは `test_extra_ignore_lets_other_modules_keys_coexist` が確かめる。
    """
    fields = AppEnvSettings.model_fields

    assert fields["app_host"].is_required() is False
    assert fields["app_port"].is_required() is False
    assert fields["app_host"].default == "127.0.0.1"
    assert fields["app_port"].default == 8000


def test_us4_ac2_invalid_env_value_stops_before_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    """US4 AC2: 不正な環境依存値（`APP_PORT=abc`）で `ValidationError` になり待受に到達しない。"""
    from pydantic import ValidationError

    monkeypatch.setenv("APP_PORT", "abc")

    with pytest.raises(ValidationError) as caught:
        AppEnvSettings()

    assert "app_port" in str(caught.value).lower()


def test_us4_ac5_main_builds_the_app_before_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """US4 AC5・FR-026: `main()` が `create_app()` を `uvicorn.run` より**前**に呼ぶ。"""
    calls: list[str] = []

    def fake_create_app() -> object:
        calls.append("create_app")
        return object()

    def fake_run(*args: Any, **kwargs: Any) -> None:
        calls.append("uvicorn.run")

    monkeypatch.setattr(main_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    main_module.main()

    assert calls == ["create_app", "uvicorn.run"]


def test_us4_ac5_main_does_not_reach_uvicorn_run_when_assembly_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US4 AC5・FR-026: 組み立てに失敗したら `uvicorn.run` に到達しない。"""
    reached: list[str] = []

    def failing_create_app() -> object:
        raise ConfigurationError("ルート設定の必須値が欠けている: enabled_modules")

    def fake_run(*args: Any, **kwargs: Any) -> None:
        reached.append("uvicorn.run")

    monkeypatch.setattr(main_module, "create_app", failing_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    with pytest.raises(ConfigurationError):
        main_module.main()

    assert reached == [], "待受が開始されている（US4 AC2 違反）"


def test_main_passes_the_env_values_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` が CFG-10・CFG-11 を `uvicorn.run` へ渡す（contracts §6）。"""
    seen: dict[str, Any] = {}
    sentinel = object()

    def fake_run(app: object, **kwargs: Any) -> None:
        seen["app"] = app
        seen.update(kwargs)

    monkeypatch.setattr(main_module, "create_app", lambda: sentinel)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("APP_PORT", "9123")

    main_module.main()

    assert seen["app"] is sentinel
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9123
