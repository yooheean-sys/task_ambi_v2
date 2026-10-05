"""F13 組み立てと起動中断（`trigger/__init__.py` の `build()`）。

段は plan (v) の (a)〜(f)。**最初に失敗した段だけを報告する**（原因が 1 つに定まる方が追いやすい）。

⚠ 止め木（`tests/conftest.py` の `_isolate_real_area_b`）が効いていることが前提である。
テストは `monkeypatch.setenv` で**一時ディレクトリ**を指す（実運用の領域 B・DL フォルダには
1 バイトも触れない＝ P-3）。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from conftest import StubKickoffRunner
from fastapi import APIRouter

import trigger
from trigger import TriggerConfig, TriggerDeps, TriggerHandle, build, config_path
from trigger.exceptions import (
    TriggerConfigError,
    TriggerDefinitionsError,
    TriggerEnvError,
    TriggerRootError,
    TriggerStartupError,
)
from trigger.models import DEFAULT_TRIGGER_ID
from trigger.repository import DefinitionsRepository

if TYPE_CHECKING:
    from pathlib import Path

READY_INTERVAL = 300


def _deps() -> TriggerDeps:
    return TriggerDeps(kickoff_runner=StubKickoffRunner())


# -- 通る形 -------------------------------------------------------------------------------


def test_build_returns_a_handle_with_a_router_and_a_lifespan(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 (f): `router` と `lifespan` を持つ `TriggerHandle` を返す（既定値を持たない）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    handle = build(_deps())

    assert isinstance(handle, TriggerHandle)
    assert isinstance(handle.router, APIRouter)
    assert callable(handle.lifespan)


def test_build_scaffolds_the_declaration_when_it_is_missing(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 (e)・F-10 (a): `triggers.yaml` が無ければ**雛形を生成する**（既定トリガー 1 件）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    build(_deps())

    definitions = DefinitionsRepository(trigger_root).load_definitions()
    assert [item.trigger_id for item in definitions] == [DEFAULT_TRIGGER_ID]
    assert definitions[0].spec == str(READY_INTERVAL)


def test_build_starts_no_threads(trigger_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SD-23: **`build()` はスレッドを 1 本も起こさない**（常駐部品は `start()` で作る）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))
    before = threading.active_count()

    build(_deps())

    assert threading.active_count() == before


def test_the_lifespan_factory_returns_a_new_context_every_time(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-03・SD-17: **呼ぶたびに新しい非同期コンテキストを返す**（使い回さない）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))
    handle = build(_deps())

    first, second = handle.lifespan(), handle.lifespan()

    assert first is not second
    assert hasattr(first, "__aenter__")
    assert hasattr(first, "__aexit__")


# -- 起動中断の表（5 通り。plan (v)） -----------------------------------------------------


def test_1_a_missing_trigger_root_aborts_the_startup() -> None:
    """(1) 段 (a): `TRIGGER_ROOT` 未設定 → `TriggerEnvError`。

    **必要なキー名と読んだ `.env` の絶対パス**を載せる。
    """
    with pytest.raises(TriggerEnvError) as caught:
        build(_deps())

    message = str(caught.value)
    assert "TRIGGER_ROOT" in message
    # ⚠ `trigger.env_file_path` を**属性越しに**呼ぶ（`from trigger import env_file_path` は
    #    import 時に束縛してしまい、autouse 止め木の差し替えを見ない）
    assert str(trigger.env_file_path()) in message, "読んだ .env の絶対パスが載っていない"


@pytest.mark.parametrize(
    "value",
    ["relative/path", ""],
    ids=["not_absolute", "empty"],
)
def test_2a_a_relative_trigger_root_aborts_the_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """(2) 段 (b): **絶対パスの検査を最初に置く**。

    ⚠ `TRIGGER_ROOT=`（空値）を pydantic は `Path(".")` として受理する。この検査が無いと
    `Path(".")` が cwd に解決されて `exists()`・`is_dir()` の両方が真になり、段 (e) が
    **カレントディレクトリに雛形を書き込む**。
    """
    monkeypatch.setenv("TRIGGER_ROOT", value)

    with pytest.raises(TriggerRootError, match="絶対パス"):
        build(_deps())


def test_2b_a_missing_trigger_root_directory_aborts_the_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(2) 段 (b): 実在しない → `TriggerRootError`（**見に行った絶対パス**を載せる）。"""
    missing = tmp_path / "never"
    monkeypatch.setenv("TRIGGER_ROOT", str(missing))

    with pytest.raises(TriggerRootError, match="実在しない") as caught:
        build(_deps())

    assert str(missing) in str(caught.value)


