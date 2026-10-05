"""vault 内部専用の例外（data-model.md §4・plan (v)）。

**契約例外はここに置かない**。`VaultNotFound`・`VaultPathViolation` は
`common/interfaces/vault.py` が正本であり、vault 側で再定義・改変しない（FR-006）。

`VaultStartupError` 階層が `AppError` を継承しない理由: 契約例外の系統
（`NotFoundError`／`ViolationError`）は**モジュール間契約の失敗**を表す語彙であり、設定不備は
契約の失敗ではない。加えて層契約により vault は apps/api の起動時例外階層を参照できない
（board と同一の判断＝ R-13・FR-054）。

`FsError` 系はすべて `rel`（vault ルート相対）だけを持ち、**絶対パスを本文に載せない**（FR-060）。
起動時例外だけは例外で、**見に行った絶対パスを載せる**（FR-054・SC-010。運用者が直す対象が
それ自体であるため）。
"""

from __future__ import annotations


class FsError(Exception):
    """ファイルシステム操作の失敗の基底（vault 内部専用）。

    `service.py` の翻訳表が契約例外へ写す。`repository.py` は契約例外を送出しない
    （contracts/fs-adapter.md §1 原則 1）。
    """


class FsNotFoundError(FsError):
    """対象が実在しない（ENOENT）。

    **`service.py` が `VaultNotFound` へ翻訳する唯一の内部例外**である。
    """

    rel: str

    def __init__(self, message: str, rel: str) -> None:
        """不在だった対象の vault ルート相対パスを保持する。"""
        super().__init__(message)
        self.rel = rel


class FsArchiveError(FsError):
    """退避に失敗した（FR-048）。**契約例外へ翻訳せず呼び手へ素通しする**。

    退避が失敗したときは**新しい版を書かず、古い版を元の場所に残す**。契約の
    `VaultPathViolation.kind` は 7 種の閉じた `Literal` であり `archive_failed` を作れない
    （DIFF-06）。

    `archive_rel` が埋まるのは **`os.replace` に失敗した 1 箇所だけ**である。手前の 3 つの送出点
    （退避先の親が実在しない／退避先フォルダを作れない／同一スタンプの連番が尽きた）では退避先の
    名前がまだ決まっておらず、`None` のままが正しい（値を捏造しない）。
    """

    rel: str
    archive_rel: str | None

    def __init__(self, message: str, rel: str, archive_rel: str | None = None) -> None:
        """退避元と（決まっていれば）退避先の vault ルート相対パスを保持する。"""
        super().__init__(message)
        self.rel = rel
        self.archive_rel = archive_rel


class FsIoError(FsError):
    """その他の入出力の失敗（権限・EXDEV・同期中の一時的な失敗）。

    **契約例外へ翻訳せず素通しする**（「空・偽に倒さない」＝ Edge Cases・C-3 (2)）。
    """

    rel: str
    op: str

    def __init__(self, message: str, rel: str, op: str) -> None:
        """失敗した対象の vault ルート相対パスと操作名を保持する。"""
        super().__init__(message)
        self.rel = rel
        self.op = op


class VaultStartupError(RuntimeError):
    """起動時検査の失敗の基底（FR-052・FR-053）。**`AppError` を継承しない**。"""


class VaultEnvError(VaultStartupError):
    """`VAULT_ROOT` の指定が無い（SC-010 a）。"""


class VaultRootError(VaultStartupError):
    """`VAULT_ROOT` が実在しない／ディレクトリでない（SC-010 b・c）。"""


class VaultConfigError(VaultStartupError):
    """`config.json` の必須値が欠けている／未知キーがある／ルート設定が読めない（SC-010 d）。"""
