"""US4・US5: クラスタ層の規律を**レビューではなく機械**で守る。FR-001・FR-034・FR-041a〜FR-045。

すべて**静的走査**なので、クラスタ本体が 1 本も無くても動き、`-k`・`-m`・部分集合実行で結果が
変わらない（SC-006・SC-007）。

見るのは 6 つ。

1. **手配線の禁止** —— `<mod>.build(` の呼び出しと `from <mod> import build` がソースに無い
2. **`from conftest import` の禁止** —— 収集順で `sys.modules["conftest"]` を取られると壊れる
3. **`MockTransport` の字句禁止** —— Hook は httpx の transport 層の差し込み点である
4. **`requires_bd` の付与一致** —— 期待値も `x2_cluster_index.csv` から導く
5. **fixture の関数スコープ** —— テスト間に実行順序依存を作らない
6. **基名の一意性** —— `tests/` に `__init__.py` を置かないので基名が top-level module 名になる

本ファイルは規律検査そのものを置くファイルなので `contract_if` を書かない（規約 §7）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

from _support.cluster_sources import cluster_index_path, load_cluster_assignments
from _support.cluster_wiring import CLUSTER_MODULES

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from collections.abc import Iterator

CONTRACT_META = True
"""規律検査そのものを置くファイル（`collect_declarations` の走査対象から外す）。"""

CLUSTERS_ROOT_REL = "apps/api/tests/integration"
SUPPORT_ROOT_REL = "apps/api/tests/_support"

FEATURE_MODULES: tuple[str, ...] = (
    "board",
    "vault",
    "settings",
    "stages",
    "monitor",
    "executor",
    "orchestrator",
    "trigger",
)
"""手配線の禁止で見る feature モジュール名（`registry.py` の登録名と同じ 8 件）。"""

BEADS_EIF = "EIF-01"
"""beads を実物で通す外部インターフェース（`requires_bd` の期待値の出所）。"""

FUNCTION_SCOPES = ("module", "session", "package", "class")
"""クラスタの `conftest.py` に**現れてはならない** fixture のスコープ。"""

_MOCK_TRANSPORT = re.compile(r"\bMockTransport\b")
_CONFTEST_IMPORT = re.compile(r"^\s*from\s+conftest\s+import\b", re.MULTILINE)


def _cluster_dirs() -> list[Path]:
    """5 つのクラスタディレクトリ（存在するものだけ）。"""
    root = find_repo_root() / CLUSTERS_ROOT_REL
    return [
        root / assignment.directory_name
        for assignment in load_cluster_assignments(cluster_index_path())
        if (root / assignment.directory_name).is_dir()
    ]


def _cluster_sources() -> Iterator[Path]:
    """クラスタ配下と `_support/cluster_*.py` の全ソース（**本ファイル自身は除く**）。

    自分を含めると、禁止語を**説明している docstring・失敗メッセージ**が検出されてしまう。
    """
    here = pathlib_path_of_this_file()
    for directory in _cluster_dirs():
        yield from sorted(directory.glob("*.py"))
    yield from sorted((find_repo_root() / SUPPORT_ROOT_REL).glob("cluster_*.py"))
    coverage = find_repo_root() / CLUSTERS_ROOT_REL / "cluster_coverage"
    if coverage.is_dir():
        yield from (path for path in sorted(coverage.glob("*.py")) if path != here)


def pathlib_path_of_this_file() -> Path:
    """本ファイルの絶対パス（走査から自分を外すのに使う）。"""
    return Path(__file__).resolve()


def _code_of(path: Path) -> str:
    """**docstring を取り除いた**ソース（禁止語の字句検査に使う）。

    docstring には「なぜ禁止か」を書くので、素のテキストを走査すると説明そのものが違反として
    報告されてしまう（2026-09-01 に実測）。AST を組み直して docstring を落とす。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_cluster_sources_never_call_a_feature_build_directly() -> None:
    """FR-001: 手配線をしない —— `<mod>.build(` も `from <mod> import build` も現れない。

    実 composition root（`api.wiring.build_modules` / `api.app.create_app`）を必ず通す（規約 §3）。
    ここが破れると、統合テストは「実際の composition root の噛み合い」を検証しなくなる。

    **`<Mod>Env` の import は禁止しない** —— 止め木の能動検査（`assert_env_path_under`）に要る。
    """
    offenders: list[str] = []
    for path in _cluster_sources():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in FEATURE_MODULES:
                offenders.extend(
                    f"{path.name}: `from {node.module} import build`"
                    for alias in node.names
                    if alias.name == "build"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "build"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in FEATURE_MODULES
            ):
                offenders.append(f"{path.name}: `{node.func.value.id}.build(...)` の呼び出し")
    assert not offenders, "手配線をしている（規約 §3・FR-001）:\n" + "\n".join(
        f"  - {item}" for item in offenders
    )


def test_cluster_sources_never_import_from_conftest() -> None:
    """FR-041a: `from conftest import ...` を使わない（R-02）。

    `contract/` の 10 本はこれに依存しており、収集順が変わると
    `ImportError: cannot import name 'BOARD_ACTOR' from 'conftest'` になる。**再発条件を
    作らない**ことがクラスタ側の責務である。型も工場も fixture で配る。
    """
    offenders = [
        path.name for path in _cluster_sources() if _CONFTEST_IMPORT.search(_code_of(path))
    ]
    assert not offenders, (
        "`from conftest import ...` を使っている（収集順で壊れる＝ R-02）: " + str(offenders)
    )


