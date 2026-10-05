"""F12 トリガー画面（`trigger/ui.py`）の**素の Python の検査**（C 群＋`016` の追加）。

`011` contracts/ui-page.md §7 の **T-U5**（import の境界）と、`016` の 3 本（ページ部品の実値・
`call_api` を `fetch` の外で呼ばないこと・`ui.page` と記憶を使わないこと）を置く。

画面を**描いて**確かめる観点は `test_trigger_ui_page.py`（NiceGUI の `user` フィクスチャ）へ
移した（`016` test-mapping の A 群）。旧 `AppTest` の 12 本はそこに移し、本ファイルからは
`streamlit` への依存ごと撤去した（`016` FR-026・T033）。

**`ui.pages` を import しない**（feature の tests から apps を import すると依存方向が逆向きに
なる＝憲章 §7-5）。登録が `module="trigger"` の 1 エントリで apps/api 側と一致することは
**REG-8h**（`uv run pytest apps/api/tests/foundation`）に委ねる。
"""

from __future__ import annotations

import ast
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
"""ページ部品を作るときに渡す接続先（描画はしないので値は何でもよい）。"""

# -- T-U5: `trigger.*` の import が 0 件（SC-013） -----------------------------------------


def test_tu5_the_page_imports_nothing_from_trigger() -> None:
    """T-U5・SC-013・U-1: `ast.parse(ui.py)` で `trigger` で始まるモジュールパスが **0 件**。

    これにより `ui-pages` 契約が `allow_indirect_imports` なしで成立し、画面のテストが trigger の
    組み立てを一切要さない。
    """
    import trigger.ui

    source = Path(trigger.ui.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _is_trigger(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module and _is_trigger(node.module):
            found.append(node.module)

    assert found == [], f"画面が自モジュールを import している: {found}"


def test_tu5_the_page_imports_neither_httpx_nor_requests() -> None:
    """U-2・010 SD-06: HTTP は標準ライブラリ `urllib.request` の 1 関数に閉じる。"""
    import trigger.ui

    source = Path(trigger.ui.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    assert not [name for name in modules if name.split(".")[0] in {"httpx", "requests"}]


def test_tu5_the_page_never_mentions_the_environment_keys() -> None:
    """U-3・010 SD-05: `TRIGGER_ROOT`・`DOWNLOADS_DIR` を**文字列としても書かない**。"""
    import trigger.ui

    source = Path(trigger.ui.__file__).read_text(encoding="utf-8")

    assert "TRIGGER_ROOT" not in source
    assert "DOWNLOADS_DIR" not in source


def _is_trigger(module: str) -> bool:
    return module == "trigger" or module.startswith("trigger.")


# -- 016: ページ部品・`fetch` 経由・`ui.page` を置かないこと -------------------------------


def test_build_triggers_page_returns_a_page_component() -> None:
    """N-1・data-model §2: 生成関数はタイトル・経路スラグ・描画関数を持つ部品を返す。

    旧 `test_tu1_the_page_factory_returns_a_streamlit_page`（:205）の移し先。中空の `st.Page` では
    読めなかった `title`・`url_path` を、素の frozen dataclass の属性として実値で確かめる。
    """
    import trigger.ui

    page = trigger.ui.build_triggers_page(BASE_URL)

    assert page.title == "トリガー"
    assert page.slug == "triggers"
    assert callable(page.render)


def _source_tree() -> ast.Module:
    """画面のソースを構文木にする。

    Returns:
        `trigger/ui.py` の構文木。
    """
    import trigger.ui

    return ast.parse(Path(trigger.ui.__file__).read_text(encoding="utf-8"))


def _code_identifiers(tree: ast.Module) -> set[str]:
    """コードに現れる識別子を集める（docstring・注記・文字列は見ない）。

    ⚠ 素朴な文字列走査にしない —— 「これを使わない」と書いた docstring そのものが引っかかる
    （2026-09-27 実測）。集めるのは変数名・属性名・点で連ねた属性の鎖・キーワード引数の名前である。

    Args:
        tree: 構文木。

    Returns:
        識別子の集合。
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
            chain: list[str] = [node.attr]
            current: ast.expr = node.value
            while isinstance(current, ast.Attribute):
                chain.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                chain.append(current.id)
                found.add(".".join(reversed(chain)))
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
    return found


def test_the_page_reaches_call_api_only_through_fetch() -> None:
    """ui-pages §3・FR-012: 画面の処理は `call_api` を直接使わず、必ず `fetch` を通す。

    ⚠ **呼び出し（`ast.Call`）ではなく参照（`ast.Name`）を数える** —— settings の `fetch` は
    `asyncio.to_thread(call_api, ...)` と**関数を引数で渡す**ので（data-model §4「settings には
    畳む層が無い」）、`Call(func=Name("call_api"))` がファイル中に 1 つも無い。呼び出しだけを
    数えると `∅ == ∅` の空振りになり、ハンドラに `asyncio.to_thread(call_api, ...)` を直書きする
    という **FR-012 違反の最も自然な形**も、別名を経由する形も捕まらない（2026-09-28 に変異で
    実測。`016` design-sync B-14）。

    ⚠ 同居テスト（SC-005）は代表の経路しか見ないので、**全経路に当てる**検査をここに置く
    （tasks 冒頭「計画外の検査の追加」）。`fetch` の中の入れ子の関数（`asyncio.to_thread` に
    渡す閉包）は `fetch` の内側として数える。
    """
    tree = _source_tree()
    fetch_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "fetch"
    )

    def _reference_lines(root: ast.AST) -> set[int]:
        return {
            node.lineno
            for node in ast.walk(root)
            if isinstance(node, ast.Name) and node.id == "call_api"
        }

    inside = _reference_lines(fetch_node)
    outside = _reference_lines(tree) - inside

    assert inside, "`fetch` が `call_api` に触れていない（差し替え点に到達しない）"
    assert outside == set(), f"`fetch` の外で `call_api` に触れている行: {sorted(outside)}"


def test_the_page_registers_no_route_and_uses_no_browser_storage() -> None:
    """research R-06・FR-021: `ui.page` を呼ばず、`storage_secret`・`app.storage` を使わない。

    経路を登録するのは apps/ui の殻だけである（`user` フィクスチャの後始末が feature の
    パッケージを `sys.modules` から消さないようにするため）。
    """
    tree = _source_tree()
    pages = [
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(".page")
    ]
    identifiers = _code_identifiers(tree)

    assert pages == [], f"画面が経路を登録している: {pages}"
    assert "storage_secret" not in identifiers
    assert "app.storage" not in identifiers
    assert "storage" not in identifiers
