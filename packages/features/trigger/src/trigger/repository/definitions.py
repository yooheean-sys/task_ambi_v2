"""F01 領域 B の宣言ファイル（`<TRIGGER_ROOT>/triggers.yaml`）の読み書き。

**宣言が正**（D07-a）—— 常駐の登録内容は起動のたびにこのファイルから作り直す。

**領域 B への書込はこの 1 箇所に閉じる**（憲章 §2 例外 (a)。履歴 csv は `history.py` が持つ）。
書込は**一時ファイル → `os.replace`** で、一時ファイルは**置換先と同じディレクトリ**に作る
（R-11。FS をまたぐ `os.replace` は `EXDEV` で失敗する＝ `004` VD-05）。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import yaml
from pydantic import ValidationError

from trigger.exceptions import TriggerDefinitionsError
from trigger.models import (
    DEFAULT_TRIGGER_ID,
    DEFAULT_TRIGGER_NAME,
    LaunchCond,
    TriggerDef,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

DEFINITIONS_FILENAME = "triggers.yaml"
TEMP_FILENAME = ".triggers.yaml.tmp"
"""一時ファイル名。**ドット始まり**にして、置換前に落ちても隣に「見える」ゴミを残さない。"""

TOP_LEVEL_KEY = "triggers"


class DefinitionsRepository:
    """`triggers.yaml` のアダプタ（F01）。"""

    def __init__(self, root: Path) -> None:
        """領域 B のルートを受け取る。

        Args:
            root: `<TRIGGER_ROOT>`（`build()` の段 (b) で絶対・実在・ディレクトリを検査済み）。
        """
        self._root = root

    @property
    def path(self) -> Path:
        """宣言ファイルの絶対パス。"""
        return self._root / DEFINITIONS_FILENAME

    @property
    def temp_path(self) -> Path:
        """一時ファイルの絶対パス（**置換先と同じディレクトリ**＝ R-11）。"""
        return self._root / TEMP_FILENAME

    def exists(self) -> bool:
        """宣言ファイルが在るか。"""
        return self.path.is_file()

    def load_definitions(self) -> tuple[TriggerDef, ...]:
        """宣言ファイルを読む。

        Returns:
            宣言の並びのままの `TriggerDef` の組（不在なら空）。

        Raises:
            TriggerDefinitionsError: YAML 構文誤り／トップレベルの形違い／必須欄の欠落。
        """
        if not self.exists():
            return ()
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            msg = f"triggers.yaml が読めない（{self.path}）: {type(exc).__name__}: {exc}"
            raise TriggerDefinitionsError(msg) from exc
        return self._parse(raw)

    def save_definitions(self, definitions: Sequence[TriggerDef]) -> None:
        """宣言ファイルを**丸ごと**書き換える（一時ファイル → `os.replace`）。

        `yaml.safe_dump(dict(...), allow_unicode=True, sort_keys=False, default_flow_style=False)`
        で書く（F-16）—— 名前・説明に日本語が入るので `allow_unicode=True` は必須、宣言の並びを
        保つので `sort_keys=False` は必須。

        Args:
            definitions: 書き出す宣言の並び。

        Raises:
            TriggerDefinitionsError: 書き出しに失敗した（**見に行った絶対パス**を載せる）。
        """
        payload = {TOP_LEVEL_KEY: [_to_raw(item) for item in definitions]}
        text = yaml.safe_dump(
            dict(payload), allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        temp = self.temp_path
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as exc:
            msg = f"triggers.yaml を書けない（{self.path}）: {exc}"
            raise TriggerDefinitionsError(msg) from exc
        finally:
            temp.unlink(missing_ok=True)

    def ensure_template(self, ready_interval_default_sec: int) -> None:
        """不在なら**既定トリガー 1 件だけ**の雛形を生成する（FR-017）。

        **既存を上書きしない**（在れば 1 バイトも変えない）。初期間隔は `config.json` から
        受け取る（**コードに直書きしない**＝ FR-020）。

        Args:
            ready_interval_default_sec: 既定トリガーの初期間隔（秒）。

        Raises:
            TriggerDefinitionsError: 生成に失敗した。
        """
        if self.exists():
            return
        self.save_definitions([default_trigger(ready_interval_default_sec)])

    def _parse(self, raw: object) -> tuple[TriggerDef, ...]:
        """`yaml.safe_load` の結果を `TriggerDef` の組へ写す。

        Raises:
            TriggerDefinitionsError: トップレベルの形違い／必須欄の欠落／未知の種別。
        """
        if raw is None:
            return ()
        if not isinstance(raw, dict):
            msg = (
                f"triggers.yaml のトップレベルが写像でない（{self.path}）:"
                f" {type(raw).__name__} が来た"
            )
            raise TriggerDefinitionsError(msg)
        items = cast("dict[str, object]", raw).get(TOP_LEVEL_KEY, [])
        if not isinstance(items, list):
            msg = (
                f"triggers.yaml の `{TOP_LEVEL_KEY}` が配列でない（{self.path}）:"
                f" {type(items).__name__} が来た"
            )
            raise TriggerDefinitionsError(msg)
        return tuple(
            self._one(index, item) for index, item in enumerate(cast("list[object]", items))
        )

    def _one(self, index: int, item: object) -> TriggerDef:
        """1 件を `TriggerDef` へ写す。

        Raises:
            TriggerDefinitionsError: 写像が来ていない／必須欄の欠落／未知の種別。
        """
        if not isinstance(item, dict):
            msg = (
                f"triggers.yaml の {TOP_LEVEL_KEY}[{index}] が写像でない（{self.path}）:"
                f" {type(item).__name__} が来た"
            )
            raise TriggerDefinitionsError(msg)
        fields = dict(cast("dict[str, object]", item))
        # `spec` は**種別によらず文字列 1 本**（DIFF-11）。人が `spec: 300` と素の数で書くのは
        # ありうる編集なので、走査の段で文字列へ寄せる（構文誤りとして落とさない）。
        if isinstance(fields.get("spec"), (int, float)) and not isinstance(
            fields.get("spec"), bool
        ):
            fields["spec"] = str(fields["spec"])
        try:
            return TriggerDef.model_validate(fields)
        except ValidationError as exc:
            msg = (
                f"triggers.yaml の {TOP_LEVEL_KEY}[{index}] を読めない（{self.path}）:"
                f" {exc.error_count()} 件 / {exc}"
            )
            raise TriggerDefinitionsError(msg) from exc


def default_trigger(ready_interval_default_sec: int) -> TriggerDef:
    """既定トリガー（`ready 巡回`）を組む（F04 と雛形生成が共有する）。

    Args:
        ready_interval_default_sec: 初期間隔（秒）。

    Returns:
        `is_default=True` の `TriggerDef`（`trigger_id` は固定値）。
    """
    return TriggerDef(
        trigger_id=DEFAULT_TRIGGER_ID,
        name=DEFAULT_TRIGGER_NAME,
        description="",
        type="interval",
        spec=str(ready_interval_default_sec),
        condition=LaunchCond(),
        enabled=True,
        is_default=True,
    )


def _to_raw(definition: TriggerDef) -> dict[str, Any]:
    """`TriggerDef` を YAML へ書ける素の写像にする（**並びは data-model §6 のまま**）。"""
    return {
        "trigger_id": definition.trigger_id,
        "name": definition.name,
        "description": definition.description,
        "type": definition.type,
        "spec": definition.spec,
        "condition": {
            "labels": list(definition.condition.labels),
            "project_key": definition.condition.project_key,
        },
        "enabled": definition.enabled,
        "is_default": definition.is_default,
    }
