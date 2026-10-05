"""クラスタ 02 の削除ガード（IF-09＝`BoardReader.has_open_issues`）。§6 #5・#6・#8・#16。

**実 settings × 実 board**（一時 beads に実 `bd` を打つ）。`has_open_issues` の呼び出し点は
`settings/service/projects.py` の削除経路**だけ**である（R-08）。

#6 は BD_BIN シム **(a)**（どのサブコマンドでも不達）で `BoardUnavailable` を注入する ——
実 `bd` を正常に使う限り原理的に踏めない例外なので、CLI 境界から入れるしかない（規約 §6）。
**シムは当該テストに閉じる**（`monkeypatch.setenv` は fixture の終了で戻る）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_shims import make_always_fails_shim
from _support.cluster_stores import ClusterStores, seed_issue
from _support.cluster_wiring import build_cluster_client

from common.kernel.enums import KindLabel, Labels

if TYPE_CHECKING:
    from pathlib import Path

    from _support.cluster_logs import CorrelationRecorder
    from _support.composition import HttpClient

pytestmark = pytest.mark.requires_bd

PROJECTS_PATH = "/settings/projects"
GENERATION_HEADER = "X-Settings-Generation"
CORRELATION_HEADER = "X-Correlation-ID"


@pytest.mark.contract_if("IF-09")
@pytest.mark.cluster_case("02#5")
def test_cluster_02_delete_is_blocked_while_open_issues_remain(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #5 / IF-09: `project:<key>` の未完了課題が実 `bd` に在れば解除できない。

    期待（§6 #5）: **409**（現在状態との衝突。D12 §4 (2)）。

    盤面は実物 —— 課題を実 `bd` で 1 件仕込んでから `DELETE` を投げる。`has_open_issues` は
    `bd list -l project:<key> --json` を打ち、既定フィルタ（closed を除く）の件数で答える。

    領域 A の宣言が **1 バイトも変わらない**ことは read 前後のバイト比較で見る（US3/AC3。
    `is_file()` では中身の書き換えを見逃す＝ T097）。
    """
    project_key = stores.project_key
    declaration = stores.area_a.root / f"projects/{project_key}/project.yaml"
    before = declaration.read_bytes()
    seed_issue(
        stores.beads,
        title="未完了の課題",
        labels=[Labels.project(project_key), Labels.kind(KindLabel.child)],
    )

    response = client.delete(f"{PROJECTS_PATH}/{project_key}")

    assert response.status_code == 409, response.text
    assert project_key in response.json()["detail"]
    assert declaration.read_bytes() == before, (
        "409 のはずの解除で領域 A の宣言が書き換わっている（バイト同一性が破れた）"
    )


