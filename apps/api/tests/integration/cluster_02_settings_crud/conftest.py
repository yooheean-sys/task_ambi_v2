"""クラスタ 02（settings CRUD × 実 board × 実 vault）の準備手段。plan (i)・(ii)・(iii)。

**ダブル 0** —— override は 1 件も無い（`board`・`vault`・`settings` の 3 MOD がすべて実物で、
settings が要求する `BoardReader`・`VaultReader` は board・vault が供給する）。したがって
`TestClient` 以外のテストダブルを持ち込まない。

止め木は 4 枚とも通す（規約 §5）。

1. `isolate_env`（autouse）で 5 つの `env_file_path` を一時 `.env` へ向け、危険な環境変数を消す。
2. 一時ストアを作ってから `monkeypatch.setenv` で指し直す（消す→指すの順）。
3. `assert_env_path_under` を **`build()` の前**に 3 MOD ぶん通す（settings は不在時に雛形を
   **書き込む**ので事後確認では手遅れ）。
4. `assert_outside_repo` を **`bd init` の前**に通す（`init_beads` の中で通る）。

fixture は**すべて関数スコープ**（FR-042。一時ディレクトリ・`bd init` をテストごとに独立させる）。
`from conftest import ...` を使わない（FR-041a・R-02）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _support.cluster_env import assert_env_path_under, isolate_env
from _support.cluster_logs import CorrelationRecorder, recording_correlation
from _support.cluster_stores import (
    ClusterStores,
    init_beads,
    make_temp_area_a,
    make_temp_vault,
    remove_beads,
    require_bd_or_skip,
)
from _support.cluster_wiring import build_cluster_client

from board import BoardEnv
from settings import SettingsEnv
from vault import VaultEnv

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from _support.composition import HttpClient

BEADS_PROJECT_KEY = "cl02"
"""`bd init -p` に渡す接頭辞（issue_id の前半になる。ラベルの `project:` とは別物）。"""

SPARE_PROJECT_KEY = "sparepj"
"""解除の正常系で消す 2 つ目のプロジェクト（`commands.yaml` を参照しない）。

