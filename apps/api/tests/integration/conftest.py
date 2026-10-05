"""統合テストの共通 fixture（`_support/composition.py` を fixture 化する。FR-036）。

実体は `_support/` にあり、本ファイルはそれを配るだけ。`cluster_NN_<slug>/` からは
fixture でも直接 import でも同じものが使える。
"""

from __future__ import annotations

import pytest
from _support.composition import ClientFactory, build_test_client


def pytest_configure(config: pytest.Config) -> None:
    """クラスタ用マーカ 4 種を登録する（FR-044・規約 §7・plan (vii)）。

    上位 `conftest.py` の登録は**下位ディレクトリの単独実行にも効く**ので、各クラスタへ
    5 回書かない。`requires_bd` はルート `pyproject.toml` に登録済みなのでここでは足さない
    （**ルート `pyproject.toml` は変更しない**＝ FR-044・Assumptions 10）。
    """
    config.addinivalue_line(
        "markers",
        "contract_if(if_id): 検証する契約行 IF-nn を 1 件だけ宣言する",
    )
    config.addinivalue_line(
        "markers",
        "cluster_case(case_id): 対応する観点行 NN#k を 1 件だけ宣言する",
    )
    config.addinivalue_line(
        "markers",
        "cluster_not_exercised(reason_id): 振る舞いを踏まない行（FR-021 の 2 件のみ）",
    )
    config.addinivalue_line(
        "markers",
        "cluster_conditional(reason_id): 条件付きの行（FR-022 の 3 件のみ）",
    )


@pytest.fixture
def client_factory() -> ClientFactory:
    """実配線で `TestClient` を組む工場を返す。"""
    return build_test_client
