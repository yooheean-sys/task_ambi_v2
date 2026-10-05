"""クラスタ 05 の表示と縮退（**IF に紐づかない観点行**）。§6 #8・#9・#13。

`CONTRACT_META = True` の理由（plan (v)・B-11）: 表示の口も watcher の状態も trigger の内部で
閉じており、契約 IF（IF-49）を主検証としない。**このファイルに `contract_if` を書かない**。

#13 は**条件付き**（`CX-02`）—— `R12-P4`（`PollingObserver` の検知遅延と CPU）を**テスト内で
実測して判定**し、不合格なら `watcher_enabled=false` へ縮退して #4 を非実行にする（FR-022。
人間の目視に委ねない）。

実測した秒数は**予算（`DETECTION_BUDGET_SEC`）とは別物**なので、`record_property` と標準出力の
両方へ残す（T089）—— `uv run pytest … -k degrades -s` で読め、`--junitxml` を渡した実行では
`<property name="cx02_detection_sec" …>` として拾える。design-sync (4) に書くのはこの**実測値**で
あって予算ではない。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from _support.cluster_env import point_config_at
from _support.cluster_stores import make_temp_area_b, short_trigger_config
from _support.cluster_wiring import build_cluster_client

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from _support.cluster_stores import TempAreaB
    from _support.composition import HttpClient
    from _support.contract_stubs import CallLog

    from api.wiring import ProtocolOverrides

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

TRIGGERS_PATH = "/triggers"
HISTORY_PATH = "/history"
WATCHER_PATH = "/watcher"
HTTP_ACCEPTED = 202

DETECTION_BUDGET_SEC = 15.0
"""`CX-02` の合否: 投下から発火までがこの秒数以内なら watcher を使う経路を実行する。

`R12-P4` は「`PollingObserver` の検知遅延が実用に耐えるか」を問う。一時 `config.json` の
`poll_interval_sec`・`stable_seconds` を短くした構成で、この上限を超えるようなら縮退する。

⚠ これは**予算であって実測値ではない**。実測は `DETECTION_PROPERTY` に残す（T089）。
"""

DETECTION_PROPERTY = "cx02_detection_sec"
"""実測した検知遅延を残す `record_property` の名前（design-sync (4) に書く値の出所）。"""

DETECTION_POLL_SEC = 0.1
"""検知待ちのポーリング間隔（実測の分解能）。"""

DETECTION_IDLE_TIMER_SEC = 600
"""実測中に **timer トリガーを黙らせる**ための間隔（T089）。

`_kickoff_calls` は `claim_next` の回数を数えるので、`timer_interval_sec=2` のままだと
**timer の発火でも増える** —— 実測値が「`PollingObserver` の検知遅延」なのか「timer の間隔」
なのか区別できない。予算(15 秒)より十分長い間隔にして、増えたら file 監視の発火だけにする。

