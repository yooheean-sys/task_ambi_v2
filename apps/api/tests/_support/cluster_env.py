"""クラスタ層の止め木（実 `.env`・実運用ストア・実 LLM API へ届かせない）。規約 §5・plan (iii)。

`contract/conftest.py` の `isolated_env_file`／`assert_env_path_under` の**同型移植**である
（`contract/` 配下の補助 module は `integration/` から import できない＝ R-01）。差分は 2 点。

1. **差し替え対象は 5 件**（`contract/` の 4 件に **`trigger.env_file_path` を足す**）。
   executor だけは差し替え点が **`executor.models.env_file_path`** である
   （`executor.env_file_path` は再エクスポートで、差し替えても効かない）。
2. `.env` の**中身を引数で受ける**（01・03 は `api_key_ref` の実値 1 件が要る）。

⚠ 止め木が効くのは、各 MOD の `<Mod>Env` の明示 `__init__(self, **overrides)` と `build()` の
`<Mod>Env(_env_file=env_file_path())` が**対で**在るときだけである。どちらかを落とすと
`model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**になる
（テストは緑のまま実運用ストアを掴む）。そのための**能動検査**が `assert_env_path_under` で、
`build()` を呼ぶ**前**に通す（settings の `build()` は不在時に雛形を**書き込む**ため、
事後確認では手遅れになる）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic_settings import BaseSettings

ENV_FILE_PATH_TARGETS = (
    "board.env_file_path",
    "vault.env_file_path",
    "settings.env_file_path",
    "executor.models.env_file_path",
    "trigger.env_file_path",
)
"""`.env` を読む 5 MOD の差し替え点（executor だけ `models` 側＝再エクスポートでは効かない）。"""

CLEARED_ENV_KEYS = (
    "BEADS_DIR",
    "BD_BIN",
    "VAULT_ROOT",
    "SETTINGS_ROOT",
    "TRIGGER_ROOT",
    "DOWNLOADS_DIR",
)
"""OS 環境変数の側から実運用ストアが漏れないよう消すキー（`.env` より優先されるため）。"""

API_KEY_ENV_PREFIX = "LLM_API_KEY"
"""実 LLM API の鍵（前方一致で消す）。"""


def isolate_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    entries: Mapping[str, str] | None = None,
) -> Path:
    """一時 `.env` を作り、5 つの `env_file_path` をそこへ向け、危険な環境変数を消す。

    止め木 4 枚のうち **1 枚目と 2 枚目**（規約 §5）。差し替えを忘れた MOD は `build()` が
    「キーが無い」で落ちるので、実物を掴んだまま緑になることが構造的に起きない。

    Args:
        monkeypatch: 差し替えの後始末を持つ pytest の道具（fixture 終了で必ず戻る）。
        tmp_path: 一時 `.env` を置くディレクトリ。
        entries: 一時 `.env` に書く `KEY=VALUE`（`api_key_ref` の実値などに使う）。
            `None` なら空の `.env` になる。

    Returns:
        差し替え先の `.env` の絶対パス（`assert_env_path_under` の `env_file=` に渡す）。
    """
    env_file = tmp_path / "cluster.env"
    lines = [f"{key}={value}" for key, value in (entries or {}).items()]
    env_file.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")
    for target in ENV_FILE_PATH_TARGETS:
        monkeypatch.setattr(target, lambda: env_file)
    for key in CLEARED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key in [name for name in os.environ if name.startswith(API_KEY_ENV_PREFIX)]:
        monkeypatch.delenv(key, raising=False)
    return env_file


def assert_env_path_under(
    env_cls: type[BaseSettings],
    field: str,
    tmp_path: Path,
    *,
    env_file: Path,
) -> Path:
    """`build()` を呼ぶ**前**に、その MOD が読む解決先が `tmp_path` 配下かを確かめる。

    止め木 4 枚のうち **3 枚目**（規約 §5）。`build()` そのものを検査に使わない —— settings の
    `build()` は不在時に雛形を**書き込む**ため、事後確認では手遅れになる。読み方は `build()` と
    **同じ経路**（`<Mod>Env(_env_file=env_file_path())`）にする。

    Args:
        env_cls: その MOD の `<Mod>Env`（`BoardEnv`・`VaultEnv`・`SettingsEnv`・`TriggerEnv`）。
        field: 解決先を持つフィールド名（`BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT`・
            `TRIGGER_ROOT`）。
        tmp_path: 配下であるべき一時ディレクトリ。
        env_file: `build()` が読む `.env` の絶対パス（`isolate_env` の戻り）。

    Returns:
        解決済みの絶対パス（`tmp_path` 配下であることが確かめられたもの）。
    """
    expected = tmp_path.resolve()
    try:
        env = env_cls(_env_file=env_file)
    except Exception as exc:  # noqa: BLE001  読めない理由を出して止めるのが本関数の役目
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__} を build() と同じ経路で読めない: {exc}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    raw: object = getattr(env, field, None)
    if not isinstance(raw, Path):
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__}.{field} が Path でない: {raw!r}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    resolved = raw.expanduser().resolve()
    if not resolved.is_relative_to(expected):
        pytest.fail(
            "止め木の能動検査: 解決先が一時ディレクトリの外にある"
            "（実物に触れる前に止めた＝規約 §5）\n"
            f"  読んだキー名: {field}\n"
            f"  解決値: {resolved}\n"
            f"  期待した接頭辞: {expected}\n"
            f"  読んだ .env: {env_file}"
        )
    return resolved


def point_config_at(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    payload: Mapping[str, object],
    tmp_path: Path,
) -> Path:
    """`<mod>.config_path` を一時 `config.json` へ向ける（R-06。**実時間に効く値だけ**）。

    `registry.py` の `_build_*` は 8 登録とも `build()` に `config=` を渡さないので、実配線
    経由では `build` の config 引数が使えない。一方で各 MOD は `config_path()`（module 大域の
    関数）を**呼び出し時に**評価するため、ここを差し替えれば実配線のまま値を変えられる。

    **判定ロジックに効く値（`grade_map`・`cost_field_path`・`max_returns`・`system_actor`・
    stages の閾値）は差し替えない**（規約 §5）。差し替えたテストは docstring に理由を書く。
    `config.json` そのものは 1 行も変えない。

    Args:
        monkeypatch: 差し替えの後始末を持つ pytest の道具。
        module_name: 差し替える MOD 名（`"trigger"` など）。
        payload: 一時 `config.json` に書く内容（その MOD の `<Mod>Config` が読める形）。
        tmp_path: 一時 `config.json` を置くディレクトリ。

    Returns:
        書いた一時 `config.json` の絶対パス。
    """
    config_file = tmp_path / f"{module_name}_config.json"
    config_file.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(f"{module_name}.config_path", lambda: config_file)
    return config_file
