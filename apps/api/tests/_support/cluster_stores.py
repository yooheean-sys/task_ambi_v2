"""クラスタ層の一時外部ストア（実 beads・実 vault・設定ファイル領域 A／B）。規約 §5・plan (iii)。

クラスタ層が触ってよい外部ストアは**一時ディレクトリの実物だけ**である。本モジュールはその
実物を `tmp_path` の下に作る手段を集めたもので、**実運用のストアには一切触れない**。

出所（`contract/` 配下は `integration/` から import できない＝ R-01 のため**同型移植**）:

- beads・vault・領域 A: `apps/api/tests/contract/contract_stores.py` の写し
- `require_bd_or_skip`: `apps/api/tests/contract/conftest.py` の `bd_bin`（**skip 版**）の写し
- 領域 B・履歴 csv・DL フォルダ: `packages/features/trigger/tests/conftest.py` の手順の**書き直し**
  （feature の `tests/` を import しない＝憲章 §1）

クラスタ別の差分は**引数で吸収**し、写しを増やさない。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404  外部ツールは公約インターフェース（CLI）経由でのみ触る
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from common.interfaces.settings import ProjectFolders
from common.kernel.config import find_repo_root
from common.kernel.enums import PersonaKey, ToolName

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# ------------------------------------------------------------------ 外部ツール `bd` の解決

BD_COMMAND = "bd"


def require_bd_or_skip() -> str:
    """`bd` 実行ファイルの絶対パス（見つからなければ **skip**）。

    ルート `apps/api/tests/conftest.py` の同名 fixture は `pytest.fail` する版だが、クラスタ側は
    `pytest.skip` で上書きする（R-13）。`bd` を持たない環境で `-m "not requires_bd"` を使わずに
    走らせても止まらないようにするためである。ルートの conftest は**変更しない**。

    Returns:
        `bd` の絶対パス。
    """
    resolved = shutil.which(BD_COMMAND)
    if resolved is None:
        pytest.skip(
            f"外部ツール '{BD_COMMAND}' が PATH 上に無い"
            '（クラスタ 02・03・01・04 は `uv run pytest -m "not requires_bd"` で外せる）'
        )
    return resolved


# ------------------------------------------------------------------ beads（board）

BEADS_INIT_TIMEOUT_SEC = 120
"""`bd init` は embedded Dolt の初期化と git commit まで行うので長めに取る。"""

BEADS_SEED_TIMEOUT_SEC = 120
"""仕込みの `bd create` 等も embedded Dolt の書込を伴う。"""

BEADS_CONFIG_FILENAME = "config.yaml"
"""board の起動時検査 (c) が見る目印（`BEADS_DIR` 直下にある必要がある）。"""


@dataclass(frozen=True, slots=True)
class TempBeads:
    """一時 beads ワークスペース（実 `bd init` 版）。"""

    workspace: Path
    """`bd init` を打った作業ディレクトリ（`.beads` の親）。"""

    beads_dir: Path
    """`BEADS_DIR` に渡す値（`.beads` **自身**＝ D19-a・D19-b）。"""

    project_key: str
    """`bd init -p` に渡したプロジェクトキー。"""

    bd_bin: str
    """`bd` 実行ファイルの絶対パス（仕込みの `bd` 呼び出しに使う）。"""


def assert_outside_repo(tmp_path: Path) -> None:
    """一時ディレクトリがリポジトリの外にあることを確かめる（止め木 4 枚目。規約 §5）。

    `bd init` は `.beads/` だけでなく git リポジトリを作り commit まで行い、`AGENTS.md`・
    `CLAUDE.md`・`.claude/` も生成する。`TMPDIR` がリポジトリ内を指す環境で事故らないよう、
    **実 `bd init` の前に必ず通す**。
    """
    assert find_repo_root() not in tmp_path.parents, (
        f"一時ディレクトリがリポジトリ内にある: {tmp_path}"
        " / bd init はリポジトリ外でのみ実行する（TMPDIR を確認する）"
    )


def init_beads(tmp_path: Path, bd_bin: str, *, project_key: str = "cluster") -> TempBeads:
    """`tmp_path/ws` で実 `bd init -p <key>` を打ち、`.beads` を返す。

    Args:
        tmp_path: 一時ディレクトリ（**リポジトリ外**であること）。
        bd_bin: `bd` 実行ファイルの絶対パス。
        project_key: `bd init -p` に渡すプロジェクトキー。

    Returns:
        作った一時 beads ワークスペース。
    """
    assert_outside_repo(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(  # noqa: S603  bd_bin は fixture が解決した実行ファイル
        [bd_bin, "init", "-p", project_key],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
        timeout=BEADS_INIT_TIMEOUT_SEC,
    )
    return TempBeads(
        workspace=workspace,
        beads_dir=workspace / ".beads",
        project_key=project_key,
        bd_bin=bd_bin,
    )


def remove_beads(beads: TempBeads) -> None:
    """`.git` と embedded Dolt を残さず消す（pytest は直近 3 回分の tmp を保持する）。"""
    shutil.rmtree(beads.workspace, ignore_errors=True)


def make_light_beads(tmp_path: Path, *, name: str = "light") -> Path:
    """`bd init` を**打たない**軽量 beads（data-model §4）。

    board の起動時検査 (b)（実在）・(c)（直下に `config.yaml`）を通せば足りる。`BD_BIN` を
    「常に失敗するシム」に向けるテストは盤面の中身を一切使わないのでこれで十分であり、実
    `bd init` の回数を大きく減らせる。

    Returns:
        `BEADS_DIR` に渡す値。
    """
    beads_dir = tmp_path / name / ".beads"
    beads_dir.mkdir(parents=True)
    (beads_dir / BEADS_CONFIG_FILENAME).write_text("", encoding="utf-8")
    return beads_dir


def run_bd(beads: TempBeads, argv: Sequence[str], *, actor: str = "cluster_seed") -> str:
    """一時 beads に対して `bd` を 1 回打つ（**仕込み専用**）。

    テストの検証は必ず実装（board の Protocol）を通す。本関数は「盤面をこの形にしてから
    始める」ための準備手段であり、**期待値の観測には使わない**（観測に使うと board を
    経由しない読み取りになる）。ただし `bd` の返す JSON を確認する用途（起票 ID の取得）
    には使う。

    Args:
        beads: 対象の一時 beads。
        argv: `bd` に渡す引数（サブコマンドから）。
        actor: `BEADS_ACTOR` に載せる値（board と同じく**書込のたびに注入する**＝ R-09）。

    Returns:
        標準出力（JSON を要求した場合はその本文）。
    """
    completed = subprocess.run(  # noqa: S603  bd_bin は fixture が解決した実行ファイル
        [beads.bd_bin, *argv],
        cwd=beads.workspace,
        capture_output=True,
        text=True,
        check=True,
        timeout=BEADS_SEED_TIMEOUT_SEC,
        env={**_bd_env(beads), "BEADS_ACTOR": actor},
    )
    return completed.stdout


def _bd_env(beads: TempBeads) -> dict[str, str]:
    """仕込みの `bd` 呼び出しに渡す環境（`BEADS_DIR` を一時 beads へ向ける）。"""
    return {**os.environ, "BEADS_DIR": str(beads.beads_dir)}


def seed_issue(
    beads: TempBeads,
    *,
    title: str,
    labels: Sequence[str],
    acceptance: str = "受け入れ条件",
    description: str = "仕込みの課題",
    parent: str | None = None,
    priority: int = 2,
    issue_type: str = "task",
    actor: str = "cluster_seed",
) -> str:
    """一時 beads に課題を 1 件仕込み、その issue_id を返す。

    argv の形は `board/service.py` の `create` に揃える（`--title`・`--no-inherit-labels`・
    `-l` の繰り返し）。**ラベルの継承を必ず切る** —— 入ると段判定が壊れる。

    Args:
        beads: 対象の一時 beads。
        title: 表題。
        labels: 付けるラベル（`Labels.kind(...)` 等で組んだもの）。
        acceptance: 受け入れ条件（完了ゲートのトークン抽出に使われる）。
        description: 本文。
        parent: 親 issue_id（無ければ `None`）。
        priority: 優先度。
        issue_type: 課題種別。
        actor: 起票者。

    Returns:
        起票された issue_id。
    """
    argv: list[str] = [
        "create",
        "--title",
        title,
        "-d",
        description,
        "-p",
        str(priority),
        "-t",
        issue_type,
    ]
    if parent is not None:
        argv.extend(("--parent", parent))
    argv.append("--no-inherit-labels")
    for label in labels:
        argv.extend(("-l", label))
    argv.extend(("--acceptance", acceptance, "--json"))
    payload: object = json.loads(run_bd(beads, argv, actor=actor))
    return _issue_id_of(payload)


def link_dependency(
    beads: TempBeads, from_id: str, to_id: str, *, actor: str = "cluster_seed"
) -> None:
    """`from_id` が `to_id` に blocked_by される辺を張る（仕込み専用）。

    ⚠ **`--parent` の辺では代用できない** —— `open_question_of` は課題の `dependencies` から
    `kind:question` を探すので、親子関係だけでは未回答の質問として拾われない（P5D-059 (2) と
    同じ理由。2026-09-01 に実測）。

    Args:
        beads: 対象の一時 beads。
        from_id: 依存する側（質問を待つ課題）。
        to_id: 依存される側（質問）。
        actor: 行為者。
    """
    run_bd(beads, ["dep", "add", from_id, "--blocked-by", to_id, "--json"], actor=actor)


def seed_comment(beads: TempBeads, issue_id: str, body: str, *, actor: str) -> None:
    """課題へコメントを 1 件仕込む（仕込み専用）。

    argv の形は `board/service.py` の `comment` に揃える —— **`--` 区切りが必須**で `--json` は
    その**前**に置く（M-28。`-m` は受け付けない＝ 2026-09-01 に実測）。

    Args:
        beads: 対象の一時 beads。
        issue_id: 対象の課題。
        body: 本文。
        actor: 行為者（人間の宣言と突き合わせるので**必ず指定する**）。
    """
    run_bd(beads, ["comment", issue_id, "--json", "--", body], actor=actor)


def _issue_id_of(payload: object) -> str:
    """`bd create --json` の応答から issue_id を取り出す（1 件だけを想定）。"""
    entry: object = payload
    if isinstance(entry, dict):
        mapping = cast("dict[str, object]", entry)
        for key in ("issues", "data", "results"):
            nested: object = mapping.get(key)
            if isinstance(nested, list) and cast("list[object]", nested):
                entry = cast("list[object]", nested)[0]
                break
    if isinstance(entry, list) and cast("list[object]", entry):
        entry = cast("list[object]", entry)[0]
    if isinstance(entry, dict):
        identifier: object = cast("dict[str, object]", entry).get("id")
        if isinstance(identifier, str):
            return identifier
    msg = f"bd create --json の応答から issue_id を取り出せない: {payload!r}"
    raise AssertionError(msg)


# ------------------------------------------------------------------ vault

VAULT_INPUT_NAME = "input.md"
VAULT_INPUT_BODY = "PJ 入力の本文\n"
VAULT_ROLE_NAME = "role_note.md"
VAULT_ROLE_BODY = "役割フォルダの本文\n"
VAULT_OUTCOME_NAME = "outcome.md"
VAULT_OUTCOME_BODY = "成果物の本文\n"
OUTSIDE_NAME = "secret.md"
OUTSIDE_BODY = "OUTSIDE\n"


@dataclass(frozen=True, slots=True)
class TempVault:
    """一時 vault（`VAULT_ROOT = tmp_path/vault`）と、その**外側**（`tmp_path/outside`）。"""

    root: Path
    """`VAULT_ROOT` に渡す値。"""

    outside: Path
    """vault ルートの**外**にある実在フォルダ（`VaultPathViolation` の反例用）。"""

    folders: ProjectFolders
    """規定どおりに実在する 3 領域の宣言。"""

    outside_folders: ProjectFolders
    """`pj_input_dir` が vault ルートの外を指す宣言（`kind="outside_root"` を踏む）。"""

    outcome_dir: Path
    """成果物フォルダの実パス（完了ゲート用の成果物を**後から置く**口＝ 04）。"""


def make_temp_vault(
    tmp_path: Path,
    *,
    project_dir: str = "pj",
    personas: Sequence[PersonaKey] = (PersonaKey.programmer, PersonaKey.writer),
    with_existing_outcome: bool = False,
) -> TempVault:
    """PJ 入力・役割・outcome の 3 領域と、ルート外の 1 ファイルを持つ木を作る。

    ルート外を表すのは `tmp_path/outside/`（vault ルートは `tmp_path/vault`＝ 1 階層下）。

    Args:
        tmp_path: 一時ディレクトリ。
        project_dir: vault ルート相対の PJ 入力フォルダ名。
        personas: 役割フォルダを作るペルソナ（先頭が `role_dirs` の主）。
        with_existing_outcome: `outcome/` に既存ファイルを 1 件置く（03 の上書き退避の観測用）。

    Returns:
        組み上がった一時 vault の記述。
    """
    root = tmp_path / "vault"
    (root / project_dir).mkdir(parents=True)
    for persona in personas:
        (root / project_dir / "roles" / str(persona)).mkdir(parents=True)
    outcome_dir = root / project_dir / "outcome"
    outcome_dir.mkdir(parents=True)
    (root / project_dir / VAULT_INPUT_NAME).write_text(
        VAULT_INPUT_BODY, encoding="utf-8", newline="\n"
    )
    if personas:
        (root / project_dir / "roles" / str(personas[0]) / VAULT_ROLE_NAME).write_text(
            VAULT_ROLE_BODY, encoding="utf-8", newline="\n"
        )
    if with_existing_outcome:
        (outcome_dir / VAULT_OUTCOME_NAME).write_text(
            VAULT_OUTCOME_BODY, encoding="utf-8", newline="\n"
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / OUTSIDE_NAME).write_text(OUTSIDE_BODY, encoding="utf-8", newline="\n")
    folders = ProjectFolders(
        pj_input_dir=project_dir,
        role_dirs={persona: f"{project_dir}/roles/{persona}" for persona in personas},
        outcome_dir=f"{project_dir}/outcome",
    )
    outside_folders = ProjectFolders(
        pj_input_dir="../outside",
        role_dirs={persona: "../outside" for persona in personas},
        outcome_dir="../outside",
    )
    return TempVault(
        root=root,
        outside=outside,
        folders=folders,
        outside_folders=outside_folders,
        outcome_dir=outcome_dir,
    )


# ------------------------------------------------------------------ 設定ファイル領域 A

AREA_A_PROJECT_KEY = "clusterpj"
"""一時領域 A が持つ唯一のプロジェクト。"""

AREA_A_PERSONA = PersonaKey.programmer
"""一時領域 A が持つ唯一のメンバのペルソナ。"""

AREA_A_COMMAND = "demo"
"""`commands.yaml` に置く唯一のコマンド（`policy.csv` から参照させる＝ V-25 死に定義の禁止）。"""

AREA_A_COMMAND_ARGV = ("/bin/echo", "hello")
"""`commands.yaml` の `argv`（**2 要素**）。

