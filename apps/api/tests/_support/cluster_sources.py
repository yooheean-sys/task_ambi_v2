"""設計正本の読み手（クラスタ割当 csv とクラスタ文書 §6 の観点表）。data-model §2・plan (v)。

**期待値を 1 つも持たない**（`contract_ledger.py` と同じ規律）。件数・行数・重複数はいずれも
返さない —— 数えるのはテスト側である。正本が改訂されたら、テストコードの数値を書き換えずに
結果が追随する。

読む正本は 2 つ。

1. `docs/design/appendix_cluster_test_plan/x2_cluster_index.csv`（**17 列・CRLF・UTF-8**）
2. `docs/design/appendix_cluster_test_plan/x2_cluster_0N_<slug>.md` の **§6 の観点表**
   （ファイル名は 1 の「文書」列から引く。ハードコードしない）

加えて、クラスタのテストが付けた**観点行の宣言**（`cluster_case` ほか）を **AST で静的に**
走査する。実行中の pytest セッションの情報を一切使わない（`-k`・`-m`・部分集合実行で結果が
変わらないことが要件＝ FR-012）。
"""

from __future__ import annotations

import ast
import csv
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from _support.contract_ledger import EM_DASH, META_FLAG

from common.kernel.config import find_repo_root

if TYPE_CHECKING:
    from pathlib import Path

CLUSTER_INDEX_REL = "docs/design/appendix_cluster_test_plan/x2_cluster_index.csv"
"""クラスタ割当の正本（リポジトリルート相対）。"""

CLUSTER_DOCS_REL = "docs/design/appendix_cluster_test_plan"
"""クラスタ文書の置き場（`文書` 列のファイル名をここに繋ぐ）。"""

LIST_SEPARATOR = ";"
"""csv のセル内で並びを区切る記号。"""

CASE_MARKER = "cluster_case"
NOT_EXERCISED_MARKER = "cluster_not_exercised"
CONDITIONAL_MARKER = "cluster_conditional"

_SECTION_SIX = re.compile(r"^## 6\.")
"""観点表を持つ節の見出し（**直後の最初の Markdown 表**を読む）。"""

_TABLE_ROW = re.compile(r"^\|\s*(\d+)\s*\|")
"""観点表の本文行（`#` 列が数字である行だけを取る。ヘッダ行・区切り行は落ちる）。"""

_NOTE_PATTERN = re.compile(r"（[^（）]*）")
"""セル内の**全角括弧の注記**（分割の**前**に落とす。`contract_ledger` と同型）。"""


def cluster_index_path() -> Path:
    """クラスタ割当 csv の絶対パス。"""
    return find_repo_root() / CLUSTER_INDEX_REL


def cluster_docs_dir() -> Path:
    """クラスタ文書の置き場の絶対パス。"""
    return find_repo_root() / CLUSTER_DOCS_REL


# --------------------------------------------------------------------------------------
# 1. クラスタ割当（`x2_cluster_index.csv` の 1 行）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusterAssignment:
    """クラスタ割当 csv の 1 行。クラスタの単位である。"""

    cluster_id: str
    """`"01"`〜`"05"`。"""

    slug: str
    """`"monitor_mapping"` 等（ディレクトリ名の後半）。"""

    modules: tuple[str, ...]
    """`対象MOD` 列（`enabled_modules` の期待値）。"""

    primary_ifs: tuple[str, ...]
    """`主検証IF` 列（被覆検査の期待値）。"""

    passthrough_ifs: tuple[str, ...]
    """`通過IF` 列（**宣言してはならない**側）。"""

    stub_protocols: tuple[str, ...]
    """`Protocolスタブ` 列（override 集合の照合）。"""

    actual_eifs: tuple[str, ...]
    """`実際に通すEIF` 列（`requires_bd` の期待値の出所）。"""

    document: str
    """`文書` 列（§6 の読み手へ渡すファイル名）。"""

    @property
    def directory_name(self) -> str:
        """テストの置き場のディレクトリ名（`cluster_{id}_{slug}`）。"""
        return f"cluster_{self.cluster_id}_{self.slug}"

    @property
    def document_path(self) -> Path:
        """クラスタ文書の絶対パス。"""
        return cluster_docs_dir() / self.document


def _split_list(raw: str) -> tuple[str, ...]:
    """`;` 区切りのセルを組へ割る（空セル・**「無し」の記号 `—`** は空の組）。

    `contract_ledger._split_exceptions` と**同型**にする —— 全角括弧の注記を**先に**落として
    から `—` を判定する。03 の `Protocolスタブ` 欄は
    `—（呼び手 CrewExecutor.run はテストがドライバ。憲章 §7-6）` であり、注記を落とさないと
    「スタブ 1 件」と読まれて配線の照合が黙って誤る（override は 0 件が正）。
    """
    stripped = _NOTE_PATTERN.sub("", raw).strip()
    if not stripped or stripped == EM_DASH:
        return ()
    return tuple(
        item
        for item in (part.strip() for part in stripped.split(LIST_SEPARATOR))
        if item and item != EM_DASH
    )


