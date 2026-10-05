"""トリガー画面（NiceGUI）の検証（US2 AS-1〜AS-9・ui-pages N-12〜N-22）。

手段は NiceGUI の `user` フィクスチャ（ブラウザを立てない＝憲章 2.0.0 §8・FR-031）。

⚠ 規約 `contracts/ui-test-conventions.md` §2・§3 を守る。
1. `trigger.ui` はモジュール先頭で束ね、差し替えは `monkeypatch.setattr(trigger_ui, "call_api", …)`
   で行う（文字列経路の差し替えを使わない）。
2. ページ関数はテスト関数の中で定義し、描画関数を直接呼ぶ（apps/ui の殻を通さない）。
3. 固定の `sleep` を使わない（非同期の結果は `should_see` の待ちで確かめる）。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from nicegui import ElementFilter
from nicegui import ui as nui

from trigger import ui as trigger_ui

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from nicegui.element import Element
    from nicegui.testing import User

BASE_URL = "http://trigger-ui.invalid:9999"
"""画面に渡す接続先（実運用の値ではないことが一目で分かる形）。"""

WORDING_PATH = Path(__file__).with_name("trigger_ui_wording.json")
"""文言の一覧（`1dd3f09` の `trigger/ui.py` から抜き出した表示文字列）。"""

WATCHER_OK: dict[str, object] = {
    "scheduler_running": True,
    "running": False,
    "watchers": [{"trigger_id": "tr-file", "watch_dir": "/tmp/dl", "alive": True}],
}
"""既定の `GET /watcher` の本文。"""

TRIGGER_ROW: dict[str, object] = {
    "trigger_id": "tr-1",
    "name": "毎朝のバッチ",
    "description": "朝に流す",
    "type": "cron",
    "spec": "0 7 * * 1",
    "enabled": True,
    "is_default": False,
    "next_fire_at": "2026-09-28T07:00:00",
    "last_result": {"status": "finished", "finished_at": "2026-09-27T07:00:10"},
    "condition": {"labels": ["role:executor", "kind:batch"], "project_key": "pj1"},
}
"""既定の `GET /triggers` の 1 件目（既定トリガーではない）。"""

DEFAULT_TRIGGER_ROW: dict[str, object] = {
    "trigger_id": "tr-ready",
    "name": "既定の準備トリガー",
    "description": "",
    "type": "interval",
    "spec": "300",
    "enabled": True,
    "is_default": True,
    "next_fire_at": None,
    "last_result": None,
    "condition": {"labels": ["role:executor"], "project_key": None},
}
"""既定トリガー（削除できず、仕様と有効以外が読み取り専用になる）。"""

HISTORY_ROW: dict[str, object] = {
    "started_at": "2026-09-27T07:00:00",
    "finished_at": "2026-09-27T07:00:10",
    "trigger_name": "毎朝のバッチ",
    "kind": "trigger_spec",
    "status": "finished",
    "issues_ok": 2,
    "issues_failing": 0,
    "notes": "",
    "dropped_path": "",
    "run_id": "run-1",
    "kickoff_id": "kick-1",
    "extra_not_shown": "この列は出ない",
}
"""既定の `GET /history` の 1 件（15 列のうち 11 列だけが画面に出る）。"""

LIST_COLUMNS = (
    "名前",
    "説明",
    "種別",
    "仕様",
    "起動条件",
    "有効",
    "次回実行時刻",
    "直近結果",
    "既定",
)
"""一覧の 9 列（この順。`011/§3` タブ 1 の 8 列に D26 で「起動条件」を足した）。"""

WATCHER_COLUMNS = ("trigger_id", "watch_dir", "alive")
"""常駐の状態の監視先の 3 列。"""

OPERATION_BUTTONS = ("edit-save", "toggle-tr-1", "run-tr-1", "delete-tr-1")
"""書込中に押せなくなる操作ボタン（E-6・J-15）。"""


# --------------------------------------------------------------------------------------
# 共通の補助
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class FakeApi:
    """要求を記録する偽の `call_api`（唯一の差し替え点＝ ui-pages §3）。

    Attributes:
        responses: `(method, path)` → 応答。値を並びにすると、呼ばれた順に消費して最後の値が残る。
        calls: 送られた `(method, path, body)` の並び（`fetch` がスレッドから追記する）。
        gate: 設定すると、応答を返す前にこの `Event` が立つまで待つ（同時実行の検査）。
        gate_methods: `gate` で塞ぐメソッド（既定は GET 以外＝書込だけ）。
    """

    responses: dict[tuple[str, str], object] = field(default_factory=dict[tuple[str, str], object])
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])
    gate: threading.Event | None = None
    gate_methods: tuple[str, ...] = ()

    def __call__(
        self, _base_url: str, method: str, path: str, body: object | None = None
    ) -> trigger_ui.ApiResponse:
        """要求を記録して応答を返す。"""
        self.calls.append((method, path, body))
        if self.gate is not None and method in self.gate_methods:
            # ⚠ タイムアウトを付ける（立たなくてもテストが固まらない）
            self.gate.wait(timeout=10.0)
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

    def _response_for(self, method: str, path: str) -> trigger_ui.ApiResponse:
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
                queued = cast("list[trigger_ui.ApiResponse]", found)
                return queued.pop(0) if len(queued) > 1 else queued[0]
            return cast("trigger_ui.ApiResponse", found)
        return _default_response(method, path)


def _default_response(method: str, path: str) -> trigger_ui.ApiResponse:
    """応答表に無いときの既定（正常系の最小の形）。

    Args:
        method: HTTP メソッド。
        path: 経路。

    Returns:
        応答。
    """
    if method != "GET":
        return ok({"run_id": "run-9"})
    bare = path.split("?")[0]
    if bare == "/watcher":
        return ok(WATCHER_OK)
    if bare == "/triggers":
        return ok([TRIGGER_ROW])
    if bare == "/history":
        return ok([HISTORY_ROW])
    return ok(None)


def ok(payload: object) -> trigger_ui.ApiResponse:
    """200 の応答を作る。

    Args:
        payload: 本文。

    Returns:
        応答。
    """
    return trigger_ui.ApiResponse(status=200, payload=payload)


def failing(status: int, payload: object) -> trigger_ui.ApiResponse:
    """2xx でない応答を作る。

    Args:
        status: 応答コード。
        payload: 本文。

    Returns:
        応答。
    """
    return trigger_ui.ApiResponse(status=status, payload=payload)


def unreachable() -> trigger_ui.ApiResponse:
    """接続できない応答（`status=0`）を作る。

    Returns:
        応答。
    """
    return trigger_ui.ApiResponse(
        status=0, payload=None, error="app に接続できません: <urlopen error>"
    )


async def open_page(user: User) -> None:
    """トリガー画面を開く（殻を通さず描画関数を直接呼ぶ＝規約 §2）。

    Args:
        user: `user` フィクスチャ。
    """

    @nui.page("/")
    async def _page() -> None:  # pyright: ignore[reportUnusedFunction]  # `@nui.page` が経路に登録するので、名前は参照されない
        await trigger_ui.render_triggers(BASE_URL)

    await user.open("/")


def use_api(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> FakeApi:
    """偽の `call_api` を差し込む。

    Args:
        monkeypatch: 差し替えの後始末を持つ pytest の道具。
        api: 差し込む偽物。

    Returns:
        差し込んだ偽物（そのまま使えるように返す）。
    """
    monkeypatch.setattr(trigger_ui, "call_api", api)
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
    element = one(user, marker)
    setter = getattr(element, "set_value", None)
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


async def confirm(user: User, marker: str, text: str, *, event: str = "keydown.enter") -> None:
    """テキスト欄に入れて確定する（regulation §2・data-model §6）。

    Args:
        user: `user` フィクスチャ。
        marker: 印。
        text: 入れる文字（空文字なら消す）。
        event: 確定を起こす出来事（`keydown.enter` か `blur`）。
    """
    interaction = user.find(marker=marker)
    interaction.clear()
    if text:
        interaction.type(text)
    interaction.trigger(event)


async def wait_until(condition: Callable[[], bool], *, what: str, retries: int = 200) -> None:
    """条件が満たされるまで上限つきで待つ（固定の `sleep` を使わない）。

    ⚠ `should_see` は**すでに満たされている条件では待受に譲らない**ので、非同期の処理
    （ボタンの処理・確定の読取）の観測にはこちらを使う。前の操作の文言が残っている間に
    `should_see` で待つと、背景の処理が走る前に通り抜ける（2026-09-27 実測）。

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