def test_2c_a_trigger_root_that_is_a_file_aborts_the_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(2) 段 (b): ディレクトリでない → `TriggerRootError`（不在とは別のメッセージ）。"""
    target = tmp_path / "not_a_dir"
    target.write_text("", encoding="utf-8")
    monkeypatch.setenv("TRIGGER_ROOT", str(target))

    with pytest.raises(TriggerRootError, match="ディレクトリでない"):
        build(_deps())


def test_3_a_broken_declaration_aborts_the_startup(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(3) 段 (e): `triggers.yaml` が壊れている → `TriggerDefinitionsError`。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))
    (trigger_root / "triggers.yaml").write_text("triggers: [\n  - broken", encoding="utf-8")

    with pytest.raises(TriggerDefinitionsError):
        build(_deps())


def test_4_an_unreadable_config_aborts_the_startup(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(4) 段 (d): `config.json` が読めない → `TriggerConfigError`（**絶対パス**を載せる）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "config.json が壊れている"
        raise ValueError(msg)

    monkeypatch.setattr("trigger.TriggerConfig", _boom)

    with pytest.raises(TriggerConfigError) as caught:
        build(_deps())

    assert str(config_path()) in str(caught.value)


def test_5_an_invalid_downloads_dir_aborts_the_startup(
    trigger_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(5) 段 (c): `DOWNLOADS_DIR` が**設定されていて**不正 → `TriggerRootError`。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(tmp_path / "never"))

    with pytest.raises(TriggerRootError, match="DOWNLOADS_DIR"):
        build(_deps())


def test_only_the_first_failing_step_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**最初に失敗した段だけを報告する**（`TRIGGER_ROOT` が不正なら (e) までは進まない）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(tmp_path / "never"))
    monkeypatch.setenv("DOWNLOADS_DIR", str(tmp_path / "also_never"))

    with pytest.raises(TriggerRootError, match="実在しない"):
        build(_deps())


def test_all_startup_errors_share_one_base(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-05: 起動時例外 4 種は `TriggerStartupError` を基底に持つ（`AppError` は継がない）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root / "never"))

    with pytest.raises(TriggerStartupError):
        build(_deps())


# -- T062 残りの表 ------------------------------------------------------------------------


def test_build_succeeds_without_a_downloads_dir(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-011・FR-074: **`DOWNLOADS_DIR` 未設定でも `build()` は成功する**。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    assert build(_deps()) is not None


def test_a_valid_downloads_dir_is_accepted(
    trigger_root: Path, downloads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 (c): 設定されていて正しければ通る。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads_dir))

    assert build(_deps()) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"misfire_grace_sec": 60, "min_interval_sec": 60},
        {"min_interval_sec": 600, "ready_interval_default_sec": 300},
        {"poll_interval_sec": 0.0},
        {"poll_interval_sec": -1.0},
        {"stable_seconds": 0},
        {"timezone": "Mars/Olympus"},
    ],
    ids=[
        "grace_not_below_min",
        "min_above_default",
        "poll_zero",
        "poll_negative",
        "stable_below_one",
        "unknown_timezone",
    ],
)
def test_an_invariant_violation_aborts_the_startup(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    """段 (d): **不変条件 3 種**の違反 → `TriggerConfigError`（起動中断＝ FR-075）。

    1. `misfire_grace_sec < min_interval_sec <= ready_interval_default_sec`
    2. `poll_interval_sec > 0`・`stable_seconds >= 1`
    3. `timezone` が `None` でなければ `ZoneInfo` で解決できる
    """
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    with pytest.raises(TriggerConfigError):
        build(_deps(), config=TriggerConfig(**overrides))


def test_the_shipped_configuration_satisfies_the_invariants(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """出荷時の `config.json`（7 キー）は不変条件を満たす（30 < 60 ≤ 300）。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    assert build(_deps()) is not None


def test_the_shipped_configuration_has_exactly_seven_keys() -> None:
    """CFG-62〜68 の **7 キーちょうど**（`extra="forbid"` なので余分は起動中断）。"""
    import json

    raw = json.loads(config_path().read_text(encoding="utf-8"))

    assert set(raw) == {
        "ready_interval_default_sec",
        "min_interval_sec",
        "misfire_grace_sec",
        "poll_interval_sec",
        "stable_seconds",
        "watcher_enabled",
        "timezone",
    }
    assert raw == {
        "ready_interval_default_sec": 300,
        "min_interval_sec": 60,
        "misfire_grace_sec": 30,
        "poll_interval_sec": 5.0,
        "stable_seconds": 2,
        "watcher_enabled": True,
        "timezone": None,
    }


def test_the_config_argument_is_the_only_way_to_swap_watcher_enabled(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-67・R-13: `build(deps, config=TriggerConfig(watcher_enabled=False))` が効く。"""
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))

    handle = build(_deps(), config=TriggerConfig(watcher_enabled=False))

    assert handle is not None


def test_the_configuration_cannot_be_overridden_by_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`settings_customise_sources` を絞ってあるので**環境変数で上書きされない**。"""
    monkeypatch.setenv("stable_seconds", "99")
    monkeypatch.setenv("STABLE_SECONDS", "99")
    monkeypatch.setenv("watcher_enabled", "false")

    config = TriggerConfig()

    assert config.stable_seconds == 2
    assert config.watcher_enabled is True


def test_an_unknown_configuration_key_is_rejected() -> None:
    """`extra="forbid"`: 未知キーは受け付けない。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TriggerConfig(unknown_key=1)


# -- 止め木の対（R-13） -------------------------------------------------------------------


def test_the_stopper_pair_makes_build_read_the_temporary_env(
    trigger_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-13: `env_file_path()` を差し替えると `build()` が**その `.env` を読む**。

    ⚠ `env_file_path()` の module 関数・`TriggerEnv` の明示 `__init__`・`build()` 側の
    `TriggerEnv(_env_file=env_file_path())` の **3 つが対で**初めて止め木が効く。どれかを
    落とすと `model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**になる。
    """
    fake_env = tmp_path / "fake.env"
    fake_env.write_text(f"TRIGGER_ROOT={trigger_root}\n", encoding="utf-8")
    monkeypatch.setattr("trigger.env_file_path", lambda: fake_env)

    handle = build(_deps())

    assert handle is not None
    assert (trigger_root / "triggers.yaml").is_file(), "差し替えた .env が読まれていない"
