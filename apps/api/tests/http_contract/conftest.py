"""HTTP 契約テスト層（層 5）の fixture。マーカ登録・止め木・実配線の組み立て。

規約の正本は `specs/014-http-contracts/contracts/http-contract-test-conventions.md`
（転記が `apps/api/tests/http_contract/README.md`）。

★ **止め木**（`isolated_env_file`）が本ファイルの核である。実リポジトリの `.env` は
`BEADS_DIR`・`VAULT_ROOT`・`SETTINGS_ROOT`・`DOWNLOADS_DIR` を実値で持ち、`VAULT_ROOT` と
`DOWNLOADS_DIR` は **`/mnt/c` 配下**を指す。autouse で既定を安全側に倒さないと、`build()` を
呼ぶテストが**実運用のストアを掴む**（FR-017・FR-018・SC-013）。**この fixture を消さない・
迂回しない。**

⚠ 止め木が効くのは、各 MOD の `<Mod>Env` の**明示 `__init__(self, **overrides)`** と `build()` の
**`<Mod>Env(_env_file=env_file_path())`** が**対で**在るときだけである。どちらかを落とすと
`model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**になる（テストは緑の
まま実運用ストアを掴む）。

層 4（`contract/conftest.py`）は**共有されない**ので独立に持つ。差し替え点は層 4 の 4 点に
**`trigger.env_file_path` を足した 5 点**である（層 4 は trigger を組まないため抜けている＝ HD-10。
層 4 のファイルは変更対象外なので直さず、ここで独立に持つ）。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import pytest
from _support.contract_stubs import CallLog, StubBoardReader, StubKickoffRunner
from _support.fake_modules import CallRecorder, make_topology
from http_stores import (
    make_area_a_sparse_global_agents,
    make_area_a_two_projects,
    make_area_a_without_commands,
    make_temp_area_a,
)
from http_wiring import (
    CONFORMS_NAME,
    GatedBoardReader,
    assert_declared_pair_was_checked,
    assert_env_path_under,
    build_module_app,
    build_settings_app,
    declared_pair,
    make_orchestrator_overrides,
    recording_conforms,
)

from common.interfaces.board import BoardReader
from common.interfaces.orchestrator import KickoffRunner
from common.interfaces.vault import FolderCheck
from trigger import TriggerEnv

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType

    from fastapi import FastAPI
    from http_wiring import ConformsChecker, StatusPair

    from api.wiring import ProtocolOverrides

ENV_FILE_PATH_TARGETS = (
    "board.env_file_path",
    "vault.env_file_path",
    "settings.env_file_path",
    "trigger.env_file_path",
    "executor.models.env_file_path",
)
"""`.env` を読む 5 MOD の差し替え点（executor だけ `models` 側＝再エクスポートでは効かない）。"""

CLEARED_ENV_KEYS = (
    "BEADS_DIR",
    "BD_BIN",
    "VAULT_ROOT",
    "SETTINGS_ROOT",
    "TRIGGER_ROOT",
    "DOWNLOADS_DIR",
)
"""OS 環境変数の側から実運用ストアが漏れないよう消すキー（`.env` より優先されるため）。"""

API_KEY_ENV_PREFIX = "LLM_API_KEY"
"""実 LLM API の鍵（FR-015。前方一致で消す）。"""


def pytest_configure(config: pytest.Config) -> None:
    """`http_op` マーカを登録する（R-09・規約 §5）。

    ルート pyproject.toml の `markers` は触らない（FR-037）。
    """
    config.addinivalue_line(
        "markers",
        "http_op(method, path, status): 検証する (パス, 操作, status) 対を 1 件だけ宣言する",
    )


@pytest.fixture(autouse=True)
def isolated_env_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """実 `.env`・実運用ストア・実 LLM API へ届かない既定に倒す（FR-017・FR-018・R-08）。

    2 段構えの 1 段目である。**先に安全側へ倒し**、提供 MOD 別の fixture が 2 段目として
    一時ディレクトリへ指し直す。指し直しを忘れたテストは `build()` が「キーが無い」で
    落ちるので、実物を掴んだまま緑になることが構造的に起きない。

    Yields:
        差し替え先の空の `.env` の絶対パス。
    """
    empty_env = tmp_path_factory.mktemp("http_contract_env") / ".env"
    empty_env.write_text("", encoding="utf-8")
    for target in ENV_FILE_PATH_TARGETS:
        monkeypatch.setattr(target, lambda: empty_env)
    for key in CLEARED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key in [name for name in os.environ if name.startswith(API_KEY_ENV_PREFIX)]:
        monkeypatch.delenv(key, raising=False)
    yield empty_env


@pytest.fixture(autouse=True)
def declared_pair_is_checked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """**宣言が在るなら**、本体がその対を `assert_conforms` に通したことを見る（FR-002・FR-004）。

    被覆検査（`test_http_coverage_ledger.py` の C-1〜C-6）は「**宣言が在るか**」しか見ないので、
    宣言と本体が食い違っても緑のままになる —— 別の操作を叩いて当該の対を「覆った」と主張でき、
    被覆台帳は 100 対を数え続ける。危険が実在するのは**宣言と本体が独立した手書きの 2 か所**に
    ある形（`assert_conforms` にモジュール定数や `pytest.param` の値を直書きするテスト）である。

    規則は「**宣言が在るなら**本体が一致する対を検査していること」であって「呼んだ対は必ず宣言と
    一致する」ではない —— 後者にすると、宣言を持たない補助テストが落ちる。

    包むのは**テストモジュールの束縛**で、控え先はこの fixture の**局所変数**である
    （規約 §10-9「状態はモジュール大域に持たない」）。`monkeypatch` が包みを自動で外すので、
    テストをまたいで何も残らない —— 部分集合実行でも結果が変わらない（FR-034。HD-28 が退けた
    のは**被覆の実行時収集**であって、テストの中で閉じる自己検査ではない）。

    Yields:
        なし（本体の前に包み、後で宣言と突き合わせる）。
    """
    node = cast("pytest.Item", request.node)  # pyright: ignore[reportUnknownMemberType]  # `FixtureRequest.node` は抽象 property で戻り型注釈を持たない（pytest 8）
    declared = declared_pair(node)
    if declared is None:
        yield
        return
    module = cast("ModuleType", request.module)  # pyright: ignore[reportUnknownMemberType]  # `FixtureRequest.module` も戻り型注釈を持たない
    original: ConformsChecker | None = getattr(module, CONFORMS_NAME, None)
    assert original is not None, (
        f"{node.nodeid}: http_op を宣言しているのに {CONFORMS_NAME} を import していない"
        "（宣言した対を検査する手段が無い）"
    )
    checked: list[StatusPair] = []
    monkeypatch.setattr(module, CONFORMS_NAME, recording_conforms(original, checked))

    yield

    assert_declared_pair_was_checked(node, declared, checked)


# --------------------------------------------------------------------------------------
# 一時ディレクトリ（関数スコープ。テスト間に状態を持ち越さない＝ FR-034）
# --------------------------------------------------------------------------------------


@pytest.fixture
def area_a_root(tmp_path: Path) -> Path:
    """settings が所有する領域 A の一時ルート（`build()` の前に雛形を置く）。"""
    root = tmp_path / "area_a"
    root.mkdir()
    return root


@pytest.fixture
def trigger_root(tmp_path: Path) -> Path:
    """trigger が所有する領域 B の一時ルート（空。`build()` が雛形を生成する）。"""
    root = tmp_path / "area_b"
    root.mkdir()
    return root


@pytest.fixture
def downloads_dir(tmp_path: Path) -> Path:
    """`file` 種別トリガーの監視対象（**ext4**。実運用の Windows 側 DL に触れない）。"""
    root = tmp_path / "downloads"
    root.mkdir()
    return root


# --------------------------------------------------------------------------------------
# `/health`（提供実装 apps/api。**実 MOD を 1 つも組まない**＝外部ストアを持たない）
# --------------------------------------------------------------------------------------


@pytest.fixture
def topology_recorder() -> CallRecorder:
    """偽の登録 8 件の組み立て順を記録する（`/health` の `modules` と突き合わせる）。"""
    return CallRecorder()


@pytest.fixture
def health_topology_app(topology_recorder: CallRecorder) -> FastAPI:
    """偽の登録 **8 件**で組んだ app（plan (iii)）。

    `enabled_modules` は同じ 8 件。`registrations=` に偽の登録を渡すので実 MOD は 1 つも
    組み立たない（外部ストアに触れない）。
    """
    registrations = make_topology(topology_recorder)
    names = tuple(registration.name for registration in registrations)
    empty: ProtocolOverrides = {}
    return build_module_app(enabled_modules=names, overrides=empty, registrations=registrations)


@pytest.fixture
def health_minimal_app() -> FastAPI:
    """最小形（有効 MOD 0 件・登録 0 件）で組んだ app。"""
    empty: ProtocolOverrides = {}
    return build_module_app(enabled_modules=(), overrides=empty, registrations=())


# --------------------------------------------------------------------------------------
# orchestrator（9 契約スタブ。外部ストアを持たない）
# --------------------------------------------------------------------------------------


@pytest.fixture
def kickoff_log() -> CallLog:
    """orchestrator の 9 スタブが共有する記録。"""
    return CallLog()


@pytest.fixture
def kickoffs_app(kickoff_log: CallLog) -> FastAPI:
    """9 契約スタブで組んだ orchestrator の app（外部ストアなし・関数スコープ）。"""
    board_reader: BoardReader = StubBoardReader(kickoff_log)
    return build_module_app(
        enabled_modules=("orchestrator",),
        overrides=make_orchestrator_overrides(kickoff_log, board_reader=board_reader),
    )


@pytest.fixture
def gated_board_reader(kickoff_log: CallLog) -> GatedBoardReader:
    """`list_working` を 1 回だけ塞ぐ包み（409 用）。"""
    inner: BoardReader = StubBoardReader(kickoff_log)
    return GatedBoardReader(inner)


@pytest.fixture
def gated_kickoffs_app(kickoff_log: CallLog, gated_board_reader: GatedBoardReader) -> FastAPI:
    """1 本目の周回を `list_working` で塞げる orchestrator の app（409 用）。"""
    board_reader: BoardReader = gated_board_reader
    return build_module_app(
        enabled_modules=("orchestrator",),
        overrides=make_orchestrator_overrides(kickoff_log, board_reader=board_reader),
    )


# --------------------------------------------------------------------------------------
# trigger（領域 B の実物 ＋ `KickoffRunner` の契約準拠スタブ）
# --------------------------------------------------------------------------------------


@pytest.fixture
def trigger_log() -> CallLog:
    """trigger が呼ぶ `KickoffRunner` の記録。"""
    return CallLog()


@pytest.fixture
def trigger_app(
    trigger_log: CallLog,
    trigger_root: Path,
    downloads_dir: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """領域 B を一時ディレクトリに置いて組んだ trigger の app（plan (iii)・HD-22）。

    `build()` が `triggers.yaml` の**既定トリガー 1 件だけの雛形**を生成する（テストは雛形を
    自前で書かない）。`build()` の**前**に止め木の能動検査を通す。`DOWNLOADS_DIR` は
    **ext4 の一時ディレクトリ**（実運用の Windows 側 DL フォルダに触れない）。

    常駐は `with TestClient(app)` に入って出るだけで足りる（追加の起動制御・明示 join を
    置かない＝規約 §9）。
    """
    monkeypatch.setenv("TRIGGER_ROOT", str(trigger_root))
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads_dir))
    assert_env_path_under(TriggerEnv, "TRIGGER_ROOT", trigger_root, env_file=isolated_env_file)
    assert_env_path_under(TriggerEnv, "DOWNLOADS_DIR", downloads_dir, env_file=isolated_env_file)
    kickoff_runner: KickoffRunner = StubKickoffRunner(trigger_log)
    overrides: ProtocolOverrides = {KickoffRunner: kickoff_runner}
    return build_module_app(enabled_modules=("trigger",), overrides=overrides)


# --------------------------------------------------------------------------------------
# settings（領域 A の実物 ＋ `BoardReader`・`VaultReader` の契約準拠スタブ）
# --------------------------------------------------------------------------------------


@pytest.fixture
def settings_log() -> CallLog:
    """settings が呼ぶ 2 契約の記録。"""
    return CallLog()


@pytest.fixture
def settings_app(
    settings_log: CallLog,
    area_a_root: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """既定の領域 A で組んだ settings の app（**関数スコープ**＝ 1 ケース 1 app）。"""
    make_temp_area_a(area_a_root)
    return build_settings_app(
        log=settings_log, root=area_a_root, env_file=isolated_env_file, monkeypatch=monkeypatch
    )


@pytest.fixture
def settings_app_without_commands(
    settings_log: CallLog,
    area_a_root: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """コマンド定義が無い変種（`putPolicy` 200 用。V-25 を外す）。"""
    make_area_a_without_commands(area_a_root)
    return build_settings_app(
        log=settings_log, root=area_a_root, env_file=isolated_env_file, monkeypatch=monkeypatch
    )


@pytest.fixture
def settings_app_sparse_global_agents(
    settings_log: CallLog,
    area_a_root: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """`global/agents.yaml` に 1 ペルソナだけの変種（`getGlobalAgent` 404 用）。"""
    make_area_a_sparse_global_agents(area_a_root)
    return build_settings_app(
        log=settings_log, root=area_a_root, env_file=isolated_env_file, monkeypatch=monkeypatch
    )


@pytest.fixture
def settings_app_two_projects(
    settings_log: CallLog,
    area_a_root: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """PJ が 2 件の変種（`deleteProject` 204 用）。"""
    make_area_a_two_projects(area_a_root)
    return build_settings_app(
        log=settings_log, root=area_a_root, env_file=isolated_env_file, monkeypatch=monkeypatch
    )


@pytest.fixture
def settings_app_with_open_issues(
    settings_log: CallLog,
    area_a_root: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """盤面に未完了が在る変種（`deleteProject` 409 用）。"""
    make_area_a_two_projects(area_a_root)
    return build_settings_app(
        log=settings_log,
        root=area_a_root,
        env_file=isolated_env_file,
        monkeypatch=monkeypatch,
        has_open=True,
    )


@pytest.fixture
def settings_app_folder_missing(
    settings_log: CallLog,
    area_a_root: Path,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """保管庫のフォルダが実在しない変種（`createProject` 422 の `folder_missing` 用）。"""
    make_temp_area_a(area_a_root)
    return build_settings_app(
        log=settings_log,
        root=area_a_root,
        env_file=isolated_env_file,
        monkeypatch=monkeypatch,
        folder_check=FolderCheck(ok=False, missing=["新PJ/roles/programmer"]),
    )
