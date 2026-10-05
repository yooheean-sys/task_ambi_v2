"""設定画面 2 ページ（settings-F22）。`016` contracts/ui-pages.md。

⚠ **本ファイルは `settings.*` を 1 つも import しない**（憲章 2.0.0 §4 注記・FR-014）。
import してよいのは `nicegui` と、標準ライブラリの `__future__`・`asyncio`・`collections`・
`dataclasses`・`json`・`typing`・`urllib` だけである（ui-pages §2。`collections` は型注釈だけに
使う）。モジュールの操作は app の HTTP API 経由で行い、HTTP 境界は JSON なので DTO
（`settings.schemas`）を共有する必要も無い。

この形により (1) SC-016（画面のソースに service・repository への参照が 0 件）が**自明に真**になり、
(2) `ui-pages` 契約が `allow_indirect_imports` **なしで**成立し、(3) 画面のテストが settings の
組み立てを一切要さない。

**`httpx`・`requests` を使わない**（`httpx-scope` が settings を対象側に含む＝ 010 SD-06）。
**環境依存値の層を読まない**（接続先は登録の閉包が引数で渡す＝ 010 SD-05・FR-014）。

⚠ **`ui.page` を呼ばない**（research R-06）。経路を登録するのは apps/ui の殻だけである。NiceGUI の
`user` フィクスチャは後始末でページ関数のモジュールを `sys.modules` から消すため、feature 側に
ページ関数を置くと消される。
⚠ **`app.storage` を使わない・`storage_secret` を渡さない**（FR-021・憲章 2.0.0 §6）。画面の状態は
部品そのものと `_AiScreen`／`_ProjectsScreen` のインスタンスが持つ。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from nicegui import ui as nui

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

TIMEOUT_SECONDS = 10
"""要求 1 回の待ち時間（**再試行しない**＝憲章 §10。落ちたら接続エラーを出すだけ）。"""

GENERATION_HEADER = "X-Settings-Generation"
EFFECT_HEADER = "X-Settings-Effect"
"""保存結果のヘッダ（010 SD-12）。**綴りは口が載せるもの**（`next-kickoff` はハイフン）。

⚠ 読むときは**名前の大文字小文字を区別しない**（`header_value`）—— Starlette は小文字で送る
（`016` D-1・FR-008）。
"""

IMMEDIATE = "immediate"
NEXT_KICKOFF = "next-kickoff"
"""⚠ **ハイフン**である（010 SD-18）。内部表現 `EffectClass.next_kickoff` はアンダースコアで、
写像は口の `router/__init__.py` の 1 箇所にしか無い。画面がアンダースコアで分岐すると
**どの保存でも「次回 kickoff から」の表示に落ちない**。"""

UNCHANGED_NOTICE = "領域 A のファイルは 1 つも変更されていません"
"""422 のときに**必ず添える**文言（FR-090・U-B）。"""

LLM_PRESETS: tuple[tuple[str, str], ...] = (
    ("openrouter", "https://openrouter.ai/api/v1"),
    ("local", "http://127.0.0.1:8080/v1"),
)
"""新規 LLM プロファイルの雛形 2 件（名前と接続先だけ）。

**雛形の正本はここ**である（**FR-094 の書き換え済み本文**＝ 010 SD-19）—— 画面は HTTP のみで
モジュール設定を読めず、雛形を返す口を足すと **SC-014（D26 以降は 18 パス・39 操作）**を破るため。
観測点は **SC-029**（雛形が 2 件出る／値の出所が本ファイル以外に 0 件）。
"""

SECRET_NOTICE = "鍵の実値は入力しません（.env のキー名だけを指定します）"
"""FR-093・SC-008: 画面は `api_key_ref`（参照名）だけを受ける。