def result_texts(user: User) -> set[str]:
    """上部の結果表示（印 `result`）の中の文字を集める。

    Args:
        user: `user` フィクスチャ。

    Returns:
        結果表示の中の文字の集合。
    """
    found: set[str] = set()
    for element in one(user, "result").descendants():
        props: dict[str, object] = dict(element.props)
        for key in ("text", "label"):
            found.update(_strings_of(props.get(key)))
        found.update(_strings_of(getattr(element, "text", None)))
    return found


# --------------------------------------------------------------------------------------
# T022: 常駐の状態（US2 AS-1・AS-9・N-14・N-15・N-20・D-6・J-11）
# --------------------------------------------------------------------------------------


async def test_watcher_section_shows_scheduler_and_running(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-15・US2 AS-1: 見出しの下に「スケジューラ: … / 進行中の発火: …」の 1 行が出る。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "常駐の状態" in texts
    assert "スケジューラ: 稼働中 / 進行中の発火: なし" in texts


@pytest.mark.parametrize(
    ("scheduler_running", "running", "expected"),
    [
        (True, True, "スケジューラ: 稼働中 / 進行中の発火: あり"),
        (True, False, "スケジューラ: 稼働中 / 進行中の発火: なし"),
        (False, True, "スケジューラ: 停止 / 進行中の発火: あり"),
        (False, False, "スケジューラ: 停止 / 進行中の発火: なし"),
    ],
)
async def test_watcher_section_covers_the_four_combinations(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    scheduler_running: bool,
    running: bool,
    expected: str,
) -> None:
    """N-15: 真偽の 4 通りで「稼働中／停止」「あり／なし」が入れ替わる。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/watcher"): ok(
                    {"scheduler_running": scheduler_running, "running": running, "watchers": []}
                )
            }
        ),
    )

    await open_page(user)

    assert expected in texts_on(user)


async def test_watcher_section_lists_the_watched_folders(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-15: 監視先の表が 3 列で出る（列の順は `trigger_id`・`watch_dir`・`alive`）。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    columns, rows = table_of(user, "watchers-table")
    assert tuple(columns) == WATCHER_COLUMNS
    assert rows == [{"trigger_id": "tr-file", "watch_dir": "/tmp/dl", "alive": "True"}]


async def test_watcher_section_says_so_when_no_folder_is_watched(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-15: 監視先 0 件なら表の代わりに文言を出す（旧 :221-223）。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/watcher"): ok(
                    {"scheduler_running": True, "running": False, "watchers": []}
                )
            }
        ),
    )

    await open_page(user)

    assert "監視中のフォルダはありません" in texts_on(user)
    with pytest.raises(AssertionError):
        one(user, "watchers-table")


async def test_watcher_section_warns_when_a_watcher_is_dead(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-15・US2 AC6: `alive=false` が 1 件でもあれば復旧しない旨を添える（旧 :301）。"""
    dead = {
        "scheduler_running": True,
        "running": False,
        "watchers": [{"trigger_id": "tr-file", "watch_dir": "/tmp/dl", "alive": False}],
    }
    use_api(monkeypatch, FakeApi(responses={("GET", "/watcher"): ok(dead)}))

    await open_page(user)

    assert trigger_ui.NO_RECOVERY_NOTICE in texts_on(user)


async def test_watcher_section_is_quiet_while_the_watcher_lives(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-15: 生きている間は予告を出さない（旧 :315）。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    assert trigger_ui.NO_RECOVERY_NOTICE not in texts_on(user)


async def test_page_shows_only_the_connection_error_when_the_watcher_is_unreachable(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-14・`011/§4`: `GET /watcher` が `status=0` なら接続できない旨だけを出し、
    以降を描かない（旧 :283）。
    """
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/watcher"): unreachable()}))

    await open_page(user)

    texts = texts_on(user)
    assert "app に接続できません: <urlopen error>" in texts
    assert "一覧" not in texts, "接続できないのに 3 区画を描いている"
    assert "常駐の状態" not in texts
    assert api.paths() == ["/watcher"], "以降の読取を送っている"


async def test_page_survives_a_raising_call_api(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-14・US2 AS-9: `call_api` が `URLError` を投げても画面が落ちない（旧 :269）。"""

    def _raise(_base_url: str, _method: str, _path: str, _body: object | None = None) -> object:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(trigger_ui, "call_api", _raise)

    await open_page(user)

    assert any(text.startswith("app に接続できません") for text in texts_on(user))


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (500, {"detail": "内部誤り"}, "500: {'detail': '内部誤り'}"),
        (404, {"detail": "見つかりません"}, "404: 見つかりません"),
    ],
)
async def test_watcher_section_shows_the_response_and_keeps_the_three_panels(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: object,
    expected: str,
) -> None:
    """D-6・J-11・E-3: `status` が 0 以外の非 2xx なら応答だけを出し、3 区画は描く。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/watcher"): failing(status, payload)}))

    await open_page(user)

    texts = texts_on(user)
    assert "常駐の状態" in texts
    assert expected in texts
    assert "スケジューラ: 稼働中 / 進行中の発火: なし" not in texts
    for panel in ("一覧", "追加／編集", "履歴"):
        assert panel in texts, f"区画 {panel} が描かれていない"


# --------------------------------------------------------------------------------------
# T023: 一覧の区画（US2 AS-2・N-12）
# --------------------------------------------------------------------------------------


async def test_listing_shows_the_count_and_nine_columns(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-12・US2 AS-2・D26: 件数のキャプションと 9 列の表が出る（列の順は契約どおり）。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "登録トリガー 1 件" in texts
    columns, rows = table_of(user, "triggers-table")
    assert tuple(columns) == LIST_COLUMNS
    assert rows == [
        {
            "名前": "毎朝のバッチ",
            "説明": "朝に流す",
            "種別": "cron",
            "仕様": "0 7 * * 1",
            "起動条件": "ラベル: role:executor kind:batch ／ PJ: pj1",
            "有効": "True",
            "次回実行時刻": "2026-09-28T07:00:00",
            "直近結果": "finished（2026-09-27T07:00:10）",
            "既定": "False",
        }
    ]


async def test_a_condition_without_a_project_targets_every_project(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 起動条件に PJ の指定が無ければ「全て」と出す（既定トリガーの形）。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([DEFAULT_TRIGGER_ROW])}))

    await open_page(user)

    _columns, rows = table_of(user, "triggers-table")
    assert rows[0]["起動条件"] == "ラベル: role:executor ／ PJ: 全て"


async def test_the_mechanism_is_explained_with_a_link_to_the_templates(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26: 定義ファイルの所在・発火後の流れ・段テンプレートへのリンクを出す。

    静的な説明なので、要求は増やさない。
    """
    api = use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert trigger_ui.MECHANISM_HEADING in texts
    assert trigger_ui.TRIGGER_FILE_NOTICE in texts
    assert set(trigger_ui.FLOW_STEPS) <= texts
    link = one(user, "prompt-link")
    assert getattr(link, "text", None) == trigger_ui.PROMPT_LINK_TEXT
    props: dict[str, object] = dict(link.props)
    assert props.get("href") == "/settings-ai"
    assert all(
        path in ("/watcher", "/triggers") or path.startswith("/history") for path in api.paths()
    )


async def test_the_mechanism_is_not_drawn_when_the_app_is_unreachable(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US6 AC3: 接続できないときは、その旨だけを出す（仕組みの説明も描かない）。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/watcher"): unreachable()}))

    await open_page(user)

    assert trigger_ui.MECHANISM_HEADING not in texts_on(user)


async def test_listing_shows_a_heading_per_row(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """N-12・ui-pages §4 の書き換え 5: 行ごとの見出しは「{name}（{trigger_id}）」
    （`**` を付けない）。
    """
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    texts = texts_on(user)
    assert "毎朝のバッチ（tr-1）" in texts
    assert "**毎朝のバッチ**（tr-1）" not in texts
    assert "次回実行時刻: 2026-09-28T07:00:00 / 直近結果: finished（2026-09-27T07:00:10）" in texts


@pytest.mark.parametrize(
    ("last_result", "next_fire_at", "expected_result", "expected_next"),
    [
        ({"status": "finished", "finished_at": "x"}, "y", "finished（x）", "y"),
        ({"status": "finished", "finished_at": None}, None, "finished（—）", "—"),
        (None, None, "—", "—"),
        ({"status": "started", "finished_at": None}, None, "実行中／未終了", "—"),
    ],
)
async def test_listing_renders_the_last_result_and_next_fire(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    last_result: object,
    next_fire_at: object,
    expected_result: str,
    expected_next: str,
) -> None:
    """N-12・US5 AC2: 直近結果と次回実行時刻の 4 通り（旧 :270-278）。"""
    row = {**TRIGGER_ROW, "last_result": last_result, "next_fire_at": next_fire_at}
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([row])}))

    await open_page(user)

    _, rows = table_of(user, "triggers-table")
    assert rows[0]["直近結果"] == expected_result
    assert rows[0]["次回実行時刻"] == expected_next


