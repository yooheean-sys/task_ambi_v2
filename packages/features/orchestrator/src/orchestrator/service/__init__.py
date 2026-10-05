"""周回の本体（憲章 §3 の同名サブパッケージ分割）。`OrchestratorService` を再エクスポートする。"""

from __future__ import annotations

from orchestrator.service.loop import OrchestratorService

__all__ = ["OrchestratorService"]
