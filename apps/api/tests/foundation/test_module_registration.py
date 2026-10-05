"""モジュール登録 3 点の整合検査（FR-011・FR-012・FR-013・FR-018・SC-003・SC-004）。

モジュールを 1 つ増やすとき、土台側で追記が要るのは 3 ファイル（ルート pyproject.toml・
.importlinter・ルート config.json）。1 箇所でも怠ると「保護されていないのに緑」になるため、
4 集合 S0〜S3 の一致を機械的に突き合わせる。判定規則は
specs/000-workspace-foundation/contracts/module-registration.md §3（REG-1〜REG-7）。

本 feature 完了時点は S0 = S1∩features = S2 = S3 = ∅ であり、**空集合どうしの一致で通ること自体は
保護の証明にならない**。実際に何かを保証しているのは REG-5（root_packages の各要素が import 可能）
だけである。負の検証は quickstart S5 (b) が担う。
"""

from __future__ import annotations

import configparser
import fnmatch
import importlib.util
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

NON_FEATURE_ROOTS = {"api", "ui", "common"}


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


def _lines(value: str) -> list[str]:
    return [ln.strip() for ln in value.splitlines() if ln.strip()]


@pytest.fixture(scope="module")
def root() -> Path:
    return _repo_root()


@pytest.fixture(scope="module")
def known_modules(root: Path) -> set[str]:
    """既知 9 種（正本は docs/design/phase4/p4_importlinter.toml の root_packages）。"""
    design = root / "docs" / "design" / "phase4" / "p4_importlinter.toml"
    assert design.is_file(), f"既知 9 種の正本が見つからない: {design}"
    data: dict[str, Any] = tomllib.loads(design.read_text(encoding="utf-8"))
    raw: Any = data["tool"]["importlinter"]["root_packages"]
    return {str(name) for name in raw} - NON_FEATURE_ROOTS


@pytest.fixture(scope="module")
def s0_existing(root: Path) -> set[str]:
    """S0: 実在 feature パッケージ（packages/features/*/src/<name>/）。正の集合。"""
    return {
        src.name
        for src in (root / "packages" / "features").glob("*/src/*")
        if src.is_dir() and src.name != "__pycache__"
    }


@pytest.fixture(scope="module")
def s1_workspace(root: Path) -> dict[str, Any]:
    """S1: ルート pyproject.toml の dependencies・[tool.uv.sources]・members glob。"""
    data: dict[str, Any] = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps_raw: Any = data["project"]["dependencies"]
    sources_raw: Any = data["tool"]["uv"]["sources"]
    members_raw: Any = data["tool"]["uv"]["workspace"]["members"]
    return {
        "dependencies": {
            str(d).split("[")[0].split(">")[0].split("=")[0].strip() for d in deps_raw
        },
        "sources": {str(k) for k in sources_raw},
        "members": [str(m) for m in members_raw],
    }


@pytest.fixture(scope="module")
def s2_contracts(root: Path) -> dict[str, set[str]]:
    """S2: .importlinter の root_packages・independence.modules・layers の feature 層。"""
    path = root / ".importlinter"
    assert path.is_file(), f".importlinter が実在しない: {path}"
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    root_packages = set(_lines(parser["importlinter"]["root_packages"]))
    modules = set(_lines(parser["importlinter:contract:features-independent"]["modules"]))
    layer_lines = _lines(parser["importlinter:contract:layers"]["layers"])
    # feature 層は最上層（api | ui）と最下層（common）に挟まれた中間行。feature 0 件なら存在しない
    middle = layer_lines[1:-1]
    layers = {name.strip() for line in middle for name in line.split("|") if name.strip()}
    return {
        "root_packages": root_packages,
        "root_features": root_packages - NON_FEATURE_ROOTS,
        "independence": modules,
        "layers": layers,
    }


@pytest.fixture(scope="module")
def s3_enabled(root: Path) -> list[str]:
    """S3: ルート config.json の enabled_modules（重複検査のため list のまま返す）。"""
    path = root / "config.json"
    assert path.is_file(), f"ルート config.json が実在しない: {path}"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw: Any = data["enabled_modules"]
    return [str(name) for name in raw]


def test_reg1_existing_features_are_declared_in_workspace(
    s0_existing: set[str], s1_workspace: dict[str, Any]
) -> None:
    """REG-1: S0 ⊆ S1（dependencies と [tool.uv.sources] の双方に現れる）。"""
    deps: set[str] = s1_workspace["dependencies"]
    sources: set[str] = s1_workspace["sources"]
    missing = {
        name: [
            place
            for place, declared in (
                ("[project.dependencies]", deps),
                ("[tool.uv.sources]", sources),
            )
            if name not in declared
        ]
        for name in sorted(s0_existing)
    }
    missing = {name: places for name, places in missing.items() if places}
    assert not missing, (
        "実在する feature がルート pyproject.toml に登録されていない（登録点 1/3）。"
        " モジュール名ごとに追記先を示す: "
        + "; ".join(f"{name} → {', '.join(places)} に追記" for name, places in missing.items())
    )


