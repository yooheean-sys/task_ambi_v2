"""F05 保存の検査（**8 通り**）と F06 起動条件の写像。

**番号（`V-1`〜`V-8`）の正本は `specs/011-trigger/contracts/trigger-http.md` §4** である。

共通の約束:

- validate は**書込の前に**全部走らせ、**違反を一覧で返す**（最初の 1 件で打ち切らない）
- **拒否したとき宣言ファイルは 1 バイトも変わらない**（FR-027・SC-009）
- 拒否は常駐にも影響しない（`add_job`／`remove_job` を呼ぶ前に落ちる）

外縁検査は `os.path.realpath` ＋ **`(st_dev, st_ino)` を親方向へ 1 段ずつ照合**する
（vault の手法。**文字列比較を使わない**＝ FR-024・M-07）。接頭辞が同じだけの兄弟フォルダや
symlink で外へ出る形を、文字列比較は捕まえられない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from common.kernel.enums import Labels
from trigger.exceptions import validation_error
from trigger.models import LaunchCond, TriggerDef
from trigger.schemas import ValidationProblemItem
from trigger.service import jobs

if TYPE_CHECKING:
    from trigger import TriggerConfig

MUTABLE_DEFAULT_FIELDS = ("spec", "enabled")
"""既定トリガーで**変更を許す 2 欄だけ**（D06-j(1)・FN-30・SD-12）。"""

GUARDED_DEFAULT_FIELDS = ("name", "description", "type", "condition")
"""既定トリガーで変更を許さない欄（違反したら `where` は**差異のあった欄名**）。"""

_DEFAULT_GUARD_REASON = "既定トリガーで変更できるのは間隔と有効／無効だけ（FN-30）"


def validate_definition(
    definition: TriggerDef,
    *,
    config: TriggerConfig,
    downloads_dir: Path | None,
    current: TriggerDef | None = None,
) -> TriggerDef:
    """保存候補を検査し、既定値を充当した宣言を返す（V-1〜V-6・V-8）。

    Args:
        definition: 保存しようとしている宣言。
        config: 挙動設定（`min_interval_sec`・`timezone` を読む）。
        downloads_dir: `DOWNLOADS_DIR`（未設定なら `None`）。
        current: 更新前の宣言（`PUT` のときだけ渡す。既定トリガーの照合に要る）。

    Returns:
        既定ラベルを充当した宣言（`is_default` は `current` の値を引き継ぐ）。

    Raises:
        TriggerValidationError: 違反が 1 件以上（**一覧**を持つ。最初の 1 件で打ち切らない）。
    """
    normalized = _fill_defaults(definition, current)
    problems: list[ValidationProblemItem] = []
    problems.extend(_check_spec(normalized, config=config, downloads_dir=downloads_dir))
    if current is not None and current.is_default:
        problems.extend(_check_default_guard(normalized, current))
    if problems:
        raise validation_error(problems)
    return normalized


def validate_delete(definition: TriggerDef) -> None:
    """削除を検査する（**V-7** のみ）。

    Args:
        definition: 削除しようとしている宣言。

    Raises:
        TriggerValidationError: 既定トリガーの削除（`where="trigger_id"`）。
    """
    if definition.is_default:
        raise validation_error(
            [
                ValidationProblemItem(
                    where="trigger_id",
                    reason=(
                        "既定トリガーは削除できない（人間回答後の再開を担う唯一の経路"
                        "＝ D04-c・FR-019）。止めたいときは有効／無効のトグルを使う"
                    ),
                )
            ]
        )


def resolve_watch_dir(base: Path, relative: str) -> str | None:
    """`watch_dir`（`DOWNLOADS_DIR` からの相対）を解決し、基準の内側なら実パスを返す。

    Args:
        base: `DOWNLOADS_DIR`。
        relative: `watch_dir`（既定 `"."`）。

    Returns:
        基準の内側にある実在のパス。外を指す／実在しないなら `None`。
    """
    return realpath_within(base, os.path.join(str(base), relative))  # noqa: PTH118  # 相対の結合は realpath の前段なので Path を挟まない


def realpath_within(base: Path, candidate: str) -> str | None:
    """`candidate` を `realpath` し、`base` の内側なら実パスを返す（**文字列比較を使わない**）。

    `(st_dev, st_ino)` を親方向へ 1 段ずつ照合する（vault の手法。M-07 で Windows 側の共有でも成立を
    実測済み）。`POST /hooks/file` の外縁検査（FR-041）もこれを使う。

    Args:
        base: 基準フォルダ。
        candidate: 検査するパス（絶対でも相対でもよい）。

    Returns:
        基準の内側にある実在のパス。外を指す／実在しないなら `None`。
    """
    real = Path(os.path.realpath(candidate))
    try:
        base_stat = Path(os.path.realpath(base)).stat()
    except OSError:
        return None
    current = real
    while True:
        try:
            stat = current.stat()
        except OSError:
            return None
        if (stat.st_dev, stat.st_ino) == (base_stat.st_dev, base_stat.st_ino):
            return str(real)
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _fill_defaults(definition: TriggerDef, current: TriggerDef | None) -> TriggerDef:
    """既定ラベル（`role:executor`）を充当し、`is_default` を引き継ぐ（F06・FR-026）。"""
    condition = definition.condition
    if not condition.labels:
        condition = condition.model_copy(update={"labels": (Labels.role_executor(),)})
    return definition.model_copy(
        update={
            "condition": condition,
            "is_default": current.is_default if current is not None else False,
        }
    )


def _check_spec(
    definition: TriggerDef, *, config: TriggerConfig, downloads_dir: Path | None
) -> list[ValidationProblemItem]:
    """種別ごとの `spec` を検査する（V-1〜V-6）。"""
    if definition.type == "cron":
        return _check_cron(definition.spec, config)
    if definition.type == "interval":
        return _check_interval(definition.spec, config)
    return _check_file(definition.spec, downloads_dir)


def _check_cron(expression: str, config: TriggerConfig) -> list[ValidationProblemItem]:
    """V-1（欄数）・V-2（欄の値）。"""
    original = jobs.check_cron(expression, config.timezone)
    if len(expression.split()) != jobs.CRON_FIELDS:
        return [
            ValidationProblemItem(
                where="spec",
                reason=(
                    f"cron は 5 欄（秒を持たない）で書く: 「{expression}」"
                    f" / APScheduler: {original}"
                ),
            )
        ]
    if original is not None:
        return [
            ValidationProblemItem(
                where="spec",
                reason=(
                    f"cron の欄の値が不正: 「{expression}」 / APScheduler: {original}"
                    " / **曜日は 0 が月曜**（標準 crontab の 7＝日曜は通らない。日曜は 6）"
                ),
            )
        ]
    return []


def _check_interval(expression: str, config: TriggerConfig) -> list[ValidationProblemItem]:
    """V-3（数として読めない）・V-4（下限未満）。"""
    try:
        seconds = int(expression)
    except ValueError:
        return [
            ValidationProblemItem(
                where="spec",
                reason=f"interval は秒を表す整数を指定する: 「{expression}」",
            )
        ]
    if seconds < config.min_interval_sec:
        return [
            ValidationProblemItem(
                where="spec",
                reason=(
                    f"interval が下限を下回る: {seconds} 秒"
                    f" / 下限は {config.min_interval_sec} 秒（min_interval_sec）"
                ),
            )
        ]
    return []


def _check_file(watch_dir: str, downloads_dir: Path | None) -> list[ValidationProblemItem]:
    """V-6（`DOWNLOADS_DIR` 未設定）・V-5（外縁検査）。"""
    if downloads_dir is None:
        return [
            ValidationProblemItem(
                where="type",
                reason=(
                    "DOWNLOADS_DIR が設定されていないので file 種別のトリガーは保存できない"
                    "（.env に DL フォルダの絶対パスを設定する＝ CFG-09）"
                ),
            )
        ]
    resolved = resolve_watch_dir(downloads_dir, watch_dir)
    if resolved is None:
        return [
            ValidationProblemItem(
                where="spec",
                reason=(
                    f"watch_dir が監視対象の外を指している（または実在しない）: 「{watch_dir}」"
                    f" / 基準: {Path(os.path.realpath(downloads_dir))}"
                    f" / 解決後の実パス: {Path(os.path.realpath(os.path.join(str(downloads_dir), watch_dir)))}"  # noqa: E501,PTH118  # 拒否理由に両方を載せる（FR-024）
                ),
            )
        ]
    return []


def _check_default_guard(candidate: TriggerDef, current: TriggerDef) -> list[ValidationProblemItem]:
    """V-8: 既定トリガーで**許されない変更**を拾う（`where` は差異のあった欄名）。"""
    problems: list[ValidationProblemItem] = []
    for field in GUARDED_DEFAULT_FIELDS:
        if _differs(field, getattr(candidate, field), getattr(current, field)):
            problems.append(
                ValidationProblemItem(
                    where=field,
                    reason=(
                        f"{_DEFAULT_GUARD_REASON}: 「{field}」は変更できない"
                        f"（現在値: {getattr(current, field)}）"
                    ),
                )
            )
    return problems


def _differs(field: str, candidate: object, current: object) -> bool:
    """1 欄の差異を判定する（**`condition.labels` は集合として比較**＝ SD-12）。"""
    if (
        field == "condition"
        and isinstance(candidate, LaunchCond)
        and isinstance(current, LaunchCond)
    ):
        return (set(candidate.labels), candidate.project_key) != (
            set(current.labels),
            current.project_key,
        )
    return candidate != current
