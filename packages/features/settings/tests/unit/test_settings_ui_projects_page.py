"""プロジェクトページ（NiceGUI）の検証（US1 AS-5a・AS-8・AS-9 の #14〜#16・AS-12）。

対応する契約項は `contracts/ui-pages.md` の N-5・N-10・N-19（と、そこから引く U-K・D-1・D-5・
D-7・R-1・W-1・J-9・J-12・J-15・N-18）である。旧テストの移し先は `contracts/test-mapping.md`
の略号 **SP**（旧 `test_settings_ui.py:347` `test_p5_a_connection_failure_only_shows_the_error`
を含む）。

手段は NiceGUI の `user` フィクスチャ（ブラウザを立てない＝憲章 2.0.0 §8・FR-031）。

⚠ 規約 `contracts/ui-test-conventions.md` §2・§3 を守る。
1. `settings.ui` はモジュール先頭で束ね、差し替えは `monkeypatch.setattr` で行う
   （文字列経路の差し替えを使わない）。
2. ページ関数はテスト関数の中で定義し、描画関数を直接呼ぶ（apps/ui の殻を通さない）。
3. 固定の `sleep` を使わない（非同期の結果は `should_see` か `wait_until` の待ちで確かめる）。
4. 補助は `test_settings_ui_ai_page.py` から写す（共有の conftest を作らない＝規約 §4）。

⚠ **偽の応答のヘッダ名は小文字で組む**（FR-008・D-1）—— Starlette は小文字で送るので、画面と
同じ綴りで偽装すると、綴りがずれていても分岐が一致して緑になる。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from nicegui import ElementFilter
from nicegui import ui as nui

from settings import ui as settings_ui

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from nicegui.element import Element
    from nicegui.testing import User

BASE_URL = "http://settings-ui.invalid:9999"
"""画面に渡す接続先（実運用の値ではないことが一目で分かる形）。"""

WORDING_PATH = Path(__file__).with_name("settings_ui_wording.json")
"""文言の一覧（`1dd3f09` の `settings/ui.py` から抜き出した表示文字列）。"""

PERSONAS = ("programmer", "reviewer", "planner")
"""`GET /settings/agents` が返すペルソナ 3 種（役割フォルダ欄の数と印を見るための偽の応答）。"""

FILLED_ROLE_PERSONAS = ("programmer", "planner")
"""役割フォルダ欄に値を入れるペルソナ（`reviewer` は空にして本文から落ちることを見る）。"""

PROJECT_KEY = "pj"
"""登録・編集・解除で使うプロジェクトキー（英数だけ＝移行前と同じ経路になる値）。"""

AGENTS_GLOBAL: list[dict[str, object]] = [
    {"persona_key": persona, "project_key": None, "role": f"{persona} の役割"}
    for persona in PERSONAS
]
"""`GET /settings/agents` の既定の本文（役割フォルダ欄の組み立てに使う読取＝ data-model §5）。"""

PROJECTS: list[dict[str, object]] = [
    {
        "project_key": PROJECT_KEY,
        "name": "見本プロジェクト",
        "folders": {
            "pj_input_dir": "in",
            "role_dirs": {"programmer": "role-prog"},
            "outcome_dir": "out",
        },
        "is_reserved": False,
    },
    {
        "project_key": "_program",
        "name": "予約",
        "folders": {"pj_input_dir": "i", "role_dirs": {}, "outcome_dir": "o"},
        "is_reserved": True,
    },
]
"""`GET /settings/projects` の既定の本文（一覧の表の元＝ `ProjectView` 2 件）。"""

RELOADED_PROJECTS: list[dict[str, object]] = [
    {
        "project_key": "pj2",
        "name": "取り直した後",
        "folders": {"pj_input_dir": "in2", "role_dirs": {}, "outcome_dir": "out2"},
        "is_reserved": False,
    }
]
"""取り直しで返す 2 回目の本文（表が置き換わったことを見る）。"""

FORM_BODY: dict[str, object] = {
    "project_key": PROJECT_KEY,
    "name": "見本プロジェクト",
    "folders": {
        "pj_input_dir": "in",
        "role_dirs": {"programmer": "role-prog", "planner": "role-plan"},
        "outcome_dir": "out",
    },
}
"""#14・#15 の期待する本文（`role_dirs` は**値を入れたペルソナだけ**＝ §2-2 の表）。"""

WRITE_BUTTONS = ("pj_create", "pj_update", "pj_release")
"""プロジェクトページの書込ボタン 3 個（§2-2 の #14〜#16）。"""

SECTION_HEADINGS = ("登録・編集", "解除")
"""一覧の後に続く節の見出し（移行前の `st.header` の単位。並びは移行前のまま）。"""

PROBLEM: dict[str, object] = {
    "where": "/srv/area-a/projects.yaml",
    "kind": "shape",
    "reason": "必須欄が空",
}
"""422 の違反 1 件（`where` は口の値をそのまま出す）。"""


# --------------------------------------------------------------------------------------
# 共通の補助（`test_settings_ui_ai_page.py` と同じ形。feature をまたいで共有しない）
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class FakeApi:
    """要求を記録する偽の `call_api`（唯一の差し替え点＝ ui-pages §3）。

    Attributes:
        responses: `(method, path)` → 応答。値を並びにすると、呼ばれた順に消費して最後の値が残る。
        calls: 送られた `(method, path, body)` の並び。
        gate: 設定すると、応答を返す前にこの `Event` が立つまで待つ（同時実行の検査）。
        gate_methods: `gate` で塞ぐメソッド。
    """

    responses: dict[tuple[str, str], object] = field(default_factory=dict[tuple[str, str], object])
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])
    gate: threading.Event | None = None
    gate_methods: tuple[str, ...] = ()

    def __call__(
        self, _base_url: str, method: str, path: str, body: object | None = None
    ) -> settings_ui.ApiResponse:
        """要求を記録して応答を返す。"""
        self.calls.append((method, path, body))
        if self.gate is not None and method in self.gate_methods:
            self.gate.wait(timeout=10.0)  # ⚠ 立たなくてもテストが固まらないように上限を置く
        return self._response_for(method, path)

    def paths(self, method: str | None = None) -> list[str]:
        """記録した経路を返す。

        Args:
            method: 絞り込むメソッド（`None` なら全部）。

        Returns:
            経路の並び。
        """
        return [path for verb, path, _ in self.calls if method is None or verb == method]

    def writes(self) -> list[tuple[str, str, object]]:
        """書込要求（GET 以外）だけを返す。

        Returns:
            `(method, path, body)` の並び。
        """
        return [call for call in self.calls if call[0] != "GET"]

    def _response_for(self, method: str, path: str) -> settings_ui.ApiResponse:
        """応答表から 1 件引く（並びなら順に消費する）。

        Args:
            method: HTTP メソッド。
            path: 経路（クエリを含む）。

        Returns:
            応答。表に無ければ既定を返す。
        """
        for key in ((method, path), (method, path.split("?")[0])):
            if key not in self.responses:
                continue
            found = self.responses[key]
            if isinstance(found, list):
                queued = cast("list[settings_ui.ApiResponse]", found)
                return queued.pop(0) if len(queued) > 1 else queued[0]
            return cast("settings_ui.ApiResponse", found)
        return _default_response(method, path)