`resolve_command` の要点は**分かち書きされた並び**である。1 本の文字列へ連結して返す実装と
区別できるよう、宣言側も期待値側もこの 1 つの定数から取る（テストへ直書きしない）。
"""

RESERVED_PROJECT_KEY = "_program"
"""予約プロジェクトのキー（`settings/service/projects.py` の `RESERVED_KEY` と同値）。"""

AREA_A_HUMAN_ACTOR = "human_owner"
"""`humans.yaml` に置く人間の行為者（`is_human` が真を返す唯一の値）。"""

AREA_A_HOOK_NAME = "cluster-hook"
AREA_A_HOOK_POINTS = "PRE_TOOL_CALL"

AREA_A_LIMITS_GLOBAL_REQUESTS = 5
AREA_A_LIMITS_GLOBAL_COST = 0.5
AREA_A_LIMITS_PJ_REQUESTS = 9
AREA_A_LIMITS_PJ_COST = 1.25

AREA_A_TASK_OVERRIDE_KEY = "child"
AREA_A_TASK_PJ_DESCRIPTION = "PJ 上書きの作業指示: {acceptance}"
AREA_A_TASK_PJ_EXPECTED_OUTPUT = "PJ 上書きの期待成果"

AREA_A_GLOBAL_ROLE = "クラスタテストの共通ロール"
AREA_A_GLOBAL_GOAL = "クラスタテストの共通ゴール"
AREA_A_GLOBAL_BACKSTORY = "クラスタテストの共通バックストーリ"
AREA_A_GLOBAL_MAX_ITER = 3

DEFAULT_ALLOWED_TOOLS: tuple[ToolName, ...] = (ToolName.read_issue, ToolName.record_progress)
"""領域 A の既定で許可する Tool（`policy.csv` の allow 行と `agents.yaml` の `tools`）。"""

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
"""`llm_profiles.yaml` の既定 `base_url`（偽 LLM サーバを使うクラスタは引数で差し替える）。"""

DEFAULT_API_KEY_REF = "LLM_API_KEY_CLUSTER"
"""`llm_profiles.yaml` の既定 `api_key_ref`（`.env` に実値 1 件を置くクラスタが使う）。"""

RULES_BODY = "PJ 共通ルールの本文\n"


@dataclass(frozen=True, slots=True)
class TempAreaA:
    """一時の設定ファイル領域 A（`SETTINGS_ROOT = tmp_path/area_a`）。"""

    root: Path
    """`SETTINGS_ROOT` に渡す値。"""

    project_key: str
    """登録済みの唯一のプロジェクトキー。"""

    persona: PersonaKey
    """登録済みの唯一のメンバのペルソナ。"""

    command_name: str
    """`commands.yaml` に在るコマンド名。"""

    human_actor: str
    """`humans.yaml` に在る人間の行為者。"""

    allowed_tools: tuple[ToolName, ...]
    """`policy.csv` が許可した Tool（許可されていない Tool は暗黙 deny＝ 03 #1）。"""

    api_key_ref: str
    """`llm_profiles.yaml` の `api_key_ref`（`.env` のキー名）。"""

    command_argv: tuple[str, ...]
    """`commands.yaml` に登録した `argv`（期待値もここから取る＝テストへ直書きしない）。"""

    spare_project_key: str | None
    """`commands.yaml` を参照**しない** 2 つ目のプロジェクト（解除の正常系に要る）。

    ⚠ **唯一の PJ は解除できない** —— 解除は `policy.csv` ごと退避するので、`commands.yaml` の
    定義が「policy から 1 行も参照されない死に定義」（V-25）になり、保存前の検査が 409 で
    弾く（`010` B-51 と同型。2026-09-01 に実測）。解除の正常系（02 #8）はこの 2 つ目を消す。
    """


