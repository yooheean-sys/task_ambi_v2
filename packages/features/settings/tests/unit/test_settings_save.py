"""US1: 保存経路（settings-F02）の 4 段。contracts/save-pipeline.md §6。

検証行: S-1（検査に落ちたら 1 バイトも変わらない）・S-2／S-14（世代）・S-4／S-15（書込失敗）・
S-5／S-6／S-7（違反の一覧）・S-8（秘密の非露出）。

**世代は `SaveOutcome.generation` で観測する**（`SettingsCrudService` の内部を覗かない）。
「書込に失敗しても世代が増えていない」は、失敗のあとに通る保存を 1 回行い、それが `N+1` を
返すこと（`N+2` でないこと）で観測する。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    area_bytes,
    make_crud,
    make_data,
    temp_files,
    write_minimal_area_a,
)

from settings.exceptions import SettingsValidationError
from settings.models import PROBLEM_KINDS, ChangeSet, EffectClass, FileWrite
from settings.repository import dump_policy_rows, dump_yaml_mapping
from settings.service.save import save_candidate

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from settings.models import AreaAData

HUMANS_REL = "global/humans.yaml"
POLICY_REL = "projects/pj/policy.csv"


def _humans_change(actors: Sequence[str]) -> tuple[dict[str, object], ChangeSet]:
    """`global/humans.yaml` を 1 ファイルだけ書き換える候補の素材。"""
    raw: dict[str, object] = {"human_actors": list(actors)}
    changes = ChangeSet(writes=(FileWrite(rel=HUMANS_REL, text=dump_yaml_mapping(raw)),))
    return raw, changes


def _passing_save(root: Path, actors: Sequence[str]) -> tuple[AreaAData, ChangeSet]:
    """検査を通る候補（人間の行為者を差し替えるだけ）。"""
    raw, changes = _humans_change(actors)
    return dataclasses.replace(make_data(root), humans_raw=raw), changes


# -- S-1 検査に落ちる保存（SC-001・FR-012a） --------------------------------------------


def test_s1_a_rejected_save_leaves_every_byte_untouched(settings_root: Path) -> None:
    """S-1・SC-001: 保存前後で**全ファイルのバイト列が完全一致**し、一時ファイルが 0 件。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    before = area_bytes(settings_root)
    # V-20: 人間の宣言が AI メンバの行為者名（`pj.programmer`）と衝突する
    candidate, changes = _passing_save(settings_root, ["pj.programmer"])

    with pytest.raises(SettingsValidationError):
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    assert area_bytes(settings_root) == before, "差分 0 バイトでない"
    assert temp_files(settings_root) == []


def test_s1_a_rejected_policy_save_cleans_up_its_staged_file(settings_root: Path) -> None:
    """FR-012a (a)・010 SD-02: 候補 policy の構文検査で作った一時ファイルも**必ず片付く**。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    before = area_bytes(settings_root)
    # 列数違反（`p` は 3 列）。読めなさは `build_enforcer` の担保 2 が捕まえる → `orphan`
    rows = (("p", "member:pj.programmer", "tool:read_issue", "execute", "extra"),)
    candidate = dataclasses.replace(make_data(settings_root), policy_rows={"pj": rows})
    changes = ChangeSet(
        writes=(FileWrite(rel=POLICY_REL, text=dump_policy_rows([row[0:] for row in rows])),)
    )

    with pytest.raises(SettingsValidationError) as caught:
        save_candidate(crud, candidate, changes, EffectClass.immediate)

    assert {item.kind for item in caught.value.problems} == {"orphan"}
    assert area_bytes(settings_root) == before
    assert temp_files(settings_root) == [], "2-4 の一時ファイルが残っている"


# -- S-2・S-14 世代（SC-002・FR-019・Edge Cases） ----------------------------------------


def test_s2_each_accepted_save_advances_the_generation_by_exactly_one(settings_root: Path) -> None:
    """S-2・SC-002: 連続 3 回の保存で世代が `1→2→3→4` と**ちょうど 1 ずつ**増える。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)

    generations: list[int] = []
    for index in range(3):
        candidate, changes = _passing_save(settings_root, [f"人間{index}"])
        generations.append(
            save_candidate(crud, candidate, changes, EffectClass.next_kickoff).generation
        )

    assert generations == [2, 3, 4]


