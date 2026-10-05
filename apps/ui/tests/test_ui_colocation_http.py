"""同居の実 HTTP 側の検証（FR-034 の後半 2 点＝SC-005・FR-008）。

`test_ui_colocation.py`（`TestClient`）が見る SC-006・SC-010・SC-004 に対して、本ファイルは残る
2 点を見る。

1. 画面の書込が待っている間も、同じ待受が別の要求に応答する（SC-005・FR-012・US3 AS-6）。
   `/speckit-analyze` C1 の裁定により、**画面の側の経路**（(1)）と **`/health` そのもの**（(2)）の
   2 本を並べる。
2. `call_api` を**差し替えずに**実 HTTP を通したとき、保存の成功に反映区分と世代が出る
   （FR-008・US3 AS-7・ui-pages D-1）。Starlette はヘッダ名を**小文字で**送る
   （`x-settings-effect`・`x-settings-generation`＝2026-09-27 に実測）ので、名前を大文字小文字を
   区別せずに読む D-1 が効いていなければ、この文言は出ない。

⚠ 準備は `apps/api/tests/_support` を **import だけで**再利用する（規約 §5・ND-15。`_support` に
1 行も追記しない）。`apps/ui/tests/conftest.py` は作らない（`015` §2）ので、準備は本ファイルの
ローカル fixture に閉じる。

⚠ 実 HTTP で待ち受ける側に **UI を載せない**（research R-22）—— 同じプロセスで「待ち受ける
NiceGUI」と「`user` フィクスチャが模擬する NiceGUI」が大域の `core.app` を取り合うためである。

⚠ `nicegui_reset_globals()` のローカル fixture を置かない —— NiceGUI を触るテストはどれも `user`
フィクスチャを使い、その `user_simulation` が既に `with nicegui_reset_globals():` の中で組んで
いる（`.venv` の `nicegui/testing/user_simulation.py`）。二重に掛けても戻す対象は同じで、掛ける
順（fixture の解決順）だけが不確かになる。`create_ui_app` を呼ぶテストは本ファイルに無い。

⚠ 固定の `sleep` を使わない（規約 §2）。非同期の観測は `wait_until`、1 秒の上限は
`asyncio.wait_for` で表明する。塞いだ `Event` は必ず `finally` で解放する。
"""

from __future__ import annotations

import asyncio
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import httpx
import pytest
import uvicorn
from _support.cluster_env import assert_env_path_under, isolate_env
from _support.cluster_stores import make_temp_area_a
from _support.composition import make_root_config
from _support.contract_stubs import CallLog, StubBoardReader, StubVaultReader
from nicegui import app as nicegui_app
from nicegui import ui as nui

from api import create_app
from common.interfaces.board import BoardReader
from common.interfaces.vault import VaultReader
from settings import SettingsEnv
from settings import ui as settings_ui

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from fastapi import FastAPI
    from nicegui.element import Element
    from nicegui.testing import User

    from api.wiring import ProtocolOverrides

STUB_BASE_URL = "http://settings-colocation.invalid:9999"
"""偽の `call_api` を使うテストが画面へ渡す接続先（実運用の値でないことが一目で分かる形）。"""

ASGI_BASE_URL = "http://colocation-app.invalid"
"""`ASGITransport` に載せた実物の app へ送るときの base URL（ネットワークには出ない）。"""

HOST = "127.0.0.1"
"""実 HTTP の待受のホスト。"""

PROBE_PATH = "/probe"
"""テストの中で NiceGUI の app に足す、軽い経路（(1) が叩く相手）。"""

HEALTH_PATH = "/health"
"""生存確認の経路（(2) が叩く相手）。"""

RESPONSE_LIMIT_SECONDS = 1.0
"""別の要求が返るまでの上限（SC-005 の「1 秒以内」）。"""

GATE_TIMEOUT_SECONDS = 10.0
"""塞いだ書込がスレッドの中で待つ上限（⚠ 解放し損ねてもテストが固まらないように置く）。"""

STARTUP_LIMIT_SECONDS = 10.0
"""スレッドの uvicorn が待受を始めるまでの上限。"""

SHUTDOWN_LIMIT_SECONDS = 10.0
"""スレッドの uvicorn が止まるまでの上限（後始末で `join` する）。"""

POLL_INTERVAL_SECONDS = 0.01
"""待ちの 1 回分（固定の `sleep` ではなく、条件を見る間隔）。"""

SAVE_MARKER = "humans_save"
"""(1)・(2) が使う書込（必須欄が無い最も単純な書込＝ data-model §5）。"""

LIMITS_MARKER = "limits_global_save"
"""(3) が押す書込（上限の全体既定＝§2-2 の #7。実物の口は `PUT /settings/limits`）。"""

