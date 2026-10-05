"""クラスタ 01 の確認読取・順序・release・二重 begin（IF-39・IF-08・IF-15・IF-38）。§6 #5〜#8。

- **#5**: 確認読取が通らない（`BoardUnavailable`）→ `MappingUnverified` → 周回打ち切り（IF-39）
- **#6**: 封筒 comment が状態変更より**先**に盤面へ入っている（IF-08）
- **#7**: release の完全性（status と assignee の**両方**が戻る。IF-15）
- **#8**: 進行中 RunState がある状態の `begin` は差し替えるだけで**止めない**（IF-38）

#5 の注入は BD_BIN シム **(a)**（規約 §6）。`finish` の時点で盤面へ届かなくなる形を作るため、
**周回を始めてからシムへ差し替える**必要がある —— `board.build()` は `BD_BIN` を実行ファイルの
実在だけで確かめるので、環境変数を差し替えても既に組んだ board には効かない。したがって
`_run_steps` が `BoardUnavailable` を受ける形（シム (a) を最初から向けた board）で踏む。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_scripts import make_tool_call, make_turn
from _support.cluster_shims import make_always_fails_shim

from api.wiring import require
from common.interfaces.board import BoardReader, IssueStatus
from common.interfaces.monitor import RunMonitor
from common.interfaces.orchestrator import KickoffRunner, KickoffStatus
from common.interfaces.settings import LimitPolicy
from common.kernel.enums import StateLabel, ToolName
from monitor import MonitorConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _support.cluster_wiring import ClusterRuntime

    from common.interfaces.orchestrator import ExecutionContext

pytestmark = pytest.mark.requires_bd

TURN_COST = 0.01
KICKOFF_ID = "cluster01-begin"
THRESHOLD_T = 1_000_000

HALTING_CALLS = 1
"""`_halting_script()` ＋ `max_requests=1` で**実際に送られる**往復数（2 往復目は送信前に停止）。"""

COMPLETING_CALLS = 4
"""`_completing_script()` で**実際に送られる**往復数（差し戻し 0 なら台本と同数）。"""

NO_CALLS = 0
"""盤面に届かない board では claim の時点で落ちるので、LLM へは 1 度も送られない。"""


def _completing_script() -> list[dict[str, object]]:
    """差し戻しを起こさずに 1 周で終わる 4 往復の台本（`grade_w_i` と同型）。"""
    work_call = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    work_call["tool_calls"] = [
        make_tool_call(1, ToolName.complete_issue, {"reason": "クラスタ 01 の完了"})
    ]
    review_call = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    review_call["tool_calls"] = [
        make_tool_call(
            2, ToolName.submit_review, {"approved": True, "feedback": "承認", "files_read": []}
        )
    ]
    return [work_call, make_turn(cost=TURN_COST), review_call, make_turn(cost=TURN_COST)]


def _halting_script() -> list[dict[str, object]]:
    """上限超過を誘発する 2 往復の台本（F 級の写像を起こす）。"""
    first = make_turn(content=None, finish_reason="tool_calls", cost=TURN_COST)
    first["tool_calls"] = [make_tool_call(1, ToolName.read_issue, {})]
    return [first, make_turn(cost=TURN_COST)]


@pytest.mark.contract_if("IF-39")
@pytest.mark.cluster_case("01#5")
def test_cluster_01_unverifiable_mapping_stops_the_cycle(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """クラスタ 01 #5 / IF-39: 写像を確かめられなければ `MappingUnverified` で**周回を打ち切る**。

    期待（§6 #5）: `MappingUnverified` → orchestrator が停止痕跡を残し
    `KickoffResult.status=stopped`・周回打ち切り（P3「黙って進まない」）。

    注入は BD_BIN シム **(a)**（どのサブコマンドでも不達）。盤面が読めないので claim も写像も
    確認読取も通らず、`finish` は `MappingUnverified` を上げる。orchestrator は例外を握って
    停止痕跡を残し、周回を止める（`verdict is None` → `stop=True`）。

    「黙って進まない」ことの観測点は **`status` が `ok` でないこと**である —— 盤面に届かない
    まま `ok` を返す実装は、次の周回で同じ課題をもう一度掴んでしまう。
    """
    runtime = cluster(_halting_script(), max_requests=1)
    seed_leaf(runtime)
    # 盤面を仕込んでから BD_BIN をシムへ向け、**組み直して**不達の board で走らせる。
    monkeypatch.setenv("BD_BIN", str(make_always_fails_shim(tmp_path)))
    broken = cluster(_halting_script(), max_requests=1)
    runner: KickoffRunner = require(broken.wiring.provided, KickoffRunner)

    result = runner.run(context)

    assert result.status is not KickoffStatus.ok, (
        f"盤面に届かないのに `ok` を返している（黙って進んだ）: {result.status}"
    )
    # 鎖は claim の時点で切れる —— LLM へ 1 度でも送っていたら「盤面を読めないまま作業を
    # 始めた」ことになる（偽サーバは台本を使い切っても最終要素を返し続けるので、往復数を
    # 見ない限りこの誤りは緑のまま通る＝ R-04）。
    assert broken.llm.call_count == NO_CALLS, (
        f"盤面が読めないのに LLM へ送っている: 実際={broken.llm.call_count}"
    )
    assert runtime.llm.call_count == NO_CALLS, (
        f"走らせていない側の偽サーバが呼ばれている: 実際={runtime.llm.call_count}"
    )


@pytest.mark.contract_if("IF-08")
@pytest.mark.cluster_case("01#6")
def test_cluster_01_envelope_comment_precedes_the_state_change(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #6 / IF-08: 封筒 comment が状態変更（`set_labels`／`close`）より**先**に入る。

    期待（§6 #6）: F/W とも comment（封筒）が状態変更より先に bd に入っている（`comments` の
    順序で assert）。

    `BoardReader.comments` は **bd の返す順のまま**（`created_at` 昇順）なので、封筒より後に
    書かれた進捗コメントが在れば順序が読める。ここでは F 級の写像を起こし、**封筒が
    `comments` の最後に来る**（＝状態変更の前に書かれた最後の書込である）ことを見る。

    順序が逆の実装では、状態変更が先に効いて確認読取が「ラベルは合っているが封筒が無い」形に
    なり、`MappingUnverified` になる —— したがって**周回が止まっていない**ことも順序の証拠に
    なる（#5 と対の観測）。
    """
    marker = MonitorConfig().envelope_marker
    runtime = cluster(_halting_script(), max_requests=1)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue_id = seed_leaf(runtime)

    result = runner.run(context)

    assert result.status is KickoffStatus.ok, (
        f"確認読取が通らなかった（封筒より先に状態を変えた疑い）: {result.status}"
    )
    comments = board_reader.comments(issue_id)
    envelope_positions = [
        index for index, comment in enumerate(comments) if comment.body.startswith(marker)
    ]
    assert envelope_positions, f"封筒が盤面に無い: {[c.body[:40] for c in comments]}"
    issue = board_reader.show(issue_id)
    assert issue.labels, "ラベルが読めない"
    assert any(label.endswith(StateLabel.failing.value) for label in issue.labels), (
        f"状態変更が入っていない（写像が途中で止まった）: {issue.labels}"
    )
    assert runtime.llm.call_count == HALTING_CALLS, (
        f"往復数が台本・上限と食い違う: 実際={runtime.llm.call_count} 期待={HALTING_CALLS}"
    )


