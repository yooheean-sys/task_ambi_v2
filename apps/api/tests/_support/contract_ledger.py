"""契約台帳 csv の最小 loader ＋ `contract/` の IF 宣言の AST 走査（FR-003・FR-004・FR-005）。

**`packages/common/tests/_support/design_docs.py` の最小の写しである。** あちらは別 workspace
パッケージ（`common`）のテスト補助であり `pythonpath = ["apps/api/tests"]` の外にあるため
import できない。正規化規則（`count_args`・`return_type`・mmd パーサ）は**移植しない**
（層 1 の仕事であり、層 4 はそこをやり直さない＝ FR-020）。

**期待値を 1 つも持たない**（FR-004）。行数・メソッド数・重複数・提供 MOD 別件数はいずれも
返さない —— 数えるのはテスト側である。台帳が改訂されたら、テストコードの数値を書き換えずに
結果が追随する。
"""

from __future__ import annotations

import ast
import csv
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

CONTRACT_CSV_REL = "docs/design/phase4/p4_module_contract.csv"
"""契約台帳の位置（リポジトリルート相対。正本は 1 つだけ）。"""

META_FLAG = "CONTRACT_META"
"""module 大域にこの名前で `True` を置いたファイルは AST 走査の対象外になる（規約 §4-2）。"""

MARKER_ATTR = "contract_if"
EXCEPTION_SEPARATOR = "／"
"""例外欄の区切り（全角スラッシュ）。"""

EM_DASH = "—"
"""例外欄が「無し」を表す記号（空タプルにする）。"""

ASYNC_PREFIX = "非同期"
"""`同期/非同期` 欄がこれで始まれば非同期（`同期（ブロッキング…）` は同期）。"""

_NOTE_PATTERN = re.compile(r"（[^（）]*）")
"""例外欄の**全角括弧の注記**。注記の中に区切り文字が入るので、分割の**前**に落とす。"""


@dataclass(frozen=True, slots=True)
class ContractRow:
    """契約台帳の 1 行（IF-nn）。被覆の単位である。"""

    if_id: str
    provider: str
    consumer: str
    protocol: str
    method: str
    exceptions: tuple[str, ...]
    is_async: bool


@dataclass(frozen=True, slots=True)
class IfDeclaration:
    """`contract/` のテスト 1 本が宣言した IF。"""

    if_id: str | None
    """`contract_if` の引数（受理できる書式で 1 つだけ付いていなければ `None`）。"""

    test_name: str
    file: str
    """`contract_dir` 相対のファイル名。"""

    count: int
    """そのテストに付いた `contract_if` の個数（0 も記録する）。"""


@dataclass(frozen=True, slots=True)
class CoverageDiff:
    """台帳と宣言の突合表（FR-003 の報告 4 点）。"""

    missing: frozenset[str]
    """台帳にあって宣言が無い IF。"""

    unknown: frozenset[str]
    """宣言にあって台帳に無い IF。"""

    ledger_total: int
    """台帳の行数（重複込み）。"""

    declared_total: int
    """宣言された相異なる IF の数。"""

    malformed: tuple[IfDeclaration, ...]
    """`contract_if` の付け方が受理できない宣言（0 個・2 個以上・非リテラル引数）。"""


@dataclass(frozen=True, slots=True)
class UnresolvedDeclaration:
    """宣言した IF の**メソッド名がテスト本体に現れなかった**宣言（FR-003 の 5 点目）。

    被覆検査（`compare`）は IF-id の集合しか突き合わせないので、`contract_if("IF-13")` を
    付けて別のメソッドだけを呼ぶテストでも被覆に数えられる。この型はその形を報告する。
    """

    file: str
    test_name: str
    if_id: str
    method: str
    """台帳がその IF に割り当てているメソッド名（期待値は台帳から取る＝ FR-004）。"""


def contract_csv_path() -> Path:
    """契約台帳の絶対パス（cwd 非依存）。"""
    return find_repo_root() / CONTRACT_CSV_REL


def _split_exceptions(raw: str) -> tuple[str, ...]:
    """例外欄を名前のタプルにする。

    全角括弧の注記を**先に**落とす —— 注記の中に区切り文字（`／`）が入る行がある
    （例: IF-33 は `VaultNotFound（役割が宣言に無い／領域フォルダが実在しない…）` の後ろに
    `／VaultPathViolation` が続く）。先に分割すると注記が名前として混ざる。
    """
    stripped = _NOTE_PATTERN.sub("", raw).strip()
    if not stripped or stripped == EM_DASH:
        return ()
    return tuple(
        name for name in (part.strip() for part in stripped.split(EXCEPTION_SEPARATOR)) if name
    )


