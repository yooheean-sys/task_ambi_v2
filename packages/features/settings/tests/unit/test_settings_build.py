"""`build()` の起動時 6 段（plan (vii)・FR-062・R-08）。**最初に失敗した段だけ**を報告する。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter
from settings_testing import (
    config_for_tests,
    stub_deps,
    write,
    write_agents,
    write_minimal_area_a,
    write_policy,
)

import settings
from settings import SettingsConfig, build, config_path
from settings.exceptions import (
    SettingsAreaError,
    SettingsConfigError,
    SettingsEnvError,
    SettingsRootError,
    SettingsValidationError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _use_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """止め木を通したまま `SETTINGS_ROOT` を一時ディレクトリへ向ける。"""
    monkeypatch.setenv("SETTINGS_ROOT", str(root))


# -- (a) SETTINGS_ROOT が読めない -------------------------------------------------------


def test_a_missing_env_key_aborts_with_the_key_and_the_env_path() -> None:
    """SC-009 a: 必要なキー名と**読んだ `.env` の絶対パス**が失敗に載る。"""
    with pytest.raises(SettingsEnvError) as excinfo:
        build(stub_deps())
    message = str(excinfo.value)
    assert "SETTINGS_ROOT" in message
    assert str(settings.env_file_path()) in message


def test_a_unreadable_env_file_is_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) `.env` が UTF-8 で読めなくても**生の例外を漏らさない**（FR-062）。

    `UnicodeDecodeError` がそのまま build の外へ出ると、`SettingsStartupError` 階層に閉じる
    という data-model §5 の構図が破れ、読んだ `.env` の絶対パスも失われる。
    """
    broken = tmp_path / ".env"
    broken.write_bytes(b"SETTINGS_ROOT=\xff\xfe\n")
    monkeypatch.setattr("settings.env_file_path", lambda: broken)
    with pytest.raises(SettingsEnvError) as excinfo:
        build(stub_deps())
    assert str(broken) in str(excinfo.value)


# -- (b) 絶対パスでない／実在しない／ディレクトリでない ---------------------------------


@pytest.mark.parametrize(
    "raw",
    [pytest.param("", id="空値"), pytest.param("relative_area", id="相対パス")],
)
def test_b_non_absolute_root_aborts_without_writing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """(b) 絶対パスでない `SETTINGS_ROOT` は中断する（**cwd に 1 件も書かない**）。

    空値を pydantic は `Path(".")` として受理する。絶対パスの検査が無いと `Path(".")` が cwd に
    解決されて `exists()`・`is_dir()` の両方が真になり、(d1) が**カレントディレクトリに雛形 6
    ファイルを書き込む**（FR-019・SC-015 が 1 回で観測可能な被害と呼ぶ書込）。
    """
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("SETTINGS_ROOT", raw)
    with pytest.raises(SettingsRootError, match="絶対パスでない"):
        build(stub_deps())
    assert list(workdir.iterdir()) == []


# -- (b) 実在しない／ディレクトリでない -------------------------------------------------


def test_b_absent_root_aborts_with_the_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-009 b: 見に行った絶対パスが載る。**settings はルートを作らない**。"""
    target = tmp_path / "無い場所"
    _use_root(monkeypatch, target)
    with pytest.raises(SettingsRootError, match="実在しない"):
        build(stub_deps())
    assert not target.exists()


def test_b_non_directory_root_is_a_separate_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`exists()` で先に分けるので「不在」と「ディレクトリでない」が別のメッセージになる。"""
    target = tmp_path / "file"
    target.write_text("", encoding="utf-8")
    _use_root(monkeypatch, target)
    with pytest.raises(SettingsRootError, match="ディレクトリでない"):
        build(stub_deps())