2026-09-01 の実測では 2・600 のどちらでも **2.00 秒**で、交絡していないことを確認した
（`poll_interval_sec=0.2` ＋ `stable_seconds=1` の構成）。
"""


def _kickoff_calls(call_log: CallLog) -> int:
    """周回が始まった回数（`claim_next` がスタブへ届いた回数）。"""
    return len(call_log.of("BoardWriter", "claim_next"))


@pytest.mark.cluster_case("05#8")
def test_cluster_05_history_and_watcher_are_read_only_views(
    cluster_client: Callable[..., HttpClient],
    area_b: TempAreaB,
    call_log: CallLog,
    poll: Callable[..., None],
    history_rows: Callable[[], Sequence[str]],
) -> None:
    """クラスタ 05 #8: `GET /history` は csv を**表示用にだけ**読み、`GET /watcher` が状態を返す。

    期待（§6 #8）: `GET /history` は csv を表示用にだけ読む（ロジックは読み戻さない 憲章 §2）／
    `GET /watcher` が alive・scheduler 状態を返す。

    「表示用にだけ」を確かめるため、**csv を読んだ後で発火の判断が変わらない**ことを見る ——
    `GET /history` を挟んでも次の発火が普通に起きる（読み戻した値で分岐していない）。
    """
    with cluster_client() as http:
        accepted = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
        assert accepted.status_code == HTTP_ACCEPTED, accepted.text
        poll(lambda: _kickoff_calls(call_log) >= 1, what="1 本目の kickoff")
        poll(lambda: len(history_rows()) >= 2, what="履歴の確定行")

        history = http.get(HISTORY_PATH)
        watcher = http.get(WATCHER_PATH)

        assert history.status_code == 200, history.text
        rows = history.json()
        assert rows, "履歴が空（表示できていない）"
        assert all("run_id" in row for row in rows), rows[0]

        assert watcher.status_code == 200, watcher.text
        state = watcher.json()
        assert state["scheduler_running"] is True, state
        assert all(entry["alive"] is True for entry in state["watchers"]), state

        before = _kickoff_calls(call_log)
        again = http.post(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}/run", json={})
        assert again.status_code == HTTP_ACCEPTED, again.text
        poll(lambda: _kickoff_calls(call_log) > before, what="履歴を読んだ後の 2 本目")


@pytest.mark.cluster_case("05#9")
def test_cluster_05_watcher_state_is_shown_without_auto_recovery(
    cluster_client: Callable[..., HttpClient], area_b: TempAreaB
) -> None:
    """クラスタ 05 #9: watcher の生死は**見せるだけ**（自動復旧しない＝憲章 §10）。

    期待（§6 #9）: `WatcherState.alive=False` を `GET /watcher` で見せるのみ（自動復旧なし）。

    `GET /watcher` が `alive` を**そのまま**返すことと、`file` トリガーごとに 1 件ずつ状態を
    持つことを確かめる。自動復旧の仕組みが**無い**ことは、`watchers` の件数が宣言と一致し、
    観測のたびに増えたりしないことで見る。
    """
    with cluster_client() as http:
        first = http.get(WATCHER_PATH)
        second = http.get(WATCHER_PATH)

    assert first.status_code == 200, first.text
    state = first.json()
    watchers = state["watchers"]
    assert len(watchers) == 1, f"`file` トリガーは 1 件のはず: {watchers}"
    assert watchers[0]["trigger_id"] == area_b.file_trigger_id, watchers[0]
    assert "alive" in watchers[0], f"生死が見えない: {watchers[0]}"
    assert second.json()["watchers"] == watchers, (
        "観測のたびに watcher の状態が変わっている（復旧を試みた疑い）"
    )


@pytest.mark.cluster_case("05#13")
@pytest.mark.cluster_conditional("CX-02")
def test_cluster_05_file_watch_degrades_when_polling_is_too_slow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_overrides: ProtocolOverrides,
    call_log: CallLog,
    record_property: Callable[[str, object], None],
) -> None:
    """クラスタ 05 #13: `R12-P4` 不合格なら `watcher_enabled=false` へ**縮退**する。

    期待（§6 #13）: `watcher_enabled=false` → §6-4 は見ず §6-5（hooks）のみ。

    **条件付き（`CX-02`）**: `PollingObserver` の検知遅延を**テスト内で実測**する。上限
    （`DETECTION_BUDGET_SEC`）以内に発火すれば合格で、この経路（#4）は実行してよい。超えたら
    `pytest.skip` で理由を出力に残し、**縮退した構成（`watcher_enabled=false`）でも
    `POST /hooks/file` の経路が生きている**ことだけを確かめる。

    実測値は `record_property(DETECTION_PROPERTY, …)` と標準出力の**両方**に残し、design-sync
    (4) へ転記する（FR-022 の「非実行理由を出力に残す」）。予算（`DETECTION_BUDGET_SEC`）を
    記録しても「どれだけ余裕があったか」は分からない —— 予算ぎりぎりで通ったのか一桁下だったの
    かで、縮退の判断も次の実測の要否も変わる。

    検知しないまま予算を使い切った場合は `inf` を残す（「測れなかった」と「速かった」を
    取り違えない）。timer トリガーは `DETECTION_IDLE_TIMER_SEC` へ追いやってあるので、
    `claim_next` が増えたら **file 監視の発火**である（T089）。
    """
    area = make_temp_area_b(tmp_path, timer_interval_sec=DETECTION_IDLE_TIMER_SEC)
    monkeypatch.setenv("TRIGGER_ROOT", str(area.root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(area.downloads_dir))
    point_config_at(monkeypatch, "trigger", short_trigger_config(min_interval_sec=2), tmp_path)
    dropped = area.downloads_dir / "measured.txt"

    elapsed = float("inf")
    with build_cluster_client("05", stub_overrides) as http:
        assert http.get(TRIGGERS_PATH).status_code == 200, "起動していない"
        before = _kickoff_calls(call_log)
        started = time.monotonic()
        dropped.write_text("検知遅延の実測\n", encoding="utf-8")
        deadline = started + DETECTION_BUDGET_SEC
        while time.monotonic() < deadline:
            if _kickoff_calls(call_log) > before:
                elapsed = time.monotonic() - started
                break
            time.sleep(DETECTION_POLL_SEC)

    record_property(DETECTION_PROPERTY, elapsed)
    print(  # noqa: T201  実測値を出力に残すのが本テストの成果物の 1 つ（FR-022・T089）
        f"CX-02 実測: 検知遅延={elapsed:.2f} 秒 / 予算={DETECTION_BUDGET_SEC} 秒"
        f" / 分解能={DETECTION_POLL_SEC} 秒"
    )

    if elapsed > DETECTION_BUDGET_SEC:
        pytest.skip(
            f"CX-02: `PollingObserver` の検知が {DETECTION_BUDGET_SEC} 秒以内に起きない"
            f"（実測={elapsed:.2f} 秒。`watcher_enabled=false` へ縮退し #4 は非実行にする）"
        )

    assert elapsed <= DETECTION_BUDGET_SEC, (
        f"検知遅延が上限を超えた（縮退の判断が要る）: {elapsed:.2f} 秒"
    )


@pytest.mark.cluster_case("05#13")
@pytest.mark.cluster_conditional("CX-02")
def test_cluster_05_hooks_path_still_works_when_the_watcher_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_overrides: ProtocolOverrides,
    call_log: CallLog,
    poll: Callable[..., None],
) -> None:
    """クラスタ 05 #13: 縮退した構成（`watcher_enabled=false`）でも hooks 経路は生きている。

    期待（§6 #13）: `watcher_enabled=false` → §6-4（file 監視）は見ず §6-5（hooks）のみ。

    **条件付き（`CX-02`）** の**縮退した側**を実際に組んで確かめる —— watcher を切った構成で
    `GET /watcher` の `watchers` が空になり、それでも `POST /hooks/file` から発火できる。
    退避経路が生きていることが、縮退を選べる根拠である。
    """
    area = make_temp_area_b(tmp_path, timer_interval_sec=2)
    monkeypatch.setenv("TRIGGER_ROOT", str(area.root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(area.downloads_dir))
    point_config_at(
        monkeypatch,
        "trigger",
        short_trigger_config(min_interval_sec=2, watcher_enabled=False),
        tmp_path,
    )
    dropped = area.downloads_dir / "hooked_when_degraded.txt"
    dropped.write_text("縮退した構成からの通知\n", encoding="utf-8")

    with build_cluster_client("05", stub_overrides) as http:
        watcher = http.get(WATCHER_PATH)
        assert watcher.status_code == 200, watcher.text
        assert watcher.json()["watchers"] == [], f"watcher を切ったのに立っている: {watcher.json()}"

        before = _kickoff_calls(call_log)
        accepted = http.post("/hooks/file", json={"path": str(dropped)})

        assert accepted.status_code in {200, HTTP_ACCEPTED}, accepted.text
        poll(lambda: _kickoff_calls(call_log) > before, what="縮退した構成での hooks 発火")

    assert dropped.is_file(), "退避経路でも投下ファイルを消費しない"