async def test_listing_table_is_empty_without_a_basic_no_data_label(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J-19: 0 件の表に UI 基盤の既定の文字（「No data available」）を出さない。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([])}))

    await open_page(user)

    assert "登録トリガー 0 件" in texts_on(user)
    assert "No data available" not in texts_on(user)
    props: dict[str, object] = dict(one(user, "triggers-table").props)
    assert props.get("no-data-label") == ""


@pytest.mark.parametrize(
    ("enabled", "label", "expected_body"),
    [(True, "無効にする", {"enabled": False}), (False, "有効にする", {"enabled": True})],
)
async def test_listing_toggle_sends_one_patch(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    label: str,
    expected_body: dict[str, bool],
) -> None:
    """N-12・US2 AS-2: 切替は `PATCH /triggers/{id}` を 1 本だけ送り、ボタン名が状態で変わる。"""
    row = {**TRIGGER_ROW, "enabled": enabled}
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([row])}))
    await open_page(user)
    assert label in texts_on(user)

    user.find(marker="toggle-tr-1").click()
    await user.should_see("受け付けました")

    assert api.writes() == [("PATCH", "/triggers/tr-1", expected_body)]


async def test_listing_run_sends_one_post_and_shows_the_run_id(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-12・US2 AS-2: 手動実行は `POST /triggers/{id}/run` を 1 本送り、`run_id` を出す。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    user.find(marker="run-tr-1").click()
    await user.should_see("受け付けました（run_id: run-9）")

    assert api.writes() == [("POST", "/triggers/tr-1/run", None)]


async def test_listing_delete_sends_one_delete(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """N-12・US2 AS-2: 削除は `DELETE /triggers/{id}` を 1 本送る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    user.find(marker="delete-tr-1").click()
    await user.should_see("受け付けました")

    assert api.writes() == [("DELETE", "/triggers/tr-1", None)]


async def test_listing_cannot_delete_the_default_trigger(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-12: 既定トリガーの削除ボタンは押せない（`011/§3`）。"""
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/triggers"): ok([TRIGGER_ROW, DEFAULT_TRIGGER_ROW])}),
    )
    await open_page(user)

    assert is_disabled(user, "delete-tr-ready")
    assert not is_disabled(user, "delete-tr-1")
    user.find(marker="delete-tr-ready").click()

    assert api.writes() == [], "押せないはずのボタンで要求が送られた"


# --------------------------------------------------------------------------------------
# T024: 追加／編集の区画（US2 AS-3〜AS-5・E-1・E-4・N-17・N-22）
# --------------------------------------------------------------------------------------

EDIT_FIELDS = (
    "edit-name",
    "edit-description",
    "edit-type",
    "edit-spec",
    "edit-labels",
    "edit-project",
    "edit-enabled",
)
"""追加／編集の 7 項目の印（data-model §6）。"""

NEW_DEFAULTS: dict[str, object] = {
    "edit-name": "",
    "edit-description": "",
    "edit-type": "interval",
    "edit-spec": "",
    "edit-labels": "role:executor",
    "edit-project": "",
    "edit-enabled": True,
}
"""「（新規追加）」のときの既定値（data-model §6）。"""

OTHER_ROW: dict[str, object] = {
    "trigger_id": "tr-2",
    "name": "夜のバッチ",
    "description": "夜に流す",
    "type": "file",
    "spec": "inbox",
    "enabled": True,
    "is_default": False,
    "next_fire_at": None,
    "last_result": None,
    "condition": {"labels": ["role:reviewer"], "project_key": None},
}
"""2 件目（切り替えの検査で使う）。"""

DISABLED_ROW: dict[str, object] = {**TRIGGER_ROW, "enabled": False}
"""ラベル・プロジェクトキー・有効が**すべて既定値と異なる**相手（US2 AS-3）。"""


def label_of(row: dict[str, object]) -> str:
    """一覧の表示名（選択肢のラベル）を作る。

    Args:
        row: トリガー 1 件。

    Returns:
        `{name}（{trigger_id}）`。
    """
    return f"{row['name']}（{row['trigger_id']}）"


async def test_edit_target_options_start_with_the_new_entry(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1: 選択肢は「（新規追加）」＋一覧の表示名で、初期の選択は「（新規追加）」。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([TRIGGER_ROW, OTHER_ROW])}))

    await open_page(user)

    options = getattr(one(user, "edit-target"), "options", None)
    assert options == ["（新規追加）", label_of(TRIGGER_ROW), label_of(OTHER_ROW)]
    assert value_of(user, "edit-target") == "（新規追加）"
    for marker, expected in NEW_DEFAULTS.items():
        assert value_of(user, marker) == expected, f"{marker} の既定値が違う"