def _write(root: Path, rel: str, text: str) -> None:
    """領域 A ルート相対のパスへ本文を書く（親フォルダも作る）。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _agents_global_body(personas: Sequence[PersonaKey], tools: Sequence[ToolName]) -> str:
    """④ 段の `global/agents.yaml`（V-04 の必須 3 種＋許可フィールドを埋める）。

    **出荷時既定を写させない**（`_scaffold` は不在のときだけ書くので、先に置けば上書きされない）。
    """
    listed = "\n".join(f"    - {tool}" for tool in tools)
    return "".join(
        f"{persona}:\n"
        f"  role: |-\n    {AREA_A_GLOBAL_ROLE}\n"
        f"  goal: |-\n    {AREA_A_GLOBAL_GOAL}\n"
        f"  backstory: |-\n    {AREA_A_GLOBAL_BACKSTORY}\n"
        f"  tools:\n{listed}\n"
        f"  max_iter: {AREA_A_GLOBAL_MAX_ITER}\n"
        for persona in personas
    )


def _profile_body(*, base_url: str, api_key_ref: str) -> str:
    """検査を通る最小の `llm_profiles.yaml`（`api_key` の**実値**は持たない）。"""
    return (
        "profiles:\n"
        "  default:\n"
        "    provider_kind: openai_compatible\n"
        f"    base_url: {base_url}\n"
        "    model_id: demo-model\n"
        "    context_window: 32768\n"
        f"    api_key_ref: {api_key_ref}\n"
        "default: default\n"
    )


def _commands_body(command: str, argv: Sequence[str]) -> str:
    """トップレベルは `commands:`。`argv` は空でない文字列の一覧（V-26）。"""
    listed = "\n".join(f"      - {word}" for word in argv)
    return f"commands:\n  {command}:\n    argv:\n{listed}\n"


def _limits_body(project_key: str) -> str:
    """`global/limits.yaml`（CFG-39 の ④ と ⑤ を**どちらも**置く。V-27 を満たす正の値）。"""
    return (
        "global:\n"
        f"  max_requests_per_kickoff: {AREA_A_LIMITS_GLOBAL_REQUESTS}\n"
        f"  max_cost_per_kickoff: {AREA_A_LIMITS_GLOBAL_COST}\n"
        "overrides:\n"
        f"  {project_key}:\n"
        f"    max_requests_per_kickoff: {AREA_A_LIMITS_PJ_REQUESTS}\n"
        f"    max_cost_per_kickoff: {AREA_A_LIMITS_PJ_COST}\n"
    )


VAULT_GRANTS: tuple[tuple[str, str], ...] = (
    ("vault:pj_input", "read"),
    ("vault:role", "read"),
    ("vault:outcome", "read"),
    ("vault:outcome", "write"),
)
"""保管庫の道具が**2 段目**に要求する対象と行為（`executor/tools/base.py` の `obj_for_vault`）。

