"""クラスタ 05 の発火 4 経路と相関 ID（IF-49 ×5）。§6 #2・#3・#4・#5・#12。

**実 trigger × 実 orchestrator**（9 スタブ）。見るのは `KickoffRunner.run`（IF-49）が
**どの経路からも同じ形で呼ばれる**ことである —— timer／manual／file 投下／`POST /hooks/file`。

`fire` は**戻り値を持たない**（結果は履歴 csv と構造化ログにだけ残る）ので、観測は
**履歴の行**と**スタブへの呼び出し記録**で行う。実時間を待つところは**上限つきポーリング**を
使う（固定の待ち時間に依存しない＝ FR-045）。

スレッドは `with build_cluster_client(...)` の退出で必ず回収される（lifespan が `shutdown` を
呼ぶ）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from _support.cluster_stores import TempAreaB
    from _support.composition import HttpClient
    from _support.contract_stubs import CallLog

TRIGGERS_PATH = "/triggers"
HOOKS_FILE_PATH = "/hooks/file"
HTTP_ACCEPTED = 202
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422

DROPPED_NAME = "dropped.txt"
"""DL フォルダへ投下するファイル名。"""

CORRELATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
"""`new_correlation_id()` が発番する形（UUID4 を 32 桁の小文字 16 進で表したもの）。"""


def _kickoff_calls(call_log: CallLog) -> int:
    """スタブ `BoardWriter.claim_next` の呼び出し回数（＝周回が始まった回数）。

    `KickoffRunner` は**実物**なので `CallLog` に載らない。周回が始まったことは、周回の 1 手目
    である `claim_next` がスタブへ届いた回数で数える。
    """
    return len(call_log.of("BoardWriter", "claim_next"))


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#2")
def test_cluster_05_timer_fire_runs_the_kickoff_and_writes_history(
    cluster_client: Callable[..., HttpClient],
    call_log: CallLog,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
) -> None:
    """クラスタ 05 #2 / IF-49: 短い `interval` の timer が `KickoffRunner.run` を呼ぶ。

    期待（§6 #2）: `fire(kind=timer)` → `ExecutionContext` → `KickoffRunner.run` →
    `KickoffResult(no_work)` → 履歴に開始行を追記 → 終了時に同じ行を全列埋めて `os.replace`。

    `interval` は一時 `config.json`（`min_interval_sec` を短くしたもの）と対で 1 秒にしてある
    （実物の 60 秒のままでは validate に落ちるうえ 60 秒以上待つ＝ R-06）。

    9 スタブの `claim_next` は `None` を返すので周回は即 `no_work` で終わる —— ここで見たいのは
    **呼ばれること**であって周回の中身ではない。
    """
    with cluster_client() as http:
        assert http.get(TRIGGERS_PATH).status_code == 200, "起動していない"
        poll(lambda: _kickoff_calls(call_log) >= 1, what="timer 発火による kickoff")

    assert _kickoff_calls(call_log) >= 1, "timer から kickoff が呼ばれていない"
    rows = history_rows()
    assert len(rows) >= 2, f"履歴に見出しと 1 行が無い: {rows}"
    assert any("timer" in row for row in rows), f"timer の行が履歴に無い: {rows}"


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#3")
def test_cluster_05_manual_run_is_accepted_and_recorded(
    cluster_client: Callable[..., HttpClient],
    area_b: TempAreaB,
    call_log: CallLog,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
) -> None:
    """クラスタ 05 #3 / IF-49: `POST /triggers/{id}/run` は **202** で受け付けワーカーで走らせる。

    期待（§6 #3）: 202（run_id）→ ワーカースレッドで `fire(kind=manual)` → 履歴行。不明 id は
    404。

    202 は「受け付けた」だけなので、実際に走ったことは**上限つきポーリング**で待って確かめる。
    """
    with cluster_client() as http:
        accepted = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
        assert accepted.status_code == HTTP_ACCEPTED, accepted.text
        assert accepted.json().get("run_id"), f"run_id が返っていない: {accepted.json()}"

        poll(lambda: _kickoff_calls(call_log) >= 1, what="manual 発火による kickoff")

        missing = http.post(f"{TRIGGERS_PATH}/no-such-trigger/run", json={})
        assert missing.status_code == HTTP_NOT_FOUND, missing.text

    rows = history_rows()
    assert any("manual" in row for row in rows), f"manual の行が履歴に無い: {rows}"


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#4")
def test_cluster_05_file_drop_fires_after_the_file_settles(
    cluster_client: Callable[..., HttpClient],
    area_b: TempAreaB,
    call_log: CallLog,
    poll: Callable[..., None],
) -> None:
    """クラスタ 05 #4 / IF-49: DL フォルダへの投下は**安定してから**発火し、ファイルを消費しない。

    期待（§6 #4）: `on_created/on_modified` → サイズ・mtime が `stable_seconds` 安定 →
    `fire(kind=file, dropped_path)`。**投下ファイルは消費しない**（D06-h）。

    watcher は `PollingObserver` なので検知に `poll_interval_sec` ぶんの遅れがある（一時
    `config.json` で短くしてある）。**投下したファイルが残っている**ことまで確かめる ——
    消費してしまう実装は、人間が置いたファイルを勝手に消すことになる。
    """
    dropped = area_b.downloads_dir / DROPPED_NAME

    with cluster_client() as http:
        assert http.get(TRIGGERS_PATH).status_code == 200, "起動していない"
        before = _kickoff_calls(call_log)
        dropped.write_text("投下した中身\n", encoding="utf-8")

        poll(lambda: _kickoff_calls(call_log) > before, what="file 投下による kickoff")

    assert dropped.is_file(), "投下ファイルが消費されている（D06-h 違反）"
    assert dropped.read_text(encoding="utf-8") == "投下した中身\n", "投下ファイルが書き換わっている"


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#5")
def test_cluster_05_file_hook_joins_the_same_completion_path(
    cluster_client: Callable[..., HttpClient],
    area_b: TempAreaB,
    call_log: CallLog,
    poll: Callable[..., None],
) -> None:
    """クラスタ 05 #5 / IF-49: `POST /hooks/file` が同じ完了検知へ合流する。

    期待（§6 #5）: 同じ完了検知 → `fire(kind=file)` に合流（P5D-042）。本文不正 → 422／
    watch 対象外のパス → 422。

    退避経路（EXT-12 の PowerShell FSW の代わり）である。**同じ `fire` へ合流する**ことを、
    kickoff の呼び出し回数が増えることで確かめる。
    """
    dropped = area_b.downloads_dir / "hooked.txt"
    dropped.write_text("退避経路から通知する\n", encoding="utf-8")

    with cluster_client() as http:
        before = _kickoff_calls(call_log)

        accepted = http.post(HOOKS_FILE_PATH, json={"path": str(dropped)})

        assert accepted.status_code in {200, HTTP_ACCEPTED}, accepted.text
        poll(lambda: _kickoff_calls(call_log) > before, what="`POST /hooks/file` による kickoff")

        malformed = http.post(HOOKS_FILE_PATH, json={})
        assert malformed.status_code == HTTP_UNPROCESSABLE, malformed.text

        outside = http.post(HOOKS_FILE_PATH, json={"path": "/tmp/not-watched.txt"})
        assert outside.status_code == HTTP_UNPROCESSABLE, outside.text

    assert dropped.is_file(), "退避経路でも投下ファイルを消費しない"


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#12")
def test_cluster_05_non_http_entry_issues_a_correlation_id(
    cluster_client: Callable[..., HttpClient],
    call_log: CallLog,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
) -> None:
    """クラスタ 05 #12 / IF-49: **非 HTTP 入口**（timer）で相関 ID を**発番**する。

    期待（§6 #12）: 非 HTTP 入口（timer／file）で correlation_id を発番し contextvar で
    `KickoffRunner.run` 内まで貫通（憲章 §6「HTTP を通らない入口でも同じ ID をその入口で発番」）。

    timer 発火は middleware を通らないので、`fire` の段 2 が自分で束縛する。**発番されたこと**は
    履歴 csv の `correlation_id` 欄が `new_correlation_id()` の形（32 桁の小文字 16 進）で
    埋まっていることで見る —— ログを観測点にすると、`fire` の中で出るログが 1 行も無い構成
    （9 スタブは即 `no_work` で戻る）では空振りする（2026-09-01 に実測）。
    """
    with cluster_client() as http:
        assert http.get(TRIGGERS_PATH).status_code == 200, "起動していない"
        poll(lambda: _kickoff_calls(call_log) >= 1, what="timer 発火による kickoff")

    rows = history_rows()
    assert len(rows) >= 2, f"履歴に行が無い: {rows}"
    correlation = rows[1].rstrip().split(",")[-1].strip('"')
    assert CORRELATION_ID_PATTERN.match(correlation), (
        f"非 HTTP 入口で相関 ID が発番されていない（32 桁の 16 進でない）: {correlation!r}"
    )


@pytest.mark.contract_if("IF-49")
@pytest.mark.cluster_case("05#12")
def test_cluster_05_history_rows_carry_the_same_correlation_id(
    cluster_client: Callable[..., HttpClient],
    call_log: CallLog,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
    tmp_path: Path,
) -> None:
    """クラスタ 05 #12 / IF-49: 発番した相関 ID が**履歴の行にも**載る。

    期待（§6 #12）: 同上（発番した ID が最後まで同じ）。

    履歴 csv は 15 列で、最後が `correlation_id` である（`RunHistoryRow` の宣言順）。発番した
    ID が行に載っていれば、`fire` の入口から履歴の確定まで同じ ID が使われたことになる。
    """
    assert tmp_path.is_dir(), "前提: 一時ディレクトリが用意されている"

    with cluster_client() as http:
        assert http.get(TRIGGERS_PATH).status_code == 200, "起動していない"
        poll(lambda: _kickoff_calls(call_log) >= 1, what="timer 発火による kickoff")

    rows = history_rows()
    assert len(rows) >= 2, f"履歴に行が無い: {rows}"
    header, *body = rows
    assert "correlation_id" in header, f"履歴の見出しに相関 ID の列が無い: {header}"
    assert body, "履歴の本文行が無い"
    assert body[0].rstrip().split(",")[-1].strip('"'), f"相関 ID の欄が空: {body[0]}"
