"""settings 内部専用の例外（data-model.md §5・plan (vi)）。

**契約例外はここに置かない**。`ProjectNotFound`・`MemberNotFound`・`UnknownCommand` は
`common/interfaces/settings.py` が正本であり、settings 側で再定義・改変しない（FR-006）。

`SettingsStartupError` が `AppError` も `api.exceptions.StartupError` も継承しない理由:
契約例外の系統（`NotFoundError`／`ViolationError`）は**モジュール間契約の失敗**を表す語彙で
あり、設定不備は契約の失敗ではない。加えて層契約により settings は apps/api の起動時例外
階層を参照できない（board・vault と同一の判断）。

`SettingsValidationError` が `SettingsStartupError` を継承しない理由: 後半（F02 の保存経路）は
同じ例外を **HTTP 422** に写す（settings-F18〜F20・EIF-05）。起動時例外の系統に混ぜると、
後半で「起動失敗」と「保存拒否」が同じ木になり、翻訳規則が書けない。

起動時例外だけは**見に行った絶対パスを載せる**（FR-062・R-08。運用者が直す対象がそれ自体で
あるため）。`ValidationProblem.where` は領域 A ルート相対のままである（FR-051）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from settings.models import ValidationProblem


class SettingsValidationError(Exception):
    """整合検査の違反（F18・F19・F20）。前半では起動中断に使う（(d3)）。

    **`SettingsStartupError` を継承しない**（後半で HTTP 422 に写るため）。
    """

    problems: list[ValidationProblem]

    def __init__(self, message: str, problems: Sequence[ValidationProblem]) -> None:
        """違反の**一覧**を保持する（最初の 1 件で打ち切らない＝ FR-046・US3 AC-13）。"""
        super().__init__(message)
        self.problems = list(problems)


class SettingsStartupError(RuntimeError):
    """起動時検査の失敗の基底（FR-062）。**`AppError` を継承しない**。"""


class SettingsEnvError(SettingsStartupError):
    """(a) `SETTINGS_ROOT` が読めない（SC-009 a）。"""


class SettingsRootError(SettingsStartupError):
    """(b) `SETTINGS_ROOT` が実在しない／ディレクトリでない（SC-009 b）。"""


class SettingsConfigError(SettingsStartupError):
    """(c) `config.json` の必須値欠け・未知キー（SC-009 c）。"""


class SettingsAreaError(SettingsStartupError):
    """(d1)(d2) 雛形生成の失敗・構文誤り・必須ファイル不在。**一覧を持つ**。

    構文が通らないファイルに対して整合検査を走らせても意味が無いため、(d2) は (d3) の手前で
    打ち切る。ただし (d2) の中では**打ち切らずに全件集める**（US3 AC-5・AC-13）。
    """

    issues: list[str]

    def __init__(self, message: str, issues: Sequence[str] = ()) -> None:
        """読取段で集めた失敗の一覧を保持する。"""
        super().__init__(message)
        self.issues = list(issues)


class SettingsItemNotFound(Exception):  # noqa: N818  # 例外名は設計が正本（data-model §7）。改名すると口の翻訳表と食い違う
    """後半（口）の 404 の信号。未登録の LLM プロファイル名・コマンド名など。

    **`AppError`（`NotFoundError`）も `SettingsStartupError` も継承しない**。契約例外の系統は
    「**モジュール間契約の失敗**」を表す語彙であり（`common/kernel/errors.py` の docstring・
    P5D-001）、本例外は契約の失敗ではなく **HTTP 境界の信号**である。翻訳するのは
    `router/_translate.py` の 1 箇所だけで、そこで **404 ＋ `DetailBody`** になる。
    `SettingsValidationError` が `Exception` 直系である判断と同型。

    契約例外が在るものは**そちらをそのまま使う** —— 未登録のプロジェクトキーは
    `ProjectNotFound`、未登録のメンバは `MemberNotFound`、不明なコマンド名は
    `UnknownCommand`（いずれも `common/interfaces/settings.py` が正本。再定義・改変しない）。
    """


class ProjectDeletionBlocked(Exception):  # noqa: N818  # 例外名は設計が正本（data-model §7）。契約例外 3 種と同じ扱い
    """プロジェクト解除の拒否（**409**）。`SettingsItemNotFound` と同じ理由で `Exception` 直系。

    4 通りで送出する —— 予約キー／未完了課題あり／盤面が読めない（`BoardUnavailable`）／
    候補の検査に落ちた。`DELETE /settings/projects/{k}` の正本は **204／404／409 のみで
    422 を持たない**ため、検査に落ちた場合も 409 に畳む（010 SD-03）。本文は `DetailBody` の
    **単一文字列**であり、**機械可読な違反一覧は返らない**。
    """


def validation_error(problems: Sequence[ValidationProblem]) -> SettingsValidationError:
    """違反の一覧を口の **422** へ運ぶ例外を組む（要約の文言を 1 箇所に閉じる）。

    保存経路（`service/save.py`）と保存時だけの 3 規則（`service/{projects,ai_config}.py`）が
    同じ形で送出するための小道具である。**規則そのものはここに書かない**。

    Args:
        problems: 違反の一覧（**最初の 1 件で打ち切らない**＝ FR-012・SC-005）。

    Returns:
        要約つきの `SettingsValidationError`。
    """
    lines = "\n".join(f"- [{item.kind}] {item.where}: {item.reason}" for item in problems)
    msg = f"設定の検査に違反がある（{len(problems)} 件）:\n{lines}"
    return SettingsValidationError(msg, problems)