⚠ **1 段目（`tool:<name>`）だけを許可しても道具は動かない** —— `write_outcome` は
`vault:outcome / write` を、読取 3 種は該当領域の `read` を追加で要求し、行が無ければ
`tool_denied` になる（2026-09-01 に実測）。`policy.csv` の allow 行はこの 2 段を**両方**置く。
"""


REPO_GRANTS: tuple[tuple[str, str], ...] = (("repo:files", "read"), ("repo:files", "write"))
"""repo 系道具の 2 段目（D25-a）。`repo_local_path` を渡したときだけ置く。"""


def _policy_body(
    project_key: str,
    personas: Sequence[PersonaKey],
    tools: Sequence[ToolName],
    *,
    vault_grants: Sequence[tuple[str, str]] = VAULT_GRANTS,
) -> str:
    """`policy.csv`（**許可行だけ**を並べる。行の無い組は暗黙 deny＝ 03 #1 の作り方）。"""
    lines: list[str] = []
    for persona in personas:
        subject = f"member:{project_key}.{persona}"
        lines.extend(f"p, {subject}, tool:{tool}, execute" for tool in tools)
        lines.append(f"p, {subject}, cmd:{AREA_A_COMMAND}, execute")
        lines.extend(f"p, {subject}, {obj}, {act}" for obj, act in vault_grants)
    return "".join(f"{line}\n" for line in lines)


