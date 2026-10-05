"""クラスタ 05 の lifespan と宣言 CRUD（**IF に紐づかない観点行**）。§6 #1・#7。

`CONTRACT_META = True` の理由（plan (v)・B-11）: lifespan の再登録も UC-09 の validate も
trigger の内部経路であり、契約 IF（IF-49）を主検証としない。**このファイルに `contract_if` を
書かない**。

C 層（`wiring_contract/`）は「lifespan が 1 つ登録され非同期文脈管理子であること」までしか
見ていない（§0-8）。ここは**入って何が起きるか**（宣言の再登録・watcher 開始・雛形生成・
中断）を見る。

FR-032（配線の過不足）もここに置く —— `KickoffRunner` を override に入れると
`DuplicateProviderError` になることまで確かめる（CD-10）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_stores import make_temp_area_b
from _support.cluster_wiring import build_cluster_client, cluster_modules
from _support.contract_stubs import CallLog, StubKickoffRunner

from api.exceptions import DuplicateProviderError, UnsatisfiedContractError
from common.interfaces.orchestrator import KickoffRunner
from trigger.exceptions import TriggerDefinitionsError, TriggerRootError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _support.cluster_stores import TempAreaB
    from _support.composition import HttpClient

    from api.wiring import ProtocolOverrides

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

TRIGGERS_PATH = "/triggers"
WATCHER_PATH = "/watcher"
HTTP_UNPROCESSABLE = 422
DEFAULT_TRIGGER_ID = "ready-sweep"
"""既定トリガーの固定 ID（削除不可）。"""


@pytest.mark.cluster_case("05#1")
def test_cluster_05_lifespan_registers_every_declared_trigger(
    cluster_client: Callable[..., HttpClient], area_b: TempAreaB
) -> None:
    """クラスタ 05 #1: lifespan が `triggers.yaml` を読み**全ジョブを再登録**し watcher を開始する。

    期待（§6 #1）: triggers.yaml を読み全ジョブを再登録（宣言が正・jobstore はインメモリ
    D07-a）→ watcher 開始。

    宣言は 3 件（既定・timer・file）。`GET /triggers` に 3 件とも出て、`GET /watcher` が
    scheduler の稼働と `file` トリガーの watcher を見せることを確かめる。
    """
    with cluster_client() as http:
        listed = http.get(TRIGGERS_PATH)
        watcher = http.get(WATCHER_PATH)

        assert listed.status_code == 200, listed.text
        ids = {item["trigger_id"] for item in listed.json()}
        assert DEFAULT_TRIGGER_ID in ids, f"既定トリガーが登録されていない: {ids}"
        assert area_b.timer_trigger_id in ids, f"timer トリガーが登録されていない: {ids}"
        assert area_b.file_trigger_id in ids, f"file トリガーが登録されていない: {ids}"

        assert watcher.status_code == 200, watcher.text
        state = watcher.json()
        assert state["scheduler_running"] is True, f"scheduler が動いていない: {state}"
        assert state["watchers"], f"watcher が 1 つも立っていない: {state}"


@pytest.mark.cluster_case("05#1")
def test_cluster_05_missing_definitions_file_is_scaffolded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_env: Path,
    stub_overrides: ProtocolOverrides,
) -> None:
    """クラスタ 05 #1: `triggers.yaml` が無ければ**既定トリガーだけの雛形**を生成して起動する。

    期待（§6 #1）: 不在なら既定トリガーだけの雛形を生成。

    領域 B を**空**にしてから組む。雛形が領域 B の**内側**に生成されることも確かめる
    （外に書いたら止め木の意味が無い）。
    """
    from _support.cluster_env import point_config_at  # noqa: PLC0415  # 局所的に要るだけ
    from _support.cluster_stores import short_trigger_config  # noqa: PLC0415

    area = make_temp_area_b(tmp_path, write_definitions=False)
    monkeypatch.setenv("TRIGGER_ROOT", str(area.root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(area.downloads_dir))
    point_config_at(monkeypatch, "trigger", short_trigger_config(), tmp_path)
    assert not area.definitions.exists(), "前提: `triggers.yaml` は無い"

    with build_cluster_client("05", stub_overrides) as http:
        listed = http.get(TRIGGERS_PATH)

    assert listed.status_code == 200, listed.text
    assert area.definitions.is_file(), "雛形が生成されていない"
    ids = {item["trigger_id"] for item in listed.json()}
    assert ids == {DEFAULT_TRIGGER_ID}, f"既定トリガーだけのはず: {ids}"
    assert cluster_env.is_file(), "止め木の一時 `.env` が消えている"


@pytest.mark.cluster_case("05#1")
def test_cluster_05_broken_definitions_abort_the_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_overrides: ProtocolOverrides,
) -> None:
    """クラスタ 05 #1: YAML 不正・`TRIGGER_ROOT` 不正は**起動中断**（黙って既定にしない）。

    期待（§6 #1）: YAML 不正／`TRIGGER_ROOT` 不正 → 起動中断（trigger-F13・F01）。

    2 通りを踏む —— `triggers.yaml` を壊す形と、`TRIGGER_ROOT` が実在しない形。
    """
    from _support.cluster_env import point_config_at  # noqa: PLC0415  # 局所的に要るだけ
    from _support.cluster_stores import short_trigger_config  # noqa: PLC0415

    broken = make_temp_area_b(tmp_path, definitions_body="triggers: [ unclosed\n")
    monkeypatch.setenv("TRIGGER_ROOT", str(broken.root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(broken.downloads_dir))
    point_config_at(monkeypatch, "trigger", short_trigger_config(), tmp_path)

    with pytest.raises(TriggerDefinitionsError):
        build_cluster_client("05", stub_overrides)

    monkeypatch.setenv("TRIGGER_ROOT", str(tmp_path / "does_not_exist"))
    with pytest.raises(TriggerRootError):
        build_cluster_client("05", stub_overrides)


@pytest.mark.cluster_case("05#7")
def test_cluster_05_trigger_crud_validates_and_reflects_in_the_scheduler(
    cluster_client: Callable[..., HttpClient], area_b: TempAreaB
) -> None:
    """クラスタ 05 #7: UC-09 の validate が効き、保存が scheduler へ反映される。

    期待（§6 #7）: cron 5 フィールド／interval ≥ min／watch_dir 絶対パス／既定トリガー削除不可
    → 422。保存 → `triggers.yaml` を一時ファイル→`os.replace` → 再登録。enable／disable が
    scheduler に反映。

    違反の 3 形（cron の欄数・interval の下限・既定トリガーの削除）と、正常な保存の反映を
    1 本で見る。
    """
    with cluster_client() as http:
        bad_cron = http.post(
            TRIGGERS_PATH,
            json={
                "name": "壊れた cron",
                "type": "cron",
                "spec": "* * *",
                "condition": {"labels": ["role:executor"], "project_key": None},
                "enabled": True,
            },
        )
        assert bad_cron.status_code == HTTP_UNPROCESSABLE, bad_cron.text

        too_short = http.post(
            TRIGGERS_PATH,
            json={
                "name": "短すぎる間隔",
                "type": "interval",
                "spec": "0",
                "condition": {"labels": ["role:executor"], "project_key": None},
                "enabled": True,
            },
        )
        assert too_short.status_code == HTTP_UNPROCESSABLE, too_short.text

        deleted = http.delete(f"{TRIGGERS_PATH}/{DEFAULT_TRIGGER_ID}")
        assert deleted.status_code == HTTP_UNPROCESSABLE, deleted.text

        toggled = http.patch(f"{TRIGGERS_PATH}/{area_b.timer_trigger_id}", json={"enabled": False})
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["enabled"] is False, toggled.json()

    saved = area_b.definitions.read_text(encoding="utf-8")
    assert "enabled: false" in saved, f"無効化が `triggers.yaml` に反映されていない: {saved}"


@pytest.mark.cluster_case("05#1")
def test_cluster_05_wiring_rejects_a_kickoff_runner_override(
    stub_overrides: ProtocolOverrides,
) -> None:
    """FR-032: `KickoffRunner` を override に入れると `DuplicateProviderError`（CD-10）。

    対応する §6 の行が無いので `05#1`（起動の成否）に対応させた（判断は design-sync に記録）。

    05 の override は **9 件**で、`KickoffRunner` は**含めてはならない** —— trigger が要求する
    `KickoffRunner` は有効化した実物の orchestrator が供給するからである。1 件抜いた場合の
    `UnsatisfiedContractError` も対にして見る。
    """
    from _support.cluster_wiring import build_cluster_wiring  # noqa: PLC0415  # 本テストだけが使う

    from common.interfaces.board import BoardReader  # noqa: PLC0415

    with pytest.raises(DuplicateProviderError) as duplicated:
        build_cluster_wiring("05", {**stub_overrides, KickoffRunner: StubKickoffRunner(CallLog())})
    assert "KickoffRunner" in str(duplicated.value), duplicated.value
    assert "orchestrator" in str(duplicated.value), duplicated.value

    reduced = {key: value for key, value in stub_overrides.items() if key is not BoardReader}
    with pytest.raises(UnsatisfiedContractError) as unsatisfied:
        build_cluster_wiring("05", reduced)
    assert "BoardReader" in str(unsatisfied.value), unsatisfied.value


@pytest.mark.cluster_case("05#1")
def test_cluster_05_health_reports_the_cluster_modules(
    cluster_client: Callable[..., HttpClient],
) -> None:
    """US3/AC1: 組み上げ結果を `GET /health` の `modules` で観測する（T094）。

    05 にはこれまで、組み上がったインスタンスの MOD 集合を assert するテスト自体が無かった
    （`DuplicateProviderError` による間接確認のみ）。lifespan を通した実配線の app が
    `orchestrator`・`trigger` の 2 MOD で組まれていることを HTTP の口から確かめる。対応する
    §6 の行が無いので `05#1`（起動の成否。FR-032 と同じ寄せ方）に対応させた（判断は
    design-sync に記録）。

    期待値は `cluster_modules("05")` から引き、**整列して**比べる（重複も検知するため）。
    """
    with cluster_client() as http:
        response = http.get("/health")

    assert response.status_code == 200, response.text
    modules = response.json()["modules"]
    assert sorted(modules) == sorted(cluster_modules("05")), modules
