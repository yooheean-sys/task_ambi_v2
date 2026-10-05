"""trigger のテスト共通 fixture とスタブ（plan (ix)）。

★ **止め木**（`_isolate_real_area_b`）が本ファイルの核である。実リポジトリの `.env` は
`TRIGGER_ROOT`・`DOWNLOADS_DIR` を実値で持つため、autouse で既定を安全側に倒さないと
`build()` を呼ぶテストが**実運用の領域 B と DL フォルダを掴む**。

⚠ **trigger の止め木は settings より重い**。settings は雛形の書込 1 回だが、trigger は

- 実運用の領域 B に `triggers.yaml` と履歴 csv を書き、
- **実運用の Windows Downloads に `PollingObserver` を張り付け、投下ファイルで実際に
  kickoff が起きうる**。

⚠ この止め木が効くのは、`trigger.TriggerEnv` の**明示 `__init__(self, **overrides)`** と
`trigger.build()` の **`TriggerEnv(_env_file=env_file_path())`** が**対で**在るときだけである
（R-13）。どちらかを消すと `model_config` のクラス定義時評価が残るだけになり、止め木は
**黙って無効**になる。**この fixture を消さない・迂回しない。**

**実運用の DL フォルダ（Windows 側の共有）に 1 バイトも触れない**（spec A-05・P-2）。監視のテストは
`tmp_path`（ext4）だけを使う。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import pytest

from common.interfaces.orchestrator import (
    ExecutionContext,
    IssueRunSummary,
    KickoffBusy,
    KickoffResult,
    KickoffStatus,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

StubMode = Literal["ok", "busy", "boom", "slow"]
"""`StubKickoffRunner` の 4 モード（plan (ix)）。"""


@pytest.fixture(autouse=True)
def _isolate_real_area_b(  # pyright: ignore[reportUnusedFunction]  # autouse fixture は pytest が収集して呼ぶ
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """どのテストも実リポジトリの `.env`・実運用の領域 B・実 DL フォルダに届かない。"""
    empty_env = tmp_path_factory.mktemp("env") / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr("trigger.env_file_path", lambda: empty_env)
    monkeypatch.delenv("TRIGGER_ROOT", raising=False)
    monkeypatch.delenv("DOWNLOADS_DIR", raising=False)
    yield


@pytest.fixture
def trigger_root(tmp_path: Path) -> Path:
    """一時の領域 B のルート（**中身は空**。雛形生成の検証がここから始まる）。"""
    root = tmp_path / "area_b"
    root.mkdir()
    return root


@pytest.fixture
def downloads_dir(tmp_path: Path) -> Path:
    """一時の DL フォルダ（**ext4**。実運用の DL フォルダには触れない＝ P-2）。"""
    path = tmp_path / "downloads"
    path.mkdir()
    return path


@dataclass
class StubKickoffRunner:
    """`KickoffRunner`（IF-49）のスタブ。**1 クラス・4 モード**（plan (ix)）。

    `bd`・vault・領域 A・LLM に一切つながらない。呼び出しごとに受け取った `ExecutionContext`
    を `calls` に記録する。
    """

    mode: StubMode = "ok"
    """`ok`＝`KickoffResult` を返す／`busy`＝`KickoffBusy`／`boom`＝`RuntimeError`／
    `slow`＝`seconds` だけ眠ってから `ok` と同じ結果を返す。"""

    seconds: float = 0.0
    """`slow` モードの待ち。"""

    results: Sequence[str] = ()
    """返す `IssueRunSummary.result` の並び（畳み方の検証用。6 通りを入れられる）。"""

    notes: Sequence[str] = ()
    """返す `KickoffResult.notes`（`" / "` 連結の検証用）。"""

    kickoff_id: str = "k-ok"
    status: KickoffStatus = KickoffStatus.ok
    current_kickoff_id: str = "k-1"
    """`busy` のときに `KickoffBusy` へ載せる値。"""

    calls: list[ExecutionContext] = field(default_factory=list[ExecutionContext])
    correlation_ids: list[str | None] = field(default_factory=list[str | None])
    """呼び出し時点の `CorrelationContext.current()`（相関 ID の貫通＝ SC-018 の観測点）。"""

    entered: threading.Event = field(default_factory=threading.Event)
    """1 回目の呼び出しに入ったことの合図（進行中を待つテスト用）。"""

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def run(self, ctx: ExecutionContext) -> KickoffResult:
        """`KickoffRunner.run`（IF-49）。同期・ブロッキング。"""
        from common.kernel.log import CorrelationContext

        with self._lock:
            self.calls.append(ctx)
            self.correlation_ids.append(CorrelationContext.current())
        self.entered.set()
        if self.mode == "busy":
            raise KickoffBusy("別の kickoff が実行中", current_kickoff_id=self.current_kickoff_id)
        if self.mode == "boom":
            msg = "スタブが意図的に落ちた"
            raise RuntimeError(msg)
        if self.mode == "slow":
            time.sleep(self.seconds)
        now = datetime.now(UTC)
        return KickoffResult(
            kickoff_id=self.kickoff_id,
            status=self.status,
            issues=[_summary(index, result) for index, result in enumerate(self.results)],
            notes=list(self.notes),
            started_at=now,
            finished_at=now,
        )

    @property
    def count(self) -> int:
        """呼び出し回数。"""
        with self._lock:
            return len(self.calls)


def _summary(index: int, result: str) -> IssueRunSummary:
    """`IssueRunSummary` を 1 件組む（`result` 以外は畳み方に影響しない）。"""
    from common.kernel.enums import PersonaKey, StageName

    return IssueRunSummary(
        issue_id=f"i-{index}",
        stage=StageName.plain,
        persona=PersonaKey.programmer,
        result=_narrow(result),
        grade=None,
        returns=0,
    )


def _narrow(
    result: str,
) -> Literal["closed", "released", "failing", "escalated", "unsafe", "halted"]:
    """文字列を `IssueRunSummary.result` の Literal へ絞る（テスト側の便宜）。"""
    allowed = ("closed", "released", "failing", "escalated", "unsafe", "halted")
    if result not in allowed:
        msg = f"未知の result: {result}"
        raise ValueError(msg)
    return result  # pyright: ignore[reportReturnType]  # 直上で allowed に含まれることを確かめている