def make_temp_area_a(
    tmp_path: Path,
    *,
    project_key: str = AREA_A_PROJECT_KEY,
    persona: PersonaKey = AREA_A_PERSONA,
    allowed_tools: Sequence[ToolName] = DEFAULT_ALLOWED_TOOLS,
    declared_tools: Sequence[ToolName] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    api_key_ref: str = DEFAULT_API_KEY_REF,
    project_folders: Mapping[str, object] | None = None,
    spare_project_key: str | None = None,
    with_reserved_project: bool = False,
    with_hooks: bool = True,
    command_argv: Sequence[str] = AREA_A_COMMAND_ARGV,
    extra_personas: Sequence[PersonaKey] = (),
    repo_local_path: str | None = None,
) -> TempAreaA:
    """`build()` の整合検査 3 種を通る最小の領域 A を組む。

    `build()` の `_scaffold` が**不在のときだけ**生成するのは 6 件
    （`global/model.conf`・`global/agents.yaml`・`global/tasks.yaml`・`global/llm_profiles.yaml`・
    `global/commands.yaml`・`global/humans.yaml`）である。このうち後ろ 3 件は**空の骨格**が
    入るため、正常系を作るには**先に自前の内容を置いて**上書きを避ける必要がある。

    Args:
        tmp_path: 一時ディレクトリ。
        project_key: 登録するプロジェクトキー。
        persona: 登録するメンバのペルソナ。
        allowed_tools: `policy.csv` に allow 行を置く Tool（置かない Tool は暗黙 deny）。
        declared_tools: `agents.yaml` の `tools`（`None` なら `allowed_tools` と同じ）。
            allow していない Tool を宣言に載せたい 03 #1 が使う。
        base_url: `llm_profiles.yaml` の `base_url`（偽 LLM サーバの実 URL を渡す）。
        api_key_ref: `llm_profiles.yaml` の `api_key_ref`（`.env` のキー名）。
        project_folders: `project.yaml` の `folders`（`None` なら vault の既定の形）。
        spare_project_key: 2 つ目のプロジェクトのキー（`None` なら作らない）。`commands.yaml`
            を参照しない `policy.csv` を持つので、**これを解除しても死に定義にならない**。
        with_reserved_project: 予約キー `_program` の登録を置く（解除不可の観測に要る。
            置かないと `DELETE /settings/projects/_program` は「未登録」で 404 になり、
            予約キーの分岐に届かない）。
        extra_personas: `persona` に加えて登録するメンバのペルソナ。**レビュー段の担当
            （`orchestrator/config.json` の `review_persona`＝ `planner`）を要るクラスタは
            ここで足す** —— 居ないと `member_resolver.resolve` が `MemberNotFound` になり、
            レビュー run が人間へ渡す経路に落ちる（2026-09-01 に実測）。V-17（メンバ ⊆
            `role_dirs`）と V-19（メンバ ⊆ policy 主体）も同時に満たす形で書き出す。
        command_argv: `commands.yaml` の `argv`（既定は `/bin/echo hello`）。**副作用の
            ある argv を渡せば**「登録された並びのまま起動された」ことを痕跡で確かめられる。
        with_hooks: `hooks.yaml` に宣言を 1 件置く。**executor を有効 MOD に含むクラスタでは
            偽にする** —— `executor/config.json` の `hook_catalog` は**空**であり、台帳に無い
            名前の宣言があると `build` 段で `ExecutorFailure` になる（2026-09-01 に実測）。
            `config.json` は 1 行も変えないので、宣言の側を 0 件にして避ける。
        repo_local_path: `project.yaml` の `repos.local_path`（D25）。渡すと全メンバに
            `repo:files` の read・write の権限行も置く（repo 系道具の 2 段目を通す）。
            `None` ならリポジトリ未登録のプロジェクトになる（従来どおり）。

    Returns:
        組み上がった一時領域 A の記述。
    """
    root = tmp_path / "area_a"
    root.mkdir(parents=True)
    declared = tuple(declared_tools) if declared_tools is not None else tuple(allowed_tools)
    # `_scaffold` が空の骨格・出荷時既定で埋めてしまうものを先に置く。
    _write(
        root,
        "global/llm_profiles.yaml",
        _profile_body(base_url=base_url, api_key_ref=api_key_ref),
    )
    _write(root, "global/commands.yaml", _commands_body(AREA_A_COMMAND, command_argv))
    _write(root, "global/humans.yaml", f"human_actors:\n  - {AREA_A_HUMAN_ACTOR}\n")
    personas = (persona, *extra_personas)
    _write(root, "global/agents.yaml", _agents_global_body(personas, declared))
    # `limits.yaml` は `_scaffold` の対象外なので、置かないと解決が ③ 段しか通らない。
    _write(root, "global/limits.yaml", _limits_body(project_key))
    # PJ 側は `_scaffold` の対象外（登録は CRUD の領分）なのですべて自前で置く。
    folders_body = _project_folders_body(personas, project_folders)
    repos_body = f"repos:\n  local_path: {repo_local_path}\n" if repo_local_path else ""
    _write(
        root,
        f"projects/{project_key}/project.yaml",
        f"name: クラスタテスト\nis_reserved: false\n{folders_body}{repos_body}",
    )
    _write(
        root,
        f"projects/{project_key}/agents.yaml",
        "".join(f"{name}:\n  goal: |-\n    PJ 固有ゴール\n" for name in personas),
    )
    _write(
        root,
        f"projects/{project_key}/tasks.yaml",
        f"{AREA_A_TASK_OVERRIDE_KEY}:\n"
        f"  description: |-\n    {AREA_A_TASK_PJ_DESCRIPTION}\n"
        f"  expected_output: |-\n    {AREA_A_TASK_PJ_EXPECTED_OUTPUT}\n",
    )
    _write(
        root,
        f"projects/{project_key}/policy.csv",
        _policy_body(
            project_key,
            personas,
            allowed_tools,
            vault_grants=(*VAULT_GRANTS, *REPO_GRANTS) if repo_local_path else VAULT_GRANTS,
        ),
    )
    _write(root, f"projects/{project_key}/rules.md", RULES_BODY)
    if with_hooks:
        _write(
            root,
            f"projects/{project_key}/hooks.yaml",
            f"hooks:\n  - name: {AREA_A_HOOK_NAME}\n    points: {AREA_A_HOOK_POINTS}\n"
            f"    agents:\n      - {persona}\n",
        )
    else:
        _write(root, f"projects/{project_key}/hooks.yaml", "hooks: []\n")
    if spare_project_key is not None:
        _write_spare_project(root, spare_project_key, persona, allowed_tools)
    if with_reserved_project:
        _write_reserved_project(root, persona)
    return TempAreaA(
        root=root,
        project_key=project_key,
        persona=persona,
        command_name=AREA_A_COMMAND,
        human_actor=AREA_A_HUMAN_ACTOR,
        allowed_tools=tuple(allowed_tools),
        api_key_ref=api_key_ref,
        command_argv=tuple(command_argv),
        spare_project_key=spare_project_key,
    )


