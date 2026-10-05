"""apps/api の起動を中断させる失敗の階層（data-model.md §8）。

`common.kernel.errors.AppError` は**継承しない**。`AppError` の系統は「モジュール間契約の
例外」であり、配線の失敗は契約の失敗ではなく**起動を止める構成エラー**であるため。
各例外が本文に必ず載せる情報は contracts/composition-root.md §4 が正本。
"""

from __future__ import annotations


class StartupError(RuntimeError):
    """起動を中断させる失敗の基底。"""


class ConfigurationError(StartupError):
    """設定の不備（FR-025）。欠けた／不正なキー名と読んだファイルの絶対パスを本文に載せる。"""


class WiringError(StartupError):
    """配線の不備の基底。"""


class UnknownModuleError(WiringError):
    """有効 MOD 一覧に登録の無いモジュール名がある（FR-012）。"""


class DuplicateModuleError(WiringError):
    """有効 MOD 一覧に同じ名前が 2 回以上ある（FR-049）。"""


class UnsatisfiedContractError(WiringError):
    """供給元の無い契約が残っている（FR-011）。"""


class ExtraneousOverrideError(WiringError):
    """有効化されたどのモジュールも要求しない契約への override（FR-045）。"""


class DuplicateProviderError(WiringError):
    """同じ契約が 2 つ以上から供給される（FR-046）。"""


class CircularDependencyError(WiringError):
    """インスタンスレベルの相互依存（FR-010。憲章 §3 の設計見直しシグナル）。"""


class HandleContractMismatchError(WiringError):
    """宣言 `provides` と組み立て口が返した鍵集合が一致しない（data-model.md §3 の不変条件）。"""
