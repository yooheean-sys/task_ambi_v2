"""トリガー画面 1 ページ（trigger-F12）。`016` contracts/ui-pages.md。

⚠ **本ファイルは `trigger.*` を 1 つも import しない**（憲章 2.0.0 §4 注記・U-1・FR-014）。
import してよいのは `nicegui` と、標準ライブラリの `__future__`・`asyncio`・`collections`・
`dataclasses`・`json`・`typing`・`urllib` だけである（ui-pages §2。`collections` は型注釈だけに
使う）。モジュールの操作は app の HTTP API 経由で行い、HTTP 境界は JSON なので DTO
（`trigger.schemas`）を共有する必要も無い。

この形により (1) SC-013（画面のソースに `trigger.*` の import が 0 件）が**自明に真**になり、
(2) `ui-pages` 契約が `allow_indirect_imports` **なしで**成立し、(3) 画面のテストが trigger の
組み立てを一切要さない。

**`httpx`・`requests` を使わない**（`httpx-scope` が trigger を対象側に含む＝ 010 SD-06・U-2）。
**環境依存値の層を読まない**（接続先は登録の閉包が引数で渡す＝ 010 SD-05・U-3）。

⚠ **`ui.page` を呼ばない**（research R-06）。経路を登録するのは apps/ui の殻だけである。NiceGUI の
`user` フィクスチャは後始末でページ関数のモジュールを `sys.modules` から消すため、feature 側に
ページ関数を置くと消される。
⚠ **`app.storage` を使わない・`storage_secret` を渡さない**（FR-021・憲章 2.0.0 §6）。画面の状態は
部品そのものが持つ。

⚠ **`U-n` の番号は `011` の contracts/ui-page.md §2 が正本**（`plan.md` (viii) の同名の表は
**別の対応表**で、`U-4` から先がずれる）。本ファイルは契約側だけを引く。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from nicegui import ui as nui

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

TIMEOUT_SECONDS = 10
"""要求 1 回の待ち時間（**再試行しない**＝憲章 §10・U-4。落ちたら接続エラーを出すだけ）。"""

UNCHANGED_NOTICE = "宣言ファイルは 1 バイトも変更されていません"
"""422 のときに**必ず添える**文言（FR-027 の帰結を利用者に伝える）。"""

NO_RECOVERY_NOTICE = (
    "監視スレッドが停止しています。システムは自分で復旧しません（再起動が要ります）"
)
"""`alive=false` に添える文言（憲章 §10・N-7・US2 AC6）。"""

UNFINISHED_LABEL = "実行中／未終了"
"""`status=started` のままの行の表示（US1 AC5・US5 AC2）。"""

TRIGGER_TYPES = ("cron", "interval", "file")

SPEC_HELP: dict[str, str] = {
    "cron": "crontab 5 欄（秒を持たない）。曜日は 0 が月曜（標準 crontab の 7＝日曜は通らない）",
    "interval": "秒を表す整数（設定の min_interval_sec 以上）",
    "file": "DL フォルダからの相対パス（既定は .）。基準は .env の設定値",
}
"""`type` に応じた入力補助。**補助は説明であって検査ではない**（判定の正本は口の側）。

