"""settings のテスト共通 fixture。共有部品は [settings_testing](./settings_testing.py) にある。

★ **止め木**（`_isolate_real_area_a`）が本ファイルの核である。実リポジトリの `.env` には領域 A
のルートが実値で入っているため、autouse で既定を安全側に倒さないと `build()` を呼ぶテストが
**実運用の領域 A を掴む**（FR-019・SC-015）。

⚠ settings の止め木は vault より重い。**settings の build は書込を行う**（不在なら
`model.conf`・`agents.yaml`・`tasks.yaml` を生成する）ため、止め木が外れたテストは実運用の
領域 A に雛形を書き込む。読むだけの vault と違い **1 回で観測可能な被害が出る**。

⚠ この止め木が効くのは、`settings.SettingsEnv` の**明示 `__init__(self, **overrides)`** と
`settings.build()` の **`SettingsEnv(_env_file=env_file_path())`** が**対で**在るときだけである。
どちらかを消すと `model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**に
なる（テストは緑のまま実運用の領域 A に書き込む）。**この fixture を消さない・迂回しない。**
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_real_area_a(  # pyright: ignore[reportUnusedFunction]  # autouse fixture は pytest が収集して呼ぶ
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """どのテストも実リポジトリの `.env` と実運用の領域 A に届かない（FR-019・SC-015）。"""
    empty_env = tmp_path_factory.mktemp("env") / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr("settings.env_file_path", lambda: empty_env)
    monkeypatch.delenv("SETTINGS_ROOT", raising=False)
    yield


@pytest.fixture
def settings_root(tmp_path: Path) -> Path:
    """一時の領域 A のルート（**中身は空**。雛形生成の検証がここから始まる）。"""
    root = tmp_path / "area_a"
    root.mkdir()
    return root