def _default_response(method: str, path: str) -> settings_ui.ApiResponse:
    """応答表に無いときの既定（正常系の最小の形）。

    Args:
        method: HTTP メソッド。
        path: 経路。

    Returns:
        応答。
    """
    if method != "GET":
        return saved()
    defaults: dict[str, object] = {
        "/settings/projects": PROJECTS,
        "/settings/agents": AGENTS_GLOBAL,
    }
    return ok(defaults.get(path.partition("?")[0]))


def ok(payload: object) -> settings_ui.ApiResponse:
    """200 の応答（ヘッダなし）を作る。

    Args:
        payload: 本文。

    Returns:
        応答。
    """
    return settings_ui.ApiResponse(status=200, payload=payload)


def saved(
    *, effect: str | None = "next-kickoff", generation: str | None = "7", lower: bool = True
) -> settings_ui.ApiResponse:
    """書込の成功の応答（反映区分と世代のヘッダ付き）を作る。

    Args:
        effect: 反映区分（`None` ならヘッダを載せない）。
        generation: 世代（`None` ならヘッダを載せない）。
        lower: ヘッダ名を小文字で組むか（FR-008。偽の応答の既定は小文字）。

    Returns:
        応答。
    """
    headers: dict[str, str] = {}
    effect_name = "x-settings-effect" if lower else "X-Settings-Effect"
    generation_name = "x-settings-generation" if lower else "X-Settings-Generation"
    if effect is not None:
        headers[effect_name] = effect
    if generation is not None:
        headers[generation_name] = generation
    return settings_ui.ApiResponse(status=200, payload=None, headers=headers)


def failing(status: int, payload: object) -> settings_ui.ApiResponse:
    """2xx でない応答を作る。

    Args:
        status: 応答コード。
        payload: 本文。

    Returns:
        応答。
    """
    return settings_ui.ApiResponse(status=status, payload=payload)


def unreachable() -> settings_ui.ApiResponse:
    """接続できない応答（`status=0`）を作る。

    Returns:
        応答。
    """
    return settings_ui.ApiResponse(
        status=0, payload=None, error="app に接続できません: <urlopen error>"
    )


async def open_page(user: User) -> None:
    """プロジェクトページを開く（殻を通さず描画関数を直接呼ぶ＝規約 §2）。

    Args:
        user: `user` フィクスチャ。
    """

    @nui.page("/")
    async def _page() -> None:  # pyright: ignore[reportUnusedFunction]  # `@nui.page` が経路に登録するので、名前は参照されない
        await settings_ui.render_projects(BASE_URL)

    await user.open("/")


def use_api(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> FakeApi:
    """偽の `call_api` を差し込む。

    Args:
        monkeypatch: 差し替えの後始末を持つ pytest の道具。
        api: 差し込む偽物。

    Returns:
        差し込んだ偽物。
    """
    monkeypatch.setattr(settings_ui, "call_api", api)
    return api


def _strings_of(value: object) -> list[str]:
    """任意の値から、表示になりうる文字だけを取り出す。

    Args:
        value: 調べる値。

    Returns:
        空でない文字の並び。
    """
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        items = cast("list[object]", value)
        return [text for item in items for text in _strings_of(item)]
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        return [text for item in mapping.values() for text in _strings_of(item)]
    return []


def _mappings_of(value: object) -> list[dict[object, object]]:
    """一覧の中の写像だけを取り出す（表の列・行を読むため）。

    Args:
        value: 調べる値。

    Returns:
        写像の並び。
    """
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast("dict[object, object]", item) for item in items if isinstance(item, dict)]


def _visible_elements(user: User) -> list[Element]:
    """画面に**見えている**部品だけを集める（祖先が隠れているものも除く）。

    ⚠ `current_layout.descendants()` は**隠した部品まで返す**ので、N-18 の完全一致の検査が
    弱くなる（`016` 2026-09-27 実測）。NiceGUI 自身の `ElementFilter(only_visible=True)` に委ねる。

    Args:
        user: `user` フィクスチャ。

    Returns:
        見えている部品の並び。
    """
    client = user.client
    assert client is not None, "ページをまだ開いていない"
    with client:
        return list(ElementFilter(only_visible=True, local_scope=False))


def texts_on(user: User) -> set[str]:
    """画面の部品の文字を集める（N-18 の照合範囲）。

    Args:
        user: `user` フィクスチャ。

    Returns:
        画面に現れている文字の集合。
    """
    found: set[str] = set()
    for element in _visible_elements(user):
        props: dict[str, object] = dict(element.props)
        for key in ("text", "label", "placeholder", "value", "error-message"):
            found.update(_strings_of(props.get(key)))
        for attribute in ("text", "content"):
            found.update(_strings_of(getattr(element, attribute, None)))
        found.update(_strings_of(getattr(element, "options", None)))
        for column in _mappings_of(getattr(element, "columns", None)):
            found.update(_strings_of(column.get("label")))
        for row in _mappings_of(getattr(element, "rows", None)):
            for cell in row.values():
                found.update(_strings_of(cell))
    return found


def table_of(user: User, marker: str) -> tuple[list[str], list[dict[object, object]]]:
    """印の付いた表の列名と行を読む（規約 §2）。

    Args:
        user: `user` フィクスチャ。
        marker: 表の印。

    Returns:
        `(列名の並び, 行の並び)`。
    """
    element = one(user, marker)
    columns = _mappings_of(getattr(element, "columns", None))
    labels = [str(column.get("label", "")) for column in columns]
    return labels, _mappings_of(getattr(element, "rows", None))


def one(user: User, marker: str) -> Element:
    """印の付いた部品を 1 つだけ引く。

    Args:
        user: `user` フィクスチャ。
        marker: 印。

    Returns:
        その部品。
    """
    elements = sorted(user.find(marker=marker).elements, key=lambda item: item.id)
    assert len(elements) == 1, f"印 {marker!r} の部品が 1 つでない: {len(elements)}"
    return elements[0]


def exists(user: User, marker: str) -> bool:
    """印の付いた部品が**見える状態で**在るかを見る。

    ⚠ 「隠す」は NiceGUI の可視性（`set_visibility`）で行うので、判定も可視性を見る必要がある。
    `user.find` は `only_visible=True` で、祖先の可視性も見る形で引くので、それに委ねる
    （見つからなければ `AssertionError` を投げる）。

    Args:
        user: `user` フィクスチャ。
        marker: 印。

    Returns:
        見える状態で 1 つ以上在れば True。
    """
    try:
        user.find(marker=marker)
    except AssertionError:
        return False
    return True


