"""FastAPI の実行エントリ（contracts/composition-root.md §6）。

**配線・登録・ミドルウェア・生存確認の実装をここに置かない**（→ DIFF-04）。組み立ては
`app.py`／`wiring.py`／`registry.py` が行い、本ファイルは待受の開始だけを担う。
`create_app()` を先に呼ぶので、どちらの段で失敗しても `uvicorn.run` に到達しない
（＝待受を開始せずに中断する。US4 AC5・FR-026）。
"""

from __future__ import annotations

import uvicorn

from api.app import create_app
from api.config import AppEnvSettings


def main() -> None:
    """アプリを組み立ててから待受を開始する。

    Raises:
        StartupError: 設定または配線の不備（`create_app()` 由来）。待受は開始しない。
    """
    app = create_app()
    env = AppEnvSettings()
    uvicorn.run(app, host=env.app_host, port=env.app_port)


if __name__ == "__main__":
    main()