移行前に Streamlit が Markdown として描いていた記号（`` ` ``）を除いた**描画後の文字**である
（`016` ui-pages §4 の書き換え 6 箇所のうち 1 件）。
"""

POLICY_ROWS_LABEL = "行（1 行 1 件。p, 主体, 対象, 動作 ／ g, 主体, 役割）"
"""権限表の入力欄のラベル（同じく書き換え 6 箇所のうち 1 件＝ ui-pages §4）。"""

LIST_REPLACE_NOTICE = "上書きするとリストは丸ごと置き換わります"
"""FR-047・U-I: 一覧型（`tools`・`skills`・`mcps`）の merge 規則。"""

AGENT_TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("role", "役割"),
    ("goal", "目的"),
    ("backstory", "背景"),
    ("llm", "LLM プロファイル"),
)
AGENT_LIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("tools", "道具（空白区切り）"),
    ("skills", "技能（空白区切り）"),
)
AGENT_INT_FIELDS: tuple[tuple[str, str], ...] = (
    ("max_iter", "反復上限"),
    ("max_rpm", "毎分要求上限"),
    ("max_execution_time", "実行時間上限"),
)
MCPS_FIELD = "mcps"
"""FR-047 の**許可 10 種**（4 ＋ 2 ＋ 3 ＋ `mcps`）。U-I の「上書きしたい項目だけ」を
入力欄の単位に落としたもの。

`mcps` だけは 1 件が 4 項目（`name`・`transport`・`command`・`args`）の構造を持つので
**JSON で受ける**。**許可項目の検査（V-03・V-05・V-06）と未登録ペルソナの拒否（V-17）は
口とサービスが既に持つので画面側で再実装しない**（SC-013）。
"""

MCPS_LABEL = "外部サーバ（mcps・JSON の一覧）"
"""`mcps` の入力欄の見出し。"""

TASKS_FILE_NOTICE = (
    "ファイル: 設定フォルダ（領域 A）の global/tasks.yaml（プロジェクトの上書きは "
    "projects/<キー>/tasks.yaml）。反映は次回 kickoff から"
)
"""段テンプレートの所在（D26）。

パスは規約の相対パスだけを書く —— 設定フォルダの実値・そのキー名は画面に持たせない（SC-025）。
"""

STAGE_NO_AGENT = "（指定なし）"
STAGE_REPLACE_TOOLS_LABEL = "この段で道具を置き換える（外すと担当の装備をそのまま使う）"
STAGE_OVERRIDDEN = "このプロジェクトで上書き中です"
STAGE_INHERITED = "全体の値を表示中です（保存するとこの段だけ上書きになります）"
STAGE_UNREADABLE = "上書きを読めませんでした（全体の値を表示中です）"
STAGE_NOT_OVERRIDDEN_NOTICE = "この段は上書きされていません（何も送っていません）"
"""段ごとの編集欄（D26）の文言。"""

ROLES_SOURCE_NOTICE = (
    "装備＝agents.yaml（全体の既定にプロジェクトの上書きを重ねた結果）／"
    "許可＝projects/<キー>/policy.csv／段の担当と道具＝tasks.yaml。"
    "編集は「AI 設定」ページで行います"
)
ROLE_EQUIPPED_ONLY = "装備のみ（実行時に拒否されます）"
ROLE_ALLOWED_ONLY = "許可のみ（装備していません）"
ROLES_NO_MEMBERS = "このプロジェクトにメンバはいません"
ROLES_STAGE_TOOLS_HEADING = (
    "担当を段テンプレートで決めていない段の道具（担当は orchestrator の設定で決まります）"
)
"""役割ごとの設定（D26。閲覧のみ）の文言。"""

_MARK = "○"
_NO_MARK = "—"
"""表の真偽の表し方（装備・許可）。"""

_AGENT_NUMBER_MINIMUM = 0
"""エージェントの数値 3 種の下限（M-1・J-18。移行前の `min_value=0`）。"""

_CONTEXT_WINDOW_DEFAULT = 32768
_REQUESTS_DEFAULT = 60
_COST_DEFAULT = 1.0
"""数値欄の既定値（移行前の `value=`）。"""

_TITLE_CLASSES = "text-xl font-bold"
_HEADER_CLASSES = "text-lg font-bold"
_SUBHEADER_CLASSES = "font-bold mt-2"
_CAPTION_CLASSES = "text-sm text-gray-500"
_SUCCESS_CLASSES = "text-positive"
_ERROR_CLASSES = "text-negative"
_NOTICE_CLASSES = "text-info"
"""見た目（等価の対象は成功・誤り・案内が**見分けられる**ことだけ＝ J-19・ui-pages §4）。"""

_FULL_WIDTH_CLASSES = "w-full"
"""枠いっぱいに広げる（NiceGUI の既定は中身の幅まで縮む）。"""

_CODE_CLASSES = "w-full max-h-96 overflow-auto"
"""読取結果の JSON の塊（長い行・長い本文は枠の中でスクロールさせる）。"""

_COLUMN_STYLE = "min-width: 6rem"
"""表の列の最小幅（セルは折り返す＝ `ui.shell` の既定なので、日本語の短い列が 1 文字幅まで潰れない
ようにする）。"""

_HTTP_OK = 200
_HTTP_MULTIPLE_CHOICES = 300
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_UNPROCESSABLE = 422
_HTTP_SERVER_ERROR = 500


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """`call_api` の戻り。**成功も検査違反も同じ形**（応答コード ＋ 本文の写像）＝ FR-088a。"""

    status: int
    """HTTP の応答コード。**接続不能なら 0**。"""

    payload: object
    """JSON を読んだ結果。本文が無ければ `None`。"""

    error: str | None = None
    """接続不能・本文が読めない等の説明。正常なら `None`。"""

    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    """応答ヘッダ（保存結果の 2 つを読む）。**受け取った綴りのまま**写す。"""


def call_api(base_url: str, method: str, path: str, body: object | None = None) -> ApiResponse:
    """HTTP API を 1 回叩く（**唯一の差し替え点**＝ FR-088・FR-080 の UI 版）。

    `urllib.error.HTTPError` は**それ自体がファイルオブジェクト**なので `exc.read()` で本文を
    読み、`exc.code` と組で返す（**422 の違反一覧・409 の理由を取りこぼさない**＝ FR-088a）。
    `URLError`（`HTTPError` を除く）と `OSError` は `status=0` にする（FR-092・EIF-10）。
    **自動復旧も再試行も持たない**（憲章 §10）。

    ⚠ **同期のまま変えない**（`016` FR-013）。待受を塞がないための `asyncio.to_thread` は `fetch`
    が行う。符号化も行わない（呼ぶ側が `encode` を通す＝ D-5）。

    Args:
        base_url: app の base URL（登録の閉包が渡す）。
        method: HTTP メソッド。
        path: `/settings/...` の経路。
        body: JSON 本文（無ければ `None`）。

    Returns:
        応答コード・本文・ヘッダ・接続エラーの説明。
    """
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310  # 接続先は登録の閉包が渡す http(s) の base URL
        url=f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method.upper(),
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as stream:  # noqa: S310  # 同上
            return ApiResponse(
                status=stream.status,
                payload=_read_json(stream.read()),
                headers=dict(stream.headers),
            )
    except urllib.error.HTTPError as exc:
        # **本文を必ず読む** —— 422 の `problems` と 409 の `detail` はここにしか無い。
        return ApiResponse(
            status=exc.code, payload=_read_json(exc.read()), headers=dict(exc.headers)
        )
    except (urllib.error.URLError, OSError) as exc:
        return ApiResponse(status=0, payload=None, error=f"app に接続できません: {exc}")


def _read_json(raw: bytes) -> object:
    """本文を JSON として読む（空なら `None`。読めなければ生テキストのまま返す）。"""
    if not raw:
        return None
    try:
        return json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return raw.decode(errors="replace")


async def fetch(base_url: str, method: str, path: str, body: object | None = None) -> ApiResponse:
    """`call_api` を**待受の外**で呼ぶ（`016` FR-012・research R-08）。

    `asyncio.to_thread` に出すので、10 秒のタイムアウトが待受（同じプロセスの HTTP API を含む）を
    塞がない。**畳む層は持たない**（移行前と同じ。`call_api` が自分で `status=0` に畳む＝
    data-model §4）。

    Args:
        base_url: app の base URL。
        method: HTTP メソッド。
        path: 経路（符号化は呼ぶ側が済ませている）。
        body: JSON 本文。

    Returns:
        応答。
    """
    return await asyncio.to_thread(call_api, base_url, method, path, body)


def encode(value: object) -> str:
    """利用者の値を経路の段・クエリの値として符号化する（D-5・J-9）。

    英数・`-`・`_` だけの値では、符号化しても移行前と同じ文字列になる。

    Args:
        value: 符号化する値。

    Returns:
        `urllib.parse.quote(..., safe="")` を通した文字列。
    """
    return urllib.parse.quote(str(value), safe="")


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    """応答ヘッダを**名前の大文字小文字を区別せずに**引く（D-1・FR-008）。

    Starlette は小文字（`x-settings-effect`）で送り、`urllib` は受け取った綴りのまま写すので、
    定数の綴り（`X-Settings-Effect`）と一致しない。

    Args:
        headers: 応答ヘッダ。
        name: 引くヘッダ名。

    Returns:
        値。無ければ `None`。

    """
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def success_message(response: ApiResponse) -> str:
    """U-D・FR-091・D-1: 反映区分と世代を出す（**ヘッダが無い応答では出さない**）。

    Args:
        response: 2xx の応答。

    Returns:
        画面に出す成功の文言。
    """
    generation = header_value(response.headers, GENERATION_HEADER)
    effect = header_value(response.headers, EFFECT_HEADER)
    if generation is None or effect is None:
        return "保存しました。"
    if effect == IMMEDIATE:
        return f"保存しました。権限ポリシーとコマンド定義は即時に効きます（世代 {generation}）"
    if effect == NEXT_KICKOFF:
        return f"保存しました。次回 kickoff から効きます（世代 {generation}）"
    return f"保存しました。（世代 {generation}）"


@dataclass(frozen=True, slots=True)
class SettingsPage:
    """設定画面のページ部品（apps/ui の構造型 `UiPage` に構造で適合する＝ data-model §2）。"""

    title: str
    """ナビゲーションに出すタイトル。"""

    slug: str
    """経路スラグ（公開 URL は `/ui/{slug}`）。"""

    render: Callable[[], Awaitable[None]]
    """ページの中身だけを描く非同期関数（共通の見出しは殻が描く）。"""


def build_ai_settings_page(base_url: str) -> SettingsPage:
    """「AI 設定」ページ（UC-07）の部品を作る。

    **`ui.page` を呼ばない**（経路の登録は apps/ui の殻だけが行う）。

    Args:
        base_url: app の base URL。

    Returns:
        タイトル・経路スラグ・描画関数を持つページ部品。
    """
    return SettingsPage(
        title="AI 設定", slug="settings-ai", render=lambda: render_ai_settings(base_url)
    )


def build_projects_page(base_url: str) -> SettingsPage:
    """「プロジェクト」ページ（UC-08）の部品を作る。

    Args:
        base_url: app の base URL。

    Returns:
        タイトル・経路スラグ・描画関数を持つページ部品。
    """
    return SettingsPage(
        title="プロジェクト", slug="settings-projects", render=lambda: render_projects(base_url)
    )


async def render_ai_settings(base_url: str) -> None:
    """「AI 設定」ページの中身を描く（テストが直接呼べるよう公開する）。

    Args:
        base_url: app の base URL。
    """
    await _AiScreen(base_url).draw()


async def render_projects(base_url: str) -> None:
    """「プロジェクト」ページの中身を描く（公開の理由は `render_ai_settings` と同じ）。

    Args:
        base_url: app の base URL。
    """
    await _ProjectsScreen(base_url).draw()


# --------------------------------------------------------------------------------------
# 応答の表示（U-B〜U-E・D-7）
# --------------------------------------------------------------------------------------


def _is_success(response: ApiResponse) -> bool:
    """2xx かどうか。"""
    return _HTTP_OK <= response.status < _HTTP_MULTIPLE_CHOICES


def _is_transient(response: ApiResponse) -> bool:
    """やり直せば結果が変わりうる失敗（`status=0`・5xx）かどうか（`016` design-sync B-32 の裁定）。

    404・409・422 などは、同じ要求をやり直しても同じ応答になるので含めない。
    """
    return response.status == 0 or response.status >= _HTTP_SERVER_ERROR


def _as_mapping(value: object) -> dict[str, object]:
    """HTTP 境界の**生の JSON** を `dict[str, object]` に写す（読めなければ空）。

    JSON は型を持たないので、境界の narrow はこの 1 箇所に閉じる。
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def _as_list(value: object) -> list[object]:
    """同上（配列版）。"""
    if not isinstance(value, list):
        return []
    return list(cast("list[object]", value))


def _problems_of(payload: object) -> list[dict[str, object]]:
    """422 の本文から違反の一覧を取り出す（形が違えば空）。"""
    return [
        {key: str(value) for key, value in mapping.items()}
        for mapping in (
            _as_mapping(item) for item in _as_list(_as_mapping(payload).get("problems"))
        )
        if mapping
    ]


def _detail_of(payload: object) -> str:
    """404・409 の本文から `detail`（単一文字列）を取り出す。"""
    mapping = _as_mapping(payload)
    return str(mapping.get("detail", payload)) if mapping else str(payload)


def _rows_of(payload: object) -> list[dict[str, object]]:
    """一覧の応答（配列）を表にできる形へ写す。"""
    return [mapping for mapping in (_as_mapping(item) for item in _as_list(payload)) if mapping]


def _level_of(row: Mapping[str, object]) -> str:
    """U-A・R-11: `project_key` が `null` なら**全体**、そうでなければ**プロジェクト**。"""
    key = row.get("project_key")
    return "全体" if key is None else f"プロジェクト（{key}）"


def _cell(value: object) -> str:
    """表のセルの文字（辞書・配列は JSON にする＝ ui-pages §4）。"""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _table(rows: Sequence[Mapping[str, object]], *, marker: str) -> None:
    """一覧を表で出す（列は行のキーを最初に現れた順の和集合＝ ui-pages §4）。

    空の表に UI 基盤の既定の文字（「No data available」）を出さない（J-19）。

    Args:
        rows: 表に出す行。
        marker: 表に付ける印。
    """
    columns: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(
                    {
                        "name": key,
                        "label": key,
                        "field": key,
                        "align": "left",
                        "style": _COLUMN_STYLE,
                        "headerStyle": _COLUMN_STYLE,
                    }
                )
    table = nui.table(
        rows=[{key: _cell(value) for key, value in row.items()} for row in rows],
        columns=columns,
    ).classes(_FULL_WIDTH_CLASSES)
    table.mark(marker).props('no-data-label=""')


def _code_block(payload: object) -> None:
    """読取結果の生表示（旧 `st.write(dict/list)`）を JSON の塊で出す（research R-11）。

    Args:
        payload: 応答の本文。
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    nui.code(text, language="json").classes(_CODE_CLASSES)


def _draw_failure(response: ApiResponse) -> None:
    """2xx でない応答を、いま描いている位置に出す（U-B・U-C・U-E・D-7・J-13）。

    422 は件数つきの文言と違反の表、404・409 は「{コード}: {detail}」、それ以外は
    「{コード}: {本文}」、接続できないときは接続の説明である。動的な文字列は Markdown として
    解釈せず、生の文字（`ui.label`）で出す（J-19）。

    Args:
        response: 2xx でない応答。
    """
    if response.status == 0:
        # U-E: **接続できない旨を表示するだけ**。画面自体は落ちない・自動復旧を持たない
        nui.label(response.error or "app に接続できません").classes(_ERROR_CLASSES)
        return
    if response.status == _HTTP_UNPROCESSABLE:
        problems = _problems_of(response.payload)
        nui.label(f"検査に落ちました（違反 {len(problems)} 件）。{UNCHANGED_NOTICE}").classes(
            _ERROR_CLASSES
        )
        if problems:
            # `where` は領域 A ルート相対なので**絶対パスは画面にも出ない**（SC-007）。
            # 画面は口が返した値を**加工しない**（FR-032 (b)）。
            _table(problems, marker="problems-table")
        else:
            nui.label(str(response.payload))
        return
    if response.status in (_HTTP_NOT_FOUND, _HTTP_CONFLICT):
        # U-C: 409 は `{"detail": "…"}` の**単一文字列**をそのまま出す（010 SD-03 の帰結を
        # 文言でも隠さない）。**機械可読な違反一覧は付かない。**
        nui.label(f"{response.status}: {_detail_of(response.payload)}").classes(_ERROR_CLASSES)
        return
    nui.label(f"{response.status}: {response.payload}").classes(_ERROR_CLASSES)


class _Results:
    """書込欄ごとの結果表示（印 `<書込ボタンの印>-result`）と「同時に 1 つ」の制御（J-12）。"""

    def __init__(self) -> None:
        """初期化する。"""
        self._areas: dict[str, nui.column] = {}

    def area(self, marker: str) -> None:
        """書込ボタンの直後に結果の表示域を 1 つ置く。

        Args:
            marker: 書込ボタンの印。
        """
        area = nui.column().classes("gap-0")
        area.mark(f"{marker}-result")
        self._areas[marker] = area

    def _replace(self, marker: str) -> nui.column:
        """ページの中の結果をすべて消し、当該の表示域を返す。

        Args:
            marker: 書込ボタンの印。

        Returns:
            空にした表示域。
        """
        for area in self._areas.values():
            if not area.is_deleted:
                area.clear()
        return self._areas[marker]

    def success(self, marker: str, message: str) -> None:
        """成功の文言を出す。

        Args:
            marker: 書込ボタンの印。
            message: 出す文言。
        """
        with self._replace(marker):
            nui.label(message).classes(_SUCCESS_CLASSES)

    def failure(self, marker: str, response: ApiResponse) -> None:
        """2xx でない応答を出す。

        Args:
            marker: 書込ボタンの印。
            response: 応答。
        """
        with self._replace(marker):
            _draw_failure(response)

    def notice(self, marker: str, message: str) -> None:
        """要求を送らなかった理由（JSON が読めない等）を出す（J-20）。

        Args:
            marker: 書込ボタンの印。
            message: 出す文言。
        """
        with self._replace(marker):
            nui.label(message).classes(_ERROR_CLASSES)


class _Writes:
    """ページの書込ボタンをまとめて押せる／押せなくする（W-1・J-15）。"""

    def __init__(self) -> None:
        """初期化する。"""
        self._buttons: list[nui.button] = []

    def add(self, button: nui.button) -> None:
        """書込ボタンを 1 つ登録する。

        Args:
            button: 書込ボタン。
        """
        self._buttons.append(button)

    def set_enabled(self, *, enabled: bool) -> None:
        """登録した書込ボタンをまとめて押せる／押せなくする。

        Args:
            enabled: 押せるようにするなら True。
        """
        for button in self._buttons:
            if not button.is_deleted:
                button.set_enabled(enabled)


@dataclass(frozen=True, slots=True)
class _Confirmation:
    """確定 1 回ぶん（`_KeyField` が確定を受けた時点で作り、確定に伴う処理へ渡す）。

    Attributes:
        key: 確定を受けた時点のキー。確定に伴う読取の経路は、これで組む（`016` design-sync
            B-33 の裁定 (2)）。
        names: この確定で送る読込の名前（送る順）。キーを変える確定なら欄の読込すべて、
            同じキーでの再試行なら前回の確定で一時的に失敗した読込だけ（B-34 の裁定）。
        is_latest: この確定が、同じキー欄で最後に受けた確定かを返す。最新でない確定の結果は
            画面に反映しない（B-33 の裁定 (1)）。
    """

    key: str
    names: tuple[str, ...]
    is_latest: Callable[[], bool]


class _KeyField:
    """確定してから使うプロジェクトキーの欄 1 つ（data-model §6・K-1・J-16）。

    Enter かフォーカスの移動で確定し、依存する読取を行う。キーに依存する書込ボタンは
    `guard` を通して置く。

    ⚠ **判定と状態の更新は、イベントを受けた時点で同期的に行う**（`016` T097・design-sync B-31）。
    NiceGUI は非同期のイベント処理を直列化せず、バックグラウンドタスクとして並行に走らせる
    （`nicegui/events.py` の `handle_event`）。マウスで書込ボタンを押すと、押し下げでフォーカスが
    移り、キー欄の blur が click より**先に**届く。判定を処理の中（タスクが走り出した後）に置くと、
    click が確定の途中の状態を見て、利用者が新しいキーの読取を見ないまま書込を送る（共通ルール
    では、前のキーの本文を新しいキーへ送る＝ D-4 の再発）。

    **同じキーでの確定し直し**（`016` design-sync B-32 の裁定）: キー欄の値が確定済みのキーと
    同じなら、確定に伴う処理を行わない（書きかけの入力を読み直しで消さない・同じ読取を二重に
    送らない）。ただし、前回の確定に伴う読込に一時的な失敗（`status=0`・5xx）があったときは、
    **その読込だけを**読み直す（J-14 の再試行・B-34 の裁定）。404・409 などでは読み直さない。

    **確定が重なったとき**（B-33 の裁定）: 確定を受けるたびに世代を進め、確定に伴う処理には
    受けた時点のキーと「自分が最新か」の判定を渡す（`_Confirmation`）。先に受けた確定の読取が
    後から届いても、その結果は画面にも再試行の要否にも使わない。

    Attributes:
        element: キー欄。
        confirmed: 確定済みのキー（確定を受けた時点で更新する）。
    """

    def __init__(
        self,
        marker: str,
        names: tuple[str, ...],
        confirm: Callable[[_Confirmation], Awaitable[frozenset[str]]],
    ) -> None:
        """キー欄を置き、Enter とフォーカスの移動で確定するようにする。

        Args:
            marker: キー欄の印。
            names: 確定に伴う読込の名前（送る順）。
            confirm: 確定に伴う処理（受けた確定の読込を送り、一時的な失敗（`status=0`・5xx）で
                終わった読込の名前を返す）。
        """
        self.confirmed = ""
        self._names = names
        self._confirm = confirm
        self._changing = 0
        self._generation = 0
        self._failed: frozenset[str] = frozenset()
        self.element = nui.input(label="プロジェクトキー")
        self.element.mark(marker)
        self.element.on("keydown.enter", self._on_confirm)
        self.element.on("blur", self._on_confirm)

    def _on_confirm(self) -> Awaitable[None] | None:
        """確定を受ける（確定済みのキーと、キーを変える確定の数を**受けた時点で**更新する）。

        キーが変わらず、前回の確定に伴う読込に一時的な失敗も無ければ、何もしない（B-32）。
        キーが変わらないときは、一時的に失敗した読込だけを読み直す（B-34）。

        Returns:
            確定に伴う処理（NiceGUI がバックグラウンドタスクとして待つ）。何もしないなら `None`。
        """
        key = str(self.element.value or "")
        changing = key != self.confirmed
        if not changing and not self._failed:
            return None
        names = (
            self._names if changing else tuple(name for name in self._names if name in self._failed)
        )
        self.confirmed = key
        self._failed = frozenset()  # 処理中の同じキーの確定は、結果が出るまで読み直さない
        self._generation += 1
        if changing:
            self._changing += 1
        generation = self._generation
        confirmation = _Confirmation(
            key=key, names=names, is_latest=lambda: generation == self._generation
        )
        return self._run(confirmation, changing=changing)

    async def _run(self, confirmation: _Confirmation, *, changing: bool) -> None:
        """確定に伴う処理を行い、終わったらキーを変える確定の数を戻す。

        再試行の対象は、**最後に受けた確定**の結果だけで決める（先に受けた確定が後から
        終わっても上書きしない＝ B-33 の裁定 (1)）。

        Args:
            confirmation: この確定。
            changing: キーを変える確定か。
        """
        try:
            failed = await self._confirm(confirmation)
        finally:
            if changing:
                self._changing -= 1
        if confirmation.is_latest():
            self._failed = failed

    def guard(
        self, write: Callable[[str], Awaitable[None]]
    ) -> Callable[[], Awaitable[None] | None]:
        """キーに依存する書込ボタンの受け口を作る（K-1・J-16）。

        押した時点で (1) キーを変える確定が終わっていなければ、送らない（確定はその処理が行う）
        (2) キー欄の文字が確定済みのキーと違えば、送らずに確定だけを行う (3) どちらでもなければ、
        押した時点の確定済みのキーへ書込を送る。同じキーでの再試行（B-32・J-14）は (1) に
        数えない。(1) は、キーを変える確定が終わるまでの押下を捨てる規則である（design-sync
        B-35 の裁定で K-1・FR-009a・data-model §6 に書き足した）。

        Args:
            write: 書込の処理（送り先のキーを受ける）。

        Returns:
            書込ボタンの `on_click` に渡す関数。
        """

        def on_click() -> Awaitable[None] | None:
            """押したときの判定（同期）。"""
            if self._changing:
                return None
            if str(self.element.value or "") != self.confirmed:
                return self._on_confirm()
            return write(self.confirmed)

        return on_click


# --------------------------------------------------------------------------------------
# AI 設定ページ（UC-07）
# --------------------------------------------------------------------------------------

_SECTION_READS: dict[str, tuple[str, ...]] = {
    "agents": ("agents", "members"),
    "tasks": ("tasks_global", "tasks_project"),
    "profiles": ("profiles",),
    "limits": ("limits",),
    "commands": ("commands",),
    "policies": ("humans", "policies", "hooks"),
}
"""節 → その節の読取表示（data-model §5）。

⚠ 共通ルールの `GET /settings/rules/projects/{k}` は**入れない** —— 本文欄の値を入れるための
読取（D-4）で、表示ではないので取り直しでは送らない。
"""

_KEYED_READS: dict[str, str] = {
    "members": "/settings/agents?project_key={key}",
    "tasks_project": "/settings/tasks/{key}",
    "policies": "/settings/policies/{key}",
    "hooks": "/settings/hooks/{key}",
    "rules": "/settings/rules/projects/{key}",
}
"""キー欄に依存する読込 → 経路（`{key}` には符号化したキーを入れる＝ D-5）。"""


def _keyed_path(name: str, key: str) -> str:
    """キー欄に依存する読込の経路を組む。

    Args:
        name: 読込の名前（`_KEYED_READS` のキー）。
        key: プロジェクトキー（符号化はここで行う）。

    Returns:
        経路。
    """
    return _KEYED_READS[name].format(key=encode(key))


def _templates_of(payload: object) -> dict[str, dict[str, object]]:
    """段テンプレートの応答を「段キー → 項目」に写す（**値が null の項目を除く**＝ D26）。

    `GET` は任意項目を null で返す（M-2）。そのまま `PUT` に載せると、口が `exclude_unset` で
    落とせず `agent: null` がファイルに書かれるので、送り返す前にここで落とす。

    Args:
        payload: `GET /settings/tasks/...` の本文。

    Returns:
        段キー → 項目（読めなければ空）。
    """
    return {
        key: {name: value for name, value in _as_mapping(spec).items() if value is not None}
        for key, spec in _as_mapping(payload).items()
    }


class _StageForm:
    """段テンプレート 1 段の編集欄（D26。全体用と PJ 上書き用の 2 組を置く）。

    Attributes:
        stage: 段の選択。
        state: 表示中の値の出どころ（PJ 上書き用だけが使う）。
    """

    def __init__(
        self,
        prefix: str,
        stages: Sequence[str],
        personas: Sequence[str],
        on_stage: Callable[[], None],
    ) -> None:
        """段の選択と 5 つの欄を置く。

        Args:
            prefix: 欄の印の接頭辞（段の選択そのものの印でもある）。
            stages: 段キー（全体の読取のキー）。
            personas: `agent` の選択肢に出すペルソナ。
            on_stage: 段を選び直したときの処理（欄へ値を入れ直す）。
        """
        self.stage = nui.select(list(stages), value=stages[0] if stages else None, label="段")
        self.stage.mark(prefix)
        self.stage.on_value_change(lambda _event: on_stage())
        self.state = nui.label("").classes(_NOTICE_CLASSES)
        self.state.mark(f"{prefix}_state")
        self._description = nui.textarea(label="description（指示文）")
        self._description.mark(f"{prefix}_description")
        self._expected = nui.textarea(label="expected_output（期待する出力）")
        self._expected.mark(f"{prefix}_expected_output")
        for area in (self._description, self._expected):
            area.classes(_FULL_WIDTH_CLASSES).props("rows=10")
        self._agent_options: dict[str, str] = {"": STAGE_NO_AGENT}
        self._agent_options.update({name: name for name in personas})
        self._agent = nui.select(dict(self._agent_options), value="", label="agent（既定の担当）")
        self._agent.mark(f"{prefix}_agent")
        self._replace_tools = nui.checkbox(STAGE_REPLACE_TOOLS_LABEL)
        self._replace_tools.mark(f"{prefix}_replace_tools")
        self._tools = nui.input(label="tools（空白区切り）").classes(_FULL_WIDTH_CLASSES)
        self._tools.mark(f"{prefix}_tools")
        self._tools.bind_enabled_from(self._replace_tools, "value")

    @property
    def selected(self) -> str:
        """選んでいる段キー（未選択なら空文字）。"""
        return str(self.stage.value or "")

    def fill(self, spec: Mapping[str, object] | None) -> None:
        """欄へ 1 段ぶんの値を入れる（無ければ空にする）。

        Args:
            spec: 段の項目（`_templates_of` の値）。
        """
        values = spec or {}
        self._description.set_value(str(values.get("description") or ""))
        self._expected.set_value(str(values.get("expected_output") or ""))
        agent = values.get("agent")
        name = agent if isinstance(agent, str) else ""
        if name not in self._agent_options:
            # 読取に無いペルソナ名（全体の読取の失敗など）でも、選択肢に足して値を落とさない
            self._agent_options[name] = name
            # NiceGUI の set_options は options を型引数なしの List | Dict で注釈している
            self._agent.set_options(dict(self._agent_options))  # pyright: ignore[reportUnknownMemberType]
        self._agent.set_value(name)
        tools = values.get("tools")
        self._replace_tools.set_value(isinstance(tools, list))
        self._tools.set_value(" ".join(str(tool) for tool in _as_list(tools)))

    def body(self) -> dict[str, object]:
        """欄の値を 1 段の本文にする（`agent` は選んだときだけ、`tools` は置き換えるときだけ）。

        Returns:
            `TaskTemplateSpec` に載せる本文（置き換えで空欄なら `tools: []`＝道具なし）。
        """
        body: dict[str, object] = {
            "description": str(self._description.value or ""),
            "expected_output": str(self._expected.value or ""),
        }
        agent = str(self._agent.value or "")
        if agent:
            body["agent"] = agent
        if self._replace_tools.value:
            body["tools"] = str(self._tools.value or "").split()
        return body


class _AiScreen:
    """AI 設定ページ 1 枚ぶんの状態と描画。"""

    _results: _Results
    _writes: _Writes
    _global_agent_persona: nui.input
    _member_key: _KeyField
    _agents_persona: nui.input
    _member_form: nui.column
    _members_area: nui.column
    _tasks_global_body: nui.textarea
    _tasks_key: _KeyField
    _tasks_project_body: nui.textarea
    _tasks_project_form: nui.column
    _tasks_project_area: nui.column
    _stage_global: _StageForm
    _stage_project: _StageForm
    _profile_preset: nui.select
    _profile_name: nui.input
    _profile_base: nui.input
    _profile_model: nui.input
    _profile_window: nui.number
    _profile_ref: nui.input
    _limits_project: nui.input
    _limits_req: nui.number
    _limits_cost: nui.number
    _command_name: nui.input
    _command_argv: nui.input
    _humans_body: nui.textarea
    _policies_key: _KeyField
    _policies_form: nui.column
    _policy_body: nui.textarea
    _rules_form: nui.column
    _rules_area: nui.column
    _rules: nui.textarea
    _hooks_body: nui.textarea

    def __init__(self, base_url: str) -> None:
        """初期化する。

        Args:
            base_url: app の base URL。
        """
        self._base_url = base_url
        self._reads: dict[str, ApiResponse | None] = {}
        self._agent_texts: dict[str, dict[str, nui.input]] = {}
        self._agent_numbers: dict[str, dict[str, nui.number]] = {}
        self._agent_mcps: dict[str, nui.textarea] = {}
        self._results = _Results()
        self._writes = _Writes()
        self._sections = {
            name: nui.refreshable(drawer)
            for name, drawer in (
                ("agents", self._draw_agents_read),
                ("members", self._draw_members_read),
                ("tasks_global", self._draw_tasks_global_read),
                ("tasks_project", self._draw_tasks_project_read),
                ("profiles", self._draw_profiles_read),
                ("limits", self._draw_limits_read),
                ("commands", self._draw_commands_read),
                ("humans", self._draw_humans_read),
                ("policies", self._draw_policies_read),
                ("hooks", self._draw_hooks_read),
            )
        }

    # -- 組み立て -----------------------------------------------------------------------

    async def draw(self) -> None:
        """ページの中身を描く（節の並びは移行前のまま＝ data-model §5）。"""
        nui.label("AI 設定").classes(_TITLE_CLASSES)
        nui.label(f"接続先 app: {self._base_url}").classes(_CAPTION_CLASSES)

        first = await fetch(self._base_url, "GET", "/settings/agents")
        self._reads["agents"] = first
        if first.status == 0:
            # D-2・US1 AS-5b: 接続できない旨だけを出し、以降の節を描かない
            _draw_failure(first)
            return

        await self._load(
            "tasks_global", "profiles", "limits", "commands", "humans", fetch_only=True
        )
        for draw_section in (
            self._draw_agents_section,
            self._draw_tasks_section,
            self._draw_profiles_section,
            self._draw_limits_section,
            self._draw_commands_section,
            self._draw_policies_section,
        ):
            with nui.card():
                draw_section()

    # -- 読取の取得と取り直し -----------------------------------------------------------

    def _read_path(self, name: str) -> str | None:
        """読取の経路（キーが未確定なら `None`）。

        Args:
            name: 読取の名前。

        Returns:
            経路。送らないなら `None`。
        """
        fixed = {
            "agents": "/settings/agents",
            "tasks_global": "/settings/tasks/global",
            "profiles": "/settings/llm-profiles",
            "limits": "/settings/limits",
            "commands": "/settings/commands",
            "humans": "/settings/humans",
        }
        if name in fixed:
            return fixed[name]
        if name == "members":
            key = self._member_key.confirmed
        elif name == "tasks_project":
            key = self._tasks_key.confirmed
        elif name in ("policies", "hooks"):
            key = self._policies_key.confirmed
        else:
            return None
        return _keyed_path(name, key) if key else None

    async def _load(self, *names: str, fetch_only: bool = False) -> list[ApiResponse]:
        """読取を送り、必要なら表示を取り直す（R-1）。

        Args:
            names: 読取の名前。
            fetch_only: 表示の取り直しを行わない（最初の組み立て時）。

        Returns:
            この呼び出しで送った読取の応答（キーが未確定で送らなかった読取は含まない）。
            確定の処理は、ほかの取り直しと混ざらないよう `_reads` ではなくこれを見る。
        """
        responses: list[ApiResponse] = []
        for name in names:
            path = self._read_path(name)
            if path is None:
                continue
            response = await fetch(self._base_url, "GET", path)
            self._reads[name] = response
            responses.append(response)
            if not fetch_only:
                await self._sections[name].refresh()
        return responses

    async def _confirm_reads(
        self,
        confirmation: _Confirmation,
        reflect: Callable[[str, ApiResponse], Awaitable[None]],
    ) -> frozenset[str]:
        """確定に伴う読込を送り、最新の確定なら画面に出す（`016` design-sync B-33・B-34）。

        - 経路は、確定を受けた時点のキーで組む（B-33 の裁定 (2)）。
        - 読込のたびに最新かを見て、最新でなければ画面に出さない（B-33 の裁定 (1)）。残りの
          読込は、受けた時点のキーの経路へ送り続ける。
        - 読込が例外で止まったら、止まった読込と、それ以降に送るはずだった読込を送らずに
          `status=0` とみなす（B-33 の裁定 (3)）。`call_api` が `status=0` に畳むのは
          `URLError`・`OSError` だけで、`http.client.IncompleteRead` などはここまで届く
          （`call_api` は変えない＝ ui-pages §3）。

        Args:
            confirmation: この確定。
            reflect: 最新の確定の読込 1 つを画面に出す処理（名前と応答を受ける）。

        Returns:
            一時的な失敗（`status=0`・5xx）で終わった読込の名前（次の再試行の対象＝ B-34）。
        """
        failed: set[str] = set()
        stopped: ApiResponse | None = None
        for name in confirmation.names:
            response = stopped
            if response is None:
                try:
                    response = await fetch(
                        self._base_url, "GET", _keyed_path(name, confirmation.key)
                    )
                except Exception as exc:  # 畳まれない例外も接続できない扱い（B-33 (3)）
                    # 文言は `call_api` の接続失敗と同じ（新しい文言は足さない）
                    stopped = response = ApiResponse(
                        status=0, payload=None, error=f"app に接続できません: {exc}"
                    )
            if _is_transient(response):
                failed.add(name)
            if confirmation.is_latest():
                await reflect(name, response)
        return frozenset(failed)

    def _payload(self, name: str) -> object:
        """読取の本文（未取得・2xx でなければ `None`）。

        Args:
            name: 読取の名前。

        Returns:
            本文。
        """
        response = self._reads.get(name)
        if response is None or not _is_success(response):
            return None
        return response.payload

    def _draw_read(self, name: str, *, marker: str) -> None:
        """1 つの読取表示を描く（2xx でなければ**同じ位置に**誤りを出す＝ D-7・J-13）。

        ⚠ 印は**外側の器**に付ける —— 中身（JSON の塊か誤り）が入れ替わっても、
        「その位置」を印で引けるようにするためである（印を JSON の塊に付けると、読取が
        失敗した場面で印そのものが消えて D-7 を機械的に確かめられない）。

        Args:
            name: 読取の名前。
            marker: 読取表示に付ける印。
        """
        area = nui.column().classes("gap-0 w-full")
        area.mark(marker)
        with area:
            response = self._reads.get(name)
            if response is None:
                return
            if not _is_success(response):
                _draw_failure(response)
                return
            _code_block(response.payload)

    def _draw_agents_read(self) -> None:
        """節 1 の全体既定の表（U-A・N-2）。"""
        response = self._reads.get("agents")
        if response is None:
            return
        if not _is_success(response):
            _draw_failure(response)
            return
        rows = _rows_of(response.payload)
        nui.label(f"ペルソナ {len(rows)} 種（段との対応は表示しません）").classes(_CAPTION_CLASSES)
        _table([{"レベル": _level_of(row), **row} for row in rows], marker="agents-table")

    def _draw_members_read(self) -> None:
        """節 1 のメンバの表（U-I・N-2）。"""
        response = self._reads.get("members")
        if response is None:
            return
        if not _is_success(response):
            _draw_failure(response)
            return
        rows = _rows_of(response.payload)
        _table([{"レベル": _level_of(row), **row} for row in rows], marker="members-table")

    def _draw_tasks_global_read(self) -> None:
        """節 2 の全体の読取表示。"""
        self._draw_read("tasks_global", marker="tasks_global-read")

    def _draw_tasks_project_read(self) -> None:
        """節 2 の上書きの読取表示。"""
        self._draw_read("tasks_project", marker="tasks_project-read")

    def _draw_profiles_read(self) -> None:
        """節 3 の読取表示。"""
        self._draw_read("profiles", marker="profiles-read")

    def _draw_limits_read(self) -> None:
        """節 4 の読取表示。"""
        self._draw_read("limits", marker="limits-read")

    def _draw_commands_read(self) -> None:
        """節 5 の読取表示。"""
        self._draw_read("commands", marker="commands-read")

    def _draw_humans_read(self) -> None:
        """節 6 の人間の行為者の読取表示。"""
        self._draw_read("humans", marker="humans-read")

    def _draw_policies_read(self) -> None:
        """節 6 の権限表の読取表示。"""
        self._draw_read("policies", marker="policies-read")

    def _draw_hooks_read(self) -> None:
        """節 6 の Hook の読取表示。"""
        self._draw_read("hooks", marker="hooks-read")

    # -- 節 1: ペルソナ既定とメンバ -----------------------------------------------------

    def _draw_agents_section(self) -> None:
        """ペルソナ既定とメンバ（**9 種すべて**を出し、段との対応は出さない＝ U-H・P5D-017）。"""
        nui.label("ペルソナ既定とメンバ").classes(_HEADER_CLASSES)
        self._sections["agents"]()

        nui.label("ペルソナ既定の上書き").classes(_SUBHEADER_CLASSES)
        self._global_agent_persona = nui.input(label="ペルソナ（既定を上書きする対象）")
        self._global_agent_persona.mark("global_agent_persona")
        self._draw_agent_form("global_agent")
        self._button("ペルソナ既定を保存", "global_agent_save", self._save_global_agent)

        nui.label("メンバ").classes(_SUBHEADER_CLASSES)
        self._member_key = _KeyField("agents_project", ("members",), self._confirm_member_key)
        self._members_area = nui.column().classes("gap-0 w-full")
        with self._members_area:
            self._sections["members"]()
        self._member_form = nui.column().classes(_FULL_WIDTH_CLASSES)
        self._member_form.set_visibility(False)
        with self._member_form:
            nui.label(LIST_REPLACE_NOTICE).classes(_CAPTION_CLASSES)
            nui.label(
                "全体の既定は上の表（読み取り専用）です。上書きしたい項目だけを入力してください。"
            ).classes(_NOTICE_CLASSES)
            self._agents_persona = nui.input(label="ペルソナ")
            self._agents_persona.mark("agents_persona")
            self._draw_agent_form("agents")
            self._button("メンバを保存", "agents_save", self._member_key.guard(self._save_member))

    def _draw_agent_form(self, prefix: str) -> None:
        """許可 10 種の入力欄を置く（U-I・FR-047）。

        Args:
            prefix: 欄の印の接頭辞（同じ画面に 2 組置くため）。
        """
        texts: dict[str, nui.input] = {}
        numbers: dict[str, nui.number] = {}
        for name, label in (*AGENT_TEXT_FIELDS, *AGENT_LIST_FIELDS):
            element = nui.input(label=label)
            element.mark(f"{prefix}_{name}")
            texts[name] = element
        mcps = nui.textarea(label=MCPS_LABEL)
        mcps.mark(f"{prefix}_{MCPS_FIELD}")
        for name, label in AGENT_INT_FIELDS:
            number = nui.number(
                label=label, value=_AGENT_NUMBER_MINIMUM, min=_AGENT_NUMBER_MINIMUM, precision=0
            )
            number.mark(f"{prefix}_{name}")
            numbers[name] = number
        self._agent_texts[prefix] = texts
        self._agent_numbers[prefix] = numbers
        self._agent_mcps[prefix] = mcps

    def _agent_body(self, prefix: str, marker: str) -> dict[str, object] | None:
        """**入力された項目だけ**を本文にする（U-I・FR-047・J-30）。

        空欄は送らない（「上書きしたい項目だけを入力させる」）。数値 3 種は **0 を未入力**と
        見なす。mcps の JSON が読めなければ、その旨を出して `None` を返す（要求を送らない）。

        Args:
            prefix: 欄の印の接頭辞。
            marker: 結果を出す書込ボタンの印。

        Returns:
            `AgentSpec` に載せる本文。mcps が読めなければ `None`。
        """
        body: dict[str, object] = {}
        for name, _label in AGENT_TEXT_FIELDS:
            value = str(self._agent_texts[prefix][name].value or "")
            if value:
                body[name] = value
        for name, _label in AGENT_LIST_FIELDS:
            value = str(self._agent_texts[prefix][name].value or "")
            if value.split():
                body[name] = value.split()
        raw = str(self._agent_mcps[prefix].value or "")
        if raw.strip():
            try:
                body[MCPS_FIELD] = json.loads(raw)
            except ValueError as exc:
                self._results.notice(marker, f"JSON として読めません: {exc}")
                return None
        for name, _label in AGENT_INT_FIELDS:
            number = self._agent_numbers[prefix][name].value
            if number is not None and int(number) > 0:
                body[name] = int(number)
        return body

    async def _save_global_agent(self) -> None:
        """ペルソナ既定の編集（`PUT /settings/agents/global/{persona_key}`）。

        全体の既定は必須 3 種（`role`・`goal`・`backstory`）を要するが、**それを見るのは候補の
        検査 V-04** であり画面では数えない（SC-013）。
        """
        persona = str(self._global_agent_persona.value or "")
        if not persona:
            return
        body = self._agent_body("global_agent", "global_agent_save")
        if body is None:
            return
        await self._write(
            "global_agent_save",
            "PUT",
            f"/settings/agents/global/{encode(persona)}",
            body,
            section="agents",
        )

    async def _confirm_member_key(self, confirmation: _Confirmation) -> frozenset[str]:
        """メンバ欄のプロジェクトキーの確定に伴う読取（data-model §6・J-13）。

        確定済みのキーは `_KeyField` が確定を受けた時点で更新している。最新でない確定は
        画面を変えない（design-sync B-33）。

        Args:
            confirmation: この確定。

        Returns:
            一時的な失敗（`status=0`・5xx）で終わった読込の名前（B-32・B-34 の再試行の対象）。
        """
        if not confirmation.key:
            if confirmation.is_latest():
                self._reads["members"] = None
                self._member_form.set_visibility(False)
                await self._sections["members"].refresh()
            return frozenset()

        async def reflect(name: str, response: ApiResponse) -> None:
            """読込を出し、読めたときだけメンバ欄を出す。"""
            self._reads[name] = response
            await self._sections[name].refresh()
            self._member_form.set_visibility(_is_success(response))

        return await self._confirm_reads(confirmation, reflect)

    async def _save_member(self, key: str) -> None:
        """メンバの編集（`PUT /settings/agents/{k}/{persona}`。K-1 は `_KeyField.guard` が見る）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        persona = str(self._agents_persona.value or "")
        if not persona or not key:
            return
        body = self._agent_body("agents", "agents_save")
        if body is None:
            return
        await self._write(
            "agents_save",
            "PUT",
            f"/settings/agents/{encode(key)}/{encode(persona)}",
            body,
            section="agents",
        )

    # -- 節 2: 段テンプレート -----------------------------------------------------------

    def _draw_tasks_section(self) -> None:
        """段テンプレート（**全体の 7 キー**と **PJ のキー単位の丸ごと置換**を別の欄に分ける）。

        それぞれ「段ごとの編集欄」（D26）を先に置き、JSON の一括編集欄を後ろに残す。
        """
        nui.label("段テンプレート").classes(_HEADER_CLASSES)
        nui.label(TASKS_FILE_NOTICE).classes(_CAPTION_CLASSES)
        stages = list(_templates_of(self._payload("tasks_global")))
        personas = [str(row.get("persona_key", "")) for row in _rows_of(self._payload("agents"))]
        nui.label("全体（7 キー）").classes(_SUBHEADER_CLASSES)
        self._stage_global = _StageForm("tasks_stage", stages, personas, self._fill_stage_global)
        self._fill_stage_global()
        self._button("この段を全体に保存", "tasks_stage_global_save", self._save_stage_global)
        nui.label("JSON で一括編集（7 キーをまとめて置き換える）").classes(_SUBHEADER_CLASSES)
        self._sections["tasks_global"]()
        self._tasks_global_body = nui.textarea(label="全体の 7 キー（JSON。段キー → テンプレート）")
        self._tasks_global_body.mark("tasks_global_body")
        self._button("全体を保存", "tasks_global_save", self._save_tasks_global)

        nui.label("プロジェクトの上書き").classes(_SUBHEADER_CLASSES)
        nui.label("上書きを全部消すと全体に戻ります（FR-070）").classes(_CAPTION_CLASSES)
        self._tasks_key = _KeyField("tasks_project", ("tasks_project",), self._confirm_tasks_key)
        self._tasks_project_area = nui.column().classes("gap-0 w-full")
        with self._tasks_project_area:
            self._sections["tasks_project"]()
        self._tasks_project_form = nui.column().classes(_FULL_WIDTH_CLASSES)
        self._tasks_project_form.set_visibility(False)
        with self._tasks_project_form:
            self._stage_project = _StageForm(
                "tasks_pj_stage", stages, personas, self._fill_stage_project
            )
            self._button(
                "この段を上書き保存",
                "tasks_stage_project_save",
                self._tasks_key.guard(self._save_stage_project),
            )
            self._button(
                "この段の上書きを外す",
                "tasks_stage_project_remove",
                self._tasks_key.guard(self._remove_stage_project),
            )
            nui.label("JSON で一括編集（上書き全体を置き換える）").classes(_SUBHEADER_CLASSES)
            self._tasks_project_body = nui.textarea(label="上書き（JSON。キー単位の丸ごと置換）")
            self._tasks_project_body.mark("tasks_project_body")
            self._button(
                "上書きを保存",
                "tasks_project_save",
                self._tasks_key.guard(self._save_tasks_project),
            )
            self._button(
                "上書きを全部解除", "tasks_clear", self._tasks_key.guard(self._clear_tasks_project)
            )

    async def _confirm_tasks_key(self, confirmation: _Confirmation) -> frozenset[str]:
        """段テンプレート節のプロジェクトキーの確定に伴う読取。

        確定済みのキーは `_KeyField` が確定を受けた時点で更新している。最新でない確定は
        画面を変えない（design-sync B-33）。

        Args:
            confirmation: この確定。

        Returns:
            一時的な失敗（`status=0`・5xx）で終わった読込の名前（B-32・B-34 の再試行の対象）。
        """
        if not confirmation.key:
            if confirmation.is_latest():
                self._reads["tasks_project"] = None
                self._tasks_project_form.set_visibility(False)
                await self._sections["tasks_project"].refresh()
            return frozenset()

        async def reflect(name: str, response: ApiResponse) -> None:
            """読込を出し、上書きの欄を出す（読めなくても出す）。段ごとの欄も入れ直す。"""
            self._reads[name] = response
            await self._sections[name].refresh()
            self._tasks_project_form.set_visibility(True)
            self._fill_stage_project()

        return await self._confirm_reads(confirmation, reflect)

    def _json_body(self, area: nui.textarea, marker: str) -> object | None:
        """JSON の本文を 1 つ受ける（空欄・読めない本文なら `None` ＝**送らない**）。

        読めないときだけ「JSON として読めません: …」を出す（空欄では何も出さない＝ J-20）。

        Args:
            area: 入力欄。
            marker: 結果を出す書込ボタンの印。

        Returns:
            読めた JSON。空欄または JSON として読めなければ `None`。
        """
        text = str(area.value or "")
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            self._results.notice(marker, f"JSON として読めません: {exc}")
            return None

    async def _save_tasks_global(self) -> None:
        """全体の 7 キーを保存する（`PUT /settings/tasks/global`）。"""
        body = self._json_body(self._tasks_global_body, "tasks_global_save")
        if body is None:
            return
        await self._write(
            "tasks_global_save",
            "PUT",
            "/settings/tasks/global",
            body,
            section="tasks",
            after=self._fill_stage_global,
        )

    async def _save_tasks_project(self, key: str) -> None:
        """上書きを保存する（`PUT /settings/tasks/{k}`）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        if not key:
            return
        body = self._json_body(self._tasks_project_body, "tasks_project_save")
        if body is None:
            return
        await self._write(
            "tasks_project_save",
            "PUT",
            f"/settings/tasks/{encode(key)}",
            body,
            section="tasks",
            after=self._fill_stage_project,
        )

    async def _clear_tasks_project(self, key: str) -> None:
        """上書きを全部解除する（`DELETE /settings/tasks/{k}`）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        if not key:
            return
        await self._write(
            "tasks_clear",
            "DELETE",
            f"/settings/tasks/{encode(key)}",
            None,
            section="tasks",
            after=self._fill_stage_project,
        )

    # -- 節 2 の段ごとの編集欄（D26） ----------------------------------------------------

    def _fill_stage_global(self) -> None:
        """全体の段ごとの欄へ、選んでいる段の現在値を入れる（全体の読取から）。"""
        form = self._stage_global
        form.fill(_templates_of(self._payload("tasks_global")).get(form.selected))

    def _fill_stage_project(self) -> None:
        """PJ の段ごとの欄へ値を入れる（上書きがあればそれ、無ければ全体の値）。

        キーが未確定なら何もしない（欄そのものが隠れている）。
        """
        if not self._tasks_key.confirmed:
            return
        form = self._stage_project
        stage = form.selected
        inherited = _templates_of(self._payload("tasks_global")).get(stage)
        response = self._reads.get("tasks_project")
        if response is None or not _is_success(response):
            form.state.set_text(STAGE_UNREADABLE)
            form.fill(inherited)
            return
        overrides = _templates_of(response.payload)
        if stage in overrides:
            form.state.set_text(STAGE_OVERRIDDEN)
            form.fill(overrides[stage])
            return
        form.state.set_text(STAGE_INHERITED)
        form.fill(inherited)

    async def _save_stage_global(self) -> None:
        """選んでいる段だけを全体に保存する（取り直した 7 キーの 1 段を差し替えて `PUT`）。"""
        stage = self._stage_global.selected
        if not stage:
            return
        body = self._stage_global.body()
        await self._write_stage(
            "tasks_stage_global_save",
            "/settings/tasks/global",
            lambda templates: {**templates, stage: body},
            after=self._fill_stage_global,
        )

    async def _save_stage_project(self, key: str) -> None:
        """選んでいる段だけを PJ で上書きする（取り直した上書きの 1 キーを差し替えて `PUT`）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        stage = self._stage_project.selected
        if not key or not stage:
            return
        body = self._stage_project.body()
        await self._write_stage(
            "tasks_stage_project_save",
            f"/settings/tasks/{encode(key)}",
            lambda templates: {**templates, stage: body},
            after=self._fill_stage_project,
        )

    async def _remove_stage_project(self, key: str) -> None:
        """選んでいる段の上書きだけを外す（残りが空なら `DELETE`＝全体の 7 キーに戻る）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        stage = self._stage_project.selected
        if not key or not stage:
            return

        def without(templates: dict[str, dict[str, object]]) -> Mapping[str, object] | None:
            """選んだ段を除く（上書きされていなければ `None`＝送らない）。"""
            if stage not in templates:
                return None
            return {name: spec for name, spec in templates.items() if name != stage}

        await self._write_stage(
            "tasks_stage_project_remove",
            f"/settings/tasks/{encode(key)}",
            without,
            after=self._fill_stage_project,
        )

    async def _write_stage(
        self,
        marker: str,
        path: str,
        edit: Callable[[dict[str, dict[str, object]]], Mapping[str, object] | None],
        *,
        after: Callable[[], None],
    ) -> None:
        """段 1 つの書込（D26）: 取り直し → 1 段を差し替え（外し）→ `PUT`（空なら `DELETE`）。

        画面を開いた後に別の書き手が変えた**ほかの段**を、古い読取で上書きしないよう、送る直前に
        同じ経路を読み直す（憲章 §2。読み直しと書込の間の競合は防がない＝排他を前提にしない）。

        Args:
            marker: 書込ボタンの印。
            path: 読み直しと書込の経路（符号化済み）。
            edit: 読み直した「段キー → 項目」から送る本文を作る（`None` なら送らない）。
            after: 成功して取り直した後に欄を入れ直す処理。
        """
        self._writes.set_enabled(enabled=False)
        try:
            current = await fetch(self._base_url, "GET", path)
            if not _is_success(current):
                self._results.failure(marker, current)
                return
            edited = edit(dict(_templates_of(current.payload)))
            if edited is None:
                self._results.notice(marker, STAGE_NOT_OVERRIDDEN_NOTICE)
                return
            method, body = ("PUT", edited) if edited else ("DELETE", None)
            response = await fetch(self._base_url, method, path, body)
            await self._finish(marker, response, section="tasks", after=after)
        finally:
            self._writes.set_enabled(enabled=True)

    # -- 節 3: LLM プロファイル ---------------------------------------------------------

    def _draw_profiles_section(self) -> None:
        """LLM プロファイル（**雛形 2 件**は画面側の定数＝ U-G・SC-029。鍵の実値は扱わない）。"""
        nui.label("LLM プロファイル").classes(_HEADER_CLASSES)
        self._sections["profiles"]()
        nui.label(SECRET_NOTICE).classes(_CAPTION_CLASSES)
        first = LLM_PRESETS[0][0]
        self._profile_preset = nui.select(
            [name for name, _url in LLM_PRESETS], value=first, label="雛形"
        )
        self._profile_preset.mark("profile_preset")
        self._profile_preset.on_value_change(lambda event: self._apply_preset(str(event.value)))
        self._profile_name = nui.input(label="名前", value=first)
        self._profile_name.mark("profile_name")
        self._profile_base = nui.input(label="接続先", value=dict(LLM_PRESETS)[first])
        self._profile_base.mark("profile_base")
        self._profile_model = nui.input(label="モデル")
        self._profile_model.mark("profile_model")
        self._profile_window = nui.number(
            label="文脈長", value=_CONTEXT_WINDOW_DEFAULT, min=1, precision=0
        )
        self._profile_window.mark("profile_window")
        self._profile_ref = nui.input(label="鍵の参照名")
        self._profile_ref.mark("profile_ref")
        self._button("プロファイルを保存", "profile_save", self._save_profile)

    def _apply_preset(self, preset: str) -> None:
        """雛形を選び直すと名前と接続先が置き換わる（D-3・J-20 (3)）。

        Args:
            preset: 選ばれた雛形の名前。
        """
        self._profile_name.set_value(preset)
        self._profile_base.set_value(dict(LLM_PRESETS).get(preset, ""))

    async def _save_profile(self) -> None:
        """プロファイルを保存する（`POST /settings/llm-profiles`）。"""
        name = str(self._profile_name.value or "")
        window = self._profile_window.value
        if not name or window is None:
            return
        await self._write(
            "profile_save",
            "POST",
            "/settings/llm-profiles",
            {
                "name": name,
                "provider_kind": "openrouter" if name == "openrouter" else "openai_compatible",
                "base_url": self._profile_base.value,
                "model_id": self._profile_model.value,
                "context_window": int(window),
                "api_key_ref": self._profile_ref.value,
            },
            section="profiles",
        )

    # -- 節 4: 上限 ---------------------------------------------------------------------

    def _draw_limits_section(self) -> None:
        """上限（**全体既定**と **PJ 上書き**をレベル付きで出す＝ U-A・R-11）。"""
        nui.label("上限").classes(_HEADER_CLASSES)
        self._sections["limits"]()
        nui.label("段・ペルソナ単位の上限は持ちません（FR-056）").classes(_CAPTION_CLASSES)
        self._limits_project = nui.input(label="プロジェクトキー")
        self._limits_project.mark("limits_project")
        self._limits_req = nui.number(
            label="1 kickoff の要求上限", value=_REQUESTS_DEFAULT, min=1, precision=0
        )
        self._limits_req.mark("limits_req")
        self._limits_cost = nui.number(label="1 kickoff の費用上限", value=_COST_DEFAULT, min=0.0)
        self._limits_cost.mark("limits_cost")
        self._button("全体既定を保存", "limits_global_save", self._save_limits_global)
        self._button("プロジェクトの上書きを保存", "limits_project_save", self._save_limits_project)

    def _limits_body(self) -> dict[str, object] | None:
        """上限の本文（要求上限・費用上限が空なら `None` ＝**送らない**＝ J-18）。

        Returns:
            本文。どちらかが空なら `None`。
        """
        requests_limit = self._limits_req.value
        cost_limit = self._limits_cost.value
        if requests_limit is None or cost_limit is None:
            return None
        return {
            "max_requests_per_kickoff": int(requests_limit),
            "max_cost_per_kickoff": float(cost_limit),
        }

    async def _save_limits_global(self) -> None:
        """全体既定を保存する（`PUT /settings/limits`）。"""
        body = self._limits_body()
        if body is None:
            return
        await self._write("limits_global_save", "PUT", "/settings/limits", body, section="limits")

    async def _save_limits_project(self) -> None:
        """プロジェクトの上書きを保存する（`PUT /settings/limits/{k}`。確定を要しない）。"""
        key = str(self._limits_project.value or "")
        body = self._limits_body()
        if not key or body is None:
            return
        await self._write(
            "limits_project_save",
            "PUT",
            f"/settings/limits/{encode(key)}",
            body,
            section="limits",
        )

    # -- 節 5: コマンド定義 -------------------------------------------------------------

    def _draw_commands_section(self) -> None:
        """コマンド定義（**即時に効く**＝ P5D-016）。"""
        nui.label("コマンド定義").classes(_HEADER_CLASSES)
        self._sections["commands"]()
        self._command_name = nui.input(label="登録名")
        self._command_name.mark("command_name")
        self._command_argv = nui.input(label="引数列（空白区切り）")
        self._command_argv.mark("command_argv")
        self._button("コマンド定義を保存", "command_save", self._save_command)

    async def _save_command(self) -> None:
        """コマンド定義を保存する（`PUT /settings/commands/{name}`）。"""
        name = str(self._command_name.value or "")
        if not name:
            return
        await self._write(
            "command_save",
            "PUT",
            f"/settings/commands/{encode(name)}",
            {"name": name, "argv": str(self._command_argv.value or "").split()},
            section="commands",
        )

    # -- 節 6: 権限・ルール・Hook・人間 -------------------------------------------------

    def _draw_policies_section(self) -> None:
        """権限・共通ルール・Hook・人間の行為者。"""
        nui.label("権限・ルール・Hook・人間").classes(_HEADER_CLASSES)
        nui.label("人間の行為者").classes(_SUBHEADER_CLASSES)
        self._sections["humans"]()
        self._humans_body = nui.textarea(label="行為者名（1 行 1 件）")
        self._humans_body.mark("humans_body")
        self._button("人間の行為者を保存", "humans_save", self._save_humans)

        self._policies_key = _KeyField(
            "policies_project", ("policies", "hooks", "rules"), self._confirm_policies_key
        )
        self._policies_form = nui.column().classes(_FULL_WIDTH_CLASSES)
        self._policies_form.set_visibility(False)
        with self._policies_form:
            nui.label("権限表").classes(_SUBHEADER_CLASSES)
            self._sections["policies"]()
            self._policy_body = nui.textarea(label=POLICY_ROWS_LABEL)
            self._policy_body.mark("policy_body")
            self._button("権限表を保存", "policy_save", self._policies_key.guard(self._save_policy))

            nui.label("共通ルール").classes(_SUBHEADER_CLASSES)
            self._rules_area = nui.column().classes("gap-0 w-full")
            self._rules_form = nui.column().classes(_FULL_WIDTH_CLASSES)
            with self._rules_form:
                self._rules = nui.textarea(label="本文（空でも保存できます）")
                self._rules.mark("rules")
                self._button(
                    "共通ルールを保存", "rules_save", self._policies_key.guard(self._save_rules)
                )

            nui.label("Hook").classes(_SUBHEADER_CLASSES)
            self._sections["hooks"]()
            nui.label("宣言の形式だけを検査します（接続も PATH 解決もしません）").classes(
                _CAPTION_CLASSES
            )
            self._hooks_body = nui.textarea(
                label='宣言（JSON。{"hooks": [{"name": …, "points": […]}]}）'
            )
            self._hooks_body.mark("hooks_body")
            self._button("Hook を保存", "hooks_save", self._policies_key.guard(self._save_hooks))

    async def _save_humans(self) -> None:
        """人間の行為者を保存する（`PUT /settings/humans`。必須欄は無い）。"""
        actors = str(self._humans_body.value or "")
        await self._write(
            "humans_save",
            "PUT",
            "/settings/humans",
            {"human_actors": [line.strip() for line in actors.splitlines() if line.strip()]},
            section="policies",
        )

    async def _confirm_policies_key(self, confirmation: _Confirmation) -> frozenset[str]:
        """権限・ルール・Hook 節のプロジェクトキーの確定に伴う読取（D-4・J-14）。

        確定済みのキーは `_KeyField` が確定を受けた時点で更新している。最新でない確定は
        画面も共通ルールの本文欄も変えない（design-sync B-33。前のキーの本文を次のキーへ
        送らないため）。同じキーでの再試行では、一時的に失敗した読込だけを読む（B-34。
        権限表・Hook だけの失敗では、共通ルールの書きかけの本文を消さない）。

        Args:
            confirmation: この確定。

        Returns:
            一時的な失敗（`status=0`・5xx）で終わった読込の名前（B-32・B-34 の再試行の対象）。
        """
        if not confirmation.key:
            if confirmation.is_latest():
                self._reads["policies"] = None
                self._reads["hooks"] = None
                self._policies_form.set_visibility(False)
                await self._sections["policies"].refresh()
                await self._sections["hooks"].refresh()
            return frozenset()

        async def reflect(name: str, response: ApiResponse) -> None:
            """読込を出す（共通ルールは本文欄へ入れる）。"""
            if name == "rules":
                self._show_rules(response)
                return
            self._reads[name] = response
            await self._sections[name].refresh()
            self._policies_form.set_visibility(True)

        return await self._confirm_reads(confirmation, reflect)

    def _show_rules(self, response: ApiResponse) -> None:
        """共通ルールの読込の結果を本文欄へ入れる（D-4・J-14）。

        **表示ではないので取り直しでは送らない**（`_SECTION_READS` に入れない）。

        Args:
            response: `GET /settings/rules/projects/{k}` の応答（最新の確定のものだけが来る）。
        """
        self._rules_area.clear()
        if _is_success(response):
            self._rules.set_value(str(_as_mapping(response.payload).get("body", "")))
            self._rules_form.set_visibility(True)
            return
        if response.status == _HTTP_NOT_FOUND:
            # J-14: 404 なら本文を空にして「404: {detail}」を出す（欄と保存ボタンは出す）
            self._rules.set_value("")
            self._rules_form.set_visibility(True)
            with self._rules_area:
                _draw_failure(response)
            return
        # J-14: `status=0`・5xx なら誤りを出し、本文欄と保存ボタンを出さない
        self._rules_form.set_visibility(False)
        with self._rules_area:
            _draw_failure(response)

    def _policy_rows(self, text: str) -> list[dict[str, object]]:
        """`policy.csv` と同じ 1 行 1 件の形を `PolicyRow` の形へ写す。

        **引用符を足さない**（FR-029。足すと V-21 が孤児として拒否する）。列数（`p`=3・`g`=2）と
        行の種別は**口の型と候補の判定器**が見るので、ここでは数えない（SC-013）。

        Args:
            text: 1 行 1 件の権限行（`p, 主体, 対象, 動作` ／ `g, 主体, 役割`）。

        Returns:
            `PolicySpec.rows` に載せる形。
        """
        rows: list[dict[str, object]] = []
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.split(",") if cell.strip()]
            if cells:
                rows.append({"ptype": cells[0], "fields": cells[1:]})
        return rows

    async def _save_policy(self, key: str) -> None:
        """権限表を保存する（`PUT /settings/policies/{k}`。**即時**に効く＝ P5D-016）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        if not key:
            return
        await self._write(
            "policy_save",
            "PUT",
            f"/settings/policies/{encode(key)}",
            {"rows": self._policy_rows(str(self._policy_body.value or ""))},
            section="policies",
        )

    async def _save_rules(self, key: str) -> None:
        """共通ルールを保存する（`PUT /settings/rules/projects/{k}`。**空文字も可**＝ FR-065）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        if not key:
            return
        await self._write(
            "rules_save",
            "PUT",
            f"/settings/rules/projects/{encode(key)}",
            {"body": self._rules.value},
            section="policies",
        )

    async def _save_hooks(self, key: str) -> None:
        """Hook 宣言を保存する（`PUT /settings/hooks/{k}`。**V-18** は形式検査だけ＝ REC-02）。

        Args:
            key: 送り先のキー（押した時点の確定済みのキー）。
        """
        if not key:
            return
        body = self._json_body(self._hooks_body, "hooks_save")
        if body is None:
            return
        await self._write(
            "hooks_save",
            "PUT",
            f"/settings/hooks/{encode(key)}",
            body,
            section="policies",
        )

    # -- 書込の共通処理（R-1・W-1・J-12・J-15） -----------------------------------------

    def _button(self, text: str, marker: str, action: Callable[[], Awaitable[None] | None]) -> None:
        """書込ボタンと、その直後の結果の表示域を置く（J-12・J-21 の並び）。

        Args:
            text: ボタン名。
            marker: 印。
            action: 押したときの処理（キーに依存する書込は `_KeyField.guard` を通したもの）。
        """
        button = nui.button(text, on_click=action)
        button.mark(marker)
        self._writes.add(button)
        self._results.area(marker)

    async def _write(
        self,
        marker: str,
        method: str,
        path: str,
        body: object | None,
        *,
        section: str,
        after: Callable[[], None] | None = None,
    ) -> None:
        """書込を 1 本送り、2xx のときだけその節の読取表示を取り直す（R-1・W-1）。

        Args:
            marker: 書込ボタンの印。
            method: HTTP メソッド。
            path: 経路（符号化済み）。
            body: JSON 本文。
            section: 取り直す節の名前（`_SECTION_READS` のキー）。
            after: 成功して取り直した後の処理（段ごとの欄の入れ直し＝ D26）。
        """
        self._writes.set_enabled(enabled=False)
        try:
            response = await fetch(self._base_url, method, path, body)
            await self._finish(marker, response, section=section, after=after)
        finally:
            self._writes.set_enabled(enabled=True)

    async def _finish(
        self,
        marker: str,
        response: ApiResponse,
        *,
        section: str,
        after: Callable[[], None] | None,
    ) -> None:
        """書込の応答を出し、2xx のときだけ節を取り直して `after` を呼ぶ。

        Args:
            marker: 書込ボタンの印。
            response: 書込の応答。
            section: 取り直す節の名前（`_SECTION_READS` のキー）。
            after: 取り直した後の処理（無ければ `None`）。
        """
        if not _is_success(response):
            self._results.failure(marker, response)
            return
        self._results.success(marker, success_message(response))
        await self._load(*_SECTION_READS[section])
        if after is not None:
            after()


# --------------------------------------------------------------------------------------
# プロジェクトページ（UC-08）
# --------------------------------------------------------------------------------------


def _tool_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """装備と許可の対照（`RoleToolView` の並び）を表の行にする（D26）。

    Args:
        rows: `{"tool", "equipped", "allowed"}` の並び。

    Returns:
        表の行（片方だけ真なら「状態」に食い違いを書く）。
    """
    table: list[dict[str, object]] = []
    for row in rows:
        equipped = row.get("equipped") is True
        allowed = row.get("allowed") is True
        state = ""
        if equipped and not allowed:
            state = ROLE_EQUIPPED_ONLY
        elif allowed and not equipped:
            state = ROLE_ALLOWED_ONLY
        table.append(
            {
                "Tool": row.get("tool"),
                "装備": _MARK if equipped else _NO_MARK,
                "許可": _MARK if allowed else _NO_MARK,
                "状態": state,
            }
        )
    return table


def _draw_roles(payload: object) -> None:
    """役割ごとの実効設定（`RolesView`）を描く（D26。閲覧のみ）。

    Args:
        payload: `GET /settings/projects/{k}/roles` の本文。
    """
    view = _as_mapping(payload)
    roles = _rows_of(view.get("roles"))
    if not roles:
        nui.label(ROLES_NO_MEMBERS).classes(_CAPTION_CLASSES)
    for role in roles:
        persona = str(role.get("persona_key", ""))
        with nui.card().props("flat bordered").classes(_FULL_WIDTH_CLASSES):
            _draw_role(persona, role)
    stage_tools = _rows_of(view.get("stage_tools"))
    if stage_tools:
        nui.label(ROLES_STAGE_TOOLS_HEADING).classes(_SUBHEADER_CLASSES)
        _table(
            [
                {
                    "段": row.get("stage"),
                    "道具": " ".join(str(t) for t in _as_list(row.get("tools"))),
                }
                for row in stage_tools
            ],
            marker="roles-stage-tools",
        )


def _draw_role(persona: str, role: Mapping[str, object]) -> None:
    """役割 1 つ（見出し・LLM・既定担当の段・装備と許可・2 段目の許可・段の装備の置き換え）。

    Args:
        persona: ペルソナキー（表の印に使う）。
        role: `RoleView` の 1 件。
    """
    nui.label(f"{persona}（{role.get('role', '')}）").classes(_SUBHEADER_CLASSES)
    stages = _rows_of(role.get("stages"))
    names = "、".join(str(stage.get("stage", "")) for stage in stages) or "なし"
    nui.label(f"LLM: {role.get('llm_profile', '')} ／ 権限の主体: {role.get('casbin_subject', '')}")
    nui.label(f"既定の担当の段: {names}")
    tools = _tool_rows(_rows_of(role.get("tools")))
    mismatches = sum(1 for row in tools if row["状態"] == ROLE_EQUIPPED_ONLY)
    if mismatches:
        nui.label(f"装備しているのに許可されていない Tool が {mismatches} 件あります").classes(
            _ERROR_CLASSES
        )
    _table(tools, marker=f"roles-tools-{persona}")
    grants = _rows_of(role.get("grants"))
    nui.label("2 段目の許可（vault・cmd・repo）").classes(_CAPTION_CLASSES)
    if grants:
        _table(
            [{"対象": grant.get("obj"), "動作": grant.get("act")} for grant in grants],
            marker=f"roles-grants-{persona}",
        )
    else:
        nui.label("なし")
    for stage in stages:
        if stage.get("replaces_tools") is True:
            nui.label(f"段 {stage.get('stage')} では装備を置き換えます").classes(_CAPTION_CLASSES)
            _table(
                _tool_rows(_rows_of(stage.get("tools"))),
                marker=f"roles-stage-{persona}-{stage.get('stage')}",
            )


class _ProjectsScreen:
    """プロジェクトページ 1 枚ぶんの状態と描画。"""

    _results: _Results
    _writes: _Writes
    _pj_key: nui.input
    _pj_name: nui.input
    _pj_input: nui.input
    _pj_outcome: nui.input
    _pj_remote_url: nui.input
    _pj_local_path: nui.input
    _pj_release_key: nui.input
    _roles_project: nui.select | None
    _roles_area: nui.column

    def __init__(self, base_url: str) -> None:
        """初期化する。

        Args:
            base_url: app の base URL。
        """
        self._base_url = base_url
        self._listed: ApiResponse | None = None
        self._roles: dict[str, nui.input] = {}
        self._results = _Results()
        self._writes = _Writes()
        self._listing = nui.refreshable(self._draw_listing)
        self._confirmed_key = ""
        # 欄（部品の id）→ その欄へ最後に読み込んだ値（読み込む前は空文字＝ D24 §6）
        self._loaded: dict[int, str] = {}
        self._roles_project = None
        self._roles_generation = 0

    async def draw(self) -> None:
        """ページの中身を描く。"""
        nui.label("プロジェクト").classes(_TITLE_CLASSES)
        nui.label(f"接続先 app: {self._base_url}").classes(_CAPTION_CLASSES)
        self._listed = await fetch(self._base_url, "GET", "/settings/projects")
        if self._listed.status != _HTTP_OK:
            # 移行前どおり: 非 200 なら応答を出して以降を描かない（N-5・D-2 相当）
            _draw_failure(self._listed)
            return
        with nui.card():
            self._listing()
        with nui.card().classes(_FULL_WIDTH_CLASSES):
            self._draw_roles_card()
        personas = [
            str(row.get("persona_key", ""))
            for row in _rows_of((await fetch(self._base_url, "GET", "/settings/agents")).payload)
        ]
        with nui.card():
            self._draw_form(personas)
        with nui.card():
            self._draw_release()

    def _draw_listing(self) -> None:
        """プロジェクト一覧（読取表示）。"""
        response = self._listed
        if response is None:
            return
        if not _is_success(response):
            _draw_failure(response)
            return
        _table(_rows_of(response.payload), marker="projects-table")
        self._sync_role_options()

    # -- 役割ごとの設定（D26。閲覧のみ） ------------------------------------------------

    def _project_keys(self) -> list[str]:
        """取得済みの一覧にあるプロジェクトキー（**要求は送らない**）。"""
        listed = self._listed.payload if self._listed is not None else None
        return [str(row.get("project_key", "")) for row in _rows_of(listed)]

    def _draw_roles_card(self) -> None:
        """役割ごとの設定の節（プロジェクトを選んで「表示」を押すと 1 本だけ読む）。

        開いた時点・キー欄の確定・書込の後には読まない（読取表示は一覧 1 本のまま＝ R-1）。
        """
        nui.label("役割ごとの設定（閲覧のみ）").classes(_HEADER_CLASSES)
        nui.label(ROLES_SOURCE_NOTICE).classes(_CAPTION_CLASSES)
        with nui.row().classes("items-center"):
            self._roles_project = nui.select(self._project_keys(), label="プロジェクト")
            self._roles_project.classes("min-w-48")
            self._roles_project.mark("roles_project")
            nui.button("表示", on_click=self._show_roles).mark("roles_show")
        self._roles_area = nui.column().classes(f"gap-2 {_FULL_WIDTH_CLASSES}")
        self._roles_area.mark("roles_area")

    def _sync_role_options(self) -> None:
        """一覧を取り直したら、役割の節の選択肢も揃える（節を描く前なら何もしない）。"""
        if self._roles_project is None:
            return
        keys = self._project_keys()
        # NiceGUI の set_options は options を型引数なしの List | Dict で注釈している
        self._roles_project.set_options(keys)  # pyright: ignore[reportUnknownMemberType]
        if self._roles_project.value not in keys:
            self._roles_project.set_value(None)

    async def _show_roles(self) -> None:
        """選んだプロジェクトの役割ごとの設定を読み、節に描く（最後に押した分だけを出す）。"""
        if self._roles_project is None:
            return
        key = str(self._roles_project.value or "")
        if not key:
            return
        self._roles_generation += 1
        generation = self._roles_generation
        response = await fetch(self._base_url, "GET", f"/settings/projects/{encode(key)}/roles")
        if generation != self._roles_generation:
            return
        self._roles_area.clear()
        with self._roles_area:
            if not _is_success(response):
                _draw_failure(response)
                return
            _draw_roles(response.payload)

    def _draw_form(self, personas: Sequence[str]) -> None:
        """登録・編集（フォルダ対応付け 3 種は**すべて必須**）。

        キー欄を確定すると、登録済みのプロジェクトなら現在の値が欄に入る（`_on_key_confirm`）。

        Args:
            personas: 役割フォルダ欄を出すペルソナ。
        """
        nui.label("登録・編集").classes(_HEADER_CLASSES)
        nui.label("フォルダの実体は作りません（先に保管庫側で用意してください）").classes(
            _CAPTION_CLASSES
        )
        self._pj_key = nui.input(label="プロジェクトキー")
        self._pj_key.mark("pj_key")
        self._pj_key.on("keydown.enter", self._on_key_confirm)
        self._pj_key.on("blur", self._on_key_confirm)
        self._pj_name = nui.input(label="名前")
        self._pj_name.mark("pj_name")
        self._pj_input = nui.input(label="入力フォルダ")
        self._pj_input.mark("pj_input")
        self._pj_outcome = nui.input(label="成果物フォルダ")
        self._pj_outcome.mark("pj_outcome")
        for persona in personas:
            element = nui.input(label=f"役割フォルダ（{persona}）")
            element.mark(f"pj_role_{persona}")
            self._roles[persona] = element
        # CFG-74・D24: リポジトリ対応付けは**任意**（両方空なら未登録のまま）
        self._pj_remote_url = nui.input(label="リモートリポジトリの URL（任意）")
        self._pj_remote_url.mark("pj_remote_url")
        self._pj_local_path = nui.input(label="ローカルリポジトリのパス（任意・OS の絶対パス）")
        self._pj_local_path.mark("pj_local_path")
        self._button("登録", "pj_create", self._create)
        self._button("編集", "pj_update", self._update)

    def _draw_release(self) -> None:
        """解除（**フォルダ実体は消えません**＝ FR-007・SC-010）。"""
        nui.label("解除").classes(_HEADER_CLASSES)
        nui.label("退避するだけで、保管庫のフォルダ実体は 1 つも消えません").classes(
            _CAPTION_CLASSES
        )
        self._pj_release_key = nui.input(label="解除するプロジェクトキー")
        self._pj_release_key.mark("pj_release_key")
        self._button("解除", "pj_release", self._release)

    def _on_key_confirm(self) -> None:
        """キー欄の確定で、登録済みのプロジェクトの現在の値を欄へ読み込む（D24 §6）。

        読み込む元は取得済みの一覧（`GET /settings/projects`）の行で、**要求は送らない**。
        確定はキー欄の Enter かフォーカスの移動である（data-model §6 の「確定」と同じ）。

        - キーが前回の確定と同じなら何もしない（書きかけの入力を読み直しで消さない）。
        - 一覧に無いキー（新規の登録）なら欄を変えない。
        - 前回読み込んだ値のままの欄（利用者が書き換えていない欄）だけを書き込む（trigger の
          J-10 と同じ規則）。⚠ マウスで書込ボタンを押すと、キー欄の blur が click より**先に**
          届く（B-31）。確定より前に手で入れた値をここで上書きすると、その値は送られない。

        判定と書き込みは、イベントを受けた時点で同期的に済ませる（`_KeyField` と同じ理由）。
        """
        key = str(self._pj_key.value or "")
        if key == self._confirmed_key:
            return
        self._confirmed_key = key
        listed = self._listed.payload if self._listed is not None else None
        row = next((row for row in _rows_of(listed) if row.get("project_key") == key), None)
        if row is None:
            return
        for element, value in self._values_of(row):
            if str(element.value or "") == self._loaded.get(element.id, ""):
                element.set_value(value)
                self._loaded[element.id] = value

    def _values_of(self, row: Mapping[str, object]) -> list[tuple[nui.input, str]]:
        """一覧の 1 行を、欄とその値の組に写す（値の無い欄は空文字）。

        Args:
            row: `GET /settings/projects` の 1 行（`ProjectView`）。

        Returns:
            `(欄, 値)` の並び。役割フォルダは画面に欄のあるペルソナだけ。
        """
        folders = _as_mapping(row.get("folders"))
        roles = _as_mapping(folders.get("role_dirs"))
        repos = _as_mapping(row.get("repos"))
        pairs: list[tuple[nui.input, object]] = [
            (self._pj_name, row.get("name")),
            (self._pj_input, folders.get("pj_input_dir")),
            (self._pj_outcome, folders.get("outcome_dir")),
            *((element, roles.get(persona)) for persona, element in self._roles.items()),
            (self._pj_remote_url, repos.get("remote_url")),
            (self._pj_local_path, repos.get("local_path")),
        ]
        return [(element, "" if value is None else str(value)) for element, value in pairs]

    def _body(self) -> dict[str, object]:
        """登録・編集の本文（値を入れたペルソナだけを `role_dirs` に載せる）。

        `repos` は**値を入れた欄があるときだけ**載せる（CFG-74・D24。両方空なら載せない）。
        登録済みのプロジェクトは、キーの確定で 2 欄に現在の値が入る（`_on_key_confirm`）ので、
        書き換えずに編集しても `repos` は消えない。2 欄を空にして編集すると、`updateProject` の
        全置換で `repos` が外れる（解除。クラスタ 02 #17）。

        Returns:
            `POST`／`PUT` の本文。
        """
        repos = {
            key: value
            for key, value in (
                ("remote_url", self._pj_remote_url.value),
                ("local_path", self._pj_local_path.value),
            )
            if value
        }
        body: dict[str, object] = {
            "project_key": self._pj_key.value,
            "name": self._pj_name.value,
            "folders": {
                "pj_input_dir": self._pj_input.value,
                "role_dirs": {
                    persona: element.value
                    for persona, element in self._roles.items()
                    if element.value
                },
                "outcome_dir": self._pj_outcome.value,
            },
        }
        if repos:
            body["repos"] = repos
        return body

    async def _create(self) -> None:
        """登録する（`POST /settings/projects`）。"""
        key = str(self._pj_key.value or "")
        if not key:
            return
        await self._write("pj_create", "POST", "/settings/projects", self._body())

    async def _update(self) -> None:
        """編集する（`PUT /settings/projects/{k}`）。"""
        key = str(self._pj_key.value or "")
        if not key:
            return
        await self._write("pj_update", "PUT", f"/settings/projects/{encode(key)}", self._body())

    async def _release(self) -> None:
        """解除する（`DELETE /settings/projects/{k}`。本文なし）。"""
        key = str(self._pj_release_key.value or "")
        if not key:
            return
        await self._write("pj_release", "DELETE", f"/settings/projects/{encode(key)}", None)

    def _button(self, text: str, marker: str, action: Callable[[], Awaitable[None]]) -> None:
        """書込ボタンと、その直後の結果の表示域を置く。

        Args:
            text: ボタン名。
            marker: 印。
            action: 押したときの処理。
        """
        button = nui.button(text, on_click=action)
        button.mark(marker)
        self._writes.add(button)
        self._results.area(marker)

    async def _write(self, marker: str, method: str, path: str, body: object | None) -> None:
        """書込を 1 本送り、2xx のときだけ一覧を取り直す（R-1）。

        ⚠ `GET /settings/agents` は**取り直さない** —— 役割フォルダ欄の組み立てに使う読取で、
        表示ではない（data-model §5 の「読取表示に数えないもの」）。

        Args:
            marker: 書込ボタンの印。
            method: HTTP メソッド。
            path: 経路（符号化済み）。
            body: JSON 本文。
        """
        self._writes.set_enabled(enabled=False)
        try:
            response = await fetch(self._base_url, method, path, body)
            if not _is_success(response):
                self._results.failure(marker, response)
                return
            self._results.success(marker, success_message(response))
            self._listed = await fetch(self._base_url, "GET", "/settings/projects")
            await self._listing.refresh()
        finally:
            self._writes.set_enabled(enabled=True)