def load_contract_rows(csv_path: Path) -> tuple[ContractRow, ...]:
    """台帳 csv の全行を読む（列名は日本語のまま）。

    Args:
        csv_path: 台帳の絶対パス（テストは `tmp_path` に作った**写し**も渡す）。

    Returns:
        ファイルに現れた順の全行。**件数も重複数も返さない**（数えるのはテスト側＝ FR-004）。
    """
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = [
            ContractRow(
                if_id=(record.get("if_id") or "").strip(),
                provider=(record.get("提供モジュール") or "").strip(),
                consumer=(record.get("利用モジュール") or "").strip(),
                protocol=(record.get("Protocol名") or "").strip(),
                method=(record.get("メソッド名") or "").strip(),
                exceptions=_split_exceptions(record.get("例外") or ""),
                is_async=(record.get("同期/非同期") or "").strip().startswith(ASYNC_PREFIX),
            )
            for record in csv.DictReader(handle)
        ]
    return tuple(rows)


def _is_meta_module(tree: ast.Module) -> bool:
    """module 大域に `CONTRACT_META = True` があるか（**ファイル名では除外しない**）。"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == META_FLAG:
                value = node.value
                if isinstance(value, ast.Constant) and value.value is True:
                    return True
    return False


def _is_contract_if(node: ast.expr) -> bool:
    """デコレータが `pytest.mark.contract_if(...)` の呼び出しか。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != MARKER_ATTR:
        return False
    mark = func.value
    if not isinstance(mark, ast.Attribute) or mark.attr != "mark":
        return False
    root = mark.value
    return isinstance(root, ast.Name) and root.id == "pytest"


def _literal_arg(node: ast.expr) -> str | None:
    """`contract_if` の引数が**文字列リテラル 1 個**ならその値（それ以外は `None`）。

    変数・f-string・連結・引数 0 個／2 個以上・キーワード引数はいずれも受理しない
    （AST で静的に読むための規約＝ conventions §4）。
    """
    if not isinstance(node, ast.Call) or node.keywords or len(node.args) != 1:
        return None
    only = node.args[0]
    if not isinstance(only, ast.Constant):
        return None
    value: object = only.value
    return value if isinstance(value, str) else None


def _declared_if(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str | None, int]:
    """テスト関数のデコレータから `(if_id, contract_if の個数)` を読む。"""
    markers = [item for item in node.decorator_list if _is_contract_if(item)]
    literals = [value for value in (_literal_arg(item) for item in markers) if value]
    if_id = literals[0] if len(markers) == 1 and len(literals) == 1 else None
    return if_id, len(markers)


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """module 直下の `def test_*`（`collect_declarations` と同じ範囲）。"""
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def _declarations_of(tree: ast.Module, file_name: str) -> list[IfDeclaration]:
    """module 直下の `def test_*` ごとに宣言を 1 件作る。"""
    found: list[IfDeclaration] = []
    for node in _test_functions(tree):
        if_id, count = _declared_if(node)
        found.append(IfDeclaration(if_id=if_id, test_name=node.name, file=file_name, count=count))
    return found