async def test_edit_target_loads_the_seven_fields_of_the_selected_trigger(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1・US2 AS-3: 既定値と**異なる**相手を選ぶと 7 項目がその値になる。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([DISABLED_ROW])}))
    await open_page(user)

    set_value(user, "edit-target", label_of(DISABLED_ROW))
    await user.should_see("毎朝のバッチ")

    assert value_of(user, "edit-name") == "毎朝のバッチ"
    assert value_of(user, "edit-description") == "朝に流す"
    assert value_of(user, "edit-type") == "cron"
    assert value_of(user, "edit-spec") == "0 7 * * 1"
    assert value_of(user, "edit-labels") == "role:executor kind:batch"
    assert value_of(user, "edit-project") == "pj1"
    assert value_of(user, "edit-enabled") is False


async def test_edit_target_reloads_when_switching_to_another_trigger(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1: 別のトリガーに切り替えると 7 項目を読み込み直す。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([TRIGGER_ROW, OTHER_ROW])}))
    await open_page(user)

    set_value(user, "edit-target", label_of(TRIGGER_ROW))
    set_value(user, "edit-target", label_of(OTHER_ROW))
    await user.should_see("夜のバッチ")

    assert value_of(user, "edit-name") == "夜のバッチ"
    assert value_of(user, "edit-type") == "file"
    assert value_of(user, "edit-spec") == "inbox"
    assert value_of(user, "edit-labels") == "role:reviewer"
    assert value_of(user, "edit-project") == ""


async def test_edit_target_back_to_new_restores_the_defaults(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1: 「（新規追加）」に戻すと 7 項目が既定値に戻る。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([DISABLED_ROW])}))
    await open_page(user)

    set_value(user, "edit-target", label_of(DISABLED_ROW))
    set_value(user, "edit-target", "（新規追加）")
    await user.should_see("（新規追加）")

    for marker, expected in NEW_DEFAULTS.items():
        assert value_of(user, marker) == expected, f"{marker} が既定値に戻っていない"


