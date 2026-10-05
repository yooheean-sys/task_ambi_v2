"""組み立てと設定検査（`build`。stages-F08・US6）。

**`tmp_path` を使うのはこのファイルだけ**である（stages の 4 メソッドはファイルに触れない）。
実リポジトリの `packages/features/stages/config.json` を掴まないよう、`config_path` を
差し替えてから `build()` を呼ぶ。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from conftest import (
    make_ctx,
    make_item,
    make_member,
    make_project_rules,
    make_proposal,
    make_templates,
)

from common.kernel.enums import PersonaKey, StageName
from stages import StagesConfig, StagesConfigError, StagesDeps, build, config_path

if TYPE_CHECKING:
    from collections.abc import Iterator

VALID_CONFIG: dict[str, Any] = {
    "min_items_override": {},
    "min_acceptance_len": 10,
    "placeholder_patterns": ["", "-", "tbd"],
    "max_acceptance_default": None,
}


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """`config.json` の所在を一時領域へ差し替える（実ファイルを掴まない）。"""
    path = tmp_path / "config.json"
    monkeypatch.setattr("stages.config_path", lambda: path)
    yield path


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_reads_config_file(config_file: Path) -> None:
    _write(config_file, VALID_CONFIG)
    handle = build(StagesDeps())
    assert handle.router is None


def test_unknown_key_aborts_with_absolute_path(config_file: Path) -> None:
    """US6 AC-5・FR-056: 未知キーで中断し、**見に行った絶対パス**を示す。"""
    _write(config_file, {**VALID_CONFIG, "unknown_key": 1})
    with pytest.raises(StagesConfigError) as exc:
        build(StagesDeps())
    assert str(config_file) in str(exc.value)


def test_wrong_type_aborts_with_absolute_path(config_file: Path) -> None:
    """(a) 必須値の型違い（欠けと同じ扱い）でも絶対パスを示す。"""
    _write(config_file, {**VALID_CONFIG, "min_acceptance_len": "十"})
    with pytest.raises(StagesConfigError) as exc:
        build(StagesDeps())
    assert str(config_file) in str(exc.value)


def test_unknown_stage_name_override_aborts(config_file: Path) -> None:
    """US6 AC-3・SC-010: 段表に無い段名の上書きは中断し、未知の段名と既知 5 段を示す。"""
    _write(config_file, {**VALID_CONFIG, "min_items_override": {"unknown_stage": 2}})
    with pytest.raises(StagesConfigError) as exc:
        build(StagesDeps())
    message = str(exc.value)
    assert str(config_file) in message
    assert "unknown_stage" in message
    for name in StageName:
        assert str(name) in message


THRESHOLD_CASES: tuple[tuple[dict[str, Any], str], ...] = (
    ({"min_items_override": {"project": 0}}, "min_items_override"),
    ({"min_items_override": {"project": -1}}, "min_items_override"),
    ({"min_acceptance_len": 0}, "min_acceptance_len"),
    ({"max_acceptance_default": 0}, "max_acceptance_default"),
)

THRESHOLD_IDS = ("min-items-zero", "min-items-negative", "min-len-zero", "max-acceptance-zero")


@pytest.mark.parametrize(("override", "keyword"), THRESHOLD_CASES, ids=THRESHOLD_IDS)
def test_threshold_out_of_range_aborts(override: dict[str, Any], keyword: str) -> None:
    """FR-055a: 閾値が 1 未満なら中断する（差し替え口を使うので `tmp_path` は要らない）。"""
    with pytest.raises(StagesConfigError) as exc:
        build(StagesDeps(), config=StagesConfig(**{**VALID_CONFIG, **override}))
    assert keyword in str(exc.value)


def test_null_max_acceptance_is_allowed() -> None:
    """FR-055a: `max_acceptance_default` の `null`（＝上限なし）は許す。"""
    handle = build(StagesDeps(), config=StagesConfig(**VALID_CONFIG))
    for name in StageName:
        assert handle.stage_catalog.resolve_stage(*_labels_for(name)).max_acceptance is None


def _labels_for(name: StageName) -> tuple[set[str], bool]:
    """段名からその段に落ちる入力を作る（テスト内の対応表）。"""
    table: dict[StageName, tuple[set[str], bool]] = {
        StageName.program: ({"kind:program"}, False),
        StageName.project: ({"kind:request"}, False),
        StageName.subrequest: ({"kind:subrequest"}, True),
        StageName.child: (set(), True),
        StageName.plain: (set(), False),
    }
    return table[name]


def test_min_items_default_is_one_for_every_stage() -> None:
    """US6 AC-2・FR-054: 上書きが無ければ**全段 1**。"""
    handle = build(StagesDeps(), config=StagesConfig(**VALID_CONFIG))
    for name in StageName:
        assert handle.stage_catalog.resolve_stage(*_labels_for(name)).min_items == 1


def test_min_items_override_applies_to_named_stage_only() -> None:
    """US6 AC-1: 上書きを書いた段だけ最小件数が変わる。"""
    config = StagesConfig(**{**VALID_CONFIG, "min_items_override": {"project": 3}})
    handle = build(StagesDeps(), config=config)
    catalog = handle.stage_catalog
    assert catalog.resolve_stage(*_labels_for(StageName.project)).min_items == 3
    assert catalog.resolve_stage(*_labels_for(StageName.subrequest)).min_items == 1


def test_max_acceptance_default_is_applied_to_every_stage() -> None:
    """CFG-50: 既定上限に値があれば全段に載る。"""
    config = StagesConfig(**{**VALID_CONFIG, "max_acceptance_default": 2})
    handle = build(StagesDeps(), config=config)
    for name in StageName:
        assert handle.stage_catalog.resolve_stage(*_labels_for(name)).max_acceptance == 2


def test_thresholds_reach_the_gate() -> None:
    """US6 AC-4: 最小長・穴埋め文言が検査に効く。"""
    config = StagesConfig(
        **{**VALID_CONFIG, "min_acceptance_len": 100, "placeholder_patterns": ["独自語"]}
    )
    gate = build(StagesDeps(), config=config).proposal_gate
    spec = build(StagesDeps(), config=config).stage_catalog.resolve_stage(
        *_labels_for(StageName.project)
    )
    result = gate.validate_proposal(
        make_proposal([make_item(title="独自語", acceptance=["- [ ] outcome/a.md にまとめる"])]),
        spec,
        [],
    )
    assert any("title" in p for p in result.problems)
    assert any("短" in p for p in result.problems)


def test_both_contracts_are_the_same_instance() -> None:
    """FR-005: `stage_catalog is proposal_gate`。"""
    handle = build(StagesDeps(), config=StagesConfig(**VALID_CONFIG))
    assert handle.stage_catalog is handle.proposal_gate


def test_router_is_none() -> None:
    """FR-007: HTTP 経路を持たない。"""
    assert build(StagesDeps(), config=StagesConfig(**VALID_CONFIG)).router is None


def test_config_path_points_at_module_config() -> None:
    """規約の固定相対パス（憲章 §5）。"""
    assert config_path() == Path(__file__).resolve().parents[2] / "config.json"
    assert config_path().is_file()


def test_shipped_config_json_has_all_four_keys() -> None:
    """CFG-47〜50: 出荷時の `config.json` は 4 キーすべてを明示して置く。"""
    payload = json.loads(config_path().read_text(encoding="utf-8"))
    assert set(payload) == set(StagesConfig.model_fields)
    assert payload["max_acceptance_default"] is None


def test_config_has_no_env_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-057: モジュール設定は**環境変数で上書きできない**。

    `settings_customise_sources` を `(init_settings, JsonConfigSettingsSource)` **だけ**に
    絞っていることの固定である。`env_settings` を戻す「整理」をしても、これが無いと
    落ちるテストが 1 つも無い（`config_path()` の遅延解決の方は `config_file` fixture が
    守っている）。先行 005 の `test_config_has_no_env_source` と同型。

    出荷時の `config.json`（`min_acceptance_len=10`・`max_acceptance_default=null`）を読む
    ので `tmp_path` は要らない。
    """
    monkeypatch.setenv("MIN_ACCEPTANCE_LEN", "99")
    monkeypatch.setenv("MAX_ACCEPTANCE_DEFAULT", "3")
    loaded = StagesConfig()
    assert loaded.min_acceptance_len == 10
    assert loaded.max_acceptance_default is None


def test_methods_do_no_file_io_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """US6 AC-6・SC-002・FR-008: 組み立て後は 4 メソッドがファイルを 1 度も開かない。"""
    handle = build(StagesDeps(), config=StagesConfig(**VALID_CONFIG))

    def _forbidden(*args: object, **kwargs: object) -> None:
        msg = f"build() 以降にファイルを開いた: {args!r}"
        raise AssertionError(msg)

    monkeypatch.setattr("builtins.open", _forbidden)
    monkeypatch.setattr(Path, "open", _forbidden)
    monkeypatch.setattr(Path, "read_text", _forbidden)
    catalog = handle.stage_catalog
    gate = handle.proposal_gate
    spec = catalog.resolve_stage({"kind:subrequest"}, True)
    catalog.build_prompt(
        spec,
        make_member(persona_key=PersonaKey.system_engineer),
        make_project_rules(),
        make_templates(),
        make_ctx(),
        None,
    )
    gate.validate_proposal(make_proposal([make_item()]), spec, [])
    gate.extract_outcome_tokens("- [ ] outcome/a.md にまとめる")