@pytest.mark.contract_if("IF-09")
@pytest.mark.cluster_case("02#6")
def test_cluster_02_delete_is_blocked_when_the_board_cannot_be_read(
    stores: ClusterStores, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """クラスタ 02 #6 / IF-09: 盤面が読めないときも解除しない（拒否側に倒れる）。

    期待（§6 #6）: `BoardUnavailable` → **409**。読めないときに `False`（＝未完了なし）へ
    推測で倒さない（D06-j(4)・P3「黙って進まない」）。

    注入は BD_BIN シム **(a)**。`board.build()` は `BD_BIN` を `shutil.which` ＋ `os.access` で
    しか確かめず `bd version` を打たないので、任意の実行可能スクリプトを向けても `build()` は
    通る。**シムを向けてからアプリを組む**必要があるので、`client` fixture は使わずここで組む。

    領域 A の宣言が **1 バイトも変わらない**ことは read 前後のバイト比較で見る（US3/AC3。
    `is_file()` では中身の書き換えを見逃す＝ T097）。
    """
    monkeypatch.setenv("BD_BIN", str(make_always_fails_shim(tmp_path)))
    project_key = stores.project_key
    declaration = stores.area_a.root / f"projects/{project_key}/project.yaml"
    before = declaration.read_bytes()

    with build_cluster_client("02", {}) as http:
        response = http.delete(f"{PROJECTS_PATH}/{project_key}")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "盤面が読めない" in detail, detail
    assert declaration.read_bytes() == before, (
        "盤面が読めないのに領域 A の宣言が書き換わっている（バイト同一性が破れた）"
    )


@pytest.mark.contract_if("IF-09")
@pytest.mark.cluster_case("02#8")
def test_cluster_02_delete_archives_the_project_when_the_board_is_clean(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #8 / IF-09: 未完了課題が無ければ解除でき、`_archive` へ**退避**される。

    期待（§6 #8）: `_archive` へ退避（**消さない**）・スナップショット世代＋1。

    盤面は実物のまま（課題を 1 件も仕込まない）。`has_open_issues` が偽を返す経路である。

    消すのは**2 つ目の PJ** である —— 唯一の PJ を解除すると `policy.csv` ごと退避されて
    `commands.yaml` の定義が死に定義（V-25）になり、盤面と無関係に 409 で弾かれる
    （`010` B-51 と同型。2026-09-01 に実測）。
    """
    project_key = stores.area_a.spare_project_key
    assert project_key is not None, "2 つ目の PJ が用意されていない（fixture の設定を確認する）"
    before = int(client.get(PROJECTS_PATH).headers.get(GENERATION_HEADER, "0"))

    response = client.delete(f"{PROJECTS_PATH}/{project_key}")

    assert response.status_code == 204, response.text
    generation = int(response.headers[GENERATION_HEADER])
    assert generation > before, f"世代が増えていない: 前={before} 後={generation}"
    assert not (stores.area_a.root / f"projects/{project_key}/project.yaml").is_file(), (
        "解除したのに元の場所に残っている"
    )
    archived = list((stores.area_a.root / "_archive").glob(f"**/{project_key}*"))
    assert archived, "`_archive` へ退避されていない（消してしまった疑い）"
    assert client.get(f"{PROJECTS_PATH}/{project_key}").status_code == 404


@pytest.mark.contract_if("IF-09")
@pytest.mark.cluster_case("02#16")
def test_cluster_02_correlation_id_reaches_the_settings_save_and_the_bd_audit_log(
    client: HttpClient,
    stores: ClusterStores,
    correlation_recorder: CorrelationRecorder,
) -> None:
    """クラスタ 02 #16 / IF-09: HTTP 入口で発番した相関 ID が settings と bd のログへ貫通する。

    期待（§6 #16）: HTTP 入口の middleware で発番した correlation_id が settings の保存ログ・
    bd 呼び出しの監査ログに載る（憲章 §6）。

    B 層（`http_contract/`）は応答ヘッダの往復しか見ていないので、ここは**ログまで**を見る
    （spec §0-7）。相関 ID は `LogRecord` に載らず整形器が `CorrelationContext.current()` を
    読むので、レコードが出た瞬間の束縛値を控える観測点を使う。

    解除の経路は board（`has_open_issues`）と settings（保存）の**両方**を通る唯一の操作なので、
    IF-09 を踏みつつ 2 つのロガを 1 度に見られる。

    #8 と同じく**2 つ目の PJ** を消す（唯一の PJ は V-25 で解除できない）。
    """
    project_key = stores.area_a.spare_project_key
    assert project_key is not None, "2 つ目の PJ が用意されていない（fixture の設定を確認する）"
    sent = "0123456789abcdef0123456789abcdef"

    response = client.delete(f"{PROJECTS_PATH}/{project_key}", headers={CORRELATION_HEADER: sent})

    assert response.status_code == 204, response.text
    assert response.headers[CORRELATION_HEADER] == sent

    board_ids = correlation_recorder.correlation_ids("board")
    settings_ids = correlation_recorder.correlation_ids("settings")
    assert board_ids, "bd の監査ログが 1 行も出ていない（board を通っていない）"
    assert settings_ids, "settings の保存ログが 1 行も出ていない"
    assert board_ids == {sent}, f"bd 監査ログの相関 ID が貫通していない: {board_ids}"
    assert settings_ids == {sent}, f"settings 保存ログの相関 ID が貫通していない: {settings_ids}"