def value_of(user: User, marker: str) -> object:
    """入力欄の値を読む。

    Args:
        user: `user` フィクスチャ。
        marker: 印。

    Returns:
        現在の値。
    """
    return getattr(one(user, marker), "value", None)


def is_disabled(user: User, marker: str) -> bool:
    """部品が押せない状態かを見る。

    Args:
        user: `user` フィクスチャ。
        marker: 印。

    Returns:
        押せないなら True。
    """
    return not getattr(one(user, marker), "enabled", True)


def fill(user: User, marker: str, text: str) -> None:
    """テキスト欄に文字を入れる（確定はしない）。

    Args:
        user: `user` フィクスチャ。
        marker: 印。
        text: 入れる文字。
    """
    interaction = user.find(marker=marker)
    interaction.clear()
    if text:
        interaction.type(text)


async def wait_until(condition: Callable[[], bool], *, what: str, retries: int = 200) -> None:
    """条件が満たされるまで上限つきで待つ（固定の `sleep` を使わない）。

    ⚠ `should_see` は**すでに満たされている条件では待受に譲らない**ので、非同期の処理の観測には
    こちらを使う（`016` design-sync の trigger 側と同じ理由）。

    Args:
        condition: 満たされたら True を返す関数。
        what: 満たされなかったときの説明。
        retries: 試す回数（1 回 0.01 秒）。

    Raises:
        AssertionError: 上限まで満たされなかったとき。
    """
    for _ in range(retries):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"条件が満たされなかった: {what}")


def result_texts(user: User, marker: str) -> set[str]:
    """書込欄の結果表示（印 `<書込ボタンの印>-result`）の中の文字を集める。

    Args:
        user: `user` フィクスチャ。
        marker: 書込ボタンの印。

    Returns:
        結果表示の中の文字の集合。
    """
    found: set[str] = set()
    for element in one(user, f"{marker}-result").descendants():
        props: dict[str, object] = dict(element.props)
        for key in ("text", "label"):
            found.update(_strings_of(props.get(key)))
        found.update(_strings_of(getattr(element, "text", None)))
        for column in _mappings_of(getattr(element, "columns", None)):
            found.update(_strings_of(column.get("label")))
        for row in _mappings_of(getattr(element, "rows", None)):
            for cell in row.values():
                found.update(_strings_of(cell))
    return found


def all_result_texts(user: User) -> dict[str, set[str]]:
    """3 個の書込欄の結果表示を、印ごとにまとめて読む（J-12 の「同時に 1 つ」の検査）。

    Args:
        user: `user` フィクスチャ。

    Returns:
        書込ボタンの印 → 結果表示の中の文字。
    """
    return {
        marker: result_texts(user, marker)
        for marker in WRITE_BUTTONS
        if exists(user, f"{marker}-result")
    }


def heading_order(user: User) -> list[str]:
    """節の見出しが画面に現れた順を返す。

    Args:
        user: `user` フィクスチャ。

    Returns:
        見出しの並び（描いた順）。
    """
    seen: list[str] = []
    for element in user.current_layout.descendants():
        text = getattr(element, "text", None)
        if isinstance(text, str) and text in SECTION_HEADINGS and text not in seen:
            seen.append(text)
    return seen


def prepare_form(user: User) -> None:
    """登録・編集の欄をすべて埋める（役割フォルダは 2 種だけ入れる）。

    Args:
        user: `user` フィクスチャ。
    """
    fill(user, "pj_key", PROJECT_KEY)
    fill(user, "pj_name", "見本プロジェクト")
    fill(user, "pj_input", "in")
    fill(user, "pj_outcome", "out")
    fill(user, "pj_role_programmer", "role-prog")
    fill(user, "pj_role_planner", "role-plan")


def prepare_release(user: User) -> None:
    """解除の欄を埋める。

    Args:
        user: `user` フィクスチャ。
    """
    fill(user, "pj_release_key", PROJECT_KEY)


async def press(user: User, api: FakeApi, marker: str, *, writes: int = 1) -> None:
    """書込ボタンを押し、要求が届くまで待つ。

    Args:
        user: `user` フィクスチャ。
        api: 偽の `call_api`。
        marker: 書込ボタンの印。
        writes: 押した後に届いているべき書込の本数。
    """
    user.find(marker=marker).click()
    await wait_until(lambda: len(api.writes()) == writes, what=f"{marker} の書込")


# --------------------------------------------------------------------------------------
# T046 (1)(4)(5): 開いたときの一覧・案内文・役割フォルダ欄（US1 AS-1・N-5・FR-007）
# --------------------------------------------------------------------------------------


