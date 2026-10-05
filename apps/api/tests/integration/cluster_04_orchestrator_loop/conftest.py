"""クラスタ 04（1 周回の全経路と起票順序）の準備手段。plan (i)・(ii)・(iii)。

**実 orchestrator × 実 stages × 実 board × 実 settings × 実 vault**（2 スタブ）。
override は `CrewExecutor` と `RunMonitor` の 2 件だけである（`registry.py` の `requires`／
`provides` から機械的に決まる）。

`CrewExecutor` は **`SequencedCrewExecutor` で包む** —— 1 周回で `run` は `purpose=work`
（差し戻しで反復）と `purpose=review` に**複数回**呼ばれるので、呼び出しごとに違う
`CrewOutcome` を返す必要がある。並びを使い切ったら**失敗する**（偽サーバのように最終要素を
繰り返さない＝ FR-033a）。

**`max_returns`・`system_actor` は実物のまま**使い、期待値は `OrchestratorConfig()` から引く
（R-06。判定ロジックに効く設定値を差し替えない）。

**HTTP と直接ドライブを 1 つのテストで混ぜない**（R-07）—— `create_app` は `Wiring` を公開
しないので別インスタンスになる。single-flight は HTTP だけで観測する。

fixture は**すべて関数スコープ**（FR-042）。`from conftest import ...` を使わない（FR-041a）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from _support.cluster_doubles import SequencedCrewExecutor
from _support.cluster_env import assert_env_path_under, isolate_env
from _support.cluster_logs import CorrelationRecorder, recording_correlation
from _support.cluster_stores import (
    ClusterStores,
    init_beads,
    link_dependency,
    make_temp_area_a,
    make_temp_vault,
    remove_beads,
    require_bd_or_skip,
    seed_issue,
)
from _support.cluster_wiring import (
    ClusterRuntime,
    build_cluster_client,
    build_cluster_wiring,
)
from _support.contract_stubs import CallLog, StubCrewExecutor, StubRunMonitor

from board import BoardEnv
from common.interfaces.board import ClaimFilter
from common.interfaces.executor import CrewExecutor, CrewOutcome
from common.interfaces.monitor import Grade, Measurement, RunMonitor, RunVerdict
from common.interfaces.orchestrator import ExecutionContext, LaunchKind
from common.kernel.enums import KindLabel, Labels, PersonaKey, StateLabel
from orchestrator import OrchestratorConfig
from settings import SettingsEnv
from vault import VaultEnv

if TYPE_CHECKING:
    from pathlib import Path

    from _support.composition import HttpClient

    from api.wiring import ProtocolOverrides

BEADS_PROJECT_KEY = "cl04"
"""`bd init -p` に渡す接頭辞。"""

PERSONA = PersonaKey.programmer
"""領域 A に登録済みの作業担当のペルソナ（`subrequest` 段が許すペルソナに含まれる）。"""

REVIEW_PERSONA = PersonaKey(OrchestratorConfig().review_persona)
"""レビュー段の担当（**実物の設定から引く**＝直書きしない）。

⚠ このメンバが領域 A に**居ないと 1 周回が閉じない** —— レビュー run が
`member_resolver.resolve` で `MemberNotFound` になり、人間へ渡す経路へ落ちる
（2026-09-01 に実測）。
"""


def make_outcome(
    *,
    completed: bool = True,
    completion_reason: str = "クラスタ 04 の完了",
    proposal: object | None = None,
    review: object | None = None,
    escalation: object | None = None,
    task_output: str | None = "クラスタ 04 の出力",
) -> CrewOutcome:
    """スタブ `CrewExecutor` が返す 1 回ぶんの結果。

    Args:
        completed: 完了を申告するか（偽なら orchestrator が差し戻す）。
        completion_reason: 完了の理由。
        proposal: 分解提案（`propose` 段で使う）。
        review: レビュー結果（`review` 用の run で使う）。
        escalation: エスカレーション要求。
        task_output: 作業の出力。

    Returns:
        `CrewOutcome`。
    """
    return CrewOutcome(
        proposal=proposal,  # pyright: ignore[reportArgumentType]  # 呼び手が `Proposal` を渡す
        task_output=task_output,
        completed=completed,
        completion_reason=completion_reason,
        review=review,  # pyright: ignore[reportArgumentType]  # 呼び手が `ReviewVerdict` を渡す
        escalation=escalation,  # pyright: ignore[reportArgumentType]  # 呼び手が `Escalation` を渡す
        written=[],
        events=[],
        halted=False,
        halted_reason=None,
        progress_notes=0,
    )


def make_verdict(grade: Grade | None = None) -> RunVerdict:
    """スタブ `RunMonitor` が返す判定（**盤面には書かない**等級を既定にする）。

    04 では monitor がスタブなので、等級写像そのものは見ない（それは 01 の領分＝ `NX-02`）。
    """
    return RunVerdict(
        grade=grade,
        kinds=[],
        halted=False,
        closed=False,
        envelope="",
        measurement=Measurement(
            kickoff_id="cluster04",
            requests=0,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            cost_missing_count=0,
            max_tokens_before_send=0,
            halted_reason=None,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            unattributed_requests=0,
        ),
        failures=[],
    )


@pytest.fixture
def bd_bin() -> str:
    """`bd` の絶対パス（無ければ **skip**）。"""
    return require_bd_or_skip()


@pytest.fixture(autouse=True)
def cluster_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse）。"""
    return isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def call_log() -> CallLog:
    """2 スタブが共有する 1 本の順序つき記録。"""
    return CallLog()


