"""F05 保存の検査（**8 通り**）と F06 起動条件の写像（`service/validate.py`）。

**番号（`V-1`〜`V-8`）の正本は
[contracts/trigger-http.md](../../../../specs/011-trigger/contracts/trigger-http.md) §4** である
（plan (viii) の同名の表はその写し）。**期待値は契約からの写しにする（発明しない）**。

共通の約束（契約 §4）:

- validate は**書込の前に**全部走らせ、**違反を一覧で返す**（最初の 1 件で打ち切らない）
- **拒否したとき宣言ファイルは 1 バイトも変わらない**（FR-027・SC-009）
- 拒否は常駐にも影響しない（`add_job`／`remove_job` を呼ぶ前に落ちる）
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from common.kernel.enums import Labels
from trigger import TriggerConfig
from trigger.exceptions import TriggerValidationError
from trigger.models import DEFAULT_TRIGGER_ID, LaunchCond, TriggerDef
from trigger.repository import DefinitionsRepository, default_trigger
from trigger.service.validate import (
    realpath_within,
    resolve_watch_dir,
    validate_definition,
    validate_delete,
)

if TYPE_CHECKING:
    from pathlib import Path

MIN_INTERVAL = 60
CRON_FIELDS = 5


def _config(**overrides: object) -> TriggerConfig:
    fields: dict[str, object] = {"min_interval_sec": MIN_INTERVAL}
    fields.update(overrides)
    return TriggerConfig(**fields)


def _definition(**overrides: object) -> TriggerDef:
    fields: dict[str, object] = {
        "trigger_id": "t1",
        "name": "テスト",
        "description": "",
        "type": "interval",
        "spec": "300",
        "condition": LaunchCond(),
        "enabled": True,
        "is_default": False,
    }
    fields.update(overrides)
    return TriggerDef.model_validate(fields)


def _problems(exc: TriggerValidationError) -> list[tuple[str, str]]:
    return [(item.where, item.reason) for item in exc.problems]


# -- V-1〜V-8（表駆動。期待値は contracts/trigger-http.md §4 の写し） --------------------


def test_v1_a_cron_expression_that_is_not_five_fields_is_rejected(downloads_dir: Path) -> None:
    """V-1: cron の欄数が 5 でない → `where="spec"`・「5 欄（秒を持たない）」＋ 原文。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="cron", spec="0 0 * * * *"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["spec"]
    reason = problems[0][1]
    assert "5 欄（秒を持たない）" in reason
    assert "expected 5" in reason, f"from_crontab の原文が載っていない: {reason}"


def test_v2_a_cron_expression_with_a_bad_field_value_is_rejected(downloads_dir: Path) -> None:
    """V-2: 曜日 `7` は通らない（**「曜日は 0 が月曜」**を必ず添える＝ FR-022）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="cron", spec="0 9 * * 7"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["spec"]
    reason = problems[0][1]
    assert "曜日は 0 が月曜" in reason, f"利用者が誤りやすい点が書かれていない: {reason}"
    assert "7" in reason


def test_v3_an_interval_that_is_not_a_number_is_rejected(downloads_dir: Path) -> None:
    """V-3: interval が数として読めない（`spec` は文字列 1 本なので**解釈の失敗も拒否理由**）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="interval", spec="まいにち"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["spec"]
    assert "秒を表す整数" in problems[0][1]


def test_v4_an_interval_below_the_minimum_is_rejected(downloads_dir: Path) -> None:
    """V-4: `min_interval_sec` 未満 →  **設定から採った下限値**を reason に載せる（FR-023）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="interval", spec="59"),
            config=_config(min_interval_sec=60),
            downloads_dir=downloads_dir,
        )

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["spec"]
    assert "60" in problems[0][1], f"設定の下限値が載っていない: {problems[0][1]}"


def test_v4_uses_the_configured_minimum_not_a_hard_coded_one(downloads_dir: Path) -> None:
    """下限は**設定から採る**（コードに直書きしない）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="interval", spec="100"),
            config=_config(min_interval_sec=120),
            downloads_dir=downloads_dir,
        )

    assert "120" in _problems(caught.value)[0][1]


def test_v5_a_watch_dir_outside_the_downloads_folder_is_rejected(
    downloads_dir: Path, tmp_path: Path
) -> None:
    """V-5: `watch_dir` が `DOWNLOADS_DIR` の**外**を指す（FR-024・D15-e）。

    **文字列比較を使わない**ことを symlink で確かめる —— `downloads/link` は文字列としては
    基準の内側に見えるが、`realpath` で外へ出る。
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (downloads_dir / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="file", spec="link"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["spec"]
    reason = problems[0][1]
    assert str(downloads_dir.resolve()) in reason, f"基準の絶対パスが載っていない: {reason}"
    assert str(outside.resolve()) in reason, f"解決後の実パスが載っていない: {reason}"


def test_v5_a_relative_escape_is_rejected(downloads_dir: Path) -> None:
    """V-5: `..` で外へ出る形も拒否する。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="file", spec="../elsewhere"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    assert [where for where, _ in _problems(caught.value)] == ["spec"]