async def test_the_page_opens_with_its_heading_and_target(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-5・FR-005: 見出しと接続先が出て、一覧とペルソナの読取が移行前の順に送られる。"""
    api = use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "プロジェクト" in texts
    assert f"接続先 app: {BASE_URL}" in texts
    assert heading_order(user) == list(SECTION_HEADINGS)
    assert api.paths("GET") == ["/settings/projects", "/settings/agents"]


async def test_the_listing_is_shown_as_a_table(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """ui-pages §4・US1 AS-12: 一覧は `projects-table` に、行のキーを列として出る。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    columns, rows = table_of(user, "projects-table")
    assert columns == list(PROJECTS[0]), "列が行のキーの並びと違う"
    assert [str(row.get("project_key")) for row in rows] == [PROJECT_KEY, "_program"]
    assert rows[0]["folders"] == json.dumps(PROJECTS[0]["folders"], ensure_ascii=False)


async def test_the_two_notices_about_the_folders_are_shown(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-007: フォルダの実体を作らない旨と、解除で消えない旨の 2 つの案内が出る。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "フォルダの実体は作りません（先に保管庫側で用意してください）" in texts
    assert "退避するだけで、保管庫のフォルダ実体は 1 つも消えません" in texts


@pytest.mark.parametrize(
    "personas",
    [("programmer", "reviewer", "planner"), ("tester", "writer")],
    ids=["default", "other"],
)
async def test_a_role_folder_field_appears_for_every_persona(
    user: User, monkeypatch: pytest.MonkeyPatch, personas: tuple[str, ...]
) -> None:
    """FR-007・data-model §5: 役割フォルダ欄が `GET /settings/agents` のペルソナごとに 1 つ出る。

    ⚠ **応答を 2 通りで駆動する** —— 1 通りだけだと、画面が応答を捨てて固定の一覧を使っても
    緑のままになる（変異で実測。`016` design-sync B-26）。欄の**集合が応答と一致する**ことと、
    応答に無いペルソナの欄が**出ない**ことの両方を見る。
    """
    rows = [{"persona_key": name, "project_key": None} for name in personas]
    use_api(monkeypatch, FakeApi(responses={("GET", "/settings/agents"): ok(rows)}))

    await open_page(user)

    for persona in personas:
        assert exists(user, f"pj_role_{persona}"), f"{persona} の役割フォルダ欄が無い"
        assert f"役割フォルダ（{persona}）" in texts_on(user)
    for absent in {"programmer", "reviewer", "planner", "tester", "writer"} - set(personas):
        assert not exists(user, f"pj_role_{absent}"), f"応答に無い {absent} の欄が出ている"


# --------------------------------------------------------------------------------------
# T046 (2)(3): 一覧が読めないとき（US1 AS-5a・N-5・D-7。旧 `test_settings_ui.py:347`）
# --------------------------------------------------------------------------------------


async def test_an_unreachable_listing_only_says_so(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-5・US1 AS-5a: `status=0` なら接続できない旨だけを出し、一覧と 3 つの欄を描かない。

    ⚠ 旧 `test_p5_a_connection_failure_only_shows_the_error` は誤りの文言だけを見ていた。
    一覧・登録・編集・解除が**描かれない**ことまで確かめる（test-mapping A 群の「強める点」）。
    """
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/settings/projects"): unreachable()}))

    await open_page(user)

    texts = texts_on(user)
    assert "プロジェクト" in texts
    assert f"接続先 app: {BASE_URL}" in texts
    assert "app に接続できません: <urlopen error>" in texts
    for marker in ("projects-table", "pj_key", "pj_release_key", *WRITE_BUTTONS):
        assert not exists(user, marker), f"接続できないのに {marker} を描いている"
    assert heading_order(user) == [], "接続できないのに節の見出しを描いている"
    assert api.paths("GET") == ["/settings/projects"], "以降の読取を送っている"


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (500, {"detail": "内部誤り"}, "500: {'detail': '内部誤り'}"),
        (404, {"detail": "見つかりません"}, "404: 見つかりません"),
    ],
    ids=["500", "404"],
)
async def test_a_failing_listing_stops_the_page(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: object,
    expected: str,
) -> None:
    """D-7・移行前どおり: 一覧が非 200 なら応答を出して以降を描かない。"""
    api = use_api(
        monkeypatch, FakeApi(responses={("GET", "/settings/projects"): failing(status, payload)})
    )

    await open_page(user)

    assert expected in texts_on(user)
    for marker in ("projects-table", "pj_key", "pj_release_key", *WRITE_BUTTONS):
        assert not exists(user, marker), f"非 200 なのに {marker} を描いている"
    assert heading_order(user) == [], "非 200 なのに節の見出しを描いている"
    assert api.paths("GET") == ["/settings/projects"], "以降の読取を送っている"


# --------------------------------------------------------------------------------------
# T046 (6)(7): 3 経路の書込（US1 AS-8・AS-9 の #14〜#16・N-10・U-K）
# --------------------------------------------------------------------------------------

WRITE_CASES: tuple[tuple[str, str, Callable[[User], None], object], ...] = (
    ("#14", "pj_create", prepare_form, ("POST", "/settings/projects", FORM_BODY)),
    ("#15", "pj_update", prepare_form, ("PUT", f"/settings/projects/{PROJECT_KEY}", FORM_BODY)),
    ("#16", "pj_release", prepare_release, ("DELETE", f"/settings/projects/{PROJECT_KEY}", None)),
)
"""§2-2 の #14〜#16（プロジェクトページの 3 経路）。`{…}` の値は英数だけにする。"""


@pytest.mark.parametrize(
    ("marker", "prepare", "expected"),
    [(marker, prepare, expected) for _num, marker, prepare, expected in WRITE_CASES],
    ids=[num for num, _marker, _prepare, _expected in WRITE_CASES],
)
async def test_each_write_sends_exactly_one_request_in_the_contract_shape(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    prepare: Callable[[User], None],
    expected: object,
) -> None:
    """N-10・US1 AS-8・AS-9: 3 経路が移行前と同じメソッド・経路・本文でちょうど 1 本送られる。

    本文はそのフォームの入力だけから組み（一覧の読取結果が混ざらない＝ U-K）、`role_dirs` には
    値を入れたペルソナだけが入る（#14・#15）。解除は本文を持たない（#16）。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare(user)

    await press(user, api, marker)
    await wait_until(
        lambda: bool(result_texts(user, marker)) and not is_disabled(user, marker),
        what="応答と取り直しの完了（ここまでに 2 本目が来ていないことを見る窓）",
    )

    assert api.writes() == [expected]


async def test_repos_fields_add_the_repos_section_to_the_body(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-74・D24: リポジトリ欄に値を入れると、本文に `repos` 節が載る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare_form(user)
    fill(user, "pj_remote_url", "https://gitlab.example.com/g/r.git")
    fill(user, "pj_local_path", "/home/demo/work/r")

    await press(user, api, "pj_create")
    await wait_until(lambda: bool(result_texts(user, "pj_create")), what="登録の応答")

    sent = api.writes()[0]
    assert sent[2] == {
        **FORM_BODY,
        "repos": {
            "remote_url": "https://gitlab.example.com/g/r.git",
            "local_path": "/home/demo/work/r",
        },
    }


async def test_repos_section_accepts_only_one_side(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-74: 片方だけの入力は片方だけの `repos` 節になる（両項目とも任意）。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare_form(user)
    fill(user, "pj_local_path", "/home/demo/work/r")

    await press(user, api, "pj_create")
    await wait_until(lambda: bool(result_texts(user, "pj_create")), what="登録の応答")

    sent = api.writes()[0]
    assert sent[2] == {**FORM_BODY, "repos": {"local_path": "/home/demo/work/r"}}


async def test_empty_repos_fields_send_no_repos_section(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-74・運用要件: リポジトリ欄が両方空なら本文に `repos` を載せない（未登録のまま）。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare_form(user)

    await press(user, api, "pj_create")
    await wait_until(lambda: bool(result_texts(user, "pj_create")), what="登録の応答")

    sent = api.writes()[0]
    assert sent[2] == FORM_BODY
    body = sent[2]
    assert isinstance(body, dict)
    assert "repos" not in body


# --------------------------------------------------------------------------------------
# D24 §6: キー欄の確定で、登録済みのプロジェクトの現在の値を欄へ読み込む（bd eyf）
# --------------------------------------------------------------------------------------

REPOS_KEY = "pjrepo"
"""リポジトリ対応付けを持つ登録済みのプロジェクトのキー。"""

REPOS_PROJECT: dict[str, object] = {
    "project_key": REPOS_KEY,
    "name": "リポジトリ付き",
    "folders": {
        "pj_input_dir": "rin",
        "role_dirs": {"planner": "role-plan2"},
        "outcome_dir": "rout",
    },
    "is_reserved": False,
    "repos": {"remote_url": "git@gitlab.example.com:g/r.git", "local_path": "/home/demo/work/r"},
}
"""`GET /settings/projects` の 1 行（`ProjectView`。`repos` を持つ）。"""

REPOS_FIELDS: dict[str, str] = {
    "pj_name": "リポジトリ付き",
    "pj_input": "rin",
    "pj_outcome": "rout",
    "pj_role_programmer": "",
    "pj_role_reviewer": "",
    "pj_role_planner": "role-plan2",
    "pj_remote_url": "git@gitlab.example.com:g/r.git",
    "pj_local_path": "/home/demo/work/r",
}
"""`REPOS_PROJECT` を読み込んだ後の欄の値（役割フォルダの無いペルソナは空）。"""

REPOS_BODY: dict[str, object] = {
    "project_key": REPOS_KEY,
    "name": "リポジトリ付き",
    "folders": {
        "pj_input_dir": "rin",
        "role_dirs": {"planner": "role-plan2"},
        "outcome_dir": "rout",
    },
    "repos": {"remote_url": "git@gitlab.example.com:g/r.git", "local_path": "/home/demo/work/r"},
}
"""読み込んだまま「編集」を押したときの本文（現在の値がそのまま戻る）。"""


def listing_api(*rows: dict[str, object]) -> FakeApi:
    """一覧に `rows` を返す偽の `call_api` を作る。

    Args:
        rows: `GET /settings/projects` の行。

    Returns:
        偽物。
    """
    return FakeApi(responses={("GET", "/settings/projects"): ok(list(rows))})


def confirm_key(user: User, key: str, *, event: str = "blur") -> None:
    """キー欄に `key` を入れて確定する（Enter かフォーカスの移動）。

    Args:
        user: `user` フィクスチャ。
        key: 入れるキー。
        event: 確定のイベント。
    """
    fill(user, "pj_key", key)
    user.find(marker="pj_key").trigger(event)


def form_values(user: User) -> dict[str, object]:
    """登録・編集の欄の値を、`REPOS_FIELDS` と同じ印ごとに読む。

    Args:
        user: `user` フィクスチャ。

    Returns:
        印 → 値。
    """
    return {marker: value_of(user, marker) for marker in REPOS_FIELDS}


@pytest.mark.parametrize("event", ["blur", "keydown.enter"])
async def test_confirming_a_registered_key_loads_its_current_values(
    user: User, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    """D24 §6: 登録済みのキーを確定すると、全欄に現在の値が入る。要求は増えない。"""
    api = use_api(monkeypatch, listing_api(PROJECTS[0], REPOS_PROJECT))
    await open_page(user)

    confirm_key(user, REPOS_KEY, event=event)

    assert form_values(user) == REPOS_FIELDS
    assert api.paths() == ["/settings/projects", "/settings/agents"], "確定で要求を送っている"


async def test_editing_a_loaded_project_keeps_its_repos(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D24 §6（D24-e の回帰）: 読み込んだまま「編集」を押すと、`repos` を含む現在の値が戻る。

    直す前は、編集フォームが空のまま送られ、`updateProject` の全置換で `repos` が消えていた。
    """
    api = use_api(monkeypatch, listing_api(PROJECTS[0], REPOS_PROJECT))
    await open_page(user)
    confirm_key(user, REPOS_KEY)

    await press(user, api, "pj_update")

    assert api.writes() == [("PUT", f"/settings/projects/{REPOS_KEY}", REPOS_BODY)]


async def test_values_typed_before_the_key_are_not_overwritten(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D24 §6・B-31: 確定より前に手で入れた欄は上書きせず、空の欄だけを読み込む。

    キーを最後に入れてそのままマウスで「編集」を押すと、キー欄の blur が click より先に届く。
    blur → click の順に起こして、手で入れた値が本文に残ることを見る。
    """
    api = use_api(monkeypatch, listing_api(REPOS_PROJECT))
    await open_page(user)
    fill(user, "pj_name", "手で入れた名前")
    fill(user, "pj_remote_url", "https://gitlab.example.com/g/other.git")

    confirm_key(user, REPOS_KEY)
    await press(user, api, "pj_update")

    sent = api.writes()[0][2]
    assert sent == {
        **REPOS_BODY,
        "name": "手で入れた名前",
        "repos": {
            "remote_url": "https://gitlab.example.com/g/other.git",
            "local_path": "/home/demo/work/r",
        },
    }


async def test_an_unregistered_key_leaves_the_fields_alone(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D24 §6: 一覧に無いキー（新規の登録）を確定しても、欄は変わらない。"""
    use_api(monkeypatch, listing_api(REPOS_PROJECT))
    await open_page(user)
    fill(user, "pj_name", "新しいプロジェクト")

    confirm_key(user, "newpj")

    assert value_of(user, "pj_name") == "新しいプロジェクト"
    assert value_of(user, "pj_input") == ""
    assert value_of(user, "pj_remote_url") == ""


async def test_switching_keys_reloads_only_untouched_fields(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D24 §6（J-10 と同じ規則）: 別の登録済みキーへ移ると、書き換えていない欄だけが移る。

    書き換えていない欄は移り先の値になる。移り先（`PROJECTS[0]`）は `repos` を持たないので、
    読み込んだままの 2 欄は空になる。
    """
    use_api(monkeypatch, listing_api(PROJECTS[0], REPOS_PROJECT))
    await open_page(user)
    confirm_key(user, REPOS_KEY)
    fill(user, "pj_outcome", "書き換えた成果物")

    confirm_key(user, PROJECT_KEY)

    assert form_values(user) == {
        "pj_name": "見本プロジェクト",
        "pj_input": "in",
        "pj_outcome": "書き換えた成果物",
        "pj_role_programmer": "role-prog",
        "pj_role_reviewer": "",
        "pj_role_planner": "",
        "pj_remote_url": "",
        "pj_local_path": "",
    }


async def test_reconfirming_the_same_key_after_a_reload_keeps_the_fields(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D24 §6・FR-010: 書込の後に一覧を取り直しても、同じキーの確定し直しでは欄を変えない。

    取り直した一覧で値が変わっていても読み込まない（入力欄の値は取り直しで変えない）。
    """
    changed = {**REPOS_PROJECT, "name": "外で変わった名前"}
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/settings/projects"): [ok([REPOS_PROJECT]), ok([changed])]}),
    )
    await open_page(user)
    confirm_key(user, REPOS_KEY)
    await press(user, api, "pj_update")
    await wait_until(
        lambda: api.paths("GET").count("/settings/projects") == 2, what="一覧の取り直し"
    )

    user.find(marker="pj_key").trigger("blur")

    assert value_of(user, "pj_name") == "リポジトリ付き"


async def test_clearing_the_loaded_repos_fields_unregisters_them(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D24 §6・クラスタ 02 #17: 読み込んだ 2 欄を空にして「編集」を押すと、`repos` を送らない。

    `updateProject` は全置換なので、これがリポジトリ対応付けの解除になる。
    """
    api = use_api(monkeypatch, listing_api(REPOS_PROJECT))
    await open_page(user)
    confirm_key(user, REPOS_KEY)
    fill(user, "pj_remote_url", "")
    fill(user, "pj_local_path", "")

    await press(user, api, "pj_update")

    body = {key: value for key, value in REPOS_BODY.items() if key != "repos"}
    assert api.writes() == [("PUT", f"/settings/projects/{REPOS_KEY}", body)]


BARRIER_WRITES: dict[str, tuple[str, str]] = {
    "pj_create": ("POST", "/settings/projects"),
    "pj_release": ("DELETE", f"/settings/projects/{PROJECT_KEY}"),
}
"""窓を作るために押すボタンと、それが送る `(メソッド, 経路)`。"""

EMPTY_FIELD_CASES: tuple[
    tuple[str, Callable[[User], None], str, str, Callable[[User], None]], ...
] = (
    ("pj_create", prepare_form, "pj_key", "pj_release", prepare_release),
    ("pj_update", prepare_form, "pj_key", "pj_release", prepare_release),
    ("pj_release", prepare_release, "pj_release_key", "pj_create", prepare_form),
)
"""（空にする必須欄を持つボタン, 埋める手順, 必須欄, 窓を作るボタン, その埋める手順）。"""


@pytest.mark.parametrize(
    ("marker", "prepare", "required", "barrier", "barrier_prepare"),
    list(EMPTY_FIELD_CASES),
    ids=[marker for marker, _p, _r, _b, _bp in EMPTY_FIELD_CASES],
)
async def test_an_empty_required_field_sends_nothing(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    prepare: Callable[[User], None],
    required: str,
    barrier: str,
    barrier_prepare: Callable[[User], None],
) -> None:
    """data-model §5: 必須欄（キー）が空なら要求を送らず、何も出さない。

    ⚠ 押した直後に数えるだけでは**空振り**になる（差し替えた `call_api` は `asyncio.to_thread`
    の先で走るので、押した直後はまだ 1 本も届いていない＝手元で実測）。**別のボタンの書込が
    終わるまで**待って窓を作り、その間に 1 本も増えていないことで「送らない」を表明する。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare(user)
    fill(user, required, "")
    barrier_prepare(user)
    opened = len(api.paths("GET"))

    user.find(marker=marker).click()
    await press(user, api, barrier)
    await wait_until(lambda: not is_disabled(user, barrier), what="窓を作る書込の完了")

    sent = [(method, path) for method, path, _body in api.writes()]
    assert sent == [BARRIER_WRITES[barrier]], f"{required} が空なのに要求を送った: {sent}"
    assert not result_texts(user, marker), f"{required} が空なのに結果を出した"
    assert api.paths("GET")[opened:] == ["/settings/projects"], "読取まで送っている"


# --------------------------------------------------------------------------------------
# T046 (8): 書込成功後の取り直し（US1 AS-12・R-1・N-11・data-model §5）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("marker", "prepare"),
    [
        ("pj_create", prepare_form),
        ("pj_update", prepare_form),
        ("pj_release", prepare_release),
    ],
    ids=["pj_create", "pj_update", "pj_release"],
)
async def test_a_successful_write_reloads_only_the_listing(
    user: User, monkeypatch: pytest.MonkeyPatch, marker: str, prepare: Callable[[User], None]
) -> None:
    """R-1・data-model §5: 2xx の直後に `GET /settings/projects` をちょうど 1 回取り直す。

    役割フォルダ欄を組み立てる `GET /settings/agents` は読取表示に数えないので送らない。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare(user)
    opened = len(api.paths("GET"))

    await press(user, api, marker)
    await wait_until(lambda: len(api.paths("GET")) > opened, what="取り直しの読取")
    await wait_until(
        lambda: bool(result_texts(user, marker)) and not is_disabled(user, marker),
        what="応答と取り直しの完了（W-1 の戻り）",
    )

    assert api.paths("GET")[opened:] == ["/settings/projects"], "取り直しが一覧 1 本でない"


async def test_the_reloaded_listing_replaces_the_table(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-1・US1 AS-12: 取り直した一覧が表に出て、入力欄の値と成功の文言は残る。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/settings/projects"): [ok(PROJECTS), ok(RELOADED_PROJECTS)]}),
    )
    await open_page(user)
    prepare_form(user)
    opened = len(api.paths("GET"))

    await press(user, api, "pj_create")
    await wait_until(lambda: len(api.paths("GET")) > opened, what="取り直しの読取")

    _, rows = table_of(user, "projects-table")
    assert [str(row.get("project_key")) for row in rows] == ["pj2"], "表が取り直されていない"
    assert value_of(user, "pj_key") == PROJECT_KEY, "取り直しで入力欄の値が変わった"
    assert "保存しました。次回 kickoff から効きます（世代 7）" in result_texts(user, "pj_create")


async def test_a_failing_write_does_not_reload(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """R-1: 422 では取り直さず、入力も残す。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("POST", "/settings/projects"): failing(422, {"problems": [PROBLEM]})}),
    )
    await open_page(user)
    prepare_form(user)
    opened = len(api.paths("GET"))

    await press(user, api, "pj_create")
    await wait_until(lambda: bool(result_texts(user, "pj_create")), what="違反の表示")

    assert len(api.paths("GET")) == opened, "422 なのに取り直した"
    assert value_of(user, "pj_key") == PROJECT_KEY


# --------------------------------------------------------------------------------------
# T046 (9): 書込中の同時実行（W-1・N-21・J-15）
# --------------------------------------------------------------------------------------


async def test_every_write_button_is_locked_while_waiting(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W-1・N-21・J-15: 書込の応答を待つ間はページの書込ボタン 3 個をすべて押せなくする。"""
    gate = threading.Event()
    api = use_api(monkeypatch, FakeApi(gate=gate, gate_methods=("POST", "PUT", "DELETE")))
    await open_page(user)
    prepare_form(user)
    prepare_release(user)
    assert not any(is_disabled(user, marker) for marker in WRITE_BUTTONS)

    try:
        user.find(marker="pj_create").click()
        await wait_until(lambda: is_disabled(user, "pj_create"), what="書込の開始")
        for marker in WRITE_BUTTONS:
            assert is_disabled(user, marker), f"{marker} が押せるままになっている"
    finally:
        gate.set()

    await wait_until(
        lambda: not any(is_disabled(user, marker) for marker in WRITE_BUTTONS),
        what="応答と取り直しの後に押せる状態へ戻ること",
    )
    assert len(api.writes()) == 1


# --------------------------------------------------------------------------------------
# T046 (10): 経路の段の符号化（D-5・N-19・J-9）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [("a b", "a%20b"), ("a?b", "a%3Fb"), ("a#b", "a%23b")],
    ids=["space", "question", "hash"],
)
async def test_user_values_are_percent_encoded_in_the_path(
    user: User, monkeypatch: pytest.MonkeyPatch, raw: str, encoded: str
) -> None:
    """D-5・N-19・J-9: キーは経路の段で符号化され、例外にならない（本文は生の値）。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare_form(user)
    fill(user, "pj_key", raw)

    await press(user, api, "pj_update")
    await wait_until(lambda: not is_disabled(user, "pj_release"), what="ボタンが戻ること")
    fill(user, "pj_release_key", raw)
    await press(user, api, "pj_release", writes=2)

    assert api.writes()[0][1] == f"/settings/projects/{encoded}"
    assert api.writes()[1][1] == f"/settings/projects/{encoded}"
    body = cast("dict[str, object]", api.writes()[0][2])
    assert body["project_key"] == raw, "本文の値まで符号化している"


async def test_a_plain_key_is_sent_unchanged(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-5・N-19: 英数・`-`・`_` だけのキーでは要求が移行前と同じ文字列になる。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare_form(user)
    fill(user, "pj_key", "pj-1_a")

    await press(user, api, "pj_update")
    await wait_until(lambda: not is_disabled(user, "pj_release"), what="ボタンが戻ること")
    fill(user, "pj_release_key", "pj-1_a")
    await press(user, api, "pj_release", writes=2)

    assert api.writes()[0][1] == "/settings/projects/pj-1_a"
    assert api.writes()[1][1] == "/settings/projects/pj-1_a"


# --------------------------------------------------------------------------------------
# T046 (11): 書込の結果表示（J-12・U-B〜U-E・D-1・N-3・N-4）
# --------------------------------------------------------------------------------------


async def test_the_result_is_shown_next_to_the_button_that_was_pressed(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-12: 結果は押したボタンの直後に出て、次の書込の結果が出たら前の欄の結果は消える。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    prepare_form(user)
    prepare_release(user)

    await press(user, api, "pj_create")
    await wait_until(lambda: bool(result_texts(user, "pj_create")), what="登録の結果")
    filled = {marker: texts for marker, texts in all_result_texts(user).items() if texts}
    assert list(filled) == ["pj_create"], f"同時に出ている結果が 1 つでない: {filled}"

    await wait_until(lambda: not is_disabled(user, "pj_release"), what="ボタンが戻ること")
    await press(user, api, "pj_release", writes=2)
    await wait_until(lambda: not result_texts(user, "pj_create"), what="前の結果が消えること")

    filled = {marker: texts for marker, texts in all_result_texts(user).items() if texts}
    assert list(filled) == ["pj_release"], f"同時に出ている結果が 1 つでない: {filled}"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (saved(), "保存しました。次回 kickoff から効きます（世代 7）"),
        (
            saved(effect="immediate"),
            "保存しました。権限ポリシーとコマンド定義は即時に効きます（世代 7）",
        ),
        (failing(404, {"detail": "見つかりません"}), "404: 見つかりません"),
        (failing(409, {"detail": "予約キー"}), "409: 予約キー"),
        (failing(500, {"detail": "内部誤り"}), "500: {'detail': '内部誤り'}"),
        (unreachable(), "app に接続できません: <urlopen error>"),
    ],
    ids=["next_kickoff", "immediate", "404", "409", "500", "unreachable"],
)
async def test_the_write_result_shows_each_outcome(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    response: settings_ui.ApiResponse,
    expected: str,
) -> None:
    """U-B〜U-E・D-1: 成功の文言・404 と 409 の `detail`・その他の生の本文・接続不能を出す。"""
    api = use_api(monkeypatch, FakeApi(responses={("POST", "/settings/projects"): response}))
    await open_page(user)
    prepare_form(user)

    await press(user, api, "pj_create")
    await wait_until(lambda: bool(result_texts(user, "pj_create")), what="結果の表示")

    assert expected in result_texts(user, "pj_create")
    assert not exists(user, "problems-table"), "違反の表が付いている"


async def test_a_422_lists_the_violations_with_a_count_and_the_notice(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-3・US1 AS-2: 422 は件数つきの `problems-table` と変更なしの旨をその欄に出す。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("POST", "/settings/projects"): failing(422, {"problems": [PROBLEM]})}),
    )
    await open_page(user)
    prepare_form(user)

    await press(user, api, "pj_create")
    await user.should_see(f"検査に落ちました（違反 1 件）。{settings_ui.UNCHANGED_NOTICE}")

    columns, rows = table_of(user, "problems-table")
    assert tuple(columns) == ("where", "kind", "reason")
    assert rows[0]["where"] == PROBLEM["where"]
    assert result_texts(user, "pj_create") >= {
        f"検査に落ちました（違反 1 件）。{settings_ui.UNCHANGED_NOTICE}"
    }


