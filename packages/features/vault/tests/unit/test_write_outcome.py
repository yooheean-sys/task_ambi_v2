"""`write_outcome` のゲート鎖・退避・原子性（US2・SC-003・SC-005・FR-045〜050）。"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from vault_testing import dirs_under, make_adapter, make_service, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import VaultArea, VaultNotFound, VaultPathViolation
from common.kernel.enums import PersonaKey
from vault import VaultConfig
from vault.exceptions import FsArchiveError, FsIoError
from vault.repository import ARCHIVE_SEQ_LIMIT, TMP_PREFIX

if TYPE_CHECKING:
    from vault.repository import FsAdapter


def _outcome(vault_root: Path) -> Path:
    return vault_root / "pj" / "outcome"


# ------------------------------------------------------------------------- ゲート鎖（T032）


def test_new_file(vault_root: Path, folders: ProjectFolders) -> None:
    service = make_service(vault_root)
    written = service.write_outcome(folders, "o.md", "BODY")
    assert written.rel_path == "pj/outcome/o.md"
    assert written.bytes == 4
    assert written.overwritten is False
    assert written.archived_to is None
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "BODY"


def test_overwrite_reports_the_archive(vault_root: Path, folders: ProjectFolders) -> None:
    write_file(vault_root, "pj/outcome/o.md", "OLD")
    service = make_service(vault_root)
    written = service.write_outcome(folders, "o.md", "NEW")
    assert written.overwritten is True
    assert written.archived_to is not None
    assert written.archived_to.startswith("pj/outcome/_archive/o.md.")
    assert (vault_root / written.archived_to).read_text(encoding="utf-8") == "OLD"
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "NEW"


def test_empty_content_is_rejected_and_nothing_is_written(
    vault_root: Path, folders: ProjectFolders
) -> None:
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "o.md", "")
    assert exc.value.kind == "empty"
    assert list(_outcome(vault_root).iterdir()) == []


def test_content_over_the_limit_is_rejected_with_measured_bytes(
    vault_root: Path, folders: ProjectFolders
) -> None:
    service = make_service(vault_root, VaultConfig(max_outcome_bytes=5))
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "o.md", "abcdef")
    assert exc.value.kind == "too_large"
    assert "6 bytes > 5" in exc.value.detail
    assert list(_outcome(vault_root).iterdir()) == []


def test_content_exactly_at_the_limit_passes(vault_root: Path, folders: ProjectFolders) -> None:
    service = make_service(vault_root, VaultConfig(max_outcome_bytes=5))
    assert service.write_outcome(folders, "o.md", "abcde").bytes == 5


def test_content_bytes_are_counted_in_the_write_encoding(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """バイト数は**書き込む文字コードでの符号化長**（文字数ではない）。"""
    service = make_service(vault_root, VaultConfig(max_outcome_bytes=5))
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "o.md", "あい")  # 2 文字 = 6 バイト
    assert "6 bytes > 5" in exc.value.detail


def test_name_gate_runs_before_the_content_gate(vault_root: Path, folders: ProjectFolders) -> None:
    """FR-045: 先の段で止まったら後の段を行わない（内容も空だが名前の違反を返す）。"""
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "a:b.md", "")
    assert exc.value.kind == "forbidden_name"


def test_missing_outcome_dir_is_not_found_and_no_folder_is_created(vault_root: Path) -> None:
    """R-14・C-3 (3): `outcome_dir` 不在 → `VaultNotFound`（フォルダを作らない＝ FR-014）。"""
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/nope",
    )
    service = make_service(vault_root)
    before = dirs_under(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.write_outcome(declared, "o.md", "BODY")
    assert exc.value.rel_path == "pj/nope"
    assert exc.value.role is None
    assert dirs_under(vault_root) == before


def test_outcome_dir_outside_the_root_is_a_violation(vault_root: Path) -> None:
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="../outside",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(declared, "o.md", "BODY")
    assert exc.value.kind == "outside_root"


def test_only_one_write_method_exists() -> None:
    """SC-003: `service.py` の書込口は `write_outcome` だけ（P5D-011）。"""
    source = Path("packages/features/vault/src/vault/service.py").read_text(encoding="utf-8")
    assert source.count("def write") == 1


# --------------------------------------------------------------------------- 退避（T030）


def test_archive_moves_the_old_version(vault_root: Path, folders: ProjectFolders) -> None:
    """旧版が**移動**する（元の場所に残らない＝ FR-047）。"""
    write_file(vault_root, "pj/outcome/o.md", "OLD")
    service = make_service(vault_root)
    written = service.write_outcome(folders, "o.md", "NEW")
    archive = _outcome(vault_root) / "_archive"
    assert [p.name for p in archive.iterdir()] == [Path(str(written.archived_to)).name]
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "NEW"


def test_archive_dir_is_created_only_when_needed_and_only_one_level(
    vault_root: Path, folders: ProjectFolders
) -> None:
    service = make_service(vault_root)
    before = dirs_under(vault_root)
    service.write_outcome(folders, "o.md", "FIRST")
    assert dirs_under(vault_root) == before  # 新規書込では作られない
    service.write_outcome(folders, "o.md", "SECOND")
    assert dirs_under(vault_root) - before == {"pj/outcome/_archive"}


def test_archive_stamp_format(vault_root: Path, folders: ProjectFolders) -> None:
    """`%Y%m%dT%H%M%SZ`（UTC・秒精度・`:` を含まない）＝ C-2 (1)・R-07。"""
    write_file(vault_root, "pj/outcome/o.md", "OLD")
    service = make_service(vault_root)
    archived = str(service.write_outcome(folders, "o.md", "NEW").archived_to)
    stamp = archived.rsplit(".", 1)[-1]
    assert ":" not in stamp
    assert len(stamp) == 16
    assert stamp[8] == "T"
    assert stamp.endswith("Z")
    assert stamp[:8].isdigit()


def test_two_archives_in_the_same_second_do_not_overwrite(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """M-19 の再現防止（FR-047a）: 2 回目が `-2` になり 1 回目が残る。"""
    service = make_service(vault_root)
    service.write_outcome(folders, "o.md", "V1")
    first = str(service.write_outcome(folders, "o.md", "V2").archived_to)
    second = str(service.write_outcome(folders, "o.md", "V3").archived_to)
    assert first != second
    assert second.endswith("-2")
    assert (vault_root / first).read_text(encoding="utf-8") == "V1"
    assert (vault_root / second).read_text(encoding="utf-8") == "V2"
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "V3"


def test_archive_fails_when_the_sequence_is_exhausted(
    vault_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """連番の上限 999 を超えたら失敗する（C-2 (1)・FR-047a）。

    スタンプを固定して名前空間を埋める（実時間に依存させない）。
    """
    stamp = "20260824T000000Z"
    monkeypatch.setattr("vault.repository._utc_stamp", lambda: stamp)
    write_file(vault_root, "pj/outcome/o.md", "OLD")
    archive = _outcome(vault_root) / "_archive"
    archive.mkdir()
    (archive / f"o.md.{stamp}").write_text("x", encoding="utf-8")
    for n in range(2, ARCHIVE_SEQ_LIMIT + 1):
        (archive / f"o.md.{stamp}-{n}").write_text("x", encoding="utf-8")
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(
        rel="pj/outcome/o.md", area=VaultArea.outcome, area_rels=("pj/outcome",)
    )
    with pytest.raises(FsArchiveError):
        adapter.archive(resolved)
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "OLD"


def test_archive_error_carries_the_destination_only_when_it_is_decided(
    vault_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FsArchiveError.archive_rel` は退避先が確定した送出点でだけ埋まる（T087・data-model §4）。

    移動（`os.replace`）に失敗したときは退避先が決まっているので載る。退避先の親が不在の
    送出点では決まっていないので `None` のままである（値を捏造しない）。
    """
    stamp = "20260824T000000Z"
    monkeypatch.setattr("vault.repository._utc_stamp", lambda: stamp)
    write_file(vault_root, "pj/outcome/o.md", "OLD")
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(
        rel="pj/outcome/o.md", area=VaultArea.outcome, area_rels=("pj/outcome",)
    )

    def _boom(src: object, dst: object) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("vault.repository.os.replace", _boom)
    with pytest.raises(FsArchiveError) as moved:
        adapter.archive(resolved)
    assert moved.value.archive_rel == f"pj/outcome/_archive/o.md.{stamp}"
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "OLD"

    # 退避先が決まる前に落ちる送出点では `None` のまま
    absent = adapter.resolve(rel="pj/nope/o.md", area=VaultArea.outcome, area_rels=("pj/nope",))
    with pytest.raises(FsArchiveError) as undecided:
        adapter.archive(absent)
    assert undecided.value.archive_rel is None