def test_v6_a_file_trigger_without_a_downloads_dir_is_rejected() -> None:
    """V-6: `DOWNLOADS_DIR` 未設定で `file` 種別 → `where="type"`（FR-025・CFG-09）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="file", spec="."),
            config=_config(),
            downloads_dir=None,
        )

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["type"]
    assert "DOWNLOADS_DIR" in problems[0][1]


def test_v7_deleting_the_default_trigger_is_rejected() -> None:
    """V-7: 既定トリガーの**削除** → `where="trigger_id"`（FR-019・D06-j(1)）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_delete(default_trigger(300))

    problems = _problems(caught.value)
    assert [where for where, _ in problems] == ["trigger_id"]
    assert "既定トリガーは削除できない" in problems[0][1]


def test_v7_deleting_a_normal_trigger_is_allowed() -> None:
    """通る形: 既定でないトリガーの削除は落ちない。"""
    validate_delete(_definition())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "改名した"),
        ("description", "説明を足した"),
        ("type", "cron"),
        ("condition", LaunchCond(labels=("role:planner",))),
    ],
    ids=["name", "description", "type", "condition"],
)
def test_v8_a_forbidden_change_to_the_default_trigger_is_rejected(
    downloads_dir: Path, field: str, value: object
) -> None:
    """V-8: 既定トリガーの許されない変更 → `where` は**差異のあった欄名**（SD-12・FN-30）。"""
    current = default_trigger(300)
    candidate = current.model_copy(update={field: value})
    if field == "type":
        candidate = candidate.model_copy(update={"spec": "0 9 * * 0"})

    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            candidate, config=_config(), downloads_dir=downloads_dir, current=current
        )

    problems = _problems(caught.value)
    assert field in [where for where, _ in problems], f"欄名が where に出ていない: {problems}"
    assert any("変更できるのは間隔と有効／無効だけ" in reason for _, reason in problems)


def test_v8_allows_changing_the_spec_and_the_enabled_flag(downloads_dir: Path) -> None:
    """V-8: 許すのは `spec` と `enabled` の **2 つだけ**（両方変えても通る）。"""
    current = default_trigger(300)

    result = validate_definition(
        current.model_copy(update={"spec": "600", "enabled": False}),
        config=_config(),
        downloads_dir=downloads_dir,
        current=current,
    )

    assert result.spec == "600"
    assert result.enabled is False
    assert result.is_default is True


def test_v8_compares_labels_as_a_set(downloads_dir: Path) -> None:
    """V-8: `condition.labels` は**集合として**比較する（並び替えは変更でない＝ SD-12）。"""
    current = _definition(
        trigger_id=DEFAULT_TRIGGER_ID,
        is_default=True,
        condition=LaunchCond(labels=("a", "b")),
    )

    result = validate_definition(
        current.model_copy(update={"condition": LaunchCond(labels=("b", "a"))}),
        config=_config(),
        downloads_dir=downloads_dir,
        current=current,
    )

    assert set(result.condition.labels) == {"a", "b"}


# -- 通る形 -------------------------------------------------------------------------------


def test_a_five_field_cron_expression_passes(downloads_dir: Path) -> None:
    """通る形: crontab 5 欄。"""
    result = validate_definition(
        _definition(type="cron", spec="0 9 * * 0-4"),
        config=_config(),
        downloads_dir=downloads_dir,
    )

    assert result.spec == "0 9 * * 0-4"
    assert len(result.spec.split()) == CRON_FIELDS


def test_an_interval_exactly_at_the_minimum_passes(downloads_dir: Path) -> None:
    """通る形: 下限ちょうど（`<` であって `<=` でない）。"""
    assert (
        validate_definition(
            _definition(type="interval", spec="60"),
            config=_config(min_interval_sec=60),
            downloads_dir=downloads_dir,
        ).spec
        == "60"
    )


def test_the_default_watch_dir_is_a_single_dot(downloads_dir: Path) -> None:
    """通る形: `watch_dir` の既定 `"."`（`DOWNLOADS_DIR` そのもの）。"""
    assert (
        validate_definition(
            _definition(type="file", spec="."),
            config=_config(),
            downloads_dir=downloads_dir,
        ).spec
        == "."
    )


