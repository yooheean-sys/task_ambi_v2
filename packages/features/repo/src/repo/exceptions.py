"""repo 内部専用の例外。

**契約例外はここに置かない**。`RepoNotFound`・`RepoPathViolation` は
`common/interfaces/repo.py` が正本であり、repo 側で再定義・改変しない。

`RepoStartupError` 階層が `AppError` を継承しない理由は vault と同じ: 契約例外の系統は
**モジュール間契約の失敗**を表す語彙であり、設定不備は契約の失敗ではない。

`FsError` 系はすべて `rel`（リポジトリ相対）だけを持ち、**絶対パスを本文に載せない**
（本文は AI へ渡る指摘文の材料になる）。
"""

from __future__ import annotations


class FsError(Exception):
    """作業ツリーの操作の失敗の基底（repo 内部専用）。

    `service.py` が契約例外へ翻訳する。`repository.py` は契約例外を送出しない。
    """

    rel: str

    def __init__(self, message: str, rel: str) -> None:
        """失敗した対象のリポジトリ相対パスを保持する。"""
        super().__init__(message)
        self.rel = rel


class FsNotFoundError(FsError):
    """対象が実在しない。"""


class FsKindError(FsError):
    """対象の種類が違う（ファイルの位置にディレクトリがある・親がファイルである等）。"""


class FsEscapeError(FsError):
    """実体まで解決した先が作業ツリーの外にある（シンボリックリンク経由を含む）。"""


class FsGitDirError(FsError):
    """実体まで解決した先が `.git` の配下にある。"""


class RepoStartupError(RuntimeError):
    """起動時検査の失敗の基底。**`AppError` を継承しない**。"""


class RepoConfigError(RepoStartupError):
    """`config.json` の必須値が欠けている／未知キーがある／ルート設定が読めない。"""