def collect_declarations(contract_dir: Path) -> tuple[IfDeclaration, ...]:
    """`contract_dir` **直下**の `test_*.py` を AST で走査して IF 宣言を集める（FR-003・R-02）。

    **実行中の pytest セッションの情報を一切使わない。** `-k` や `-m` で部分集合を実行しても
    結果が変わらないことが要件である（部分集合実行で結果不変＝ CHK015）。

    除外は 3 つ —— (a) `conftest.py` と補助モジュール（`test_` で始まらないファイル）
    (b) module 大域に `CONTRACT_META = True` を持つファイル（**ファイル名で除外しない**）
    (c) 直下でないファイル（再帰しない）。

    Args:
        contract_dir: 走査するディレクトリ（`apps/api/tests/contract`）。

    Returns:
        ファイル名・関数名の順に並べた宣言。
    """
    found: list[IfDeclaration] = []
    for path in sorted(contract_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _is_meta_module(tree):
            continue
        found.extend(_declarations_of(tree, path.name))
    return tuple(found)


def compare(rows: Iterable[ContractRow], declarations: Iterable[IfDeclaration]) -> CoverageDiff:
    """台帳と宣言を突き合わせる（data-model §1-3）。

    Args:
        rows: 台帳の全行。
        declarations: `collect_declarations` の結果。

    Returns:
        FR-003 の報告 4 点（`missing`・`unknown`・件数の計上・`malformed`）。
    """
    all_rows = tuple(rows)
    all_declarations = tuple(declarations)
    ledger_ids = {row.if_id for row in all_rows}
    malformed = tuple(item for item in all_declarations if item.count != 1 or item.if_id is None)
    declared_ids = {
        item.if_id for item in all_declarations if item.if_id is not None and item.count == 1
    }
    return CoverageDiff(
        missing=frozenset(ledger_ids - declared_ids),
        unknown=frozenset(declared_ids - ledger_ids),
        ledger_total=len(all_rows),
        declared_total=len(declared_ids),
        malformed=malformed,
    )


def _referenced_names(node: ast.AST) -> set[str]:
    """部分木に現れる属性名（`ast.Attribute.attr`）と名前（`ast.Name.id`）を集める。

    `reader.show(...)` は属性、`_round_trip(...)` のようなヘルパ呼び出しは名前として拾う。
    呼び出しの形（`Call`）に限定しない —— スタブへ渡す・記録から引く形（例:
    `call_log.of("BoardReader", "list_working")`）は文字列なので拾えないが、そこは
    **本検査の対象外**である（必要条件であって十分条件ではない）。
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.Name):
            found.add(child.id)
    return found


def _module_helpers(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """module 直下の**テストでない**関数（ヘルパを 1 段だけ辿るための索引）。"""
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("test_")
    }


def unresolved_declarations(
    contract_dir: Path, rows: Iterable[ContractRow]
) -> tuple[UnresolvedDeclaration, ...]:
    """**宣言の真正性**を見る —— 宣言した IF のメソッド名がテスト本体に現れるか（FR-003）。

    `compare` は「宣言 IF の集合 ＝ 台帳 IF の集合」しか見ないので、`contract_if` を付けて
    まったく別のメソッドを呼ぶテストでも被覆に数えられる（`ContractRow.protocol`・`method` は
    そこで 1 度も使われない）。ここはその穴を塞ぐ **必要条件**の検査である。

    規則は 2 段 —— (a) テスト関数の部分木にメソッド名が現れれば解決
    (b) 現れなければ、そのテストが名前で参照している **module 直下のヘルパ関数を 1 段だけ**
    辿って同じことを見る（`test_contract_monitor_hook.py` の `_round_trip` が
    `on_outbound`／`on_inbound` を包んでおり、IF-41 の 2 本はこれを通す）。

    ⚠ **必要条件であって十分条件ではない。** メソッド名が現れるだけでは、そのメソッドを主眼に
    検証しているとは言えない（歯止めの穴の一族＝ `design-sync.md` B-12）。

    除外は `collect_declarations` と同じ（直下のみ・`CONTRACT_META = True` のファイルは対象外）。
    受理できない宣言（`count != 1`）と台帳に無い IF は `compare` の `malformed`・`unknown` が
    報告するので、ここでは飛ばす。

    Args:
        contract_dir: 走査するディレクトリ（`apps/api/tests/contract`）。
        rows: 台帳の全行（メソッド名の期待値はここから取る＝ FR-004）。

    Returns:
        解決できなかった宣言（ファイル名・関数名の順）。
    """
    method_of = {row.if_id: row.method for row in rows}
    found: list[UnresolvedDeclaration] = []
    for path in sorted(contract_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _is_meta_module(tree):
            continue
        helpers = _module_helpers(tree)
        for node in _test_functions(tree):
            if_id, count = _declared_if(node)
            if if_id is None or count != 1 or if_id not in method_of:
                continue
            method = method_of[if_id]
            names = _referenced_names(node)
            if method in names:
                continue
            if any(method in _referenced_names(helpers[name]) for name in names & helpers.keys()):
                continue
            found.append(
                UnresolvedDeclaration(
                    file=path.name, test_name=node.name, if_id=if_id, method=method
                )
            )
    return tuple(found)