def test_s14_the_same_save_twice_is_not_idempotent(settings_root: Path) -> None:
    """S-14・Edge Cases: 同じ保存を 2 回送ると世代が**さらに 1 増える**（冪等にしない）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    candidate, changes = _passing_save(settings_root, ["同じ人"])

    first = save_candidate(crud, candidate, changes, EffectClass.next_kickoff)
    second = save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    assert (first.generation, second.generation) == (2, 3)


def test_the_effect_class_is_carried_through_untouched(settings_root: Path) -> None:
    """FR-022: 反映区分は呼び手が決めた値がそのまま戻る（保存経路は解釈しない）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    candidate, changes = _passing_save(settings_root, ["人"])

    assert save_candidate(crud, candidate, changes, EffectClass.immediate).effect is (
        EffectClass.immediate
    )


def test_an_accepted_save_actually_writes_the_file(settings_root: Path) -> None:
    """段 3: 通った保存は `ChangeSet` のファイルだけを書く（FR-015・D-d）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    before = area_bytes(settings_root)
    candidate, changes = _passing_save(settings_root, ["新しい人"])

    save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    after = area_bytes(settings_root)
    assert {rel for rel in after if after[rel] != before.get(rel)} == {HUMANS_REL}
    assert "新しい人" in after[HUMANS_REL].decode()
    assert temp_files(settings_root) == []


# -- S-4・S-15 書込の失敗（SC-004・FR-021） ---------------------------------------------


def test_s4_a_failed_write_keeps_the_old_file_and_the_old_generation(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-4・SC-004 (a)(b)(c): 旧ファイルが読める・一時ファイル 0 件・世代が増えていない。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    before = area_bytes(settings_root)
    candidate, changes = _passing_save(settings_root, ["書けなかった人"])

    def _boom(self: object, rel: str, temporary: Path) -> None:
        msg = "置換に失敗した"
        raise OSError(msg)

    monkeypatch.setattr("settings.repository.AreaAWriter.commit", _boom)
    with pytest.raises(OSError, match="置換に失敗した"):
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    assert area_bytes(settings_root) == before, "(a) 旧ファイルが読めない"
    assert temp_files(settings_root) == [], "(b) 一時ファイルが残っている"

    monkeypatch.undo()
    accepted, accepted_changes = _passing_save(settings_root, ["書けた人"])
    outcome = save_candidate(crud, accepted, accepted_changes, EffectClass.next_kickoff)

    assert outcome.generation == 2, "(c) 失敗した保存が世代を進めていた"


def test_s15_a_failed_registration_leaves_no_empty_project_directory(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-15・SC-004 (d)・plan D-j: 3-0 の後・3-2 の前で落ちても空のフォルダが残らない。

    残すと `AreaAReader.list_project_keys` が**無条件に**プロジェクトとみなし、定義ファイルが
    無いため**次回起動が中断する**。
    """
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    data = make_data(settings_root)
    new_key = "newpj"
    project_raw: dict[str, object] = {
        "name": "新規",
        "is_reserved": False,
        "folders": {
            "pj_input_dir": new_key,
            "role_dirs": {"programmer": f"{new_key}/roles/programmer"},
            "outcome_dir": f"{new_key}/outcome",
        },
    }
    candidate = dataclasses.replace(
        data,
        projects_raw={**data.projects_raw, new_key: project_raw},
        agents_raw={**data.agents_raw, new_key: {}},
        tasks_raw={**data.tasks_raw, new_key: None},
        hooks_raw={**data.hooks_raw, new_key: None},
        rules_raw={**data.rules_raw, new_key: None},
        policy_rows={**data.policy_rows, new_key: ()},
    )
    changes = ChangeSet(
        writes=(
            FileWrite(rel=f"projects/{new_key}/project.yaml", text=dump_yaml_mapping(project_raw)),
            FileWrite(rel=f"projects/{new_key}/agents.yaml", text="{}\n"),
            FileWrite(rel=f"projects/{new_key}/policy.csv", text=""),
        ),
        new_project=new_key,
    )

    def _boom(self: object, rel: str, temporary: Path) -> None:
        msg = "置換に失敗した"
        raise OSError(msg)

    monkeypatch.setattr("settings.repository.AreaAWriter.commit", _boom)
    with pytest.raises(OSError, match="置換に失敗した"):
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    from settings.repository import AreaAReader  # noqa: PLC0415

    assert new_key not in AreaAReader(settings_root).list_project_keys()
    assert temp_files(settings_root) == []


# -- S-5・S-6・S-7 違反の一覧（SC-005〜SC-007） -----------------------------------------