@pytest.mark.parametrize("kind", [None, "unknown-kind"])
async def test_edit_type_falls_back_to_interval(
    user: User, monkeypatch: pytest.MonkeyPatch, kind: object
) -> None:
    """E-1・data-model §6: `type` が未設定・未知の語なら `interval` にする。"""
    row = {**TRIGGER_ROW, "type": kind}
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([row])}))
    await open_page(user)

    set_value(user, "edit-target", label_of(row))
    await user.should_see("毎朝のバッチ")

    assert value_of(user, "edit-type") == "interval"


async def test_edit_save_posts_a_new_trigger(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-1・US2 AS-4: 「（新規追加）」のままなら `POST /triggers` に 7 項目を送る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    user.find(marker="edit-name").type("新しい")
    user.find(marker="edit-spec").type("60")
    user.find(marker="edit-save").click()
    await user.should_see("受け付けました")

    assert api.writes() == [
        (
            "POST",
            "/triggers",
            {
                "name": "新しい",
                "description": "",
                "type": "interval",
                "spec": "60",
                "condition": {"labels": ["role:executor"], "project_key": None},
                "enabled": True,
            },
        )
    ]


async def test_edit_save_puts_an_existing_trigger(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1・US2 AS-4: 既存を選んでいれば `PUT /triggers/{id}` に 7 項目を送る。"""
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([DISABLED_ROW])}))
    await open_page(user)
    set_value(user, "edit-target", label_of(DISABLED_ROW))
    await user.should_see("毎朝のバッチ")

    user.find(marker="edit-save").click()
    await user.should_see("受け付けました")

    assert api.writes() == [
        (
            "PUT",
            "/triggers/tr-1",
            {
                "name": "毎朝のバッチ",
                "description": "朝に流す",
                "type": "cron",
                "spec": "0 7 * * 1",
                "condition": {
                    "labels": ["role:executor", "kind:batch"],
                    "project_key": "pj1",
                },
                "enabled": False,
            },
        )
    ]


async def test_edit_default_trigger_locks_five_fields_and_warns(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-17・`011/§3`: 既定トリガーは仕様と有効以外の 5 項目が読み取り専用で予告が出る。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/triggers"): ok([DEFAULT_TRIGGER_ROW])}),
    )
    await open_page(user)

    set_value(user, "edit-target", label_of(DEFAULT_TRIGGER_ROW))
    await user.should_see(trigger_ui.DEFAULT_TRIGGER_NOTICE)

    for marker in ("edit-name", "edit-description", "edit-type", "edit-labels", "edit-project"):
        assert is_disabled(user, marker), f"{marker} が読み取り専用でない"
    assert not is_disabled(user, "edit-spec")
    assert not is_disabled(user, "edit-enabled")


@pytest.mark.parametrize("kind", ["cron", "interval", "file"])
async def test_edit_shows_the_spec_help_of_the_selected_type(
    user: User, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """E-4・US2 AS-5: 種別ごとの入力補助が描画後の文字で出る（cron・file は書き換え済み）。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)

    set_value(user, "edit-type", kind)
    await user.should_see(trigger_ui.SPEC_HELP[kind])

    assert trigger_ui.SPEC_HELP[kind] in texts_on(user)
    assert "**" not in trigger_ui.SPEC_HELP[kind]
    assert "`" not in trigger_ui.SPEC_HELP[kind]


async def test_edit_keeps_user_input_and_updates_only_changed_fields_on_reload(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-22・J-10: 取り直しで X の値が変わったら、変わった項目だけを書き換える。"""
    changed = {**TRIGGER_ROW, "enabled": False}
    api = use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/triggers"): [ok([TRIGGER_ROW]), ok([changed])]}),
    )
    await open_page(user)
    set_value(user, "edit-target", label_of(TRIGGER_ROW))
    await user.should_see("毎朝のバッチ")
    user.find(marker="edit-description").clear()
    user.find(marker="edit-description").type("手で書いた説明")

    user.find(marker="toggle-tr-1").click()
    await user.should_see("受け付けました")

    assert value_of(user, "edit-enabled") is False, "サーバ側で変わった項目が書き換わっていない"
    assert value_of(user, "edit-description") == "手で書いた説明", "手入力が消えた"
    assert api.paths("GET").count("/triggers") == 2


async def test_edit_falls_back_to_new_when_the_target_disappears(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-22・J-10: 取り直しで選んでいた相手が消えたら「（新規追加）」と既定値に戻る。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/triggers"): [ok([TRIGGER_ROW]), ok([])]}),
    )
    await open_page(user)
    set_value(user, "edit-target", label_of(TRIGGER_ROW))
    await user.should_see("毎朝のバッチ")

    user.find(marker="edit-save").click()
    await user.should_see("登録トリガー 0 件")

    assert value_of(user, "edit-target") == "（新規追加）"
    for marker, expected in NEW_DEFAULTS.items():
        assert value_of(user, marker) == expected, f"{marker} が既定値に戻っていない"


async def test_edit_keeps_input_while_the_same_target_stays_selected(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1: 同じ相手を選び続ける間は入力を消さない。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/triggers"): [ok([TRIGGER_ROW]), ok([TRIGGER_ROW])]}),
    )
    await open_page(user)
    set_value(user, "edit-target", label_of(TRIGGER_ROW))
    await user.should_see("毎朝のバッチ")
    user.find(marker="edit-spec").clear()
    user.find(marker="edit-spec").type("30 8 * * *")

    user.find(marker="run-tr-1").click()
    await user.should_see("受け付けました（run_id: run-9）")

    assert value_of(user, "edit-spec") == "30 8 * * *"
    assert value_of(user, "edit-target") == label_of(TRIGGER_ROW)


# --------------------------------------------------------------------------------------
# T025: 操作の結果・取り直し・同時実行（US2 AS-6・AS-7・E-2・E-3・E-5・E-6・N-13・N-20・N-21）
# --------------------------------------------------------------------------------------

PROBLEM = {"where": "/tmp/x/triggers.yaml", "kind": "V-3-固有の語", "reason": "種別が不正です"}
"""422 の違反 1 件（`kind` が画面に出ないことを見るため、固有の語を入れる）。"""


@pytest.mark.parametrize("marker", ["edit-save", "toggle-tr-1", "run-tr-1", "delete-tr-1"])
async def test_operation_success_reloads_the_three_reads_once_each(
    user: User, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    """E-2・US2 AS-6: 成功したら 3 つの読取をそれぞれ 1 回だけ取り直す。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    opened = list(api.paths("GET"))

    user.find(marker=marker).click()
    await user.should_see("受け付けました")

    after = api.paths("GET")[len(opened) :]
    assert sorted(after) == ["/history", "/triggers", "/watcher"], (
        f"取り直しの読取が 3 つ 1 回ずつでない: {after}"
    )


async def test_operation_success_shows_the_message_once_in_the_result_area(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-2・J-20: 成功の旨は上部の結果表示に 1 つだけ出て、取り直しで重複しない。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)

    user.find(marker="run-tr-1").click()
    await user.should_see("受け付けました（run_id: run-9）")

    inside = [
        element
        for element in one(user, "result").descendants()
        if "受け付けました（run_id: run-9）" in str(getattr(element, "text", ""))
    ]
    assert len(inside) == 1, f"結果表示に成功の文言が {len(inside)} 個ある"


async def test_operation_result_is_replaced_by_the_next_operation(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-2・J-20: 次の操作の結果で前の結果が置き換わる。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("DELETE", "/triggers/tr-1"): failing(404, {"detail": "見つかりません"}),
            }
        ),
    )
    await open_page(user)

    user.find(marker="run-tr-1").click()
    await user.should_see("受け付けました（run_id: run-9）")
    user.find(marker="delete-tr-1").click()
    await user.should_see("404: 見つかりません")

    texts = result_texts(user)
    assert "404: 見つかりません" in texts
    assert "受け付けました（run_id: run-9）" not in texts


