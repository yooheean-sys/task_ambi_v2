"""ルート config.json が設定台帳と過不足 0 で一致することの検査（FR-016・FR-017・SC-005）。

期待キー集合は `docs/design/appendix_env_design/x3_config_catalog.csv` を**直接読んで**導出する
（テスト内にハードコードしない）。台帳を書き換えれば検査が追随するため、台帳と実装の乖離が
構造的に起きない。台帳キー名の `root.` は「どのファイルに属するか」を示す scope 記号であり、
JSON の階層ではない（x3_notes §3）。

既定値の照合からは CFG-20（enabled_modules）を除外する。台帳の既定値（8 モジュール全列挙）は
**完成形の姿**であり、実体はモジュールが実在した分だけ追記されて完成形へ近づくから
（FR-018・DIFF-07）。除外しないと本検査は必ず落ちる（台帳の既定値列は設計同期 A-6 でも変更しない）。
CFG-20 は別途「**完成形の部分集合**であること」を assert する（`003` T089・design-sync B-7。
`002` 完了時点の姿を固定した `== []` は、最初の feature が実在した時点で誤りになった）。
"""

from __future__ import annotations

import ast
import csv
import json
import tomllib
from pathlib import Path
from typing import Any, cast

# 既定値の照合から外す台帳行。理由は本モジュールの docstring を参照（完成形と初期値の差）。
DEFAULT_CHECK_EXCLUDE = {"CFG-20"}

REQUIRED_KEYS = {"app_name", "env_label", "enabled_modules"}
SCOPE_PREFIX = "root."


def _repo_root() -> Path:
    """[tool.uv.workspace] を持つ pyproject.toml を上方向に探す（cwd 非依存。research R-09）。"""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            data: dict[str, Any] = tomllib.loads(candidate.read_text(encoding="utf-8"))
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return parent
    raise AssertionError(
        "workspace ルート（[tool.uv.workspace] を持つ pyproject.toml）が見つからない"
    )


def _catalog_rows(root: Path) -> list[dict[str, str]]:
    """台帳のうち、ルート config.json（配置先 c1・キー名が root. 始まり）の行。"""
    path = root / "docs" / "design" / "appendix_env_design" / "x3_config_catalog.csv"
    assert path.is_file(), f"設定台帳が見つからない: {path}"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = [
        row
        for row in rows
        if row["配置先(a/b/c)"] == "c1" and row["キー名"].startswith(SCOPE_PREFIX)
    ]
    assert selected, "台帳から c1 かつ root. 始まりの行が 1 件も取れない（列名の変更を疑う）"
    return selected


def _config(root: Path) -> dict[str, Any]:
    path = root / "config.json"
    assert path.is_file(), f"ルート config.json が実在しない: {path}"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _leaf_keys(data: dict[str, Any], prefix: str = "") -> list[str]:
    leaves: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            leaves.extend(_leaf_keys(cast(dict[str, Any], value), f"{prefix}{key}."))
        else:
            leaves.append(f"{prefix}{key}")
    return leaves