def _write_spare_project(
    root: Path, key: str, persona: PersonaKey, allowed_tools: Sequence[ToolName]
) -> None:
    """2 つ目のプロジェクトを置く（`commands.yaml` を**参照しない** `policy.csv`）。

    cmd 行を持たせない —— 持たせるとこちらを解除したときに `commands.yaml` の定義が
    死に定義になり、結局どちらも解除できなくなる（V-25）。
    """
    _write(
        root,
        f"projects/{key}/project.yaml",
        "name: 解除できるプロジェクト\nis_reserved: false\n"
        + _project_folders_body((persona,), None),
    )
    _write(root, f"projects/{key}/agents.yaml", f"{persona}:\n  goal: |-\n    予備 PJ のゴール\n")
    _write(
        root,
        f"projects/{key}/policy.csv",
        "".join(f"p, member:{key}.{persona}, tool:{tool}, execute\n" for tool in allowed_tools),
    )
    _write(root, f"projects/{key}/rules.md", RULES_BODY)


def _write_reserved_project(root: Path, persona: PersonaKey) -> None:
    """予約キー `_program` の登録を置く（**メンバを持たせない**）。

    メンバを置くと V-19（メンバ ⊆ policy 主体）が `policy.csv` の行を要求するので、
    `agents.yaml` は空にする。`is_reserved: true` が解除不可の分岐の入口である。
    """
    _write(
        root,
        f"projects/{RESERVED_PROJECT_KEY}/project.yaml",
        f"name: プログラム\nis_reserved: true\n{_project_folders_body((persona,), None)}",
    )
    _write(root, f"projects/{RESERVED_PROJECT_KEY}/agents.yaml", "{}\n")
    _write(root, f"projects/{RESERVED_PROJECT_KEY}/policy.csv", "")
    _write(root, f"projects/{RESERVED_PROJECT_KEY}/rules.md", RULES_BODY)