def test_archive_does_not_create_the_parent(vault_root: Path) -> None:
    """親（`outcome/`）が不在なら `_archive/` を作らず失敗する（FR-014）。"""
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(rel="pj/nope/o.md", area=VaultArea.outcome, area_rels=("pj/nope",))
    before = dirs_under(vault_root)
    with pytest.raises(FsArchiveError):
        adapter.archive(resolved)
    assert dirs_under(vault_root) == before


def test_archive_failure_keeps_the_old_version_and_writes_nothing(
    vault_root: Path, folders: ProjectFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-048: 退避に失敗したら新版を書かず古い版を残し、**契約例外へ翻訳しない**。"""
    write_file(vault_root, "pj/outcome/o.md", "OLD")
    service = make_service(vault_root)

    def _boom(self: FsAdapter, resolved: object) -> str:
        raise FsArchiveError("退避に失敗した", rel="pj/outcome/o.md")

    monkeypatch.setattr("vault.repository.FsAdapter.archive", _boom)
    with pytest.raises(FsArchiveError):
        service.write_outcome(folders, "o.md", "NEW")
    assert (_outcome(vault_root) / "o.md").read_text(encoding="utf-8") == "OLD"


# ------------------------------------------------------------------- 原子的書込（T028）


def test_nothing_appears_when_the_replace_fails(
    vault_root: Path, folders: ProjectFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.replace` の**前**に失敗したら成果物の場所に 1 バイトも現れず、一時ファイルも残らない。"""
    service = make_service(vault_root)

    def _boom(src: object, dst: object) -> None:
        msg = "replace failed"
        raise OSError(msg)

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(FsIoError):
        service.write_outcome(folders, "o.md", "BODY")
    assert list(_outcome(vault_root).iterdir()) == []


def test_the_old_version_survives_in_the_archive_when_stage_5_fails(
    vault_root: Path, folders: ProjectFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-049 の「古い版があればそれを残す」は**退避先に残る**の意味である（T098・VD-49）。

    FR-045 が段の順を (4) 退避 → (5) 原子的書込 と固定しているため、段 5 に到達した時点で
    古い版は既に `outcome/` から `_archive/` へ**移動済み**である（FR-047「移動であって複製
    ではない」・SC-005「元の場所に古い版が残った事象が 0 件」）。したがって段 5 が失敗しても
    古い版は `outcome/` に戻らない。**裁定 2026-08-24（ユーザ決定）＝「古い版を残さない形で
    よい」** ―― 退避先から戻すロールバックは作らない（憲章 §10「自前の復旧機構を作らない」）。

    ⚠ **観測される帰結**: 完了ゲート `outcome_exists` が `True` → `False` に転じる。人間は
    `_archive/` から中身を取り戻せる。呼び手は `FsIoError` を受けるので「書けた」と誤解しない。

    US2/AC7（**退避**に失敗する状況）とは別物である ―― そちらは段 4 で止まるので古い版は元の
    場所に残り、`test_archive_failure_keeps_the_old_version_and_writes_nothing` が固定している。
    """
    service = make_service(vault_root)
    service.write_outcome(folders, "o.md", "OLD-VERSION")
    assert service.outcome_exists(folders, "o.md") is True

    real_replace = os.replace
    seen = {"calls": 0}

    def _fail_the_second_replace(src: object, dst: object) -> None:
        # 1 回目＝退避（成功させる）／2 回目＝本書込の差し替え（失敗させる）
        seen["calls"] += 1
        if seen["calls"] >= 2:
            raise OSError(errno.ENOSPC, "no space left on device")
        real_replace(src, dst)  # pyright: ignore[reportArgumentType]  # 実引数は Path（テストの差し替え用シグネチャ）

    monkeypatch.setattr("vault.repository.os.replace", _fail_the_second_replace)
    with pytest.raises(FsIoError) as exc:
        service.write_outcome(folders, "o.md", "NEW-VERSION")
    assert exc.value.op == "replace"

    # (1) 成果物フォルダに中途半端な内容も一時ファイルも残らない（FR-049 前段）
    assert [p.name for p in _outcome(vault_root).iterdir()] == ["_archive"]
    # (2) 古い版は**失われていない** ―― 退避先に 1 件だけ、中身も無傷（FR-049 後段の読み）
    archived = list((_outcome(vault_root) / "_archive").iterdir())
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "OLD-VERSION"
    # (3) 完了ゲートは実ファイルの有無で答える（US5）。退避済みは数えない（FR-043）
    assert service.outcome_exists(folders, "o.md") is False


def test_temp_file_is_created_in_the_same_directory(
    vault_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-17: FS をまたぐ `os.replace` は `EXDEV`。一時ファイルは**書込先と同一ディレクトリ**。"""
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(
        rel="pj/outcome/o.md", area=VaultArea.outcome, area_rels=("pj/outcome",)
    )
    seen: list[Path] = []
    real_replace = os.replace

    def _spy(src: object, dst: object) -> None:
        seen.append(Path(str(src)))
        real_replace(str(src), str(dst))

    monkeypatch.setattr(os, "replace", _spy)
    adapter.write_atomic(resolved, b"BODY")
    assert seen[0].parent == _outcome(vault_root)
    assert seen[0].name.startswith(TMP_PREFIX)


def test_temp_files_are_invisible_to_list_and_exists(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """残った一時ファイルは `list_inputs` にも `outcome_exists` にも現れない（FR-050）。"""
    leftover = _outcome(vault_root) / f"{TMP_PREFIX}deadbeef"
    leftover.write_text("LEFTOVER", encoding="utf-8")
    service = make_service(vault_root)
    listed = [ref.rel_path for ref in service.list_inputs(folders, PersonaKey.planner)]
    assert listed == []
    with pytest.raises(VaultPathViolation):
        service.outcome_exists(folders, leftover.name)


def test_write_atomic_returns_the_byte_count(vault_root: Path) -> None:
    adapter = make_adapter(vault_root)
    resolved = adapter.resolve(
        rel="pj/outcome/o.md", area=VaultArea.outcome, area_rels=("pj/outcome",)
    )
    assert adapter.write_atomic(resolved, b"12345") == 5