def _leaf_value(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        current = current[part]
    return current


def _parse_default(raw: str) -> Any:
    """台帳の既定値セルを Python 値として読む（リテラルでなければ文字列のまま）。"""
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


def test_leaf_keys_match_catalog_exactly() -> None:
    """リーフキー集合が台帳と過不足 0 で一致する（SC-005・US3 AC1）。"""
    root = _repo_root()
    expected = sorted(row["キー名"][len(SCOPE_PREFIX) :] for row in _catalog_rows(root))
    actual = sorted(_leaf_keys(_config(root)))
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    assert not missing and not extra, (
        "ルート config.json のリーフキーが台帳（x3_config_catalog.csv の c1・root.*）と一致しない。"
        f" 台帳にあって config.json に無い: {missing} /"
        f" config.json にあって台帳に無い: {extra}"
    )


def test_top_level_keys_are_five_without_scope_nesting() -> None:
    """最上位キーは 5 つ。台帳の scope 記号 `root.` を JSON の入れ子にしない（x3_notes §3）。"""
    config = _config(_repo_root())
    assert "root" not in config, (
        "台帳キー名の `root.` は scope 記号であり JSON の階層ではない。"
        " config.json に `root` という入れ子を作らないこと（x3_notes §3）"
    )
    assert set(config) == {"app_name", "env_label", "log", "enabled_modules", "defaults"}, (
        f"最上位キーは 5 つ（FR-016）。現値: {sorted(config)}"
    )


def test_required_keys_present_and_typed_as_catalog() -> None:
    """必須／任意の別と型が台帳と一致する（FR-017・US3 AC2）。"""
    root = _repo_root()
    config = _config(root)
    problems: list[str] = []
    for row in _catalog_rows(root):
        dotted = row["キー名"][len(SCOPE_PREFIX) :]
        required = row["必須/任意"].startswith("必須")
        declared_type = row["型"]
        try:
            value = _leaf_value(config, dotted)
        except KeyError:
            if required:
                problems.append(f"{row['cfg_id']} {dotted}: 必須なのに config.json に無い")
            continue
        if declared_type.startswith("list["):
            if not isinstance(value, list):
                problems.append(
                    f"{row['cfg_id']} {dotted}: 型が list でない（{type(value).__name__}）"
                )
        elif declared_type.startswith("str") and not isinstance(value, str):
            problems.append(f"{row['cfg_id']} {dotted}: 型が str でない（{type(value).__name__}）")
    assert not problems, "台帳と config.json の必須／型が食い違う: " + "; ".join(problems)


def test_required_key_set_matches_catalog() -> None:
    """必須キーが台帳の「必須」行と一致する（3 キー: app_name・env_label・enabled_modules）。"""
    root = _repo_root()
    catalog_required = {
        row["キー名"][len(SCOPE_PREFIX) :]
        for row in _catalog_rows(root)
        if row["必須/任意"].startswith("必須")
    }
    assert catalog_required == REQUIRED_KEYS, (
        "台帳の必須キーが想定と違う。"
        f"台帳: {sorted(catalog_required)} / 想定: {sorted(REQUIRED_KEYS)}"
    )


def test_defaults_match_catalog_except_enabled_modules() -> None:
    """既定値が台帳と一致する。CFG-20 のみ除外する（理由はモジュール docstring）。"""
    root = _repo_root()
    config = _config(root)
    mismatches: list[str] = []
    for row in _catalog_rows(root):
        if row["cfg_id"] in DEFAULT_CHECK_EXCLUDE:
            continue
        dotted = row["キー名"][len(SCOPE_PREFIX) :]
        expected = _parse_default(row["既定値"])
        actual = _leaf_value(config, dotted)
        if actual != expected:
            mismatches.append(f"{row['cfg_id']} {dotted}: 台帳 {expected!r} / 実体 {actual!r}")
    assert not mismatches, "既定値が台帳と一致しない: " + "; ".join(mismatches)


def _catalog_enabled_modules(root: Path) -> list[str]:
    """CFG-20 の既定値セル（完成形の 8 モジュール全列挙）を名前の並びとして読む。

    セルは `[board,vault,…]` と裸の識別子で書かれており `ast.literal_eval` では読めないため、
    角括弧を外してカンマで分ける（`_parse_default` が文字列のまま返すのはこのため）。
    """
    for row in _catalog_rows(root):
        if row["cfg_id"] == "CFG-20":
            raw = row["既定値"].strip().strip("[]")
            return [name.strip().strip("\"'") for name in raw.split(",") if name.strip()]
    raise AssertionError("台帳に CFG-20（root.enabled_modules）の行が無い")


def test_enabled_modules_is_a_subset_of_the_catalog_default() -> None:
    """CFG-20 は既定値照合から外す代わりに、**完成形の部分集合**であることを assert する。

    台帳の既定値（8 モジュール全列挙）は完成形の姿であり、実体はモジュールが実在した分だけ
    追記されて完成形へ近づく（FR-018・DIFF-07）。したがってここで見るのは
    (1) 重複が無いこと、(2) 台帳の完成形に無い名前が混ざっていないこと、の 2 点である。

    「有効化されているが**まだ実在しない**モジュール」を落とすのは本検査ではなく
    `test_module_registration.py` の **REG-6**（S3 ⊆ S0）である。1 つの事象で 2 本が赤くならない
    よう、責務を分けたまま保つこと。
    """
    root = _repo_root()
    enabled = cast("list[str]", _config(root)["enabled_modules"])
    catalog = _catalog_enabled_modules(root)

    duplicates = sorted({name for name in enabled if enabled.count(name) > 1})
    assert not duplicates, f"enabled_modules に重複がある: {duplicates}"

    unknown = sorted(set(enabled) - set(catalog))
    assert not unknown, (
        f"enabled_modules に台帳 CFG-20 の完成形に無い名前がある（台帳: {catalog}）: {unknown}"
    )


def test_no_secret_like_values() -> None:
    """秘密（API キー様式）を config.json に置かない（SC-008・US3 AC3・FR-020）。"""
    raw = (_repo_root() / "config.json").read_text(encoding="utf-8")
    lowered = raw.lower()
    hits = [word for word in ("sk-", "api_key=", "password", "secret", "token") if word in lowered]
    # redact_keys の値（"*_API_KEY*" 等）はマスク対象のキー名パターンであり秘密そのものではない
    hits = [word for word in hits if word != "api_key="]
    assert not hits, f"config.json に秘密らしき文字列がある（SC-008）: {hits}"
