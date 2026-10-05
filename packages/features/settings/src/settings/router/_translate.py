"""例外 → 応答の翻訳を**1 箇所に閉じる**（FR-078・contracts/settings-http.md §4）。

**上記以外は素通しさせない** —— 内部例外を口の外へ出さない。起動時例外の系統
（`SettingsStartupError`）は保存経路では起きない（`SettingsAreaError` だけが候補 policy の
読めなさで関わるが、`service/save.py` が `ValidationProblem(kind="orphan")` に翻訳済み）。

**本ファイルに `from __future__ import annotations` を置かない**（`router/` の 6 ファイル共通。
F-20 の実測。`Annotated[..., Depends(局所 provider)]` の解決が壊れるのを避けるため、同じ
サブパッケージ内で扱いを揃える）。
"""

from collections.abc import Callable

from fastapi import Response, status
from fastapi.responses import JSONResponse

from common.interfaces.settings import MemberNotFound, ProjectNotFound, UnknownCommand
from settings.exceptions import (
    ProjectDeletionBlocked,
    SettingsItemNotFound,
    SettingsValidationError,
)
from settings.models import SaveOutcome
from settings.schemas import DetailBody, ProblemBody, ValidationProblemItem

NOT_FOUND_ERRORS = (ProjectNotFound, MemberNotFound, UnknownCommand, SettingsItemNotFound)
"""**404** に翻訳する例外（契約例外 3 ＋ 口の信号 1）。"""

_NOT_FOUND_PHRASES: dict[type[Exception], str] = {
    ProjectNotFound: "プロジェクトが見つからない",
    MemberNotFound: "メンバが見つからない",
    UnknownCommand: "コマンド定義が見つからない",
}
"""契約例外の**メッセージは識別子そのもの**（前半の流儀。1 行も変えない）なので、口では
何が見つからないのかを添える。`SettingsItemNotFound` は既に文になっているのでそのまま出す。
"""


def not_found_detail(exc: Exception) -> str:
    """**404** の `detail`（単一文字列）を組む。"""
    phrase = _NOT_FOUND_PHRASES.get(type(exc))
    return f"{phrase}: {exc}" if phrase is not None else str(exc)


def run[T](action: Callable[[], T]) -> T | JSONResponse:
    """CRUD を呼び、例外を応答へ翻訳する（ハンドラは本関数の 1 行に閉じる）。

    Args:
        action: CRUD サービスの呼び出しを包んだ無引数の呼び出し可能。

    Returns:
        成功なら `action()` の戻り、失敗なら 422／404／409 の `JSONResponse`。
    """
    try:
        return action()
    except SettingsValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=ProblemBody(
                problems=[
                    # **13 語の `kind` をそのまま返す**（正本の 7 語に切り詰めない＝ A-09・B-42）
                    ValidationProblemItem(where=item.where, kind=item.kind, reason=item.reason)
                    for item in exc.problems
                ]
            ).model_dump(),
        )
    except NOT_FOUND_ERRORS as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=DetailBody(detail=not_found_detail(exc)).model_dump(),
        )
    except ProjectDeletionBlocked as exc:
        # 010 SD-03: 正本に 422 が無いので、候補の検査に落ちた場合もここへ畳む
        # （**機械可読な違反一覧は返らない**）。
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=DetailBody(detail=str(exc)).model_dump(),
        )


type HeaderFactory = Callable[[SaveOutcome], dict[str, str]]
"""保存結果を応答ヘッダへ写す関数の型。

**実体は `router/__init__.py` の 1 箇所にだけ置く**（010 SD-18）—— `EffectClass.next_kickoff`
（アンダースコア）→ ヘッダ値 `next-kickoff`（ハイフン）の写像を第 2 の場所に作らないため、
各集約は**引数で受け取る**（`__init__` を import すると循環する）。
"""


def saved[T](response: Response, headers: HeaderFactory, result: tuple[T, SaveOutcome]) -> T:
    """保存の戻りから本文を取り出し、応答ヘッダへ世代と反映区分を載せる（D-1）。

    **成功したときだけ**呼ばれる（`run` の内側）。422・404・409 にはヘッダを載せない。

    Args:
        response: FastAPI が注入する応答オブジェクト。
        headers: 保存結果 → ヘッダの写像（`router/__init__.py` が渡す）。
        result: CRUD の `(本文, 保存結果)`。

    Returns:
        応答の本文。
    """
    body, outcome = result
    response.headers.update(headers(outcome))
    return body


def no_content(headers: HeaderFactory, outcome: SaveOutcome) -> Response:
    """204 の応答を組む（**本文が無くても保存結果のヘッダは載せる**＝ D-1）。"""
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers(outcome))
