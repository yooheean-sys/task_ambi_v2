"""AI 設定ページ（NiceGUI）の検証（US1 AS-1〜AS-12・ui-pages N-2〜N-11・N-18〜N-21）。

手段は NiceGUI の `user` フィクスチャ（ブラウザを立てない＝憲章 2.0.0 §8・FR-031）。

⚠ 規約 `contracts/ui-test-conventions.md` §2・§3 を守る。
1. `settings.ui` はモジュール先頭で束ね、差し替えは `monkeypatch.setattr` で行う
   （文字列経路の差し替えを使わない）。
2. ページ関数はテスト関数の中で定義し、描画関数を直接呼ぶ（apps/ui の殻を通さない）。
3. 固定の `sleep` を使わない（非同期の結果は `should_see` か `wait_until` の待ちで確かめる）。

⚠ **偽の応答のヘッダ名は小文字で組む**（FR-008・D-1）—— Starlette は小文字で送るので、画面と
同じ綴りで偽装すると、綴りがずれていても分岐が一致して緑になる。
"""

from __future__ import annotations

import asyncio
import http.client
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

PERSONAS = (
    "programmer",
    "reviewer",
    "planner",
    "tester",
    "writer",
    "analyst",
    "designer",
    "operator",
    "researcher",
)
"""ペルソナ 9 種（US1 AS-7 の「9 種すべてが出る」を見るための偽の応答）。"""

PROJECT_KEY = "pj"
"""メンバ・段テンプレート・権限の各節で確定するプロジェクトキー（英数だけ）。"""


def _global_agent_row(persona: str) -> dict[str, object]:
    """全体既定の行 1 件（`project_key` が null＝レベル「全体」）。

    Args:
        persona: ペルソナキー。

    Returns:
        1 行。
    """
    return {"persona_key": persona, "project_key": None, "role": f"{persona} の役割"}


AGENTS_GLOBAL: list[dict[str, object]] = [_global_agent_row(name) for name in PERSONAS]
"""`GET /settings/agents` の既定の本文（9 種）。"""

AGENTS_MEMBERS: list[dict[str, object]] = [
    {"persona_key": "programmer", "project_key": PROJECT_KEY, "role": "PJ の役割"},
]
"""`GET /settings/agents?project_key=pj` の既定の本文。"""

TASKS_GLOBAL: dict[str, object] = {"plain": {"description": "d", "expected_output": "e"}}
LLM_PROFILES: list[dict[str, object]] = [{"name": "openrouter", "model_id": "m"}]
LIMITS: dict[str, object] = {"max_requests_per_kickoff": 60, "max_cost_per_kickoff": 1.0}
COMMANDS: list[dict[str, object]] = [{"name": "demo", "argv": ["/bin/echo", "hello"]}]
HUMANS: dict[str, object] = {"human_actors": ["alice"]}
POLICIES: dict[str, object] = {"rows": [{"ptype": "p", "fields": ["alice", "data", "read"]}]}
HOOKS: dict[str, object] = {"hooks": [{"name": "h", "points": ["pre"]}]}
RULES: dict[str, object] = {"body": "共通ルールの本文"}
"""各節の読取の既定の本文。"""

SECTION_READS: dict[str, str] = {
    "agents": "/settings/agents",
    "tasks_global": "/settings/tasks/global",
    "profiles": "/settings/llm-profiles",
    "limits": "/settings/limits",
    "commands": "/settings/commands",
    "humans": "/settings/humans",
}
"""開いたときに送る 6 つの読取（節の並びの順）。"""

WRITE_BUTTONS = (
    "global_agent_save",
    "agents_save",
    "tasks_global_save",
    "tasks_project_save",
    "tasks_clear",
    "tasks_stage_global_save",
    "tasks_stage_project_save",
    "tasks_stage_project_remove",
    "profile_save",
    "limits_global_save",
    "limits_project_save",
    "command_save",
    "humans_save",
    "policy_save",
    "rules_save",
    "hooks_save",
)
"""AI 設定ページの書込ボタン 16 個（§2-2 の #1〜#13 ＋ D26 の段ごとの欄の 3 個）。"""

AGENT_ITEMS = (
    "role",
    "goal",
    "backstory",
    "llm",
    "tools",
    "skills",
    "mcps",
    "max_iter",
    "max_rpm",
    "max_execution_time",
)
"""許可 10 項目（FR-047。ペルソナ既定の上書き欄とメンバ欄の両方が持つ）。"""


# --------------------------------------------------------------------------------------
# 共通の補助（`test_trigger_ui_page.py` と同じ形。feature をまたいで共有しない）
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class Hold:
    """経路 1 つへの応答を塞ぐ栓（`FakeApi.hold` が作る。design-sync B-33 の順序を作る）。

    ⚠ 解放は `finally` で必ず行う（`release` の待ちにも 10 秒の上限がある）。

    Attributes:
        arrived: 塞いだ経路への要求が届いたら立つ。
        release: 立てると応答を返す（立った後に届く要求は塞がない）。
        returned: 塞いだ要求の応答を返し終えたら立つ（例外を投げたときも立つ）。
    """

    arrived: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    returned: threading.Event = field(default_factory=threading.Event)


@dataclass(slots=True)
class FakeApi:
    """要求を記録する偽の `call_api`（唯一の差し替え点＝ ui-pages §3）。

    Attributes:
        responses: `(method, path)` → 応答。値を並びにすると、呼ばれた順に消費して最後の値が残る。
            応答の代わりに例外を置くと、それを投げる（`call_api` が畳まない例外＝ B-33 (3)）。
        calls: 送られた `(method, path, body)` の並び。
        gate: 設定すると、応答を返す前にこの `Event` が立つまで待つ（同時実行の検査）。
        gate_methods: `gate` で塞ぐメソッド。
        holds: 経路 → 栓（`hold` で置く。経路ごとに塞ぐ）。
    """

    responses: dict[tuple[str, str], object] = field(default_factory=dict[tuple[str, str], object])
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])
    gate: threading.Event | None = None
    gate_methods: tuple[str, ...] = ()
    holds: dict[str, Hold] = field(default_factory=dict[str, Hold])

    def __call__(
        self, _base_url: str, method: str, path: str, body: object | None = None
    ) -> settings_ui.ApiResponse:
        """要求を記録して応答を返す（記録は塞ぐ前に行う＝送った順に並ぶ）。"""
        self.calls.append((method, path, body))
        if self.gate is not None and method in self.gate_methods:
            self.gate.wait(timeout=10.0)  # ⚠ 立たなくてもテストが固まらないように上限を置く
        hold = self.holds.get(path)
        if hold is None or hold.release.is_set():
            return self._respond(method, path)
        hold.arrived.set()
        hold.release.wait(timeout=10.0)  # ⚠ 同上
        try:
            return self._respond(method, path)
        finally:
            hold.returned.set()

    def hold(self, path: str) -> Hold:
        """経路 1 つへの応答を、解放するまで塞ぐ（要求を送る前に置く）。

        Args:
            path: 塞ぐ経路（クエリを含む）。

        Returns:
            栓。
        """
        hold = Hold()
        self.holds[path] = hold
        return hold

    def _respond(self, method: str, path: str) -> settings_ui.ApiResponse:
        """応答表から 1 件引いて返す（例外が置いてあれば投げる）。

        Args:
            method: HTTP メソッド。
            path: 経路（クエリを含む）。

        Returns:
            応答。

        Raises:
            Exception: 応答表に例外が置いてあるとき。
        """
        found = self._response_for(method, path)
        if isinstance(found, Exception):
            raise found
        return found

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

    def _response_for(self, method: str, path: str) -> settings_ui.ApiResponse | Exception:
        """応答表から 1 件引く（並びなら順に消費する）。

        Args:
            method: HTTP メソッド。
            path: 経路（クエリを含む）。

        Returns:
            応答か、投げる例外。表に無ければ既定を返す。
        """
        for key in ((method, path), (method, path.split("?")[0])):
            if key not in self.responses:
                continue
            found = self.responses[key]
            if isinstance(found, list):
                queued = cast("list[settings_ui.ApiResponse | Exception]", found)
                return queued.pop(0) if len(queued) > 1 else queued[0]
            return cast("settings_ui.ApiResponse | Exception", found)
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
    bare, _, query = path.partition("?")
    defaults: dict[str, object] = {
        "/settings/agents": AGENTS_MEMBERS if query else AGENTS_GLOBAL,
        "/settings/tasks/global": TASKS_GLOBAL,
        f"/settings/tasks/{PROJECT_KEY}": {"plain": {"description": "pj"}},
        "/settings/llm-profiles": LLM_PROFILES,
        "/settings/limits": LIMITS,
        "/settings/commands": COMMANDS,
        "/settings/humans": HUMANS,
        f"/settings/policies/{PROJECT_KEY}": POLICIES,
        f"/settings/hooks/{PROJECT_KEY}": HOOKS,
        f"/settings/rules/projects/{PROJECT_KEY}": RULES,
    }
    return ok(defaults.get(bare))


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
    """AI 設定ページを開く（殻を通さず描画関数を直接呼ぶ＝規約 §2）。

    Args:
        user: `user` フィクスチャ。
    """

    @nui.page("/")
    async def _page() -> None:  # pyright: ignore[reportUnusedFunction]  # `@nui.page` が経路に登録するので、名前は参照されない
        await settings_ui.render_ai_settings(BASE_URL)

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

    ⚠ `current_layout.descendants()` は**隠した部品まで返す** —— 確定前のフォームのように
    `set_visibility(False)` で隠した部品が「出ている」と数えられ、N-18 の完全一致の検査が
    弱くなる（2026-09-27 実測）。NiceGUI 自身の `ElementFilter(only_visible=True)` に委ねる。

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


def set_value(user: User, marker: str, value: object) -> None:
    """入力欄・選択の値を設定して変更の処理を起こす。

    Args:
        user: `user` フィクスチャ。
        marker: 印。
        value: 設定する値。
    """
    setter = getattr(one(user, marker), "set_value", None)
    assert callable(setter), f"印 {marker!r} の部品は値を設定できない"
    cast("Callable[[object], None]", setter)(value)


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


async def confirm(user: User, marker: str, text: str, *, event: str = "keydown.enter") -> None:
    """キー欄に入れて確定する（data-model §6）。

    Args:
        user: `user` フィクスチャ。
        marker: 印。
        text: 入れる文字（空文字なら消す）。
        event: 確定を起こす出来事。
    """
    fill(user, marker, text)
    user.find(marker=marker).trigger(event)


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
    """16 個の書込欄の結果表示を、印ごとにまとめて読む（J-12 の「同時に 1 つ」の検査）。

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


SECTION_HEADINGS = (
    "ペルソナ既定とメンバ",
    "段テンプレート",
    "LLM プロファイル",
    "上限",
    "コマンド定義",
    "権限・ルール・Hook・人間",
)
"""節の見出し（移行前の `st.header` の単位。並びは移行前のまま＝ data-model §5）。"""


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


# --------------------------------------------------------------------------------------
# T037: 一覧と最初の読取（US1 AS-1・AS-5b・N-2・N-5・N-20・D-2・D-7・J-13）
# --------------------------------------------------------------------------------------


async def test_the_page_opens_with_its_heading_and_target(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US1 AS-1: 見出しと接続先が出て、6 つの読取が節の並びの順に送られる。"""
    api = use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "AI 設定" in texts
    assert f"接続先 app: {BASE_URL}" in texts
    assert heading_order(user) == list(SECTION_HEADINGS)
    assert api.paths("GET") == list(SECTION_READS.values())


async def test_the_global_rows_are_tagged_with_their_level(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-2・U-A: 全体既定の表はレベル列を持ち、`project_key` が null の行は「全体」。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    columns, rows = table_of(user, "agents-table")
    assert columns[0] == "レベル"
    assert len(rows) == len(PERSONAS)
    assert {str(row.get("レベル")) for row in rows} == {"全体"}
    assert f"ペルソナ {len(PERSONAS)} 種（段との対応は表示しません）" in texts_on(user)


async def test_a_member_row_is_tagged_as_a_project_on_the_ai_page(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-2・FR-032 (a): **AI 設定ページで** `project_key` 非 null の行が「プロジェクト（pj）」。

    ⚠ 旧 `test_p2_a_project_row_is_tagged_as_a_project` はプロジェクトページを描いており、
    この行が出ない場面を見ていた（空振り）。メンバ欄をキーの確定で出してから確かめる。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await confirm(user, "agents_project", PROJECT_KEY)
    await wait_until(
        lambda: f"/settings/agents?project_key={PROJECT_KEY}" in api.paths("GET"),
        what="メンバの読取",
    )
    await user.should_see(marker="members-table")

    _, rows = table_of(user, "members-table")
    assert [str(row.get("レベル")) for row in rows] == [f"プロジェクト（{PROJECT_KEY}）"]


async def test_an_unreachable_first_read_stops_the_page(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-2・US1 AS-5b: 最初の `GET /settings/agents` が `status=0` なら以降の節を描かない。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/settings/agents"): unreachable()}))

    await open_page(user)

    texts = texts_on(user)
    assert "AI 設定" in texts
    assert f"接続先 app: {BASE_URL}" in texts
    assert "app に接続できません: <urlopen error>" in texts
    assert heading_order(user) == [], "接続できないのに節の見出しを描いている"
    assert api.paths("GET") == ["/settings/agents"], "以降の読取を送っている"


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (500, {"detail": "内部誤り"}, "500: {'detail': '内部誤り'}"),
        (404, {"detail": "見つかりません"}, "404: 見つかりません"),
    ],
)
async def test_a_failing_first_read_keeps_the_other_sections(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: object,
    expected: str,
) -> None:
    """J-13・D-7: 最初の読取が 5xx・404 なら、その節に応答を出し、ほかの節は描く。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/settings/agents"): failing(status, payload)}))

    await open_page(user)

    assert expected in texts_on(user)
    assert heading_order(user) == list(SECTION_HEADINGS)


@pytest.mark.parametrize(
    ("read_path", "marker"),
    [
        ("/settings/limits", "limits-read"),
        ("/settings/commands", "commands-read"),
        ("/settings/llm-profiles", "profiles-read"),
    ],
)
async def test_a_failing_section_read_shows_the_error_in_place(
    user: User, monkeypatch: pytest.MonkeyPatch, read_path: str, marker: str
) -> None:
    """D-7: ほかの節の読取が非 2xx なら、その位置に保存時と同じ形の誤りを出す。"""
    use_api(
        monkeypatch, FakeApi(responses={("GET", read_path): failing(500, {"detail": "壊れた"})})
    )

    await open_page(user)

    inside = {str(getattr(element, "text", "")) for element in one(user, marker).descendants()}
    assert "500: {'detail': '壊れた'}" in inside
    assert heading_order(user) == list(SECTION_HEADINGS)


async def test_a_failing_member_read_hides_the_member_form(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-13・移行前どおり: キーを確定したときのメンバの読取が失敗したら、メンバ欄を出さない。"""
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", f"/settings/agents?project_key={PROJECT_KEY}"): failing(
                    500, {"detail": "メンバが読めない"}
                )
            }
        ),
    )
    await open_page(user)

    await confirm(user, "agents_project", PROJECT_KEY)
    await wait_until(
        lambda: f"/settings/agents?project_key={PROJECT_KEY}" in api.paths("GET"),
        what="メンバの読取",
    )

    assert "500: {'detail': 'メンバが読めない'}" in texts_on(user)
    assert not exists(user, "members-table"), "読取が失敗したのにメンバの表が出ている"
    assert not exists(user, "agents_save"), "読取が失敗したのにメンバの保存ボタンが出ている"