# --------------------------------------------------------------------------------------
# D26: 役割ごとの設定（閲覧のみ）
# --------------------------------------------------------------------------------------

ROLES_PATH = f"/settings/projects/{PROJECT_KEY}/roles"

ROLES: dict[str, object] = {
    "project_key": PROJECT_KEY,
    "roles": [
        {
            "persona_key": "programmer",
            "member_id": "pj.programmer",
            "casbin_subject": "member:pj.programmer",
            "role": "コーダー",
            "goal": "作る",
            "llm_profile": "openrouter",
            "stages": [
                {
                    "stage": "child",
                    "replaces_tools": True,
                    "tools": [{"tool": "submit_review", "equipped": True, "allowed": False}],
                }
            ],
            "tools": [
                {"tool": "read_issue", "equipped": True, "allowed": True},
                {"tool": "run_command", "equipped": True, "allowed": False},
                {"tool": "write_repo_file", "equipped": False, "allowed": True},
            ],
            "grants": [{"obj": "repo:files", "act": "write"}],
        }
    ],
    "stage_tools": [{"stage": "review_normal", "tools": ["read_issue", "submit_review"]}],
}
"""`GET /settings/projects/pj/roles` の偽の本文（装備のみ・許可のみ・両方を 1 件ずつ）。"""


