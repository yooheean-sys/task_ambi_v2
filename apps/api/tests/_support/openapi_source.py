"""正本 `docs/design/phase6/openapi.yaml` の最小 loader ＋ `http_contract/` の宣言の AST 走査。

`_support/contract_ledger.py`（層 4 の契約台帳 loader）と**同型**である。あちらが csv を読んで
IF-nn の被覆を突き合わせるのに対し、こちらは OpenAPI の YAML を読んで **(操作, status) 対**の
被覆を突き合わせる。

**期待値を 1 つも持たない**（FR-003）。パス数・操作数・対の数・パス名・status の一覧はいずれも
返さない —— 数えるのはテスト側である。正本にパスや操作が 1 件足されたら、テストコードの数値を
書き換えずに結果が追随する。

置き場が `_support/` なのは、雛形データではなく**正本の読み手**だからである（FR-041 の
「スタブと型の面だけ」の例外読み＝ HD-29）。雛形データ・recipe・台帳は `http_contract/` に置く。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import yaml

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

OPENAPI_REL = "docs/design/phase6/openapi.yaml"
"""正本の位置（リポジトリルート相対。正本は 1 つだけ）。"""

MARKER_ATTR = "http_op"
"""宣言のマーカ名。デコレータでも `pytest.param(marks=...)` の中でも同じ `Call` ノードになる。"""

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})
"""`paths.<path>` 直下でメソッドとして扱うキー（`parameters` 等の共有キーを除くため）。"""

JSON_MEDIA_TYPE = "application/json"

REF_KEY = "$ref"

REF_PREFIX = "#/"

MAX_REF_DEPTH = 16
"""`$ref` の多段解決の上限（循環したときに無限ループにしない）。"""

UNREADABLE_ARGS = "http_op の引数が文字列リテラル 3 つでない（変数・f-string・引数の数違い）"
"""静的に読めない宣言の理由。"""

type StatusPair = tuple[str, str, str]
"""(METHOD, パステンプレート, status) の 3 つ組。被覆・台帳・宣言のすべてがこの鍵を共有する。"""

type OperationKey = tuple[str, str]
"""(METHOD, パステンプレート) の 2 つ組。"""


@dataclass(frozen=True, slots=True)
class Operation:
    """正本が宣言する 1 操作。"""

    method: str
    """大文字に正規化した HTTP メソッド。"""

    path: str
    """パス**テンプレート**（`/settings/projects/{project_key}`）。実値を埋めない。"""

    operation_id: str
    x_mod: str
    """所有 MOD（`settings`／`trigger`／`orchestrator`／`apps/api`）。"""

    statuses: tuple[str, ...]
    """`responses` のキー（文字列のまま）。"""

    @property
    def key(self) -> OperationKey:
        """被覆の突合に使う鍵。"""
        return (self.method, self.path)

    @property
    def pairs(self) -> tuple[StatusPair, ...]:
        """この操作が持つ (操作, status) 対。"""
        return tuple((self.method, self.path, status) for status in self.statuses)


@dataclass(frozen=True, slots=True)
class Declaration:
    """テスト側が `http_op` で出した宣言。"""

    method: str
    path: str
    status: str
    source: str
    """見つけたファイルの基名（失敗時の案内に出す）。"""

    line: int

    @property
    def pair(self) -> StatusPair:
        """宣言した対。"""
        return (self.method, self.path, self.status)


@dataclass(frozen=True, slots=True)
class UnresolvedDeclaration:
    """引数を静的に読めなかった宣言（変数・f-string・引数の数が 3 でない）。"""

    source: str
    line: int
    reason: str


@dataclass(frozen=True, slots=True)
class CoverageDiff:
    """正本と「宣言 ∪ 台帳」の突合表（data-model §1）。"""

    missing_operations: tuple[OperationKey, ...]
    """正本に在って宣言も台帳も無い操作。"""

    unknown_operations: tuple[OperationKey, ...]
    """宣言または台帳に在って正本に無い操作。"""

    missing_pairs: tuple[StatusPair, ...]
    """正本に在って宣言も台帳も無い (操作, status)。"""

    unknown_pairs: tuple[StatusPair, ...]
    """宣言または台帳に在って正本に無い (操作, status)。"""

    overlapping_pairs: tuple[StatusPair, ...]
    """宣言と台帳の**両方**に在る対（「検証した」と「踏まない」の矛盾）。"""


def openapi_path() -> Path:
    """正本の絶対パス（cwd 非依存）。"""
    return find_repo_root() / OPENAPI_REL


def load_document(path: Path) -> Mapping[str, Any]:
    """正本を読む（**読むだけ**。1 バイトも書かない＝ FR-037）。

    Args:
        path: 正本の絶対パス。

    Returns:
        `yaml.safe_load` の結果。
    """
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        message = f"正本が写像でない: {path}"
        raise TypeError(message)
    return cast("Mapping[str, Any]", loaded)


def _as_mapping(node: object) -> Mapping[str, Any]:
    """写像として扱う（正本の構造が壊れていたら型で落とす）。"""
    if not isinstance(node, dict):
        message = f"写像を期待したが {type(node).__name__} だった: {node!r}"
        raise TypeError(message)
    return cast("Mapping[str, Any]", node)


def operations(doc: Mapping[str, Any]) -> tuple[Operation, ...]:
    """正本の全操作を読む（**件数を返さない**＝数えるのはテスト側）。

    `paths.<path>` 直下の非メソッドキー（`parameters` 等）は飛ばす。

    Args:
        doc: `load_document` の結果。

    Returns:
        正本に現れた順の全操作。
    """
    found: list[Operation] = []
    for path, raw_item in _as_mapping(doc["paths"]).items():
        item = _as_mapping(raw_item)
        for method, raw_op in item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            operation = _as_mapping(raw_op)
            responses = _as_mapping(operation["responses"])
            found.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    operation_id=str(operation["operationId"]),
                    x_mod=str(operation["x-mod"]),
                    statuses=tuple(str(status) for status in responses),
                )
            )
    return tuple(found)


def declared_pairs(doc: Mapping[str, Any]) -> frozenset[StatusPair]:
    """正本が宣言する (操作, status) 対の集合。

    Args:
        doc: `load_document` の結果。

    Returns:
        `(METHOD, パステンプレート, status)` の集合。
    """
    return frozenset(pair for operation in operations(doc) for pair in operation.pairs)


def resolve(doc: Mapping[str, Any], node: object) -> object:
    """`#/…` の `$ref` を正本内で解決する（多段・循環上限つき）。

    Args:
        doc: 正本。
        node: 解決したい節（`$ref` を持たなければそのまま返す）。

    Returns:
        解決後の節。

    Raises:
        ValueError: 参照が正本の外を指す／深さが上限を超えた。
    """
    current = node
    for _ in range(MAX_REF_DEPTH):
        if not isinstance(current, dict):
            return current
        mapping = cast("Mapping[str, Any]", current)
        ref = mapping.get(REF_KEY)
        if not isinstance(ref, str):
            return mapping
        if not ref.startswith(REF_PREFIX):
            message = f"正本の外を指す参照は解決しない: {ref}"
            raise ValueError(message)
        target: object = doc
        for part in ref.removeprefix(REF_PREFIX).split("/"):
            target = _as_mapping(target)[part]
        current = target
    message = f"$ref の解決が上限 {MAX_REF_DEPTH} 段を超えた（循環している疑い）: {node!r}"
    raise ValueError(message)


def response_schema(
    doc: Mapping[str, Any], *, path: str, method: str, status: str
) -> object | None:
    """宣言された応答本文の schema を返す（**204 は `None`**）。

    `responses.<status>` が `$ref` なら `components/responses` を辿り、
    `content["application/json"].schema` を取る。schema の中の `$ref` は**解決しない**
    （検査器が必要になった時点で `resolve` する）。

    Args:
        doc: 正本。
        path: パステンプレート。
        method: HTTP メソッド（大小どちらでもよい）。
        status: status（文字列）。

    Returns:
        schema の節。本文を持たない宣言（204）は `None`。
    """
    operation = _as_mapping(_as_mapping(_as_mapping(doc["paths"])[path])[method.lower()])
    response = resolve(doc, _as_mapping(operation["responses"])[status])
    content = _as_mapping(response).get("content")
    if content is None:
        return None
    return _as_mapping(_as_mapping(content)[JSON_MEDIA_TYPE])["schema"]


# --------------------------------------------------------------------------------------
# 宣言の静的走査（R-07。実行時収集は FR-034 を構造的に破るので採らない）
# --------------------------------------------------------------------------------------


def _is_http_op(node: ast.expr) -> bool:
    """`pytest.mark.http_op(...)` の呼び出しか（デコレータでも `marks=` の中でも同じ）。"""
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


def _literal_args(node: ast.Call) -> tuple[str, str, str] | None:
    """`http_op` の引数が**文字列リテラル 3 つ**ならその値（それ以外は `None`）。

    変数・f-string・連結・引数の数違い・キーワード引数はいずれも受理しない
    （AST で静的に読むための規約＝規約 §5）。
    """
    if node.keywords or len(node.args) != 3:
        return None
    values: list[str] = []
    for arg in node.args:
        if not isinstance(arg, ast.Constant):
            return None
        value: object = arg.value
        if not isinstance(value, str):
            return None
        values.append(value)
    return (values[0], values[1], values[2])


def collect_declarations(
    directory: Path,
) -> tuple[tuple[Declaration, ...], tuple[UnresolvedDeclaration, ...]]:
    """`directory` **直下**の `*.py` を AST で走査して宣言を集める（FR-034・R-07）。

    **実行中の pytest セッションの情報を一切使わない。** `-k` や `-m` で部分集合を実行しても
    結果が変わらないことが要件である（FR-034）。デコレータでも `pytest.param(marks=…)` の中でも
    AST 上は同じ `Call` ノードなので、モジュール全体から拾う。

    Args:
        directory: 走査するディレクトリ（`apps/api/tests/http_contract`）。

    Returns:
        (静的に読めた宣言, 読めなかった宣言) の対。どちらもファイル名・行番号の順。
    """
    found: list[Declaration] = []
    unresolved: list[UnresolvedDeclaration] = []
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_http_op(node):
                continue
            literals = _literal_args(node)
            if literals is None:
                unresolved.append(
                    UnresolvedDeclaration(
                        source=path.name,
                        line=node.lineno,
                        reason=UNREADABLE_ARGS,
                    )
                )
                continue
            method, op_path, status = literals
            found.append(
                Declaration(
                    method=method,
                    path=op_path,
                    status=status,
                    source=path.name,
                    line=node.lineno,
                )
            )
    return tuple(found), tuple(unresolved)


def compare(
    pairs: Iterable[StatusPair],
    declarations: Iterable[Declaration],
    ledger: Iterable[StatusPair],
) -> CoverageDiff:
    """正本の対と「宣言 ∪ 台帳」を突き合わせる（data-model §1・plan (v) C-1〜C-4）。

    Args:
        pairs: 正本が宣言する対（`declared_pairs` の結果、または `x-mod` で絞ったもの）。
        declarations: `collect_declarations` の第 1 要素。
        ledger: 踏まない対（`UNREACHED` の鍵）。

    Returns:
        5 方向の差分。
    """
    source_pairs = frozenset(pairs)
    declared = frozenset(item.pair for item in declarations)
    unreached = frozenset(ledger)
    covered = declared | unreached
    source_ops = frozenset((method, path) for method, path, _status in source_pairs)
    covered_ops = frozenset((method, path) for method, path, _status in covered)
    return CoverageDiff(
        missing_operations=tuple(sorted(source_ops - covered_ops)),
        unknown_operations=tuple(sorted(covered_ops - source_ops)),
        missing_pairs=tuple(sorted(source_pairs - covered)),
        unknown_pairs=tuple(sorted(covered - source_pairs)),
        overlapping_pairs=tuple(sorted(declared & unreached)),
    )
