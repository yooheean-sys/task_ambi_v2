"""共通例外の基底（`AppError` と 4 系統）。

正本は `docs/design/phase5/common/p5_common_data_model.mmd` の
`namespace common_kernel_errors_py`、契約は contracts/kernel.md §2。

契約例外 15 件はここに集めず、**提供モジュールの `common/interfaces/<MOD>.py`**
に定義して本基底を継承する（P5D-001）。`RunHalted` は `BaseException` 直系で
本階層に入れない（Z-6。`common/interfaces/monitor.py`）。
"""

from __future__ import annotations


class AppError(Exception):
    """全契約例外の基底。"""

    message: str
    cause: BaseException | None

    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        """`message` を `Exception` へ渡し、`message`・`cause` を属性として保持する。"""
        super().__init__(message)
        self.message = message
        self.cause = cause


class NotFoundError(AppError):
    """対象が無い。"""


class UnavailableError(AppError):
    """外部が使えない。"""


class UnverifiedError(AppError):
    """書いた結果を確認できない。"""


class ViolationError(AppError):
    """規則違反・実行失敗。"""