def _select(user: User, marker: str, value: object) -> None:
    """選択の値を設定する（変更の処理も起きる）。"""
    setter = getattr(one(user, marker), "set_value", None)
    assert callable(setter), f"印 {marker!r} の部品は値を設定できない"
    cast("Callable[[object], None]", setter)(value)


async def _show_roles(user: User, api: FakeApi) -> None:
    """プロジェクトを選んで「表示」を押し、節が描かれるまで待つ。"""
    _select(user, "roles_project", PROJECT_KEY)
    user.find(marker="roles_show").click()
    await wait_until(lambda: ROLES_PATH in api.paths("GET"), what="役割の読取")
    await wait_until(lambda: bool(one(user, "roles_area").default_slot.children), what="描画")


async def test_the_roles_section_lists_the_projects_without_reading(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 選択肢は取得済みの一覧から作り、開いた時点では役割を読まない。"""
    api = use_api(monkeypatch, FakeApi())

    await open_page(user)

    assert getattr(one(user, "roles_project"), "options", None) == [PROJECT_KEY, "_program"]
    assert ROLES_PATH not in api.paths()
    assert settings_ui.ROLES_SOURCE_NOTICE in texts_on(user)


async def test_showing_roles_sends_exactly_one_read(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 「表示」で役割の読取を 1 本だけ送る（書込は送らない）。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", ROLES_PATH): ok(ROLES)}))
    await open_page(user)
    opened = len(api.calls)

    await _show_roles(user, api)

    assert api.calls[opened:] == [("GET", ROLES_PATH, None)]


async def test_the_tool_table_marks_both_kinds_of_mismatch(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 装備のみ（実行時に拒否される）と許可のみを見分け、件数の警告を出す。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", ROLES_PATH): ok(ROLES)}))
    await open_page(user)

    await _show_roles(user, api)

    labels, rows = table_of(user, "roles-tools-programmer")
    assert labels == ["Tool", "装備", "許可", "状態"]
    states = {str(row["Tool"]): str(row["状態"]) for row in rows}
    assert states == {
        "read_issue": "",
        "run_command": settings_ui.ROLE_EQUIPPED_ONLY,
        "write_repo_file": settings_ui.ROLE_ALLOWED_ONLY,
    }
    texts = texts_on(user)
    assert "装備しているのに許可されていない Tool が 1 件あります" in texts
    assert "programmer（コーダー）" in texts
    assert "既定の担当の段: child" in texts


async def test_grants_and_stage_tools_are_shown(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 2 段目の許可・段での装備の置き換え・担当未指定の段の道具を表で出す。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", ROLES_PATH): ok(ROLES)}))
    await open_page(user)

    await _show_roles(user, api)

    assert table_of(user, "roles-grants-programmer")[1] == [{"対象": "repo:files", "動作": "write"}]
    _labels, stage_rows = table_of(user, "roles-stage-programmer-child")
    assert [str(row["状態"]) for row in stage_rows] == [settings_ui.ROLE_EQUIPPED_ONLY]
    assert table_of(user, "roles-stage-tools")[1] == [
        {"段": "review_normal", "道具": "read_issue submit_review"}
    ]


async def test_a_project_without_members_says_so(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: メンバ 0 人なら、その旨だけを出す。"""
    empty: dict[str, object] = {"project_key": PROJECT_KEY, "roles": [], "stage_tools": []}
    api = use_api(monkeypatch, FakeApi(responses={("GET", ROLES_PATH): ok(empty)}))
    await open_page(user)

    await _show_roles(user, api)

    assert settings_ui.ROLES_NO_MEMBERS in texts_on(user)


async def test_a_failing_roles_read_is_shown_in_place(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 読めなければ節の位置に誤りを出す（404 は detail をそのまま）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", ROLES_PATH): failing(404, {"detail": "pj は未登録"})}),
    )
    await open_page(user)

    await _show_roles(user, api)

    assert "404: pj は未登録" in texts_on(user)


async def test_the_roles_choices_follow_the_reloaded_listing(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 書込の後に一覧を取り直したら、選択肢も揃える（役割そのものは読まない）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/settings/projects"): [ok(PROJECTS), ok(RELOADED_PROJECTS)]}),
    )
    await open_page(user)
    prepare_release(user)

    await press(user, api, "pj_release")
    await wait_until(
        lambda: getattr(one(user, "roles_project"), "options", None) == ["pj2"], what="選択肢"
    )

    assert not any(path.endswith("/roles") for path in api.paths())


# --------------------------------------------------------------------------------------
# T047: 文言の完全一致（FR-007・J-22・N-18 の SP 分）
# --------------------------------------------------------------------------------------


async def _drive_open(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`projects:open`: 既定の応答で開く（一覧・登録・編集・解除の見出しと欄）。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)


SCENARIOS: dict[str, Callable[[User, pytest.MonkeyPatch], Awaitable[None]]] = {
    "projects:open": _drive_open,
}
"""文言の一覧の `scenario` → その文言を画面に出す駆動関数（プロジェクトページの分）。

⚠ **場面は 1 つだけである** —— `page` が `projects` の文言 16 件は、すべて開いたときに出る
静的な文字（見出し・案内・ラベル・ボタン名）である。結果表示の雛形（「{コード}: {detail}」・
「保存しました。…」・「検査に落ちました（違反 N 件）。…」）は**両ページで共通の関数**が出す
ので、文言の一覧では `page` が `ai` の件が代表する（`016` T008 の仕分け）。それらの振る舞いは
本ファイルの `test_the_write_result_shows_each_outcome`・
`test_a_422_lists_the_violations_with_a_count_and_the_notice`・
`test_an_unreachable_listing_only_says_so`・`test_a_failing_listing_stops_the_page` が見ている。"""


def wording(page: str = "projects") -> list[dict[str, object]]:
    """文言の一覧のうち、当該ページの分を読む。

    Args:
        page: `ai` か `projects`。

    Returns:
        一覧の各件。
    """
    loaded: object = json.loads(WORDING_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    items = cast("list[object]", loaded)
    entries: list[dict[str, object]] = []
    for item in items:
        assert isinstance(item, dict)
        entry = cast("dict[str, object]", item)
        if entry["page"] == page:
            entries.append(entry)
    return entries


def matches(entry: dict[str, object], texts: Sequence[str]) -> bool:
    """1 件の文言が画面の文字に完全一致で現れるかを見る。

    Args:
        entry: 文言の一覧の 1 件。
        texts: 画面の部品の文字。

    Returns:
        現れていれば True。
    """
    pattern = entry["pattern"]
    if isinstance(pattern, str):
        return any(re.fullmatch(pattern, text) for text in texts)
    return entry["text"] in texts


def test_wording_entries_all_have_a_driving_scenario() -> None:
    """N-18・J-22: 一覧の `projects` の全件の `scenario` が駆動関数の表にある（空振りを防ぐ）。"""
    entries = wording()

    assert entries, "文言の一覧に `projects` の件が無い"
    missing = sorted({str(entry["scenario"]) for entry in entries} - set(SCENARIOS))

    assert not missing, f"駆動関数の無い scenario がある: {missing}"
    assert all(str(entry["scenario"]).startswith("projects:") for entry in entries)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
async def test_wording_appears_verbatim(
    user: User, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """N-18・FR-007・J-22: 一覧の各文言が部品の文字として完全一致で現れる。

    割り当ての漏れは `test_wording_entries_all_have_a_driving_scenario` が見る（一覧の全件が
    この表の scenario を指す）。書込の結果の文言は共通の層のもので `page` が `ai` なので、
    ここの場面に 1 件も割り当たらない scenario がありうる。
    """
    entries = [entry for entry in wording() if entry["scenario"] == scenario]
    await SCENARIOS[scenario](user, monkeypatch)

    texts = sorted(texts_on(user))
    missing = [str(entry["id"]) for entry in entries if not matches(entry, texts)]

    assert not missing, f"{scenario} で出ない文言がある: {missing}\n画面の文字: {texts}"
