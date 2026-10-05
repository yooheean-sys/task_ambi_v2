"""クラスタの配線（D14-e）。plan (ii)・data-model §7・規約 §3。

**手配線をしない** —— `<mod>.build(...)` をテストから呼ばず、実 composition root
（`api.wiring.build_modules` / `api.app.create_app`）を必ず通す。やることは 2 つだけである。

1. `enabled_modules` でクラスタ外 MOD を**構築から外す**（override はモジュールの組み立てを
   抑止しないので、外すのは有効 MOD 一覧の側）。
2. 残った未充足契約にだけ `overrides` でスタブを注入する。

**過不足は起動時に落ちる** —— 足りなければ `UnsatisfiedContractError`、要求されていない契約への
override は `ExtraneousOverrideError`、有効 MOD と override の二重供給は `DuplicateProviderError`。

取り出しは `api.wiring.require` を使い、`cast` を自前で書かない。受ける変数には必ず Protocol
型を注釈する（憲章 §3。注釈が無いと pyright strict の構造適合検査が発火しない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from _support.composition import build_test_client, make_root_config

from api.wiring import build_modules

if TYPE_CHECKING:
    from collections.abc import Mapping

    from _support.cluster_stores import ClusterStores
    from _support.composition import HttpClient
    from _support.fake_llm_server import FakeLlmServer

    from api.wiring import ProtocolOverrides, Wiring
    from common.kernel.config import RootConfig

CLUSTER_MODULES: Mapping[str, tuple[str, ...]] = {
    "01": ("board", "monitor", "executor", "orchestrator"),
    "02": ("board", "vault", "settings"),
    "03": ("board", "vault", "settings", "monitor", "executor", "repo"),
    "04": ("board", "vault", "settings", "stages", "orchestrator", "repo"),
    "05": ("orchestrator", "trigger"),
}
"""クラスタ ID → `enabled_modules`（`x2_cluster_index.csv` の「対象 MOD」列と一致する）。

未充足契約（＝ override で埋める契約）の件数は `registry.py` の `requires`／`provides` から
機械的に決まる: 01＝10・02＝0・03＝0・04＝2・05＝10（D25 で 01 に `RepoReader`・
`RepoWriter`、05 に `RepoReader` が加わった。03・04 は実物の repo を有効化する）。

⚠ **05 の override に `KickoffRunner` を入れない** —— trigger が要求する `KickoffRunner` は
有効化した実物の orchestrator が供給するので、入れると `DuplicateProviderError` になる（CD-10）。
"""


def cluster_modules(cluster_id: str) -> tuple[str, ...]:
    """クラスタ ID に対応する有効 MOD 一覧を返す。

    Args:
        cluster_id: `"01"`〜`"05"`。

    Returns:
        `enabled_modules` に渡す並び。
    """
    try:
        return CLUSTER_MODULES[cluster_id]
    except KeyError as exc:
        msg = f"未知のクラスタ ID: {cluster_id!r} / 既知: {sorted(CLUSTER_MODULES)}"
        raise KeyError(msg) from exc


def cluster_config(cluster_id: str) -> RootConfig:
    """そのクラスタの有効 MOD 一覧を持つルート設定を組む。

    ルート `config.json` は 1 行も変えない（差し替えは `make_root_config` の引数だけで行う）。

    Args:
        cluster_id: `"01"`〜`"05"`。

    Returns:
        `build_modules`／`create_app` に渡す `RootConfig`。
    """
    return make_root_config(enabled_modules=cluster_modules(cluster_id))


def build_cluster_wiring(cluster_id: str, overrides: ProtocolOverrides) -> Wiring:
    """実 composition root でそのクラスタを組み、`Wiring` を返す（直接ドライブ用）。

    Args:
        cluster_id: `"01"`〜`"05"`。
        overrides: 未充足契約 → スタブ実装。

    Returns:
        `api.wiring.build_modules` の戻り（`provided` から `require` で取り出す）。
    """
    return build_modules(
        enabled_modules=cluster_modules(cluster_id),
        overrides=overrides,
        config=cluster_config(cluster_id),
    )


def build_cluster_client(cluster_id: str, overrides: ProtocolOverrides) -> HttpClient:
    """実配線 `create_app` でそのクラスタを組み、`TestClient` を返す（HTTP 入口用）。

    ⚠ `create_app` は `Wiring` を公開しないので、本関数のアプリと `build_cluster_wiring` の
    戻りは**別インスタンス**である（R-07）。**1 つのテストの中で HTTP と直接ドライブを
    混ぜない**。lifespan を持つ登録（trigger）を含むクラスタは `with` で通す。

    Args:
        cluster_id: `"01"`〜`"05"`。
        overrides: 未充足契約 → スタブ実装。

    Returns:
        実配線で組んだクライアント。
    """
    return build_test_client(
        enabled_modules=cluster_modules(cluster_id),
        overrides=overrides,
        config=cluster_config(cluster_id),
    )


@dataclass(frozen=True, slots=True)
class ClusterRuntime:
    """「実物一式」をひとまとめにした戻り（03・01・04 が共有する）。

    クラスタの `conftest.py` ではなく `_support/` に置く —— テストが型注釈のために
    `from conftest import ...`（および相対 import）へ手を伸ばさないようにするためである
    （FR-041a・R-02）。

    契約実装は `wiring.provided` から `api.wiring.require` で取り出す（規約 §3。`cast` を
    自前で書かない。受ける変数には Protocol 型を必ず注釈する）。
    """

    wiring: Wiring
    """実 composition root が組んだ結果。"""

    stores: ClusterStores
    """一時 beads・vault・領域 A。"""

    server: FakeLlmServer | None = None
    """偽 LLM サーバ（使わないクラスタは `None`）。**`call_count` を必ず assert する**＝ R-04。"""

    @property
    def llm(self) -> FakeLlmServer:
        """偽 LLM サーバ（渡されていなければ失敗する）。"""
        if self.server is None:
            msg = "このクラスタ実行には偽 LLM サーバが渡されていない"
            raise AssertionError(msg)
        return self.server
