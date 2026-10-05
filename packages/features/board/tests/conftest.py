"""board のテスト共通 fixture。共有部品は [board_testing](./board_testing.py) にある。"""

from __future__ import annotations

import pytest

from board import BoardConfig


@pytest.fixture
def default_config() -> BoardConfig:
    """`packages/features/board/config.json` の既定値（CFG-22〜26）。"""
    return BoardConfig()