# -- (c) config.json が読めない ---------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('{"system_actor": "system", "unknown_key": "x"}', id="未知キー"),
        pytest.param('{"context_reserve_tokens": 4096}', id="必須値欠け"),
    ],
)
def test_c_broken_config_aborts_with_the_absolute_path(
    settings_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """SC-009 c: 必須値欠け・未知キーは中断し、**見に行った絶対パス**が載る。"""
    broken = tmp_path / "broken_config.json"
    broken.write_text(body, encoding="utf-8")
    monkeypatch.setattr("settings.config_path", lambda: broken)
    _use_root(monkeypatch, settings_root)
    with pytest.raises(SettingsConfigError) as excinfo:
        build(stub_deps())
    assert str(broken) in str(excinfo.value)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('{"system_actor": "system",,}', id="構文誤り"),
        pytest.param("[1, 2, 3]", id="トップレベルが配列"),
        pytest.param("", id="空ファイル"),
    ],
)
def test_c_unparsable_config_is_translated_with_the_absolute_path(
    settings_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """(c) **JSON として読めない** `config.json` も同じ段に畳む（FR-062・SC-009 c）。

    `JsonConfigSettingsSource` は `json.loads` を呼ぶので、構文誤り・空ファイルは
    `JSONDecodeError`、トップレベルが配列なら `TypeError` になる。どちらも
    `ValidationError` ではないため、`except ValidationError` だけでは生の例外が
    build の外へ漏れ、見に行った絶対パスも載らない。
    """
    broken = tmp_path / "broken_config.json"
    broken.write_text(body, encoding="utf-8")
    monkeypatch.setattr("settings.config_path", lambda: broken)
    _use_root(monkeypatch, settings_root)
    with pytest.raises(SettingsConfigError) as excinfo:
        build(stub_deps())
    assert str(broken) in str(excinfo.value)


def test_config_path_is_cwd_independent() -> None:
    """規約の固定相対パス（憲章 §5）。`config.json` は cwd に依らず解決できる。"""
    assert config_path().is_file()
    assert config_path().name == "config.json"


def test_config_rejects_unknown_keys() -> None:
    """`extra="forbid"`（FR-061 と対）: 未知キーは (c) 段で落ちる。"""
    from pydantic import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError):
        SettingsConfig(unknown_key="x")


def test_config_has_no_env_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-061: モジュール設定は**環境変数で上書きできない**。"""
    monkeypatch.setenv("SYSTEM_ACTOR", "侵入")
    monkeypatch.setenv("CONTEXT_RESERVE_TOKENS", "1")
    loaded = SettingsConfig()
    assert loaded.system_actor != "侵入"
    assert loaded.context_reserve_tokens != 1


# -- (d1) 雛形生成 / (d2) 読取 -----------------------------------------------------------


def test_d1_empty_root_succeeds_with_zero_projects(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US3 AC-3・FR-017: 中身が空でも**成功する**（PJ 0 件で起動する）。"""
    _use_root(monkeypatch, settings_root)
    handle = build(stub_deps())
    assert isinstance(handle.router, APIRouter)
    assert (settings_root / "global/model.conf").is_file()


