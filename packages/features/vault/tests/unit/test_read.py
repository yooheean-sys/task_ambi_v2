"""`read_input`・`read_outcome_file`・`outcome_exists` の分岐（US1・US5・SC-007）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from vault_testing import log_extra, make_service, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import FileRef, VaultArea, VaultNotFound, VaultPathViolation
from common.kernel.enums import PersonaKey
from vault import VaultConfig


def _ref(area: VaultArea, rel_path: str) -> FileRef:
    return FileRef(
        area=area,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        size_bytes=0,
        mtime=datetime.now(tz=UTC),
    )


# --------------------------------------------------------------------------- read_input


def test_read_input_returns_text(vault_root: Path, folders: ProjectFolders) -> None:
    write_file(vault_root, "pj/a.md", "こんにちは")
    service = make_service(vault_root)
    assert service.read_input(folders, _ref(VaultArea.pj_input, "pj/a.md")) == "こんにちは"


def test_read_input_missing_raises_vault_not_found(
    vault_root: Path, folders: ProjectFolders
) -> None:
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.read_input(folders, _ref(VaultArea.pj_input, "pj/nope.md"))
    assert exc.value.rel_path == "pj/nope.md"
    assert exc.value.role is None


def test_read_input_directory_is_not_found(vault_root: Path, folders: ProjectFolders) -> None:
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound):
        service.read_input(folders, _ref(VaultArea.pj_input, "pj/sub"))


def test_read_input_rejects_undecodable_bytes(vault_root: Path, folders: ProjectFolders) -> None:
    """UTF-8 で復号できない → `not_text`（**途中までの文字列を返さない**）。"""
    (vault_root / "pj" / "bin.md").write_bytes(b"ok\xff\xfetail")
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(VaultArea.pj_input, "pj/bin.md"))
    assert exc.value.kind == "not_text"
    assert "utf-8" in exc.value.detail
    assert "ok" not in exc.value.detail.replace("pj/bin.md", "")


def test_read_input_applies_the_size_limit(vault_root: Path, folders: ProjectFolders) -> None:
    """`read_input` には上限を掛ける（`read_outcome_file` とは非対称＝ R-15）。"""
    write_file(vault_root, "pj/big.md", "abcdef")
    service = make_service(vault_root, VaultConfig(max_outcome_bytes=5))
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(VaultArea.pj_input, "pj/big.md"))
    assert exc.value.kind == "too_large"
    assert "6" in exc.value.detail
    assert "5" in exc.value.detail


def test_read_input_role_area_is_the_union_of_declared_role_dirs(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """FR-026a・C-4: 分離は**呼び手**が行う（vault は宣言の和集合を領域とする）。"""
    write_file(vault_root, "pj/roles/writer/guide.md", "WRITER")
    service = make_service(vault_root)
    # 絞っていない宣言では、planner として動く実行でも writer の手引きが読める
    assert service.read_input(folders, _ref(VaultArea.role, "pj/roles/writer/guide.md")) == "WRITER"
    # 呼び手が当該実行の役割だけに絞れば領域外になる
    narrowed = folders.model_copy(
        update={"role_dirs": {PersonaKey.planner: folders.role_dirs[PersonaKey.planner]}}
    )
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(narrowed, _ref(VaultArea.role, "pj/roles/writer/guide.md"))
    assert exc.value.kind == "outside_area"


def test_read_input_does_not_apply_the_name_gate(vault_root: Path, folders: ProjectFolders) -> None:
    """name-gate §4: `read_input` は名前ゲートを掛けない（守るのは領域内での再解決）。"""
    write_file(vault_root, "pj/.hidden.md", "H")
    service = make_service(vault_root)
    assert service.read_input(folders, _ref(VaultArea.pj_input, "pj/.hidden.md")) == "H"


# --------------------------------------------------------------------- read_outcome_file


def test_read_outcome_file_returns_text(vault_root: Path, folders: ProjectFolders) -> None:
    write_file(vault_root, "pj/outcome/o.md", "OUT")
    service = make_service(vault_root)
    assert service.read_outcome_file(folders, "o.md") == "OUT"


def test_read_outcome_file_missing_raises_instead_of_empty_string(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """不在 → `VaultNotFound`（**空文字列を返さない**＝ FN-13）。"""
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.read_outcome_file(folders, "nope.md")
    assert exc.value.rel_path == "pj/outcome/nope.md"


@pytest.mark.parametrize(
    ("filename", "kind"),
    [
        ("a:b.md", "forbidden_name"),
        ("CON.md", "reserved"),
        ("_archive", "reserved"),
        (".hidden.md", "forbidden_name"),
    ],
)
def test_read_outcome_file_applies_the_name_gate(
    vault_root: Path, folders: ProjectFolders, filename: str, kind: str
) -> None:
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_outcome_file(folders, filename)
    assert exc.value.kind == kind


def test_read_outcome_file_rejects_a_path_that_leaves_the_area(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """`filename` にパス区切りがあれば名前ゲートで止まる（領域外へ出る前）。"""
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_outcome_file(folders, "../a.md")
    assert exc.value.kind == "forbidden_name"
    assert "path_separator" in exc.value.detail


def test_read_outcome_file_is_outside_area_when_outcome_dir_escapes(vault_root: Path) -> None:
    """宣言そのものが領域の外を指すときは `outside_root`（不在に倒さない）。"""
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="../outside",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_outcome_file(declared, "secret.md")
    assert exc.value.kind == "outside_root"


def test_read_outcome_file_rejects_undecodable_bytes(
    vault_root: Path, folders: ProjectFolders
) -> None:
    (vault_root / "pj" / "outcome" / "bin.md").write_bytes(b"\xff\xfe")
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_outcome_file(folders, "bin.md")
    assert exc.value.kind == "not_text"


def test_read_outcome_file_has_no_size_limit(vault_root: Path, folders: ProjectFolders) -> None:
    """**サイズ上限を掛けない**（R-15・VD-20。`read_input` 経由とは非対称）。"""
    write_file(vault_root, "pj/outcome/big.md", "abcdef")
    service = make_service(vault_root, VaultConfig(max_outcome_bytes=5))
    assert service.read_outcome_file(folders, "big.md") == "abcdef"
    # 同じファイルを read_input 経由で読むと上限が掛かる（設計どおりの非対称）
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(VaultArea.outcome, "pj/outcome/big.md"))
    assert exc.value.kind == "too_large"


# ------------------------------------------------------------------------ outcome_exists


def test_outcome_exists_true_for_an_existing_file(
    vault_root: Path, folders: ProjectFolders
) -> None:
    write_file(vault_root, "pj/outcome/o.md", "OUT")
    service = make_service(vault_root)
    assert service.outcome_exists(folders, "o.md") is True


def test_outcome_exists_true_for_an_empty_file(vault_root: Path, folders: ProjectFolders) -> None:
    """**中身が空のファイルも `True`**（FR-043）。"""
    write_file(vault_root, "pj/outcome/empty.md", "")
    service = make_service(vault_root)
    assert service.outcome_exists(folders, "empty.md") is True


def test_outcome_exists_false_when_missing(vault_root: Path, folders: ProjectFolders) -> None:
    service = make_service(vault_root)
    assert service.outcome_exists(folders, "nope.md") is False


def test_outcome_exists_does_not_count_archived_versions(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """`_archive/` にだけ同名 → `False`（退避済みを数えない）。"""
    write_file(vault_root, "pj/outcome/_archive/o.md", "OLD")
    service = make_service(vault_root)
    assert service.outcome_exists(folders, "o.md") is False


def test_outcome_exists_does_not_fall_back_to_false_on_a_bad_name(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """名前ゲート不合格 → **`VaultPathViolation`（`False` に倒さない）**＝ FR-044。"""
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.outcome_exists(folders, "a?b.md")
    assert exc.value.kind == "forbidden_name"


def test_outcome_exists_rejects_a_declaration_that_leaves_the_root(vault_root: Path) -> None:
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="../outside",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.outcome_exists(declared, "secret.md")
    assert exc.value.kind == "outside_root"


# ------------------------------------------------- 領域フォルダ不在（T076・T077・VD-34）


def test_read_outcome_file_missing_outcome_dir_is_not_found(vault_root: Path) -> None:
    """`outcome_dir` 不在は「領域の外」ではなく「対象が無い」（FR-042・VD-19 と同じ読み）。"""
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/nope",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.read_outcome_file(declared, "o.md")
    assert exc.value.rel_path == "pj/nope"
    assert exc.value.role is None


def test_outcome_exists_is_false_when_the_outcome_dir_is_missing(vault_root: Path) -> None:
    """完了ゲートは「フォルダをまだ人間が作っていない」を **False** で答える（FR-043・US5）。

    人間が `outcome/` を作るまでは正常状態である（FR-014）ため、この経路は通常運用で踏む。
    SC-007 が「不在を偽に倒さない」と定める 3 経路（成果物の読取・入力の一覧・入力の読取）に
    存在確認は入っていない（存在確認は**名前の違反**を偽に倒さないことだけを求められている）。
    """
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/nope",
    )
    service = make_service(vault_root)
    assert service.outcome_exists(declared, "o.md") is False


def test_outcome_exists_still_rejects_a_bad_name_when_the_outcome_dir_is_missing(
    vault_root: Path,
) -> None:
    """名前ゲートは領域の実在より**前**にある（SC-007 の後段を不在で迂回させない）。"""
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/nope",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.outcome_exists(declared, "a?b.md")
    assert exc.value.kind == "forbidden_name"


def test_outcome_exists_leaves_no_warning_when_the_outcome_dir_is_missing(
    vault_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """握り潰す不在は WARNING を残さない（T089・VD-41）。

    人間が `outcome/` を作るまでは正常状態（FR-014・SC-004）で、完了ゲートは 1 kickoff の
    中で本メソッドを繰り返し呼ぶ。ここに WARNING が積まれると US7「事後に追える唯一の手段
    がログ」の可読性が落ちる（VD-37 が塞いだ「痕跡が無い」の**逆側**の失敗）。情報は残る
    INFO 行が持つので失われない。
    """
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner", PersonaKey.writer: "pj/roles/writer"},
        outcome_dir="pj/nope",
    )
    service = make_service(vault_root)
    with caplog.at_level(logging.INFO, logger="vault.service"):
        assert service.outcome_exists(declared, "o.md") is False
    records = [r for r in caplog.records if r.name == "vault.service"]
    assert [r.levelno for r in records] == [logging.INFO]
    assert records[0].getMessage() == "vault.outcome_exists"
    assert log_extra(records[0], "rel_path") == "pj/nope"
    assert log_extra(records[0], "result") is False


def test_missing_area_root_still_warns_where_the_caller_sees_it(
    vault_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """level を一律に下げていない: **呼び手に届く**不在は WARNING のまま（T089・VD-41）。

    `outcome_exists` と同じ `_require_inside` の分岐を `read_outcome_file`・`read_input` も
    通るが、そちらでは `VaultNotFound` が呼び手へ届くので記録が要る。
    """
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/nope", PersonaKey.writer: "pj/roles/writer"},
        outcome_dir="pj/gone",
    )
    service = make_service(vault_root)
    with caplog.at_level(logging.INFO, logger="vault.service"):
        with pytest.raises(VaultNotFound):
            service.read_outcome_file(declared, "o.md")
        with pytest.raises(VaultNotFound):
            service.read_input(declared, _ref(VaultArea.role, "pj/nope/a.md"))
    warned = [
        r for r in caplog.records if r.name == "vault.service" and r.levelno == logging.WARNING
    ]
    assert [log_extra(r, "rel_path") for r in warned] == ["pj/gone", "pj/nope"]
    assert {log_extra(r, "result") for r in warned} == {"not_found"}


def test_read_input_missing_role_dir_is_not_found(vault_root: Path) -> None:
    """役割フォルダ不在は `list_inputs` と同じく `VaultNotFound`（T077）。"""
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/nope"},
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.read_input(declared, _ref(VaultArea.role, "pj/nope/a.md"))
    assert exc.value.rel_path == "pj/nope"
    assert exc.value.role is None
    # 対で読む: 同じ宣言・同じ不在に対して `list_inputs` が上げるものと形が揃っている
    with pytest.raises(VaultNotFound) as listed:
        service.list_inputs(declared, PersonaKey.planner)
    assert (listed.value.rel_path, listed.value.role) == (exc.value.rel_path, exc.value.role)


def test_outside_area_still_wins_when_the_area_root_exists(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """領域根が**実在する**なら、領域の外を指す参照は従来どおり `outside_area`（退行検査）。"""
    write_file(vault_root / "other/x.md", "OTHER")
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(folders, _ref(VaultArea.pj_input, "other/x.md"))
    assert exc.value.kind == "outside_area"


def test_read_input_missing_role_dir_is_not_found_with_multiple_roles(vault_root: Path) -> None:
    """役割を 2 つ宣言し、**対象の役割だけ**が不在のとき（T079・VD-35）。

    `role_dirs` が複数なのは例外ではなく**既定の構成**である（`folders` fixture も 2 件・
    `PersonaKey` は 9 種）。和集合で「1 つでも在れば領域は在る」と判定すると、ここで
    `outside_area` に戻ってしまう。
    """
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={
            PersonaKey.planner: "pj/nope",  # 不在
            PersonaKey.writer: "pj/roles/writer",  # 実在
        },
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultNotFound) as exc:
        service.read_input(declared, _ref(VaultArea.role, "pj/nope/a.md"))
    assert exc.value.rel_path == "pj/nope"
    assert exc.value.role is None
    # 対で読む: 同じ宣言・同じ不在に対して `list_inputs` が上げるものと形が揃っている
    with pytest.raises(VaultNotFound) as listed:
        service.list_inputs(declared, PersonaKey.planner)
    assert (listed.value.rel_path, listed.value.role) == (exc.value.rel_path, exc.value.role)


def test_area_root_outside_the_vault_is_not_reported_as_missing(vault_root: Path) -> None:
    """ルート外に**実在する**領域根を「実在しない」と報告しない（T080・VD-35）。

    `../outside` は `vault_root` fixture が実際に作るディレクトリである。実在するのだから
    「不在」ではなく、問題は境界である。同じ宣言に対して `check_project_folders` が返すのも
    境界の違反であり、両者は矛盾しない。
    """
    declared = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "../outside"},
        outcome_dir="pj/outcome",
    )
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.read_input(declared, _ref(VaultArea.role, "pj/sub/x.md"))
    assert exc.value.kind == "outside_area"
    with pytest.raises(VaultPathViolation) as checked:
        service.check_project_folders(declared)
    assert checked.value.kind == "outside_root"
