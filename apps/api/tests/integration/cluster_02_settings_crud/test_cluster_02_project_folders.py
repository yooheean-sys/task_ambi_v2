"""クラスタ 02 の PJ 登録・編集（IF-32＝`VaultReader.check_project_folders`）。§6 #2・#3・#4。

**実 settings × 実 vault**（ダブル 0）。`check_project_folders` の呼び出し点は
`settings/service/projects.py` の登録・編集経路**だけ**である（R-08。`validate/forward.py` の
同名関数は settings 内部の順方向 validate で、vault の Protocol を通らない）。

止め木は conftest の autouse が通す。盤面（beads）はここでは使わないが、settings の `build()`
が `BoardReader` を要求するので board も実物で組む（有効 MOD 一覧が 3 件）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_stores import VAULT_INPUT_NAME, ClusterStores

from common.kernel.enums import PersonaKey

if TYPE_CHECKING:
    from _support.composition import HttpClient

pytestmark = pytest.mark.requires_bd

PROJECTS_PATH = "/settings/projects"
GENERATION_HEADER = "X-Settings-Generation"


def _spec(
    project_key: str, *, pj_input_dir: str, outcome_dir: str, role_dir: str
) -> dict[str, object]:
    """`POST`／`PUT /settings/projects` の本文（`ProjectSpec`）を組む。"""
    return {
        "project_key": project_key,
        "name": "クラスタ 02 のプロジェクト",
        "folders": {
            "pj_input_dir": pj_input_dir,
            "role_dirs": {PersonaKey.programmer.value: role_dir},
            "outcome_dir": outcome_dir,
        },
    }


@pytest.mark.contract_if("IF-32")
@pytest.mark.cluster_case("02#2")
def test_cluster_02_register_with_missing_folders_is_rejected_without_touching_beads(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #2 / IF-32: 実 vault が「不在」と答えた宣言は登録できない。

    期待（§6 #2）: **422**・不在パスの一覧が `problems` に載る・**beads には書かない**
    （D07-g）。不在の件数だけ違反が積まれる（`service/projects.py` の `_check_folders`）。

    盤面を触っていないことは、登録の前後で `project:<新キー>` の課題が 0 件のままであることでは
    なく「settings が board の書込口を持たない」ことで担保される —— 02 の board 依存は
    `BoardReader` の 1 本だけであり、`has_open_issues`（IF-09）は削除経路にしか無い（R-08）。
    ここでは**盤面に書かれるはずのものが無い**ことを、`bd` の実ワークスペースに `.beads` 以外の
    変更が起きないことで見る代わりに、応答が 422 で世代ヘッダを持たないことで見る。
    """
    body = _spec(
        "newpj",
        pj_input_dir="does/not/exist",
        outcome_dir="also/missing",
        role_dir="missing/role",
    )

    response = client.post(PROJECTS_PATH, json=body)

    assert response.status_code == 422, response.text
    problems = response.json()["problems"]
    reasons = [item["reason"] for item in problems]
    assert problems, "不在パスの一覧が空（IF-32 の戻りが捨てられている）"
    assert all(item["kind"] == "folder_missing" for item in problems), problems
    for missing in ("does/not/exist", "also/missing", "missing/role"):
        assert any(missing in reason for reason in reasons), (
            f"不在パス {missing!r} が problems に載っていない: {reasons}"
        )
    assert GENERATION_HEADER not in response.headers, (
        "422 なのに世代ヘッダが載っている（何かを書いた疑い）"
    )
    listed = client.get(PROJECTS_PATH).json()
    assert not [item for item in listed if item["project_key"] == "newpj"], (
        "422 のはずの登録が領域 A に残っている"
    )


@pytest.mark.contract_if("IF-32")
@pytest.mark.cluster_case("02#3")
def test_cluster_02_register_with_existing_folders_is_written_and_readable(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #3 / IF-32: 実在する宣言は `project.yaml` として書かれ GET で読める。

    期待（§6 #3）: `projects/<key>/project.yaml` が一時ファイル → `os.replace` で書かれ、
    スナップショット世代が **＋1**、GET で読める。

    実 vault が「すべて在る」と答える宣言を渡す（`make_temp_vault` が作った 3 領域）。
    """
    before = int(client.get(PROJECTS_PATH).headers.get(GENERATION_HEADER, "0"))
    body = _spec(
        "newpj",
        pj_input_dir="pj",
        outcome_dir="pj/outcome",
        role_dir=f"pj/roles/{PersonaKey.programmer.value}",
    )

    response = client.post(PROJECTS_PATH, json=body)

    assert response.status_code == 201, response.text
    generation = int(response.headers[GENERATION_HEADER])
    assert generation > before, f"世代が増えていない: 前={before} 後={generation}"

    written = stores.area_a.root / "projects/newpj/project.yaml"
    assert written.is_file(), "`project.yaml` が領域 A に書かれていない"
    assert "pj/outcome" in written.read_text(encoding="utf-8")

    fetched = client.get(f"{PROJECTS_PATH}/newpj")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["folders"]["outcome_dir"] == "pj/outcome"


@pytest.mark.contract_if("IF-32")
@pytest.mark.cluster_case("02#4")
def test_cluster_02_update_with_missing_new_folder_leaves_the_existing_declaration(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #4 / IF-32: 編集で新パスが不在なら 422 で**既存は不変**。

    期待（§6 #4）: 422（既存は不変）。登録経路と編集経路の**両方**に `check_project_folders` が
    在ることを、同じ IF の別の呼び出し点として踏む。

    既存の宣言（`make_temp_area_a` が置いたもの）が書き換わっていないことまで見る ——
    「422 を返したが領域 A は先に書いていた」を見逃さないため。
    """
    project_key = stores.project_key
    original = (stores.area_a.root / f"projects/{project_key}/project.yaml").read_text(
        encoding="utf-8"
    )

    response = client.put(
        f"{PROJECTS_PATH}/{project_key}",
        json=_spec(
            project_key,
            pj_input_dir="pj/gone",
            outcome_dir="pj/outcome",
            role_dir=f"pj/roles/{PersonaKey.programmer.value}",
        ),
    )

    assert response.status_code == 422, response.text
    problems = response.json()["problems"]
    assert any("pj/gone" in item["reason"] for item in problems), problems
    after = (stores.area_a.root / f"projects/{project_key}/project.yaml").read_text(
        encoding="utf-8"
    )
    assert after == original, "422 のはずの編集で `project.yaml` が書き換わっている"
    assert (stores.vault.root / "pj" / VAULT_INPUT_NAME).is_file(), (
        "実 vault の中身が編集で壊れている（読取だけのはず）"
    )