async def test_operation_422_lists_where_and_reason_without_kind(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-13・US2 AS-7: 422 は件数つきの文言と「{where}: {reason}」を出し、`kind` は出さない。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("POST", "/triggers"): failing(422, {"problems": [PROBLEM, PROBLEM]}),
            }
        ),
    )
    await open_page(user)

    user.find(marker="edit-save").click()
    await user.should_see(
        "検査に落ちました（違反 2 件）。宣言ファイルは 1 バイトも変更されていません"
    )

    texts = result_texts(user)
    assert "/tmp/x/triggers.yaml: 種別が不正です" in texts
    assert "- /tmp/x/triggers.yaml: 種別が不正です" not in texts, "行頭の `- ` が残っている"
    assert "V-3-固有の語" not in "\n".join(texts_on(user)), "`kind` が画面に出ている"


async def test_operation_422_without_problems_shows_zero_and_the_raw_body(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-3: `problems` を持たない 422 は「（違反 0 件）」と生の本文を出す。"""
    body = {"detail": "形が違う"}
    use_api(monkeypatch, FakeApi(responses={("POST", "/triggers"): failing(422, body)}))
    await open_page(user)

    user.find(marker="edit-save").click()
    await user.should_see(
        "検査に落ちました（違反 0 件）。宣言ファイルは 1 バイトも変更されていません"
    )

    assert str(body) in result_texts(user)


async def test_operation_500_shows_the_status_and_the_raw_payload(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-3: 契約に無い応答は「{status}: {str(payload)}」を出す。"""
    payload = {"detail": "内部誤り"}
    use_api(
        monkeypatch,
        FakeApi(responses={("POST", "/triggers/tr-1/run"): failing(500, payload)}),
    )
    await open_page(user)

    user.find(marker="run-tr-1").click()
    await user.should_see(f"500: {payload}")

    assert f"500: {payload}" in result_texts(user)


async def test_operation_unreachable_keeps_the_page(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-5: 操作が接続に失敗したら結果表示に出し、ページはそのまま残る。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("PATCH", "/triggers/tr-1"): unreachable()}),
    )
    await open_page(user)

    user.find(marker="toggle-tr-1").click()
    await user.should_see("app に接続できません: <urlopen error>")

    assert "app に接続できません: <urlopen error>" in result_texts(user)
    assert "登録トリガー 1 件" in texts_on(user), "ページが消えた"
    assert "常駐の状態" in texts_on(user)


async def test_read_failures_stay_inside_their_panel(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-5・J-12: 一覧・履歴の読取の失敗は上部の結果表示ではなく各区画の中に出る。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/triggers"): failing(500, {"detail": "一覧が読めない"}),
                ("GET", "/history"): failing(500, {"detail": "履歴が読めない"}),
            }
        ),
    )

    await open_page(user)

    texts = texts_on(user)
    assert "500: {'detail': '一覧が読めない'}" in texts
    assert "500: {'detail': '履歴が読めない'}" in texts
    assert not result_texts(user), f"読取の失敗が上部に出ている: {result_texts(user)}"