def _project_folders_body(
    personas: Sequence[PersonaKey], folders: Mapping[str, object] | None
) -> str:
    """`project.yaml` の `folders:` 節を組む（`None` なら vault の既定の形）。

    **メンバに登録するペルソナは全員ぶん `role_dirs` に書く**（V-17。欠けると保存も起動も
    落ちる）。
    """
    if folders is None:
        listed = "".join(f"    {name}: pj/roles/{name}\n" for name in personas)
        return f"folders:\n  pj_input_dir: pj\n  role_dirs:\n{listed}  outcome_dir: pj/outcome\n"
    role_dirs: object = folders.get("role_dirs", {})
    listed = ""
    if isinstance(role_dirs, dict):
        listed = "".join(
            f"    {key}: {value}\n" for key, value in cast("dict[str, object]", role_dirs).items()
        )
    return (
        "folders:\n"
        f"  pj_input_dir: {folders.get('pj_input_dir', 'pj')}\n"
        "  role_dirs:\n"
        f"{listed}"
        f"  outcome_dir: {folders.get('outcome_dir', 'pj/outcome')}\n"
    )


# ------------------------------------------------------------------ 設定ファイル領域 B（trigger）

AREA_B_DEFAULT_TRIGGER_ID = "ready-sweep"
"""既定トリガーの固定 ID（`trigger/models.py` の `DEFAULT_TRIGGER_ID` と同値。削除不可）。"""

AREA_B_DEFAULT_TRIGGER_NAME = "ready 巡回"
"""既定トリガーの名前（同上 `DEFAULT_TRIGGER_NAME`）。"""

AREA_B_TIMER_TRIGGER_ID = "cluster-timer"
"""クラスタ 05 が timer 発火に使う `interval` トリガーの ID。"""

AREA_B_FILE_TRIGGER_ID = "cluster-file"
"""クラスタ 05 が file 発火に使う `file` トリガーの ID。"""

HISTORY_DIRNAME = "history"
"""履歴 csv を置く領域 B 直下のフォルダ名（`trigger/repository/history.py` と同じ）。"""


@dataclass(frozen=True, slots=True)
class TempAreaB:
    """一時の設定ファイル領域 B（`TRIGGER_ROOT = tmp_path/area_b`）。"""

    root: Path
    """`TRIGGER_ROOT` に渡す値。"""

    definitions: Path
    """`triggers.yaml` の絶対パス（不在のまま渡して雛形生成を見るときは実在しない）。"""

    history_dir: Path
    """履歴 csv を置くフォルダ（読み取り専用にして書込失敗を作る口＝ 05 #10）。"""

    downloads_dir: Path
    """`DOWNLOADS_DIR` に渡す値（**ext4 の一時フォルダ**。実運用の DL には触れない）。"""

    timer_trigger_id: str
    """短い `interval` を持つトリガーの ID（`None` 相当の値は入らない）。"""

    file_trigger_id: str | None
    """`file` トリガーの ID（置かなかったときは `None`）。"""


def make_downloads_dir(tmp_path: Path, *, name: str = "downloads") -> Path:
    """一時の DL フォルダ（**ext4**）を作る。

    実運用の DL フォルダ（`/mnt/c` 配下の Windows 共有）には 1 バイトも触れない（規約 §5）。

    Returns:
        `DOWNLOADS_DIR` に渡す値。
    """
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_history_dir(root: Path) -> Path:
    """領域 B の `history/` を作る（`trigger` は不在なら自分で作るが、権限操作の口が要る）。

    Args:
        root: 領域 B のルート。

    Returns:
        作った `history/` の絶対パス。
    """
    history = root / HISTORY_DIRNAME
    history.mkdir(parents=True, exist_ok=True)
    return history


def _trigger_yaml_entry(
    *,
    trigger_id: str,
    name: str,
    trigger_type: str,
    spec: str,
    enabled: bool = True,
    is_default: bool = False,
) -> str:
    """`triggers.yaml` の 1 件（8 欄）を組む。`spec` は**種別によらず文字列 1 本**（DIFF-11）。"""
    return (
        f"  - trigger_id: {trigger_id}\n"
        f"    name: {name}\n"
        f'    description: ""\n'
        f"    type: {trigger_type}\n"
        f'    spec: "{spec}"\n'
        f"    condition:\n"
        f"      labels:\n"
        f"        - role:executor\n"
        f"      project_key: null\n"
        f"    enabled: {'true' if enabled else 'false'}\n"
        f"    is_default: {'true' if is_default else 'false'}\n"
    )


