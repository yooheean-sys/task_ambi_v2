"""外縁・領域ゲート（US1・SC-002・SC-009・quickstart S4 の 7 通り）。

スタブを 1 つも使わず、`tmp_path` を vault ルートに見立てた**実ファイルシステム**に対して行う。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from vault_testing import link, make_adapter, make_service, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import FileRef, VaultArea, VaultNotFound, VaultPathViolation
from common.kernel.enums import PersonaKey


def _ref(area: VaultArea, rel_path: str) -> FileRef:
    """`read_input` に渡す参照（`size_bytes`・`mtime` は再解決で使わない＝ FR-026）。"""
    return FileRef(
        area=area,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        size_bytes=0,
        mtime=datetime.now(tz=UTC),
    )


@pytest.mark.parametrize(
    ("area", "rel_path", "kind"),
    [
        # 1. vault ルートの外を指す相対パス
        (VaultArea.pj_input, "../outside/secret.md", "outside_root"),
        # 2. 絶対パス
        (VaultArea.pj_input, "/etc/passwd", "outside_root"),
        # 3. ルート内だが宣言された領域の外
        (VaultArea.pj_input, "other/x.md", "outside_area"),
        # 4. 領域内から `..` で領域を出るがルートには留まる
        (VaultArea.pj_input, "pj/../other/x.md", "outside_area"),
    ],
)
def test_resolve_rejects_outside(
    vault_root: Path,
    folders: ProjectFolders,
    area: VaultArea,
    rel_path: str,
    kind: str,
) -> None:
    write_file(vault_root, "other/x.md", "ROOT_INTERNAL")
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(area, rel_path))
    assert exc.value.kind == kind


def test_symlink_out_of_root_is_outside_root(
    vault_root: Path, folders: ProjectFolders, tmp_path: Path
) -> None:
    """5. 領域内に置いた symlink がルート外を指す（`realpath` 後に判定＝ M-09）。"""
    link(vault_root, "pj/escape.md", tmp_path / "outside" / "secret.md")
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(VaultArea.pj_input, "pj/escape.md"))
    assert exc.value.kind == "outside_root"


def test_final_element_symlink_out_of_area_is_outside_area(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """6. **最終要素だけが**領域外を指す symlink（FR-022 後段）。"""
    write_file(vault_root, "other/x.md", "ROOT_INTERNAL")
    link(vault_root, "pj/toother.md", vault_root / "other" / "x.md")
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(VaultArea.pj_input, "pj/toother.md"))
    assert exc.value.kind == "outside_area"


def test_tampered_file_ref_cannot_escape_the_area(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """7. 一覧で得た `FileRef` の `rel_path` を領域外に書き換えたもの（FR-026）。"""
    write_file(vault_root, "pj/a.md", "A")
    write_file(vault_root, "other/x.md", "ROOT_INTERNAL")
    service = make_service(vault_root)
    listed = service.list_inputs(folders, PersonaKey.planner)
    original = next(ref for ref in listed if ref.rel_path == "pj/a.md")
    tampered = original.model_copy(update={"rel_path": "other/x.md"})
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, tampered)
    assert exc.value.kind == "outside_area"


def test_file_ref_is_re_resolved_every_time(vault_root: Path, folders: ProjectFolders) -> None:
    """SC-009: 一覧 → 外部から改名 → `read_input` が `VaultNotFound`（解決結果を持ち回らない）。"""
    write_file(vault_root, "pj/a.md", "A")
    service = make_service(vault_root)
    ref = next(r for r in service.list_inputs(folders, PersonaKey.planner) if r.name == "a.md")
    assert service.read_input(folders, ref) == "A"
    (vault_root / "pj" / "a.md").rename(vault_root / "pj" / "b.md")
    with pytest.raises(VaultNotFound) as exc:
        service.read_input(folders, ref)
    assert exc.value.rel_path == "pj/a.md"
    assert exc.value.role is None


def test_returned_paths_are_vault_root_relative(vault_root: Path, folders: ProjectFolders) -> None:
    """呼び手へ絶対パスを返さない（FR-025・SC-009）。"""
    write_file(vault_root, "pj/a.md", "A")
    service = make_service(vault_root)
    for ref in service.list_inputs(folders, PersonaKey.planner):
        assert not ref.rel_path.startswith("/")
        assert str(vault_root) not in ref.rel_path


def test_resolve_returns_booleans_not_exceptions(vault_root: Path) -> None:
    """`FsAdapter.resolve` は合否を**真偽値で**返す（contracts/fs-adapter.md §1 原則 1）。"""
    adapter = make_adapter(vault_root)
    inside = adapter.resolve(rel="pj/a.md", area=VaultArea.pj_input, area_rels=("pj",))
    assert (inside.root_ok, inside.area_ok) == (True, True)
    assert inside.rel == "pj/a.md"
    outside = adapter.resolve(rel="../x.md", area=VaultArea.pj_input, area_rels=("pj",))
    assert outside.root_ok is False
    other = adapter.resolve(rel="other/x.md", area=VaultArea.pj_input, area_rels=("pj",))
    assert (other.root_ok, other.area_ok) == (True, False)


def test_resolve_does_not_use_string_comparison_for_containment(vault_root: Path) -> None:
    """FR-024: 綴りが前置一致でも、実体が領域の外なら領域外と判定する。

    `pj2` は `pj` を前置に持つが別実体である（`startswith` を根拠にすると内側と誤判定する）。
    """
    (vault_root / "pj2").mkdir()
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(rel="pj2/a.md", area=VaultArea.pj_input, area_rels=("pj",))
    assert resolved.root_ok is True
    assert resolved.area_ok is False


def test_missing_area_root_does_not_make_everything_inside(vault_root: Path) -> None:
    """領域根が実在しないとき、その候補は不一致として扱う（contracts/fs-adapter.md §3 手順 5）。"""
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(rel="pj/a.md", area=VaultArea.role, area_rels=("nope",))
    assert resolved.root_ok is True
    assert resolved.area_ok is False