# --------------------------------------------------------------------------------------
# T038: 書込の結果表示（US1 AS-2〜AS-4・N-3・N-4・J-12）
# --------------------------------------------------------------------------------------

PROBLEM: dict[str, object] = {
    "where": "/tmp/x/agents.yaml",  # noqa: S108  # 偽の応答の値（ファイルに触らない）
    "kind": "forward",
    "reason": "参照先が無い",
}
"""422 の違反 1 件（`where` に**絶対パス**を入れ、加工されずに出ることを見る＝ FR-032 (b)）。"""


async def _save_humans(user: User, api: FakeApi) -> None:
    """人間の行為者を保存する（必須欄が無いので最も単純な書込＝ data-model §5）。

    Args:
        user: `user` フィクスチャ。
        api: 偽の `call_api`。
    """
    user.find(marker="humans_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="人間の行為者の保存")


async def test_a_422_lists_the_violations_with_a_count_and_the_notice(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-3・US1 AS-2・FR-032 (b): 422 は件数つきの表と変更なしの旨を出し、`where` を
    そのまま出す。
    """
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={("PUT", "/settings/humans"): failing(422, {"problems": [PROBLEM, PROBLEM]})}
        ),
    )
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see(f"検査に落ちました（違反 2 件）。{settings_ui.UNCHANGED_NOTICE}")

    columns, rows = table_of(user, "problems-table")
    assert tuple(columns) == ("where", "kind", "reason")
    assert rows[0]["where"] == "/tmp/x/agents.yaml"  # noqa: S108  # 同上
    assert result_texts(user, "humans_save") >= {
        f"検査に落ちました（違反 2 件）。{settings_ui.UNCHANGED_NOTICE}"
    }


async def test_a_409_shows_the_single_detail_without_a_problems_table(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U-C: 409 は `detail` の単一文字列だけを出し、違反の表は付かない。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): failing(409, {"detail": "予約キー"})}),
    )
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see("409: 予約キー")

    assert "409: 予約キー" in result_texts(user, "humans_save")
    assert not exists(user, "problems-table")


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (404, {"detail": "見つかりません"}, "404: 見つかりません"),
        (500, {"detail": "内部誤り"}, "500: {'detail': '内部誤り'}"),
    ],
)
async def test_other_failures_show_the_status_and_the_body(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: object,
    expected: str,
) -> None:
    """U-C・U-E: 404 は `detail`、それ以外は `str(payload)` を添える。"""
    responses: dict[tuple[str, str], object] = {
        ("PUT", "/settings/humans"): failing(status, payload)
    }
    api = use_api(monkeypatch, FakeApi(responses=responses))
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see(expected)

    assert expected in result_texts(user, "humans_save")


async def test_an_unreachable_write_only_says_so(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U-E: 書込が接続できないときは、その旨を出すだけ（画面は落ちない）。"""
    api = use_api(monkeypatch, FakeApi(responses={("PUT", "/settings/humans"): unreachable()}))
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see("app に接続できません: <urlopen error>")

    assert "app に接続できません: <urlopen error>" in result_texts(user, "humans_save")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (saved(effect="next-kickoff"), "保存しました。次回 kickoff から効きます（世代 7）"),
        (
            saved(effect="immediate"),
            "保存しました。権限ポリシーとコマンド定義は即時に効きます（世代 7）",
        ),
        (saved(effect="unknown-effect"), "保存しました。（世代 7）"),
        (saved(effect=None, generation=None), "保存しました。"),
        (
            saved(effect="next-kickoff", lower=False),
            "保存しました。次回 kickoff から効きます（世代 7）",
        ),
    ],
    ids=["next_kickoff", "immediate", "unknown", "no_headers", "upper_case_headers"],
)
async def test_a_successful_save_shows_the_effect_and_the_generation(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    response: settings_ui.ApiResponse,
    expected: str,
) -> None:
    """N-4・D-1・US1 AS-4: 反映区分と世代を出す（ヘッダ名の大文字小文字によらない）。"""
    api = use_api(monkeypatch, FakeApi(responses={("PUT", "/settings/humans"): response}))
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see(expected)

    assert expected in result_texts(user, "humans_save")


async def test_only_one_result_is_shown_at_a_time(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-12: 結果は押したボタンの直後に出て、別の書込の結果が出たら前の欄の結果は
    消える。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see("保存しました。次回 kickoff から効きます（世代 7）")
    assert result_texts(user, "humans_save"), "押したボタンの直後に結果が出ていない"

    user.find(marker="limits_global_save").click()
    await wait_until(lambda: len(api.writes()) == 2, what="上限の保存")
    await wait_until(lambda: not result_texts(user, "humans_save"), what="前の結果が消えること")

    filled = {marker: texts for marker, texts in all_result_texts(user).items() if texts}
    assert list(filled) == ["limits_global_save"], f"同時に出ている結果が 1 つでない: {filled}"


async def test_a_dynamic_string_is_not_interpreted_as_markdown(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-19: app が返す動的な文字列は Markdown として解釈せず、生の文字で出す。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): failing(404, {"detail": "**強調**"})}),
    )
    await open_page(user)

    await _save_humans(user, api)
    await user.should_see("404: **強調**")

    assert "404: **強調**" in result_texts(user, "humans_save")


# --------------------------------------------------------------------------------------
# T039: LLM プロファイル節（US1 AS-6・AS-10・D-3）
# --------------------------------------------------------------------------------------


