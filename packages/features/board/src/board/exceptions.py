"""board 内部専用の例外（data-model §5・plan (v)）。

**契約例外はここに置かない**。`IssueNotFound`・`BoardUnavailable`・`BoardWriteUnverified` は
`common/interfaces/board.py` が正本であり、board 側で再定義しない（FR-006）。

`BoardStartupError` 階層が `AppError` を継承しない理由: 契約例外の系統
（`NotFoundError`／`UnavailableError`／`UnverifiedError`）は**モジュール間契約の失敗**を表す語彙であり、
設定不備は契約の失敗ではない（apps/api の `StartupError` が `AppError` を継承しないのと同じ判断）。
"""

from __future__ import annotations

from enum import StrEnum


class BdErrorKind(StrEnum):
    """`bd` の失敗の種別（contracts/bd-cli.md §4）。"""

    not_found = "not_found"
    lock_exhausted = "lock_exhausted"
    unavailable = "unavailable"
    other = "other"


class BdCommandError(Exception):
    """`bd` の 1 呼び出しが失敗した（board 内部専用）。

    `service.py` の単一の翻訳関数が契約例外へ写す。`repository.py` は契約例外を送出しない
    （FR-041・層の分離）。
    """

    kind: BdErrorKind
    argv: tuple[str, ...]
    rc: int
    stderr: str
    attempts: int

    def __init__(
        self,
        message: str,
        kind: BdErrorKind,
        argv: tuple[str, ...],
        rc: int,
        stderr: str,
        attempts: int = 1,
    ) -> None:
        """失敗の種別・実行した argv・終了コード・標準エラー出力の先頭・試行回数を保持する。

        `attempts` は **FR-043 が `BoardUnavailable` に要求する試行回数**の出所である。`BdCli` は
        失敗時に `BdResult` を返さないため、例外がこの値を運ばないと `service.py` の翻訳関数が
        値を作れない（`BoardService` は `runner` しか持たず `lock_wait_max_attempts` を知らない）。
        → design-sync A-8。
        """
        super().__init__(message)
        self.kind = kind
        self.argv = argv
        self.rc = rc
        self.stderr = stderr
        self.attempts = attempts


class BdMappingError(BdCommandError):
    """機械可読出力の写像に失敗した（必須キーの欠落・未知の値）。

    `kind=other`・`argv=()`・`rc=0` で初期化し、`service.py` が直前の `BdResult` から
    `argv`・`rc`・`attempts` を埋め直す（R-11）。
    """

    key: str

    def __init__(self, message: str, key: str) -> None:
        """欠けたキー名（または不正だった値）を保持する。"""
        super().__init__(message, kind=BdErrorKind.other, argv=(), rc=0, stderr=message)
        self.key = key


class BoardStartupError(RuntimeError):
    """起動時検査の失敗の基底（FR-047）。**`AppError` を継承しない**。"""


class BoardEnvError(BoardStartupError):
    """`.env` の必須キーが無い（FR-047 a）。"""


class BoardWorkspaceError(BoardStartupError):
    """盤面のディレクトリが無い／`.beads` の体裁でない（FR-047 b・c）。"""


class BoardBinaryError(BoardStartupError):
    """`bd` 実行ファイルを解決できない／実行できない（FR-047 d）。"""


class BoardConfigError(BoardStartupError):
    """`config.json` の必須値が欠けている／未知キーがある（FR-047 e）。"""