def load_cluster_assignments(csv_path: Path) -> tuple[ClusterAssignment, ...]:
    """クラスタ割当 csv を読む（**件数も重複数も返さない**＝ R-10・data-model §2-1）。

    CRLF・UTF-8。`csv.DictReader` に `newline=""` で渡す（`contract_ledger.load_contract_rows`
    と同じ形）。テストは `tmp_path` に作った**写し**も渡せる（数値を直書きしていないことの
    証明に使う）。

    Args:
        csv_path: 読む csv（正本またはその写し）。

    Returns:
        csv の行順に並べた割当。
    """
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = [
            ClusterAssignment(
                cluster_id=record["cluster_id"].strip(),
                slug=record["slug"].strip(),
                modules=_split_list(record["対象MOD"]),
                primary_ifs=_split_list(record["主検証IF"]),
                passthrough_ifs=_split_list(record["通過IF"]),
                stub_protocols=_split_list(record["Protocolスタブ"]),
                actual_eifs=_split_list(record["実際に通すEIF"]),
                document=record["文書"].strip(),
            )
            for record in csv.DictReader(handle)
        ]
    return tuple(rows)


REQUIRED_COLUMNS = (
    "cluster_id",
    "slug",
    "対象MOD",
    "主検証IF",
    "通過IF",
    "Protocolスタブ",
    "実際に通すEIF",
    "文書",
)
"""`load_cluster_assignments` が読む列（**この 8 つが在れば読める**）。

健全性検査はここを期待値に使う —— 「列数が 17」をテストへ書き写すと、正本が 1 列増えただけで
読めているのに赤くなる（FR-011 の「期待値を書き写さない」に反する）。見るべきは
**読み手が要る列がヘッダに実在するか**である。
"""


def header_columns(csv_path: Path) -> tuple[str, ...]:
    """csv のヘッダ行の列名（健全性検査がここから期待値を作る）。

    Args:
        csv_path: 読む csv。

    Returns:
        ヘッダ行の列名。
    """
    with csv_path.open(encoding="utf-8", newline="") as handle:
        empty: list[str] = []
        header = next(csv.reader(handle), empty)
    return tuple(name.strip() for name in header)


# --------------------------------------------------------------------------------------
# 2. 観点行（クラスタ文書 §6 の 1 行）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseRow:
    """クラスタ文書 §6 の観点表の 1 行。検証観点の単位である。"""

    cluster_id: str
    """呼び出し側が渡す（文書とクラスタの対応は index.csv が持つ）。"""

    number: int
    """`#` 列。"""

    viewpoint: str
    expectation: str
    source: str

    @property
    def case_id(self) -> str:
        """`NN#k`（`cluster_case` の引数と同じ書式）。"""
        return f"{self.cluster_id}#{self.number}"


def load_case_rows(doc_path: Path, cluster_id: str) -> tuple[CaseRow, ...]:
    """クラスタ文書 §6 の観点表を読む（**読めたものを返すだけ**＝ R-11）。

    `^## 6\\.` にマッチする見出しを探し、**その直後の最初の Markdown 表**を読む。表のヘッダ行と
    区切り行を飛ばし、`^\\|\\s*(\\d+)\\s*\\|` にマッチする行だけを取る。

    **健全性はテスト側が見る**（5 文書とも 1 行以上・1 起点連番＝ FR-023a）。読み手が判定まで
    持つと、抽出が 0 行に落ちたときに「空 vs 空」で偽の緑になりうる（`014` が踏んだ形）。

    Args:
        doc_path: 読むクラスタ文書。
        cluster_id: その文書のクラスタ ID（`case_id` の前半になる）。

    Returns:
        表に現れた順の観点行。
    """
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    found: list[CaseRow] = []
    in_section = False
    seen_table = False
    for line in lines:
        if _SECTION_SIX.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("## "):
            break
        match = _TABLE_ROW.match(line)
        if match is None:
            if seen_table and not line.startswith("|"):
                # 最初の表が終わった（同じ節に別の表が続いても読まない）。
                break
            continue
        seen_table = True
        cells = _table_cells(line)
        found.append(
            CaseRow(
                cluster_id=cluster_id,
                number=int(match.group(1)),
                viewpoint=cells[1] if len(cells) > 1 else "",
                expectation=cells[2] if len(cells) > 2 else "",
                source=cells[3] if len(cells) > 3 else "",
            )
        )
    return tuple(found)


