"""`build()` の起動時 4 検査と組み立て（US6・SC-010・FR-005・FR-052〜057・R-05）。

ファイル名が `test_build.py` でないのは、`board/tests/unit/test_build.py` と **top-level
モジュール名が衝突する**ためである（pytest の既定 import モードでは `tests/` 配下に
`__init__.py` を置かないので基名がそのままモジュール名になる）。plan (viii) 末尾が
`vault_testing.py` に対して述べたのと同じ理由で、基名を一意にして曖昧さを消す。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from vault_testing import write_file

import vault
from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import VaultPathViolation
from common.kernel.enums import PersonaKey
from vault import VaultConfig, VaultDeps, build, config_path
from vault.exceptions import VaultConfigError, VaultEnvError, VaultRootError


def _folders() -> ProjectFolders:
    return ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/outcome",
    )


# ------------------------------------------------------------------------ 4 検査（順序つき）


def test_a_missing_vault_root(tmp_path: Path) -> None:
    """(a) vault ルート未設定 → `VaultEnvError`（キー名と**読んだ `.env` の絶対パス**）。"""
    del tmp_path
    with pytest.raises(VaultEnvError) as exc:
        build(VaultDeps())
    message = str(exc.value)
    # 期待するキー名は**宣言から採る**（リテラルを書かない。改名すればここで落ちる）
    assert next(iter(vault.VaultEnv.model_fields)) in message
    assert str(vault.env_file_path()) in message


def test_b_vault_root_does_not_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) 実在しない → `VaultRootError`（**見に行った絶対パス**）。"""
    missing = tmp_path / "nope"
    monkeypatch.setenv("VAULT_ROOT", str(missing))
    with pytest.raises(VaultRootError) as exc:
        build(VaultDeps())
    assert str(missing) in str(exc.value)
    assert "実在しない" in str(exc.value)


def test_c_vault_root_is_not_a_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) ディレクトリでない → `VaultRootError`（b と**別のメッセージ**）。"""
    as_file = tmp_path / "afile"
    as_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("VAULT_ROOT", str(as_file))
    with pytest.raises(VaultRootError) as exc:
        build(VaultDeps())
    assert str(as_file) in str(exc.value)
    assert "ディレクトリでない" in str(exc.value)
    assert "実在しない" not in str(exc.value)


def test_d_config_with_an_unknown_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) 未知キー → `VaultConfigError`（**読んだ `config.json` の絶対パス**）。"""
    root = tmp_path / "vault"
    root.mkdir()
    bad = tmp_path / "config.json"
    bad.write_text(json.dumps({"unknown_key": 1}), encoding="utf-8")
    monkeypatch.setenv("VAULT_ROOT", str(root))
    monkeypatch.setattr("vault.config_path", lambda: bad)
    with pytest.raises(VaultConfigError) as exc:
        build(VaultDeps())
    assert str(bad) in str(exc.value)


def test_d_config_with_a_bad_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    bad = tmp_path / "config.json"
    bad.write_text(json.dumps({"max_outcome_bytes": "large"}), encoding="utf-8")
    monkeypatch.setenv("VAULT_ROOT", str(root))
    monkeypatch.setattr("vault.config_path", lambda: bad)
    with pytest.raises(VaultConfigError):
        build(VaultDeps())


def test_checks_report_only_the_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """順序は a → b → c → d で、**最初に失敗したものだけ**を報告する。"""
    bad = tmp_path / "config.json"
    bad.write_text(json.dumps({"unknown_key": 1}), encoding="utf-8")
    monkeypatch.setattr("vault.config_path", lambda: bad)
    # a と d が同時に壊れていても a を報告する
    with pytest.raises(VaultEnvError):
        build(VaultDeps())
    # b と d が同時に壊れていても b を報告する
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "nope"))
    with pytest.raises(VaultRootError):
        build(VaultDeps())


# ------------------------------------------------------------------------------- 組み立て


