"""US6 設定画面（settings-F22）の**素の Python の検査**（C 群＋`016` の追加）。

`010` contracts/ui-pages.md §7 の **P-10**（`call_api` の応答の写し方）と、`016` の 5 本
（ページ部品の実値・`call_api` を `fetch` の外で呼ばないこと・`ui.page` と記憶を使わないこと・
文言の一覧の割り当て・応答ヘッダ名の大文字小文字）を置く。

画面を**描いて**確かめる観点は `test_settings_ui_ai_page.py`（AI 設定ページ）と
`test_settings_ui_projects_page.py`（プロジェクトページ）へ移した（`016` test-mapping の A 群）。
旧 `AppTest` の A 群（旧 :160・:277〜:589）はそこに移し、本ファイルからは `streamlit` への
依存ごと撤去した（`016` FR-026・T048・T055）。

**`ui.pages` を import しない**（feature の tests から apps を import すると依存方向が逆向きに
なる＝憲章 §7-5）。登録が `module="settings"` の 2 エントリで apps/api 側と一致することは
**REG-8h**（`uv run pytest apps/api/tests/foundation`）に委ねる。
"""

from __future__ import annotations

import ast
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import cast

import pytest

from settings import ui as settings_ui
from settings.ui import ApiResponse, call_api

BASE_URL = "http://127.0.0.1:8000"

WORDING_PATH = Path(__file__).with_name("settings_ui_wording.json")
"""文言の一覧（`1dd3f09` の `settings/ui.py` から抜き出した表示文字列）。"""


# -- T086 P-10: `call_api`（FR-088a） ----------------------------------------------------


def test_p10_a_success_and_a_violation_come_back_in_the_same_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-088a: 成功も検査違反も**同じ形**（応答コード ＋ 本文の写像）で返る。"""

    def _open(*_args: object, **_kwargs: object) -> _FakeStream:
        return _FakeStream(200, b'{"human_actors": []}', {"X-A": "1"})

    monkeypatch.setattr("urllib.request.urlopen", _open)

    response = call_api(BASE_URL, "GET", "/settings/humans")

    assert response.status == 200
    assert response.payload == {"human_actors": []}
    assert response.error is None
    assert response.headers["X-A"] == "1"


def test_p10_a_422_body_is_never_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-088a: `HTTPError` は**それ自体がファイルオブジェクト**なので本文を必ず読む。"""
    body = json.dumps({"problems": [{"where": "w", "kind": "forward", "reason": "r"}]}).encode()

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError(BASE_URL, 422, "Unprocessable", _headers(), io.BytesIO(body))

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    response = call_api(BASE_URL, "PUT", "/settings/humans", {"human_actors": []})

    assert response.status == 422
    assert response.payload == {"problems": [{"where": "w", "kind": "forward", "reason": "r"}]}


def test_p10_a_409_reason_is_never_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-088a: 409 の `detail`（単一文字列）も取りこぼさない。"""

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError(
            BASE_URL, 409, "Conflict", _headers(), io.BytesIO(b'{"detail": "\\u4e88\\u7d04"}')
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    response = call_api(BASE_URL, "DELETE", "/settings/projects/_program")

    assert response.status == 409
    assert response.payload == {"detail": "予約"}


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("接続拒否"), OSError("落ちている")],
    ids=["url_error", "os_error"],
)
def test_p10_a_connection_failure_becomes_status_zero(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-092・EIF-10: 接続不能は `status=0`・`error` 非 `None`（**再試行しない**）。"""

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    response = call_api(BASE_URL, "GET", "/settings/humans")

    assert response.status == 0
    assert response.payload is None
    assert response.error is not None


def _headers() -> Message:
    """`HTTPError` が要求するヘッダ（`email.message.Message`）の空の器。"""
    return Message()


class _FakeStream:
    """`urlopen` の戻りの偽物（context manager）。"""

    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers
        self._body = body

    def read(self) -> bytes:
        """本文を返す。"""
        return self._body

    def __enter__(self) -> _FakeStream:
        """`with` に入る。"""
        return self

    def __exit__(self, *_args: object) -> None:
        """`with` を出る。"""
        return