@pytest.fixture
def stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_env: Path,
    bd_bin: str,
) -> Iterator[ClusterStores]:
    """一時 beads・vault・領域 A を作り、3 MOD の環境変数を指し直す（`build()` の前）。

    Yields:
        作った一時ストアのひとまとめ。
    """
    beads = init_beads(tmp_path, bd_bin, project_key=BEADS_PROJECT_KEY)
    vault = make_temp_vault(tmp_path, personas=(PERSONA, REVIEW_PERSONA))
    # `hooks.yaml` は空にする（executor は有効 MOD に無いが、`hook_catalog` との照合を将来
    # 踏まないように 03 と揃える）。
    # D25: 実物の repo が完了ゲートで `repo/<パス>` の実在を確かめる一時の作業ツリー。repo は
    # git を呼ばないので `.git` は空ディレクトリで足りる。対応付けは全テストに置く（スタブの
    # 実行器には影響しない。`repo/` の成果物を書かない課題では repo に問い合わせない）。
    work_tree = tmp_path / "repo_work"
    (work_tree / ".git").mkdir(parents=True)
    area_a = make_temp_area_a(
        tmp_path,
        persona=PERSONA,
        with_hooks=False,
        extra_personas=(REVIEW_PERSONA,),
        repo_local_path=str(work_tree),
    )

    monkeypatch.setenv("BEADS_DIR", str(beads.beads_dir))
    monkeypatch.setenv("VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=cluster_env)
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)

    yield ClusterStores(beads=beads, vault=vault, area_a=area_a)

    remove_beads(beads)


@pytest.fixture
def make_overrides(call_log: CallLog) -> Callable[..., ProtocolOverrides]:
    """2 件の未充足契約を埋める写像を組む工場を返す。

    `CrewExecutor` は**呼ばれる回数ぶんの `CrewOutcome`** を並びで受け取り、
    `SequencedCrewExecutor` が順に返す。使い切ったら失敗する（往復数の読み違いを緑にしない）。

    Returns:
        `(outcomes, verdict) → override 写像` の呼び出し可能。
    """

    def _make(
        outcomes: Sequence[CrewOutcome] = (),
        *,
        verdict: RunVerdict | None = None,
        executor: CrewExecutor | None = None,
    ) -> ProtocolOverrides:
        # 憲章 §3: Protocol 実装を受ける変数には該当 Protocol 型を必ず注釈する。
        crew_executor: CrewExecutor
        if executor is not None:
            crew_executor = executor
        else:
            crew_executor = SequencedCrewExecutor(
                [StubCrewExecutor(call_log, outcome=outcome) for outcome in outcomes]
            )
        run_monitor: RunMonitor = StubRunMonitor(
            call_log, verdict=verdict if verdict is not None else make_verdict()
        )
        return {CrewExecutor: crew_executor, RunMonitor: run_monitor}

    return _make


@pytest.fixture
def cluster(
    stores: ClusterStores, make_overrides: Callable[..., ProtocolOverrides]
) -> Callable[..., ClusterRuntime]:
    """override を渡すと**実物一式**を組んで返す工場（直接ドライブ用）。

    Returns:
        `(outcomes, verdict) → 実物一式` の呼び出し可能。
    """

    def _build(
        outcomes: Sequence[CrewOutcome] = (),
        *,
        verdict: RunVerdict | None = None,
        executor: CrewExecutor | None = None,
    ) -> ClusterRuntime:
        overrides = make_overrides(outcomes, verdict=verdict, executor=executor)
        return ClusterRuntime(wiring=build_cluster_wiring("04", overrides), stores=stores)

    return _build


