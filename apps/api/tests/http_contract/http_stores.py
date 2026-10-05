"""settings が所有する外部ストア（領域 A）の雛形と変種（data-model §4・R-03・FR-041）。

`build()` を呼ぶ**前**に置く。`build()` の `_scaffold` は共通側 6 件を**不在のときだけ**生成
するので、先に置いたものは上書きされない —— 逆に、後ろ 3 件（`llm_profiles.yaml`・
`commands.yaml`・`humans.yaml`）は雛形が**空の骨格**を作るため、先に置かないと
`getLlmProfile` 200・`getCommand` 200 の観測点が作れない。`global/limits.yaml` は雛形の対象外。

`global/agents.yaml`・`global/tasks.yaml`・`global/model.conf` は雛形が同梱既定（9 ペルソナ・
7 段テンプレート）を写すので**ここでは置かない**（`getGlobalAgent` 404 を踏む変種だけが置く）。

⚠ **`_support/` へ置かない**（FR-041）／**`contract/contract_stores.py` を import しない**
（FR-038。`sys.path` に載らず単独実行が落ちる）／**feature の `tests/` を import しない**
（憲章 §1）。層 1 の `settings_testing.write_minimal_area_a` は**手順だけ**見て書き直してある。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

PROJECT_KEY = "pj"
"""既定の雛形が置くプロジェクト。"""

SECOND_PROJECT_KEY = "keep"
"""`deleteProject` の 204／409 用の 2 件目（消しても最後の 1 件にならないため）。"""

PERSONA_KEY = "programmer"
"""既定の雛形が置くペルソナ（同梱既定の 9 件に含まれる）。"""

DEFAULT_PROFILE = "default"
SPARE_PROFILE = "spare"
"""既定でも参照中でもないプロファイル（`deleteLlmProfile` 204 用）。"""

COMMAND_NAME = "run_tests"
HUMAN_ACTOR = "taro"

LLM_PROFILES = f"""\
profiles:
  {DEFAULT_PROFILE}:
    provider_kind: openai_compatible
    base_url: http://127.0.0.1:8080/v1
    model_id: demo-model
    context_window: 32768
    api_key_ref: DEMO_API_KEY_REF
  {SPARE_PROFILE}:
    provider_kind: openai_compatible
    base_url: http://127.0.0.1:8080/v1
    model_id: spare-model
    context_window: 16384
    api_key_ref: DEMO_API_KEY_REF
default: {DEFAULT_PROFILE}
"""
"""鍵の**実値**は持たない（`api_key_ref` は `.env` のキー名）。"""

COMMANDS = f"""\
commands:
  {COMMAND_NAME}:
    argv:
      - ls
"""

EMPTY_COMMANDS = "commands: {}\n"
"""`putPolicy` 200 用の変種（V-25「死に定義の禁止」を外すため定義を空にする）。"""

HUMANS = f"human_actors:\n  - {HUMAN_ACTOR}\n"

LIMITS = f"""\
global:
  max_requests_per_kickoff: 100
  max_cost_per_kickoff: 10.0
overrides:
  {PROJECT_KEY}:
    max_requests_per_kickoff: 50
    max_cost_per_kickoff: 5.0
"""

SPARSE_GLOBAL_AGENTS = f"""\
{PERSONA_KEY}:
  role: |-
    実装担当
  goal: |-
    受け取った課題を実装する
  backstory: |-
    あなたは実装担当です。
"""
"""`getGlobalAgent` 404 用（既定の雛形は 9 ペルソナすべてを埋めるので 200 になってしまう）。"""

PROJECT_RULES = "# プロジェクト規約\n\n1 行だけの規約。\n"

HOOKS = "hooks: []\n"

PROJECT_TASKS = """\
plain:
  description: |
    担当 issue: {issue_id}
    タイトル: {title}

    issue の説明:
    {description}

    人間からの指示:
    {human_guidance}

    前回の差し戻し理由:
    {feedback}

    受け入れ条件:
    {acceptance}
  expected_output: |
    成果物の要約
