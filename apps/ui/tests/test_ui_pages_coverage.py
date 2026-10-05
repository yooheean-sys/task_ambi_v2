"""UI 配線契約テストの被覆が**正本駆動**で守られていること（US1・FR-006・FR-007）。

`contracts/coverage-declaration.md` が正本。開発者が `pages.py` に 4 件目の UI 登録を足したとき、
対応するテストを書き忘れると**その同定子を挙げて**赤くなる。

**同定子は `(module, title)` の対**である。REG-8h は `module` の**集合**しか見ないため、settings の
エントリが 1 件に減っても・片方の `title` が変わっても通る（GAP-5）。対にすると両方が被覆に映る。

**期待する件数（3）も `"AI 設定"` 等のリテラルも判定側に書かない。** `UI_PAGE_REGISTRATIONS` を
import して集合を作る。例外は C-4 の自己実証だけで、合成データは**関数の中**に置く。

⚠ 走査ヘルパは `apps/api/tests/wiring_contract/test_wiring_contract_coverage.py`
と**重複して持つ**。
FR-004（apps/api 側と apps/ui 側を 1 ファイルに混ぜない）と FR-036（`_support/` に新規モジュールを
置かない）を同時に満たすための**意図した重複**である（plan Complexity Tracking）。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ui.pages import UI_PAGE_REGISTRATIONS

if TYPE_CHECKING:
    from collections.abc import Iterable

DECLARATION_NAME = "UI_PAGE_COVERS"
"""UI テストファイルが module 大域で名乗る定数名（規約 §2）。"""

UI_TESTS_DIR = Path(__file__).parent
"""走査対象のディレクトリ（**直下のみ**。再帰しない）。"""

EXPECTED_BASENAME_TEMPLATE = "test_ui_pages_<観点>.py"
"""不足を報告するときに示す基名の案（FR-032）。"""


@dataclass(frozen=True, slots=True)
class UiCoverageDeclaration:
    """ある `test_*.py` の module 大域 `UI_PAGE_COVERS` の中身（data-model §3-3）。"""

    file: str
    keys: tuple[tuple[str, str], ...]
    malformed: bool
    raw: str


@dataclass(frozen=True, slots=True)
class UiCoverageDiff:
    """実 UI 登録と宣言の差分（data-model §3-3）。"""

    missing: frozenset[tuple[str, str]]
    unknown: frozenset[tuple[str, str]]
    registered_total: int
    declared_total: int
    malformed: tuple[UiCoverageDeclaration, ...]


def _read_pair_tuple(value: ast.expr) -> tuple[tuple[tuple[str, str], ...], bool]:
    """`UI_PAGE_COVERS` の右辺を読む（リテラルのタプル・要素は文字列 2 つの対・非空）。

    Args:
        value: 代入の右辺の AST ノード。

    Returns:
        `(読めた対, 形式違反か)`。形式違反なら対は空。
    """
    if not isinstance(value, ast.Tuple) or not value.elts:
        return (), True
    keys: list[tuple[str, str]] = []
    for element in value.elts:
        if not isinstance(element, ast.Tuple) or len(element.elts) != 2:
            return (), True
        parts: list[str] = []
        for item in element.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return (), True
            parts.append(item.value)
        keys.append((parts[0], parts[1]))
    return tuple(keys), False


def _declaration_in(tree: ast.Module, file_name: str) -> UiCoverageDeclaration | None:
    """module 大域の `UI_PAGE_COVERS` を 1 件だけ読む（**関数の中の同名変数は読まない**）。

    Args:
        tree: 解析済みの module。
        file_name: 報告に載せるファイル名。

    Returns:
        宣言。定数を持たないファイルは `None`（宣言 0 件として扱う）。
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == DECLARATION_NAME
            for target in node.targets
        ):
            continue
        keys, malformed = _read_pair_tuple(node.value)
        return UiCoverageDeclaration(
            file=file_name, keys=keys, malformed=malformed, raw=ast.unparse(node.value)
        )
    return None