# -- 016: ページ部品・`fetch` 経由・`ui.page`・文言の割り当て・ヘッダの読み方 ---------------


def test_the_two_page_factories_return_page_components() -> None:
    """N-1・data-model §2: 2 つの生成関数が、タイトル・経路スラグ・描画関数を持つ部品を返す。

    旧 `test_p1_two_pages_are_public_and_both_return_a_page`（旧 :160）の移し先。中空の
    `st.Page` では読めなかった `title`・`url_path` を、素の frozen dataclass の属性として
    実値で確かめる。
    """
    ai_page = settings_ui.build_ai_settings_page(BASE_URL)
    projects_page = settings_ui.build_projects_page(BASE_URL)

    assert (ai_page.title, ai_page.slug) == ("AI 設定", "settings-ai")
    assert (projects_page.title, projects_page.slug) == ("プロジェクト", "settings-projects")
    assert callable(ai_page.render)
    assert callable(projects_page.render)


def _source_tree() -> ast.Module:
    """画面のソースを構文木にする。

    Returns:
        `settings/ui.py` の構文木。
    """
    return ast.parse(Path(settings_ui.__file__).read_text(encoding="utf-8"))


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


def _wording_entries() -> list[dict[str, object]]:
    """文言の一覧を読む（2 ページ分の全件）。

    Returns:
        一覧の各件。
    """
    loaded: object = json.loads(WORDING_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    items = cast("list[object]", loaded)
    entries: list[dict[str, object]] = []
    for item in items:
        assert isinstance(item, dict)
        entries.append(cast("dict[str, object]", item))
    return entries


def test_every_wording_entry_names_a_scenario_of_its_own_page() -> None:
    """N-18・SC-001a: 全件の `scenario` が、その件の `page` と同じ接頭辞を持つ。

    各ページのテストは自分のページの分しか照合しないので（`wording("ai")`）、接頭辞が
    ずれた件はどちらの照合からも漏れる。ここで全件を 2 つの照合に割り振り切る。
    """
    entries = _wording_entries()

    assert entries, "文言の一覧が空である"
    assert {str(entry["page"]) for entry in entries} == {"ai", "projects"}
    mismatched = [
        f"{entry['id']}: page={entry['page']} scenario={entry['scenario']}"
        for entry in entries
        if not str(entry["scenario"]).startswith(f"{entry['page']}:")
    ]

    assert not mismatched, f"page と scenario の接頭辞が一致しない: {mismatched}"


def test_success_message_reads_the_header_names_case_insensitively() -> None:
    """D-1・data-model §3: 反映区分と世代を、ヘッダ名の大文字小文字によらず読む。

    ⚠ ヘッダ**値**の綴りは口が載せるとおりのリテラル（`010` SD-18）。ヘッダ**名**は画面の
    定数から組み、小文字化した綴りと突き合わせる（定数が既に小文字なら 2 つの写像が同じに
    なり検査が空振りするので、違うことを先に表明する）。
    """
    effect, generation = settings_ui.EFFECT_HEADER, settings_ui.GENERATION_HEADER
    # ⚠ **2 つを個別に**表明する —— タプルで比べると片方だけ大文字を含めば通り、もう片方が
    # 小文字化された将来にその項だけ静かに空振りへ落ちる（`016` design-sync B-15）
    assert effect != effect.lower(), f"{effect} が既に小文字（検査が空振りする）"
    assert generation != generation.lower(), f"{generation} が既に小文字（検査が空振りする）"

    mixed = settings_ui.success_message(
        ApiResponse(status=200, payload=None, headers={effect: "next-kickoff", generation: "7"})
    )
    lower = settings_ui.success_message(
        ApiResponse(
            status=200,
            payload=None,
            headers={effect.lower(): "next-kickoff", generation.lower(): "7"},
        )
    )
    bare = settings_ui.success_message(ApiResponse(status=200, payload=None, headers={}))

    assert mixed == lower
    assert mixed != bare, "ヘッダを読まずに同じ文言を返している（検査が空振りする）"