def _table_cells(line: str) -> list[str]:
    """Markdown 表の 1 行をセルへ割る（両端の `|` を落として空白を削る）。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


# --------------------------------------------------------------------------------------
# 3. 観点行の宣言（クラスタのテストが付けたマーカ）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseDeclaration:
    """クラスタのテスト 1 本が宣言した観点行。"""

    case_id: str | None
    """`cluster_case` の引数（受理できる書式で 1 つだけ付いていなければ `None`）。"""

    test_name: str
    file: str
    """`cluster_dir` 相対のファイル名。"""

    count: int
    """そのテストに付いた `cluster_case` の個数（0 も記録する）。"""

    not_exercised: str | None
    """`cluster_not_exercised` の理由 ID（無ければ `None`）。"""

    conditional: str | None
    """`cluster_conditional` の理由 ID（無ければ `None`）。"""

    is_meta: bool
    """そのファイルが `CONTRACT_META = True` を持つか。"""


def _is_marker(node: ast.expr, name: str) -> bool:
    """デコレータが `pytest.mark.<name>(...)` の呼び出しか。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != name:
        return False
    mark = func.value
    if not isinstance(mark, ast.Attribute) or mark.attr != "mark":
        return False
    root = mark.value
    return isinstance(root, ast.Name) and root.id == "pytest"


def _literal_arg(node: ast.expr) -> str | None:
    """マーカの引数が**文字列リテラル 1 個**ならその値（それ以外は `None`）。

    変数・f-string・連結・引数 0 個／2 個以上・キーワード引数はいずれも受理しない
    （AST で静的に読むための規約＝規約 §7）。
    """
    if not isinstance(node, ast.Call) or node.keywords or len(node.args) != 1:
        return None
    only = node.args[0]
    if not isinstance(only, ast.Constant):
        return None
    value: object = only.value
    return value if isinstance(value, str) else None


def _first_literal(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> str | None:
    """そのマーカの最初のリテラル引数（付いていなければ `None`）。"""
    for item in node.decorator_list:
        if _is_marker(item, name):
            literal = _literal_arg(item)
            if literal is not None:
                return literal
    return None


def _declared_case(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str | None, int]:
    """テスト関数のデコレータから `(case_id, cluster_case の個数)` を読む。"""
    markers = [item for item in node.decorator_list if _is_marker(item, CASE_MARKER)]
    literals = [value for value in (_literal_arg(item) for item in markers) if value]
    case_id = literals[0] if len(markers) == 1 and len(literals) == 1 else None
    return case_id, len(markers)


def is_meta_module(tree: ast.Module) -> bool:
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


def test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """module 直下の `def test_*`（`contract_ledger.collect_declarations` と同じ範囲）。"""
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def collect_case_declarations(cluster_dir: Path) -> tuple[CaseDeclaration, ...]:
    """`cluster_dir` **直下**の `test_*.py` を AST で走査して観点行の宣言を集める。

    **実行中の pytest セッションの情報を一切使わない**（FR-012）。

    `contract_ledger.collect_declarations` との差は 1 点だけ —— **`CONTRACT_META = True` の
    ファイルも走査する**。観点行の対応は META ファイルのテストにも要るためである（IF に
    紐づかない観点行は META へまとめる＝ plan (v)・B-11）。META かどうかは `is_meta` に載せる。

    Args:
        cluster_dir: 走査するディレクトリ（`cluster_NN_<slug>/`）。

    Returns:
        ファイル名・関数名の順に並べた宣言。
    """
    found: list[CaseDeclaration] = []
    for path in sorted(cluster_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        meta = is_meta_module(tree)
        for node in test_functions(tree):
            case_id, count = _declared_case(node)
            found.append(
                CaseDeclaration(
                    case_id=case_id,
                    test_name=node.name,
                    file=path.name,
                    count=count,
                    not_exercised=_first_literal(node, NOT_EXERCISED_MARKER),
                    conditional=_first_literal(node, CONDITIONAL_MARKER),
                    is_meta=meta,
                )
            )
    return tuple(found)


def collect_contract_if_in_meta(cluster_dir: Path) -> tuple[tuple[str, str], ...]:
    """`CONTRACT_META = True` のファイルに付いた `contract_if` を集める（B-11 の抜け道封じ）。

    META ファイルは `collect_declarations` の走査から外れるので、そこへ宣言を隠せば被覆検査から
    逃げられてしまう。本関数の戻りが空でなければ規約違反である。

    Args:
        cluster_dir: 走査するディレクトリ。

    Returns:
        `(ファイル名, テスト関数名)` の組。
    """
    found: list[tuple[str, str]] = []
    for path in sorted(cluster_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not is_meta_module(tree):
            continue
        for node in test_functions(tree):
            if any(_is_marker(item, "contract_if") for item in node.decorator_list):
                found.append((path.name, node.name))
    return tuple(found)