def test_reader_and_writer_are_the_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-005: `vault_reader is outcome_writer`。"""
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("VAULT_ROOT", str(root))
    handle = build(VaultDeps())
    assert handle.vault_reader is handle.outcome_writer
    assert handle.router is None


def test_config_override_takes_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-057: `config=` による差し替えが効く（⑧）。"""
    root = tmp_path / "vault"
    (root / "pj/outcome").mkdir(parents=True)
    monkeypatch.setenv("VAULT_ROOT", str(root))
    handle = build(VaultDeps(), config=VaultConfig(max_outcome_bytes=3))
    with pytest.raises(VaultPathViolation) as exc:
        handle.outcome_writer.write_outcome(_folders(), "o.md", "abcd")
    assert exc.value.kind == "too_large"


def test_build_does_not_accept_an_env_argument() -> None:
    """`env` は引数で受けない（解決順序は ⑥→⑦。差し替えは `config` だけ＝ plan (vi)）。"""
    assert list(inspect.signature(build).parameters) == ["deps", "config"]


def test_build_does_not_pin_the_resolved_vault_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-05: vault ルートを `realpath` して保持しない（起動後の移動・再リンクに追随する）。"""
    first = tmp_path / "v1"
    second = tmp_path / "v2"
    for root in (first, second):
        (root / "pj/outcome").mkdir(parents=True)
    write_file(second, "pj/outcome/o.md", "SECOND")
    link = tmp_path / "root_link"
    link.symlink_to(first)
    monkeypatch.setenv("VAULT_ROOT", str(link))
    handle = build(VaultDeps())
    assert handle.vault_reader.outcome_exists(_folders(), "o.md") is False
    link.unlink()
    link.symlink_to(second)
    assert handle.vault_reader.outcome_exists(_folders(), "o.md") is True


def test_encoding_falls_back_to_the_root_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-11: `config.encoding` が `None` ならルート `defaults.encoding` を継承する。"""
    root = tmp_path / "vault"
    (root / "pj/outcome").mkdir(parents=True)
    monkeypatch.setenv("VAULT_ROOT", str(root))
    assert VaultConfig().encoding is None
    handle = build(VaultDeps())
    handle.outcome_writer.write_outcome(_folders(), "o.md", "あ")
    assert (root / "pj/outcome/o.md").read_bytes() == "あ".encode()


def test_root_config_failure_names_the_file_it_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-010 (d) の後段: ルート設定が読めない失敗にも**見に行った絶対パス**が載る（T094）。

    `data-model.md` §4 は `VaultConfigError` を「config.json の必須値欠け・未知キー／
    **ルート設定が読めない**（SC-010 d）」と定めており、FR-054 はその両方に絶対パスを求める。
    ここで作るのは**スキーマ不一致**（必須キー欠け）の版である ―― 不在と JSON 不正は
    `common.kernel.config._ensure_readable` が元例外にパスを載せるが、`ValidationError`
    だけは載せないため、この分岐が FR-054 の唯一の穴だった（VD-45）。
    """
    root = tmp_path / "vault"
    (root / "pj/outcome").mkdir(parents=True)
    monkeypatch.setenv("VAULT_ROOT", str(root))
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    bad_root_config = fake_repo / "config.json"
    bad_root_config.write_text(json.dumps({}), encoding="utf-8")  # 必須キーが 1 つも無い
    # `config_path()` も `RootConfig` も `common.kernel.config` の globals から
    # `find_repo_root` を引くので、ここを差し替えると両方が偽のルートを見る
    monkeypatch.setattr("common.kernel.config.find_repo_root", lambda: fake_repo)
    assert VaultConfig().encoding is None  # ルート継承の経路に入る前提（R-11）
    with pytest.raises(VaultConfigError) as exc:
        build(VaultDeps())
    assert str(bad_root_config) in str(exc.value)


def test_module_config_json_has_no_encoding_key() -> None:
    """B-3: モジュール `config.json` に `encoding` キーを書かない（ルート継承を実装に現す）。"""
    payload = json.loads(config_path().read_text(encoding="utf-8"))
    assert "encoding" not in payload


def test_env_file_isolation_is_in_effect(tmp_path: Path) -> None:
    """★ 止め木が効いていること（FR-019・SC-015）。実 `.env` を読んでいたら (a) は落ちない。"""
    del tmp_path
    # 名前で束縛すると差し替え前の関数を掴む。**モジュール経由**で見る
    assert vault.env_file_path().read_text(encoding="utf-8") == ""
