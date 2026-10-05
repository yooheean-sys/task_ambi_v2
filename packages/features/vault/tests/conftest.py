"""vault のテスト共通 fixture。共有部品は [vault_testing](./vault_testing.py) にある。

★ **止め木**（`_isolate_real_vault`）が本ファイルの核である。実リポジトリの `.env` には
vault ルートが実値で入っているため、autouse で既定を安全側に倒さないと `build()` を呼ぶ
テストが**実運用の Obsidian vault を掴む**（FR-019・SC-015）。

⚠ この止め木が効くのは、`vault.VaultEnv` の**明示 `__init__(self, **overrides)`** と
`vault.build()` の **`VaultEnv(_env_file=env_file_path())`** が**対で**在るときだけである。
どちらかを消すと `model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**に
なる（テストは緑のまま実運用 vault を掴む）。**この fixture を消さない・迂回しない。**
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from common.interfaces.settings import ProjectFolders
from common.kernel.enums import PersonaKey

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_real_vault(  # pyright: ignore[reportUnusedFunction]  # autouse fixture は pytest が収集して呼ぶ（名前で要求されないので `_` 始まり＝ plan (viii)・T016）
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """どのテストも実リポジトリの `.env` と実運用の vault ルートに届かない（FR-019・SC-015）。"""
    empty_env = tmp_path_factory.mktemp("env") / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr("vault.env_file_path", lambda: empty_env)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    yield


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """一時の vault ルート。3 領域と、その外側の 1 ファイルを持つ木を作る。

    ルート外を表すのは `tmp_path/outside/`（vault ルートは `tmp_path/vault`＝ 1 階層下）。
    symlink を張るテストは**リンク先を `tmp_path` の内側に作る**（pytest の掃除は symlink を
    辿らないため、外側に実体を作ると残る）。
    """
    root = tmp_path / "vault"
    (root / "pj/outcome").mkdir(parents=True)
    (root / "pj/sub").mkdir(parents=True)
    (root / "pj/roles/planner").mkdir(parents=True)
    (root / "pj/roles/writer").mkdir(parents=True)
    (root / "other").mkdir(parents=True)
    (root / "outside.md").write_text("SECRET", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("OUTSIDE", encoding="utf-8")
    return root


@pytest.fixture
def folders() -> ProjectFolders:
    """既定構成の宣言（`outcome_dir` が `pj_input_dir` の内側＝ D06-a）。

    settings が未実装のため、テストが直接組み立てる（憲章 §7-6「呼び出し元はドライバで偽装
    する」・spec Assumptions）。
    """
    return ProjectFolders(
        pj_input_dir="pj",
        role_dirs={
            PersonaKey.planner: "pj/roles/planner",
            PersonaKey.writer: "pj/roles/writer",
        },
        outcome_dir="pj/outcome",
    )