async def test_operation_buttons_are_locked_while_waiting(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-6・N-21・J-15: 書込の応答を待つ間はページの操作ボタンをすべて押せなくする。"""
    gate = threading.Event()
    use_api(
        monkeypatch,
        FakeApi(gate=gate, gate_methods=("POST", "PUT", "PATCH", "DELETE")),
    )
    await open_page(user)
    assert not any(is_disabled(user, marker) for marker in OPERATION_BUTTONS)

    try:
        user.find(marker="run-tr-1").click()
        await wait_until(lambda: is_disabled(user, "run-tr-1"), what="書込の開始")
        for marker in OPERATION_BUTTONS:
            assert is_disabled(user, marker), f"{marker} が押せるままになっている"
    finally:
        gate.set()

    await user.should_see("受け付けました（run_id: run-9）")
    for marker in OPERATION_BUTTONS:
        assert not is_disabled(user, marker), f"{marker} が押せる状態に戻っていない"


# --------------------------------------------------------------------------------------
# T026: 履歴の区画（US2 AS-8・N-17・SC-001a）
# --------------------------------------------------------------------------------------

HISTORY_COLUMNS_EXPECTED = (
    "started_at",
    "finished_at",
    "trigger_name",
    "kind",
    "status",
    "issues_ok",
    "issues_failing",
    "notes",
    "dropped_path",
    "run_id",
    "kickoff_id",
)
"""履歴の 11 列（この順）。"""


async def test_history_reads_without_a_month_when_opened(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US2 AS-8 (1): 開いたときは `month` なしで `GET /history` を送り、月の欄は空。"""
    api = use_api(monkeypatch, FakeApi())

    await open_page(user)

    assert "/history" in api.paths("GET")
    assert not [path for path in api.paths("GET") if path.startswith("/history?")]
    assert value_of(user, "history-month") == ""
    assert "月（YYYYMM。空なら当月）" in texts_on(user)


@pytest.mark.parametrize("event", ["keydown.enter", "blur"])
async def test_history_reads_the_given_month_on_confirm(
    user: User, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    """US2 AS-8 (2): 月を入れて確定すると `GET /history?month=YYYYMM` を送る（Enter と blur）。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await confirm(user, "history-month", "202609", event=event)

    await wait_until(lambda: "/history?month=202609" in api.paths("GET"), what="月を指定した読取")


async def test_history_reads_without_a_month_after_clearing_it(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US2 AS-8: 月を空に戻して確定すると `month` なしに戻る。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)
    await confirm(user, "history-month", "202609")
    await wait_until(lambda: "/history?month=202609" in api.paths("GET"), what="月を指定した読取")

    await confirm(user, "history-month", "")
    await wait_until(lambda: api.paths("GET").count("/history") == 2, what="月なしに戻した読取")

    assert api.paths("GET")[-1] == "/history"


async def test_history_shows_eleven_columns_in_order(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US2 AS-8 (3): 履歴の表は 11 列で、15 列のうち画面に出ない列は無い。"""
    use_api(monkeypatch, FakeApi())

    await open_page(user)

    columns, rows = table_of(user, "history-table")
    assert tuple(columns) == HISTORY_COLUMNS_EXPECTED
    assert "extra_not_shown" not in columns
    assert rows[0]["status"] == "finished"


async def test_history_marks_unfinished_rows(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """US2 AS-8 (4)・US5 AC2: `status=started` の行は「started（実行中／未終了）」になる。"""
    row = {**HISTORY_ROW, "status": "started", "finished_at": None}
    use_api(monkeypatch, FakeApi(responses={("GET", "/history"): ok([row])}))

    await open_page(user)

    _, rows = table_of(user, "history-table")
    assert rows[0]["status"] == "started（実行中／未終了）"
    assert "1 件が 実行中／未終了 です（プロセスが落ちた run を含む）" in texts_on(user)


async def test_history_says_so_for_a_month_without_records(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US2 AS-8 (5)・§0-3 の例外: 記録の無い月は表の代わりに文言を出す（現物のまま）。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/history"): ok([])}))

    await open_page(user)

    assert "この月の実行履歴はありません" in texts_on(user)
    with pytest.raises(AssertionError):
        one(user, "history-table")


# --------------------------------------------------------------------------------------
# T027: 符号化（D-5・J-9・N-19）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("2026 09", "/history?month=2026%2009"), ("20#26", "/history?month=20%2326")],
)
async def test_history_month_is_percent_encoded(
    user: User, monkeypatch: pytest.MonkeyPatch, typed: str, expected: str
) -> None:
    """D-5・N-19: 月の欄の空白・`#` はクエリの値として符号化され、例外にならない。"""
    api = use_api(monkeypatch, FakeApi())
    await open_page(user)

    await confirm(user, "history-month", typed)

    await wait_until(
        lambda: expected in api.paths("GET"),
        what=f"符号化された経路 {expected}（実際: {api.paths('GET')}）",
    )


async def test_trigger_id_segments_are_percent_encoded(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-5・N-19: `trigger_id` に `?`・`#` を含む行でも、経路の段が符号化される。"""
    row = {**TRIGGER_ROW, "trigger_id": "tr?1#x"}
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([row])}))
    await open_page(user)

    user.find(marker="toggle-tr?1#x").click()
    await wait_until(lambda: len(api.writes()) == 1, what="切替の要求")
    user.find(marker="run-tr?1#x").click()
    await wait_until(lambda: len(api.writes()) == 2, what="手動実行の要求")
    user.find(marker="delete-tr?1#x").click()
    await wait_until(lambda: len(api.writes()) == 3, what="削除の要求")

    assert [path for _, path, _ in api.writes()] == [
        "/triggers/tr%3F1%23x",
        "/triggers/tr%3F1%23x/run",
        "/triggers/tr%3F1%23x",
    ]


async def test_plain_values_keep_the_paths_unchanged(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-5・J-9: 英数・`-`・`_` だけの値では、要求の経路が移行前と同じ文字列になる。"""
    row = {**TRIGGER_ROW, "trigger_id": "tr-1_a9"}
    api = use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([row])}))
    await open_page(user)

    user.find(marker="run-tr-1_a9").click()
    await wait_until(lambda: len(api.writes()) == 1, what="手動実行の要求")
    await confirm(user, "history-month", "202609")
    await wait_until(lambda: "/history?month=202609" in api.paths("GET"), what="月を指定した読取")

    assert ("POST", "/triggers/tr-1_a9/run", None) in api.calls


# --------------------------------------------------------------------------------------
# T028: 文言の完全一致（FR-007・J-22・N-18 の TP 分）
# --------------------------------------------------------------------------------------


async def _drive_open(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:open`: 既定の応答で開く（見出し・3 区画・一覧・常駐状態・履歴）。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/triggers"): ok([TRIGGER_ROW]),
                ("GET", "/history"): ok([{**HISTORY_ROW, "status": "started"}]),
            }
        ),
    )
    await open_page(user)


async def _drive_empty(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:empty`: 監視先 0 件・一覧 0 件・記録の無い月。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/watcher"): ok(
                    {"scheduler_running": False, "running": True, "watchers": []}
                ),
                ("GET", "/triggers"): ok([]),
                ("GET", "/history"): ok([]),
            }
        ),
    )
    await open_page(user)


