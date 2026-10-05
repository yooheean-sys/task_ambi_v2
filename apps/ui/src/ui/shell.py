"""UI の殻（contracts/ui-shell.md §4）。経路の登録・共通の見出し・入口・0 枚の案内。

**業務ロジックを持たない**。ページの中身は feature 側の `render` が描き、殻は共通の見出しと
ナビゲーション、それに全ページに共通の体裁（`apply_theme`・中央寄せの枠）だけを描く。

体裁は NiceGUI 標準の組み方に寄せる（`nui.header` の帯・`nui.card`・既定の classes/props）。
見た目は等価の対象外（`016` J-19）なので、文字・印・並びは変えずに体裁だけを足す（`016`
design-sync B-39）。

⚠ NiceGUI の `ui` は **`nui`** と別名で import する（パッケージ名 `ui` と取り違えないため＝
ui-shell §1）。

⚠ 経路を登録するのは `register_pages` が**呼ばれた時点だけ**である（FR-023）。import しただけ
では NiceGUI の経路が 1 件も増えない（S-12）。

⚠ 関数の中で遅延 import をしない（規約 §3 の 4。`user` フィクスチャの後始末が本モジュールを
`sys.modules` から消すため、遅延 import は消えた後の再読込を招く）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui as nui

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from ui.pages import UiPage

NO_PAGES_MESSAGE = "有効化された UI ページがありません"
"""ページ 0 枚のときに出す案内（`002/U-1`・US3 AS-4。文言は移行前のまま）。"""

_HEADING_CLASSES = "text-xl font-bold"
"""共通の見出し（アプリ名）の見た目。等価の対象ではない（ui-pages §4 の「見た目」）。"""

_HEADER_CLASSES = "items-center gap-6 px-6 py-3"
"""見出しの帯（`nui.header`）の並び。"""

_NAV_CLASSES = "gap-4"
"""ナビゲーションの並びの見た目。"""

_LINK_CLASSES = "text-white no-underline opacity-80 hover:opacity-100"
"""帯の上のリンクの見た目（帯の地色の上で読めるように白字にする）。"""

_CURRENT_LINK_CLASSES = "text-white no-underline font-bold"
"""いま開いているページのリンクの見た目。"""

_CONTENT_CLASSES = "w-full max-w-5xl mx-auto"
"""ページの中身を載せる枠（中央寄せ・最大幅）。"""

_FIELD_TYPES = (nui.input, nui.textarea, nui.number, nui.select)
"""既定の体裁を当てる入力部品。

