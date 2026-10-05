"""F01 領域 B の宣言ファイル（`repository/definitions.py`）と F04 既定トリガーの保証。

正本は data-model §6（`triggers.yaml` の形）と plan (i) の F01 行。**宣言が正**（D07-a）なので、
ここが壊れると常駐の登録内容そのものが壊れる。

**書込は `definitions.py` の 1 箇所に閉じる**（憲章 §2）。一時ファイルは**同じディレクトリ**に
作って `os.replace` する（R-11。FS をまたぐと `EXDEV` で失敗する）。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import yaml

from trigger.exceptions import TriggerDefinitionsError
from trigger.models import DEFAULT_TRIGGER_ID, LaunchCond, TriggerDef
from trigger.repository import DefinitionsRepository

if TYPE_CHECKING:
    from pathlib import Path

READY_INTERVAL = 300
TMP_NAME = ".triggers.yaml.tmp"


def _repo(root: Path) -> DefinitionsRepository:
    return DefinitionsRepository(root)


# -- 雛形生成（既定トリガー 1 件だけ） ---------------------------------------------------


def test_template_is_created_with_exactly_one_default_trigger(trigger_root: Path) -> None:
    """宣言ファイルが無い状態で雛形が生成され、**既定トリガーが 1 件だけ**（FR-017）。"""
    repo = _repo(trigger_root)

    repo.ensure_template(READY_INTERVAL)

    definitions = repo.load_definitions()
    assert len(definitions) == 1
    only = definitions[0]
    assert only.trigger_id == DEFAULT_TRIGGER_ID
    assert only.type == "interval"
    assert only.spec == str(READY_INTERVAL)
    assert only.is_default is True
    assert only.enabled is True
    assert list(only.condition.labels) == ["role:executor"]


def test_template_is_not_regenerated_when_the_file_already_exists(trigger_root: Path) -> None:
    """既に在れば**バイト列を 1 バイトも変えない**（雛形で上書きしない）。"""
    repo = _repo(trigger_root)
    repo.ensure_template(READY_INTERVAL)
    before = repo.path.read_bytes()

    repo.ensure_template(READY_INTERVAL)

    assert repo.path.read_bytes() == before


def test_the_initial_interval_comes_from_the_configuration_not_from_the_code(
    trigger_root: Path,
) -> None:
    """初期間隔は `config.json` の `ready_interval_default_sec` から採る（FR-020）。"""
    repo = _repo(trigger_root)

    repo.ensure_template(120)

    assert repo.load_definitions()[0].spec == "120"


# -- 往復（spec の文字列性・日本語・並び） -----------------------------------------------


def test_spec_stays_a_string_through_a_round_trip(trigger_root: Path) -> None:
    """`spec` は**種別によらず文字列 1 本**（DIFF-11）。`"300"` が `int` にならない。"""
    repo = _repo(trigger_root)
    repo.ensure_template(READY_INTERVAL)

    repo.save_definitions(repo.load_definitions())

    raw = yaml.safe_load(repo.path.read_text(encoding="utf-8"))
    assert raw["triggers"][0]["spec"] == "300"
    assert isinstance(raw["triggers"][0]["spec"], str)
    assert repo.load_definitions()[0].spec == "300"


def test_japanese_names_and_descriptions_survive(trigger_root: Path) -> None:
    """日本語の名前・説明が壊れない（`allow_unicode=True` が要る＝ F-16）。"""
    repo = _repo(trigger_root)
    definition = TriggerDef(
        trigger_id="t1",
        name="毎朝の巡回",
        description="平日の朝に ready を拾う",
        type="cron",
        spec="0 9 * * 0-4",
        condition=LaunchCond(),
        enabled=True,
    )

    repo.save_definitions([definition])

    text = repo.path.read_text(encoding="utf-8")
    assert "毎朝の巡回" in text, "エスケープされている（allow_unicode=True が無い）"
    assert "平日の朝に ready を拾う" in text
    assert repo.load_definitions()[0].name == "毎朝の巡回"


def test_key_order_is_preserved_and_flow_style_is_block(trigger_root: Path) -> None:
    """`sort_keys=False`・`default_flow_style=False` で並びが保たれる。"""
    repo = _repo(trigger_root)
    repo.ensure_template(READY_INTERVAL)

    text = repo.path.read_text(encoding="utf-8")

    keys = [line.strip().lstrip("- ").split(":")[0] for line in text.splitlines() if ":" in line]
    trigger_id_at = keys.index("trigger_id")
    assert keys[trigger_id_at : trigger_id_at + 5] == [
        "trigger_id",
        "name",
        "description",
        "type",
        "spec",
    ], f"宣言の並びが変わった: {keys}"
    assert "{" not in text and "[" not in text.replace('["role:executor"]', ""), (
        "flow style で書かれている（default_flow_style=False が無い）"
    )


# -- 書込の原子性（R-11） -----------------------------------------------------------------


def test_writes_go_through_a_dot_prefixed_temp_file_in_the_same_directory(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ファイルは**同じディレクトリ**の `.triggers.yaml.tmp`（R-11。`EXDEV` を避ける）。"""
    repo = _repo(trigger_root)
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src: object, dst: object, **kwargs: object) -> None:
        del kwargs
        seen.append((str(src), str(dst)))
        real_replace(str(src), str(dst))

    monkeypatch.setattr("trigger.repository.definitions.os.replace", _spy)

    repo.ensure_template(READY_INTERVAL)

    assert len(seen) == 1
    src, dst = seen[0]
    assert src.endswith(TMP_NAME)
    assert os.path.dirname(src) == os.path.dirname(dst), "一時ファイルが別ディレクトリにある"


