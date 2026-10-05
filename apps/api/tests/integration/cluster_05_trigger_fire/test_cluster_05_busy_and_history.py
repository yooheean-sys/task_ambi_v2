"""クラスタ 05 の二重発火・履歴書込失敗・shutdown（IF-49 ×3）。§6 #6・#10・#11。

いずれも「**安全側に倒さない**」の観測である（N-1〜N-6）—— 2 本目は `KickoffBusy` を握りつぶさず
履歴へ `busy` で残し、履歴が書けなくても kickoff は止めず、`shutdown(wait=True)` は進行中の
kickoff を完走させる。

**9 スタブは `claim_next=None` で周回が即終了する**ので、二重発火を観測するには 1 本目を
走らせ続ける待機ラッパが要る（FR-033a）。待ちには上限がある（既定 10 秒）。
"""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest
from _support.cluster_doubles import Gate, GatedBoardWriter
from _support.contract_stubs import StubBoardWriter

from common.interfaces.board import BoardWriter

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from _support.cluster_stores import TempAreaB
    from _support.composition import HttpClient
    from _support.contract_stubs import CallLog

    from api.wiring import ProtocolOverrides

TRIGGERS_PATH = "/triggers"
HTTP_ACCEPTED = 202
BUSY_STATUS = "busy"
"""履歴 csv の `status` 欄に載る「別の kickoff が実行中だった」の印。"""


def _kickoff_calls(call_log: CallLog) -> int:
    """周回が始まった回数（`claim_next` がスタブへ届いた回数）。"""
    return len(call_log.of("BoardWriter", "claim_next"))


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#6")
def test_cluster_05_second_fire_is_recorded_as_busy_without_stopping_the_first(
    cluster_client: Callable[..., HttpClient],
    stub_overrides: ProtocolOverrides,
    call_log: CallLog,
    area_b: TempAreaB,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
) -> None:
    """クラスタ 05 #6 / IF-49: 2 本目は `KickoffBusy` → 履歴 `status=busy`（1 本目は止めない）。

    期待（§6 #6）: 2 本目は `KickoffBusy` → `finalize_history(status=busy)`（kickoff は
    止めない）。

    1 本目を `GatedBoardWriter`（`claim_next` の中で待つ）で保持したまま 2 本目を投げる ——
    9 スタブのままでは周回が即終わってしまい、二重発火の窓が開かない（FR-033a）。

    `KickoffBusy` を**握りつぶさない**ことは、履歴に `busy` の行が残ることで見る。
    """
    gate = Gate()
    inner: BoardWriter = StubBoardWriter(call_log)
    gated: BoardWriter = GatedBoardWriter(inner, gate)
    overrides = {**stub_overrides, BoardWriter: gated}

    with cluster_client(overrides=overrides) as http:
        first = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
        assert first.status_code == HTTP_ACCEPTED, first.text
        gate.wait_entered(what="1 本目の `claim_next`")

        second = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
        assert second.status_code == HTTP_ACCEPTED, (
            f"2 本目の受付そのものは 202 のはず（busy は履歴に残す）: {second.text}"
        )
        poll(
            lambda: any(BUSY_STATUS in row for row in history_rows()),
            what="2 本目の busy が履歴に残ること",
        )
        gate.release()

    rows = history_rows()
    assert any(BUSY_STATUS in row for row in rows), f"busy の行が履歴に無い: {rows}"
    assert _kickoff_calls(call_log) >= 1, "1 本目が走っていない（止めてしまった疑い）"


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#10")
def test_cluster_05_history_write_failure_does_not_stop_the_kickoff(
    cluster_client: Callable[..., HttpClient],
    area_b: TempAreaB,
    call_log: CallLog,
    poll: Callable[..., None],
) -> None:
    """クラスタ 05 #10 / IF-49: 履歴が書けなくても kickoff は**止めない**。

    期待（§6 #10）: 開始行の追記失敗 → 構造化ログのみ（kickoff は止めない）／終了時の書換え
    失敗 → 旧ファイルが残りログに確定値。

    履歴ディレクトリを**読み取り専用**にして書込を失敗させる。`KickoffRunner.run` が呼ばれる
    こと（＝ kickoff が走ったこと）をスタブの記録で確かめる。

    teardown で権限を戻す（一時物を残さない＝規約 §9）。
    """
    original_mode = area_b.history_dir.stat().st_mode
    area_b.history_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with cluster_client() as http:
            accepted = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
            assert accepted.status_code == HTTP_ACCEPTED, accepted.text
            poll(
                lambda: _kickoff_calls(call_log) >= 1,
                what="履歴が書けなくても kickoff が走ること",
            )
    finally:
        area_b.history_dir.chmod(original_mode)

    assert _kickoff_calls(call_log) >= 1, "履歴の失敗で kickoff を止めている"


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#11")
def test_cluster_05_shutdown_lets_the_running_kickoff_finish(
    cluster_client: Callable[..., HttpClient],
    stub_overrides: ProtocolOverrides,
    call_log: CallLog,
    area_b: TempAreaB,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
) -> None:
    """クラスタ 05 #11 / IF-49: `shutdown(wait=True)` で進行中の kickoff が**完走**する。

    期待（§6 #11）: `shutdown(wait=True)` で進行中 kickoff は完走。

    1 本目を待機ラッパで保持し、**解放してから** `with` を抜ける。lifespan の終了処理が
    `shutdown(wait=True)` を呼ぶので、進行中の周回は途中で切られずに履歴の確定行まで進む。

    「完走した」ことは履歴に `busy` でない行（＝最後まで進んだ run）が残ることで見る。
    """
    gate = Gate()
    inner: BoardWriter = StubBoardWriter(call_log)
    gated: BoardWriter = GatedBoardWriter(inner, gate)
    overrides = {**stub_overrides, BoardWriter: gated}

    with cluster_client(overrides=overrides) as http:
        accepted = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
        assert accepted.status_code == HTTP_ACCEPTED, accepted.text
        gate.wait_entered(what="1 本目の `claim_next`")
        gate.release()
        poll(lambda: _kickoff_calls(call_log) >= 1, what="1 本目の周回")

    rows = history_rows()
    assert len(rows) >= 2, f"履歴に確定行が無い: {rows}"
    finished = [row for row in rows[1:] if BUSY_STATUS not in row]
    assert finished, f"完走した run の行が無い: {rows}"