"""
"""PJ 側の段テンプレート 1 キー（`getProjectTasks` 200・`deleteProjectTasks` 204 用）。"""


def _write(root: Path, rel: str, text: str) -> Path:
    """領域 A ルート相対のパスへ本文を書く（親フォルダも作る）。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def _write_project(root: Path, key: str, *, name: str = "デモ") -> None:
    """`projects/<key>/project.yaml`（ペルソナは `PERSONA_KEY` 1 件）。"""
    body = (
        f"name: {name}\n"
        "is_reserved: false\n"
        "folders:\n"
        f"  pj_input_dir: {key}\n"
        "  role_dirs:\n"
        f"    {PERSONA_KEY}: {key}/roles/{PERSONA_KEY}\n"
        f"  outcome_dir: {key}/outcome\n"
    )
    _write(root, f"projects/{key}/project.yaml", body)


def _write_members(root: Path, key: str) -> None:
    """`projects/<key>/agents.yaml`（PJ 側は空 object 可＝ global 既定のまま参加）。"""
    _write(root, f"projects/{key}/agents.yaml", f"{PERSONA_KEY}: {{}}\n")


def _write_policy(root: Path, key: str, *, with_command: bool = True) -> None:
    """`projects/<key>/policy.csv`（**引用符を書かない**）。

    `cmd:` 行は V-24（policy の `cmd:` ⊆ 定義）と V-25（死に定義の禁止）の両方を満たすために
    既定で置く。`with_command=False` は定義を空にした変種と対で使う。
    """
    rows = [f"p, member:{key}.{PERSONA_KEY}, tool:read_issue, execute"]
    if with_command:
        rows.append(f"p, member:{key}.{PERSONA_KEY}, cmd:{COMMAND_NAME}, execute")
    _write(root, f"projects/{key}/policy.csv", "".join(f"{row}\n" for row in rows))


def make_temp_area_a(root: Path) -> Path:
    """検査を通る領域 A を `build()` の**前**に組む（data-model §4 の既定）。

    Args:
        root: 領域 A のルート（`SETTINGS_ROOT` が指す先）。

    Returns:
        渡された `root`。
    """
    _write(root, "global/llm_profiles.yaml", LLM_PROFILES)
    _write(root, "global/commands.yaml", COMMANDS)
    _write(root, "global/humans.yaml", HUMANS)
    _write(root, "global/limits.yaml", LIMITS)
    _write_project(root, PROJECT_KEY)
    _write_members(root, PROJECT_KEY)
    _write_policy(root, PROJECT_KEY)
    _write(root, f"projects/{PROJECT_KEY}/rules.md", PROJECT_RULES)
    _write(root, f"projects/{PROJECT_KEY}/hooks.yaml", HOOKS)
    _write(root, f"projects/{PROJECT_KEY}/tasks.yaml", PROJECT_TASKS)
    return root


def make_area_a_without_commands(root: Path) -> Path:
    """コマンド定義が 1 件も無い変種（`putPolicy` 200 用）。

    V-25「死に定義の禁止」に触れないよう、定義を空にしたうえで policy から `cmd:` 行を落とす。

    Args:
        root: 領域 A のルート。

    Returns:
        渡された `root`。
    """
    make_temp_area_a(root)
    _write(root, "global/commands.yaml", EMPTY_COMMANDS)
    _write_policy(root, PROJECT_KEY, with_command=False)
    return root


def make_area_a_sparse_global_agents(root: Path) -> Path:
    """`global/agents.yaml` に 1 ペルソナだけを置く変種（`getGlobalAgent` 404 用）。

    雛形は不在のときだけ同梱既定（9 ペルソナ）を写すので、**先に置けば**残り 8 件は既定を
    持たない状態になる。

    Args:
        root: 領域 A のルート。

    Returns:
        渡された `root`。
    """
    make_temp_area_a(root)
    _write(root, "global/agents.yaml", SPARSE_GLOBAL_AGENTS)
    return root


def make_area_a_two_projects(root: Path) -> Path:
    """プロジェクトを 2 件置く変種（`deleteProject` の 204／409 用）。

    Args:
        root: 領域 A のルート。

    Returns:
        渡された `root`。
    """
    make_temp_area_a(root)
    _write_project(root, SECOND_PROJECT_KEY, name="残す")
    _write_members(root, SECOND_PROJECT_KEY)
    _write_policy(root, SECOND_PROJECT_KEY)
    return root


def area_bytes(root: Path) -> dict[str, bytes]:
    """領域 A 配下の**全ファイルのバイト列**（mtime は使わない）。

    422・409 で「1 バイトも変わらない」ことを主張するのに使う。

    Args:
        root: 領域 A のルート。

    Returns:
        ルート相対パス → 中身。
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