async def _drive_dead_watcher(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:dead-watcher`: 監視スレッドが停止している。"""
    dead = {
        "scheduler_running": True,
        "running": False,
        "watchers": [{"trigger_id": "tr-file", "watch_dir": "/tmp/dl", "alive": False}],
    }
    use_api(monkeypatch, FakeApi(responses={("GET", "/watcher"): ok(dead)}))
    await open_page(user)


async def _drive_unreachable(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:unreachable`: 最初の `GET /watcher` が接続できない。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/watcher"): unreachable()}))
    await open_page(user)


async def _drive_422(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:422`: 保存が 422（違反 1 件）。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("POST", "/triggers"): failing(422, {"problems": [PROBLEM]})}),
    )
    await open_page(user)
    user.find(marker="edit-save").click()
    await user.should_see(
        "検査に落ちました（違反 1 件）。宣言ファイルは 1 バイトも変更されていません"
    )


async def _drive_404(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:404`: 削除が 404。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={("DELETE", "/triggers/tr-1"): failing(404, {"detail": "見つかりません"})}
        ),
    )
    await open_page(user)
    user.find(marker="delete-tr-1").click()
    await user.should_see("404: 見つかりません")


async def _drive_500(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:500`: 手動実行が 500。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("POST", "/triggers/tr-1/run"): failing(500, {"detail": "x"})}),
    )
    await open_page(user)
    user.find(marker="run-tr-1").click()
    await user.should_see("500: {'detail': 'x'}")


async def _drive_run_accepted(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:run-accepted`: 手動実行が成功して `run_id` が出る。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    user.find(marker="run-tr-1").click()
    await user.should_see("受け付けました（run_id: run-9）")


async def _drive_default_selected(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:default-selected`: 既定トリガーを選び、3 種の入力補助を順に出す。"""
    use_api(monkeypatch, FakeApi(responses={("GET", "/triggers"): ok([DEFAULT_TRIGGER_ROW])}))
    await open_page(user)
    set_value(user, "edit-target", label_of(DEFAULT_TRIGGER_ROW))
    await user.should_see(trigger_ui.DEFAULT_TRIGGER_NOTICE)


async def _drive_disabled_row(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:disabled-row`: 無効なトリガーで「有効にする」を出す。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/triggers"): ok([{**TRIGGER_ROW, "enabled": False}])}),
    )
    await open_page(user)


async def _drive_spec_help(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:spec-help-cron`: 種別を `cron` にして入力補助を出す。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    set_value(user, "edit-type", "cron")
    await user.should_see(trigger_ui.SPEC_HELP["cron"])


async def _drive_spec_help_file(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:spec-help-file`: 種別を `file` にして入力補助を出す。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    set_value(user, "edit-type", "file")
    await user.should_see(trigger_ui.SPEC_HELP["file"])


async def _drive_no_next_fire(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:no-next-fire`: 次回実行時刻と直近結果が無い（「—」が出る）。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/triggers"): ok(
                    [{**TRIGGER_ROW, "next_fire_at": None, "last_result": None}]
                )
            }
        ),
    )
    await open_page(user)


async def _drive_unfinished_row(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:unfinished-row`: 直近結果が `started`（「実行中／未終了」が出る）。"""
    use_api(
        monkeypatch,
        FakeApi(
            responses={
                ("GET", "/triggers"): ok([{**TRIGGER_ROW, "last_result": {"status": "started"}}]),
                ("GET", "/history"): ok([{**HISTORY_ROW, "status": "started"}]),
            }
        ),
    )
    await open_page(user)


async def _drive_unreachable_without_detail(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:unreachable-bare`: 説明の無い `status=0`（既定の文言が出る）。"""
    use_api(
        monkeypatch,
        FakeApi(responses={("GET", "/watcher"): trigger_ui.ApiResponse(status=0, payload=None)}),
    )
    await open_page(user)


async def _drive_accepted(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """`triggers:accepted`: 切替が成功して「受け付けました」だけが出る。"""
    use_api(monkeypatch, FakeApi())
    await open_page(user)
    user.find(marker="toggle-tr-1").click()
    await user.should_see("受け付けました")


SCENARIOS: dict[str, Callable[[User, pytest.MonkeyPatch], Awaitable[None]]] = {
    "triggers:unreachable-bare": _drive_unreachable_without_detail,
    "triggers:accepted": _drive_accepted,
    "triggers:open": _drive_open,
    "triggers:empty": _drive_empty,
    "triggers:dead-watcher": _drive_dead_watcher,
    "triggers:unreachable": _drive_unreachable,
    "triggers:422": _drive_422,
    "triggers:404": _drive_404,
    "triggers:500": _drive_500,
    "triggers:run-accepted": _drive_run_accepted,
    "triggers:default-selected": _drive_default_selected,
    "triggers:disabled-row": _drive_disabled_row,
    "triggers:spec-help-cron": _drive_spec_help,
    "triggers:spec-help-file": _drive_spec_help_file,
    "triggers:no-next-fire": _drive_no_next_fire,
    "triggers:unfinished-row": _drive_unfinished_row,
}
"""文言の一覧の `scenario` → その文言を画面に出す駆動関数。"""


def wording() -> list[dict[str, object]]:
    """文言の一覧を読む。

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
    """N-18・J-22: 一覧の全件の `scenario` が駆動関数の表にある（空振りを防ぐ）。"""
    entries = wording()

    assert entries, "文言の一覧が空である"
    missing = sorted({str(entry["scenario"]) for entry in entries} - set(SCENARIOS))

    assert not missing, f"駆動関数の無い scenario がある: {missing}"
    assert all(str(entry["page"]) == "triggers" for entry in entries)
    assert all(str(entry["scenario"]).startswith("triggers:") for entry in entries)


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
