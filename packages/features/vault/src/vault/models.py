"""vault 内部の値オブジェクト（data-model.md §2）。

いずれも `dataclass(frozen=True, slots=True)`。憲章 §7-2 の「受け渡し型は frozen」が対象と
するのは `common/interfaces/` のモジュール間契約型であり、ここに置くのは vault 内部でしか
現れない器である（`os.stat_result` を業務ロジックへ漏らさないなど）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from common.interfaces.vault import VaultArea


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """解決済みパス（vault-F01）。**キャッシュしない**（憲章 §2・FR-016）。

    `real` を持つのは同一呼び出しの中で `stat`／`read`／`replace` に渡すためだけである。
    **呼び手・ログ・例外本文に出さない**（FR-025・FR-060）。`root_ok`／`area_ok` は
    **真偽値であって例外ではない**（送出するのは `service.py`＝ R-13）。
    """

    rel: str
    """vault ルート相対の正規化済みパス（区切りは常に `/`）。"""

    real: Path
    """`os.path.realpath` 後の絶対パス。**呼び手へ出さない**。"""

    area: VaultArea
    """どの領域として解決したか。"""

    root_ok: bool
    """vault ルートの内側か（実体の同一性で判定＝ R-01・FR-024）。"""

    area_ok: bool
    """宣言された領域根（複数可）のいずれかの内側か。"""


@dataclass(frozen=True, slots=True)
class NameViolation:
    """ファイル名ゲートの違反 1 件（vault-F07）。"""

    code: str
    """規則コード（`empty_name`・`path_separator`・`forbidden_char`・`control_char`・
    `reserved_word`・`trailing_dot_space`・`obsidian_char`・`excluded_prefix`・
    `archive_dir_name`・`too_long`）。"""

    token: str
    """**該当した文字・語・実測値**（例 `":"`／`"CON"`／`"215 bytes > 200"`）。

    置換・除去・切り詰めはしない（P5D-010・FR-029）。「禁止文字が含まれます」だけでは
    AI が直せないため、**必ず埋める**。
    """


@dataclass(frozen=True, slots=True)
class NameGateResult:
    """ファイル名ゲートの結果（vault-F07）。"""

    ok: bool
    """`not violations`。"""

    violations: tuple[NameViolation, ...]
    """触れた規則すべて（早期打ち切りしない＝ FR-031）。"""


@dataclass(frozen=True, slots=True)
class FileStat:
    """列挙・読取で使う `os.stat_result` の写し。

    `os.stat_result` を `service.py` へ漏らさないために置く（R-19・design-sync A-3）。
    """

    size_bytes: int
    """`st_size`。"""

    mtime: datetime
    """`datetime.fromtimestamp(st_mtime, tz=UTC)`。"""
