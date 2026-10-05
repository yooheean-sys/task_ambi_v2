"""F11 プロジェクト CRUD の候補づくりと 3 つのガード（plan (ii)）。

すべて `(current: AreaAData, …) -> tuple[AreaAData, ChangeSet]` の**純関数**であり、領域 A に
1 バイトも書かない（書くのは `service/save.py` の段 3 だけ）。

**利用契約 IF-32（`VaultReader.check_project_folders`）と IF-09（`BoardReader.has_open_issues`）
が実際に呼ばれる唯一の場所**である。`service/{members,ai_config}.py` を import しない。

本ファイルが生成する `ValidationProblem` の `kind` は **`key_rule`・`folder_missing`・
`format` の 3 語**だけである（`format` は repos の形式検査のため D24 で追加。
ほかの 2 語は保存時だけの規則＝ contracts/save-pipeline.md §5）。ほかの規則は
候補に前半の 28 規則が当たることで効く（SC-013・S-9）。
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

from common.interfaces.board import BoardUnavailable
from common.interfaces.settings import ProjectFolders, ProjectNotFound
from common.interfaces.vault import VaultPathViolation
from settings.exceptions import ProjectDeletionBlocked, validation_error
from settings.models import ChangeSet, FileWrite, ValidationProblem
from settings.repository import dump_policy_rows, dump_yaml_mapping
from settings.schemas import ProjectSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from common.interfaces.board import BoardReader
    from common.interfaces.vault import VaultReader
    from settings import SettingsConfig
    from settings.models import AreaAData

RESERVED_KEY = "_program"
"""唯一の予約プロジェクトキー（P5D-014・P3。**予約の意味をそれ以外に広げない**）。"""

RESERVED_PREFIX = "_"
"""予約の接頭辞。`RESERVED_KEY` 以外に使わせない。"""

_REMOTE_URL_RE = re.compile(r"(?:https?://\S+|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:\S+)")
"""`repos.remote_url` の形式: `http(s)://…` または SCP ライク構文（`git@host:path`）（D24）。"""

_LOCAL_PATH_RE = re.compile(r"/\S*")
"""`repos.local_path` の形式: POSIX 絶対パス（`/` 始まり・空白を含まない）（D24）。"""

LIMITS_REL = "global/limits.yaml"
"""解除のときだけ書き換える共通側のファイル（上限の上書きは PJ の外にある＝ plan D-k）。"""

PROJECT_FILES = ("project.yaml", "agents.yaml", "policy.csv")
"""登録が置く 3 ファイル（FR-033。段テンプレート上書きと共通ルールは作らない）。"""


def build_create(
    current: AreaAData, spec: ProjectSpec, *, config: SettingsConfig, vault_reader: VaultReader
) -> tuple[AreaAData, ChangeSet]:
    """F11-a 登録の候補を組む（キー規則 → 保管庫の実在確認 → 3 ファイル）。

    **前段で落ちたら後段を実行しない**（FR-034）—— キー規則に落ちたら保管庫を呼ばない。

    Args:
        current: 保持している生の構造。
        spec: 口が受けた本文。
        config: 挙動設定（`key_forbidden_chars`＝ CFG-36 の初使用点）。
        vault_reader: 保管庫の読み口（IF-32）。

    Returns:
        候補の生の構造と、触るファイルの全体（`new_project` つき）。

    Raises:
        SettingsValidationError: キー規則（`key_rule`）または実在確認（`folder_missing`）の違反。
    """
    key = spec.project_key
    problems = _check_key_rule(key, current, config)
    if problems:
        raise validation_error(problems)
    problems = _check_folders(spec, vault_reader)
    problems.extend(_check_repos(spec))
    if problems:
        raise validation_error(problems)

    raw = _project_raw(spec, is_reserved=key == RESERVED_KEY)
    candidate = dataclasses.replace(
        current,
        projects_raw={**current.projects_raw, key: raw},
        agents_raw={**current.agents_raw, key: {}},
        tasks_raw={**current.tasks_raw, key: None},
        hooks_raw={**current.hooks_raw, key: None},
        rules_raw={**current.rules_raw, key: None},
        policy_rows={**current.policy_rows, key: ()},
    )
    changes = ChangeSet(
        writes=(
            FileWrite(rel=f"projects/{key}/project.yaml", text=dump_yaml_mapping(raw)),
            FileWrite(rel=f"projects/{key}/agents.yaml", text="{}\n"),
            # D16-h (9): 登録時は**空の** `policy.csv` を置く（行が 0 件）。
            FileWrite(rel=f"projects/{key}/policy.csv", text=dump_policy_rows(())),
        ),
        new_project=key,
    )
    return candidate, changes


def build_update(
    current: AreaAData, project_key: str, spec: ProjectSpec, *, vault_reader: VaultReader
) -> tuple[AreaAData, ChangeSet]:
    """F11-b 編集の候補を組む（**編集でも実在確認を呼ぶ**＝ DIFF-14）。

    **フォルダ実体を触らない**（FR-039）。`project_key` は**ファイルに書かない**（FR-038）。
    `role_dirs` からペルソナを外したときに居るメンバがあれば **V-17** が候補の検査で落とす
    （新しい規則を足さない＝ C-07・P4）。

    Args:
        current: 保持している生の構造。
        project_key: パスのキー。
        spec: 口が受けた本文。
        vault_reader: 保管庫の読み口（IF-32）。

    Returns:
        候補の生の構造と、触るファイルの全体。

    Raises:
        ProjectNotFound: 未登録のキー（→ 404）。
        SettingsValidationError: 本文のキーがパスと食い違う（`key_rule`）／実在確認の違反。
    """
    _require_project(current, project_key)
    if spec.project_key != project_key:
        raise validation_error(
            [
                ValidationProblem(
                    where=f"projects/{project_key}/project.yaml:project_key",
                    kind="key_rule",
                    reason=(
                        f"本文のキーがパスと食い違う: 本文「{spec.project_key}」"
                        f" / パス「{project_key}」"
                    ),
                )
            ]
        )
    problems = _check_folders(spec, vault_reader)
    problems.extend(_check_repos(spec))
    if problems:
        raise validation_error(problems)

    # 予約かどうかは**現物の宣言を引き継ぐ**（本文に `is_reserved` は無い＝正本の `ProjectSpec`）。
    reserved = bool(current.projects_raw[project_key].get("is_reserved", False))
    raw = _project_raw(spec, is_reserved=reserved)
    candidate = dataclasses.replace(
        current, projects_raw={**current.projects_raw, project_key: raw}
    )
    changes = ChangeSet(
        writes=(FileWrite(rel=f"projects/{project_key}/project.yaml", text=dump_yaml_mapping(raw)),)
    )
    return candidate, changes


def build_delete(
    current: AreaAData, project_key: str, *, board_reader: BoardReader
) -> tuple[AreaAData, ChangeSet]:
    """F11-c 解除の候補を組む（予約キー → 盤面 → 退避 ＋ 上限の上書き除去）。

    **フォルダ実体を消さない**（FR-007・SC-010）。退避は rename であり、**掃除を持たない**
    （FR-032）。**待って再実行する仕組みを作らない**（憲章 §10）。

    Args:
        current: 保持している生の構造。
        project_key: 解除するキー。
        board_reader: 盤面の読み口（IF-09）。

    Returns:
        候補の生の構造と、退避 ＋（在れば）上限の上書き除去。

    Raises:
        ProjectNotFound: 未登録のキー（→ 404）。
        ProjectDeletionBlocked: 予約キー／未完了課題あり／盤面が読めない（→ 409）。
    """
    _require_project(current, project_key)
    if project_key == RESERVED_KEY or bool(
        current.projects_raw[project_key].get("is_reserved", False)
    ):
        msg = f"予約プロジェクトは解除できない: {project_key}"
        raise ProjectDeletionBlocked(msg)
    try:
        if board_reader.has_open_issues(project_key):
            msg = (
                f"未完了の課題が残っているプロジェクトは解除できない: {project_key}"
                " / 先に盤面側で片付ける"
            )
            raise ProjectDeletionBlocked(msg)
    except BoardUnavailable as exc:
        # D06-j(4)・FR-042: **真偽を推測しない・再試行しない**。拒否側に倒れる。
        msg = f"盤面が読めないため解除できない: {project_key} / {exc}"
        raise ProjectDeletionBlocked(msg) from exc

    candidate = dataclasses.replace(
        current,
        projects_raw=_without(current.projects_raw, project_key),
        agents_raw=_without(current.agents_raw, project_key),
        tasks_raw=_without(current.tasks_raw, project_key),
        hooks_raw=_without(current.hooks_raw, project_key),
        rules_raw=_without(current.rules_raw, project_key),
        policy_rows=_without(current.policy_rows, project_key),
    )
    writes: tuple[FileWrite, ...] = ()
    limits_raw = _limits_without_override(current, project_key)
    if limits_raw is not None:
        candidate = dataclasses.replace(candidate, limits_raw=limits_raw)
        writes = (FileWrite(rel=LIMITS_REL, text=dump_yaml_mapping(limits_raw)),)
    return candidate, ChangeSet(writes=writes, archive=project_key)


# -- 内部 -------------------------------------------------------------------------------


def _check_key_rule(
    key: str, current: AreaAData, config: SettingsConfig
) -> list[ValidationProblem]:
    """保存時だけの規則 1: キー規則（contracts/save-pipeline.md §5）。

    **既存キーへ遡って当てない**（適用点は新規キーだけ＝ P8・Edge Cases）。
    """
    where = "projects:project_key"
    if not key:
        return [ValidationProblem(where=where, kind="key_rule", reason="プロジェクトキーが空")]
    found = [char for char in config.key_forbidden_chars if char in key]
    if found:
        return [
            ValidationProblem(
                where=where,
                kind="key_rule",
                reason=f"プロジェクトキーに使えない文字がある: 「{key}」に {found}",
            )
        ]
    if key in current.projects_raw:
        return [
            ValidationProblem(
                where=where, kind="key_rule", reason=f"既に登録されているプロジェクトキー: {key}"
            )
        ]
    if key.startswith(RESERVED_PREFIX) and key != RESERVED_KEY:
        return [
            ValidationProblem(
                where=where,
                kind="key_rule",
                reason=f"`{RESERVED_PREFIX}` で始まるキーは `{RESERVED_KEY}` だけが使える: {key}",
            )
        ]
    return []


def _check_folders(spec: ProjectSpec, vault_reader: VaultReader) -> list[ValidationProblem]:
    """保存時だけの規則 2: フォルダの実在（IF-32。**不在の件数だけ**違反を積む＝ SC-005）。

    `VaultPathViolation`（宣言が保管庫の外を指す）は**例外として上がる**ので、`folder_missing`
    の 1 件に翻訳する（D-2。`POST` の正本は 201／422 だけで、応答コードの選択肢が 422 しかない）。
    """
    where = "projects:folders"
    folders = ProjectFolders(
        pj_input_dir=spec.folders.pj_input_dir,
        role_dirs=dict(spec.folders.role_dirs),
        outcome_dir=spec.folders.outcome_dir,
    )
    try:
        check = vault_reader.check_project_folders(folders)
    except VaultPathViolation as exc:
        return [
            ValidationProblem(
                where=where,
                kind="folder_missing",
                reason=f"宣言したフォルダが保管庫の外を指している: {exc}",
            )
        ]
    return [
        ValidationProblem(
            where=where, kind="folder_missing", reason=f"保管庫に無いフォルダ: {path}"
        )
        for path in check.missing
    ]


def _check_repos(spec: ProjectSpec) -> list[ValidationProblem]:
    """保存時だけの規則: リポジトリの形式（CFG-74・D24）。

    **形式だけを見る**。実在の確認・git へのアクセス可否は検査しない（未登録可の
    任意フィールドであり、壊れやすい検査を起動時・保存時の規則に混ぜない）。
    違反は**件数だけ積む**（FR-012 の全件列挙に倣う）。
    """
    if spec.repos is None:
        return []
    problems: list[ValidationProblem] = []
    url = spec.repos.remote_url
    if url is not None and _REMOTE_URL_RE.fullmatch(url) is None:
        problems.append(
            ValidationProblem(
                where="projects:repos.remote_url",
                kind="format",
                reason=(
                    "リモートリポジトリの URL の形式でない: "
                    f"「{url}」（http(s)://… または git@host:path の形）"
                ),
            )
        )
    path = spec.repos.local_path
    if path is not None and _LOCAL_PATH_RE.fullmatch(path) is None:
        problems.append(
            ValidationProblem(
                where="projects:repos.local_path",
                kind="format",
                reason=(
                    "ローカルリポジトリのパスが OS の絶対パスでない: 「"
                    f"{path}」（`/` で始まり空白を含まない形）"
                ),
            )
        )
    return problems


def _project_raw(spec: ProjectSpec, *, is_reserved: bool) -> dict[str, object]:
    """`project.yaml` の中身（**`project_key` を書かない**＝ FR-038）。

    `repos` は**値があるときだけ書く**（未登録のプロジェクトにキーを作らない＝ CFG-74）。
    """
    raw: dict[str, object] = {
        "name": spec.name,
        "is_reserved": is_reserved,
        "folders": {
            "pj_input_dir": spec.folders.pj_input_dir,
            "role_dirs": {persona.value: path for persona, path in spec.folders.role_dirs.items()},
            "outcome_dir": spec.folders.outcome_dir,
        },
    }
    if spec.repos is not None:
        repos_raw: dict[str, str] = {}
        if spec.repos.remote_url is not None:
            repos_raw["remote_url"] = spec.repos.remote_url
        if spec.repos.local_path is not None:
            repos_raw["local_path"] = spec.repos.local_path
        if repos_raw:
            raw["repos"] = repos_raw
    return raw


def _limits_without_override(current: AreaAData, project_key: str) -> dict[str, object] | None:
    """解除に伴う `global/limits.yaml` の書換え（plan D-k・FR-044）。

    上限の上書きだけが**プロジェクトのディレクトリの外**にあるため退避では消えない。残すと
    (a) `DELETE /settings/limits/{k}` が未登録 PJ に 404 を返すので**口からは二度と消せず**、
    (b) 起動時検査にも受け皿が無く（**V-27 は値域だけ**）、(c) 同じキーで再登録すると
    **上限だけが復活**して Edge Cases に反する。

    Returns:
        書き換えた生の構造。当該キーの上書きが無ければ `None`（**ファイルを作らない**）。
    """
    raw = current.limits_raw
    if raw is None:
        return None
    overrides = raw.get("overrides")
    if not isinstance(overrides, dict) or project_key not in overrides:
        return None
    kept = {name: value for name, value in overrides.items() if name != project_key}  # pyright: ignore[reportUnknownVariableType]  # 生の YAML なので値の型は未知のまま運ぶ
    # **`global:` を新たに書き出さない**（plan D-l）。現物に在るときだけ引き継ぐ。
    result: dict[str, object] = {key: value for key, value in raw.items() if key != "overrides"}
    result["overrides"] = kept
    return result


def _without[T](mapping: Mapping[str, T], key: str) -> dict[str, T]:
    """1 キーを落とした写し（元の mapping を変更しない）。"""
    return {name: value for name, value in mapping.items() if name != key}


def _require_project(current: AreaAData, project_key: str) -> None:
    """未登録キーを **404** の契約例外にする（FR-075）。

    Raises:
        ProjectNotFound: 未登録のキー。
    """
    if project_key not in current.projects_raw:
        raise ProjectNotFound(project_key)
