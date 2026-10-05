"""クラスタ 02 の PJ リポジトリ対応付け（CFG-74・D24）。§6 #17・#18。

`repos`（`remote_url`／`local_path`）は**任意**の宣言で、形式のみの緩い検査（実在・
git アクセス可否は見ない）を通れば登録・編集・解除できる。値は orchestrator が
`ContextRefs` 経由で stages の段テンプレート（`{repository_remote_url}`・
`{repository_local_path}`）へ充填する材料になる —— 本クラスタが見るのはその**入口**
（HTTP → 領域 A → スナップショット → GET）で、注入の出口側はクラスタ 04。

`CONTRACT_META = True` を置く理由は `test_cluster_02_local_gates.py` と同じ: `repos` の
形式検査は settings 内部の保存経路ゲート（S-9）であり、モジュール間の契約 IF を
**1 本も通らない**（フォルダ存在確認＝IF-32 は `folders` の話で、`repos` の対象外）。
**このファイルに `contract_if` を書かない**。
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
GENERATION_HEADER = "X-Settings-Generation"
HTTPS_URL = "https://gitlab.example.com/group/repo.git"
SCP_LIKE_URL = "git@gitlab.example.com:group/repo.git"
LOCAL_DIR = "/home/demo/work/repo"


def _spec(project_key: str, *, repos: dict[str, str] | None = None) -> dict[str, object]:
    """`POST`／`PUT /settings/projects` の本文（既定は実在する 3 領域を指す）。"""
    body: dict[str, object] = {
        "project_key": project_key,
        "name": "クラスタ 02 のプロジェクト",
        "folders": {
            "pj_input_dir": "pj",
            "role_dirs": {PersonaKey.programmer.value: f"pj/roles/{PersonaKey.programmer.value}"},
            "outcome_dir": "pj/outcome",
        },
    }
    if repos is not None:
        body["repos"] = repos
    return body


@pytest.mark.cluster_case("02#17")
def test_cluster_02_repos_round_trip_register_update_unregister(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #17: `repos` 付き登録 → GET → 編集で更新 → 解除で `repos` キー消滅。

    期待（§6 #17）: `repos` 付き登録 → GET で読める → 編集で更新 → 解除で `repos` キー消滅。
    未登録ならキー自体を書かない（CFG-74・D24）。
    """
    # 登録（`repos` 両方あり）
    response = client.post(
        PROJECTS_PATH,
        json=_spec("pjrepos", repos={"remote_url": HTTPS_URL, "local_path": LOCAL_DIR}),
    )
    assert response.status_code == 201, response.text

    written = stores.area_a.root / "projects/pjrepos/project.yaml"
    assert written.is_file(), "`project.yaml` が領域 A に書かれていない"
    text = written.read_text(encoding="utf-8")
    assert "repos:" in text and HTTPS_URL in text and LOCAL_DIR in text, text

    fetched = client.get(f"{PROJECTS_PATH}/pjrepos")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["repos"] == {"remote_url": HTTPS_URL, "local_path": LOCAL_DIR}

    # 編集で更新（URL を SCP ライクに・パスは据え置き）
    updated = client.put(
        f"{PROJECTS_PATH}/pjrepos",
        json=_spec("pjrepos", repos={"remote_url": SCP_LIKE_URL, "local_path": LOCAL_DIR}),
    )
    assert updated.status_code == 200, updated.text
    assert client.get(f"{PROJECTS_PATH}/pjrepos").json()["repos"] == {
        "remote_url": SCP_LIKE_URL,
        "local_path": LOCAL_DIR,
    }

    # 編集で解除（`repos` を渡さない）→ 領域 A から `repos` キー自体が消える
    cleared = client.put(f"{PROJECTS_PATH}/pjrepos", json=_spec("pjrepos"))
    assert cleared.status_code == 200, cleared.text
    text_after = (stores.area_a.root / "projects/pjrepos/project.yaml").read_text(encoding="utf-8")
    assert "repos:" not in text_after, f"解除したはずの `repos` キーが残っている:\n{text_after}"
    assert client.get(f"{PROJECTS_PATH}/pjrepos").json()["repos"] is None


@pytest.mark.cluster_case("02#18")
def test_cluster_02_repos_format_violations_are_rejected_without_writing(
    client: HttpClient, stores: ClusterStores
) -> None:
    """クラスタ 02 #18: `repos` の形式違反は **422** で、領域 A に**何も書かない**（P1 ゲート）。

    期待（§6 #18）: 形式のみの緩い検査（URL・絶対パスらしさ。実在・git アクセス可否は
    見ない＝D24）に落ちたら 422・何も書かない。2 件の違反が**まとめて** `problems` に載る
    （`service/projects.py` の `_check_repos`）。
    """
    before = sorted(path.name for path in (stores.area_a.root / "projects").iterdir())

    response = client.post(
        PROJECTS_PATH,
        json=_spec(
            "pjrepos",
            repos={"remote_url": "not-a-url", "local_path": "relative/path"},
        ),
    )

    assert response.status_code == 422, response.text
    problems = response.json()["problems"]
    assert all(item["kind"] == "format" for item in problems), problems
    wheres = sorted(item["where"] for item in problems)
    assert wheres == ["projects:repos.local_path", "projects:repos.remote_url"], problems
    assert GENERATION_HEADER not in response.headers, (
        "422 なのに世代ヘッダが載っている（何かを書いた疑い）"
    )
    after = sorted(path.name for path in (stores.area_a.root / "projects").iterdir())
    assert after == before, f"422 のはずの登録で領域 A が変わっている: {before} → {after}"