def test_the_temp_file_does_not_survive_the_replace(trigger_root: Path) -> None:
    """置換後に一時ファイルが残らない。"""
    repo = _repo(trigger_root)

    repo.ensure_template(READY_INTERVAL)

    assert not (trigger_root / TMP_NAME).exists()
    assert sorted(item.name for item in trigger_root.iterdir()) == ["triggers.yaml"]


def test_the_temp_file_is_removed_even_when_the_replace_fails(
    trigger_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.replace` が落ちても `finally` で一時ファイルを消す（ゴミを残さない）。"""
    repo = _repo(trigger_root)

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "置換に失敗"
        raise OSError(msg)

    monkeypatch.setattr("trigger.repository.definitions.os.replace", _boom)

    with pytest.raises(TriggerDefinitionsError):
        repo.ensure_template(READY_INTERVAL)

    assert not (trigger_root / TMP_NAME).exists()


# -- 壊れた宣言 ---------------------------------------------------------------------------


def test_a_yaml_syntax_error_becomes_a_definitions_error(trigger_root: Path) -> None:
    """YAML 構文誤り → `TriggerDefinitionsError`（段 (e) の起動中断）。"""
    repo = _repo(trigger_root)
    repo.path.write_text("triggers: [\n  - broken", encoding="utf-8")

    with pytest.raises(TriggerDefinitionsError):
        repo.load_definitions()


@pytest.mark.parametrize(
    "text",
    [
        "- a\n- b\n",  # トップレベルが配列
        "triggers: 3\n",  # triggers が配列でない
        "triggers:\n  - 3\n",  # 要素が写像でない
        "triggers:\n  - name: 名前だけ\n",  # 必須欄が無い
        # 未知の種別
        "triggers:\n  - trigger_id: t\n    name: n\n    type: unknown\n    spec: '1'\n",
    ],
    ids=["top_level_list", "not_a_list", "item_not_a_mapping", "missing_field", "unknown_type"],
)
def test_a_wrong_top_level_shape_becomes_a_definitions_error(trigger_root: Path, text: str) -> None:
    """トップレベルの形違い・必須欄の欠落 → `TriggerDefinitionsError`。"""
    repo = _repo(trigger_root)
    repo.path.write_text(text, encoding="utf-8")

    with pytest.raises(TriggerDefinitionsError):
        repo.load_definitions()


def test_an_empty_triggers_list_is_accepted(trigger_root: Path) -> None:
    """`triggers: []` は形として正しい（既定トリガーの保証は F04 の仕事）。"""
    repo = _repo(trigger_root)
    repo.path.write_text("triggers: []\n", encoding="utf-8")

    assert repo.load_definitions() == ()


# -- F04 既定トリガーの保証（T035。`service/__init__.py` の `ensure_default`） -------------


def test_ensure_default_adds_the_ready_sweep_when_it_is_missing(trigger_root: Path) -> None:
    """F04: `is_default=True` の `ready 巡回` が無ければ**足して書き戻す**。"""
    from trigger.service import ensure_default

    repo = _repo(trigger_root)
    repo.save_definitions(
        [
            TriggerDef(
                trigger_id="t1",
                name="他のトリガー",
                type="interval",
                spec="600",
                condition=LaunchCond(),
            )
        ]
    )

    definitions = ensure_default(repo, READY_INTERVAL)

    assert [item.trigger_id for item in definitions] == ["t1", DEFAULT_TRIGGER_ID]
    assert [item.is_default for item in definitions] == [False, True]
    assert [item.trigger_id for item in repo.load_definitions()] == ["t1", DEFAULT_TRIGGER_ID]


def test_ensure_default_does_not_rewrite_when_it_is_already_present(trigger_root: Path) -> None:
    """F04: 既にあれば**書き戻さない**（バイト列が変わらない）。"""
    from trigger.service import ensure_default

    repo = _repo(trigger_root)
    repo.ensure_template(READY_INTERVAL)
    before = repo.path.read_bytes()

    definitions = ensure_default(repo, READY_INTERVAL)

    assert len(definitions) == 1
    assert repo.path.read_bytes() == before


def test_ensure_default_takes_the_initial_interval_from_the_configuration(
    trigger_root: Path,
) -> None:
    """F04・FR-020: 初期間隔は `config.json` の `ready_interval_default_sec` から採る。"""
    from trigger.service import ensure_default

    repo = _repo(trigger_root)
    repo.save_definitions([])

    definitions = ensure_default(repo, 120)

    assert definitions[0].spec == "120"


def test_ensure_default_creates_the_file_when_it_is_missing(trigger_root: Path) -> None:
    """宣言ファイルごと無い場合も既定トリガー 1 件で立ち上がる。"""
    from trigger.service import ensure_default

    repo = _repo(trigger_root)

    definitions = ensure_default(repo, READY_INTERVAL)

    assert len(definitions) == 1
    assert definitions[0].trigger_id == DEFAULT_TRIGGER_ID
    assert repo.exists()


def test_ensure_default_recognises_the_default_by_the_is_default_flag(
    trigger_root: Path,
) -> None:
    """判定は **`is_default` の旗**（`trigger_id` を人が書き換えても既定は 1 件のまま）。"""
    from trigger.service import ensure_default

    repo = _repo(trigger_root)
    repo.save_definitions(
        [
            TriggerDef(
                trigger_id="renamed",
                name="ready 巡回",
                type="interval",
                spec="900",
                condition=LaunchCond(),
                is_default=True,
            )
        ]
    )

    definitions = ensure_default(repo, READY_INTERVAL)

    assert len(definitions) == 1, "既定が 2 件になった"
    assert definitions[0].spec == "900", "人手の間隔の変更が上書きされた"
