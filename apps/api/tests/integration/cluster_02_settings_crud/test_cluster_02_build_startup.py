"""クラスタ 02 の起動時検査・止め木・配線の過不足（**IF に紐づかない観点行**）。§6 #13・#14。

起動そのものの成否を見る観点は、正常系と同じ土台に同居できない（クラスタ 02 §5 が明記）ので
**専用の fixture** を使う。

`CONTRACT_META = True` の理由は `test_cluster_02_local_gates.py` と同じ（契約 IF を 1 本も
通らない）。**このファイルに `contract_if` を書かない**。

FR-032（配線の過不足が起動時に落ちること）と **SC-005（止め木 4 枚の実効性）** の検査もここに
置く —— どちらも対応する §6 の行が存在しないため `02#13`（起動の成否）に対応させた
（判断は design-sync に記録する）。

止め木の検査は **`stopper` を名前に含む**（quickstart §7 の
`pytest apps/api/tests/integration/cluster_02_settings_crud -k stopper -v` が選べるようにする
＝ T087）。それ以前は同コマンドが **0 selected・終了コード 5** で何も検査していなかった。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _support.cluster_env import (
    API_KEY_ENV_PREFIX,
    CLEARED_ENV_KEYS,
    ENV_FILE_PATH_TARGETS,
    assert_env_path_under,
)
from _support.cluster_stores import ClusterStores, assert_outside_repo
from _support.cluster_wiring import build_cluster_client, build_cluster_wiring, cluster_modules
from _support.composition import make_root_config
from _support.contract_stubs import CallLog, StubRunMonitor

from api.exceptions import ExtraneousOverrideError, UnsatisfiedContractError
from api.wiring import build_modules, require
from common.interfaces.board import BoardReader
from common.interfaces.monitor import RunMonitor
from common.kernel.config import find_repo_root
from settings import SettingsEnv
from settings.exceptions import SettingsAreaError

if TYPE_CHECKING:
    from _support.composition import HttpClient

CONTRACT_META = True
"""IF に紐づかない観点行を収めるファイル（`contract_if` を書いてはならない＝規約 §7）。"""

pytestmark = pytest.mark.requires_bd

PROJECTS_PATH = "/settings/projects"

OUTSIDE_TMP = "outside_tmp"
"""3 枚目の止め木を**わざと破って**みせる先（`tmp_path` の配下ではない別の接頭辞）。"""


@pytest.mark.cluster_case("02#13")
def test_cluster_02_missing_settings_root_scaffolds_and_starts(missing_root_env: Path) -> None:
    """クラスタ 02 #13: 領域 A が空でも `global/` の雛形を生成して**起動が成立する**。

    期待（§6 #13）: `global/` の雛形を生成して起動（settings-F23）。

    C 層（`wiring_contract/`）は「雛形が領域 A の外に出ないこと」しか見ていない（§0-8）ので、
    ここは**起動が成立して口が使えること**まで見る。雛形が `SETTINGS_ROOT` の**内側**に
    生成されることも確かめる（外に書いたら止め木の意味が無い）。
    """
    assert not any(missing_root_env.iterdir()), "前提: 領域 A は空である"

    with build_cluster_client("02", {}) as http:
        listed = http.get(PROJECTS_PATH)

    assert listed.status_code == 200, listed.text
    assert listed.json() == [], "空の領域 A なのに登録が読めている"
    scaffolded = sorted(path.name for path in (missing_root_env / "global").iterdir())
    assert scaffolded, "`global/` の雛形が 1 件も生成されていない"
    assert (missing_root_env / "global/model.conf").is_file(), (
        f"必須の雛形が無い: 生成されたのは {scaffolded}"
    )


@pytest.mark.cluster_case("02#14")
def test_cluster_02_broken_area_aborts_the_startup(broken_area_env: Path) -> None:
    """クラスタ 02 #14: 領域 A に構文誤りがあれば**起動中断**（黙って既定にしない）。

    期待（§6 #14）: 起動中断（例外。黙って既定にしない＝憲章 §5）。

    `SettingsAreaError` は**見に行った絶対パス**を `issues` に載せる（FR-062。本文の側は
    「一覧を参照」で、パスは属性が持つ）。原因のファイルが名指しされることまで見る ——
    「起動しなかった」だけでは、どのファイルが悪いか分からない。
    """
    with pytest.raises(SettingsAreaError) as caught:
        build_cluster_client("02", {})

    issues = caught.value.issues
    assert issues, f"読取段の失敗が 1 件も載っていない: {caught.value}"
    target = str(broken_area_env / "global/agents.yaml")
    assert any(target in issue for issue in issues), issues


@pytest.mark.cluster_case("02#13")
def test_cluster_02_wiring_rejects_an_override_nobody_requires(stores: ClusterStores) -> None:
    """FR-032: 誰も要求しない契約への override は**テスト本体が 1 行も走る前に**落ちる（SC-004）。

    対応する §6 の行が無いので `02#13`（起動の成否）に対応させた（判断は design-sync に記録）。

    02 の override は **0 件**が正しい（`board`・`vault`・`settings` の 3 MOD で契約が閉じる）。
    余らせた鍵は `ExtraneousOverrideError` になり、**実物の `build()` は 1 度も呼ばれない**
    （検査は組み立ての**前**の段で行われる）。正しい組み合わせなら組めることも併せて見る ——
    さもないと「何を渡しても落ちる」形でも緑になる。

    `stores` を要求するのは、止め木を通し一時ストアを指した状態で組むためである。
    """
    assert stores.area_a.root.is_dir(), "前提: 一時領域 A が用意されている"

    with pytest.raises(ExtraneousOverrideError) as caught:
        build_cluster_wiring("02", {RunMonitor: StubRunMonitor(CallLog())})
    assert "RunMonitor" in str(caught.value)

    wiring = build_cluster_wiring("02", {})
    board_reader: BoardReader = require(wiring.provided, BoardReader)
    assert board_reader is not None
    assert wiring.modules == ("board", "vault", "settings"), wiring.modules


@pytest.mark.cluster_case("02#13")
def test_cluster_02_wiring_reports_the_missing_provider_when_a_module_is_dropped(
    stores: ClusterStores,
) -> None:
    """FR-032（続き）: 有効 MOD を 1 件抜くと供給元の無い契約として落ちる。

    `enabled_modules` から `board` を外すと settings が要求する `BoardReader` の供給元が無くなる。
    **組み立ての途中ではなく検査の段**で落ちるので、実物の `build()` は 1 度も呼ばれない。
    失敗の本文には**契約名と要求元**の両方が載る（追加調査なしに原因が分かる）。
    """
    assert stores.area_a.root.is_dir(), "前提: 一時領域 A が用意されている"
    reduced = ("vault", "settings")

    with pytest.raises(UnsatisfiedContractError) as caught:
        build_modules(
            enabled_modules=reduced,
            overrides={},
            config=make_root_config(enabled_modules=reduced),
        )

    message = str(caught.value)
    assert "BoardReader" in message, message
    assert "settings" in message, message


@pytest.mark.cluster_case("02#13")
def test_cluster_02_stoppers_keep_the_real_env_and_production_stores_out_of_reach(
    tmp_path: Path,
    cluster_env: Path,
    stores: ClusterStores,
) -> None:
    """SC-005: 止め木 4 枚が実際に効いており、**破れば止まる**ことまで確かめる。

    quickstart §7 が名指しする 4 枚を、**1 枚ずつ**見る（対応する §6 の行が無いので `02#13` に
    対応させた。判断は design-sync に記録）。

    1. **`env_file_path` の差し替え**: `.env` を読む 5 つの口が 1 つ残らず一時 `.env` を指す。
       一覧は `_support/cluster_env.py` の `ENV_FILE_PATH_TARGETS` から引く（テストへ写しを
       作らない）ので、差し替え先が増えたら**この検査も自動で広がる**。
    2. **環境変数の `delenv`**: `CLEARED_ENV_KEYS` は「消えている」か「一時ディレクトリ配下を
       指している」かのどちらかであり、実 LLM の鍵（`LLM_API_KEY*`）は 1 件も残っていない。
    3. **`build()` 前の能動検査**: `assert_env_path_under` に**違う接頭辞**を渡すと失敗する。
       これが通ってしまう実装では、実運用ストアを掴んだままでも緑になる。
    4. **リポジトリ外の確認**: `assert_outside_repo` にリポジトリ**内**のパスを渡すと失敗する
       （`bd init` は git リポジトリを作り commit まで行うので、内側で走らせてはならない）。

    3・4 は「破ってみせる」形にしてある —— 止め木そのものが空振りしていたら、1・2 がいくら
    緑でも意味が無い（`014` の「空 vs 空の偽の緑」と同じ轍）。
    """
    repo_env = find_repo_root() / ".env"
    assert cluster_env != repo_env, f"実運用の `.env` を指したままである: {cluster_env}"
    assert cluster_env.resolve().is_relative_to(tmp_path.resolve()), cluster_env

    # 1 枚目 —— `.env` を読む口が 1 つ残らず一時 `.env` を指す。
    for target in ENV_FILE_PATH_TARGETS:
        module_name, _, attribute = target.rpartition(".")
        resolver = getattr(importlib.import_module(module_name), attribute)
        assert resolver() == cluster_env, (
            f"`{target}` が差し替わっていない（実 `.env` を読む）: {resolver()}"
        )

    # 2 枚目 —— 危険な環境変数が消えているか、一時ディレクトリ配下を指している。
    for key in CLEARED_ENV_KEYS:
        value = os.environ.get(key)
        if value is None:
            continue
        assert Path(value).resolve().is_relative_to(tmp_path.resolve()), (
            f"`{key}` が一時ディレクトリの外を指している: {value}"
        )
    leaked = sorted(key for key in os.environ if key.startswith(API_KEY_ENV_PREFIX))
    assert not leaked, f"実 LLM の鍵が環境に残っている: {leaked}"

    # 3 枚目 —— 能動検査は**違う接頭辞**を渡せば必ず落ちる（空振りでない）。
    assert stores.area_a.root.is_dir(), "前提: 一時領域 A が用意されている"
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)
    with pytest.raises(pytest.fail.Exception):
        assert_env_path_under(
            SettingsEnv, "SETTINGS_ROOT", tmp_path / OUTSIDE_TMP, env_file=cluster_env
        )

    # 4 枚目 —— リポジトリ内のパスは弾かれる（`bd init` の前に必ず通る検査）。
    assert_outside_repo(tmp_path)
    with pytest.raises(AssertionError):
        assert_outside_repo(find_repo_root() / OUTSIDE_TMP)


@pytest.mark.cluster_case("02#13")
def test_cluster_02_health_reports_the_cluster_modules(client: HttpClient) -> None:
    """US3/AC1: 組み上げ結果を `GET /health` の `modules` で観測する（T094）。

    受け入れシナリオ 1 の観測面は「`/health` の `modules` にそのクラスタの MOD だけが並ぶ」で
    ある。直接ドライブ側の `wiring.modules` 等値検査（上の FR-032 の 2 本）とは
    **別インスタンス**なので、HTTP の口からも同じ MOD 集合が見えることを確かめる。対応する
    §6 の行が無いので `02#13`（起動の成否。FR-032 と同じ寄せ方）に対応させた（判断は
    design-sync に記録）。

    期待値は `_support/cluster_wiring.py` の `cluster_modules("02")` から引く（テストへ写しを
    作らない。横断検査が割当 csv との一致を守っている）。並びは依存順（C 層の領分）なので
    **整列して**比べる（集合ではなく整列比較にするのは重複も検知するため）。
    """
    response = client.get("/health")

    assert response.status_code == 200, response.text
    modules = response.json()["modules"]
    assert sorted(modules) == sorted(cluster_modules("02")), modules
