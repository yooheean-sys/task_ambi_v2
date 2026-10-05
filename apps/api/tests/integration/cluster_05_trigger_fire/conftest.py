"""クラスタ 05（trigger の発火と履歴）の準備手段。plan (i)・(ii)・(iii)。

**実 trigger × 実 orchestrator**（9 スタブ・`bd` 不要）。override は 9 件で、
**`KickoffRunner` を入れてはならない** —— trigger が要求する `KickoffRunner` は有効化した実物の
orchestrator が供給するので、入れると `DuplicateProviderError` になる（CD-10）。

**`requires_bd` を付けない**（FR-043）—— `x2_cluster_index.csv` の「実際に通す EIF」に
`EIF-01`（beads）が無い。実物で用意するのは領域 B・履歴 csv・DL フォルダだけである。

**lifespan は `with build_cluster_client(...)` で通す**（CD-09）—— lifespan を持つ登録は trigger
の 1 件だけで、scheduler と watcher はそこで起きて片づく。

**実時間に効く 3 値だけ**を短くする（R-06）—— `trigger/config.json` の実値は
`min_interval_sec: 60`・`poll_interval_sec: 5.0`・`stable_seconds: 2` で、差し替えないと timer
発火の観測に 60 秒以上かかる。判定ロジックに効く値は差し替えない。

fixture は**すべて関数スコープ**（FR-042）。`from conftest import ...` を使わない（FR-041a）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

import pytest
from _support.cluster_env import assert_env_path_under, isolate_env, point_config_at
from _support.cluster_logs import CorrelationRecorder, recording_correlation
from _support.cluster_stores import (
    TempAreaB,
    make_temp_area_b,
    short_trigger_config,
)
from _support.cluster_wiring import build_cluster_client
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubBoardWriter,
    StubCrewExecutor,
    StubMemberResolver,
    StubProposalGate,
    StubRepoReader,
    StubRunMonitor,
    StubSettingsReader,
    StubStageCatalog,
    StubVaultReader,
)

from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import RunMonitor
from common.interfaces.repo import RepoReader
from common.interfaces.settings import MemberResolver, SettingsReader
from common.interfaces.stages import ProposalGate, StageCatalog
from common.interfaces.vault import VaultReader
from trigger import TriggerEnv

if TYPE_CHECKING:
    from pathlib import Path

    from _support.composition import HttpClient

    from api.wiring import ProtocolOverrides

TIMER_INTERVAL_SEC = 2
"""`interval` トリガーの秒数（短い一時 `config.json` と**対で**使う）。

**1 にはできない** —— `misfire_grace_sec < min_interval_sec` の不変条件があり、猶予は 1 以上の
整数なので下限は 2 になる（2026-09-01 に実測）。"""

POLL_TIMEOUT_SEC = 20.0
"""上限つきポーリングの既定の上限（固定 `sleep` に依存しない＝ FR-045）。"""

POLL_STEP_SEC = 0.1
"""ポーリングの刻み。"""


def make_stub_overrides(log: CallLog) -> ProtocolOverrides:
    """orchestrator の未充足 10 契約を埋める（**`KickoffRunner` を入れない**＝ CD-10）。

    `claim_next` は既定で `None` を返すので、周回は即 `no_work` で終わる —— 05 が見たいのは
    「trigger が `KickoffRunner.run` を**呼ぶ**こと」であって周回の中身ではない。

    Args:
        log: 10 スタブが共有する順序つき記録。

    Returns:
        契約の型 → スタブ実装（10 件。10 件目は D25 の `RepoReader`）。
    """
    # 憲章 §3: Protocol 実装を受ける変数には該当 Protocol 型を必ず注釈する。
    board_reader: BoardReader = StubBoardReader(log)
    board_writer: BoardWriter = StubBoardWriter(log)
    settings_reader: SettingsReader = StubSettingsReader(log)
    member_resolver: MemberResolver = StubMemberResolver(log)
    vault_reader: VaultReader = StubVaultReader(log)
    run_monitor: RunMonitor = StubRunMonitor(log)
    stage_catalog: StageCatalog = StubStageCatalog(log)
    proposal_gate: ProposalGate = StubProposalGate(log)
    crew_executor: CrewExecutor = StubCrewExecutor(log)
    repo_reader: RepoReader = StubRepoReader(log)
    return {
        BoardReader: board_reader,
        BoardWriter: board_writer,
        SettingsReader: settings_reader,
        MemberResolver: member_resolver,
        VaultReader: vault_reader,
        RunMonitor: run_monitor,
        StageCatalog: stage_catalog,
        ProposalGate: proposal_gate,
        CrewExecutor: crew_executor,
        RepoReader: repo_reader,
    }


def wait_until(
    predicate: Callable[[], bool],
    *,
    what: str,
    timeout_sec: float = POLL_TIMEOUT_SEC,
) -> None:
    """条件が成立するまで**上限つきで**待つ（固定の待ち時間に依存しない＝ FR-045）。

    Args:
        predicate: 成立を判定する述語。
        what: 失敗時に出す「何を待っていたか」。
        timeout_sec: 上限（超えたら失敗する。待ち直さない＝憲章 §10）。
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(POLL_STEP_SEC)
    msg = f"{what} が {timeout_sec} 秒以内に成立しなかった"
    raise AssertionError(msg)