def test_s5_two_violations_are_both_reported(settings_root: Path) -> None:
    """S-5・SC-005・FR-012: **最初の 1 件で打ち切らない**（2 件以上返る）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    data = make_data(settings_root)
    # V-20（衝突）と V-03（許可 10 の外のフィールド）を同時に仕込む
    candidate = dataclasses.replace(
        data,
        humans_raw={"human_actors": ["pj.programmer"]},
        agents_raw={"pj": {"programmer": {"unknown_field": 1}}},
    )
    changes = ChangeSet(
        writes=(
            FileWrite(rel=HUMANS_REL, text=dump_yaml_mapping({"human_actors": ["pj.programmer"]})),
        )
    )

    with pytest.raises(SettingsValidationError) as caught:
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    assert len(caught.value.problems) >= 2


def test_s6_the_kind_vocabulary_never_grows(settings_root: Path) -> None:
    """S-6・SC-006・FR-014: `kind` が **13 語の外に出ない**。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    data = make_data(settings_root)
    candidate = dataclasses.replace(
        data,
        humans_raw={"human_actors": ["pj.programmer"], "api_key": "sk-NEVER-SHOWN"},
        agents_raw={"pj": {"programmer": {"allow_delegation": True}, "unknown_persona": {}}},
    )
    changes = ChangeSet(writes=(FileWrite(rel=HUMANS_REL, text="human_actors: []\n"),))

    with pytest.raises(SettingsValidationError) as caught:
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    assert {item.kind for item in caught.value.problems} <= set(PROBLEM_KINDS)
    assert len(caught.value.problems) >= 3


def test_s7_no_absolute_path_appears_in_where(settings_root: Path) -> None:
    """S-7・SC-007・FR-013: `where` は領域 A ルート相対（**絶対パスが 1 件も出ない**）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    data = make_data(settings_root)
    candidate = dataclasses.replace(
        data,
        humans_raw={"human_actors": ["pj.programmer"]},
        agents_raw={"pj": {"programmer": {"llm": "居ないプロファイル"}}},
    )
    changes = ChangeSet(writes=(FileWrite(rel=HUMANS_REL, text="human_actors: []\n"),))

    with pytest.raises(SettingsValidationError) as caught:
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    wheres = [item.where for item in caught.value.problems]
    assert wheres
    assert not [where for where in wheres if where.startswith("/")]
    assert str(settings_root) not in "\n".join(wheres)


# -- S-8 秘密の非露出とログ（SC-008・SC-023・FR-023） ------------------------------------


def test_s8_a_secret_value_never_reaches_the_problems_or_the_log(
    settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S-8・SC-008・FR-023: 鍵の実値が応答本文にもログにも現れない。"""
    secret = "sk-THIS-MUST-NOT-APPEAR"
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    data = make_data(settings_root)
    candidate = dataclasses.replace(data, humans_raw={"human_actors": [], "api_key": secret})
    changes = ChangeSet(writes=(FileWrite(rel=HUMANS_REL, text="human_actors: []\n"),))

    with (
        caplog.at_level(logging.INFO, logger="settings.service"),
        pytest.raises(SettingsValidationError) as caught,
    ):
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    rendered = "\n".join(
        f"{item.where} {item.kind} {item.reason}" for item in caught.value.problems
    )
    assert "forbidden_key" in {item.kind for item in caught.value.problems}
    assert secret not in rendered
    assert secret not in caplog.text


def test_the_outcome_of_a_save_is_logged(
    settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-023・FR-023: 保存の成否が構造化ログに残る（**設定の実値は出さない**）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    candidate, changes = _passing_save(settings_root, ["記録される人"])

    with caplog.at_level(logging.INFO, logger="settings.service"):
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    events = [record.getMessage() for record in caplog.records]
    assert "settings.save.completed" in events
    assert "記録される人" not in caplog.text, "設定の実値を丸ごと出している"


def test_a_rejected_save_is_logged_too(
    settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-023: 拒否も残る（成功だけを記録すると「保存できなかった」が追えない）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root)
    candidate, changes = _passing_save(settings_root, ["pj.programmer"])

    with (
        caplog.at_level(logging.INFO, logger="settings.service"),
        pytest.raises(SettingsValidationError),
    ):
        save_candidate(crud, candidate, changes, EffectClass.next_kickoff)

    assert "settings.save.rejected" in [record.getMessage() for record in caplog.records]