HUMANS_PATH = "/settings/humans"
"""人間の行為者の書込先。"""

HUMANS_BODY: dict[str, object] = {"human_actors": ["human_owner"]}
"""(2) が `fetch` に渡す本文（偽の `call_api` が受けるので、口の検査は通らない）。"""

READ_PAYLOADS: dict[str, object] = {
    "/settings/agents": [
        {"persona_key": "programmer", "project_key": None, "role": "全体既定の役割"}
    ],
    "/settings/tasks/global": {"plain": {"description": "d", "expected_output": "e"}},
    "/settings/llm-profiles": {"profiles": [{"name": "default", "model_id": "demo-model"}]},
    "/settings/limits": {
        "global": {"max_requests_per_kickoff": 5, "max_cost_per_kickoff": 0.5},
        "overrides": {},
    },
    "/settings/commands": [{"name": "demo", "argv": ["/bin/echo", "hello"]}],
    "/settings/humans": {"human_actors": ["human_owner"]},
}
"""ページを開いたときの 6 読取の本文。

形は**実物の口の応答**に合わせた（settings だけを有効にした `create_app` へ `make_temp_area_a` の
領域 A で問い合わせた実測＝2026-09-27）。(3) は同じ形を実 HTTP で受けるので、偽と実で描画の経路が
分かれない。
"""

SAVED_PATTERN = re.compile(
    "保存しました。"
    "(次回 kickoff から効きます|権限ポリシーとコマンド定義は即時に効きます)"
    "（世代 (?P<generation>[0-9]+)）"
)
"""保存成功の文言（`010/U-D`・D-1）。世代は実物の口が決めるので数字で受ける。"""


@dataclass(slots=True)
class FakeApi:
    """書込だけを `threading.Event` で塞ぐ偽の `call_api`（唯一の差し替え点＝ ui-pages §3）。

    Attributes:
        gate: 書込は、この `Event` が立つまでスレッドの中で待つ。
        gate_methods: `gate` で塞ぐメソッド。
        calls: 送られた `(method, path, body)` の並び（スレッドから `append` する）。
        blocked: 書込が `gate` の待ちに入ったら立つ。
        released: `gate` を抜けた書込の経路（塞げていたかを、画面の描画に依らず見る）。
    """

    gate: threading.Event
    gate_methods: tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE")
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])
    blocked: threading.Event = field(default_factory=threading.Event)
    released: list[str] = field(default_factory=list[str])

    def __call__(
        self, _base_url: str, method: str, path: str, body: object | None = None
    ) -> settings_ui.ApiResponse:
        """要求を記録し、書込なら `gate` が立つまで待ってから応答を返す。"""
        self.calls.append((method, path, body))
        if method in self.gate_methods:
            self.blocked.set()
            self.gate.wait(timeout=GATE_TIMEOUT_SECONDS)
            self.released.append(path)
            return _saved()
        return settings_ui.ApiResponse(status=200, payload=READ_PAYLOADS.get(path.split("?")[0]))

    def writes(self) -> list[tuple[str, str, object]]:
        """書込要求（GET 以外）だけを返す。

        Returns:
            `(method, path, body)` の並び。
        """
        return [call for call in self.calls if call[0] != "GET"]


def _saved() -> settings_ui.ApiResponse:
    """塞いでいた書込を解放したときの応答（ヘッダ名は**小文字**＝FR-008）。

    Returns:
        反映区分と世代を載せた 200。
    """
    return settings_ui.ApiResponse(
        status=200,
        payload=None,
        headers={"x-settings-effect": "next-kickoff", "x-settings-generation": "7"},
    )