def test_d2_syntax_errors_are_collected(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US3 AC-5・AC-13: 構文誤りは**打ち切らずに集め**、壊れた場所を載せる。"""
    write_minimal_area_a(settings_root)
    write(settings_root, "global/agents.yaml", "planner:\n  role: a\nplanner:\n  role: b\n")
    write(settings_root, "global/commands.yaml", "\t不正なタブ\n")
    _use_root(monkeypatch, settings_root)
    with pytest.raises(SettingsAreaError) as excinfo:
        build(stub_deps())
    issues = excinfo.value.issues
    assert len(issues) >= 2
    assert any("agents.yaml" in issue and "duplicate key" in issue for issue in issues)
    assert any(str(settings_root) in issue for issue in issues)


def test_d2_missing_required_project_file_aborts(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-09・R-10: PJ ごとの必須ファイル不在は**起動中断**（黙って補わない）。"""
    write_minimal_area_a(settings_root)
    (settings_root / "projects/pj/project.yaml").unlink()
    _use_root(monkeypatch, settings_root)
    with pytest.raises(SettingsAreaError) as excinfo:
        build(stub_deps())
    assert any("project.yaml" in issue for issue in excinfo.value.issues)


def test_d2_missing_humans_file_is_a_read_stage_failure(settings_root: Path) -> None:
    """SD-33: `humans.yaml` の**不在**は読取段の失敗である（他の必須ファイルと同じ扱い）。

    読取段だけを単独で回すとこう落ちる。**build を通る限りファイルは必ず存在する**のは、
    (d1) の雛形生成が先に空の骨格を作るからである（→ 次のテスト）。US5 AC-5 が「空の宣言」で
    書かれているのはこの構造の帰結である。
    """
    from settings.repository import AreaAReader  # noqa: PLC0415
    from settings.service.loader import read_area  # noqa: PLC0415

    write_minimal_area_a(settings_root)
    (settings_root / "global/humans.yaml").unlink()
    with pytest.raises(SettingsAreaError) as excinfo:
        read_area(AreaAReader(settings_root))
    assert any("humans.yaml" in issue for issue in excinfo.value.issues)


def test_d1_regenerates_a_deleted_common_side_file(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-016: 共通側の不在ファイルは (d1) が作り直す（PJ ごとのファイルは作らない）。"""
    write_minimal_area_a(settings_root)
    (settings_root / "global/humans.yaml").unlink()
    _use_root(monkeypatch, settings_root)
    build(stub_deps())
    assert (settings_root / "global/humans.yaml").read_text("utf-8") == "human_actors: []\n"


# -- (d3) 整合検査 ----------------------------------------------------------------------


def test_d3_validation_problems_abort_with_a_list(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d3): 整合検査 **3 種すべて**を走らせ、違反を**一覧**で返す（US3 AC-13）。"""
    write_minimal_area_a(settings_root)
    write_agents(settings_root, "pj", {"programmer": "\n  nickname: x", "wizard": " {}"})
    write_policy(settings_root, "pj", ["p, member:pj.ghost, tool:read_issue, execute"])
    _use_root(monkeypatch, settings_root)
    with pytest.raises(SettingsValidationError) as excinfo:
        build(stub_deps())
    kinds = {item.kind for item in excinfo.value.problems}
    assert {"unknown_field", "agent_ref", "orphan"} <= kinds
    assert len(excinfo.value.problems) >= 3


def test_validation_error_is_not_a_startup_error() -> None:
    """data-model §5: `SettingsValidationError` は `SettingsStartupError` を継承しない。

    後半では同じ例外が **HTTP 422** に写るため、起動失敗と保存拒否を同じ木にしない。
    """
    from settings.exceptions import SettingsStartupError  # noqa: PLC0415

    assert not issubclass(SettingsValidationError, SettingsStartupError)
    assert issubclass(SettingsAreaError, SettingsStartupError)
    assert issubclass(SettingsStartupError, RuntimeError)


def test_startup_errors_do_not_inherit_app_error() -> None:
    """層契約により settings は apps/api の起動時例外階層を参照できない（board・vault と同じ）。"""
    from common.kernel.errors import AppError  # noqa: PLC0415
    from settings.exceptions import SettingsStartupError  # noqa: PLC0415

    assert not issubclass(SettingsStartupError, AppError)


# -- 段の順序（最初に失敗した段だけを報告する） ----------------------------------------


def test_the_first_failing_stage_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) と (d) が同時に壊れていても、報告されるのは (b) だけである。"""
    _use_root(monkeypatch, tmp_path / "無い場所")
    with pytest.raises(SettingsRootError):
        build(stub_deps())


# -- config 差し替え（FR-063） ----------------------------------------------------------


def test_config_can_be_replaced_through_the_build_argument(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-063: 組み立て口が**モジュール設定の差し替えを任意引数で受ける**。"""
    write_minimal_area_a(settings_root)
    _use_root(monkeypatch, settings_root)
    replaced = config_for_tests(context_reserve_tokens=1024)
    handle = build(stub_deps(), config=replaced)
    from common.kernel.enums import PersonaKey  # noqa: PLC0415

    member = handle.member_resolver.resolve("pj", PersonaKey.programmer)
    assert member.llm_profile.threshold_t == 32768 - 1024


# -- 組み立て（FR-005・DIFF-01） --------------------------------------------------------


def test_the_four_contracts_are_the_same_instance(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-005: 4 つの契約実装は**同一インスタンス**（スナップショットが 4 つに割れない）。"""
    write_minimal_area_a(settings_root)
    _use_root(monkeypatch, settings_root)
    handle = build(stub_deps())
    assert handle.settings_reader is handle.member_resolver
    assert handle.member_resolver is handle.authorizer
    assert handle.authorizer is handle.command_registry


def test_router_is_an_api_router(settings_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-081: 組み立ての戻りは**必ず口を持つ**（後半で `router` が実体になった＝ 010 SD-15）。"""
    write_minimal_area_a(settings_root)
    _use_root(monkeypatch, settings_root)
    assert isinstance(build(stub_deps()).router, APIRouter)


def test_build_logs_completion(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-051: `settings.build.completed` に世代・PJ 件数・メンバ件数が載る。"""
    write_minimal_area_a(settings_root)
    _use_root(monkeypatch, settings_root)
    with caplog.at_level(logging.INFO, logger="settings.repository"):
        build(stub_deps())
    records = [item for item in caplog.records if item.getMessage() == "settings.build.completed"]
    assert len(records) == 1
    assert getattr(records[0], "generation", None) == 1
    assert getattr(records[0], "projects", None) == 1
    assert getattr(records[0], "members", None) == 1


def test_second_build_on_a_generated_area_succeeds(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-003・US6 AC-5: 空の領域 A から 2 回組み立てて 2 回とも成功する。

    生成した雛形が**自分の整合検査を通る**ことの直接の観測である。
    """
    _use_root(monkeypatch, settings_root)
    assert isinstance(build(stub_deps()).router, APIRouter)
    assert isinstance(build(stub_deps()).router, APIRouter)
