"""実配線の組み立てヘルパ（fixture ではない部品）。

`conftest.py` は**fixture の登録点**に徹し、配線の関数・止め木の能動検査・後始末・
`BoardReader` の包み・**宣言と本体の突合**の部品はここに置く（規約 §11 の「1 ファイル 400 行を
超えたら観点で割る」）。

依存は一方向 —— `conftest.py` と各テストがここを import する。ここから `conftest.py` は
import しない。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from _support.composition import build_test_app, make_root_config
from _support.contract_stubs import (
    CallLog,
    StubBoardReader,
    StubBoardWriter,
    StubCrewExecutor,
    StubMemberResolver,
    StubProposalGate,
    StubRepoReader,
    StubRunMonitor,
    StubSettingsReader,
    StubStageCatalog,
    StubVaultReader,
)
from starlette.testclient import TestClient

from common.interfaces.board import BoardReader, BoardWriter
from common.interfaces.executor import CrewExecutor
from common.interfaces.monitor import RunMonitor
from common.interfaces.repo import RepoReader
from common.interfaces.settings import MemberResolver, SettingsReader
from common.interfaces.stages import ProposalGate, StageCatalog
from common.interfaces.vault import FolderCheck, VaultReader
from settings import SettingsEnv

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import httpx
    from _support.composition import HttpClient
    from _support.openapi_source import StatusPair
    from fastapi import FastAPI
    from pydantic_settings import BaseSettings

    from api.wiring import ModuleRegistration, ProtocolOverrides
    from common.interfaces.board import ClaimFilter, Comment, Issue

    ConformsChecker = Callable[..., None]
    """`http_asserts.assert_conforms` と同じ面（包みの型）。"""


def assert_env_path_under(
    env_cls: type[BaseSettings],
    field: str,
    tmp_path: Path,
    *,
    env_file: Path,
) -> Path:
    """`build()` を呼ぶ**前**に、その MOD が読む解決先が `tmp_path` 配下かを確かめる（FR-017）。

    `build()` そのものを検査に使わない —— settings の `build()` は不在時に雛形を**書き込む**
    ため、事後確認では手遅れになる（R-03）。読み方は `build()` と**同じ経路**
    （`<Mod>Env(_env_file=env_file_path())`）にする。

    Args:
        env_cls: その MOD の `<Mod>Env`（`SettingsEnv`・`TriggerEnv`）。
        field: 解決先を持つフィールド名（`SETTINGS_ROOT`・`TRIGGER_ROOT`）。
        tmp_path: 配下であるべき一時ディレクトリ。
        env_file: `build()` が読む `.env` の絶対パス（`env_file_path()` の戻り）。

    Returns:
        解決済みの絶対パス（`tmp_path` 配下であることが確かめられたもの）。
    """
    expected = tmp_path.resolve()
    try:
        env = env_cls(_env_file=env_file)
    except Exception as exc:
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__} を build() と同じ経路で読めない: {exc}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    raw: object = getattr(env, field, None)
    if not isinstance(raw, Path):
        pytest.fail(
            f"止め木の能動検査: {env_cls.__name__}.{field} が Path でない: {raw!r}\n"
            f"  読んだキー名: {field}\n"
            f"  読んだ .env: {env_file}\n"
            f"  期待した接頭辞: {expected}"
        )
    resolved = raw.expanduser().resolve()
    if not resolved.is_relative_to(expected):
        pytest.fail(
            "止め木の能動検査: 解決先が一時ディレクトリの外にある"
            "（実物に触れる前に止めた＝ FR-017・SC-013）\n"
            f"  読んだキー名: {field}\n"
            f"  解決値: {resolved}\n"
            f"  期待した接頭辞: {expected}\n"
            f"  読んだ .env: {env_file}"
        )
    return resolved


# --------------------------------------------------------------------------------------
# 配線（規約 §3・clarify (b) の裁定）
# --------------------------------------------------------------------------------------


def build_module_app(
    *,
    enabled_modules: Sequence[str],
    overrides: ProtocolOverrides,
    registrations: Sequence[ModuleRegistration] | None = None,
) -> FastAPI:
    """**実 composition root**で提供実装を組む（FR-010・FR-011・規約 §3）。

    `config=` を必ず渡す —— `None` だと `create_app` が `load_root_config()` を呼び、
    実リポジトリの `config.json`・`.env` を読む（R-02）。素の `FastAPI()` に router を
    直 include する形は**採らない**（相関 ID ミドルウェアが落ちる）。

    呼び手は `enabled_modules` に**当該 MOD 1 件だけ**を渡す（`/health` のトポロジ検証だけが
    偽の登録 8 件を渡す）。

    Args:
        enabled_modules: 有効化する MOD 名の並び。
        overrides: 契約の型 → 差し替えるスタブ。
        registrations: 偽の登録集合（`None` なら実 `registry.py`）。

    Returns:
        `create_app` が返した `FastAPI`。
    """
    return build_test_app(
        enabled_modules=enabled_modules,
        overrides=overrides,
        config=make_root_config(enabled_modules=enabled_modules),
        registrations=registrations,
    )


def client_of(app: FastAPI) -> HttpClient:
    """`TestClient` を `HttpClient`（`_support/composition.py` の面）として返す。

    Args:
        app: 実配線で組んだアプリ。

    Returns:
        `HttpClient` として扱う `TestClient`。
    """
    client: HttpClient = TestClient(app)
    return client


DRAIN_TIMEOUT_SECONDS = 4.0
"""周回の排出を待つ上限（FR-043・規約 §9）。上限に達したら待ち直さずに失敗させる。"""

DRAIN_POLL_SECONDS = 0.02
"""排出の確認間隔。"""

CURRENT_KICKOFF_PATH = "/kickoffs/current"


def make_orchestrator_overrides(log: CallLog, *, board_reader: BoardReader) -> ProtocolOverrides:
    """orchestrator が `registry.py` で `requires` する 9 契約のスタブ写像を組む。

    憲章 §3: Protocol は構造的部分型なので、受ける変数に**型注釈を必ず付ける**
    （注釈が無いと pyright strict の適合検査が発火しない）。

    Args:
        log: 呼び出しの記録先。
        board_reader: `BoardReader` の実体（409 のテストは塞ぐ包みを渡す）。

    Returns:
        `overrides` に渡す写像。
    """
    board_writer: BoardWriter = StubBoardWriter(log)
    vault_reader: VaultReader = StubVaultReader(log)
    settings_reader: SettingsReader = StubSettingsReader(log)
    member_resolver: MemberResolver = StubMemberResolver(log)
    run_monitor: RunMonitor = StubRunMonitor(log)
    stage_catalog: StageCatalog = StubStageCatalog(log)
    proposal_gate: ProposalGate = StubProposalGate(log)
    crew_executor: CrewExecutor = StubCrewExecutor(log)
    repo_reader: RepoReader = StubRepoReader(log)
    overrides: ProtocolOverrides = {
        BoardReader: board_reader,
        BoardWriter: board_writer,
        VaultReader: vault_reader,
        SettingsReader: settings_reader,
        MemberResolver: member_resolver,
        RunMonitor: run_monitor,
        StageCatalog: stage_catalog,
        ProposalGate: proposal_gate,
        CrewExecutor: crew_executor,
        RepoReader: repo_reader,
    }
    return overrides


def drain_kickoff(client: HttpClient) -> None:
    """周回が排出されるまで**上限つき**で待つ（FR-043・規約 §9）。

    ワーカースレッドを join しない（`start_background` は daemon スレッドを起こすだけで
    join の口が無い）。上限に達したら**待ち直さずに失敗させる**（憲章 §10）。

    Args:
        client: `GET /kickoffs/current` を叩けるクライアント。
    """
    deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(CURRENT_KICKOFF_PATH).json()
        if not body["running"]:
            return
        time.sleep(DRAIN_POLL_SECONDS)
    pytest.fail(
        f"周回が {DRAIN_TIMEOUT_SECONDS} 秒で排出されなかった"
        "（上限で待ち直さない＝憲章 §10・FR-043）。"
        f" {CURRENT_KICKOFF_PATH} の running が false にならない。"
    )


class GatedBoardReader:
    """`list_working` を**最初の 1 回だけ**塞ぐ `BoardReader` の包み（R-06・HD-25）。

    409（single-flight）を踏むために in-flight を固定する。⚠ `MemberResolver.is_human` を
    塞いでも 409 は踏めない —— `build()` が組み立て時に 1 回呼ぶだけで周回に居ない。

    `entered` が立ったら 1 本目が周回に入っている。`release` を立てると通す。
    """

    def __init__(self, inner: BoardReader) -> None:
        """包む相手と 2 本の合図を持つ。"""
        self._inner: BoardReader = inner
        self.entered = threading.Event()
        self.release = threading.Event()
        self._gated = True

    def show(self, issue_id: str) -> Issue:
        """素通し。"""
        return self._inner.show(issue_id)

    def children_of(self, parent_id: str) -> list[Issue]:
        """素通し。"""
        return self._inner.children_of(parent_id)

    def open_question_of(self, issue_id: str) -> Issue | None:
        """素通し。"""
        return self._inner.open_question_of(issue_id)

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """最初の 1 回だけ `release` が立つまで待つ（in-flight の固定）。"""
        if self._gated:
            self._gated = False
            self.entered.set()
            self.release.wait(timeout=DRAIN_TIMEOUT_SECONDS)
        return self._inner.list_working(claim_filter)

    def comments(self, issue_id: str) -> list[Comment]:
        """素通し。"""
        return self._inner.comments(issue_id)

    def has_open_issues(self, project_key: str) -> bool:
        """素通し。"""
        return self._inner.has_open_issues(project_key)


def make_settings_overrides(
    log: CallLog, *, has_open: bool = False, folder_check: FolderCheck | None = None
) -> ProtocolOverrides:
    """settings が `registry.py` で `requires` する 2 契約のスタブ写像を組む（憲章 §3）。

    Args:
        log: 呼び出しの記録先。
        has_open: `has_open_issues` の戻り（`True` で `deleteProject` の 409 を踏む）。
        folder_check: `check_project_folders` の戻り（`ok=False` で `createProject` の 422）。

    Returns:
        `overrides` に渡す写像。
    """
    check = folder_check if folder_check is not None else FolderCheck(ok=True, missing=[])
    board_reader: BoardReader = StubBoardReader(log, has_open=has_open)
    vault_reader: VaultReader = StubVaultReader(log, folder_check=check)
    overrides: ProtocolOverrides = {BoardReader: board_reader, VaultReader: vault_reader}
    return overrides


def build_settings_app(
    *,
    log: CallLog,
    root: Path,
    env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_open: bool = False,
    folder_check: FolderCheck | None = None,
) -> FastAPI:
    """領域 A を一時ディレクトリに置いて settings を組む（`build()` の前に止め木を検査する）。

    `SETTINGS_ROOT` は **OS 環境変数**で指す（`.env` より優先される）。雛形は呼び手が
    `build()` の**前**に置いておく。

    Args:
        log: スタブの記録先。
        root: 領域 A のルート（雛形を置いた後で渡す）。
        env_file: 止め木の空 `.env`。
        monkeypatch: 環境変数の差し替え口。
        has_open: `BoardReader.has_open_issues` の戻り。
        folder_check: `VaultReader.check_project_folders` の戻り。

    Returns:
        組み上がった `FastAPI`。
    """
    monkeypatch.setenv("SETTINGS_ROOT", str(root))
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", root, env_file=env_file)
    return build_module_app(
        enabled_modules=("settings",),
        overrides=make_settings_overrides(log, has_open=has_open, folder_check=folder_check),
    )


# 宣言（`http_op`）と本体の突合（規約 §5・FR-002・FR-004。fixture は `conftest.py` 側）

EXPECTED_DECLARATION_ARGS = 3
"""`http_op` の引数の数（メソッド・パステンプレート・status）。"""

CONFORMS_NAME = "assert_conforms"
"""包む対象の名前（テストモジュールが `from http_asserts import ...` で束縛している名）。"""

EMPTY_LOG_HINT = " （1 件も無い —— 呼んでいないか**別モジュール**のヘルパ経由で呼んでいる）"
"""宣言が在るのに 1 件も検査していないときの案内。"""


def declared_pair(node: pytest.Item) -> StatusPair | None:
    """このテストの `http_op` 宣言を 1 件返す（無ければ `None`）。

    デコレータでも `pytest.param(..., marks=...)` でも拾う。**1 テスト 1 宣言**（FR-002）と
    引数 3 つ（規約 §5）に反する形はここで落とす（AST の C-0 は**ファイル単位**で数えるので
    1 つのテストに 2 件付いた形が見えない）。

    Args:
        node: 判定するテスト。

    Returns:
        (メソッド, パステンプレート, status)。宣言が無ければ `None`。
    """
    declared = [tuple(str(arg) for arg in marker.args) for marker in node.iter_markers("http_op")]
    if not declared:
        return None
    malformed = [pair for pair in declared if len(pair) != EXPECTED_DECLARATION_ARGS]
    assert not malformed, f"{node.nodeid}: http_op の引数が 3 つでない宣言がある: {malformed}"
    assert len(declared) == 1, (
        f"{node.nodeid}: 1 つのテストに http_op 宣言が {len(declared)} 件ある"
        f"（FR-002 は 1 テスト 1 宣言）: {declared}"
    )
    method, path, status = declared[0]
    return (method, path, status)


def recording_conforms(original: ConformsChecker, checked: list[StatusPair]) -> ConformsChecker:
    """検査した対を `checked` へ控えてから元の検査器へ通す包みを作る（振る舞いは変えない）。

    控えるのは元を**呼ぶ前**（適合の成否と無関係。既知差分の `xfail` は元の中で落ちる）。

    Args:
        original: 包む対象（テストモジュールが束縛している `assert_conforms`）。
        checked: 控え先（**呼び手の局所変数**。大域に持たない＝規約 §10-9）。

    Returns:
        包み。
    """

    def recording(
        doc: Mapping[str, Any], *, path: str, method: str, status: str, response: httpx.Response
    ) -> None:
        checked.append((method, path, status))
        original(doc, path=path, method=method, status=status, response=response)

    return recording


def assert_declared_pair_was_checked(
    node: pytest.Item, declared: StatusPair, checked: Sequence[StatusPair]
) -> None:
    """宣言した対を本体が実際に `assert_conforms` へ通したことを表明する（FR-002・FR-004）。

    Args:
        node: 判定するテスト。
        declared: そのテストの宣言。
        checked: 本体が検査した対の並び。
    """
    listed = "".join(f"\n    - {' '.join(pair)}" for pair in checked) if checked else EMPTY_LOG_HINT
    assert declared in checked, (
        f"{node.nodeid}: 宣言した対を本体が検査していない（宣言と本体が食い違っている）\n"
        f"  宣言: {' '.join(declared)}\n"
        f"  本体が {CONFORMS_NAME} に通した対:{listed}"
    )
