"""外部実体のアダプタ（憲章 §3 のサブパッケージ分割。plan C-2）。

外部実体が 3 つ（領域 B の YAML・履歴 csv・DL フォルダ）あり、寿命も違う（YAML は要求のたび・
csv はロック付き・監視はスレッド常駐）ので 4 ファイルに割る。

**再エクスポートだけを置き、振る舞いを持たせない** —— `.importlinter` の `nicegui-scope` が
`trigger.repository` の 1 名で子孫をまとめて拾えるようにするため（plan (xi)。`016` で
`streamlit-scope` を `nicegui-scope` に置き換えた）。
"""

from trigger.repository.definitions import DefinitionsRepository, default_trigger
from trigger.repository.history import HistoryRepository, month_of
from trigger.repository.settle import SETTLE_POLL_SECONDS, SettleWorker
from trigger.repository.watcher import WatcherRepository

__all__ = [
    "SETTLE_POLL_SECONDS",
    "DefinitionsRepository",
    "HistoryRepository",
    "SettleWorker",
    "WatcherRepository",
    "default_trigger",
    "month_of",
]