⚠ NiceGUI の部品はクラス定義の時点で既定値を複写する（`Element.__init_subclass__`）ので、
`nui.textarea` は `nui.input` の既定を継がない。型ごとに当てる。
"""

_FIELD_CLASSES = "w-full"
"""入力部品の既定の classes（NiceGUI の既定は中身の幅まで縮み、ラベルが省略される）。"""

_FIELD_PROPS = "outlined dense"
"""入力部品の既定の props。"""

_TABLE_CLASSES = "w-full"
"""表の既定の classes。"""

_TABLE_PROPS = "flat bordered wrap-cells"
"""表の既定の props（Quasar の表は既定で折り返さず、長いセルで画面を突き抜ける）。"""

_CARD_CLASSES = "w-full"
"""カードの既定の classes（節の枠を枠いっぱいに広げる）。"""


def apply_theme() -> None:
    """全ページに共通の既定の体裁を当てる（NiceGUI の `default_classes`・`default_props`）。

    以後に作られる部品にだけ効く。`Classes.update_list` が重複を除くので、何度呼んでも結果は
    同じである。`user` フィクスチャはテストごとに既定値を退避して戻す
    （`nicegui.testing.general.nicegui_reset_globals`）。
    """
    for field_type in _FIELD_TYPES:
        field_type.default_classes(_FIELD_CLASSES)
        field_type.default_props(_FIELD_PROPS)
    nui.table.default_classes(_TABLE_CLASSES)
    nui.table.default_props(_TABLE_PROPS)
    nui.card.default_classes(_CARD_CLASSES)


def register_pages(pages: Sequence[UiPage], *, app_name: str, base_url: str) -> None:
    """選ばれたページの経路と入口 `/` を登録する（ui-shell §4）。

    公開 URL は `/ui/{slug}` になる（接頭辞 `/ui` は `run_with` の `mount_path` が付ける）。

    Args:
        pages: 殻に載せるページ部品（登録順）。
        app_name: 共通の見出しに出すアプリ名（ルート設定の `app_name`）。
        base_url: 画面が接続する app の base URL（0 枚のときの案内に出す）。
    """
    apply_theme()
    ordered = tuple(pages)
    for page in ordered:
        nui.page(f"/{page.slug}", title=page.title)(_page_view(page, ordered, app_name=app_name))
    nui.page("/", title=app_name)(_entry_view(ordered, app_name=app_name, base_url=base_url))


def _heading(app_name: str, pages: Sequence[UiPage], *, current: str | None = None) -> None:
    """共通の見出し（アプリ名と全ページへのリンク）を、画面上部の帯に描く（J-21）。

    リンクは登録順（`select_pages` の並び）に並べる。リンクの接頭辞 `/ui` は NiceGUI が
    クライアント側で付ける（research R-07）。

    Args:
        app_name: アプリ名。
        pages: 全ページ（リンクの並びの元）。
        current: いま開いているページのスラグ（そのリンクを太字にする）。
    """
    with nui.header().classes(_HEADER_CLASSES):
        nui.label(app_name).classes(_HEADING_CLASSES)
        if pages:
            with nui.row().classes(_NAV_CLASSES):
                for page in pages:
                    classes = _CURRENT_LINK_CLASSES if page.slug == current else _LINK_CLASSES
                    nui.link(page.title, f"/{page.slug}").classes(classes)


def _page_view(
    page: UiPage, pages: Sequence[UiPage], *, app_name: str
) -> Callable[[], Awaitable[None]]:
    """1 枚のページの経路に登録する関数を作る。

    見出しを描いてから **`await nui.context.client.connected()` を待って** 中身を描く
    （FR-012a・J-8）。NiceGUI はページの組み立てが `response_timeout`（既定 3 秒）を超えると
    誤りの画面を返すが、接続を待ってから読めば、遅い読取（1 回あたり最大 10 秒＝ FR-013）が
    この制限にかからない。`response_timeout` は既定のままとする。

    Args:
        page: 描くページ部品。
        pages: 全ページ（ナビゲーションの元）。
        app_name: アプリ名。

    Returns:
        `nui.page` に渡す非同期関数。
    """

    async def _view() -> None:
        _heading(app_name, pages, current=page.slug)
        with nui.column().classes(_CONTENT_CLASSES):
            await nui.context.client.connected()
            await page.render()

    return _view


def _entry_view(
    pages: Sequence[UiPage], *, app_name: str, base_url: str
) -> Callable[[], Awaitable[None]]:
    """入口 `/` に登録する関数を作る（ui-shell §4）。

    ページが 1 枚以上なら共通の見出しと**最初のページの中身**を描く（旧 `st.navigation` の
    既定ページと同じ）。0 枚ならアプリ名・案内・接続先を描く。

    Args:
        pages: 全ページ。
        app_name: アプリ名。
        base_url: 画面が接続する app の base URL。

    Returns:
        `nui.page` に渡す非同期関数。
    """

    async def _view() -> None:
        if pages:
            _heading(app_name, pages, current=pages[0].slug)
            with nui.column().classes(_CONTENT_CLASSES):
                await nui.context.client.connected()
                await pages[0].render()
            return
        _heading(app_name, pages)
        with nui.column().classes(_CONTENT_CLASSES):
            nui.label(NO_PAGES_MESSAGE)
            nui.label(f"接続先 app: {base_url}")

    return _view