`cron`・`file` は、移行前に Streamlit が Markdown として描いていた記号（`**`・`` ` ``）を除いた
**描画後の文字**である（`016` ui-pages §4 の書き換え 6 箇所のうち 2 件）。
"""

DEFAULT_TRIGGER_NOTICE = "既定トリガーは間隔と有効／無効だけを変更できます（FN-30）"
"""既定トリガーを選んだときの予告（**先回りするだけ**で、拒否の正本は口）。"""

MECHANISM_HEADING = "トリガーの仕組み"
TRIGGER_FILE_NOTICE = (
    "定義ファイル: トリガーフォルダ（領域 B）の triggers.yaml。この画面の「追加／編集」で"
    "書き換わります（ファイルを直接編集した場合は、app を再起動するまで反映されません）"
)
TRIGGER_DEFINES_NOTICE = (
    "トリガーが決めるのは「いつ動かすか（種別・仕様・有効）」と「どの課題を拾うか（起動条件）」"
    "だけです。既定トリガー（ready-sweep）は削除できず、変えられるのは仕様と有効だけです"
)
FLOW_HEADING = "発火すると（どのトリガーでも同じ処理で、コードで固定されています）"
FLOW_STEPS = (
    "1. 起動条件（ラベル・プロジェクト）に合う ready の課題を 1 件確保する（bd ready --claim）",
    "2. 担当（agents.yaml）・段テンプレート（tasks.yaml）・共通ルール（rules.md）から"
    "指示文を組み立てる",
    "3. AI が実行する",
    "4. 結果を判定し、次の課題へ進む",
)
PROMPT_NOTICE = "AI への指示文を変えるには、段テンプレートを編集します:"
PROMPT_LINK_TEXT = "AI 設定 › 段テンプレート"
PROMPT_LINK_TARGET = "/settings-ai"
"""「トリガーの仕組み」の文言（D26。発火後の処理そのものは orchestrator のコードで、画面からは
変えられない。変えられるのは指示文の材料＝ settings の段テンプレート等）。

リンク先は**経路の文字列だけ**を持つ（settings を import しない＝ ui-pages 契約）。書き方は殻の
`/{slug}` と同じ相対の経路で、接頭辞 `/ui` は `run_with` の `mount_path` が付ける。settings を
有効化していない構成ではリンク先が無い（許容する）。定義ファイルの実値・環境変数のキー名は
画面に持たせない（U-3）。
"""

CONDITION_ALL_PROJECTS = "全て"
"""起動条件に PJ の指定が無いときの表示（全 PJ の課題が対象）。"""

HISTORY_COLUMNS = (
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
"""履歴で画面に出す列（15 列のうち 11 列）。"""

NEW_TRIGGER_LABEL = "（新規追加）"
"""編集対象の選択肢の先頭（新規追加）。"""

DEFAULT_LABELS = "role:executor"
"""起動条件のラベルの既定値。"""

EDIT_TARGET_KEY = "edit-target"
EDIT_TYPE_KEY = "edit-type"
EDIT_SPEC_KEY = "edit-spec"
"""追加／編集の部品の印（移行前の Streamlit の `key` と同名＝ ui-pages §6）。"""

EDIT_NAME_KEY = "edit-name"
EDIT_DESCRIPTION_KEY = "edit-description"
EDIT_LABELS_KEY = "edit-labels"
EDIT_PROJECT_KEY = "edit-project"
EDIT_ENABLED_KEY = "edit-enabled"
EDIT_SAVE_KEY = "edit-save"
"""移行前は key を持たなかった部品に、本 feature で新たに付ける印（ui-pages §6）。"""

RESULT_KEY = "result"
"""ページ上部の結果表示（操作の成功も失敗もここに 1 つだけ出す＝ J-12・E-2）。"""

WATCHERS_TABLE_KEY = "watchers-table"
TRIGGERS_TABLE_KEY = "triggers-table"
HISTORY_TABLE_KEY = "history-table"
HISTORY_MONTH_KEY = "history-month"
"""表と月の欄の印（ui-pages §6）。"""

_LOCKED_ON_DEFAULT = (
    EDIT_NAME_KEY,
    EDIT_DESCRIPTION_KEY,
    EDIT_TYPE_KEY,
    EDIT_LABELS_KEY,
    EDIT_PROJECT_KEY,
)
"""既定トリガーを選んでいる間、読み取り専用にする 5 項目（仕様と有効は変更できる）。"""

_NEW_DEFAULTS: dict[str, object] = {
    EDIT_NAME_KEY: "",
    EDIT_DESCRIPTION_KEY: "",
    EDIT_TYPE_KEY: "interval",
    EDIT_SPEC_KEY: "",
    EDIT_LABELS_KEY: DEFAULT_LABELS,
    EDIT_PROJECT_KEY: "",
    EDIT_ENABLED_KEY: True,
}
"""「（新規追加）」のときの 7 項目の既定値（data-model §6）。"""

_TITLE_CLASSES = "text-xl font-bold"
_SECTION_CLASSES = "text-lg font-bold"
_ROW_HEADING_CLASSES = "font-bold"
_CAPTION_CLASSES = "text-sm text-gray-500"
_SUCCESS_CLASSES = "text-positive"
_ERROR_CLASSES = "text-negative"
_NOTICE_CLASSES = "text-info"
"""見た目（等価の対象は成功・誤り・案内が**見分けられる**ことだけ＝ J-19・ui-pages §4）。"""

_FULL_WIDTH_CLASSES = "w-full"
"""枠いっぱいに広げる（NiceGUI の既定は中身の幅まで縮む）。"""

_ROW_CARD_PROPS = "flat bordered"
"""一覧の 1 件ごとの枠（影なし・枠線あり）。"""

_COLUMN_STYLE = "min-width: 6rem"
"""表の列の最小幅（セルは折り返す＝ `ui.shell` の既定なので、日本語の短い列が 1 文字幅まで潰れない
ようにする）。"""

_HTTP_OK = 200
_HTTP_MULTIPLE_CHOICES = 300
_HTTP_NOT_FOUND = 404
_HTTP_UNPROCESSABLE = 422


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """`call_api` の戻り。**成功も検査違反も同じ形**（応答コード ＋ 本文の写像）。"""

    status: int
    """HTTP の応答コード。**接続不能なら 0**。"""

    payload: object
    """JSON を読んだ結果。本文が無ければ `None`。"""

    error: str | None = None
    """接続不能・本文が読めない等の説明。正常なら `None`。"""


def call_api(base_url: str, method: str, path: str, body: object | None = None) -> ApiResponse:
    """HTTP API を 1 回叩く（**唯一の差し替え点**＝ U-2）。

    `urllib.error.HTTPError` は**それ自体がファイルオブジェクト**なので `exc.read()` で本文を
    読み、`exc.code` と組で返す（**422 の違反一覧・404 の理由を取りこぼさない**）。
    `URLError`（`HTTPError` を除く）と `OSError` は `status=0` にする。
    **自動復旧も再試行も持たない**（憲章 §10・U-4）。

    ⚠ **同期のまま変えない**（FR-013）。待受を塞がないための `asyncio.to_thread` は `fetch` が
    行う。符号化も行わない（呼ぶ側が `encode` を通す＝ D-5）。

    Args:
        base_url: app の base URL（登録の閉包が渡す）。
        method: HTTP メソッド。
        path: `/triggers`・`/history`・`/watcher`・`/hooks/file` の経路。
        body: JSON 本文（無ければ `None`）。

    Returns:
        応答コード・本文・接続エラーの説明。
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
            return ApiResponse(status=stream.status, payload=_read_json(stream.read()))
    except urllib.error.HTTPError as exc:
        # **本文を必ず読む** —— 422 の `problems` と 404 の `detail` はここにしか無い。
        return ApiResponse(status=exc.code, payload=_read_json(exc.read()))
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
    """`call_api` を**待受の外**で呼ぶ（FR-012・research R-08）。

    `asyncio.to_thread` に出すので、10 秒のタイムアウトが待受（同じプロセスの HTTP API を含む）を
    塞がない。スレッドの中で **`URLError`・`OSError` だけ**を `status=0` の `ApiResponse` に畳む
    （US6 AC3: 接続できなくても画面が落ちない）。**それ以外の例外は畳まない**（旧 `_fetch` と
    同じ＝ data-model §4）。`call_api` は自分で `URLError` を捕まえるが、**差し替え点**である以上
    ここでも畳む（差し替えた `call_api` が `URLError` を投げても落ちない＝ ui-pages N-14）。

    Args:
        base_url: app の base URL。
        method: HTTP メソッド。
        path: 経路（符号化は呼ぶ側が済ませている）。
        body: JSON 本文。

    Returns:
        応答（接続できなければ `status=0`）。
    """

    def _call() -> ApiResponse:
        try:
            return call_api(base_url, method, path, body)
        except (urllib.error.URLError, OSError) as exc:
            return ApiResponse(status=0, payload=None, error=f"app に接続できません: {exc}")

    return await asyncio.to_thread(_call)


def encode(value: object) -> str:
    """利用者の値を経路の段・クエリの値として符号化する（D-5・J-9）。

    英数・`-`・`_` だけの値では、符号化しても移行前と同じ文字列になる。

    Args:
        value: 符号化する値。

    Returns:
        `urllib.parse.quote(..., safe="")` を通した文字列。
    """
    return urllib.parse.quote(str(value), safe="")


@dataclass(frozen=True, slots=True)
class TriggerPage:
    """トリガーページの部品（apps/ui の構造型 `UiPage` に構造で適合する＝ data-model §2）。"""

    title: str
    """ナビゲーションとタブに出すタイトル。"""

    slug: str
    """経路スラグ（公開 URL は `/ui/{slug}`）。"""

    render: Callable[[], Awaitable[None]]
    """ページの中身だけを描く非同期関数（共通の見出しは殻が描く）。"""


def build_triggers_page(base_url: str) -> TriggerPage:
    """「トリガー」ページ（UC-09/10）の部品を作る。

    **`ui.page` を呼ばない**（経路の登録は apps/ui の殻だけが行う）。

    Args:
        base_url: app の base URL。

    Returns:
        タイトル・経路スラグ・描画関数を持つページ部品。
    """
    return TriggerPage(title="トリガー", slug="triggers", render=lambda: render_triggers(base_url))


async def render_triggers(base_url: str) -> None:
    """「トリガー」ページの中身を描く（テストが直接呼べるよう公開する）。

    Args:
        base_url: app の base URL。
    """
    await _Screen(base_url).draw()


# --------------------------------------------------------------------------------------
# 応答の扱い（`011` contracts/ui-page.md §4・`016` ui-pages §5-2）
# --------------------------------------------------------------------------------------


def _is_success(response: ApiResponse) -> bool:
    """2xx かどうか。"""
    return _HTTP_OK <= response.status < _HTTP_MULTIPLE_CHOICES


def _problems_of(payload: object) -> list[dict[str, object]]:
    """422 の本文から違反の一覧を取り出す（形が違えば空）。"""
    return [_as_mapping(item) for item in _as_list(_as_mapping(payload).get("problems"))]


def _error_texts(response: ApiResponse) -> list[str]:
    """2xx でない応答を、画面に出す文言の並びにする（E-3・E-5・J-11・J-13）。

    422 は**件数つきの文言と 1 件 1 行の違反**（`kind` は出さない＝ SD-15）、404 は
    「404: {detail}」、それ以外は「{コード}: {本文}」、接続できないときは接続の説明である。

    Args:
        response: 応答。

    Returns:
        1 行 1 要素の文言（成功の応答を渡してはならない）。
    """
    if response.status == 0:
        return [response.error or "app に接続できません"]
    if response.status == _HTTP_UNPROCESSABLE:
        problems = _problems_of(response.payload)
        lines = [f"検査に落ちました（違反 {len(problems)} 件）。{UNCHANGED_NOTICE}"]
        lines.extend(f"{item.get('where')}: {item.get('reason')}" for item in problems)
        if not problems:
            lines.append(str(response.payload))
        return lines
    if response.status == _HTTP_NOT_FOUND:
        return [f"{response.status}: {_as_mapping(response.payload).get('detail', '')}"]
    return [f"{response.status}: {response.payload}"]


def _draw_error(response: ApiResponse) -> None:
    """2xx でない応答を、いま描いている位置に出す（D-7・J-13 の読取表示の誤り）。

    Args:
        response: 応答。
    """
    for text in _error_texts(response):
        nui.label(text).classes(_ERROR_CLASSES)


def _cell(value: object) -> str:
    """表のセルの文字（辞書・配列は JSON にする＝ ui-pages §4）。"""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
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


def _list_row(row: Mapping[str, object]) -> dict[str, object]:
    """1 件を表の 1 行へ写す（**`kind` は表示しない**＝常に `trigger_spec` で情報が無い）。"""
    return {
        "名前": row.get("name"),
        "説明": row.get("description"),
        "種別": row.get("type"),
        "仕様": row.get("spec"),
        "起動条件": _condition_label(row.get("condition")),
        "有効": row.get("enabled"),
        "次回実行時刻": row.get("next_fire_at") or "—",
        "直近結果": _last_result_label(row.get("last_result")),
        "既定": row.get("is_default"),
    }


def _condition_label(payload: object) -> str:
    """起動条件を 1 行にする（D26。`bd ready --claim` に渡すラベルと PJ の絞り込み）。"""
    condition = _as_mapping(payload)
    labels = " ".join(str(label) for label in _as_list(condition.get("labels"))) or "なし"
    project = condition.get("project_key") or CONDITION_ALL_PROJECTS
    return f"ラベル: {labels} ／ PJ: {project}"


def _draw_mechanism() -> None:
    """「トリガーの仕組み」（D26。静的な説明と、指示文の編集画面への導線）。"""
    nui.label(MECHANISM_HEADING).classes(_SECTION_CLASSES)
    nui.label(TRIGGER_DEFINES_NOTICE)
    nui.label(TRIGGER_FILE_NOTICE).classes(_CAPTION_CLASSES)
    nui.label(FLOW_HEADING).classes(_ROW_HEADING_CLASSES)
    with nui.column().classes("gap-0"):
        for step in FLOW_STEPS:
            nui.label(step)
    with nui.row().classes("items-center gap-1"):
        nui.label(PROMPT_NOTICE)
        nui.link(PROMPT_LINK_TEXT, PROMPT_LINK_TARGET).mark("prompt-link")


def _last_result_label(payload: object) -> str:
    """直近結果（`started` のままなら「実行中／未終了」＝ US5 AC2）。"""
    if payload is None:
        return "—"
    row = _as_mapping(payload)
    status = str(row.get("status", ""))
    if status == "started":
        return UNFINISHED_LABEL
    return f"{status}（{row.get('finished_at') or '—'}）"


def _history_row(row: Mapping[str, object]) -> dict[str, object]:
    """1 行を表示用に写す（`status=started` を「終わっていない」と分かる形にする＝ US5 AC2）。"""
    view: dict[str, object] = {column: row.get(column) for column in HISTORY_COLUMNS}
    if str(row.get("status")) == "started":
        view["status"] = f"started（{UNFINISHED_LABEL}）"
    return view


def _row_label(row: Mapping[str, object]) -> str:
    """編集対象の選択肢に出す表示名。"""
    return f"{row.get('name')}（{row.get('trigger_id')}）"


def _normalized_type(kind: object) -> str:
    """`type` の現在値（未設定・未知の語なら `interval`）。"""
    return str(kind) if kind in TRIGGER_TYPES else "interval"


def _fields_of(row: Mapping[str, object]) -> dict[str, object]:
    """選んだトリガーの 7 項目を読む（data-model §6 の表）。

    Args:
        row: トリガー 1 件。

    Returns:
        印 → 値。
    """
    condition = _as_mapping(row.get("condition"))
    labels = " ".join(str(item) for item in _as_list(condition.get("labels")))
    return {
        EDIT_NAME_KEY: str(row.get("name", "")),
        EDIT_DESCRIPTION_KEY: str(row.get("description", "")),
        EDIT_TYPE_KEY: _normalized_type(row.get("type")),
        EDIT_SPEC_KEY: str(row.get("spec", "")),
        EDIT_LABELS_KEY: labels or DEFAULT_LABELS,
        EDIT_PROJECT_KEY: str(condition.get("project_key") or ""),
        EDIT_ENABLED_KEY: bool(row.get("enabled", True)),
    }


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


# --------------------------------------------------------------------------------------
# 画面（ui-pages §4〜§6）
# --------------------------------------------------------------------------------------


class _Screen:
    """トリガー画面 1 枚ぶんの状態と描画。

    画面の状態は部品そのものと本インスタンスが持つ（`app.storage` を使わない＝ FR-021）。
    """

    _result: nui.column
    _default_notice: nui.column
    _spec_help: nui.column
    _target_select: nui.select
    _name: nui.input
    _description: nui.input
    _type: nui.select
    _spec: nui.input
    _labels: nui.input
    _project: nui.input
    _enabled: nui.checkbox
    _month_input: nui.input

    def __init__(self, base_url: str) -> None:
        """初期化する。

        Args:
            base_url: app の base URL。
        """
        self._base_url = base_url
        self._watcher = ApiResponse(status=0, payload=None)
        self._triggers = ApiResponse(status=0, payload=None)
        self._history = ApiResponse(status=0, payload=None)
        self._month = ""
        self._target = NEW_TRIGGER_LABEL
        self._loaded: dict[str, object] = dict(_NEW_DEFAULTS)
        self._static_buttons: list[nui.button] = []
        self._row_buttons: list[tuple[nui.button, bool]] = []
        self._watcher_section = nui.refreshable(self._draw_watcher)
        self._listing_section = nui.refreshable(self._draw_listing)
        self._history_section = nui.refreshable(self._draw_history)

    async def draw(self) -> None:
        """ページの中身を描く（見出し → 結果表示 → 常駐の状態 → 仕組みの説明 → 3 区画）。"""
        nui.label("トリガー").classes(_TITLE_CLASSES)
        nui.label(f"接続先 app: {self._base_url}").classes(_CAPTION_CLASSES)
        self._result = nui.column().classes("gap-0")
        self._result.mark(RESULT_KEY)

        self._watcher = await fetch(self._base_url, "GET", "/watcher")
        if self._watcher.status == 0:
            # `011/§4`・US6 AC3: **接続できない旨だけ**を出し、以降の描画を諦める
            _draw_error(self._watcher)
            return
        with nui.card():
            self._watcher_section()
        with nui.card().classes(_FULL_WIDTH_CLASSES):
            _draw_mechanism()

        self._triggers = await fetch(self._base_url, "GET", "/triggers")
        self._history = await fetch(self._base_url, "GET", self._history_path())
        with nui.card():
            with nui.tabs() as tabs:
                listing = nui.tab("一覧")
                editing = nui.tab("追加／編集")
                history = nui.tab("履歴")
            with nui.tab_panels(tabs, value=listing).classes(_FULL_WIDTH_CLASSES):
                with nui.tab_panel(listing):
                    self._listing_section()
                with nui.tab_panel(editing):
                    self._draw_editing()
                with nui.tab_panel(history):
                    self._draw_history_panel()

    # -- 上部の結果表示（J-12・E-2） ---------------------------------------------------

    def _succeed(self, message: str) -> None:
        """成功の旨を結果表示に 1 つだけ出す。

        Args:
            message: 出す文言。
        """
        self._result.clear()
        with self._result:
            nui.label(message).classes(_SUCCESS_CLASSES)

    def _fail(self, response: ApiResponse) -> None:
        """操作の失敗を結果表示に出す（前の結果は消す）。

        Args:
            response: 2xx でない応答。
        """
        self._result.clear()
        with self._result:
            _draw_error(response)

    # -- 常駐の状態（`011/§3`・D-6・J-11） ---------------------------------------------

    def _draw_watcher(self) -> None:
        """`scheduler_running`・`running`・`watchers[]` の生死を出す。"""
        nui.label("常駐の状態").classes(_SECTION_CLASSES)
        if not _is_success(self._watcher):
            # D-6・J-11: 0 以外の非 2xx は応答だけを出し、常駐状態は描かない（3 区画は描く）
            _draw_error(self._watcher)
            return
        state = _as_mapping(self._watcher.payload)
        nui.label(
            f"スケジューラ: {'稼働中' if state.get('scheduler_running') else '停止'}"
            f" / 進行中の発火: {'あり' if state.get('running') else 'なし'}"
        )
        watchers = [_as_mapping(item) for item in _as_list(state.get("watchers"))]
        if not watchers:
            nui.label("監視中のフォルダはありません")
            return
        _table(
            [
                {
                    "trigger_id": item.get("trigger_id"),
                    "watch_dir": item.get("watch_dir"),
                    "alive": item.get("alive"),
                }
                for item in watchers
            ],
            marker=WATCHERS_TABLE_KEY,
        )
        if any(not item.get("alive") for item in watchers):
            nui.label(NO_RECOVERY_NOTICE).classes(_ERROR_CLASSES)

    # -- 一覧（`011/§3` タブ 1・N-12） -------------------------------------------------

    def _trigger_rows(self) -> list[dict[str, object]]:
        """いま持っている一覧の行（読めなければ空）。"""
        if not _is_success(self._triggers):
            return []
        return [_as_mapping(item) for item in _as_list(self._triggers.payload)]

    def _draw_listing(self) -> None:
        """一覧・トグル・手動実行・削除。"""
        self._row_buttons = []
        if not _is_success(self._triggers):
            # J-12: 読取の失敗は上部ではなく区画の中に出す
            _draw_error(self._triggers)
            return
        rows = self._trigger_rows()
        nui.label(f"登録トリガー {len(rows)} 件").classes(_CAPTION_CLASSES)
        _table([_list_row(row) for row in rows], marker=TRIGGERS_TABLE_KEY)
        for row in rows:
            self._draw_row_actions(row)

    def _draw_row_actions(self, row: Mapping[str, object]) -> None:
        """1 件の操作（トグル・手動実行・削除）。

        Args:
            row: トリガー 1 件。
        """
        trigger_id = str(row.get("trigger_id", ""))
        enabled = bool(row.get("enabled"))
        is_default = bool(row.get("is_default"))
        segment = encode(trigger_id)
        with nui.card().props(_ROW_CARD_PROPS):
            nui.label(f"{row.get('name')}（{trigger_id}）").classes(_ROW_HEADING_CLASSES)
            nui.label(
                f"次回実行時刻: {row.get('next_fire_at') or '—'}"
                f" / 直近結果: {_last_result_label(row.get('last_result'))}"
            )
            with nui.row():
                self._row_button(
                    "無効にする" if enabled else "有効にする",
                    f"toggle-{trigger_id}",
                    lambda: self._operate(
                        "PATCH", f"/triggers/{segment}", {"enabled": not enabled}
                    ),
                )
                self._row_button(
                    "今すぐ実行",
                    f"run-{trigger_id}",
                    lambda: self._operate(
                        "POST", f"/triggers/{segment}/run", message=_accepted_with_run_id
                    ),
                )
                self._row_button(
                    "削除",
                    f"delete-{trigger_id}",
                    lambda: self._operate("DELETE", f"/triggers/{segment}"),
                    always_disabled=is_default,
                )

    def _row_button(
        self,
        text: str,
        marker: str,
        action: Callable[[], Awaitable[None]],
        *,
        always_disabled: bool = False,
    ) -> None:
        """一覧の行の操作ボタンを 1 つ置く。

        Args:
            text: ボタン名。
            marker: 印。
            action: 押したときの処理。
            always_disabled: 恒久的に押せない（既定トリガーの削除）。
        """
        button = nui.button(text, on_click=action)
        button.mark(marker)
        if always_disabled:
            button.disable()
        self._row_buttons.append((button, always_disabled))

    # -- 追加／編集（`011/§3` タブ 2・E-1・N-17・N-22） --------------------------------

    def _draw_editing(self) -> None:
        """追加と全量の編集（7 項目は取り直しでも作り直さない＝ J-10）。"""
        rows = self._trigger_rows()
        self._target_select = nui.select(
            [NEW_TRIGGER_LABEL, *[_row_label(row) for row in rows]],
            value=NEW_TRIGGER_LABEL,
            label="編集対象",
        )
        self._target_select.mark(EDIT_TARGET_KEY)
        self._target_select.on_value_change(lambda event: self._select_target(str(event.value)))
        self._default_notice = nui.column().classes("gap-0")

        self._name = nui.input(label="名前", value="")
        self._name.mark(EDIT_NAME_KEY)
        self._description = nui.input(label="説明", value="")
        self._description.mark(EDIT_DESCRIPTION_KEY)
        self._type = nui.select(list(TRIGGER_TYPES), value="interval", label="種別")
        self._type.mark(EDIT_TYPE_KEY)
        self._type.on_value_change(lambda _event: self._draw_spec_help())
        self._spec_help = nui.column().classes("gap-0")
        self._draw_spec_help()
        self._spec = nui.input(label="仕様", value="")
        self._spec.mark(EDIT_SPEC_KEY)
        self._labels = nui.input(label="起動条件のラベル（空白区切り）", value=DEFAULT_LABELS)
        self._labels.mark(EDIT_LABELS_KEY)
        self._project = nui.input(label="プロジェクトキー（任意）", value="")
        self._project.mark(EDIT_PROJECT_KEY)
        self._enabled = nui.checkbox("有効", value=True)
        self._enabled.mark(EDIT_ENABLED_KEY)

        save = nui.button("保存", on_click=self._save)
        save.mark(EDIT_SAVE_KEY)
        self._static_buttons.append(save)

    def _draw_spec_help(self) -> None:
        """種別に応じた入力補助を出し直す（E-4）。"""
        self._spec_help.clear()
        with self._spec_help:
            nui.label(SPEC_HELP[_normalized_type(self._type.value)]).classes(_CAPTION_CLASSES)

    def _row_for(self, label: str) -> dict[str, object] | None:
        """選択肢の表示名から一覧の行を引く（「（新規追加）」なら `None`）。

        Args:
            label: 表示名。

        Returns:
            該当する行。無ければ `None`。
        """
        return next((row for row in self._trigger_rows() if _row_label(row) == label), None)

    def _select_target(self, label: str) -> None:
        """編集対象が変わったら 7 項目を読み込み直す（E-1・data-model §6）。

        Args:
            label: 選ばれた表示名。
        """
        self._target = label
        row = self._row_for(label)
        values = _fields_of(row) if row is not None else dict(_NEW_DEFAULTS)
        for key, value in values.items():
            self._apply_field(key, value)
        self._loaded = values
        self._apply_default_lock(row)

    def _apply_field(self, key: str, value: object) -> None:
        """7 項目のうち 1 つに値を入れる。

        Args:
            key: 項目の印。
            value: 入れる値。
        """
        if key == EDIT_ENABLED_KEY:
            self._enabled.set_value(bool(value))
            return
        if key == EDIT_TYPE_KEY:
            self._type.set_value(str(value))
            return
        texts: dict[str, nui.input] = {
            EDIT_NAME_KEY: self._name,
            EDIT_DESCRIPTION_KEY: self._description,
            EDIT_SPEC_KEY: self._spec,
            EDIT_LABELS_KEY: self._labels,
            EDIT_PROJECT_KEY: self._project,
        }
        texts[key].set_value(str(value))

    def _apply_default_lock(self, row: Mapping[str, object] | None) -> None:
        """既定トリガーなら 5 項目を読み取り専用にし、予告を出す（`011/§3`）。

        Args:
            row: 選ばれた行（新規なら `None`）。
        """
        is_default = bool(row.get("is_default")) if row is not None else False
        self._default_notice.clear()
        if is_default:
            with self._default_notice:
                nui.label(DEFAULT_TRIGGER_NOTICE).classes(_NOTICE_CLASSES)
        locked: dict[str, nui.input | nui.select] = {
            EDIT_NAME_KEY: self._name,
            EDIT_DESCRIPTION_KEY: self._description,
            EDIT_TYPE_KEY: self._type,
            EDIT_LABELS_KEY: self._labels,
            EDIT_PROJECT_KEY: self._project,
        }
        for key in _LOCKED_ON_DEFAULT:
            locked[key].set_enabled(not is_default)

    def _sync_editing(self) -> None:
        """取り直しの後、選択肢を差し替えて変わった項目だけを書き込む（J-10・E-1）。"""
        rows = self._trigger_rows()
        labels = [NEW_TRIGGER_LABEL, *[_row_label(row) for row in rows]]
        if self._target not in labels:
            # 選んでいた相手が消えた → 新規に戻す（`set_value` が `_select_target` を呼ぶ）
            self._target_select.set_options(labels, value=NEW_TRIGGER_LABEL)  # pyright: ignore[reportUnknownMemberType]  # nicegui 3.17.1 の `set_options` は `options: list | dict` の素のジェネリクスで、要素の型が解決しきれない
            return
        self._target_select.set_options(labels, value=self._target)  # pyright: ignore[reportUnknownMemberType]  # 同上
        row = self._row_for(self._target)
        if row is None:
            return
        fresh = _fields_of(row)
        for key, value in fresh.items():
            if self._loaded.get(key) != value:
                self._apply_field(key, value)
        self._loaded = fresh
        self._apply_default_lock(row)

    async def _save(self) -> None:
        """保存（既存なら `PUT`、新規なら `POST`。本文は 7 項目）。"""
        body: dict[str, object] = {
            "name": self._name.value,
            "description": self._description.value,
            "type": self._type.value,
            "spec": self._spec.value,
            "condition": {
                "labels": str(self._labels.value).split(),
                "project_key": self._project.value or None,
            },
            "enabled": bool(self._enabled.value),
        }
        row = self._row_for(self._target)
        if row is None:
            await self._operate("POST", "/triggers", body)
            return
        await self._operate("PUT", f"/triggers/{encode(row.get('trigger_id'))}", body)

    # -- 履歴（`011/§3` タブ 3・US2 AS-8） --------------------------------------------

    def _history_path(self) -> str:
        """いまの月に対応する読取の経路（空なら当月＝ `month` を付けない）。"""
        if not self._month:
            return "/history"
        return f"/history?month={encode(self._month)}"

    def _draw_history_panel(self) -> None:
        """月の欄（確定で読み直す）と履歴の表示。"""
        self._month_input = nui.input(label="月（YYYYMM。空なら当月）", value="")
        self._month_input.mark(HISTORY_MONTH_KEY)
        self._month_input.on("keydown.enter", self._confirm_month)
        self._month_input.on("blur", self._confirm_month)
        self._history_section()

    async def _confirm_month(self) -> None:
        """月の欄の確定（Enter・フォーカスの移動）で履歴を読み直す（data-model §6）。"""
        self._month = str(self._month_input.value or "")
        self._history = await fetch(self._base_url, "GET", self._history_path())
        await self._history_section.refresh()

    def _draw_history(self) -> None:
        """月の実行履歴（**無い月は文言**＝ `011` SD-13・§0-3 の例外）。"""
        if not _is_success(self._history):
            # J-12: 読取の失敗は上部ではなく区画の中に出す
            _draw_error(self._history)
            return
        rows = [_as_mapping(item) for item in _as_list(self._history.payload)]
        if not rows:
            nui.label("この月の実行履歴はありません")
            return
        nui.label(f"{len(rows)} 件").classes(_CAPTION_CLASSES)
        unfinished = [row for row in rows if str(row.get("status")) == "started"]
        if unfinished:
            nui.label(
                f"{len(unfinished)} 件が {UNFINISHED_LABEL} です（プロセスが落ちた run を含む）"
            ).classes(_NOTICE_CLASSES)
        _table([_history_row(row) for row in rows], marker=HISTORY_TABLE_KEY)

    # -- 操作と取り直し（E-2・E-5・E-6・J-15） ----------------------------------------

    async def _operate(
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        message: Callable[[ApiResponse], str] = lambda _response: "受け付けました",
    ) -> None:
        """書込を 1 本送り、成功なら 3 つの読取を取り直す（E-2・E-6）。

        Args:
            method: HTTP メソッド。
            path: 経路（符号化済み）。
            body: JSON 本文。
            message: 成功の文言を作る関数。
        """
        self._set_operable(enabled=False)
        try:
            response = await fetch(self._base_url, method, path, body)
            if not _is_success(response):
                # E-5: 失敗（接続できないときを含む）は上部に出し、ページはそのまま残す
                self._fail(response)
                return
            self._succeed(message(response))
            await self._reload()
        finally:
            self._set_operable(enabled=True)

    async def _reload(self) -> None:
        """常駐の状態・一覧・履歴をそれぞれ 1 回ずつ取り直す（§7）。"""
        self._watcher = await fetch(self._base_url, "GET", "/watcher")
        self._triggers = await fetch(self._base_url, "GET", "/triggers")
        self._history = await fetch(self._base_url, "GET", self._history_path())
        await self._watcher_section.refresh()
        await self._listing_section.refresh()
        await self._history_section.refresh()
        self._sync_editing()

    def _set_operable(self, *, enabled: bool) -> None:
        """ページの操作ボタンをまとめて押せる／押せなくする（E-6・J-15）。

        Args:
            enabled: 押せるようにするなら True。
        """
        for button in self._static_buttons:
            if not button.is_deleted:
                button.set_enabled(enabled)
        for button, always_disabled in self._row_buttons:
            if button.is_deleted:
                continue
            button.set_enabled(enabled and not always_disabled)


def _accepted_with_run_id(response: ApiResponse) -> str:
    """手動実行の成功の文言（`run_id` を添える）。

    Args:
        response: 成功の応答。

    Returns:
        画面に出す文言。
    """
    return f"受け付けました（run_id: {_as_mapping(response.payload).get('run_id')}）"