def test_the_kind_is_always_trigger_spec(downloads_dir: Path) -> None:
    """SD-15: `kind` は常に `"trigger_spec"`（`openapi.yaml` の enum に 1 語しか無い）。"""
    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            _definition(type="interval", spec="1"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    assert {item.kind for item in caught.value.problems} == {"trigger_spec"}


# -- 一覧で返す（最初の 1 件で打ち切らない） -----------------------------------------------


def test_all_violations_are_reported_at_once(downloads_dir: Path) -> None:
    """違反は**一覧**で返る（`problems` は配列）。"""
    current = default_trigger(300)
    candidate = current.model_copy(update={"name": "改名", "description": "説明", "spec": "1"})

    with pytest.raises(TriggerValidationError) as caught:
        validate_definition(
            candidate, config=_config(), downloads_dir=downloads_dir, current=current
        )

    wheres = [where for where, _ in _problems(caught.value)]
    assert "spec" in wheres, "下限違反が落ちている"
    assert "name" in wheres
    assert "description" in wheres
    assert len(wheres) >= 3, f"最初の 1 件で打ち切っている: {wheres}"


def test_a_rejection_leaves_the_declaration_file_byte_identical(
    trigger_root: Path, downloads_dir: Path
) -> None:
    """FR-027・SC-009: **拒否のとき `triggers.yaml` のバイト列が変わらない**。"""
    repo = DefinitionsRepository(trigger_root)
    repo.ensure_template(300)
    before = hashlib.md5(repo.path.read_bytes(), usedforsecurity=False).hexdigest()

    with pytest.raises(TriggerValidationError):
        validate_definition(
            _definition(type="interval", spec="1"),
            config=_config(),
            downloads_dir=downloads_dir,
        )

    assert hashlib.md5(repo.path.read_bytes(), usedforsecurity=False).hexdigest() == before


# -- F06: LaunchCond → ClaimFilter（写すだけ） ----------------------------------------------


def test_launch_cond_maps_to_a_claim_filter() -> None:
    """F06・FR-026: `to_claim_filter()` は**写すだけ**。既定ラベルは `role:executor`。"""
    claim = LaunchCond().to_claim_filter()

    assert claim.labels == frozenset({Labels.role_executor()})
    assert claim.project_key is None


def test_launch_cond_carries_the_project_key_and_all_labels() -> None:
    """F06: ラベルは AND 条件でそのまま、`project_key` もそのまま渡る。"""
    claim = LaunchCond(labels=("role:executor", "kind:task"), project_key="pj").to_claim_filter()

    assert claim.labels == frozenset({"role:executor", "kind:task"})
    assert claim.project_key == "pj"


def test_an_empty_label_set_falls_back_to_the_default(downloads_dir: Path) -> None:
    """FR-026: ラベルが空なら既定 `role:executor` を充当する（validate の仕事）。"""
    result = validate_definition(
        _definition(condition=LaunchCond(labels=())),
        config=_config(),
        downloads_dir=downloads_dir,
    )

    assert list(result.condition.labels) == [Labels.role_executor()]


# -- 外縁検査（`(st_dev, st_ino)` を親方向へ 1 段ずつ。文字列比較を使わない＝ M-07） --------


def test_resolve_watch_dir_returns_the_real_path_for_the_base_itself(
    downloads_dir: Path,
) -> None:
    """`"."` は基準そのもの。"""
    assert resolve_watch_dir(downloads_dir, ".") == str(downloads_dir.resolve())


def test_resolve_watch_dir_accepts_a_nested_directory(downloads_dir: Path) -> None:
    """内側のフォルダは受け入れる。"""
    nested = downloads_dir / "a" / "b"
    nested.mkdir(parents=True)

    assert resolve_watch_dir(downloads_dir, "a/b") == str(nested.resolve())


def test_resolve_watch_dir_rejects_a_symlink_that_escapes(
    downloads_dir: Path, tmp_path: Path
) -> None:
    """symlink で外へ出る形を拒否できる（**文字列比較では捕まらない**）。"""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (downloads_dir / "escape").symlink_to(outside, target_is_directory=True)

    assert str(downloads_dir) in str(downloads_dir / "escape"), "前提: 文字列としては内側に見える"
    assert resolve_watch_dir(downloads_dir, "escape") is None


def test_resolve_watch_dir_rejects_a_missing_directory(downloads_dir: Path) -> None:
    """実在しないフォルダは監視できないので受け入れない。"""
    assert resolve_watch_dir(downloads_dir, "never") is None


def test_realpath_within_matches_a_file_under_the_base(downloads_dir: Path) -> None:
    """`POST /hooks/file` の外縁検査: 基準の内側にあるファイルの実パスを返す。"""
    target = downloads_dir / "drop.bin"
    target.write_bytes(b"payload")

    assert realpath_within(downloads_dir, str(target)) == str(target.resolve())


def test_realpath_within_rejects_a_file_outside_the_base(
    downloads_dir: Path, tmp_path: Path
) -> None:
    """基準の外にあるファイルは `None`（→ 422「監視対象の外」）。"""
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"payload")

    assert realpath_within(downloads_dir, str(outside)) is None


def test_realpath_within_rejects_a_sibling_with_a_shared_prefix(tmp_path: Path) -> None:
    """接頭辞が同じだけの兄弟フォルダを内側と誤らない（文字列比較の典型的な穴）。"""
    base = tmp_path / "dl"
    base.mkdir()
    sibling = tmp_path / "dl_other"
    sibling.mkdir()
    target = sibling / "x.bin"
    target.write_bytes(b"payload")

    assert str(target).startswith(str(base)), "前提: 文字列としては内側に見える"
    assert realpath_within(base, str(target)) is None