def test_reg2_declared_features_exist(
    s0_existing: set[str], s1_workspace: dict[str, Any], known_modules: set[str]
) -> None:
    """REG-2: S1 ⊆ S0（dependencies の feature 名に実在しないものが無い）。"""
    deps: set[str] = s1_workspace["dependencies"]
    declared_features = (deps - NON_FEATURE_ROOTS) & (known_modules | s0_existing)
    orphans = sorted(declared_features - s0_existing)
    assert not orphans, (
        "ルート pyproject.toml の [project.dependencies] に実在しない feature 名がある。"
        " 当該モジュールを packages/features/<mod>/src/<mod>/ に作るか、行を削ること: "
        f"{orphans}"
    )


def test_reg3_existing_features_match_members_glob(
    root: Path, s0_existing: set[str], s1_workspace: dict[str, Any]
) -> None:
    """REG-3: 各 s ∈ S0 が [tool.uv.workspace] members のいずれかの glob に一致する。"""
    members: list[str] = s1_workspace["members"]
    unmatched: list[str] = []
    for name in sorted(s0_existing):
        rel = f"packages/features/{name}"
        if not any(fnmatch.fnmatch(rel, pattern) for pattern in members):
            unmatched.append(rel)
    assert not unmatched, (
        "実在 feature のパスが [tool.uv.workspace] members のどの glob にも一致しない。"
        f" 一致しなかったパス: {unmatched} / members の現値: {members}"
        f" （リポジトリルート: {root}）"
    )


def test_reg4_existing_features_match_boundary_contracts(
    s0_existing: set[str], s2_contracts: dict[str, set[str]]
) -> None:
    """REG-4: S0 == S2 を root_packages・independence.modules・layers の 3 箇所すべてで照合する。"""
    places = {
        "[importlinter] root_packages": s2_contracts["root_features"],
        "[importlinter:contract:features-independent] modules": s2_contracts["independence"],
        "[importlinter:contract:layers] layers の feature 層": s2_contracts["layers"],
    }
    problems = {
        place: {"欠けている": sorted(s0_existing - actual), "余分": sorted(actual - s0_existing)}
        for place, actual in places.items()
        if actual != s0_existing
    }
    assert not problems, (
        ".importlinter の対象列が実在 feature と一致しない（登録点 2/3）。3 箇所を個別に示す: "
        + "; ".join(
            f"{place} → 欠けている {diff['欠けている']} / 余分 {diff['余分']}"
            for place, diff in problems.items()
        )
    )


def test_reg5_contract_packages_are_importable(s2_contracts: dict[str, set[str]]) -> None:
    """REG-5: root_packages の全要素が import 可能（FR-011）。

    実在しない名前を書くと `lint-imports` が "Could not find package" で **実行不能**になり、
    「保護が動いていない」と「違反がある」の区別がつかなくなる。
    """
    unresolved: list[str] = []
    for name in sorted(s2_contracts["root_packages"]):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            unresolved.append(name)
    assert not unresolved, (
        "[importlinter] root_packages に import 解決できない名前がある（FR-011）。"
        " `uv sync` で .venv に入っていないか、パッケージが未作成。"
        " 未作成なら root_packages から外し、作成時に戻すこと: "
        f"{unresolved}"
    )


def test_reg6_enabled_modules_exist(s0_existing: set[str], s3_enabled: list[str]) -> None:
    """REG-6: S3 ⊆ S0（US3 AC4）。S0 \\ S3（実在するが未有効化）は失敗にしない（FR-018）。"""
    phantom = sorted(set(s3_enabled) - s0_existing)
    assert not phantom, (
        "ルート config.json の enabled_modules に実在しないモジュール名がある"
        "（登録点 3/3・FR-018）。"
        " 当該モジュールを作るか、enabled_modules から外すこと: "
        f"{phantom}"
    )


def test_reg7_enabled_modules_are_unique_and_known(
    s3_enabled: list[str], known_modules: set[str]
) -> None:
    """REG-7: S3 に重複が無く、要素が既知のモジュール名 9 種のいずれかである。"""
    duplicates = sorted({name for name in s3_enabled if s3_enabled.count(name) > 1})
    unknown = sorted(set(s3_enabled) - known_modules)
    assert not duplicates, (
        f"ルート config.json の enabled_modules に重複がある。重複した名前: {duplicates}"
    )
    assert not unknown, (
        "ルート config.json の enabled_modules に未知のモジュール名がある。"
        f" 既知 9 種（p4_importlinter.toml の root_packages から導出）: {sorted(known_modules)}"
        f" / 未知の名前: {unknown}"
    )
