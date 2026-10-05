"""クラスタ 02 の settings 内部ゲート（**IF に紐づかない観点行**）。§6 #1・#7・#9〜#12・#15。

`CONTRACT_META = True` を置く理由（plan (v)・B-11）: これらの行は settings の内部経路・
起動時検査・スナップショットであり、**モジュール間の契約 IF を 1 本も通らない**（R-08 の実測 ——
`has_open_issues` は削除経路、`check_project_folders` は登録・編集経路にしか無い）。
`contract_ledger.collect_declarations` の走査から外すことで「非 META ファイルのテストは
ちょうど 1 件宣言する」という不変条件が保てる。**このファイルに `contract_if` を書かない**。

#11（メンバのペルソナ全員分の `role_dir` 不足）は **IF-32 ではない** —— P5D-018 は settings
内部の順方向 validate（V-17）であり、vault の Protocol を通らない（R-08）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_stores import ClusterStores

from common.kernel.enums import PersonaKey

if TYPE_CHECKING:
    from _support.composition import HttpClient

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

pytestmark = pytest.mark.requires_bd

PROJECTS_PATH = "/settings/projects"
HUMANS_PATH = "/settings/humans"
GENERATION_HEADER = "X-Settings-Generation"
RESERVED_KEY = "_program"


def _spec(project_key: str, *, role_dirs: dict[str, str] | None = None) -> dict[str, object]:
    """`POST`／`PUT /settings/projects` の本文（既定は実在する 3 領域を指す）。"""
    return {
        "project_key": project_key,
        "name": "クラスタ 02 のプロジェクト",
        "folders": {
            "pj_input_dir": "pj",
            "role_dirs": (
                role_dirs
                if role_dirs is not None
                else {PersonaKey.programmer.value: f"pj/roles/{PersonaKey.programmer.value}"}
            ),
            "outcome_dir": "pj/outcome",
        },
    }


@pytest.mark.cluster_case("02#1")
def test_cluster_02_key_rule_violations_are_rejected_without_writing(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #1: キー規則違反・重複キーは **422** で、領域 A に**何も書かない**（P1 ゲート）。

    期待（§6 #1）: 422・何も書かない。キー規則は**新規キーにだけ**当たる（既存キーへ遡らない）。

    重複（既に登録済みのキー）と禁止文字の 2 通りを踏む。どちらも `kind="key_rule"` である。
    """
    duplicate = client.post(PROJECTS_PATH, json=_spec(stores.project_key))
    assert duplicate.status_code == 422, duplicate.text
    assert any(item["kind"] == "key_rule" for item in duplicate.json()["problems"])

    before = sorted(path.name for path in (stores.area_a.root / "projects").iterdir())
    forbidden = client.post(PROJECTS_PATH, json=_spec("bad/key"))
    assert forbidden.status_code == 422, forbidden.text
    assert any(item["kind"] == "key_rule" for item in forbidden.json()["problems"])

    after = sorted(path.name for path in (stores.area_a.root / "projects").iterdir())
    assert after == before, f"422 のはずの登録で領域 A が変わっている: {before} → {after}"


@pytest.mark.cluster_case("02#7")
def test_cluster_02_reserved_project_key_cannot_be_deleted(
    reserved_client: HttpClient,
) -> None:
    """クラスタ 02 #7: 予約キー `_program` は解除できない。

    期待（§6 #7）: 削除不可。**現物は 409**（`ProjectDeletionBlocked`）であり、§6 の期待列が
    書く「422」とは食い違う —— `010` SD-03 の裁定（解除の拒否は 409 に寄せる）が正であり、
    設計文書側が追随していない（design-sync の B 表に記録する）。

    `_program` を領域 A に**登録しておく**必要がある —— 未登録だと `_require_project` が先に
    当たって 404 になり、予約キーの分岐に届かない。
    """
    response = reserved_client.delete(f"{PROJECTS_PATH}/{RESERVED_KEY}")

    assert response.status_code == 409, response.text
    assert "予約" in response.json()["detail"]