def use_api(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> FakeApi:
    """偽の `call_api` を差し込む（文字列経路の差し替えを使わない＝規約 §3）。

    Args:
        monkeypatch: 差し替えの後始末を持つ pytest の道具。
        api: 差し込む偽物。

    Returns:
        差し込んだ偽物。
    """
    monkeypatch.setattr(settings_ui, "call_api", api)
    return api


async def wait_until(condition: Callable[[], bool], *, what: str, retries: int = 200) -> None:
    """条件が満たされるまで上限つきで待つ（固定の `sleep` を使わない）。

    ⚠ `should_see` は**すでに満たされている条件では待受に譲らない**ので、非同期の処理の観測には
    こちらを使う（`test_settings_ui_ai_page.py` と同じ形）。

    Args:
        condition: 満たされたら True を返す関数。
        what: 満たされなかったときの説明。
        retries: 試す回数（1 回 `POLL_INTERVAL_SECONDS` 秒）。

    Raises:
        AssertionError: 上限まで満たされなかったとき。
    """
    for _ in range(retries):
        if condition():
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"条件が満たされなかった: {what}")


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
    """一覧の中の写像だけを取り出す（結果表示が表のときに読むため）。

    Args:
        value: 調べる値。

    Returns:
        写像の並び。
    """
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast("dict[object, object]", item) for item in items if isinstance(item, dict)]


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
    """印の付いた部品が在るかを見る。

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


def result_texts(user: User, marker: str) -> set[str]:
    """書込欄の結果表示（印 `<書込ボタンの印>-result`）の中の文字を集める。

    ⚠ 手本（`test_settings_ui_ai_page.py`）と違い、表示域がまだ無ければ空集合を返す —— 本ファイル
    では `wait_until` の条件として呼ぶので、途中経過で例外にしない。

    Args:
        user: `user` フィクスチャ。
        marker: 書込ボタンの印。

    Returns:
        結果表示の中の文字の集合。
    """
    if not exists(user, f"{marker}-result"):
        return set()
    found: set[str] = set()
    for element in one(user, f"{marker}-result").descendants():
        props: dict[str, object] = dict(element.props)
        for key in ("text", "label"):
            found.update(_strings_of(props.get(key)))
        found.update(_strings_of(getattr(element, "text", None)))
        for row in _mappings_of(getattr(element, "rows", None)):
            for cell in row.values():
                found.update(_strings_of(cell))
    return found


def saved_message(user: User, marker: str) -> re.Match[str] | None:
    """書込欄の結果表示から、保存成功の文言（反映区分と世代つき）を 1 つ探す。

    Args:
        user: `user` フィクスチャ。
        marker: 書込ボタンの印。

    Returns:
        `SAVED_PATTERN` に**完全一致**した結果（無ければ `None`）。
    """
    for text in sorted(result_texts(user, marker)):
        matched = SAVED_PATTERN.fullmatch(text)
        if matched is not None:
            return matched
    return None


@pytest.fixture(autouse=True)
def stub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse。実運用の `.env` を読ませない）。

    Returns:
        差し替え先の一時 `.env` の絶対パス。
    """
    return isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def area_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_env: Path) -> Path:
    """settings が読む一時領域 A（`create_app` の前に指し直す）。

    Returns:
        `SETTINGS_ROOT` に渡した値。
    """
    area = make_temp_area_a(tmp_path)
    monkeypatch.setenv("SETTINGS_ROOT", str(area.root))
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=stub_env)
    return area.root


@pytest.fixture
def settings_app(area_a: Path) -> FastAPI:
    """settings だけを有効にした**実物の** app（UI は載せない＝research R-22）。

    Returns:
        組み上がった app（待受は始めていない）。
    """
    assert area_a.is_dir(), "一時領域 A が組まれていない"
    log = CallLog()
    board_reader: BoardReader = StubBoardReader(log)
    vault_reader: VaultReader = StubVaultReader(log)
    overrides: ProtocolOverrides = {BoardReader: board_reader, VaultReader: vault_reader}
    return create_app(config=make_root_config(enabled_modules=["settings"]), overrides=overrides)


def _free_port() -> int:
    """空きポートを 1 つ取る（0 番で bind して割り当てられた番号を読む）。

    Returns:
        ポート番号。
    """
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _wait_for_startup(server: uvicorn.Server, thread: threading.Thread) -> None:
    """待受が始まるまで上限つきで待つ（固定の `sleep` を使わない）。

    Args:
        server: 対象の待受。
        thread: 待受を走らせているスレッド。

    Raises:
        AssertionError: 待受が始まらなかったとき。
    """
    idle = threading.Event()
    deadline = time.monotonic() + STARTUP_LIMIT_SECONDS
    while not server.started:
        assert thread.is_alive(), "待受のスレッドが起動の前に終わった"
        assert time.monotonic() < deadline, "待受が上限までに始まらなかった"
        idle.wait(POLL_INTERVAL_SECONDS)


@pytest.fixture
def served_app(settings_app: FastAPI) -> Iterator[str]:
    """実物の app を、別スレッドの uvicorn で空きポートに待ち受ける（規約 §5）。

    Yields:
        実の base URL（画面へそのまま渡す）。
    """
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(settings_app, host=HOST, port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, name="colocation-uvicorn", daemon=True)
    thread.start()
    try:
        _wait_for_startup(server, thread)
        yield f"http://{HOST}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=SHUTDOWN_LIMIT_SECONDS)
        assert not thread.is_alive(), "待受のスレッドが後始末で止まらなかった"


# --------------------------------------------------------------------------------------
# (1) SC-005・FR-012・US3 AS-6: 書込の待ちが、画面の側の待受を塞がない
# --------------------------------------------------------------------------------------