def collect_declarations(directory: Path) -> tuple[UiCoverageDeclaration, ...]:
    """`directory` **直下**の `test_*.py`
    から宣言を集める（`contracts/coverage-declaration.md` §3）。

    **実行中の pytest セッションを一切見ない**（FR-033）。`conftest.py` と `test_` で始まらない
    補助モジュールは対象外。既存の 3 ファイル（`test_ui_entry.py`・`test_ui_boundaries.py`・
    `test_ui_config.py`）は定数を持たないので**宣言 0 件**として扱われる。

    Args:
        directory: 走査するディレクトリ。

    Returns:
        読めた宣言。
    """
    declarations: list[UiCoverageDeclaration] = []
    for path in sorted(directory.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declaration = _declaration_in(tree, path.name)
        if declaration is not None:
            declarations.append(declaration)
    return tuple(declarations)


def compare_coverage(
    registered: Iterable[tuple[str, str]], declared: Iterable[UiCoverageDeclaration]
) -> UiCoverageDiff:
    """実 UI 登録の同定子と宣言の和を突き合わせる**純関数**（C-4 が自己実証する）。

    Args:
        registered: 実 UI 登録の同定子。
        declared: 集めた宣言。

    Returns:
        差分。
    """
    registered_set = frozenset(registered)
    declarations = tuple(declared)
    declared_keys = frozenset(key for declaration in declarations for key in declaration.keys)
    return UiCoverageDiff(
        missing=registered_set - declared_keys,
        unknown=declared_keys - registered_set,
        registered_total=len(registered_set),
        declared_total=len(declared_keys),
        malformed=tuple(d for d in declarations if d.malformed),
    )


def _registered_keys() -> frozenset[tuple[str, str]]:
    """実 `UI_PAGE_REGISTRATIONS` の同定子集合（**件数もリテラルも書かない**）。

    Returns:
        `(module, title)` の集合。
    """
    return frozenset(
        (registration.module, registration.title) for registration in UI_PAGE_REGISTRATIONS
    )


def test_ui_c1_every_ui_registration_has_a_wiring_contract_test() -> None:
    """C-1: 実 `UI_PAGE_REGISTRATIONS` の各 `(module, title)` に対応するテストが在る（FR-006）。

    見る欄: `UI_PAGE_REGISTRATIONS[*].module`・`title`。期待値は実登録から導く。
    """
    diff = compare_coverage(_registered_keys(), collect_declarations(UI_TESTS_DIR))

    assert not diff.missing, (
        "UI 配線契約テストが無い登録がある（UI 登録を足してテストを書き忘れた）: "
        f"{sorted(diff.missing)}\n"
        f"  期待される基名: {EXPECTED_BASENAME_TEMPLATE}\n"
        f'  その module 大域に {DECLARATION_NAME} = (("<module>", "<title>"),) を置くこと\n'
        f"  実登録 {diff.registered_total} 件 / 宣言 {diff.declared_total} 件"
    )


def test_ui_c2_every_declaration_names_a_real_ui_registration() -> None:
    """C-2: 宣言の和が実 UI 登録の同定子に収まる（余分な宣言が無い。FR-006）。

    見る欄: `UI_PAGE_REGISTRATIONS[*].module`・`title`。⚠ `pages.py` からエントリを 1 件削ると、
    そのエントリを宣言していたファイルの宣言が**余分**になり本テストが赤くなる（SC-015 の変異 3）。
    """
    declarations = collect_declarations(UI_TESTS_DIR)
    diff = compare_coverage(_registered_keys(), declarations)
    owners = {
        key: declaration.file
        for declaration in declarations
        for key in declaration.keys
        if key in diff.unknown
    }

    assert not diff.unknown, (
        f"実 UI_PAGE_REGISTRATIONS に無い組を宣言しているファイルがある: {owners}\n"
        f"  実登録: {sorted(_registered_keys())}"
    )


def test_ui_c3_declarations_are_well_formed() -> None:
    """C-3: `UI_PAGE_COVERS` の形式が壊れていない（リテラルのタプル・文字列 2 つの対・非空）。

    見る欄: `UI_PAGE_COVERS` の形式そのもの。
    """
    diff = compare_coverage(_registered_keys(), collect_declarations(UI_TESTS_DIR))
    broken = {declaration.file: declaration.raw for declaration in diff.malformed}

    assert not broken, (
        f"{DECLARATION_NAME} の形式が壊れているファイルがある: {broken}\n"
        "  形式: (module, title) の対のリテラルのタプル\n"
        '  例: UI_PAGE_COVERS = (("trigger", "トリガー"),)'
    )


def test_ui_c4_compare_coverage_names_the_missing_ui_registration() -> None:
    """C-4（自己実証）: 判定器が合成の 4 件目を `missing` に挙げる（US1 AC-3・FR-003 但し書き）。

    **合成データは関数の中に置く**ので module 大域の走査に拾われない。`pages.py` は変更しない。
    """
    synthetic_registered = {("alpha", "アルファ"), ("zzz_fake", "第 4 の頁")}
    synthetic_declared = (
        UiCoverageDeclaration(
            file="test_ui_pages_alpha.py",
            keys=(("alpha", "アルファ"),),
            malformed=False,
            raw='(("alpha", "アルファ"),)',
        ),
        UiCoverageDeclaration(
            file="test_ui_pages_ghost.py",
            keys=(("ghost", "亡霊"),),
            malformed=False,
            raw='(("ghost", "亡霊"),)',
        ),
    )

    diff = compare_coverage(synthetic_registered, synthetic_declared)

    assert diff.missing == frozenset({("zzz_fake", "第 4 の頁")})
    assert diff.unknown == frozenset({("ghost", "亡霊")})
    assert diff.registered_total == 2
    assert diff.declared_total == 2


def test_ui_c3_detects_a_malformed_declaration(tmp_path: Path) -> None:
    """C-3（自己実証）: 対でない要素を判定器が形式違反として挙げる。"""
    (tmp_path / "test_ui_pages_flat.py").write_text(
        'UI_PAGE_COVERS = ("settings", "AI 設定")\n', encoding="utf-8"
    )

    declarations = collect_declarations(tmp_path)

    assert len(declarations) == 1
    assert declarations[0].malformed


def test_ui_c1_scan_ignores_names_declared_inside_functions(tmp_path: Path) -> None:
    """C-1（走査の規律）: 関数の中の同名変数は宣言として読まない（規約 §2-1）。"""
    (tmp_path / "test_ui_pages_local.py").write_text(
        "def test_x() -> None:\n"
        '    UI_PAGE_COVERS = (("ghost", "亡霊"),)\n'
        "    assert UI_PAGE_COVERS\n",
        encoding="utf-8",
    )

    assert collect_declarations(tmp_path) == ()


def test_ui_c2_existing_ui_tests_declare_nothing() -> None:
    """C-2（走査の規律）: 定数を持たないファイルは宣言 0 件として扱う（除外印を置かない）。

    既存 3 ファイルは `UI_PAGE_COVERS` を持たないので、走査に寄与しない（`013` の
    `CONTRACT_META = True` に相当する仕掛けを置かない＝ 規約 §2-2）。
    """
    scanned = {declaration.file for declaration in collect_declarations(UI_TESTS_DIR)}

    assert "test_ui_entry.py" not in scanned
    assert "test_ui_boundaries.py" not in scanned
    assert "test_ui_config.py" not in scanned
    assert "test_ui_pages_coverage.py" not in scanned
