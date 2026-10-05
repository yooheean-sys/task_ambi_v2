"""横断: 配線契約テストの被覆が**正本駆動**で守られていること（US1・FR-005〜FR-007）。

`contracts/coverage-declaration.md` が正本。開発者が `registry.py` に 9 件目の登録を足したとき、
対応する配線契約テストを書き忘れると**その名前を挙げて**赤くなる。被覆表を人が数え直さない。

**期待する件数（8）・モジュール名（`"board"` 等）を検査側に書かない。** すべて
`MODULE_REGISTRATIONS` を import して集合を作る。例外は C-4 の自己実証だけで、合成データは
**関数の中**に置く（module 大域の走査に拾われないため＝ research R-06）。

⚠ 本ファイルは**組を宣言しない**（横断ファイル）。`WIRING_COVERS` を持たないので、走査から見ると
宣言 0 件のファイルである。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from api.registry import MODULE_REGISTRATIONS

if TYPE_CHECKING:
    from collections.abc import Iterable

DECLARATION_NAME = "WIRING_COVERS"
"""MOD ファイルが module 大域で名乗る定数名（規約 §2）。"""

CROSS_CUTTING_MARKER = "横断"
"""組を宣言しないファイルの docstring が含むべき語（C-5）。"""

WIRING_CONTRACT_DIR = Path(__file__).parent
"""走査対象のディレクトリ（**直下のみ**。再帰しない）。"""

EXPECTED_BASENAME_TEMPLATE = "test_wiring_contract_<mod>.py"
"""不足を報告するときに示す基名の案（FR-032）。"""


@dataclass(frozen=True, slots=True)
class CoverageDeclaration:
    """ある `test_*.py` の module 大域 `WIRING_COVERS` の中身（data-model §2-5）。"""

    file: str
    names: tuple[str, ...]
    malformed: bool
    raw: str


@dataclass(frozen=True, slots=True)
class CoverageDiff:
    """実登録と宣言の差分（data-model §2-5）。"""

    missing: frozenset[str]
    unknown: frozenset[str]
    registered_total: int
    declared_total: int
    malformed: tuple[CoverageDeclaration, ...]


def _read_str_tuple(value: ast.expr) -> tuple[tuple[str, ...], bool]:
    """`WIRING_COVERS` の右辺を読む（リテラルのタプル・要素は文字列・空でないこと）。

    Args:
        value: 代入の右辺の AST ノード。

    Returns:
        `(読めた名前, 形式違反か)`。形式違反なら名前は空。
    """
    if not isinstance(value, ast.Tuple) or not value.elts:
        return (), True
    names: list[str] = []
    for element in value.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return (), True
        names.append(element.value)
    return tuple(names), False


def _declaration_in(tree: ast.Module, file_name: str) -> CoverageDeclaration | None:
    """module 大域の `WIRING_COVERS` を 1 件だけ読む（**関数の中の同名変数は読まない**）。

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
        names, malformed = _read_str_tuple(node.value)
        return CoverageDeclaration(
            file=file_name, names=names, malformed=malformed, raw=ast.unparse(node.value)
        )
    return None