⚠ **唯一の PJ は解除できない** —— 解除は `policy.csv` ごと退避するので `commands.yaml` の定義が
死に定義（V-25）になり 409 で弾かれる（`010` B-51 と同型。2026-09-01 に実測）。解除の正常系
（#8）と相関 ID の貫通（#16）はこちらを消す。
"""


@pytest.fixture
def bd_bin() -> str:
    """`bd` の絶対パス（無ければ **skip**）。

    ルート `apps/api/tests/conftest.py` の同名 fixture は `pytest.fail` 版なので、`contract/` と
    同じく skip 版で上書きする（R-13）。ルートの conftest は変更しない。
    """
    return require_bd_or_skip()


@pytest.fixture(autouse=True)
def cluster_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """止め木の 1・2 枚目（autouse。**名前でも要求できる**）。

    差し替えを忘れた MOD は `build()` が「キーが無い」で落ちるので、実物を掴んだまま緑になる
    ことが構造的に起きない。

    Returns:
        差し替え先の一時 `.env` の絶対パス（`assert_env_path_under` に渡す）。
    """
    return isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_env: Path,
    bd_bin: str,
) -> Iterator[ClusterStores]:
    """一時 beads・vault・領域 A を作り、3 MOD の環境変数を指し直す（`build()` の前）。

    `assert_env_path_under` は `build()` と**同じ経路**（`<Mod>Env(_env_file=…)`）で読むので、
    ここを通れば「実運用ストアを掴んだまま緑」が起きない。

    Yields:
        作った一時ストアのひとまとめ（teardown で beads を消す）。
    """
    beads = init_beads(tmp_path, bd_bin, project_key=BEADS_PROJECT_KEY)
    vault = make_temp_vault(tmp_path)
    area_a = make_temp_area_a(tmp_path, spare_project_key=SPARE_PROJECT_KEY)

    monkeypatch.setenv("BEADS_DIR", str(beads.beads_dir))
    monkeypatch.setenv("VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))

    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=cluster_env)
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)

    yield ClusterStores(beads=beads, vault=vault, area_a=area_a)

    remove_beads(beads)


@pytest.fixture
def client(stores: ClusterStores) -> Iterator[HttpClient]:
    """実配線 `create_app` で組んだクライアント（override **0 件**）。

    `stores` を要求することで「一時ストアを指してから `build()` を呼ぶ」順序が保証される。

    Yields:
        `TestClient`（型は `HttpClient` として扱う）。
    """
    assert stores.area_a.root.is_dir(), "領域 A の準備が済んでいない（fixture の順序を確認する）"
    with build_cluster_client("02", {}) as http:
        yield http


@pytest.fixture
def missing_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cluster_env: Path) -> Path:
    """`SETTINGS_ROOT` は在るが**中身が空**の領域 A（#13 の雛形生成用の別 fixture）。

    起動そのものの成否を見る観点（#13・#14）は、正常系と同じ土台に同居できない
    （クラスタ 02 §5 が明記）。board・vault は正常に組めるようにしておく。

    Returns:
        空の領域 A のルート。
    """
    empty_root = tmp_path / "empty_area_a"
    empty_root.mkdir()
    vault = make_temp_vault(tmp_path)
    light_beads = tmp_path / "beads_only" / ".beads"
    light_beads.mkdir(parents=True)
    (light_beads / "config.yaml").write_text("", encoding="utf-8")

    monkeypatch.setenv("BEADS_DIR", str(light_beads))
    monkeypatch.setenv("VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("SETTINGS_ROOT", str(empty_root))
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=cluster_env)
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)
    return empty_root


@pytest.fixture
def broken_area_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cluster_env: Path) -> Path:
    """構文誤りのファイルを持つ領域 A（#14 の起動中断用の別 fixture）。

    `global/agents.yaml` を YAML として壊す。`build()` の (d2) 段（領域 A の読取）で
    `SettingsAreaError` になり、**黙って既定に落ちない**ことを見る。

    Returns:
        壊れた領域 A のルート。
    """
    area_a = make_temp_area_a(tmp_path)
    (area_a.root / "global/agents.yaml").write_text(
        "programmer:\n  role: |-\n   壊れた\n  goal: [unclosed\n", encoding="utf-8"
    )
    vault = make_temp_vault(tmp_path)
    light_beads = tmp_path / "beads_only" / ".beads"
    light_beads.mkdir(parents=True)
    (light_beads / "config.yaml").write_text("", encoding="utf-8")

    monkeypatch.setenv("BEADS_DIR", str(light_beads))
    monkeypatch.setenv("VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=cluster_env)
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)
    return area_a.root


@pytest.fixture
def correlation_recorder() -> Iterator[CorrelationRecorder]:
    """ルートロガへ観測点を足し、レコードごとの相関 ID を控える（#16 用）。

    実体は `_support/cluster_logs.py` にある（`caplog` では相関 ID が見えない —— `LogRecord`
    には載らず整形器の側で `CorrelationContext.current()` を読むため）。

    Yields:
        観測結果を持つハンドラ。
    """
    yield from recording_correlation()


@pytest.fixture
def reserved_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_env: Path,
    bd_bin: str,
) -> Iterator[HttpClient]:
    """予約キー `_program` を**登録済み**にした領域 A で組んだクライアント（#7 用）。

    既定の領域 A には `_program` を置かない（置くと他の観点の一覧が 1 件増える）。未登録だと
    `DELETE /settings/projects/_program` は `_require_project` に当たって 404 になり、予約キーの
    分岐へ届かないので、この観点だけ別の土台を使う。

    Yields:
        `TestClient`（型は `HttpClient` として扱う）。
    """
    beads = init_beads(tmp_path, bd_bin, project_key=BEADS_PROJECT_KEY)
    vault = make_temp_vault(tmp_path)
    area_a = make_temp_area_a(tmp_path, with_reserved_project=True)

    monkeypatch.setenv("BEADS_DIR", str(beads.beads_dir))
    monkeypatch.setenv("VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("SETTINGS_ROOT", str(area_a.root))
    assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path, env_file=cluster_env)
    assert_env_path_under(VaultEnv, "VAULT_ROOT", tmp_path, env_file=cluster_env)
    assert_env_path_under(SettingsEnv, "SETTINGS_ROOT", tmp_path, env_file=cluster_env)

    with build_cluster_client("02", {}) as http:
        yield http

    remove_beads(beads)