def make_temp_area_b(
    tmp_path: Path,
    *,
    timer_interval_sec: int = 1,
    with_file_trigger: bool = True,
    watch_subdir: str = ".",
    write_definitions: bool = True,
    definitions_body: str | None = None,
) -> TempAreaB:
    """一時領域 B（`triggers.yaml`・`history/`）と一時 DL フォルダを作る。

    出所は `contract_stores.py` に無く、`packages/features/trigger/tests/conftest.py` の手順の
    **書き直し**である（feature の `tests/` を import しない＝憲章 §1）。

    Args:
        tmp_path: 一時ディレクトリ。
        timer_interval_sec: `interval` トリガーの秒数（`min_interval_sec` を短くした一時
            `config.json` と**対で**使う＝ R-06。実物の 60 秒のままでは validate に落ちる）。
        with_file_trigger: `file` トリガーを 1 件置くか。
        watch_subdir: `file` トリガーの `spec`（`DOWNLOADS_DIR` 相対・既定 `"."`）。
        write_definitions: `triggers.yaml` を書くか（**不在**にして雛形生成を見る 05 #1 は偽）。
        definitions_body: `triggers.yaml` の本文を丸ごと差し替える（YAML 不正の注入用）。

    Returns:
        組み上がった一時領域 B の記述。
    """
    root = tmp_path / "area_b"
    root.mkdir(parents=True, exist_ok=True)
    downloads = make_downloads_dir(tmp_path)
    definitions = root / "triggers.yaml"
    if definitions_body is not None:
        definitions.write_text(definitions_body, encoding="utf-8", newline="\n")
    elif write_definitions:
        entries = [
            _trigger_yaml_entry(
                trigger_id=AREA_B_DEFAULT_TRIGGER_ID,
                name=AREA_B_DEFAULT_TRIGGER_NAME,
                trigger_type="interval",
                spec=str(timer_interval_sec),
                enabled=False,
                is_default=True,
            ),
            _trigger_yaml_entry(
                trigger_id=AREA_B_TIMER_TRIGGER_ID,
                name="クラスタ timer",
                trigger_type="interval",
                spec=str(timer_interval_sec),
            ),
        ]
        if with_file_trigger:
            entries.append(
                _trigger_yaml_entry(
                    trigger_id=AREA_B_FILE_TRIGGER_ID,
                    name="クラスタ file",
                    trigger_type="file",
                    spec=watch_subdir,
                )
            )
        definitions.write_text("triggers:\n" + "".join(entries), encoding="utf-8", newline="\n")
    return TempAreaB(
        root=root,
        definitions=definitions,
        history_dir=make_history_dir(root),
        downloads_dir=downloads,
        timer_trigger_id=AREA_B_TIMER_TRIGGER_ID,
        file_trigger_id=AREA_B_FILE_TRIGGER_ID if with_file_trigger else None,
    )


def short_trigger_config(
    *,
    min_interval_sec: int = 2,
    poll_interval_sec: float = 0.2,
    stable_seconds: int = 1,
    ready_interval_default_sec: int = 300,
    watcher_enabled: bool = True,
    misfire_grace_sec: int = 1,
) -> dict[str, object]:
    """クラスタ 05 が `trigger.config_path` へ向ける一時 `config.json` の中身（R-06）。

    **実時間に効く値だけ**を短くする（`min_interval_sec`・`poll_interval_sec`・
    `stable_seconds`・`misfire_grace_sec`）。実物は 60／5.0／2／30 で、差し替えないと timer 発火の
    観測に 60 秒以上かかる。判定ロジックに効く値は差し替えない（規約 §5）。

    ⚠ `misfire_grace_sec` も一緒に下げる —— 不変条件
    `misfire_grace_sec < min_interval_sec <= ready_interval_default_sec` があり、間隔だけを
    縮めると起動が `TriggerConfigError` で落ちる（2026-09-01 に実測）。

    Returns:
        `TriggerConfig` が読める形の写像。
    """
    return {
        "ready_interval_default_sec": ready_interval_default_sec,
        "min_interval_sec": min_interval_sec,
        "misfire_grace_sec": misfire_grace_sec,
        "poll_interval_sec": poll_interval_sec,
        "stable_seconds": stable_seconds,
        "watcher_enabled": watcher_enabled,
        "timezone": None,
    }


# ------------------------------------------------------------------ 一時ストアのひとまとめ


@dataclass(frozen=True, slots=True)
class ClusterStores:
    """beads・vault・領域 A の 3 点セット（02・03・04 が共有する）。

    クラスタの `conftest.py` ではなく `_support/` に置く —— テストが型注釈のために
    `from conftest import ...`（および相対 import）へ手を伸ばさないようにするためである
    （FR-041a・R-02。`contract/` の脆さを再発させない）。
    """

    beads: TempBeads
    vault: TempVault
    area_a: TempAreaA

    @property
    def project_key(self) -> str:
        """領域 A に登録済みの唯一のプロジェクトキー（盤面のラベルにも使う）。"""
        return self.area_a.project_key

    @property
    def persona(self) -> PersonaKey:
        """領域 A に登録済みの唯一のメンバのペルソナ。"""
        return self.area_a.persona
