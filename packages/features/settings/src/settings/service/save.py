"""保存経路（settings-F02）の 4 段（contracts/save-pipeline.md）。

**前半の 3 段（組む・検査する・保持する）をそのまま使い、書く 1 段を足す**（FR-010）。検査規則の
正本は `specs/005-settings-read/contracts/validation-rules.md`（V-01〜V-28）であり、**本 feature で
1 行も変えない**（SC-013。同じ規則を実装した関数が 2 つ存在しない）。

段 1（候補を組む）は呼び手（`service/{projects,members,ai_config}.py`）が済ませている。
**本ファイルは `service/{projects,members,ai_config}.py` を import しない**（逆向き）。

本ファイルが生成する `ValidationProblem` の `kind` は **`orphan` の 1 語だけ**である（候補
policy の読めなさの翻訳＝ §2-2）。ほかの 12 語は前半の 28 規則が出す（S-9・SC-013）。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from common.kernel.log import LoggerFactory
from settings.exceptions import SettingsAreaError, SettingsValidationError
from settings.models import SaveOutcome, ValidationProblem
from settings.service.loader import build_enforcer, build_snapshot
from settings.service.validate import validate_commands, validate_forward, validate_reverse

if TYPE_CHECKING:
    from pathlib import Path

    from settings.models import AreaAData, ChangeSet, EffectClass
    from settings.repository import AreaAWriter
    from settings.service.crud import SettingsCrudService

_logger = LoggerFactory.get_logger("settings.service")
"""保存の成否を残す（FR-023・SC-023）。**相関 ID はログ基盤が自動で載せる**（D-6）。"""

POLICY_SUFFIX = "/policy.csv"
"""段 2-4 の対象（候補の判定器に読ませるのは権限表だけ）。"""


def save_candidate(
    crud: SettingsCrudService,
    candidate: AreaAData,
    changes: ChangeSet,
    effect: EffectClass,
) -> SaveOutcome:
    """候補を検査し、書き、差し替える（段 2〜4）。

    Args:
        crud: 生の構造・依存・差し替え先のサービスを保持している CRUD サービス。
        candidate: 変更案を重ねた候補（呼び手が組んだ**純関数の結果**）。
        changes: 触るファイルの全体。
        effect: 反映区分（即時／次回 kickoff）。

    Returns:
        差し替え後の世代と反映区分。

    Raises:
        SettingsValidationError: 候補全体の検査に落ちた（**正本は 1 バイトも変わらない**）。
        OSError: 書込に失敗した（**世代は増えない**・一時ファイルは残らない）。
    """
    staged_policies = _validate(crud, candidate, changes)
    try:
        _write(crud.writer, changes, staged_policies)
    except OSError as exc:
        _logger.warning(
            "settings.save.failed",
            extra={"error": type(exc).__name__, "files": len(changes.writes)},
        )
        raise
    outcome = _swap(crud, candidate, effect)
    _logger.info(
        "settings.save.completed",
        extra={
            "generation": outcome.generation,
            "effect": outcome.effect.value,
            "files": len(changes.writes),
        },
    )
    return outcome


def _validate(
    crud: SettingsCrudService, candidate: AreaAData, changes: ChangeSet
) -> dict[str, Path]:
    """段 2: 候補**全体**に 3 種＋候補 policy の構文検査を当てる（contracts §2-1）。

    **最初の 1 件で打ち切らない**（FR-012・SC-005）。4 つすべてを走らせてから判定する。

    Returns:
        検査を通った候補 policy の一時ファイル（段 3-1 で**そのまま置換に使う**＝ FR-012a (b)）。

    Raises:
        SettingsValidationError: 違反が 1 件以上あった（2-4 の一時ファイルは片付け済み）。
    """
    config = crud.config
    problems = validate_forward(
        candidate,
        context_reserve_tokens=config.context_reserve_tokens,
        system_actor=config.system_actor,
    )
    problems.extend(validate_reverse(candidate))
    problems.extend(validate_commands(candidate))
    staged = _stage_policies(crud.writer, changes)
    problems.extend(_check_staged_policies(crud, staged))
    if problems:
        for temporary in staged.values():
            crud.writer.discard(temporary)  # FR-012a (a): 落ちたら必ず片付ける
        _logger.warning("settings.save.rejected", extra={"problems": len(problems)})
        lines = "\n".join(f"- [{item.kind}] {item.where}: {item.reason}" for item in problems)
        msg = f"設定の検査に違反がある（{len(problems)} 件）:\n{lines}"
        raise SettingsValidationError(msg, problems)
    return staged


def _stage_policies(writer: AreaAWriter, changes: ChangeSet) -> dict[str, Path]:
    """段 2-4 の前半: 候補 policy を**置換先と同じディレクトリ**へ書き出す（010 SD-02）。

    casbin の `FileAdapter` は**パスしか受けない**ため、これ以外の形が取れない。「1 バイトも
    書かない」（FR-012）は**正本のファイル**についての言明であり、一時ファイルは対象外である。

    ⚠ **新規 PJ の `policy.csv` は対象外**である —— その置換先ディレクトリは段 3-0 で初めて
    作られる（plan D-j。段 1・段 2 では作らない）ので、ここで一時ファイルを置く先が無い。
    登録が書くのは **D16-h (9) が定める空の `policy.csv`** だけであり、行が 0 件なので構文・
    列数・空打ちのどれも検査対象を持たない。`candidate.policy_rows[<新規キー>]` は空タプルで
    あり、**逆方向検査（V-21〜V-23）とコマンド突合（V-24〜V-26）は通常どおり当たる**。
    """
    new_prefix = f"projects/{changes.new_project}/" if changes.new_project is not None else None
    return {
        write.rel: writer.stage(write.rel, write.text)
        for write in changes.writes
        if write.rel.endswith(POLICY_SUFFIX)
        and not (new_prefix is not None and write.rel.startswith(new_prefix))
    }


def _check_staged_policies(
    crud: SettingsCrudService, staged: dict[str, Path]
) -> list[ValidationProblem]:
    """段 2-4 の後半: `build_enforcer` に読ませる（構文＋列数 p=3・g=2＋空打ち＝ R-05）。

    **`repository.new_enforcer` を直接呼ばない** —— 3 つの担保が `build_enforcer` に入っている。
    読めなさは `ValidationProblem(kind="orphan")` に**翻訳して積む**（口の外へ `SettingsAreaError`
    を出さない＝ FR-078）。
    """
    problems: list[ValidationProblem] = []
    model_conf = crud.reader.model_conf_path()
    for rel, temporary in staged.items():
        try:
            build_enforcer(model_conf, temporary)
        except SettingsAreaError as exc:
            problems.append(
                ValidationProblem(where=rel, kind="orphan", reason=f"権限表が読めない: {exc}")
            )
    return problems


def _write(writer: AreaAWriter, changes: ChangeSet, staged_policies: dict[str, Path]) -> None:
    """段 3: 3-0（新規 PJ のディレクトリ）＋ 2 段書込（contracts §3・C-3）。

    途中で落ちたら**未置換の一時ファイルを全部片付け**、**3-0 で作ったディレクトリも空のまま
    片付ける**（SC-004 (b)(d)）。**変更のあったファイルだけを書く**（FR-015・D-d）。

    Raises:
        OSError: いずれかの段で失敗した（**世代は増えない**）。
    """
    staged: list[tuple[str, Path]] = []
    created_dir: str | None = None
    try:
        # 3-0: `stage` は `tempfile.mkstemp(dir=target.parent)` 形なので、親が無いと 3-1 が
        # `FileNotFoundError` で落ちる（plan D-j）。**段 1・段 2 では作らない**。
        if changes.new_project is not None:
            writer.ensure_project_dir(changes.new_project)
            created_dir = changes.new_project
        # 3-1: policy は 2-4 のものを**再利用**する（同じ一時ファイルを二度作らない＝ FR-012a (b)）
        for write in changes.writes:
            temporary = staged_policies.get(write.rel)
            staged.append(
                (
                    write.rel,
                    temporary if temporary is not None else writer.stage(write.rel, write.text),
                )
            )
        # 3-2: 全部作れてから置換する
        for rel, temporary in staged:
            writer.commit(rel, temporary)
        staged.clear()
        created_dir = None
        # 3-3: 解除（PJ の `tasks.yaml`）
        for rel in changes.removals:
            writer.remove(rel)
        # 3-4: 退避（PJ 解除）。**3-2 の後**なので、`global/limits.yaml` の `overrides` 除去は
        # 退避より前に済んでいる（plan D-k）。
        if changes.archive is not None:
            writer.archive_project(changes.archive, utc_stamp())
    finally:
        for _rel, temporary in staged:
            writer.discard(temporary)
        if created_dir is not None:
            writer.discard_project_dir(created_dir)


def utc_stamp() -> str:
    """退避先の時刻印（UTC。`_archive/projects/<key>.<stamp>/`）。"""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _swap(crud: SettingsCrudService, candidate: AreaAData, effect: EffectClass) -> SaveOutcome:
    """段 4: 台帳再導出 → 世代 +1 → 差し替え → 生の構造を進める（contracts §4）。

    **`build_snapshot` は書込の後に呼ぶ**（判定器は `policy.csv` の**パス**から組むため＝ D-b）。
    **世代を決めるのは保存経路**であり、`build_snapshot`（常に 1）・`replace_snapshot`（単調増加
    だけを検査）は **1 行も変えない**（010 SD-01・D-a）。
    """
    config = crud.config
    snapshot = build_snapshot(
        candidate,
        crud.reader,
        context_reserve_tokens=config.context_reserve_tokens,
        default_requests=config.limits_defaults.max_requests_per_kickoff,
        default_cost=config.limits_defaults.max_cost_per_kickoff,
        system_actor=config.system_actor,
    )
    snapshot = dataclasses.replace(snapshot, generation=crud.generation + 1)
    crud.apply(snapshot, candidate)
    return SaveOutcome(generation=snapshot.generation, effect=effect)