def test_cluster_sources_never_mention_mock_transport() -> None:
    """FR-034: `httpx` の `MockTransport` を使わない（規約 §4）。

    monitor の `LLMHttpHook`（IF-40〜43）は httpx の**transport 層**の差し込み点であり、
    `MockTransport` はそこを置き換えてしまう。実 HTTP 往復（偽 LLM サーバ）でなければ観測系の
    検証が成立しない。
    """
    offenders = [path.name for path in _cluster_sources() if _MOCK_TRANSPORT.search(_code_of(path))]
    assert not offenders, (
        "`MockTransport` を持ち込んでいる（Hook は transport 層の差し込み点＝規約 §4）: "
        + str(offenders)
    )


def test_cluster_requires_bd_matches_the_assignment_csv() -> None:
    """FR-043: `requires_bd` の付与が「実際に通す EIF」に `EIF-01` を持つクラスタと一致する。

    期待値も**正本駆動**である（`x2_cluster_index.csv` の列から導く。テストへ書き写さない）。
    `cluster_coverage/` には付けない（静的走査のみ＝ SC-006）。

    付与単位は module 変数 `pytestmark = pytest.mark.requires_bd`（`contract/` の先例に揃える）。
    """
    root = find_repo_root() / CLUSTERS_ROOT_REL
    problems: list[str] = []
    for assignment in load_cluster_assignments(cluster_index_path()):
        directory = root / assignment.directory_name
        if not directory.is_dir():
            continue
        expected = BEADS_EIF in assignment.actual_eifs
        for path in sorted(directory.glob("test_*.py")):
            marked = "pytest.mark.requires_bd" in _code_of(path)
            if marked != expected:
                problems.append(
                    f"{assignment.directory_name}/{path.name}: `requires_bd` が"
                    f"{'付いていない' if expected else '余計に付いている'}"
                    f"（{BEADS_EIF} の有無={expected}）"
                )
    coverage = root / "cluster_coverage"
    if coverage.is_dir():
        problems.extend(
            f"cluster_coverage/{path.name}: 静的走査だけなので `requires_bd` を付けない"
            for path in sorted(coverage.glob("test_*.py"))
            if path != pathlib_path_of_this_file() and "pytest.mark.requires_bd" in _code_of(path)
        )
    assert not problems, "`requires_bd` の付与が正本と一致しない:\n" + "\n".join(
        f"  - {item}" for item in problems
    )


def test_cluster_fixtures_are_all_function_scoped() -> None:
    """FR-042: クラスタの fixture は**すべて関数スコープ**（テスト間に実行順序依存を作らない）。

    一時ディレクトリ・偽サーバのポート（OS 採番）・`bd init` をテストごとに独立させる。
    `scope="module"／"session"／"package"／"class"` が 1 つでもあると、実行順や部分集合で
    結果が変わりうる。
    """
    offenders: list[str] = []
    for directory in _cluster_dirs():
        conftest = directory / "conftest.py"
        if not conftest.is_file():
            continue
        source = conftest.read_text(encoding="utf-8")
        offenders.extend(
            f"{directory.name}/conftest.py: `scope={scope!r}`"
            for scope in FUNCTION_SCOPES
            if f'scope="{scope}"' in source or f"scope='{scope}'" in source
        )
    assert not offenders, "fixture が関数スコープでない（FR-042）:\n" + "\n".join(
        f"  - {item}" for item in offenders
    )


def test_cluster_file_basenames_are_unique_across_the_repository() -> None:
    """SC-010: 新設したファイルの基名がリポジトリ全体で一意である。

    `tests/` に `__init__.py` を置かないため、pytest の既定 import モードでは**基名がそのまま
    top-level モジュール名**になる。重複すると `uv run pytest`（全体）が collection error で
    止まる —— **モジュール単位の検証では再現しない**（CLAUDE.md）。
    """
    repo = find_repo_root()
    # `conftest.py` は pytest が特別扱いする（top-level モジュール名にならない）ので除く。
    ours = {path.name for path in _cluster_sources() if path.name != "conftest.py"} | {
        path.name for path in sorted((repo / SUPPORT_ROOT_REL).glob("cluster_*.py"))
    }
    duplicates: list[str] = []
    for name in sorted(ours):
        matches = [
            path
            for path in repo.rglob(name)
            if ".venv" not in path.parts and "__pycache__" not in path.parts
        ]
        if len(matches) > 1:
            duplicates.append(f"{name}: {[str(path.relative_to(repo)) for path in matches]}")
    assert not duplicates, (
        "基名がリポジトリ全体で重複している（全体実行が collection error で止まる）:\n"
        + "\n".join(f"  - {item}" for item in duplicates)
    )


def test_cluster_enabled_modules_match_the_assignment_csv() -> None:
    """配線宣言（`CLUSTER_MODULES`）が割当 csv の「対象 MOD」と一致する。

    有効 MOD 一覧を正本から外れて書き換えると、クラスタの範囲そのものが設計とずれる。
    期待値は csv から導く（テストへ書き写さない）。
    """
    mismatches: list[str] = []
    for assignment in load_cluster_assignments(cluster_index_path()):
        declared = set(CLUSTER_MODULES.get(assignment.cluster_id, ()))
        expected = set(assignment.modules)
        if declared != expected:
            mismatches.append(
                f"{assignment.directory_name}: 宣言={sorted(declared)} / 正本={sorted(expected)}"
            )
    assert not mismatches, "有効 MOD 一覧が割当 csv と食い違う:\n" + "\n".join(
        f"  - {item}" for item in mismatches
    )