async def test_the_profile_section_only_asks_for_a_key_reference(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-093・SC-008: 鍵に関する入力欄は「鍵の参照名」だけで、案内が描画後の文字で出る。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "鍵の参照名" in texts
    assert settings_ui.SECRET_NOTICE in texts
    assert settings_ui.SECRET_NOTICE == "鍵の実値は入力しません（.env のキー名だけを指定します）"
    assert "`" not in settings_ui.SECRET_NOTICE, "Markdown の記号が残っている"
    assert not exists(user, "profile_key"), "鍵の実値を受ける欄がある"


async def test_the_profile_section_offers_two_presets_and_no_update_or_delete(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U-G・SC-029: 雛形は 2 件で、既存プロファイルの更新・削除の操作は無い。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    options = getattr(one(user, "profile_preset"), "options", None)
    assert options == [name for name, _url in settings_ui.LLM_PRESETS]
    assert len(settings_ui.LLM_PRESETS) == 2
    assert not exists(user, "profile_update")
    assert not exists(user, "profile_delete")


async def test_changing_the_preset_replaces_the_name_and_the_base_url(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-3・US1 AS-10・J-20 (3): 雛形を変えると名前と接続先が置き換わる（手入力も置き換わる）。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    presets = dict(settings_ui.LLM_PRESETS)

    fill(user, "profile_name", "手で書いた名前")
    set_value(user, "profile_preset", "local")
    await user.should_see("local")

    assert value_of(user, "profile_name") == "local"
    assert value_of(user, "profile_base") == presets["local"]


async def test_keeping_the_preset_keeps_the_typed_name(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-20 (3): 雛形を変えない間は入力を消さない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    fill(user, "profile_name", "手で書いた名前")
    user.find(marker="humans_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="別の節の保存")

    assert value_of(user, "profile_name") == "手で書いた名前"


async def test_the_context_window_defaults_to_32768(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US1 AS-6: 文脈長の既定は 32768（移行前の `value=32768`）。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    assert value_of(user, "profile_window") == 32768


@pytest.mark.parametrize(
    ("name", "expected_kind"),
    [("openrouter", "openrouter"), ("local", "openai_compatible")],
)
async def test_saving_a_profile_sends_the_contract_body(
    user: User, monkeypatch: pytest.MonkeyPatch, name: str, expected_kind: str
) -> None:
    """§2-2 の #6: `POST /settings/llm-profiles` の本文 6 項目
    （`provider_kind` は名前で決まる）。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    presets = dict(settings_ui.LLM_PRESETS)

    set_value(user, "profile_preset", name)
    fill(user, "profile_model", "m-1")
    fill(user, "profile_ref", "LLM_API_KEY_X")
    user.find(marker="profile_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="プロファイルの保存")

    assert api.writes() == [
        (
            "POST",
            "/settings/llm-profiles",
            {
                "name": name,
                "provider_kind": expected_kind,
                "base_url": presets[name],
                "model_id": "m-1",
                "context_window": 32768,
                "api_key_ref": "LLM_API_KEY_X",
            },
        )
    ]


# --------------------------------------------------------------------------------------
# T040: ペルソナ既定とメンバ（US1 AS-7・K-1・J-30）
# --------------------------------------------------------------------------------------


async def _confirm_member_key(user: User, api: FakeApi, key: str = PROJECT_KEY) -> None:
    """メンバ欄を出すためにプロジェクトキーを確定する。

    Args:
        user: `user` フィクスチャ。
        api: 偽の `call_api`。
        key: 確定するキー。
    """
    await confirm(user, "agents_project", key)
    await wait_until(
        lambda: f"/settings/agents?project_key={key}" in api.paths("GET"), what="メンバの読取"
    )
    await user.should_see(marker="agents_save")


async def test_the_member_form_shows_the_global_defaults_read_only(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US1 AS-7・U-I: 全体既定の表を読み取り専用で隣に置き、一覧型の案内を添える。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await _confirm_member_key(user, api)

    texts = texts_on(user)
    assert settings_ui.LIST_REPLACE_NOTICE in texts
    member_notice = (
        "全体の既定は上の表（読み取り専用）です。上書きしたい項目だけを入力してください。"
    )
    assert member_notice in texts
    assert "上書きを全部消すと全体に戻ります（FR-070）" in texts


async def test_all_nine_personas_are_listed_without_a_stage_mapping(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US1 AS-7・U-H: ペルソナ 9 種すべてが出て、段との対応は出さない。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    _, rows = table_of(user, "agents-table")
    assert {str(row.get("persona_key")) for row in rows} == set(PERSONAS)
    assert "段" not in {str(column) for column in table_of(user, "agents-table")[0]}


@pytest.mark.parametrize("prefix", ["global_agent", "agents"])
async def test_both_agent_forms_offer_all_ten_permitted_items(
    user: User, monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    """US1 AS-7・FR-047: ペルソナ既定の上書き欄とメンバ欄の**両方**が許可 10 項目を持つ。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    if prefix == "agents":
        await _confirm_member_key(user, api)

    missing = [item for item in AGENT_ITEMS if not exists(user, f"{prefix}_{item}")]

    assert missing == [], f"{prefix} に無い項目: {missing}"
    labels = {str(name) for name, _label in settings_ui.AGENT_TEXT_FIELDS}
    assert labels <= set(AGENT_ITEMS)


@pytest.mark.parametrize(
    ("prefix", "save", "expected_path"),
    [
        ("global_agent", "global_agent_save", "/settings/agents/global/programmer"),
        ("agents", "agents_save", f"/settings/agents/{PROJECT_KEY}/programmer"),
    ],
)
async def test_only_the_filled_items_are_sent(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
    save: str,
    expected_path: str,
) -> None:
    """US1 AS-7・U-I・§2-2 の #1・#2: 入力した項目だけが本文に載る（空欄と数値 0 は送らない）。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    if prefix == "agents":
        await _confirm_member_key(user, api)
    persona_marker = "global_agent_persona" if prefix == "global_agent" else "agents_persona"

    fill(user, persona_marker, "programmer")
    fill(user, f"{prefix}_role", "役割の値")
    fill(user, f"{prefix}_tools", " a  b ")
    set_value(user, f"{prefix}_max_iter", 3)
    user.find(marker=save).click()
    await wait_until(lambda: len(api.writes()) == 1, what="エージェントの保存")

    assert api.writes() == [
        ("PUT", expected_path, {"role": "役割の値", "tools": ["a", "b"], "max_iter": 3})
    ]


@pytest.mark.parametrize("prefix", ["global_agent", "agents"])
async def test_an_unreadable_mcps_is_never_sent(
    user: User, monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    """J-20・J-30: mcps の JSON が読めなければ、その結果表示に出して要求を送らない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    save = "global_agent_save" if prefix == "global_agent" else "agents_save"
    if prefix == "agents":
        await _confirm_member_key(user, api)
    persona_marker = "global_agent_persona" if prefix == "global_agent" else "agents_persona"
    reads = len(api.paths("GET"))

    fill(user, persona_marker, "programmer")
    fill(user, f"{prefix}_mcps", "{壊れた")
    user.find(marker=save).click()
    await wait_until(
        lambda: any(
            text.startswith("JSON として読めません: ") for text in result_texts(user, save)
        ),
        what="JSON が読めない旨",
    )

    assert api.writes() == [], "読めない JSON のまま要求を送った"
    assert len(api.paths("GET")) == reads, "読取まで送っている"


@pytest.mark.parametrize("prefix", ["global_agent", "agents"])
async def test_an_empty_mcps_is_omitted_from_the_body(
    user: User, monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    """J-30・移行前どおり: mcps の欄が空なら mcps を載せずに送る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    save = "global_agent_save" if prefix == "global_agent" else "agents_save"
    if prefix == "agents":
        await _confirm_member_key(user, api)
    persona_marker = "global_agent_persona" if prefix == "global_agent" else "agents_persona"

    fill(user, persona_marker, "programmer")
    fill(user, f"{prefix}_role", "役割の値")
    user.find(marker=save).click()
    await wait_until(lambda: len(api.writes()) == 1, what="エージェントの保存")

    body = api.writes()[0][2]
    assert isinstance(body, dict)
    assert settings_ui.MCPS_FIELD not in body


async def test_an_unconfirmed_key_only_confirms_instead_of_writing(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-1・J-16: キー欄の文字が確定済みのキーと違えば、送らずに確定だけを行う。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_member_key(user, api)
    fill(user, "agents_persona", "programmer")

    fill(user, "agents_project", "pj2")  # ⚠ 確定しない（Enter も blur も起こさない）
    user.find(marker="agents_save").click()
    await wait_until(
        lambda: "/settings/agents?project_key=pj2" in api.paths("GET"), what="確定の読取"
    )

    assert api.writes() == [], "確定していないキーで書込を送った"


async def test_changing_the_key_keeps_the_member_inputs(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-1・J-16: キーを変えてもメンバの 10 項目の入力は残る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_member_key(user, api)

    fill(user, "agents_role", "残る役割")
    await confirm(user, "agents_project", "pj2")
    await wait_until(
        lambda: "/settings/agents?project_key=pj2" in api.paths("GET"), what="確定の読取"
    )

    assert value_of(user, "agents_role") == "残る役割"


async def test_clearing_the_confirmed_key_hides_the_member_form(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-1・J-16・移行前どおり: 確定済みのキーを空にして確定すると、表と欄を隠す。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_member_key(user, api)

    await confirm(user, "agents_project", "")
    await wait_until(lambda: not exists(user, "members-table"), what="メンバの表が隠れること")

    assert not exists(user, "agents_save")
    assert not exists(user, "agents_role")


# --------------------------------------------------------------------------------------
# T041: 書込 13 経路（US1 AS-8・AS-9・N-10・§2-2 の #1〜#13）
# --------------------------------------------------------------------------------------

TASKS_BODY = '{"plain": {"description": "d", "expected_output": "e"}}'
"""段テンプレートの本文欄に入れる JSON（そのまま送られる）。"""

HOOKS_BODY = '{"hooks": [{"name": "h", "points": ["pre"]}]}'
"""Hook の宣言欄に入れる JSON（そのまま送られる）。"""


async def _confirm_tasks_key(user: User, api: FakeApi, key: str = PROJECT_KEY) -> None:
    """段テンプレート節のプロジェクトキーを確定する。

    Args:
        user: `user` フィクスチャ。
        api: 偽の `call_api`。
        key: 確定するキー。
    """
    await confirm(user, "tasks_project", key)
    await wait_until(
        lambda: f"/settings/tasks/{key}" in api.paths("GET"), what="段テンプレートの上書きの読取"
    )
    await user.should_see(marker="tasks_project_save")


async def _confirm_policies_key(user: User, api: FakeApi, key: str = PROJECT_KEY) -> None:
    """権限・ルール・Hook 節のプロジェクトキーを確定する。

    Args:
        user: `user` フィクスチャ。
        api: 偽の `call_api`。
        key: 確定するキー。
    """
    await confirm(user, "policies_project", key)
    await wait_until(lambda: f"/settings/policies/{key}" in api.paths("GET"), what="権限表の読取")
    await user.should_see(marker="policy_save")


async def _prepare_global_agent(user: User, api: FakeApi) -> None:
    """#1 の必須欄を埋める。"""
    _ = api
    fill(user, "global_agent_persona", "programmer")
    fill(user, "global_agent_role", "r")


async def _prepare_member(user: User, api: FakeApi) -> None:
    """#2 の必須欄を埋める（キーの確定を含む）。"""
    await _confirm_member_key(user, api)
    fill(user, "agents_persona", "programmer")
    fill(user, "agents_role", "r")


async def _prepare_tasks_global(user: User, api: FakeApi) -> None:
    """#3 の必須欄を埋める。"""
    _ = api
    fill(user, "tasks_global_body", TASKS_BODY)


async def _prepare_tasks_project(user: User, api: FakeApi) -> None:
    """#4 の必須欄を埋める（キーの確定を含む）。"""
    await _confirm_tasks_key(user, api)
    fill(user, "tasks_project_body", TASKS_BODY)


async def _prepare_tasks_clear(user: User, api: FakeApi) -> None:
    """#5 の必須欄（確定済みのキーだけ）を埋める。"""
    await _confirm_tasks_key(user, api)


async def _prepare_profile(user: User, api: FakeApi) -> None:
    """#6 の必須欄を埋める。"""
    _ = api
    set_value(user, "profile_preset", "local")
    fill(user, "profile_model", "m-1")
    fill(user, "profile_ref", "LLM_API_KEY_X")


async def _prepare_limits_global(user: User, api: FakeApi) -> None:
    """#7 の必須欄（既定値で埋まっている）を確かめる。"""
    _ = api, user


async def _prepare_limits_project(user: User, api: FakeApi) -> None:
    """#8 の必須欄を埋める（このキー欄は確定を要しない＝ data-model §5）。"""
    _ = api
    fill(user, "limits_project", PROJECT_KEY)


async def _prepare_command(user: User, api: FakeApi) -> None:
    """#9 の必須欄を埋める。"""
    _ = api
    fill(user, "command_name", "demo")
    fill(user, "command_argv", " /bin/echo  hello ")


async def _prepare_humans(user: User, api: FakeApi) -> None:
    """#10 の本文を埋める（必須欄は無い）。"""
    _ = api
    fill(user, "humans_body", " alice \n\n bob \n")


async def _prepare_policy(user: User, api: FakeApi) -> None:
    """#11 の必須欄を埋める（キーの確定を含む）。"""
    await _confirm_policies_key(user, api)
    fill(user, "policy_body", "p, alice, data, read\ng, alice, admin\n\n")


async def _prepare_rules(user: User, api: FakeApi) -> None:
    """#12 の必須欄を埋める（キーの確定を含む）。"""
    await _confirm_policies_key(user, api)
    fill(user, "rules", "書き換えた本文")


async def _prepare_hooks(user: User, api: FakeApi) -> None:
    """#13 の必須欄を埋める（キーの確定を含む）。"""
    await _confirm_policies_key(user, api)
    fill(user, "hooks_body", HOOKS_BODY)


WRITE_CASES: tuple[tuple[str, str, Callable[[User, FakeApi], Awaitable[None]], object], ...] = (
    (
        "#1",
        "global_agent_save",
        _prepare_global_agent,
        ("PUT", "/settings/agents/global/programmer", {"role": "r"}),
    ),
    (
        "#2",
        "agents_save",
        _prepare_member,
        ("PUT", f"/settings/agents/{PROJECT_KEY}/programmer", {"role": "r"}),
    ),
    (
        "#3",
        "tasks_global_save",
        _prepare_tasks_global,
        ("PUT", "/settings/tasks/global", json.loads(TASKS_BODY)),
    ),
    (
        "#4",
        "tasks_project_save",
        _prepare_tasks_project,
        ("PUT", f"/settings/tasks/{PROJECT_KEY}", json.loads(TASKS_BODY)),
    ),
    ("#5", "tasks_clear", _prepare_tasks_clear, ("DELETE", f"/settings/tasks/{PROJECT_KEY}", None)),
    (
        "#6",
        "profile_save",
        _prepare_profile,
        (
            "POST",
            "/settings/llm-profiles",
            {
                "name": "local",
                "provider_kind": "openai_compatible",
                "base_url": "http://127.0.0.1:8080/v1",
                "model_id": "m-1",
                "context_window": 32768,
                "api_key_ref": "LLM_API_KEY_X",
            },
        ),
    ),
    (
        "#7",
        "limits_global_save",
        _prepare_limits_global,
        ("PUT", "/settings/limits", {"max_requests_per_kickoff": 60, "max_cost_per_kickoff": 1.0}),
    ),
    (
        "#8",
        "limits_project_save",
        _prepare_limits_project,
        (
            "PUT",
            f"/settings/limits/{PROJECT_KEY}",
            {"max_requests_per_kickoff": 60, "max_cost_per_kickoff": 1.0},
        ),
    ),
    (
        "#9",
        "command_save",
        _prepare_command,
        ("PUT", "/settings/commands/demo", {"name": "demo", "argv": ["/bin/echo", "hello"]}),
    ),
    (
        "#10",
        "humans_save",
        _prepare_humans,
        ("PUT", "/settings/humans", {"human_actors": ["alice", "bob"]}),
    ),
    (
        "#11",
        "policy_save",
        _prepare_policy,
        (
            "PUT",
            f"/settings/policies/{PROJECT_KEY}",
            {
                "rows": [
                    {"ptype": "p", "fields": ["alice", "data", "read"]},
                    {"ptype": "g", "fields": ["alice", "admin"]},
                ]
            },
        ),
    ),
    (
        "#12",
        "rules_save",
        _prepare_rules,
        ("PUT", f"/settings/rules/projects/{PROJECT_KEY}", {"body": "書き換えた本文"}),
    ),
    (
        "#13",
        "hooks_save",
        _prepare_hooks,
        ("PUT", f"/settings/hooks/{PROJECT_KEY}", json.loads(HOOKS_BODY)),
    ),
)
"""§2-2 の #1〜#13（AI 設定ページの 13 経路）。`{…}` の値は英数だけにする。"""


@pytest.mark.parametrize(
    ("marker", "prepare", "expected"),
    [(marker, prepare, expected) for _num, marker, prepare, expected in WRITE_CASES],
    ids=[num for num, _marker, _prepare, _expected in WRITE_CASES],
)
async def test_each_write_sends_exactly_one_request_in_the_contract_shape(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    prepare: Callable[[User, FakeApi], Awaitable[None]],
    expected: object,
) -> None:
    """N-10・US1 AS-9・FR-032: 13 経路が移行前と同じメソッド・経路・本文でちょうど 1 本送られる。

    本文はそのフォームの入力だけから組む（一覧の読取結果が混ざらない＝ U-K）。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await prepare(user, api)

    user.find(marker=marker).click()
    await wait_until(lambda: len(api.writes()) == 1, what=f"{marker} の書込")

    assert api.writes() == [expected]


REQUIRED_FIELD_CASES: tuple[tuple[str, str, Callable[[User, FakeApi], Awaitable[None]]], ...] = (
    ("global_agent_save", "ペルソナ", _prepare_global_agent),
    ("agents_save", "ペルソナ", _prepare_member),
    ("tasks_global_save", "本文 JSON", _prepare_tasks_global),
    ("tasks_project_save", "上書き JSON", _prepare_tasks_project),
    ("profile_save", "名前", _prepare_profile),
    ("limits_global_save", "要求上限", _prepare_limits_global),
    ("limits_project_save", "プロジェクトキー", _prepare_limits_project),
    ("command_save", "登録名", _prepare_command),
)
"""必須欄を持つ書込ボタン（data-model §5 の表）。`humans_save` は必須欄が無いので入れない。"""

EMPTY_TARGETS: dict[str, tuple[str, object]] = {
    "global_agent_save": ("global_agent_persona", ""),
    "agents_save": ("agents_persona", ""),
    "tasks_global_save": ("tasks_global_body", ""),
    "tasks_project_save": ("tasks_project_body", ""),
    "profile_save": ("profile_name", ""),
    "limits_global_save": ("limits_req", None),
    "limits_project_save": ("limits_project", ""),
    "command_save": ("command_name", ""),
}
"""必須欄を空にする対象（`limits_req` は数値欄なので `None` で空にする＝ J-18）。"""


@pytest.mark.parametrize(
    ("marker", "label", "prepare"),
    list(REQUIRED_FIELD_CASES),
    ids=[marker for marker, _label, _prepare in REQUIRED_FIELD_CASES],
)
async def test_an_empty_required_field_sends_nothing(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    label: str,
    prepare: Callable[[User, FakeApi], Awaitable[None]],
) -> None:
    """data-model §5・US1 AS-9: 必須欄が空なら要求を送らず、何も出さない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await prepare(user, api)
    target, empty = EMPTY_TARGETS[marker]
    if isinstance(empty, str):
        fill(user, target, empty)
    else:
        set_value(user, target, empty)
    reads = len(api.paths("GET"))

    user.find(marker=marker).click()
    await wait_until(lambda: True, what="押した後の処理")

    assert api.writes() == [], f"{label} が空なのに要求を送った"
    assert not result_texts(user, marker), f"{label} が空なのに結果を出した"
    assert len(api.paths("GET")) == reads, "読取まで送っている"


async def test_the_key_dependent_writes_do_not_exist_before_the_key_is_confirmed(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data-model §5: 確定済みのキーを要する書込の欄は、確定するまで現れない。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    for marker in (
        "agents_save",
        "tasks_project_save",
        "tasks_clear",
        "tasks_stage_project_save",
        "tasks_stage_project_remove",
        "policy_save",
        "rules_save",
        "hooks_save",
    ):
        assert not exists(user, marker), f"{marker} が確定前から出ている"


# --------------------------------------------------------------------------------------
# T042: 書込成功後の取り直し（US1 AS-12・FR-010・R-1・N-11）
# --------------------------------------------------------------------------------------


async def test_a_successful_write_reloads_only_its_own_section(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-1・US1 AS-12: 上限の保存が成功したら `GET /settings/limits` だけを 1 本取り直す。

    ほかの節の読取は送らず、入力中の値（コマンド定義の登録名・上限の入力欄）も変えない。
    """
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/settings/limits"): [
                    ok(LIMITS),
                    ok({"max_requests_per_kickoff": 99, "max_cost_per_kickoff": 2.5}),
                ]
            }
        ),
    )
    await open_page(user)
    fill(user, "command_name", "入力途中")
    opened = len(api.paths("GET"))

    user.find(marker="limits_global_save").click()
    await wait_until(lambda: len(api.paths("GET")) > opened, what="取り直しの読取")
    await user.should_see("保存しました。次回 kickoff から効きます（世代 7）")

    assert api.paths("GET")[opened:] == ["/settings/limits"], "取り直しが 1 本でない"
    assert "99" in "".join(texts_on(user)), "取り直した値が表示に出ていない"
    assert value_of(user, "command_name") == "入力途中", "ほかの節の入力が変わった"
    assert value_of(user, "limits_req") == 60, "同じ節の入力欄が変わった"
    assert "保存しました。次回 kickoff から効きます（世代 7）" in result_texts(
        user, "limits_global_save"
    )


async def test_a_failing_write_does_not_reload(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """R-1: 非 2xx では取り直さず、入力も残す。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/limits"): failing(422, {"problems": [PROBLEM]})}),
    )
    await open_page(user)
    opened = len(api.paths("GET"))

    user.find(marker="limits_global_save").click()
    await user.should_see(f"検査に落ちました（違反 1 件）。{settings_ui.UNCHANGED_NOTICE}")

    assert len(api.paths("GET")) == opened, "422 なのに取り直した"
    assert value_of(user, "limits_req") == 60


async def test_a_failing_reload_keeps_the_success_message(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-13・R-1: 取り直しが失敗したら節の位置に誤りを出し、成功の文言と入力欄は残す。"""
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/settings/limits"): [ok(LIMITS), failing(500, {"detail": "壊れた"})]
            }
        ),
    )
    await open_page(user)
    opened = len(api.paths("GET"))

    user.find(marker="limits_global_save").click()
    await wait_until(lambda: len(api.paths("GET")) > opened, what="取り直しの読取")
    await user.should_see("500: {'detail': '壊れた'}")

    assert "保存しました。次回 kickoff から効きます（世代 7）" in result_texts(
        user, "limits_global_save"
    )
    assert value_of(user, "limits_req") == 60


SECTION_RELOADS: tuple[
    tuple[str, Callable[[User, FakeApi], Awaitable[None]], tuple[str, ...]], ...
] = (
    ("global_agent_save", _prepare_global_agent, ("/settings/agents",)),
    ("tasks_global_save", _prepare_tasks_global, ("/settings/tasks/global",)),
    ("profile_save", _prepare_profile, ("/settings/llm-profiles",)),
    ("limits_global_save", _prepare_limits_global, ("/settings/limits",)),
    ("command_save", _prepare_command, ("/settings/commands",)),
    ("humans_save", _prepare_humans, ("/settings/humans",)),
)
"""6 節それぞれの「節の書込 → 取り直される読取」（data-model §5 の表）。"""


@pytest.mark.parametrize(
    ("marker", "prepare", "expected"),
    list(SECTION_RELOADS),
    ids=[marker for marker, _prepare, _expected in SECTION_RELOADS],
)
async def test_each_section_reloads_only_its_own_reads(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    prepare: Callable[[User, FakeApi], Awaitable[None]],
    expected: tuple[str, ...],
) -> None:
    """R-1・data-model §5: 6 節それぞれで、取り直す読取がその節の読取表示だけ。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await prepare(user, api)
    opened = len(api.paths("GET"))

    user.find(marker=marker).click()
    await wait_until(lambda: len(api.paths("GET")) > opened, what="取り直しの読取")
    await wait_until(lambda: bool(result_texts(user, marker)), what="成功の文言")

    assert tuple(api.paths("GET")[opened:]) == expected


async def test_the_policies_section_never_reloads_the_common_rules(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data-model §5: 共通ルールの読取は表示ではないので、取り直しでは送らない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _prepare_policy(user, api)
    opened = len(api.paths("GET"))

    user.find(marker="policy_save").click()
    await wait_until(lambda: len(api.paths("GET")) > opened, what="取り直しの読取")
    await wait_until(lambda: bool(result_texts(user, "policy_save")), what="成功の文言")

    reloaded = api.paths("GET")[opened:]
    assert f"/settings/rules/projects/{PROJECT_KEY}" not in reloaded
    assert set(reloaded) == {
        "/settings/humans",
        f"/settings/policies/{PROJECT_KEY}",
        f"/settings/hooks/{PROJECT_KEY}",
    }


# --------------------------------------------------------------------------------------
# T043: 共通ルールの読み込み直し（US1 AS-11・D-4・J-14）
# --------------------------------------------------------------------------------------


async def test_confirming_the_key_loads_the_common_rules_body(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-4・US1 AS-11: キーを確定すると共通ルールの本文がそのキーの現在値になる。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await _confirm_policies_key(user, api)

    assert value_of(user, "rules") == "共通ルールの本文"


async def test_changing_the_key_replaces_the_common_rules_body(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-4・US1 AS-11: キーを変えて確定すると本文が新しいキーの値に置き換わり、保存もそこへ送る。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/settings/rules/projects/pj2"): ok({"body": "pj2 の本文"})}),
    )
    await open_page(user)
    await _confirm_policies_key(user, api)

    await confirm(user, "policies_project", "pj2")
    await wait_until(
        lambda: "/settings/rules/projects/pj2" in api.paths("GET"), what="共通ルールの読込"
    )
    await wait_until(lambda: value_of(user, "rules") == "pj2 の本文", what="本文の置き換え")
    user.find(marker="rules_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="共通ルールの保存")

    assert api.writes() == [("PUT", "/settings/rules/projects/pj2", {"body": "pj2 の本文"})]


async def test_a_404_common_rules_empties_the_body_and_says_so(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-14: 読込が 404 なら本文を空にして「404: {detail}」を出す。"""
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                (
                    "GET",
                    f"/settings/rules/projects/{PROJECT_KEY}",
                ): failing(404, {"detail": "まだ無い"})
            }
        ),
    )
    await open_page(user)

    await _confirm_policies_key(user, api)

    assert value_of(user, "rules") == ""
    assert "404: まだ無い" in texts_on(user)
    assert exists(user, "rules_save"), "404 では本文欄と保存ボタンを出す（J-14）"


@pytest.mark.parametrize(
    "response", [unreachable(), failing(500, {"detail": "壊れた"})], ids=["status_zero", "5xx"]
)
async def test_an_unreadable_common_rules_hides_the_body_and_the_save(
    user: User, monkeypatch: pytest.MonkeyPatch, response: settings_ui.ApiResponse
) -> None:
    """J-14: `status=0`・5xx なら誤りを出し、本文欄と保存ボタンを出さない。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", f"/settings/rules/projects/{PROJECT_KEY}"): response}),
    )
    await open_page(user)

    await _confirm_policies_key(user, api)

    assert not exists(user, "rules"), "読めないのに本文欄が出ている"
    assert not exists(user, "rules_save"), "読めないのに保存ボタンが出ている"


async def test_confirming_the_same_key_again_does_not_reload_the_common_rules(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-4・J-14（B-32 の裁定で限定）: 前回の読込が成功なら、同じキーで確定し直しても読み直さない。

    読み直すのは、前回の確定に伴う読込が `status=0`・5xx だったときだけである
    （`test_the_same_key_retries_after_a_transient_failure`）。
    """
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")
    before = len(api.calls)

    await confirm(user, "policies_project", PROJECT_KEY)
    settle = await _settle_with_another_key(user, "policies_project")

    assert [path for _verb, path, _body in api.calls[before:]] == settle


async def test_changing_the_key_keeps_the_policy_and_hooks_inputs(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-1: キーを変えても権限表の行と Hook の宣言 JSON の入力は残る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_policies_key(user, api)
    fill(user, "policy_body", "p, alice, data, read")
    fill(user, "hooks_body", HOOKS_BODY)

    await confirm(user, "policies_project", "pj2")
    await wait_until(lambda: "/settings/policies/pj2" in api.paths("GET"), what="新しいキーの読取")

    assert value_of(user, "policy_body") == "p, alice, data, read"
    assert value_of(user, "hooks_body") == HOOKS_BODY


# --------------------------------------------------------------------------------------
# T097: マウスで押す順序（キー欄の blur → 書込ボタンの click）での K-1（J-16・D-4・B-31）
# --------------------------------------------------------------------------------------

NEXT_KEY = "pj2"
"""確定済みの `pj` から打ち変えるキー（英数だけ）。"""

NEXT_KEY_READS: dict[tuple[str, str], object] = {
    ("GET", f"/settings/agents?project_key={NEXT_KEY}"): ok(
        [{"persona_key": "programmer", "project_key": NEXT_KEY, "role": "pj2 の役割"}]
    ),
    ("GET", f"/settings/tasks/{NEXT_KEY}"): ok({"plain": {"description": "pj2 の上書き"}}),
    ("GET", f"/settings/rules/projects/{NEXT_KEY}"): ok({"body": "pj2 の本文"}),
}
"""新しいキーの依存する読取の応答（確定が終わったことを画面で見分けられる値にする）。"""

KEYED_WRITES: tuple[tuple[str, str, Callable[[User, FakeApi], Awaitable[None]]], ...] = (
    ("agents_save", "agents_project", _prepare_member),
    ("tasks_project_save", "tasks_project", _prepare_tasks_project),
    ("tasks_clear", "tasks_project", _prepare_tasks_clear),
    ("policy_save", "policies_project", _prepare_policy),
    ("rules_save", "policies_project", _prepare_rules),
    ("hooks_save", "policies_project", _prepare_hooks),
)
"""キーに依存する 6 つの書込（書込ボタンの印・キー欄の印・`pj` で確定して必須欄を埋める準備）。"""

CONFIRM_READS: dict[str, tuple[str, ...]] = {
    "agents_project": (f"/settings/agents?project_key={NEXT_KEY}",),
    "tasks_project": (f"/settings/tasks/{NEXT_KEY}",),
    "policies_project": (
        f"/settings/policies/{NEXT_KEY}",
        f"/settings/hooks/{NEXT_KEY}",
        f"/settings/rules/projects/{NEXT_KEY}",
    ),
}
"""キー欄ごとの、新しいキーの確定で送る依存する読取（送る順）。"""


def _confirmed_next_key(user: User, key_marker: str) -> bool:
    """新しいキーの確定（依存する読取とその表示）が終わったかを見る。

    Args:
        user: `user` フィクスチャ。
        key_marker: キー欄の印。

    Returns:
        終わっていれば True。
    """
    if key_marker == "agents_project":
        return exists(user, "members-table") and any(
            row.get("role") == "pj2 の役割" for row in table_of(user, "members-table")[1]
        )
    if key_marker == "tasks_project":
        return any("pj2 の上書き" in text for text in texts_on(user))
    return value_of(user, "rules") == "pj2 の本文"


@pytest.mark.parametrize(
    ("marker", "key_marker", "prepare"),
    list(KEYED_WRITES),
    ids=[marker for marker, _key_marker, _prepare in KEYED_WRITES],
)
async def test_a_click_right_after_the_key_blurs_only_confirms(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    key_marker: str,
    prepare: Callable[[User, FakeApi], Awaitable[None]],
) -> None:
    """K-1・J-16: マウスで押す順序（blur → click）でも、送らずに新しいキーの確定だけを行う。

    ⚠ `user` の `click()` は blur を起こさない。実ブラウザでは、キー欄に文字を残したまま
    書込ボタンを押すと、押し下げでフォーカスが移り、キー欄の blur が click より**先に**届く。
    NiceGUI は 2 つの非同期の処理を直列化せずに走らせるので、その順序を明示して起こす。
    """
    api = use_api(monkeypatch, FakeApi(responses=dict(NEXT_KEY_READS)))
    await open_page(user)
    await prepare(user, api)
    before = len(api.calls)

    fill(user, key_marker, NEXT_KEY)
    user.find(marker=key_marker).trigger("blur")
    user.find(marker=marker).click()
    await wait_until(lambda: _confirmed_next_key(user, key_marker), what="新しいキーの確定")

    assert api.writes() == [], "確定の途中で書込を送った"
    assert [path for _verb, path, _body in api.calls[before:]] == list(CONFIRM_READS[key_marker])


async def test_the_previous_common_rules_never_reach_the_next_key(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-4・K-1: 読取が終わる前に click が届いても、前のキーの本文を新しいキーへ送らない。"""
    gate = threading.Event()
    api = use_api(monkeypatch, FakeApi(responses=dict(NEXT_KEY_READS)))
    await open_page(user)
    await _confirm_policies_key(user, api)
    assert value_of(user, "rules") == "共通ルールの本文"

    api.gate = gate  # ⚠ ここから先の読取を塞ぐ（`FakeApi.gate` の待ちは 10 秒の上限つき）
    api.gate_methods = ("GET",)
    try:
        fill(user, "policies_project", NEXT_KEY)
        user.find(marker="policies_project").trigger("blur")
        user.find(marker="rules_save").click()
        await wait_until(
            lambda: f"/settings/policies/{NEXT_KEY}" in api.paths("GET"),
            what="新しいキーの読取が塞がれていること",
        )
        assert value_of(user, "rules") == "共通ルールの本文", "読取が終わる前の順序を作れていない"
    finally:
        gate.set()
    await wait_until(lambda: value_of(user, "rules") == "pj2 の本文", what="新しいキーの本文の読込")

    assert (
        "PUT",
        f"/settings/rules/projects/{NEXT_KEY}",
        {"body": "共通ルールの本文"},
    ) not in api.writes(), "前のキーの本文を新しいキーへ送った（D-4 の再発）"
    assert api.writes() == []


# --------------------------------------------------------------------------------------
# T100: 同じキーでの確定し直し（design-sync B-32 の裁定・FR-009a・J-14・D-4・K-1）
# --------------------------------------------------------------------------------------

KEY_FIELDS = ("agents_project", "tasks_project", "policies_project")
"""確定してから使うキー欄 3 つ（B-32 の裁定は 3 つとも同じ規則）。"""

KEY_READS: dict[str, tuple[str, ...]] = {
    "agents_project": (f"/settings/agents?project_key={PROJECT_KEY}",),
    "tasks_project": (f"/settings/tasks/{PROJECT_KEY}",),
    "policies_project": (
        f"/settings/policies/{PROJECT_KEY}",
        f"/settings/hooks/{PROJECT_KEY}",
        f"/settings/rules/projects/{PROJECT_KEY}",
    ),
}
"""キー欄ごとの、`pj` の確定に伴う依存する読取（送る順）。"""

KEPT_INPUTS: dict[str, tuple[str, str]] = {
    "agents_project": ("agents_role", "書きかけの役割"),
    "tasks_project": ("tasks_project_body", "書きかけの上書き"),
    "policies_project": ("rules", "書きかけの本文"),
}
"""キー欄ごとの、同じキーでの確定し直しで消えてはならない入力（印と値）。"""

RETRY_CASES: tuple[tuple[str, str, str], ...] = (
    ("members", "agents_project", f"/settings/agents?project_key={PROJECT_KEY}"),
    ("tasks", "tasks_project", f"/settings/tasks/{PROJECT_KEY}"),
    ("rules", "policies_project", f"/settings/rules/projects/{PROJECT_KEY}"),
    ("policies", "policies_project", f"/settings/policies/{PROJECT_KEY}"),
    ("hooks", "policies_project", f"/settings/hooks/{PROJECT_KEY}"),
)
"""失敗させる依存する読取（名前・キー欄の印・経路）。キー欄 3 つの読取をすべて含む。"""

TRANSIENT_FAILURES: tuple[tuple[str, settings_ui.ApiResponse], ...] = (
    ("status_zero", unreachable()),
    ("5xx", failing(500, {"detail": "壊れた"})),
)
"""やり直せば結果が変わりうる失敗（B-32 の裁定 (2)。404・409 などは含めない＝裁定 (3)）。"""


async def _wait_confirmed(user: User, key_marker: str) -> None:
    """キー欄の確定に伴う読取が**最後まで**終わるのを待つ。

    確定の処理は、最後の読取の応答を画面に出した後は `await` を挟まずに終わる
    （`refreshable.refresh()` は同期の描画関数なら中断しない）。そのため、最後の読取の結果が
    画面に出たことを、確定の処理の完了の目印にできる。

    ⚠ 権限の節は、権限表・Hook の後に共通ルールを読む。`_confirm_policies_key` は権限表の
    読取と保存ボタンの表示までしか待たないので、ここでは共通ルールの本文が入るまで待つ
    （待たずに本文欄へ書くと、遅れて届いた読込に上書きされる）。

    Args:
        user: `user` フィクスチャ。
        key_marker: キー欄の印。
    """
    if key_marker == "agents_project":
        await wait_until(lambda: exists(user, "agents_save"), what="メンバ欄の表示")
    elif key_marker == "tasks_project":
        await wait_until(lambda: exists(user, "tasks_project_save"), what="上書き欄の表示")
    else:
        await wait_until(
            lambda: exists(user, "rules") and value_of(user, "rules") == RULES["body"],
            what="共通ルールの本文の読込",
        )


async def _confirm_key_fully(user: User, key_marker: str) -> None:
    """キー欄を `pj` で確定し（Enter）、確定に伴う読取が最後まで終わるのを待つ。

    Args:
        user: `user` フィクスチャ。
        key_marker: キー欄の印。
    """
    await confirm(user, key_marker, PROJECT_KEY)
    await _wait_confirmed(user, key_marker)


async def _settle_with_another_key(user: User, key_marker: str) -> list[str]:
    """**別の**キー欄を `pj` で確定し、その読取が終わるまで待つ（順序の目印）。

    先に受けた処理が読取を送るなら、その要求はこの目印の読取より先に出る。処理は受けた順に
    走り出し、読取は同じスレッドプールへ受けた順に積まれるからである。「送らない」ことを、
    固定の待ちに頼らずに確かめるために使う。

    Args:
        user: `user` フィクスチャ。
        key_marker: 確かめているキー欄の印（目印にはこれと別の欄を使う）。

    Returns:
        目印の確定で送られる読取の経路。
    """
    other = "tasks_project" if key_marker == "agents_project" else "agents_project"
    await _confirm_key_fully(user, other)
    return list(KEY_READS[other])


async def _settle_after(user: User, hold: Hold, key_marker: str) -> list[str]:
    """塞いだ応答を返し終えた後、それを待っていた確定の処理が終わるまで待つ（順序の目印）。

    応答を返し終えた（`returned`）後で、別のキー欄の確定を最後まで行う。塞いだ要求の続きは、
    目印の読取より先にループへ戻る（読取の完了は、終わった順にループへ積まれる）。そのため、
    最新でない確定が画面に何かを反映するなら、目印の表示より前に済んでいる。

    ⚠ 解放（`hold.release.set()`）は呼ぶ側が `finally` で済ませておく。

    Args:
        user: `user` フィクスチャ。
        hold: 解放した栓。
        key_marker: 塞いだ読取のキー欄の印（目印にはこれと別の欄を使う）。

    Returns:
        目印の確定で送られる読取の経路。
    """
    await wait_until(hold.returned.is_set, what="塞いだ応答を返し終えたこと")
    return await _settle_with_another_key(user, key_marker)


def _failure_text(response: settings_ui.ApiResponse) -> str:
    """読取の失敗が画面に出る文字（`_draw_failure` の `status=0` と「{コード}: {本文}」）。

    Args:
        response: 2xx でない応答。

    Returns:
        画面に出る文字。
    """
    if response.status == 0:
        return response.error or ""
    return f"{response.status}: {response.payload}"


@pytest.mark.parametrize("key_marker", KEY_FIELDS)
async def test_touching_the_same_key_neither_reads_nor_clears_the_inputs(
    user: User, monkeypatch: pytest.MonkeyPatch, key_marker: str
) -> None:
    """B-32（裁定 (1)）・FR-009a: 同じキーのまま触れて離れても、読み直さず、入力を消さない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_key_fully(user, key_marker)
    marker, text = KEPT_INPUTS[key_marker]
    fill(user, marker, text)
    before = len(api.calls)

    user.find(marker=key_marker).trigger("blur")  # キー欄に触れて離れる（Tab で通過するのも同じ）
    settle = await _settle_with_another_key(user, key_marker)

    assert [path for _verb, path, _body in api.calls[before:]] == settle
    assert value_of(user, marker) == text


@pytest.mark.parametrize("key_marker", KEY_FIELDS)
async def test_a_blur_right_after_enter_reads_only_once(
    user: User, monkeypatch: pytest.MonkeyPatch, key_marker: str
) -> None:
    """B-32（裁定 (1)）: Enter で確定した直後にフォーカスが移っても、依存する読取は 1 回ずつ。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    before = len(api.calls)

    fill(user, key_marker, PROJECT_KEY)
    field = user.find(marker=key_marker)
    field.trigger("keydown.enter")
    field.trigger("blur")
    await _wait_confirmed(user, key_marker)
    settle = await _settle_with_another_key(user, key_marker)

    assert [path for _verb, path, _body in api.calls[before:]] == [
        *KEY_READS[key_marker],
        *settle,
    ]


@pytest.mark.parametrize(
    ("key_marker", "path", "failure"),
    [
        (key_marker, path, failure)
        for _name, key_marker, path in RETRY_CASES
        for _kind, failure in TRANSIENT_FAILURES
    ],
    ids=[f"{name}-{kind}" for name, _key, _path in RETRY_CASES for kind, _f in TRANSIENT_FAILURES],
)
async def test_the_same_key_retries_after_a_transient_failure(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    key_marker: str,
    path: str,
    failure: settings_ui.ApiResponse,
) -> None:
    """B-32（裁定 (2)）・J-14: 前回の確定に伴う読込が `status=0`・5xx なら、同じキーで読み直す。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", path): [failure, _default_response("GET", path)]}),
    )
    await open_page(user)
    fill(user, key_marker, PROJECT_KEY)
    user.find(marker=key_marker).trigger("keydown.enter")
    if key_marker == "policies_project" and path != KEY_READS[key_marker][-1]:
        await _wait_confirmed(user, key_marker)  # 失敗の後も共通ルールまで読み進む
    else:
        await wait_until(lambda: _failure_text(failure) in texts_on(user), what="失敗の表示")

    user.find(marker=key_marker).trigger("blur")
    await wait_until(lambda: api.paths("GET").count(path) == 2, what="同じキーでの再試行")


async def test_a_retry_in_progress_is_not_repeated(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32（裁定 (2)）: 再試行の処理中に同じキーの確定がもう一度届いても、重ねて読み直さない。"""
    path = f"/settings/agents?project_key={PROJECT_KEY}"
    failure = unreachable()
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", path): [failure, _default_response("GET", path)]}),
    )
    await open_page(user)
    fill(user, "agents_project", PROJECT_KEY)
    user.find(marker="agents_project").trigger("keydown.enter")
    await wait_until(lambda: _failure_text(failure) in texts_on(user), what="失敗の表示")
    before = len(api.calls)

    field = user.find(marker="agents_project")
    field.trigger("blur")  # 再試行を始める
    field.trigger("keydown.enter")  # 再試行の結果が出る前に、同じキーでもう一度確定する
    await _wait_confirmed(user, "agents_project")
    settle = await _settle_with_another_key(user, "agents_project")

    assert [sent for _verb, sent, _body in api.calls[before:]] == [path, *settle]


async def test_the_retry_follows_the_last_received_confirm(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32（裁定 (2)）・B-33（裁定 (1)）: 再試行の要否は、最後に受けた確定の結果で決める。

    先に受けた確定（pj2）の読取を塞ぎ、最後に受けた確定（pj）を接続できずに先に終わらせる。
    その後で先の確定が成功で終わっても、再試行の要否を上書きしない（表示も上書きしない＝
    `test_a_stale_success_does_not_replace_the_latest_failure`）。
    """
    slow = f"/settings/agents?project_key={NEXT_KEY}"
    last = f"/settings/agents?project_key={PROJECT_KEY}"
    failure = unreachable()
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", last): [failure, _default_response("GET", last)]}),
    )
    hold = api.hold(slow)
    await open_page(user)
    try:
        await confirm(user, "agents_project", NEXT_KEY)  # 先に受けた確定（読取は返らない）
        await wait_until(hold.arrived.is_set, what="先の確定の読取が塞がれたこと")
        await confirm(user, "agents_project", PROJECT_KEY)  # 最後に受けた確定
        await wait_until(lambda: _failure_text(failure) in texts_on(user), what="最後の確定の失敗")
    finally:
        hold.release.set()
    await _settle_after(user, hold, "agents_project")  # 先の確定が後から成功で終わる

    user.find(marker="agents_project").trigger("blur")
    await wait_until(lambda: api.paths("GET").count(last) == 2, what="最後の確定の失敗による再試行")


async def test_a_404_common_rules_is_not_retried_and_keeps_the_typed_body(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32（裁定 (3)）: 共通ルールが 404（未登録のキー）なら同じキーで読み直さず、本文を残す。"""
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", f"/settings/rules/projects/{PROJECT_KEY}"): failing(
                    404, {"detail": "まだ無い"}
                )
            }
        ),
    )
    await open_page(user)
    await confirm(user, "policies_project", PROJECT_KEY)
    await wait_until(lambda: "404: まだ無い" in texts_on(user), what="404 の表示")
    fill(user, "rules", "新しいキーの本文")
    before = len(api.calls)

    user.find(marker="policies_project").trigger("blur")
    settle = await _settle_with_another_key(user, "policies_project")

    assert [path for _verb, path, _body in api.calls[before:]] == settle
    assert value_of(user, "rules") == "新しいキーの本文"


async def test_a_click_right_after_a_same_key_blur_still_writes(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32 と K-1（T097）の両立: 同じキーのままの blur の直後の click は、書込を落とさない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")
    fill(user, "policy_body", "p, alice, data, read")

    user.find(marker="policies_project").trigger("blur")
    user.find(marker="policy_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="権限表の書込")

    assert api.writes() == [
        (
            "PUT",
            f"/settings/policies/{PROJECT_KEY}",
            {"rows": [{"ptype": "p", "fields": ["alice", "data", "read"]}]},
        )
    ]


# T103: Phase 12 のレビュー（変異で見つかった穴）で足したもの ----------------------------


async def test_a_failing_reload_after_a_write_does_not_arm_the_retry(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32（裁定 (2)）: 書込成功後の取り直し（R-1）の失敗は、同じキーでの再試行に数えない。"""
    members = f"/settings/agents?project_key={PROJECT_KEY}"
    failure = unreachable()
    ok_members = _default_response("GET", members)
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", members): [ok_members, failure, ok_members]}),
    )
    await open_page(user)
    await _confirm_key_fully(user, "agents_project")  # 確定の読込は成功する
    fill(user, "agents_persona", "programmer")
    fill(user, "agents_role", "r")
    user.find(marker="agents_save").click()
    await wait_until(lambda: _failure_text(failure) in texts_on(user), what="R-1 の取り直しの失敗")
    before = len(api.calls)

    user.find(marker="agents_project").trigger("blur")
    settle = await _settle_with_another_key(user, "agents_project")

    assert [path for _verb, path, _body in api.calls[before:]] == settle


async def test_a_click_during_a_same_key_retry_still_writes(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-31・B-32: 同じキーでの再試行はキーを変える確定に数えないので、その途中の click も送る。"""
    policies = f"/settings/policies/{PROJECT_KEY}"
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", policies): [
                    failing(502, {"detail": "一時的"}),
                    _default_response("GET", policies),
                ]
            }
        ),
    )
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")  # 権限表の読込だけが 502 で終わる
    fill(user, "hooks_body", HOOKS_BODY)

    user.find(marker="policies_project").trigger("blur")  # 同じキーでの再試行が走り出す
    user.find(marker="hooks_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="Hook の書込")

    assert api.writes() == [("PUT", f"/settings/hooks/{PROJECT_KEY}", json.loads(HOOKS_BODY))]


async def test_a_successful_retry_stops_further_reads(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32（裁定 (2)）: 再試行が成功したら、その後は同じキーに触れても読まない。"""
    path = f"/settings/tasks/{PROJECT_KEY}"
    failure = failing(500, {"detail": "壊れた"})
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", path): [failure, _default_response("GET", path)]}),
    )
    await open_page(user)
    fill(user, "tasks_project", PROJECT_KEY)
    user.find(marker="tasks_project").trigger("keydown.enter")
    await wait_until(lambda: _failure_text(failure) in texts_on(user), what="最初の確定の失敗")
    user.find(marker="tasks_project").trigger("blur")  # 再試行（成功する）
    await wait_until(
        lambda: any('"description": "pj"' in text for text in texts_on(user)),
        what="再試行の成功の表示",
    )
    before = len(api.calls)

    user.find(marker="tasks_project").trigger("blur")
    settle = await _settle_with_another_key(user, "tasks_project")

    assert [sent for _verb, sent, _body in api.calls[before:]] == settle


async def test_a_failed_retry_reads_again_on_the_next_confirm(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-32（裁定 (2)）: 再試行も一時的な失敗なら、次の同じキーの確定でまた読む。"""
    path = f"/settings/tasks/{PROJECT_KEY}"
    first = failing(500, {"detail": "壊れた"})
    second = failing(503, {"detail": "まだ壊れている"})
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", path): [first, second, _default_response("GET", path)]}),
    )
    await open_page(user)
    fill(user, "tasks_project", PROJECT_KEY)
    user.find(marker="tasks_project").trigger("keydown.enter")
    await wait_until(lambda: _failure_text(first) in texts_on(user), what="最初の確定の失敗")
    user.find(marker="tasks_project").trigger("blur")  # 再試行（また失敗する）
    await wait_until(lambda: _failure_text(second) in texts_on(user), what="再試行の失敗")

    user.find(marker="tasks_project").trigger("blur")
    await wait_until(lambda: api.paths("GET").count(path) == 3, what="2 度目の再試行")


@pytest.mark.parametrize(
    ("key_marker", "path"),
    [(key_marker, path) for _name, key_marker, path in RETRY_CASES],
    ids=[name for name, _key, _path in RETRY_CASES],
)
async def test_a_404_is_not_retried_on_the_same_key(
    user: User, monkeypatch: pytest.MonkeyPatch, key_marker: str, path: str
) -> None:
    """B-32（裁定 (3)）: どの依存する読込でも、404 なら同じキーでは読み直さない。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", path): failing(404, {"detail": "無い"})}))
    await open_page(user)
    fill(user, key_marker, PROJECT_KEY)
    user.find(marker=key_marker).trigger("keydown.enter")
    if key_marker == "policies_project" and path != KEY_READS[key_marker][-1]:
        await _wait_confirmed(user, key_marker)  # 404 の後も共通ルールまで読み進む
    else:
        await wait_until(lambda: "404: 無い" in texts_on(user), what="404 の表示")
    before = len(api.calls)

    user.find(marker=key_marker).trigger("blur")
    settle = await _settle_with_another_key(user, key_marker)

    assert [sent for _verb, sent, _body in api.calls[before:]] == settle


# --------------------------------------------------------------------------------------
# T105: 最新でない確定・例外で止まった確定・再試行の範囲（design-sync B-33・B-34 の裁定）
# --------------------------------------------------------------------------------------

THIRD_KEY = "pj3"
"""`pj2` の確定の途中に、さらに確定するキー（B-33 (iii)・(iv)）。"""

THIRD_KEY_READS: dict[tuple[str, str], object] = {
    ("GET", f"/settings/agents?project_key={THIRD_KEY}"): ok(
        [{"persona_key": "programmer", "project_key": THIRD_KEY, "role": "pj3 の役割"}]
    ),
    ("GET", f"/settings/rules/projects/{THIRD_KEY}"): ok({"body": "pj3 の本文"}),
}
"""`pj3` の依存する読取の応答（どの確定の結果が画面に出ているかを見分けられる値にする）。"""

POLICIES_PATH = f"/settings/policies/{PROJECT_KEY}"
HOOKS_PATH = f"/settings/hooks/{PROJECT_KEY}"
RULES_PATH = f"/settings/rules/projects/{PROJECT_KEY}"
"""権限の節の、`pj` の確定に伴う 3 つの読取の経路。"""

BROKEN = failing(500, {"detail": "壊れた"})
"""一時的な失敗（5xx）の応答。"""


def _policies_reads(key: str) -> list[str]:
    """権限の節の確定に伴う 3 つの読取の経路（送る順）。

    Args:
        key: キー。

    Returns:
        経路の並び。
    """
    return [
        f"/settings/policies/{key}",
        f"/settings/hooks/{key}",
        f"/settings/rules/projects/{key}",
    ]


def _member_keys_on(user: User) -> list[object]:
    """メンバの表に出ている行のキー。

    Args:
        user: `user` フィクスチャ。

    Returns:
        行ごとの `project_key`。
    """
    return [row.get("project_key") for row in table_of(user, "members-table")[1]]


# B-33 (1): 最新でない確定の結果は、表示・入力欄・欄の表示のどれにも反映しない ---------------


async def test_a_stale_failure_does_not_replace_the_latest_members(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-33 (1): 先に受けた確定の失敗が、後に受けた確定の成功の後に届いても、表示は最新のまま。"""
    stale = f"/settings/agents?project_key={NEXT_KEY}"
    api = use_api(monkeypatch, FakeApi(responses={("GET", stale): BROKEN}))
    hold = api.hold(stale)
    await open_page(user)
    try:
        await confirm(user, "agents_project", NEXT_KEY)  # 先に受けた確定（読取は返らない）
        await wait_until(hold.arrived.is_set, what="先の確定の読取が塞がれたこと")
        await _confirm_key_fully(user, "agents_project")  # 最後に受けた確定（pj。成功する）
    finally:
        hold.release.set()
    await _settle_after(user, hold, "agents_project")  # 先の確定が後から失敗で終わる

    assert _failure_text(BROKEN) not in texts_on(user), "最新でない確定の失敗を出した"
    assert exists(user, "agents_save"), "最新でない確定の失敗でメンバ欄が隠れた"
    assert _member_keys_on(user) == [PROJECT_KEY]


async def test_a_stale_success_does_not_replace_the_latest_failure(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-33 (1): 先に受けた確定の成功が後から届いても、最新のキーの失敗の表示を上書きしない。"""
    stale = f"/settings/agents?project_key={NEXT_KEY}"
    failure = unreachable()
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                **NEXT_KEY_READS,
                ("GET", f"/settings/agents?project_key={PROJECT_KEY}"): failure,
            }
        ),
    )
    hold = api.hold(stale)
    await open_page(user)
    try:
        await confirm(user, "agents_project", NEXT_KEY)  # 先に受けた確定（読取は返らない）
        await wait_until(hold.arrived.is_set, what="先の確定の読取が塞がれたこと")
        await confirm(user, "agents_project", PROJECT_KEY)  # 最後に受けた確定（読めない）
        await wait_until(lambda: _failure_text(failure) in texts_on(user), what="最後の確定の失敗")
    finally:
        hold.release.set()
    await _settle_after(user, hold, "agents_project")  # 先の確定が後から成功で終わる

    assert _failure_text(failure) in texts_on(user), "最新のキーの失敗の表示が消えた"
    assert not exists(user, "members-table"), "最新でない確定のメンバの表を出した"
    assert not exists(user, "agents_save"), "最新のキーが読めないのにメンバ欄が出ている"


async def test_a_stale_common_rules_never_reach_the_latest_key(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-33 (1)・D-4: pj → pj2（共通ルールの読込を塞ぐ）→ pj3 と確定する。

    pj3 の本文が入った後に pj2 の応答が届いても、本文欄は pj3 の値のままで、`rules_save` は
    pj3 の本文を pj3 へ送る（修正前は pj2 の本文を pj3 へ送った＝サーバの値を書き違える）。
    """
    stale = f"/settings/rules/projects/{NEXT_KEY}"
    api = use_api(monkeypatch, FakeApi(responses={**NEXT_KEY_READS, **THIRD_KEY_READS}))
    hold = api.hold(stale)
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")
    try:
        await confirm(user, "policies_project", NEXT_KEY)
        await wait_until(hold.arrived.is_set, what="pj2 の共通ルールの読込が塞がれたこと")
        await confirm(user, "policies_project", THIRD_KEY)
        await wait_until(
            lambda: exists(user, "rules") and value_of(user, "rules") == "pj3 の本文",
            what="pj3 の本文の読込",
        )
    finally:
        hold.release.set()
    await _settle_after(user, hold, "policies_project")  # pj2 の本文が後から届く

    assert value_of(user, "rules") == "pj3 の本文", "最新でない確定の本文で本文欄を上書きした"
    user.find(marker="rules_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="共通ルールの保存")

    assert api.writes() == [
        ("PUT", f"/settings/rules/projects/{THIRD_KEY}", {"body": "pj3 の本文"})
    ]


# B-33 (2): 確定に伴う読取の経路は、その確定を受けた時点のキーで組む ---------------------


async def test_the_reads_of_a_stale_confirm_use_the_key_it_received(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-33 (2): 先に受けた確定の途中の読取（Hook・共通ルール）は、受けた時点のキーの経路へ送る。

    修正前は、途中の読取の経路をその時点の確定済みのキーから組んだので、pj2 の確定の続きが
    pj3 の Hook・共通ルールを読んだ。
    """
    stale = f"/settings/policies/{NEXT_KEY}"
    api = use_api(monkeypatch, FakeApi(responses={**NEXT_KEY_READS, **THIRD_KEY_READS}))
    hold = api.hold(stale)
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")
    try:
        await confirm(user, "policies_project", NEXT_KEY)
        await wait_until(hold.arrived.is_set, what="pj2 の権限表の読取が塞がれたこと")
        await confirm(user, "policies_project", THIRD_KEY)
        await wait_until(
            lambda: exists(user, "rules") and value_of(user, "rules") == "pj3 の本文",
            what="pj3 の本文の読込",
        )
    finally:
        hold.release.set()
    await wait_until(
        lambda: f"/settings/rules/projects/{NEXT_KEY}" in api.paths("GET"),
        what="pj2 の確定の続きの読取が pj2 の経路へ送られること",
    )
    await _settle_with_another_key(user, "policies_project")

    gets = api.paths("GET")
    for path in (*_policies_reads(NEXT_KEY), *_policies_reads(THIRD_KEY)):
        assert gets.count(path) == 1, f"{path} を {gets.count(path)} 回読んだ"
    assert value_of(user, "rules") == "pj3 の本文"


async def test_a_confirm_reads_the_key_it_received_even_before_it_starts(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-33 (2): 確定の処理が走り出す前に次の確定を受けても、先の確定は受けた時点のキーを読む。

    2 つの確定を、間に `await` を挟まずに受ける（どちらの処理もまだ走り出していない）。
    修正前は、先の確定も走り出した時点の確定済みのキー（pj3）を読み、pj2 を 1 度も読まなかった。
    """
    api = use_api(monkeypatch, FakeApi(responses={**NEXT_KEY_READS, **THIRD_KEY_READS}))
    await open_page(user)

    for key in (NEXT_KEY, THIRD_KEY):
        fill(user, "agents_project", key)
        user.find(marker="agents_project").trigger("keydown.enter")
    await wait_until(
        lambda: exists(user, "members-table") and _member_keys_on(user) == [THIRD_KEY],
        what="pj3 のメンバの表",
    )
    await _settle_with_another_key(user, "agents_project")

    gets = api.paths("GET")
    assert gets.count(f"/settings/agents?project_key={NEXT_KEY}") == 1
    assert gets.count(f"/settings/agents?project_key={THIRD_KEY}") == 1
    assert _member_keys_on(user) == [THIRD_KEY], "最新でない確定のメンバの表を出した"


# B-33 (3): 例外で止まった確定は `status=0` と同じに扱う ----------------------------------


async def test_a_confirm_stopped_by_an_exception_is_treated_as_unreachable(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-33 (3)・D-4: Hook の読取が例外で止まったら、止まった読込とそれ以降を `status=0` とみなす。

    `call_api` が畳むのは `URLError`・`OSError` だけで、`http.client.IncompleteRead` は畳まれずに
    確定の処理へ届く。本文欄と保存ボタンを隠して「app に接続できません: …」を出す（前のキーの
    本文を新しいキーへ送れない）。同じキーに触れ直すと、失敗した読込（Hook・共通ルール）だけを
    読み直す。
    """
    error = http.client.IncompleteRead(b"")
    hooks = f"/settings/hooks/{NEXT_KEY}"
    rules = f"/settings/rules/projects/{NEXT_KEY}"
    api = use_api(
        monkeypatch,
        FakeApi(responses={**NEXT_KEY_READS, ("GET", hooks): [error, ok(HOOKS)]}),
    )
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")  # 本文欄は pj の本文

    await confirm(user, "policies_project", NEXT_KEY)
    message = f"app に接続できません: {error}"
    await wait_until(
        lambda: message in texts_on(user) and not exists(user, "rules_save"),
        what="例外で止まった確定の表示",
    )

    assert not exists(user, "rules"), "読めていないのに本文欄が出ている（前のキーの本文が残る）"
    assert rules not in api.paths("GET"), "止まった後の読込を送った"
    assert api.writes() == []

    before = len(api.calls)
    user.find(marker="policies_project").trigger("blur")
    await wait_until(
        lambda: exists(user, "rules") and value_of(user, "rules") == "pj2 の本文",
        what="同じキーでの再試行による本文の読込",
    )

    assert [path for _verb, path, _body in api.calls[before:]] == [hooks, rules]
    assert exists(user, "rules_save")


# B-34: 再試行で読み直すのは、一時的に失敗した読込だけ -------------------------------------


async def test_a_retry_after_a_hooks_failure_keeps_the_typed_common_rules(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-34 (i): Hook が 500・共通ルールが 200 なら、再試行は Hook だけを読み、本文を残す。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", HOOKS_PATH): [BROKEN, ok(HOOKS)]}))
    await open_page(user)
    await _confirm_key_fully(user, "policies_project")
    fill(user, "rules", "書きかけの本文")
    before = len(api.calls)

    user.find(marker="policies_project").trigger("blur")
    await wait_until(lambda: api.paths("GET").count(HOOKS_PATH) == 2, what="Hook の再試行")
    settle = await _settle_with_another_key(user, "policies_project")

    assert [path for _verb, path, _body in api.calls[before:]] == [HOOKS_PATH, *settle]
    assert value_of(user, "rules") == "書きかけの本文"


async def test_a_retry_after_a_hooks_failure_keeps_the_body_of_an_unregistered_key(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-34 (ii): 共通ルールが 404・Hook が 500 なら、未登録のキーで書いた本文が残る。"""
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", RULES_PATH): failing(404, {"detail": "まだ無い"}),
                ("GET", HOOKS_PATH): [BROKEN, ok(HOOKS)],
            }
        ),
    )
    await open_page(user)
    await confirm(user, "policies_project", PROJECT_KEY)
    await wait_until(lambda: "404: まだ無い" in texts_on(user), what="共通ルールの 404 の表示")
    fill(user, "rules", "新しいキーの本文")
    before = len(api.calls)

    user.find(marker="policies_project").trigger("blur")
    await wait_until(lambda: api.paths("GET").count(HOOKS_PATH) == 2, what="Hook の再試行")
    settle = await _settle_with_another_key(user, "policies_project")

    assert [path for _verb, path, _body in api.calls[before:]] == [HOOKS_PATH, *settle]
    assert value_of(user, "rules") == "新しいキーの本文"


async def test_a_retry_after_a_common_rules_failure_reloads_only_the_common_rules(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-34 (iii): 共通ルールが 500 なら、同じキーの再試行は共通ルールだけを読み、本文を入れる。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", RULES_PATH): [BROKEN, ok(RULES)]}))
    await open_page(user)
    await confirm(user, "policies_project", PROJECT_KEY)
    await wait_until(
        lambda: _failure_text(BROKEN) in texts_on(user) and not exists(user, "rules_save"),
        what="共通ルールの読込の失敗",
    )
    before = len(api.calls)

    user.find(marker="policies_project").trigger("blur")
    await _wait_confirmed(user, "policies_project")  # 共通ルールの本文が入る
    settle = await _settle_with_another_key(user, "policies_project")

    assert [path for _verb, path, _body in api.calls[before:]] == [RULES_PATH, *settle]
    assert exists(user, "rules_save")


async def test_a_failed_retry_narrows_the_next_retry_to_what_failed_again(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-34 (iv): 再試行で読み直した読込がまた失敗したら、次の再試行ではその読込だけを読む。"""
    again = failing(503, {"detail": "まだ壊れている"})
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", HOOKS_PATH): [BROKEN, again, ok(HOOKS)],
                ("GET", RULES_PATH): [BROKEN, ok(RULES)],
            }
        ),
    )
    await open_page(user)
    await confirm(user, "policies_project", PROJECT_KEY)
    await wait_until(
        lambda: _failure_text(BROKEN) in texts_on(user) and not exists(user, "rules_save"),
        what="Hook と共通ルールの読込の失敗",
    )
    first = len(api.calls)

    user.find(marker="policies_project").trigger("blur")  # 再試行（Hook はまた失敗する）
    await _wait_confirmed(user, "policies_project")  # 共通ルールの本文が入る

    assert [path for _verb, path, _body in api.calls[first:]] == [HOOKS_PATH, RULES_PATH]
    assert _failure_text(again) in texts_on(user)

    second = len(api.calls)
    user.find(marker="policies_project").trigger("blur")  # 2 度目の再試行
    await wait_until(lambda: api.paths("GET").count(HOOKS_PATH) == 3, what="Hook の 2 度目の再試行")
    settle = await _settle_with_another_key(user, "policies_project")

    assert [path for _verb, path, _body in api.calls[second:]] == [HOOKS_PATH, *settle]


# --------------------------------------------------------------------------------------
# T044: 同時実行・数値欄・符号化・JSON の時機（W-1・M-1・D-5・N-19・N-21・J-15・J-18・J-20）
# --------------------------------------------------------------------------------------


async def _confirm_every_key(user: User, api: FakeApi) -> None:
    """3 つのキー欄をすべて確定し、16 個の書込ボタンを画面に出す。

    Args:
        user: `user` フィクスチャ。
        api: 偽の `call_api`。
    """
    await _confirm_member_key(user, api)
    await _confirm_tasks_key(user, api)
    await _confirm_policies_key(user, api)


async def test_every_write_button_is_locked_while_waiting(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W-1・N-21・J-15: 書込の応答を待つ間はページの書込ボタンをすべて押せなくする。"""
    gate = threading.Event()
    api = use_api(monkeypatch, FakeApi(gate=gate, gate_methods=("POST", "PUT", "PATCH", "DELETE")))
    await open_page(user)
    await _confirm_every_key(user, api)
    present = [marker for marker in WRITE_BUTTONS if exists(user, marker)]
    assert sorted(present) == sorted(WRITE_BUTTONS), f"16 個そろっていない: {present}"
    assert not any(is_disabled(user, marker) for marker in present)

    try:
        user.find(marker="humans_save").click()
        await wait_until(lambda: is_disabled(user, "humans_save"), what="書込の開始")
        for marker in present:
            assert is_disabled(user, marker), f"{marker} が押せるままになっている"
    finally:
        gate.set()

    await wait_until(
        lambda: not any(is_disabled(user, marker) for marker in present),
        what="応答と取り直しの後に押せる状態へ戻ること",
    )


async def test_confirming_a_key_is_not_blocked_by_a_pending_write(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W-1・J-15: 確定に伴う読取は書込の待ちに制限されない。"""
    gate = threading.Event()
    api = use_api(monkeypatch, FakeApi(gate=gate, gate_methods=("POST", "PUT", "PATCH", "DELETE")))
    await open_page(user)

    try:
        user.find(marker="humans_save").click()
        await wait_until(lambda: is_disabled(user, "humans_save"), what="書込の開始")
        await confirm(user, "agents_project", PROJECT_KEY)
        await wait_until(
            lambda: f"/settings/agents?project_key={PROJECT_KEY}" in api.paths("GET"),
            what="確定に伴う読取",
        )
    finally:
        gate.set()


INTEGER_FIELDS: tuple[tuple[str, float], ...] = (
    ("global_agent_max_iter", 0),
    ("global_agent_max_rpm", 0),
    ("global_agent_max_execution_time", 0),
    ("profile_window", 1),
    ("limits_req", 1),
)
"""整数の数値欄と下限（M-1・J-18。費用上限だけは小数で下限 0.0）。"""


@pytest.mark.parametrize(
    ("marker", "minimum"), list(INTEGER_FIELDS), ids=[m for m, _ in INTEGER_FIELDS]
)
async def test_an_integer_field_rounds_and_clamps_on_confirm(
    user: User, monkeypatch: pytest.MonkeyPatch, marker: str, minimum: float
) -> None:
    """M-1・J-18: 整数の欄は小数を受けず、下限を下回る値は確定時に下限へ寄る。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)

    set_value(user, marker, 3.7)
    user.find(marker=marker).trigger("blur")
    await wait_until(lambda: value_of(user, marker) == 4, what="小数が整数へ丸まること")

    set_value(user, marker, -5)
    user.find(marker=marker).trigger("blur")
    await wait_until(lambda: value_of(user, marker) == minimum, what="下限へ寄ること")


async def test_the_cost_limit_is_a_decimal_with_a_zero_minimum(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-1・J-18: 費用上限は小数で、下限は 0.0（既定は 1.0）。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    assert value_of(user, "limits_cost") == 1.0

    set_value(user, "limits_cost", 2.5)
    user.find(marker="limits_cost").trigger("blur")
    await wait_until(lambda: value_of(user, "limits_cost") == 2.5, what="小数が保たれること")

    set_value(user, "limits_cost", -1)
    user.find(marker="limits_cost").trigger("blur")
    await wait_until(lambda: value_of(user, "limits_cost") == 0.0, what="下限へ寄ること")


@pytest.mark.parametrize("item", ["max_iter", "max_rpm", "max_execution_time"])
async def test_an_empty_agent_number_is_not_sent(
    user: User, monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    """J-18・U-I: エージェントの数値 3 種は、空のときも 0 のときも送らない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    fill(user, "global_agent_persona", "programmer")
    fill(user, "global_agent_role", "r")
    set_value(user, f"global_agent_{item}", None)
    user.find(marker="global_agent_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="ペルソナ既定の保存")

    body = api.writes()[0][2]
    assert isinstance(body, dict)
    assert item not in body


@pytest.mark.parametrize(
    ("marker", "target"),
    [("profile_save", "profile_window"), ("limits_global_save", "limits_cost")],
)
async def test_an_empty_required_number_sends_nothing(
    user: User, monkeypatch: pytest.MonkeyPatch, marker: str, target: str
) -> None:
    """J-18: 文脈長・要求上限・費用上限が空なら、そのボタンは要求を送らない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    if marker == "profile_save":
        await _prepare_profile(user, api)

    set_value(user, target, None)
    user.find(marker=marker).click()
    await wait_until(lambda: True, what="押した後の処理")

    assert api.writes() == [], f"{target} が空なのに要求を送った"


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [("a b", "a%20b"), ("a?b", "a%3Fb"), ("a#b", "a%23b")],
    ids=["space", "question", "hash"],
)
async def test_user_values_are_percent_encoded_in_paths_and_queries(
    user: User, monkeypatch: pytest.MonkeyPatch, raw: str, encoded: str
) -> None:
    """D-5・N-19・J-9: 利用者の値は経路の段とクエリの値で符号化され、例外にならない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await confirm(user, "agents_project", raw)
    await wait_until(
        lambda: f"/settings/agents?project_key={encoded}" in api.paths("GET"),
        what="符号化されたクエリ",
    )
    fill(user, "global_agent_persona", raw)
    fill(user, "global_agent_role", "r")
    user.find(marker="global_agent_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="ペルソナ既定の保存")
    fill(user, "command_name", raw)
    user.find(marker="command_save").click()
    await wait_until(lambda: len(api.writes()) == 2, what="コマンド定義の保存")

    assert api.writes()[0][1] == f"/settings/agents/global/{encoded}"
    assert api.writes()[1][1] == f"/settings/commands/{encoded}"


UNREADABLE_JSON_CASES: tuple[tuple[str, str], ...] = (
    ("tasks_global_body", "tasks_global_save"),
    ("global_agent_mcps", "global_agent_save"),
)
"""JSON の欄と、その欄を送るボタン（Hook はキーの確定が要るので別のテストで見る）。"""


@pytest.mark.parametrize(
    ("field", "save"), list(UNREADABLE_JSON_CASES), ids=[f for f, _ in UNREADABLE_JSON_CASES]
)
async def test_an_unreadable_json_only_complains_when_the_button_is_pressed(
    user: User, monkeypatch: pytest.MonkeyPatch, field: str, save: str
) -> None:
    """J-20 (1): 「JSON として読めません: …」は入力の確定では出ず、押したときに出る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    if save == "global_agent_save":
        fill(user, "global_agent_persona", "programmer")

    fill(user, field, "{壊れた")
    user.find(marker=field).trigger("blur")
    await wait_until(lambda: True, what="確定の後")
    assert not any(text.startswith("JSON として読めません: ") for text in texts_on(user)), (
        "入力の確定で誤りを出している"
    )

    user.find(marker=save).click()
    await wait_until(
        lambda: any(
            text.startswith("JSON として読めません: ") for text in result_texts(user, save)
        ),
        what="JSON が読めない旨",
    )

    assert api.writes() == []


async def test_an_unreadable_hooks_json_is_never_sent(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-20 (1): Hook の宣言 JSON も、読めなければそのボタンの結果表示に出して送らない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_policies_key(user, api)

    fill(user, "hooks_body", "{壊れた")
    user.find(marker="hooks_save").click()
    await wait_until(
        lambda: any(
            text.startswith("JSON として読めません: ") for text in result_texts(user, "hooks_save")
        ),
        what="JSON が読めない旨",
    )

    assert api.writes() == []


async def test_an_empty_table_shows_no_basic_no_data_label(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-19・N-21: 空の表に UI 基盤の既定の文字を出さない。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/settings/agents"): ok([])}))

    await open_page(user)

    assert "No data available" not in texts_on(user)
    props: dict[str, object] = dict(one(user, "agents-table").props)
    assert props.get("no-data-label") == ""


def _classes_in(user: User, marker: str) -> set[str]:
    """結果の表示域の中の部品が持つ classes を集める（成功と誤りの見分けを見る）。

    Args:
        user: `user` フィクスチャ。
        marker: 表示域の印。

    Returns:
        classes の集合。
    """
    found: set[str] = set()
    for element in one(user, marker).descendants():
        # `Element.classes` は要素の型を持たないので、narrow をここに閉じる
        found.update(cast("list[str]", list(element.classes)))
    return found


async def test_success_and_failure_are_told_apart(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-19・N-21: 成功・誤り・案内は見分けられるように描く（色合いそのものは対象外）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/limits"): failing(404, {"detail": "無い"})}),
    )
    await open_page(user)

    await _save_humans(user, api)
    await wait_until(lambda: bool(result_texts(user, "humans_save")), what="成功の文言")
    user.find(marker="limits_global_save").click()
    await wait_until(lambda: bool(result_texts(user, "limits_global_save")), what="誤りの文言")

    success_classes = _classes_in(user, "humans_save-result")
    failure_classes = _classes_in(user, "limits_global_save-result")

    assert success_classes != failure_classes, "成功と誤りが同じ見た目で描かれている"


# --------------------------------------------------------------------------------------
# D26: 段テンプレートの段ごとの編集欄
# --------------------------------------------------------------------------------------

STAGE_GLOBAL: dict[str, object] = {
    "program": {
        "description": "1 行目 {title}\n\n3 行目\n",
        "expected_output": "分解の一覧",
        "agent": "planner",
        "tools": None,
    },
    "plain": {"description": "d2", "expected_output": "e2", "agent": None, "tools": None},
    "review_normal": {
        "description": "検分",
        "expected_output": "判定",
        "agent": None,
        "tools": ["read_issue", "submit_review"],
    },
}
"""段ごとの欄の検査用の全体の読取（**`GET` と同じく任意項目を null で返す**＝ M-2）。"""

STAGE_GLOBAL_SENT: dict[str, object] = {
    "program": {
        "description": "1 行目 {title}\n\n3 行目\n",
        "expected_output": "分解の一覧",
        "agent": "planner",
    },
    "plain": {"description": "d2", "expected_output": "e2"},
    "review_normal": {
        "description": "検分",
        "expected_output": "判定",
        "tools": ["read_issue", "submit_review"],
    },
}
"""`STAGE_GLOBAL` を送り返すときの形（null の項目を落とす）。"""

STAGE_OVERRIDES: dict[str, object] = {
    "plain": {
        "description": "pj の d2",
        "expected_output": "pj の e2",
        "agent": None,
        "tools": None,
    }
}
"""PJ の上書き（`plain` だけ）。"""


def _stage_api(**overrides: object) -> FakeApi:
    """段ごとの欄の検査用の偽物（全体 3 段・PJ は `plain` だけ上書き）。

    Args:
        overrides: 追加・差し替える応答（`(method, path)` → 応答）。

    Returns:
        偽の `call_api`。
    """
    responses: dict[tuple[str, str], object] = {
        ("GET", "/settings/tasks/global"): ok(STAGE_GLOBAL),
        ("GET", f"/settings/tasks/{PROJECT_KEY}"): ok(STAGE_OVERRIDES),
    }
    for key, value in overrides.items():
        method, _, path = key.partition(" ")
        responses[(method, path)] = value
    return FakeApi(responses=responses)


async def _confirm_stage_key(user: User, api: FakeApi) -> None:
    """段テンプレートの上書きのキーを確定し、PJ の段ごとの欄に値が入るまで待つ。"""
    await _confirm_tasks_key(user, api)
    await wait_until(lambda: bool(_state(user)), what="PJ の段ごとの欄の値")


def _state(user: User) -> str:
    """PJ の段ごとの欄の「値の出どころ」の文言。"""
    return str(getattr(one(user, "tasks_pj_stage_state"), "text", ""))


async def test_the_stage_form_is_filled_with_the_first_stage_on_open(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 開いた時点で最初の段が選ばれ、現在値（複数行のまま）が欄に入っている。"""
    use_api(monkeypatch, _stage_api())

    await open_page(user)

    assert value_of(user, "tasks_stage") == "program"
    assert value_of(user, "tasks_stage_description") == "1 行目 {title}\n\n3 行目\n"
    assert value_of(user, "tasks_stage_expected_output") == "分解の一覧"
    assert value_of(user, "tasks_stage_agent") == "planner"
    assert value_of(user, "tasks_stage_replace_tools") is False
    assert value_of(user, "tasks_stage_tools") == ""


async def test_choosing_another_stage_refills_the_form(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 段を選び直すと、その段の値に入れ替わる（`tools` があれば置き換えの印が付く）。"""
    use_api(monkeypatch, _stage_api())
    await open_page(user)

    set_value(user, "tasks_stage", "review_normal")

    assert value_of(user, "tasks_stage_description") == "検分"
    assert value_of(user, "tasks_stage_agent") == ""
    assert value_of(user, "tasks_stage_replace_tools") is True
    assert value_of(user, "tasks_stage_tools") == "read_issue submit_review"


async def test_saving_a_global_stage_rereads_and_replaces_only_that_stage(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 送る直前に全体を読み直し、選んだ段だけを差し替えて `PUT` する（null は送らない）。"""
    api = use_api(monkeypatch, _stage_api())
    await open_page(user)
    set_value(user, "tasks_stage", "plain")
    fill(user, "tasks_stage_description", "新しい指示")
    reads = len(api.paths("GET"))

    user.find(marker="tasks_stage_global_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="段の保存")

    expected = dict(STAGE_GLOBAL_SENT)
    expected["plain"] = {"description": "新しい指示", "expected_output": "e2"}
    assert api.writes() == [("PUT", "/settings/tasks/global", expected)]
    put_at = api.calls.index(("PUT", "/settings/tasks/global", expected))
    assert api.calls[put_at - 1] == ("GET", "/settings/tasks/global", None), "読み直していない"
    assert api.paths("GET")[reads] == "/settings/tasks/global"


async def test_the_tools_are_sent_only_when_replacing(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 置き換えの印を外すと `tools` を送らない（担当の装備に戻る）。付けて空なら `[]`。"""
    api = use_api(monkeypatch, _stage_api())
    await open_page(user)
    set_value(user, "tasks_stage", "review_normal")
    set_value(user, "tasks_stage_replace_tools", False)

    user.find(marker="tasks_stage_global_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="印を外した保存")
    set_value(user, "tasks_stage_replace_tools", True)
    fill(user, "tasks_stage_tools", "")
    await wait_until(lambda: not is_disabled(user, "tasks_stage_global_save"), what="書込の終了")
    user.find(marker="tasks_stage_global_save").click()
    await wait_until(lambda: len(api.writes()) == 2, what="空の置き換えの保存")

    first = cast("dict[str, dict[str, object]]", api.writes()[0][2])
    second = cast("dict[str, dict[str, object]]", api.writes()[1][2])
    assert "tools" not in first["review_normal"]
    assert second["review_normal"]["tools"] == []


async def test_a_failed_reread_sends_no_write(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """D26: 読み直しが失敗したら書込を送らず、その応答を結果に出す。"""
    api = use_api(
        monkeypatch,
        _stage_api(**{"GET /settings/tasks/global": [ok(STAGE_GLOBAL), failing(500, "壊れた")]}),
    )
    await open_page(user)

    user.find(marker="tasks_stage_global_save").click()
    await wait_until(
        lambda: bool(result_texts(user, "tasks_stage_global_save")), what="読み直しの失敗"
    )

    assert api.writes() == []
    assert "500: 壊れた" in result_texts(user, "tasks_stage_global_save")


async def test_the_form_shows_the_reloaded_value_after_saving(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 保存に成功したら節を取り直し、欄にも取り直した値を入れる。"""
    reloaded = {
        **STAGE_GLOBAL,
        "program": {"description": "取り直した本文", "expected_output": "x"},
    }
    use_api(
        monkeypatch,
        _stage_api(
            **{
                "GET /settings/tasks/global": [
                    ok(STAGE_GLOBAL),
                    ok(STAGE_GLOBAL),
                    ok(reloaded),
                ]
            }
        ),
    )
    await open_page(user)

    user.find(marker="tasks_stage_global_save").click()
    await wait_until(
        lambda: value_of(user, "tasks_stage_description") == "取り直した本文", what="入れ直し"
    )

    assert value_of(user, "tasks_stage_agent") == ""


async def test_the_project_form_tells_overridden_from_inherited(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: PJ の欄は、上書きがある段ではその値、無い段では全体の値と、その旨を出す。"""
    api = use_api(monkeypatch, _stage_api())
    await open_page(user)
    await _confirm_stage_key(user, api)

    assert _state(user) == settings_ui.STAGE_INHERITED
    assert value_of(user, "tasks_pj_stage_description") == "1 行目 {title}\n\n3 行目\n"

    set_value(user, "tasks_pj_stage", "plain")

    assert _state(user) == settings_ui.STAGE_OVERRIDDEN
    assert value_of(user, "tasks_pj_stage_description") == "pj の d2"


async def test_saving_a_project_stage_adds_it_to_the_overrides(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 選んだ段を、読み直した上書きに足して `PUT` する（ほかの上書きは残す）。"""
    api = use_api(monkeypatch, _stage_api())
    await open_page(user)
    await _confirm_stage_key(user, api)
    fill(user, "tasks_pj_stage_description", "pj の指示")

    user.find(marker="tasks_stage_project_save").click()
    await wait_until(lambda: len(api.writes()) == 1, what="PJ の段の保存")

    assert api.writes() == [
        (
            "PUT",
            f"/settings/tasks/{PROJECT_KEY}",
            {
                "plain": {"description": "pj の d2", "expected_output": "pj の e2"},
                "program": {
                    "description": "pj の指示",
                    "expected_output": "分解の一覧",
                    "agent": "planner",
                },
            },
        )
    ]


async def test_removing_the_last_override_deletes_the_file(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 最後の 1 段を外すと空になるので `DELETE`（全体の 7 キーに戻る＝ FR-070）。"""
    api = use_api(monkeypatch, _stage_api())
    await open_page(user)
    await _confirm_stage_key(user, api)
    set_value(user, "tasks_pj_stage", "plain")

    user.find(marker="tasks_stage_project_remove").click()
    await wait_until(lambda: len(api.writes()) == 1, what="上書きの解除")

    assert api.writes() == [("DELETE", f"/settings/tasks/{PROJECT_KEY}", None)]


async def test_removing_one_of_several_overrides_puts_the_rest(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 上書きが複数あれば、選んだ段だけを除いて `PUT` する。"""
    overrides = {**STAGE_OVERRIDES, "program": {"description": "p", "expected_output": "q"}}
    api = use_api(monkeypatch, _stage_api(**{f"GET /settings/tasks/{PROJECT_KEY}": ok(overrides)}))
    await open_page(user)
    await _confirm_stage_key(user, api)
    set_value(user, "tasks_pj_stage", "plain")

    user.find(marker="tasks_stage_project_remove").click()
    await wait_until(lambda: len(api.writes()) == 1, what="1 段の解除")

    assert api.writes() == [
        (
            "PUT",
            f"/settings/tasks/{PROJECT_KEY}",
            {"program": {"description": "p", "expected_output": "q"}},
        )
    ]


async def test_removing_a_stage_that_is_not_overridden_sends_nothing(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 上書きされていない段を外そうとしても送らず、その旨を出す。"""
    api = use_api(monkeypatch, _stage_api())
    await open_page(user)
    await _confirm_stage_key(user, api)

    user.find(marker="tasks_stage_project_remove").click()
    await wait_until(
        lambda: bool(result_texts(user, "tasks_stage_project_remove")), what="送らない旨"
    )

    assert api.writes() == []
    assert settings_ui.STAGE_NOT_OVERRIDDEN_NOTICE in result_texts(
        user, "tasks_stage_project_remove"
    )


async def test_the_tasks_file_location_is_shown(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 段テンプレートの節に、ファイルの所在（規約の相対パス）を出す。"""
    use_api(monkeypatch, _stage_api())

    await open_page(user)

    assert settings_ui.TASKS_FILE_NOTICE in texts_on(user)


# --------------------------------------------------------------------------------------
# T045: 文言の完全一致（FR-007・J-22・N-18 の SA 分）
# --------------------------------------------------------------------------------------


async def _drive_open(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:open`: 既定の応答で開く（6 節の見出し・読取表示・入力欄・ボタン）。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)


async def _drive_member_confirmed(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:member-confirmed`: メンバ欄を出す。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_member_key(user, api)


async def _drive_tasks_confirmed(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:tasks-confirmed`: 段テンプレートの上書き欄を出す。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_tasks_key(user, api)


async def _drive_policies_confirmed(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:policies-confirmed`: 権限表・共通ルール・Hook の欄を出す。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _confirm_policies_key(user, api)


async def _drive_preset_local(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:preset-local`: 雛形 `local` を選ぶ（名前と接続先が置き換わる）。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    set_value(user, "profile_preset", "local")
    await user.should_see("local")


async def _drive_422(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:422`: 保存が 422（違反 1 件）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): failing(422, {"problems": [PROBLEM]})}),
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see(f"検査に落ちました（違反 1 件）。{settings_ui.UNCHANGED_NOTICE}")


async def _drive_404(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:404`: 保存が 404。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): failing(404, {"detail": "無い"})}),
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("404: 無い")


async def _drive_500(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:500`: 保存が 500（生の本文）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): failing(500, {"detail": "内部誤り"})}),
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("500: {'detail': '内部誤り'}")


async def _drive_unreachable_write(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:unreachable-write`: 保存が接続できない。"""
    api = use_api(monkeypatch, FakeApi(responses={("PUT", "/settings/humans"): unreachable()}))
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("app に接続できません: <urlopen error>")


async def _drive_success_next_kickoff(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:success-next-kickoff`: 保存の成功（次回 kickoff から）。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("保存しました。次回 kickoff から効きます（世代 7）")


async def _drive_success_immediate(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:success-immediate`: 保存の成功（即時に効く）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): saved(effect="immediate")}),
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("保存しました。権限ポリシーとコマンド定義は即時に効きます（世代 7）")


async def _drive_success_unknown(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:success-unknown`: 反映区分が未知の語。"""
    api = use_api(
        monkeypatch, FakeApi(responses={("PUT", "/settings/humans"): saved(effect="???")})
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("保存しました。（世代 7）")


async def _drive_success_no_headers(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:success-no-headers`: ヘッダの無い成功。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("PUT", "/settings/humans"): saved(effect=None, generation=None)}),
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("保存しました。")


async def _drive_json_unreadable(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:json-unreadable`: 段テンプレートの本文が JSON として読めない。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    fill(user, "tasks_global_body", "{壊れた")
    user.find(marker="tasks_global_save").click()
    await wait_until(
        lambda: any(
            text.startswith("JSON として読めません: ")
            for text in result_texts(user, "tasks_global_save")
        ),
        what="JSON が読めない旨",
    )


async def _drive_unreachable_bare(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai:unreachable-bare`: 説明の無い `status=0`（既定の文言が出る）。"""
    api = use_api(
        monkeypatch,
        FakeApi(
            responses={("PUT", "/settings/humans"): settings_ui.ApiResponse(status=0, payload=None)}
        ),
    )
    await open_page(user)
    await _save_humans(user, api)
    await user.should_see("app に接続できません")


SCENARIOS: dict[str, Callable[[User, pytest.MonkeyPatch], Awaitable[None]]] = {
    "ai:open": _drive_open,
    "ai:member-confirmed": _drive_member_confirmed,
    "ai:tasks-confirmed": _drive_tasks_confirmed,
    "ai:policies-confirmed": _drive_policies_confirmed,
    "ai:preset-local": _drive_preset_local,
    "ai:422": _drive_422,
    "ai:404": _drive_404,
    "ai:500": _drive_500,
    "ai:unreachable-write": _drive_unreachable_write,
    "ai:success-next-kickoff": _drive_success_next_kickoff,
    "ai:success-immediate": _drive_success_immediate,
    "ai:success-unknown": _drive_success_unknown,
    "ai:success-no-headers": _drive_success_no_headers,
    "ai:json-unreadable": _drive_json_unreadable,
    "ai:unreachable-bare": _drive_unreachable_bare,
}
"""文言の一覧の `scenario` → その文言を画面に出す駆動関数（AI 設定ページの分）。

⚠ **文言を 1 件も持たない場面は表に置かない** —— 409・最初の読取の接続不能・共通ルールの 404 は、
画面に出る文言が「{コード}: {detail}」「app に接続できません: {exc}」の**同じ雛形**なので、
一覧の上では 404・接続不能の場面が代表する。振る舞いそのものは個別のテスト
（`test_a_409_shows_the_single_detail_without_a_problems_table`・
`test_an_unreachable_first_read_stops_the_page`・`test_a_404_common_rules_empties_the_body_and_says_so`）
が見ている。"""


def wording(page: str = "ai") -> list[dict[str, object]]:
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
    """N-18・J-22: 一覧の `ai` の全件の `scenario` が駆動関数の表にある（空振りを防ぐ）。"""
    entries = wording()

    assert entries, "文言の一覧に `ai` の件が無い"
    missing = sorted({str(entry["scenario"]) for entry in entries} - set(SCENARIOS))

    assert not missing, f"駆動関数の無い scenario がある: {missing}"
    assert all(str(entry["scenario"]).startswith("ai:") for entry in entries)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
async def test_wording_appears_verbatim(
    user: User, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """N-18・FR-007・J-22: 一覧の各文言が部品の文字として完全一致で現れる。"""
    entries = [entry for entry in wording() if entry["scenario"] == scenario]
    assert entries, f"{scenario} に割り当てられた文言が無い"
    await SCENARIOS[scenario](user, monkeypatch)

    texts = sorted(texts_on(user))
    missing = [str(entry["id"]) for entry in entries if not matches(entry, texts)]

    assert not missing, f"{scenario} で出ない文言がある: {missing}\n画面の文字: {texts}"
