"""SC-011: ルート `config.json` の読み口が台帳 CFG-14〜21 と一致し、cwd に依存しない。

期待キー集合は `docs/design/appendix_env_design/x3_config_catalog.csv` を**直接読んで**導出する
（テスト内にハードコードしない）。既定値の照合から CFG-20（`enabled_modules`）は外す
（台帳の既定値＝8 モジュール全列挙は**完成形**で、初期値は `[]`＝`000` DIFF-07）。

**起動を実際に中断する検証は `002` の担当**（本 feature は「欠落が識別できる形で失敗する」まで）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from common.kernel.config import (
    CONFIG_FILENAME,
    DefaultsConfig,
    LogConfig,
    RootConfig,
    config_path,
    find_repo_root,
)

DEFAULT_CHECK_EXCLUDE = {"CFG-20"}
SCOPE_PREFIX = "root."


def _catalog(design_root: Path) -> dict[str, dict[str, str]]:
    """CFG-14〜21（ルート config.json の行）を cfg_id → 行 で返す。"""
    path = design_root / "appendix_env_design" / "x3_config_catalog.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = {
        row["cfg_id"]: row
        for row in rows
        if row["配置先(a/b/c)"] == "c1" and row["キー名"].startswith(SCOPE_PREFIX)
    }
    assert selected, "台帳から c1 かつ root. 始まりの行が取れない（列名の変更を疑う）"
    return selected


def _field_for(dotted: str) -> tuple[type[Any], str]:
    """`root.log.level` のような台帳キー名を (保持クラス, フィールド名) に解決する。"""
    parts = dotted.removeprefix(SCOPE_PREFIX).split(".")
    holders: dict[str, type[Any]] = {"log": LogConfig, "defaults": DefaultsConfig}
    if len(parts) == 1:
        return RootConfig, parts[0]
    return holders[parts[0]], parts[1]


def _parse_default(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# --- (a) 台帳との一致 -------------------------------------------------------


def test_catalog_covers_cfg_14_to_21(design_root: Path) -> None:
    assert set(_catalog(design_root)) == {f"CFG-{n}" for n in range(14, 22)}


def test_keys_match_the_catalog(design_root: Path) -> None:
    expected = {row["キー名"] for row in _catalog(design_root).values()}
    actual = {
        f"{SCOPE_PREFIX}{name}"
        for name in RootConfig.model_fields
        if name not in {"log", "defaults"}
    }
    actual |= {f"{SCOPE_PREFIX}log.{name}" for name in LogConfig.model_fields}
    actual |= {f"{SCOPE_PREFIX}defaults.{name}" for name in DefaultsConfig.model_fields}
    assert actual == expected


def test_required_and_optional_match_the_catalog(design_root: Path) -> None:
    mismatched: list[str] = []
    for cfg_id, row in _catalog(design_root).items():
        holder, name = _field_for(row["キー名"])
        required = row["必須/任意"] == "必須"
        if holder.model_fields[name].is_required() != required:
            mismatched.append(f"{cfg_id} {row['キー名']}: 台帳は{row['必須/任意']}")
    assert mismatched == []


def test_enabled_modules_is_required_without_a_pydantic_default() -> None:
    """`enabled_modules` は必須キー。Pydantic 側に既定値を置かない（`000` DIFF-07）。"""
    assert RootConfig.model_fields["enabled_modules"].is_required()


def test_optional_defaults_match_the_catalog(design_root: Path) -> None:
    mismatched: list[str] = []
    for cfg_id, row in _catalog(design_root).items():
        if cfg_id in DEFAULT_CHECK_EXCLUDE or row["必須/任意"] != "任意":
            continue
        holder, name = _field_for(row["キー名"])
        if holder.model_fields[name].default != _parse_default(row["既定値"]):
            mismatched.append(f"{cfg_id}: {holder.model_fields[name].default!r}")
    assert mismatched == []


def test_types_match_the_catalog(design_root: Path) -> None:
    """台帳の型欄の先頭語（`str`／`list[str]`）が宣言の注釈に現れる。"""
    mismatched: list[str] = []
    for cfg_id, row in _catalog(design_root).items():
        holder, name = _field_for(row["キー名"])
        declared = str(holder.__annotations__[name])
        head = row["型"].split("（", 1)[0]
        if (
            head == "str"
            and not declared.startswith(("str", "Literal"))
            or head == "list[str]"
            and not declared.startswith("list[str]")
        ):
            mismatched.append(f"{cfg_id}: {declared}")
    assert mismatched == []


def test_required_values_match_the_catalog(design_root: Path) -> None:
    """必須 2 件は台帳の値がそのままルート `config.json` に入っている。"""
    config = RootConfig()
    assert config.app_name == _catalog(design_root)["CFG-14"]["既定値"]
    assert config.env_label == _catalog(design_root)["CFG-15"]["既定値"]


# --- (b) cwd 非依存 ---------------------------------------------------------


def test_repo_root_is_resolved_without_cwd(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert find_repo_root() == repo_root
    assert config_path() == repo_root / CONFIG_FILENAME


def test_same_file_is_read_from_any_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """別ファイルを静かに読まない。cwd に同名ファイルを置いても影響しない。"""
    before = RootConfig()
    (tmp_path / CONFIG_FILENAME).write_text(
        json.dumps({"app_name": "偽", "env_label": "偽", "enabled_modules": []}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert RootConfig() == before


# --- (c) 環境変数で上書きされない -------------------------------------------


def test_environment_variables_do_not_override(monkeypatch: pytest.MonkeyPatch) -> None:
    before = RootConfig()
    monkeypatch.setenv("APP_NAME", "hacked")
    monkeypatch.setenv("ENV_LABEL", "hacked")
    monkeypatch.setenv("ENABLED_MODULES", '["hacked"]')
    assert RootConfig() == before


# --- (d)(e)(f) 失敗の形 -----------------------------------------------------


def _point_at(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr("common.kernel.config.config_path", lambda: path)


def test_missing_required_key_is_identifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """必須キー欠落は `pydantic.ValidationError` で識別できる（FR-031）。"""
    broken = tmp_path / CONFIG_FILENAME
    broken.write_text(json.dumps({"env_label": "dev", "enabled_modules": []}), encoding="utf-8")
    _point_at(monkeypatch, broken)
    with pytest.raises(ValidationError) as caught:
        RootConfig()
    assert [e["loc"] for e in caught.value.errors()] == [("app_name",)]
    assert caught.value.errors()[0]["type"] == "missing"


def test_unknown_key_fails_at_construction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """台帳に無いキーがあれば構築時に失敗する（`extra="forbid"`）。"""
    broken = tmp_path / CONFIG_FILENAME
    broken.write_text(
        json.dumps({"app_name": "a", "env_label": "dev", "enabled_modules": [], "surprise": 1}),
        encoding="utf-8",
    )
    _point_at(monkeypatch, broken)
    with pytest.raises(ValidationError) as caught:
        RootConfig()
    assert caught.value.errors()[0]["type"] == "extra_forbidden"


def test_absent_file_names_the_path_it_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = tmp_path / CONFIG_FILENAME
    _point_at(monkeypatch, absent)
    with pytest.raises(FileNotFoundError) as caught:
        RootConfig()
    assert str(absent) in str(caught.value)


def test_corrupt_file_names_the_path_it_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / CONFIG_FILENAME
    broken.write_text("{ これは JSON ではない", encoding="utf-8")
    _point_at(monkeypatch, broken)
    with pytest.raises(ValueError) as caught:
        RootConfig()
    assert str(broken) in str(caught.value)


# --- (g) frozen ＋ 約束 7 ---------------------------------------------------


def test_root_config_is_frozen() -> None:
    config = RootConfig()
    with pytest.raises(ValidationError):
        config.app_name = "書き換え"


def test_nested_config_is_frozen() -> None:
    config = RootConfig()
    with pytest.raises(ValidationError):
        config.log.level = "DEBUG"


def test_init_arguments_win_over_the_file() -> None:
    """`⑧ build() の init 引数`による差し替え（contracts/kernel.md §4 約束 7）。"""
    config = RootConfig(env_label="test", enabled_modules=["board"])
    assert config.env_label == "test"
    assert config.enabled_modules == ["board"]
    assert config.app_name == RootConfig().app_name
