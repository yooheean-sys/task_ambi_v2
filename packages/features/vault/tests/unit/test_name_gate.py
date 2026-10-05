"""ファイル名ゲートの表駆動テスト（US2 AC4・SC-006・contracts/name-gate.md §1 の 10 規則）。

各ケースで (a) **何も書かれない**、(b) `detail` から該当文字と規則が読める、
(c) **置換・除去・切り詰めが起きていない**（`outcome/` にファイルが 1 件も増えない）を見る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vault_testing import make_service, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import VaultPathViolation
from common.kernel.enums import PersonaKey
from vault import VaultConfig

# (名前, 期待する code, 期待する token, 畳んだ kind)
REJECTED: list[tuple[str, str, str, str]] = [
    # #0 空の名前
    ("", "empty_name", "", "forbidden_name"),
    # #1 パス区切り
    ("a/b.md", "path_separator", "/", "forbidden_name"),
    ("a\\b.md", "path_separator", "\\", "forbidden_name"),
    # #2 Windows/mac 非互換文字（CFG-27 の 9 種）
    ("a:b.md", "forbidden_char", ":", "forbidden_name"),
    ("a*b.md", "forbidden_char", "*", "forbidden_name"),
    ("a?b.md", "forbidden_char", "?", "forbidden_name"),
    ('a"b.md', "forbidden_char", '"', "forbidden_name"),
    ("a<b.md", "forbidden_char", "<", "forbidden_name"),
    ("a>b.md", "forbidden_char", ">", "forbidden_name"),
    # #3 制御文字
    ("a\x01b.md", "control_char", "\\x01", "forbidden_name"),
    ("a\x7fb.md", "control_char", "\\x7f", "forbidden_name"),
    # #4 Windows 予約語（拡張子付き・大文字小文字を問わず）
    ("CON", "reserved_word", "CON", "reserved"),
    ("con.md", "reserved_word", "con", "reserved"),
    ("COM1.md", "reserved_word", "COM1", "reserved"),
    ("lpt9.txt", "reserved_word", "lpt9", "reserved"),
    ("NUL", "reserved_word", "NUL", "reserved"),
    # 語幹は**最初のドットより前**（多重拡張子でも予約語は解決される＝ Windows の実挙動）
    ("CON.tar.gz", "reserved_word", "CON", "reserved"),
    ("NUL.d.ts", "reserved_word", "NUL", "reserved"),
    ("LPT1.a.b", "reserved_word", "LPT1", "reserved"),
    ("con.backup.md", "reserved_word", "con", "reserved"),
    # #5 末尾のドット・空白
    ("a.md.", "trailing_dot_space", ".", "forbidden_name"),
    ("a.md ", "trailing_dot_space", " ", "forbidden_name"),
    ("a.md\t", "trailing_dot_space", "\\x09", "forbidden_name"),
    # #6 Obsidian（`%%` を含む）
    ("a#b.md", "obsidian_char", "#", "forbidden_name"),
    ("a^b.md", "obsidian_char", "^", "forbidden_name"),
    ("a[b.md", "obsidian_char", "[", "forbidden_name"),
    ("a]b.md", "obsidian_char", "]", "forbidden_name"),
    ("a%%b.md", "obsidian_char", "%%", "forbidden_name"),
    # #7 除外接頭辞（ドット始まり。`.`・`..` もここで落ちる）
    (".hidden.md", "excluded_prefix", ".", "forbidden_name"),
    (".", "excluded_prefix", ".", "forbidden_name"),
    ("..", "excluded_prefix", ".", "forbidden_name"),
    (".vaulttmp-deadbeef", "excluded_prefix", ".", "forbidden_name"),
    # #8 退避先フォルダ名（完全一致のみ）
    ("_archive", "archive_dir_name", "_archive", "reserved"),
    # #9 長さ
    ("a" * 198 + ".md", "too_long", "201 bytes > 200", "forbidden_name"),
]

ACCEPTED = [
    "a.md",
    "_archive_notes.md",  # `archive_dir` は**接頭辞ではなく完全一致**（name-gate §2）
    "CONSOLE.md",  # 予約語は**語幹の完全一致**であって部分一致ではない
    "CONSOLE.tar.gz",  # 多重拡張子でも語幹の完全一致でなければ通る
    "a%b.md",  # `%` 単独は禁止でない（禁止は `%%`）
    "日本語の成果物.md",
    "a" * 197 + ".md",  # 上限ちょうど（200 バイト）は通る
]


@pytest.mark.parametrize(("filename", "code", "token", "kind"), REJECTED)
def test_name_gate_rejects_and_writes_nothing(
    vault_root: Path,
    folders: ProjectFolders,
    filename: str,
    code: str,
    token: str,
    kind: str,
) -> None:
    service = make_service(vault_root)
    outcome = vault_root / "pj" / "outcome"
    before = set(outcome.iterdir())
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, filename, "BODY")
    assert exc.value.kind == kind
    assert code in exc.value.detail
    assert token in exc.value.detail
    assert set(outcome.iterdir()) == before  # (a)(c) 何も書かれない・置換されない


@pytest.mark.parametrize("filename", ACCEPTED)
def test_name_gate_accepts(vault_root: Path, folders: ProjectFolders, filename: str) -> None:
    service = make_service(vault_root)
    written = service.write_outcome(folders, filename, "BODY")
    assert written.rel_path == f"pj/outcome/{filename}"
    assert (vault_root / "pj" / "outcome" / filename).read_text(encoding="utf-8") == "BODY"


def test_name_gate_collects_every_violation(vault_root: Path, folders: ProjectFolders) -> None:
    """**早期打ち切りしない**（FR-031）。`reserved` 系が 1 つでもあれば `kind="reserved"`。"""
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "CON.m:%%d ", "BODY")
    assert exc.value.kind == "reserved"
    for code in ("forbidden_char", "reserved_word", "obsidian_char", "trailing_dot_space"):
        assert code in exc.value.detail


def test_name_gate_never_reuses_too_large_for_a_long_name(
    vault_root: Path, folders: ProjectFolders
) -> None:
    """**`too_large` を名前長に流用しない**（内容のバイト数の語であるため）。"""
    service = make_service(vault_root)
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "a" * 198 + ".md", "BODY")
    assert exc.value.kind == "forbidden_name"
    assert "too_long" in exc.value.detail


def test_name_gate_length_is_counted_in_bytes(vault_root: Path, folders: ProjectFolders) -> None:
    """長さはバイト数で数える（CFG-31 の単位）。"""
    service = make_service(vault_root, VaultConfig(max_filename_bytes=10))
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "あいうえ.md", "BODY")  # 12 + 3 = 15 バイト
    assert "15 bytes > 10" in exc.value.detail


def test_archive_dir_name_comes_from_config(vault_root: Path, folders: ProjectFolders) -> None:
    """規則 #8 は `archive_dir` を**唯一の出所**とする（FR-030・R-10）。"""
    service = make_service(vault_root, VaultConfig(archive_dir="_old"))
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "_old", "BODY")
    assert exc.value.kind == "reserved"
    assert "archive_dir_name" in exc.value.detail
    # 既定の `_archive` は、`archive_dir` を変えた設定では予約されない
    assert service.write_outcome(folders, "_archive", "BODY").rel_path == "pj/outcome/_archive"


def test_excluded_prefix_comes_from_config(vault_root: Path, folders: ProjectFolders) -> None:
    """規則 #7 は `list_exclude_prefixes` を**唯一の出所**とする（R-10）。"""
    service = make_service(vault_root, VaultConfig(list_exclude_prefixes=["~"]))
    with pytest.raises(VaultPathViolation) as exc:
        service.write_outcome(folders, "~draft.md", "BODY")
    assert "excluded_prefix" in exc.value.detail
    # 既定のドット始まりは、接頭辞を差し替えた設定では拒まれない
    assert service.write_outcome(folders, ".dot.md", "BODY").rel_path == "pj/outcome/.dot.md"


def test_name_gate_does_not_apply_to_list_inputs(vault_root: Path, folders: ProjectFolders) -> None:
    """name-gate §4: 名前を受けない 3 メソッドには掛けない。"""
    write_file(vault_root, "pj/a#b.md", "X")
    service = make_service(vault_root)
    assert [r.rel_path for r in service.list_inputs(folders, PersonaKey.planner)] == ["pj/a#b.md"]