def collect_declarations(directory: Path) -> tuple[CoverageDeclaration, ...]:
    """`directory` **直下**の `test_*.py`
    から宣言を集める（`contracts/coverage-declaration.md` §3）。

    **実行中の pytest セッションを一切見ない**——`-k`／`-m` で部分集合を実行しても結果が変わらない
    （`-m "not requires_bd"` で board が deselect されても `WIRING_COVERS = ("board",)` は読まれる
    ＝ FR-033・research R-13）。`conftest.py` と `test_` で始まらない補助モジュールは対象外。

    Args:
        directory: 走査するディレクトリ。

    Returns:
        読めた宣言（宣言を持たないファイルはエントリを作らない）。
    """
    declarations: list[CoverageDeclaration] = []
    for path in sorted(directory.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declaration = _declaration_in(tree, path.name)
        if declaration is not None:
            declarations.append(declaration)
    return tuple(declarations)


def compare_coverage(
    registered: Iterable[str], declared: Iterable[CoverageDeclaration]
) -> CoverageDiff:
    """実登録名と宣言の和を突き合わせる**純関数**（C-4 が合成データを与えて自己実証する）。

    **重複宣言は失敗にしない**（同じ組を 2 ファイルが宣言してもよい＝ 規約 §2-3）。

    Args:
        registered: 実登録の同定子。
        declared: 集めた宣言。

    Returns:
        差分。
    """
    registered_set = frozenset(registered)
    declarations = tuple(declared)
    declared_names = frozenset(name for declaration in declarations for name in declaration.names)
    return CoverageDiff(
        missing=registered_set - declared_names,
        unknown=declared_names - registered_set,
        registered_total=len(registered_set),
        declared_total=len(declared_names),
        malformed=tuple(d for d in declarations if d.malformed),
    )


def _registered_names() -> frozenset[str]:
    """実 `MODULE_REGISTRATIONS` の名前集合（**件数もモジュール名も書かない**）。

    Returns:
        実登録の同定子。
    """
    return frozenset(registration.name for registration in MODULE_REGISTRATIONS)


def test_c1_every_registration_has_a_wiring_contract_test() -> None:
    """横断 C-1: 実 `MODULE_REGISTRATIONS` の各 `name` に対応するテストが在る（FR-005・US1 AC-1）。

    見る欄: `MODULE_REGISTRATIONS[*].name`。期待値は実登録から導く（ハードコード 0 件）。
    """
    diff = compare_coverage(_registered_names(), collect_declarations(WIRING_CONTRACT_DIR))

    assert not diff.missing, (
        "配線契約テストが無い登録がある（登録を足してテストを書き忘れた）: "
        f"{sorted(diff.missing)}\n"
        f"  期待される基名: {EXPECTED_BASENAME_TEMPLATE}\n"
        f'  その module 大域に {DECLARATION_NAME} = ("<mod>",) を置くこと\n'
        f"  実登録 {diff.registered_total} 件 / 宣言 {diff.declared_total} 件"
    )


def test_c2_every_declaration_names_a_real_registration() -> None:
    """横断 C-2: 宣言の和が実登録名に収まる（余分な宣言が無い。FR-005）。

    見る欄: `MODULE_REGISTRATIONS[*].name`。
    """
    declarations = collect_declarations(WIRING_CONTRACT_DIR)
    diff = compare_coverage(_registered_names(), declarations)
    owners = {
        name: declaration.file
        for declaration in declarations
        for name in declaration.names
        if name in diff.unknown
    }

    assert not diff.unknown, (
        f"実 MODULE_REGISTRATIONS に無い組を宣言しているファイルがある: {owners}\n"
        f"  実登録: {sorted(_registered_names())}"
    )


def test_c3_declarations_are_well_formed() -> None:
    """横断 C-3: `WIRING_COVERS` の形式が壊れていない（リテラルのタプル・文字列・非空）。

    見る欄: `WIRING_COVERS` の形式そのもの。
    """
    diff = compare_coverage(_registered_names(), collect_declarations(WIRING_CONTRACT_DIR))
    broken = {declaration.file: declaration.raw for declaration in diff.malformed}

    assert not broken, (
        f"{DECLARATION_NAME} の形式が壊れているファイルがある: {broken}\n"
        '  形式: 文字列のリテラルのタプル（例: WIRING_COVERS = ("stages",)）。動的に組み立てない'
    )


def test_c4_compare_coverage_names_the_missing_registration() -> None:
    """横断 C-4（自己実証）: 判定器が合成の 9 件目を `missing` に挙げる（US1 AC-3・FR-003
    但し書き）。

    見る欄: —（判定器そのものの検査）。**合成データは関数の中に置く**ので module 大域の走査に
    拾われない（research R-06）。`registry.py` は変更しない。
    """
    synthetic_registered = {"alpha", "zzz_fake"}
    synthetic_declared = (
        CoverageDeclaration(
            file="test_wiring_contract_alpha.py",
            names=("alpha",),
            malformed=False,
            raw='("alpha",)',
        ),
        CoverageDeclaration(
            file="test_wiring_contract_ghost.py",
            names=("ghost",),
            malformed=False,
            raw='("ghost",)',
        ),
    )

    diff = compare_coverage(synthetic_registered, synthetic_declared)

    assert diff.missing == frozenset({"zzz_fake"})
    assert diff.unknown == frozenset({"ghost"})
    assert diff.registered_total == 2
    assert diff.declared_total == 2


def _docstring_violations(directory: Path) -> tuple[str, ...]:
    """各 `test_*` 関数の docstring 規約（規約 §7）を検査する。

    Args:
        directory: 走査するディレクトリ。

    Returns:
        違反の説明（空なら適合）。
    """
    violations: list[str] = []
    for path in sorted(directory.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declaration = _declaration_in(tree, path.name)
        required = declaration.names if declaration is not None else (CROSS_CUTTING_MARKER,)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            doc = ast.get_docstring(node)
            if doc is None or not doc.strip():
                violations.append(f"{path.name}::{node.name}: docstring が空")
            elif not any(token in doc for token in required):
                violations.append(
                    f"{path.name}::{node.name}: "
                    f"検証する組 {list(required)} を docstring に書いていない"
                )
    return tuple(violations)


def test_c5_every_test_docstring_names_its_case() -> None:
    """横断 C-5: 各 `test_*` の docstring が非空で、検証する組（横断なら「横断」）
    を含む（規約 §7）。

    見る欄: —（docstring 規約の機械検査）。憲章 §7-6 のテストファーストは本 feature では
    「宣言 → テスト」と読み替えるため、**何の宣言を見ているか**を各テストが名乗る。
    """
    violations = _docstring_violations(WIRING_CONTRACT_DIR)

    assert not violations, "docstring 規約（規約 §7）に違反しているテストがある:\n  " + "\n  ".join(
        violations
    )


def test_c5_detects_a_missing_docstring(tmp_path: Path) -> None:
    """横断 C-5（自己実証）: docstring の無いテストを判定器が挙げる。

    見る欄: —（判定器そのものの検査）。合成のテストファイルを一時ディレクトリに書いて与える。
    """
    (tmp_path / "test_wiring_contract_ghost.py").write_text(
        'WIRING_COVERS = ("ghost",)\n\n\ndef test_no_doc() -> None:\n    pass\n',
        encoding="utf-8",
    )

    violations = _docstring_violations(tmp_path)

    assert len(violations) == 1
    assert "test_no_doc" in violations[0]


def test_c3_detects_a_malformed_declaration(tmp_path: Path) -> None:
    """横断 C-3（自己実証）: リテラルでない `WIRING_COVERS` を判定器が形式違反として挙げる。

    見る欄: —（判定器そのものの検査）。
    """
    (tmp_path / "test_wiring_contract_dynamic.py").write_text(
        "WIRING_COVERS = tuple(['stages'])\n", encoding="utf-8"
    )

    declarations = collect_declarations(tmp_path)

    assert len(declarations) == 1
    assert declarations[0].malformed


def test_c1_scan_ignores_names_declared_inside_functions(tmp_path: Path) -> None:
    """横断 C-1（走査の規律）: 関数の中の同名変数は宣言として読まない（規約 §2-1）。

    見る欄: —（走査そのものの検査）。この規律が無いと、本ファイルの C-4 が使う合成データが
    宣言として拾われてしまう。
    """
    (tmp_path / "test_wiring_contract_local.py").write_text(
        "def test_x() -> None:\n"
        '    """doc."""\n'
        '    WIRING_COVERS = ("ghost",)\n'
        "    assert WIRING_COVERS\n",
        encoding="utf-8",
    )

    assert collect_declarations(tmp_path) == ()


def test_c1_scan_does_not_recurse_into_subdirectories(tmp_path: Path) -> None:
    """横断 C-1（走査の規律）: 直下の `test_*.py` だけを読み、再帰しない（規約 §3）。

    見る欄: —（走査そのものの検査）。
    """
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "test_wiring_contract_deep.py").write_text(
        'WIRING_COVERS = ("deep",)\n', encoding="utf-8"
    )

    assert collect_declarations(tmp_path) == ()


def test_c3_conftest_and_helpers_are_not_scanned() -> None:
    """横断 C-3（走査の規律）: `conftest.py` と `test_` で始まらない補助は対象外（規約 §3）。

    見る欄: —（走査そのものの検査）。`wiring_stores.py` が宣言として拾われないこと。
    """
    scanned = {declaration.file for declaration in collect_declarations(WIRING_CONTRACT_DIR)}

    assert "conftest.py" not in scanned
    assert "wiring_stores.py" not in scanned
    assert all(name.startswith("test_") for name in scanned)


def _declared_names(directory: Path) -> tuple[tuple[str, ...], ...]:
    """`directory` から読めた宣言の**中身だけ**を並べる（ファイル名を落として比較可能にする）。

    Args:
        directory: 走査するディレクトリ。

    Returns:
        宣言した名前の並び（順序に依存しないよう整列済み）。
    """
    return tuple(sorted(declaration.names for declaration in collect_declarations(directory)))


def test_c5_scan_is_determined_only_by_the_directory_contents(tmp_path: Path) -> None:
    """横断 C-5（走査の規律）: 判定は走査対象の**中身だけ**で決まり、実行中の pytest
    セッションを見ない（FR-033・research R-13）。

    見る欄: —（走査そのものの検査）。実ディレクトリの `test_*.py` を**別の基名**で一時ディレクトリ
    へ写す。写しは pytest が 1 度も収集も import もしていないファイル群であり、リポジトリの外にある
    が、宣言の中身は実ディレクトリと**同一**に読める——走査がセッション（収集済み item・`-k`／`-m`
    の選択結果・`sys.modules`）を参照していたら、写しからは何も読めないか実物と食い違うはずである。
    あわせて、合成ファイルを 1 件増やすと結果もちょうど 1 件増え、消すと戻ることを示す
    （**結果を左右するのは走査対象の中身だけ**）。

    ⚠ 以前ここに在った `assert not any(pytest.__name__ in d.raw for d in first)` は**構造的に
    落ちない**（`WIRING_COVERS` の右辺に文字列 `"pytest"` が現れないことしか見ておらず、走査が
    セッションを見るようになっても赤くならない）ため差し替えた。`-m "not requires_bd"` を実際に
    流して board が deselect されても被覆が緑のままであることの**実測**は quickstart §3 が持つ。
    """
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    for path in sorted(WIRING_CONTRACT_DIR.glob("test_*.py")):
        (mirror / f"test_mirror_{path.name}").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    real = _declared_names(WIRING_CONTRACT_DIR)

    assert real, (
        "実ディレクトリから宣言を 1 件も読めなかった——写しとの比較が**空同士**になり偽の緑になる"
    )
    assert _declared_names(mirror) == real, (
        "pytest が収集していない写しから読めた宣言が実ディレクトリと食い違う"
        "（走査が実行中のセッションを見ている疑い）\n"
        f"  実: {real}\n"
        f"  写し: {_declared_names(mirror)}"
    )

    ghost = mirror / "test_mirror_ghost.py"
    ghost.write_text('WIRING_COVERS = ("ghost",)\n', encoding="utf-8")

    assert _declared_names(mirror) == tuple(sorted([*real, ("ghost",)]))

    ghost.unlink()

    assert _declared_names(mirror) == real