async def test_a_pending_write_does_not_block_another_request(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-005・FR-012・US3 AS-6: 書込が待っている間も、別の要求が 1 秒以内に返る。

    `call_api` を待受の上で同期のまま呼ぶ実装（research R-08 の負の対照）では、ここで
    `asyncio.wait_for` が上限に達する。
    """
    gate = threading.Event()
    api = use_api(monkeypatch, FakeApi(gate=gate))

    @nicegui_app.get(PROBE_PATH)
    async def _probe() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]  # 経路として登録するので名前は参照されない
        return {"probe": "ok"}

    @nui.page("/")
    async def _page() -> None:  # pyright: ignore[reportUnusedFunction]  # `@nui.page` が経路に登録するので、名前は参照されない
        await settings_ui.render_ai_settings(STUB_BASE_URL)

    await user.open("/")

    try:
        user.find(marker=SAVE_MARKER).click()
        await wait_until(api.blocked.is_set, what="書込がスレッドの中で待ちに入ること")

        response = await asyncio.wait_for(
            user.http_client.get(PROBE_PATH), timeout=RESPONSE_LIMIT_SECONDS
        )

        assert response.status_code == 200, f"別の要求が 200 でない: {response.status_code}"
        assert not api.released, "塞いだはずの書込が終わっている（待ちの間の観測になっていない）"
    finally:
        gate.set()

    await wait_until(lambda: bool(api.released), what="解放した書込が最後まで進むこと")
    assert [(method, path) for method, path, _ in api.writes()] == [("PUT", HUMANS_PATH)], (
        f"塞いでいたのが目当ての書込 1 本でない: {api.writes()}"
    )


# --------------------------------------------------------------------------------------
# (2) SC-005・US3 AS-6: `/health` そのものが塞がれない（`/speckit-analyze` C1 の裁定）
# --------------------------------------------------------------------------------------


async def test_health_answers_while_a_write_is_pending(
    settings_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-005・US3 AS-6: 実物の app の `/health` が、待っている書込に塞がれず 1 秒以内に返る。

    画面と app を**同じイベントループ**に載せる（`ASGITransport`）。`fetch` が
    `asyncio.to_thread` で逃がしていなければ、`/health` はここで上限に達する。
    """
    gate = threading.Event()
    api = use_api(monkeypatch, FakeApi(gate=gate))
    transport = httpx.ASGITransport(settings_app)

    async with (
        settings_app.router.lifespan_context(settings_app),
        httpx.AsyncClient(transport=transport, base_url=ASGI_BASE_URL) as client,
    ):
        pending = asyncio.create_task(
            settings_ui.fetch(STUB_BASE_URL, "PUT", HUMANS_PATH, HUMANS_BODY)
        )
        try:
            await wait_until(api.blocked.is_set, what="書込がスレッドの中で待ちに入ること")

            response = await asyncio.wait_for(
                client.get(HEALTH_PATH), timeout=RESPONSE_LIMIT_SECONDS
            )

            assert response.status_code == 200, f"`/health` が 200 でない: {response.status_code}"
            assert not pending.done(), (
                "塞いだはずの書込が終わっている（待ちの間の観測になっていない）"
            )
        finally:
            gate.set()
            released = await pending

    assert released.status == 200, f"解放した書込の応答が 200 でない: {released.status}"


# --------------------------------------------------------------------------------------
# (3) FR-008・US3 AS-7: 実 HTTP で反映区分と世代が出る（D-1）
# --------------------------------------------------------------------------------------


async def test_a_real_save_shows_the_effect_and_the_generation(user: User, served_app: str) -> None:
    """FR-008・US3 AS-7・D-1: `call_api` を差し替えずに保存し、反映区分と世代が出る。

    実物の口が載せるヘッダ名は**小文字**である（`x-settings-effect`・`x-settings-generation`。
    `putGlobalLimits` の反映区分は `next-kickoff`＝2026-09-27 に実測）。名前を大文字小文字を
    区別せずに読む D-1 が効いていなければ、反映区分も世代も出ない。
    """

    @nui.page("/")
    async def _page() -> None:  # pyright: ignore[reportUnusedFunction]  # `@nui.page` が経路に登録するので、名前は参照されない
        await settings_ui.render_ai_settings(served_app)

    await user.open("/")

    user.find(marker=LIMITS_MARKER).click()
    await wait_until(
        lambda: saved_message(user, LIMITS_MARKER) is not None, what="実 HTTP の保存の結果"
    )

    matched = saved_message(user, LIMITS_MARKER)
    assert matched is not None
    assert int(matched.group("generation")) >= 1, f"世代が 1 以上でない: {matched.group(0)}"
