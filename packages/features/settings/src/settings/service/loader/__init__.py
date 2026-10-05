"""生の読取結果 → `SettingsSnapshot`（settings-F24・F02 の build 側）。

**行数が憲章 §1 の目安を超えたので同名サブパッケージに分けた**（憲章 §3・plan (xii) と同じ
500 行の閾値）。段は build の検査順（R-08）をそのままファイルの割りにしてある:

| ファイル | 段 |
|---|---|
| `read.py` | (d2) 領域 A の読取（失敗を**一覧で**集める） |
| `enforcer.py` | 判定器の構築と 3 つの担保（contracts/authorizer.md §3） |
| `assemble.py` | (d3) 写像・merge・台帳導出・世代の採番 |
"""

from __future__ import annotations

from settings.service.loader.assemble import INITIAL_GENERATION, build_snapshot
from settings.service.loader.enforcer import build_enforcer
from settings.service.loader.read import read_area

__all__ = ["INITIAL_GENERATION", "build_enforcer", "build_snapshot", "read_area"]
