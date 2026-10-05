"""US1: 領域 A の**書込側**（`AreaAWriter`・直列化 3 種）。contracts/save-pipeline.md §3・§6。

検証行: S-11（YAML の往復）・S-12（引用符を付けない）・S-13（退避は上書きしない）
＋ FR-016・FR-017・FR-026・FR-030〜FR-032・SC-004 (d)・SC-011。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from settings_testing import temp_files, write_minimal_area_a

from settings import defaults_path
from settings.repository import (
    AreaAWriter,
    dump_policy_rows,
    dump_text,
    dump_yaml_mapping,
    load_yaml_mapping,
    new_enforcer,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# -- S-11 YAML の往復検証（FR-028。**専用の検証ローダを新設しない**＝ R-08） ------------

ROUND_TRIP_CASES: tuple[tuple[str, Mapping[str, object]], ...] = (
    (
        "global/agents.yaml",
        {
            "programmer": {
                "role": "実装者",
                "goal": "作る",
                "backstory": "背景",
                "tools": ["read_issue"],
            }
        },
    ),
    (
        "global/tasks.yaml",
        {
            "plain": {
                "description": "{acceptance} を満たす",
                "expected_output": "成果",
                "agent": None,
            }
        },
    ),
    (
        "global/llm_profiles.yaml",
        {
            "profiles": {
                "default": {
                    "provider_kind": "openai_compatible",
                    "base_url": "http://127.0.0.1:8080/v1",
                    "model_id": "demo",
                    "context_window": 32768,
                    "api_key_ref": "DEMO_API_KEY_REF",
                    "extra_headers": {"X-Demo": "1"},
                }
            },
            "default": "default",
        },
    ),
    (
        "global/limits.yaml",
        {"overrides": {"pj": {"max_requests_per_kickoff": 10, "max_cost_per_kickoff": 0.5}}},
    ),
    ("global/commands.yaml", {"commands": {"run_tests": {"argv": ["uv", "run", "pytest"]}}}),
    ("global/humans.yaml", {"human_actors": ["人間 A", "human-b"]}),
    (
        "projects/pj/project.yaml",
        {
            "name": "デモ プロジェクト",
            "is_reserved": False,
            "folders": {
                "pj_input_dir": "pj",
                "role_dirs": {"programmer": "pj/roles/programmer"},
                "outcome_dir": "pj/outcome",
            },
        },
    ),
    (
        "projects/pj/hooks.yaml",
        {"hooks": [{"name": "h", "points": "PRE_TOOL_CALL", "agents": ["programmer"]}]},
    ),
)


@pytest.mark.parametrize(
    ("rel", "data"), ROUND_TRIP_CASES, ids=[rel for rel, _ in ROUND_TRIP_CASES]
)
def test_s11_yaml_round_trips_through_the_reader(rel: str, data: Mapping[str, object]) -> None:
    """S-11・FR-028: 書いたテキストを**前半の重複キー検出ローダ**で読み直して同値になる。"""
    text = dump_yaml_mapping(data)

    assert load_yaml_mapping(text, rel) == dict(data)


def test_s11_non_ascii_is_not_escaped() -> None:
    """`allow_unicode=True`（D-g）: 日本語がそのままの文字で出る（エスケープされない）。"""
    text = dump_yaml_mapping({"name": "デモ"})

    assert "デモ" in text


def test_s11_key_order_is_preserved() -> None:
    """`sort_keys=False`（D-g）: 宣言の並びを保つ（差分を読みやすく保つため）。"""
    text = dump_yaml_mapping({"zeta": 1, "alpha": 2, "middle": 3})

    assert [line.split(":")[0] for line in text.splitlines()] == ["zeta", "alpha", "middle"]


def test_s11_nested_mappings_are_block_style() -> None:
    """`default_flow_style=False`（D-g）: 入れ子が `{...}` の 1 行にならない。"""
    text = dump_yaml_mapping({"folders": {"pj_input_dir": "pj"}})

    assert "{" not in text


def test_s11_multiline_strings_are_block_scalars() -> None:
    """D26: 改行を含む文字列は `|` で書く（tasks.yaml 冒頭の規約 Y-6・M-15 (b)）。

    末尾の改行の有無は PyYAML が `|`／`|-` を選び分ける。
    """
    data = {"plain": {"description": "1 行目 {title}\n\n3 行目\n", "expected_output": "a\nb"}}

    text = dump_yaml_mapping(data)

    assert "description: |\n" in text
    assert "expected_output: |-\n" in text
    assert "\\n" not in text
    assert load_yaml_mapping(text, "global/tasks.yaml") == data


def test_s11_unblockable_strings_fall_back_to_quotes() -> None:
    """D26: 末尾空白・タブ・CR を含む文字列は `|` で表せないので、引用符に落ちて値は保たれる。"""
    data = {"a": "行 \n次\n", "b": "\tx\ny\n", "c": "a\r\nb\n"}

    text = dump_yaml_mapping(data)

    assert "|" not in text
    assert load_yaml_mapping(text, "global/tasks.yaml") == data


def test_s11_single_line_strings_stay_plain() -> None:
    """D26: 改行の無い文字列は従来どおり（`|` にしない）。"""
    assert dump_yaml_mapping({"name": "デモ"}) == "name: デモ\n"


def test_s11_shipped_tasks_keep_block_scalars_after_a_rewrite() -> None:
    """D26: 出荷時既定の tasks.yaml を読んで書き直しても、複数行の値は 1 つも引用符に落ちない。"""
    rel = "global/tasks.yaml"
    data = load_yaml_mapping((defaults_path() / "tasks.yaml").read_text(encoding="utf-8"), rel)

    text = dump_yaml_mapping(data)

    assert "\\n" not in text
    assert load_yaml_mapping(text, rel) == data


# -- S-12 権限表の書出し（FR-029・D-h。**引用符を付けない**） ---------------------------


def test_s12_policy_rows_have_no_quotes() -> None:
    """S-12・FR-029: `csv` モジュールを使わず自前で組むので引用符が 1 つも付かない。"""
    text = dump_policy_rows(
        [
            ("p", "member:pj.programmer", "tool:read_issue", "execute"),
            ("g", "member:pj.qa", "role_x"),
        ]
    )

    assert '"' not in text
    assert "'" not in text
    assert text == "p, member:pj.programmer, tool:read_issue, execute\ng, member:pj.qa, role_x\n"


def test_s12_a_comma_inside_a_value_becomes_a_column_count_violation(settings_root: Path) -> None:
    """値にカンマが混じった行は**候補の判定器で列数違反として捕まる**（D-h の帰結）。"""
    write_minimal_area_a(settings_root)
    target = settings_root / "projects/pj/policy.csv"
    target.write_text(
        dump_policy_rows([("p", "member:pj.programmer", "tool:a,b", "execute")]),
        encoding="utf-8",
        newline="\n",
    )

    enforcer = new_enforcer(settings_root / "global/model.conf", target)

    assert [len(row) for row in enforcer.get_policy()] == [4], "カンマが列を 1 つ増やしている"


def test_dump_text_adds_a_trailing_newline() -> None:
    """`dump_text`: 末尾に改行が無ければ 1 つ足す。それ以外は素通し。"""
    assert dump_text("本文") == "本文\n"
    assert dump_text("本文\n") == "本文\n"
    assert dump_text("") == ""


# -- `AreaAWriter` の 2 段書込（FR-016・FR-017・D-e） -----------------------------------


def test_stage_writes_beside_the_target_and_does_not_replace(settings_root: Path) -> None:
    """FR-016・D-e: 一時ファイルは**置換先と同じディレクトリ**に作られ、正本は変わらない。"""
    write_minimal_area_a(settings_root)
    target = settings_root / "global/humans.yaml"
    before = target.read_bytes()
    writer = AreaAWriter(settings_root)

    temporary = writer.stage("global/humans.yaml", "human_actors:\n  - x\n")

    assert temporary.parent == target.parent, "os.replace はファイルシステムをまたげない"
    assert target.read_bytes() == before, "第 1 段では正本を書き換えない"
    assert temp_files(settings_root) == [str(temporary.relative_to(settings_root))]


def test_stage_uses_utf8_and_lf(settings_root: Path) -> None:
    """FR-017: 文字コードは常に `utf-8`、改行は LF（プラットフォーム既定に任せない）。"""
    write_minimal_area_a(settings_root)
    writer = AreaAWriter(settings_root)

    temporary = writer.stage("global/humans.yaml", "human_actors:\n  - 日本語\n")

    assert temporary.read_bytes() == "human_actors:\n  - 日本語\n".encode()


def test_commit_replaces_and_discard_removes(settings_root: Path) -> None:
    """第 2 段の `os.replace` で正本が入れ替わり、`discard` で一時ファイルが残らない。"""
    write_minimal_area_a(settings_root)
    writer = AreaAWriter(settings_root)
    temporary = writer.stage("global/humans.yaml", "human_actors: []\n")

    writer.commit("global/humans.yaml", temporary)

    assert (settings_root / "global/humans.yaml").read_text(
        encoding="utf-8"
    ) == "human_actors: []\n"
    assert temp_files(settings_root) == []


def test_failure_before_commit_keeps_the_old_file(settings_root: Path) -> None:
    """FR-016: 置換の前に落ちたら**旧ファイルが残る**（`discard` で片付けられる）。"""
    write_minimal_area_a(settings_root)
    target = settings_root / "global/humans.yaml"
    before = target.read_bytes()
    writer = AreaAWriter(settings_root)
    temporary = writer.stage("global/humans.yaml", "human_actors:\n  - 途中で落ちた\n")

    writer.discard(temporary)

    assert target.read_bytes() == before
    assert temp_files(settings_root) == []


def test_discard_is_tolerant_of_a_missing_file(settings_root: Path) -> None:
    """`discard` は不在でも失敗しない（`finally` から二重に呼ばれても壊れない）。"""
    writer = AreaAWriter(settings_root)

    writer.discard(settings_root / "does-not-exist")


def test_remove_deletes_the_original_and_tolerates_absence(settings_root: Path) -> None:
    """`remove`: 正本を消す（PJ の `tasks.yaml` の解除）。不在なら何もしない。"""
    write_minimal_area_a(settings_root)
    (settings_root / "projects/pj/tasks.yaml").write_text("plain: {}\n", encoding="utf-8")
    writer = AreaAWriter(settings_root)

    writer.remove("projects/pj/tasks.yaml")
    writer.remove("projects/pj/tasks.yaml")

    assert not (settings_root / "projects/pj/tasks.yaml").exists()


# -- 段 3-0 の `projects/<key>/`（plan D-j・SC-004 (d)） --------------------------------


def test_stage_fails_when_the_parent_directory_is_absent(settings_root: Path) -> None:
    """**3-0 が要る理由の固定**: 親が無いまま `stage` を呼ぶと落ちる（裁定 2026-08-27）。"""
    write_minimal_area_a(settings_root)
    writer = AreaAWriter(settings_root)

    with pytest.raises(OSError, match="No such file or directory"):
        writer.stage("projects/newpj/project.yaml", "name: 新規\n")


def test_ensure_project_dir_refuses_an_existing_directory(settings_root: Path) -> None:
    """`exist_ok=False`（data-model §6-1）: 重複キーは `key_rule` が先に落とすので到達しない。"""
    write_minimal_area_a(settings_root)
    writer = AreaAWriter(settings_root)

    writer.ensure_project_dir("newpj")
    assert (settings_root / "projects/newpj").is_dir()

    with pytest.raises(FileExistsError):
        writer.ensure_project_dir("newpj")


def test_discard_project_dir_removes_only_an_empty_directory(settings_root: Path) -> None:
    """SC-004 (d)・plan D-j: **空のときだけ**消し、中身があれば消さず、不在なら何もしない。"""
    write_minimal_area_a(settings_root)
    writer = AreaAWriter(settings_root)
    writer.ensure_project_dir("newpj")

    writer.discard_project_dir("newpj")
    assert not (settings_root / "projects/newpj").exists()

    writer.discard_project_dir("newpj")  # 不在でも失敗しない

    writer.discard_project_dir("pj")  # 中身がある
    assert (settings_root / "projects/pj/project.yaml").is_file()


# -- S-13 退避（FR-030〜FR-032・SC-010・SC-011） ----------------------------------------


def test_s13_archive_moves_every_file_and_leaves_nothing_behind(settings_root: Path) -> None:
    """SC-011・FR-030: `_archive/projects/<key>.<stamp>/` へ **rename** され全ファイルが揃う。"""
    write_minimal_area_a(settings_root)
    before = sorted(
        path.name for path in (settings_root / "projects/pj").iterdir() if path.is_file()
    )
    writer = AreaAWriter(settings_root)

    rel = writer.archive_project("pj", "20260827T000000Z")

    assert rel == "_archive/projects/pj.20260827T000000Z"
    moved = settings_root / rel
    assert sorted(path.name for path in moved.iterdir() if path.is_file()) == before
    assert not (settings_root / "projects/pj").exists()


def test_s13_archive_refuses_to_overwrite_an_existing_destination(settings_root: Path) -> None:
    """S-13・FR-031: 退避先が既に在れば `FileExistsError`（**上書きしない**）。"""
    write_minimal_area_a(settings_root, projects=("pj", "pj2"))
    writer = AreaAWriter(settings_root)
    writer.archive_project("pj", "same-stamp")
    os.rename(settings_root / "projects/pj2", settings_root / "projects/pj")

    with pytest.raises(FileExistsError):
        writer.archive_project("pj", "same-stamp")

    assert (settings_root / "projects/pj/project.yaml").is_file(), "失敗したので元が残っている"


def test_the_writer_has_no_cleanup_of_archives(settings_root: Path) -> None:
    """FR-032: 退避の保持期間・掃除の仕組みを**持たない**（消す口が無いことの観測）。"""
    names = {name for name in dir(AreaAWriter) if not name.startswith("__")}

    assert names == {
        "stage",
        "commit",
        "discard",
        "remove",
        "archive_project",
        "ensure_project_dir",
        "discard_project_dir",
    }