@pytest.fixture
def call_log() -> CallLog:
    """9 スタブが共有する 1 本の順序つき記録。"""
    return CallLog()


@pytest.fixture(autouse=True)
def cluster_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse）。`trigger.env_file_path` を含む 5 件を差し替える。"""
    return isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def area_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cluster_env: Path
) -> Iterator[TempAreaB]:
    """一時領域 B（`triggers.yaml`・`history/`）と一時 DL フォルダを作る。

    `trigger.config_path` を**短い間隔の一時 `config.json`** へ向ける（R-06）。差し替えるのは
    実時間に効く 3 値だけである。

    Yields:
        作った一時領域 B の記述。
    """
    area = make_temp_area_b(tmp_path, timer_interval_sec=TIMER_INTERVAL_SEC)
    monkeypatch.setenv("TRIGGER_ROOT", str(area.root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(area.downloads_dir))
    point_config_at(
        monkeypatch,
        "trigger",
        short_trigger_config(min_interval_sec=TIMER_INTERVAL_SEC),
        tmp_path,
    )
    assert_env_path_under(TriggerEnv, "TRIGGER_ROOT", tmp_path, env_file=cluster_env)
    assert_env_path_under(TriggerEnv, "DOWNLOADS_DIR", tmp_path, env_file=cluster_env)
    yield area


@pytest.fixture
def cluster_client(area_b: TempAreaB, call_log: CallLog) -> Callable[..., HttpClient]:
    """override を渡すと**実配線のクライアント**を返す工場（`with` で lifespan を通す）。

    Returns:
        `(*, overrides) → TestClient` の呼び出し可能。
    """

    def _build(*, overrides: ProtocolOverrides | None = None) -> HttpClient:
        assert area_b.root.is_dir(), "領域 B の準備が済んでいない"
        return build_cluster_client(
            "05", overrides if overrides is not None else make_stub_overrides(call_log)
        )

    return _build


@pytest.fixture
def stub_overrides(call_log: CallLog) -> ProtocolOverrides:
    """9 件の override そのもの（配線の過不足を見るテストが使う）。"""
    return make_stub_overrides(call_log)


@pytest.fixture
def poll() -> Callable[..., None]:
    """上限つきポーリングの待ち手（固定 `sleep` に依存しない＝ FR-045）。"""
    return wait_until


@pytest.fixture
def history_rows(area_b: TempAreaB) -> Callable[[], Sequence[str]]:
    """履歴 csv の**行**を読む工場（表示のためだけに読む＝ロジックは読み戻さない）。

    Returns:
        履歴ディレクトリ内の全 csv の非空行を返す呼び出し可能。
    """

    def _rows() -> Sequence[str]:
        rows: list[str] = []
        for path in sorted(area_b.history_dir.glob("history_*.csv")):
            rows.extend(
                line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        return rows

    return _rows


@pytest.fixture
def correlation_recorder() -> Iterator[CorrelationRecorder]:
    """ルートロガへ観測点を足し、レコードごとの相関 ID を控える（#12 用）。"""
    yield from recording_correlation()