@pytest.fixture
def cluster_client(
    stores: ClusterStores, make_overrides: Callable[..., ProtocolOverrides]
) -> Callable[..., HttpClient]:
    """override を渡すと**実配線のクライアント**を返す工場（HTTP 入口用）。

    ⚠ `cluster` の戻りとは**別インスタンス**である（R-07）。**1 つのテストで混ぜない**。

    Returns:
        `(outcomes, verdict) → TestClient` の呼び出し可能。
    """

    def _build(
        outcomes: Sequence[CrewOutcome] = (),
        *,
        verdict: RunVerdict | None = None,
        executor: CrewExecutor | None = None,
    ) -> HttpClient:
        overrides = make_overrides(outcomes, verdict=verdict, executor=executor)
        assert stores.area_a.root.is_dir(), "領域 A の準備が済んでいない"
        return build_cluster_client("04", overrides)

    return _build


@pytest.fixture
def seed(stores: ClusterStores) -> Callable[..., str]:
    """盤面に課題を 1 件仕込む工場を返す。

    Returns:
        `(kind, *, extra_labels, parent, acceptance, title, ready, project_key, persona)
        → issue_id` の呼び出し可能。
    """

    def _seed(
        kind: KindLabel | None,
        *,
        extra_labels: Sequence[str] = (),
        parent: str | None = None,
        acceptance: str = "- [ ] クラスタ 04 の完了条件",
        title: str = "クラスタ 04 の課題",
        ready: bool = True,
        project_key: str | None = None,
        persona: PersonaKey | None = None,
    ) -> str:
        # `project:`・`persona:` は**差し替え**（`extra_labels` に足すと 2 つ付いてしまい、
        # 前処理が先に見つけた方を採るので失敗系が踏めない＝ 2026-09-01 に実測）。
        labels = [
            Labels.project(project_key if project_key is not None else stores.project_key),
            Labels.persona(persona if persona is not None else PERSONA),
            *extra_labels,
        ]
        if kind is not None:
            # `None` を渡すと `kind:` を**付けない** —— 生の文字列で未知の値を置く形
            # （`UnknownStage` を踏む）に使う。
            labels.append(Labels.kind(kind))
        if ready:
            labels.append(Labels.role_executor())
        return seed_issue(
            stores.beads, title=title, labels=labels, parent=parent, acceptance=acceptance
        )

    return _seed


@pytest.fixture
def context() -> ExecutionContext:
    """`KickoffRunner.run` に渡す実行文脈（手動起動）。"""
    return ExecutionContext(
        kind=LaunchKind.manual,
        trigger_snapshot=None,
        dropped_path=None,
        claim_filter=ClaimFilter(labels=frozenset({Labels.role_executor()}), project_key=None),
        requested_at=datetime.now(UTC),
    )


@pytest.fixture
def working_label() -> str:
    """`state:working`（残骸の仕込みに使う）。"""
    return Labels.state(StateLabel.working)


@pytest.fixture
def correlation_recorder() -> Iterator[CorrelationRecorder]:
    """ルートロガへ観測点を足し、レコードごとの相関 ID を控える（#16 用）。"""
    yield from recording_correlation()


@pytest.fixture
def link(stores: ClusterStores) -> Callable[[str, str], None]:
    """`from_id` が `to_id` に blocked_by される辺を張る工場を返す。

    ⚠ 未回答の質問は**この辺で張る**（`--parent` では `open_question_of` に出ない）。

    Returns:
        `(from_id, to_id) → None` の呼び出し可能。
    """

    def _link(from_id: str, to_id: str) -> None:
        link_dependency(stores.beads, from_id, to_id)

    return _link


@pytest.fixture
def make_crew_outcome() -> Callable[..., CrewOutcome]:
    """スタブ `CrewExecutor` が返す 1 回ぶんの結果を組む工場を返す。

    テストが `from conftest import ...` へ手を伸ばさないよう、module 関数ではなく fixture で
    配る（FR-041a）。

    Returns:
        `CrewOutcome` を組む呼び出し可能。
    """
    return make_outcome


@pytest.fixture
def stub_overrides_keys(
    make_overrides: Callable[..., ProtocolOverrides],
) -> frozenset[type[object]]:
    """override 集合の**鍵だけ**（非実行の根拠を実配線から確認する FR-021a が使う）。

    `Wiring.provided` は override と有効 MOD の提供分を**合わせた**写像なので、そこから
    override だけを取り出すことはできない。

    Returns:
        override する契約の型の集合。
    """
    return frozenset(make_overrides([]).keys())