@pytest.mark.cluster_case("02#9")
def test_cluster_02_validation_failure_writes_nothing(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #9: UC-07 保存の検査違反は `problems[]` つきの **422** で、何も書かない。

    期待（§6 #9）: `SettingsValidationError(problems[])` → 422・**何も書かない**。違反は
    **全件**載る（最初の 1 件で打ち切らない）。

    ここでは `policy.csv` の保存に、台帳に無い Tool 名を含む行を渡す（V-24 系）。
    """
    project_key = stores.project_key
    policy_path = f"/settings/policies/{project_key}"
    original = (stores.area_a.root / f"projects/{project_key}/policy.csv").read_text(
        encoding="utf-8"
    )

    response = client.put(
        policy_path,
        json={
            "rows": [
                {
                    "ptype": "p",
                    "fields": [
                        f"member:{project_key}.{stores.persona}",
                        "tool:no_such_tool",
                        "execute",
                    ],
                }
            ]
        },
    )

    assert response.status_code == 422, response.text
    problems = response.json()["problems"]
    assert problems, "違反の一覧が空（`problems[]` が落ちている）"
    after = (stores.area_a.root / f"projects/{project_key}/policy.csv").read_text(encoding="utf-8")
    assert after == original, "422 のはずの保存で `policy.csv` が書き換わっている"


@pytest.mark.cluster_case("02#10")
def test_cluster_02_valid_save_writes_and_bumps_the_generation(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #10: 検査を通った保存は領域 A へ書かれ、世代が **＋1** になる。

    期待（§6 #10）: 領域 A 書込 → 台帳再導出 → Enforcer 再構築 → 世代＋1 のスナップショット
    差替え。02 は HTTP だけで観測するので、**書かれた実ファイル**と**世代ヘッダ**と
    **読み直しの一致**の 3 点で見る（`Authorizer` の再構築は 03 が実物で踏む）。
    """
    project_key = stores.project_key
    policy_path = f"/settings/policies/{project_key}"
    before = int(client.get(policy_path).headers.get(GENERATION_HEADER, "0"))
    subject = f"member:{project_key}.{stores.persona}"
    rows: list[dict[str, object]] = [
        {"ptype": "p", "fields": [subject, f"tool:{tool}", "execute"]}
        for tool in stores.area_a.allowed_tools
    ]
    rows.append({"ptype": "p", "fields": [subject, f"cmd:{stores.area_a.command_name}", "execute"]})

    response = client.put(policy_path, json={"rows": rows})

    assert response.status_code == 200, response.text
    generation = int(response.headers[GENERATION_HEADER])
    assert generation > before, f"世代が増えていない: 前={before} 後={generation}"
    written = (stores.area_a.root / f"projects/{project_key}/policy.csv").read_text(
        encoding="utf-8"
    )
    assert f"cmd:{stores.area_a.command_name}" in written
    assert client.get(policy_path).status_code == 200


@pytest.mark.cluster_case("02#11")
def test_cluster_02_member_without_a_role_dir_is_rejected(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #11: メンバのペルソナぶんの `role_dir` が無ければ **422**（P5D-018）。

    期待（§6 #11）: 422。**これは IF-32 ではない** —— `VaultReader.check_project_folders` は
    「宣言したフォルダが実在するか」しか見ず、「メンバ全員分の宣言が在るか」は settings 内部の
    順方向 validate（V-17）が見る（R-08）。したがって本テストは `contract_if` を持たない。

    `role_dirs` から当該ペルソナを落とす。落とした先（`pj/outcome`）は実在するので IF-32 は
    通り、V-17 だけが落ちる形にする。
    """
    project_key = stores.project_key
    response = client.put(
        f"{PROJECTS_PATH}/{project_key}",
        json=_spec(project_key, role_dirs={PersonaKey.writer.value: "pj/roles/writer"}),
    )

    assert response.status_code == 422, response.text
    reasons = [item["reason"] for item in response.json()["problems"]]
    assert any("role_dirs" in reason for reason in reasons), reasons
    assert not any("保管庫に無いフォルダ" in reason for reason in reasons), (
        f"IF-32（フォルダ実在）で落ちている —— V-17 を踏めていない: {reasons}"
    )


@pytest.mark.cluster_case("02#12")
def test_cluster_02_human_actors_can_be_read_and_written(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #12: `/settings/humans`（人間の bd actor 宣言）が読み書きできる。

    期待（§6 #12）: CRUD できる（P5D-019。F21 の列挙漏れを openapi で補った D12 §4）。
    """
    current = client.get(HUMANS_PATH)
    assert current.status_code == 200, current.text
    assert stores.area_a.human_actor in current.json()["human_actors"]

    added = "human_reviewer"
    response = client.put(HUMANS_PATH, json={"human_actors": [stores.area_a.human_actor, added]})

    assert response.status_code == 200, response.text
    assert GENERATION_HEADER in response.headers
    assert added in client.get(HUMANS_PATH).json()["human_actors"]
    assert added in (stores.area_a.root / "global/humans.yaml").read_text(encoding="utf-8")


@pytest.mark.cluster_case("02#15")
def test_cluster_02_reads_come_from_the_snapshot_not_the_files(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #15: 実行時の読取はスナップショット —— 保存後に領域 A を壊しても GET は壊れない。

    期待（§6 #15）: 保存後に領域 A のファイルを壊しても GET（一覧）は壊れない（P5D-016。
    **次の保存で** validate に落ちる）。

    起動時に読み切った構造を保持していることの観測点である。ファイルを毎回読み直す実装なら
    ここで 500 になる。
    """
    before = client.get(PROJECTS_PATH).json()
    assert before, "一覧が空（前提が崩れている）"

    (stores.area_a.root / "global/agents.yaml").write_text(
        "programmer:\n  goal: [unclosed\n", encoding="utf-8"
    )

    after = client.get(PROJECTS_PATH)
    assert after.status_code == 200, after.text
    assert after.json() == before, "壊したファイルが実行時の読取に影響している"