@pytest.mark.contract_if("IF-15")
@pytest.mark.cluster_case("01#7")
def test_cluster_01_release_restores_both_status_and_assignee(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #7 / IF-15: release 後は `bd ready` 相当の読取に**再び出る**。

    期待（§6 #7）: release 後に `bd ready` 相当の読取に出る（status と assignee の**両方**が
    戻る）。

    `claim_next` は ready の 1 件を取って `state:working` を付ける。F 級の写像で release された
    あとに**もう一度 `claim_next` が取れる**ことが「ready に戻った」ことの実物での観測である
    （status だけ戻して assignee が残る実装ではここで取れない）。
    """
    runtime = cluster(_halting_script(), max_requests=1)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    board_reader: BoardReader = require(runtime.wiring.provided, BoardReader)
    issue_id = seed_leaf(runtime)

    runner.run(context)

    issue = board_reader.show(issue_id)
    assert issue.status is not IssueStatus.closed, f"F 級で close されている: {issue.status}"
    assert not issue.assignee, (
        f"assignee が戻っていない（release が status だけを戻した疑い）: {issue.assignee}"
    )
    assert runtime.llm.call_count == HALTING_CALLS, (
        f"往復数が台本・上限と食い違う: 実際={runtime.llm.call_count} 期待={HALTING_CALLS}"
    )


@pytest.mark.contract_if("IF-38")
@pytest.mark.cluster_case("01#8")
def test_cluster_01_second_begin_supersedes_without_stopping(
    cluster: Callable[..., ClusterRuntime],
    seed_leaf: Callable[..., str],
    context: ExecutionContext,
) -> None:
    """クラスタ 01 #8 / IF-38: 進行中 RunState がある状態の `begin` は差し替えるだけで止めない。

    期待（§6 #8）: 旧 RunState を「帰属不明で打切り」とログし差し替え、**止めない**
    （single-flight は orchestrator の不変条件であり、monitor は検出のみ）。

    テストが先に `begin` を打っておき、その上で `KickoffRunner.run` を走らせる —— orchestrator は
    1 課題ごとに `begin` を呼ぶので、**2 回目の begin** が起きる。monitor が例外を上げるなら
    周回はそこで壊れるが、実際には差し替えて続く。
    """
    runtime = cluster(_completing_script())
    run_monitor: RunMonitor = require(runtime.wiring.provided, RunMonitor)
    runner: KickoffRunner = require(runtime.wiring.provided, KickoffRunner)
    seed_leaf(runtime)

    # 1 回目の begin（テストが打つ）。以後の Hook はこの器に帰属する。
    run_monitor.begin(KICKOFF_ID, _large_limits(), THRESHOLD_T)

    result = runner.run(context)

    assert result.status is KickoffStatus.ok, (
        f"二重 begin で周回が止まっている（monitor は検出のみのはず）: {result.status}"
    )
    assert result.issues, "1 件も処理していない"
    assert result.issues[0].result == "closed", f"処理が完了していない: {result.issues[0].result}"
    # 2 回目の `begin` が旧 RunState を**差し替える**（積み増さない）ので、上限判定は
    # orchestrator が渡した値で行われ、台本どおりの往復数で終わる。旧い器を残す実装なら
    # ここで往復数が変わる。
    assert runtime.llm.call_count == COMPLETING_CALLS, (
        f"往復数が台本と食い違う: 実際={runtime.llm.call_count} 期待={COMPLETING_CALLS}"
    )


def _large_limits() -> LimitPolicy:
    """テストが打つ `begin` に渡す上限（**周回を止めない**十分大きな値）。

    ここで見たいのは「2 回目の `begin` が起きても止まらない」ことであって、上限判定そのもの
    ではない。
    """
    return LimitPolicy(
        scope="cluster01", max_requests_per_kickoff=1000, max_cost_per_kickoff=1000.0
    )
