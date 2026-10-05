"""trigger 内部専用の例外（data-model §9・research R-05）。

**契約例外はここに置かない**。`KickoffBusy` は `common/interfaces/orchestrator.py` が正本であり、
trigger 側で再定義・改変しない。trigger は Protocol を 1 本も提供しないので、**モジュール間
契約の失敗を表す例外はそもそも存在しない**。

`TriggerStartupError` が `common.kernel.errors.AppError` も `api.exceptions.StartupError` も
継承しない理由: 契約例外の系統（`NotFoundError`／`ViolationError`）は**モジュール間契約の失敗**を
表す語彙であり、設定不備は契約の失敗ではない。加えて層契約により trigger は `apps/api` の
起動時例外階層を参照できない（settings・board・vault と同一の判断＝ R-05）。

`TriggerValidationError` が `TriggerStartupError` を継承しない理由: 同じ例外を **HTTP 422** に
写すため。起動時例外の系統に混ぜると「起動失敗」と「保存拒否」が同じ木になり、翻訳規則が
書けない（`settings` の先例と同型）。

起動時例外だけは**見に行った絶対パスを載せる**（運用者が直す対象がそれ自体であるため）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trigger.schemas import ValidationProblemItem


class TriggerStartupError(RuntimeError):
    """起動時検査の失敗の基底（build／lifespan の中でしか出ない）。**口に届かない**。"""


class TriggerEnvError(TriggerStartupError):
    """(a) `TRIGGER_ROOT` が読めない（FR-073・憲章 §5）。"""


class TriggerRootError(TriggerStartupError):
    """(b)(c) パスが絶対でない／実在しない／ディレクトリでない。

    `TRIGGER_ROOT`（必須）と `DOWNLOADS_DIR`（**設定されていれば**）の両方で使う。
    """


class TriggerConfigError(TriggerStartupError):
    """(d) `config.json` が読めない／不変条件違反（FR-075）。"""


class TriggerDefinitionsError(TriggerStartupError):
    """(e) `triggers.yaml` が壊れている／書けない。"""


class TriggerValidationError(Exception):
    """validate の違反（`V-1`〜`V-8`）。→ HTTP **422**。**一覧を持つ**。

    **`TriggerStartupError` を継承しない**（口の 422 に写るため＝ R-05）。
    """

    problems: list[ValidationProblemItem]

    def __init__(self, message: str, problems: Sequence[ValidationProblemItem]) -> None:
        """違反の**一覧**を保持する（最初の 1 件で打ち切らない＝ FR-027・SC-009）。"""
        super().__init__(message)
        self.problems = list(problems)


class TriggerNotFound(Exception):  # noqa: N818  # 例外名は設計が正本（data-model §9）。改名すると口の翻訳表と食い違う
    """口の **404** の信号（不明な `trigger_id`）。

    **`AppError`（`NotFoundError`）も `TriggerStartupError` も継承しない**。契約例外の系統は
    「モジュール間契約の失敗」を表す語彙であり（`common/kernel/errors.py` の docstring）、
    本例外は契約の失敗ではなく **HTTP 境界の信号**である。翻訳するのは `router.py` の 1 箇所
    だけで、そこで **404 ＋ `DetailBody`** になる（`settings.SettingsItemNotFound` と同型）。
    """


def validation_error(problems: Sequence[ValidationProblemItem]) -> TriggerValidationError:
    """違反の一覧を口の **422** へ運ぶ例外を組む（要約の文言を 1 箇所に閉じる）。

    Args:
        problems: 違反の一覧（**最初の 1 件で打ち切らない**）。

    Returns:
        要約つきの `TriggerValidationError`。
    """
    lines = "\n".join(f"- {item.where}: {item.reason}" for item in problems)
    msg = f"トリガーの検査に違反がある（{len(problems)} 件）:\n{lines}"
    return TriggerValidationError(msg, problems)
