"""US6: app の base URL の受け取り（U-4・FR-032・contracts/ui-startup.md §2）。

導出が `UiEnvSettings.resolved_app_base_url` の**1 箇所に閉じている**ことも確かめる（D15-f）。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ui.config import UiEnvSettings

DERIVED_TEMPLATE_MARKERS = ("http://", "{self.app_host}", "{self.app_port}")
"""導出式が持つはずの断片（1 箇所に閉じていることの照合に使う）。"""


def _neutralize_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """`APP_BASE_URL` を空にして導出経路へ落とす。

    ルート `.env` は実在し `APP_BASE_URL` に実値を持つ（実測）。環境変数は `.env` より優先
    されるので、**空文字列**を置くと「与えられていない」のと同じ扱いになる（`_env_file` は
    pydantic の生成する `__init__` の型に現れないため使わない）。
    """
    monkeypatch.setenv("APP_BASE_URL", "")


def test_u4_base_url_is_derived_from_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """U-4・FR-032: `APP_BASE_URL` を省くと `APP_HOST`／`APP_PORT` から導出される。"""
    _neutralize_base_url(monkeypatch)
    monkeypatch.setenv("APP_HOST", "10.0.0.5")
    monkeypatch.setenv("APP_PORT", "9001")

    settings = UiEnvSettings()

    assert not settings.app_base_url
    assert settings.resolved_app_base_url == "http://10.0.0.5:9001"


def test_u4_explicit_base_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """U-4・FR-032: `APP_BASE_URL` を与えるとその値が優先される。"""
    monkeypatch.setenv("APP_BASE_URL", "http://example.invalid:9999")
    monkeypatch.setenv("APP_HOST", "10.0.0.5")
    monkeypatch.setenv("APP_PORT", "9001")

    settings = UiEnvSettings()

    assert settings.resolved_app_base_url == "http://example.invalid:9999"


def test_defaults_apply_without_any_env() -> None:
    """CFG-10〜12 はいずれも任意で既定値を持つ（`.env` が無くても落ちない）。

    ルート `.env` が実在するため、実値ではなく**宣言**を見る（data-model §9）。
    """
    fields = UiEnvSettings.model_fields

    assert fields["app_base_url"].default is None
    assert fields["app_host"].default == "127.0.0.1"
    assert fields["app_port"].default == 8000
    assert all(not field.is_required() for field in fields.values())


def test_extra_ignore_lets_other_modules_keys_coexist(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` に他モジュールのキーが同居しても ui の起動が落ちない（contracts §3）。"""
    monkeypatch.setenv("SOME_OTHER_MODULE_ROOT", "/tmp/other")

    settings = UiEnvSettings()

    assert settings.resolved_app_base_url


def test_derivation_is_written_in_exactly_one_place() -> None:
    """D15-f: 導出は `resolved_app_base_url` の 1 箇所に閉じる。"""
    source_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "ui"
    holders: list[str] = []
    for path in sorted(source_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            rendered = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if rendered.startswith(("http://", "https://")):
                holders.append(path.name)

    assert holders == ["config.py"], (
        f"base URL の導出式が 1 箇所に閉じていない（D15-f）。現れた場所: {holders}"
    )
    assert all(
        marker in (source_dir / "config.py").read_text(encoding="utf-8")
        for marker in DERIVED_TEMPLATE_MARKERS
    )
