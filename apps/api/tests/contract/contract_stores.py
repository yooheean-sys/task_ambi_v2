"""一時外部ストアの組み立て（実 beads・実 vault のフォルダ木・設定ファイル領域 A）。

契約テスト層が触ってよい外部ストアは**一時ディレクトリの実物だけ**である（FR-013・FR-014）。
本モジュールはその実物を `tmp_path` の下に作る手段を集めたもので、**実運用のストアには
一切触れない**。組み立ての形は先行 feature のテスト土台を**読んで写した**（import はしない
——別 workspace パッケージのテスト補助は pythonpath 外にある）:

- `TempBeads`: `packages/features/board/tests/integration/conftest.py:45-61`
- `TempVault`: `packages/features/vault/tests/conftest.py:41-57`
- `TempAreaA`: `packages/features/settings/tests/settings_testing.py`（`PROFILE_BODY` 等）
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404  外部ツールは公約インターフェース（CLI）経由でのみ触る
from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.interfaces.settings import ProjectFolders
from common.kernel.config import find_repo_root
from common.kernel.enums import PersonaKey

if TYPE_CHECKING:
    from pathlib import Path

# ------------------------------------------------------------------ beads（board）

BEADS_INIT_TIMEOUT_SEC = 120
"""`bd init` は embedded Dolt の初期化と git commit まで行うので長めに取る。"""

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


def assert_outside_repo(tmp_path: Path) -> None:
    """一時ディレクトリがリポジトリの外にあることを確かめる（D19-d・FR-061）。

    `bd init` は `.beads/` だけでなく git リポジトリを作り commit まで行い、`AGENTS.md`・
    `CLAUDE.md`・`.claude/` も生成する。`TMPDIR` がリポジトリ内を指す環境で事故らないよう、
    **実 `bd init` の前に必ず通す**。
    """
    assert find_repo_root() not in tmp_path.parents, (
        f"一時ディレクトリがリポジトリ内にある: {tmp_path}"
        " / bd init はリポジトリ外でのみ実行する（TMPDIR を確認する）"
    )


def init_beads(tmp_path: Path, bd_bin: str, *, project_key: str = "contract") -> TempBeads:
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
    return TempBeads(workspace=workspace, beads_dir=workspace / ".beads", project_key=project_key)


def remove_beads(beads: TempBeads) -> None:
    """`.git` と embedded Dolt を残さず消す（pytest は直近 3 回分の tmp を保持する）。"""
    shutil.rmtree(beads.workspace, ignore_errors=True)


def make_light_beads(tmp_path: Path, *, name: str = "light") -> Path:
    """`bd init` を**打たない**軽量 beads（R-18・data-model §3-1）。

    board の起動時検査 (b)（実在）・(c)（直下に `config.yaml`）を通せば足りる。`BD_BIN` を
    「常に失敗するシム」に向ける `BoardUnavailable` の 22 本は、盤面の中身を一切使わないので
    これで十分であり、実 `bd init` の回数を大きく減らせる。

    Returns:
        `BEADS_DIR` に渡す値。
    """
    beads_dir = tmp_path / name / ".beads"
    beads_dir.mkdir(parents=True)
    (beads_dir / BEADS_CONFIG_FILENAME).write_text("", encoding="utf-8")
    return beads_dir


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


def make_temp_vault(tmp_path: Path) -> TempVault:
    """PJ 入力・役割・outcome の 3 領域と、ルート外の 1 ファイルを持つ木を作る。

    ルート外を表すのは `tmp_path/outside/`（vault ルートは `tmp_path/vault`＝ 1 階層下）。
    """
    root = tmp_path / "vault"
    (root / "pj").mkdir(parents=True)
    (root / "pj/roles/programmer").mkdir(parents=True)
    (root / "pj/roles/writer").mkdir(parents=True)
    (root / "pj/outcome").mkdir(parents=True)
    (root / "pj" / VAULT_INPUT_NAME).write_text(VAULT_INPUT_BODY, encoding="utf-8", newline="\n")
    (root / "pj/roles/programmer" / VAULT_ROLE_NAME).write_text(
        VAULT_ROLE_BODY, encoding="utf-8", newline="\n"
    )
    (root / "pj/outcome" / VAULT_OUTCOME_NAME).write_text(
        VAULT_OUTCOME_BODY, encoding="utf-8", newline="\n"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / OUTSIDE_NAME).write_text(OUTSIDE_BODY, encoding="utf-8", newline="\n")
    folders = ProjectFolders(
        pj_input_dir="pj",
        role_dirs={
            PersonaKey.programmer: "pj/roles/programmer",
            PersonaKey.writer: "pj/roles/writer",
        },
        outcome_dir="pj/outcome",
    )
    outside_folders = ProjectFolders(
        pj_input_dir="../outside",
        role_dirs={PersonaKey.programmer: "../outside"},
        outcome_dir="../outside",
    )
    return TempVault(root=root, outside=outside, folders=folders, outside_folders=outside_folders)


# ------------------------------------------------------------------ 設定ファイル領域 A

AREA_A_PROJECT_KEY = "contractpj"
"""一時領域 A が持つ唯一のプロジェクト。"""

AREA_A_PERSONA = PersonaKey.programmer
"""一時領域 A が持つ唯一のメンバのペルソナ。"""

AREA_A_COMMAND = "demo"
"""`commands.yaml` に置く唯一のコマンド（`policy.csv` から参照させる＝ V-25 死に定義の禁止）。"""

AREA_A_COMMAND_ARGV = ("/bin/echo", "hello")
"""`commands.yaml` の `argv`（**2 要素**）。

`resolve_command` の出力欄は `argv: list[str]（shell=False・引数は AI に渡させない）` であり、
要点は**分かち書きされた並び**である。1 本の文字列へ連結して返す実装と区別できるよう、
宣言側も期待値側もこの 1 つの定数から取る（テストへ直書きしない）。
"""

AREA_A_HUMAN_ACTOR = "human_owner"
"""`humans.yaml` に置く人間の行為者（`is_human` が真を返す唯一の値）。"""

AREA_A_HOOK_NAME = "contract-hook"
"""`hooks.yaml` に置く唯一の Hook 宣言の名前（V-18 の形式検査を通る）。"""

AREA_A_HOOK_POINTS = "PRE_TOOL_CALL"
"""同上（`points`。許可値は `PRE_TOOL_CALL`／`POST_TOOL_CALL` の 2 つ）。"""

AREA_A_HOOK_AGENTS = (str(AREA_A_PERSONA),)
"""同上（`agents`）。**空にしない** —— 空だと「読み取った」と「既定に落ちた」を区別できない。"""

AREA_A_LIMITS_DEFAULT_REQUESTS = 60
"""settings の `config.json` が持つ出荷時既定（CFG-39 の ③ 段）。**上書きと区別するため**に
だけ持つ値で、テストはこれと等しくならないことを主張する。"""

AREA_A_LIMITS_DEFAULT_COST = 1.0
"""同上（`max_cost_per_kickoff` の ③ 段）。"""

AREA_A_LIMITS_GLOBAL_REQUESTS = 5
"""`global/limits.yaml` の `global:`（CFG-39 の ④ 段）。V-27 を満たす正の値。"""

AREA_A_LIMITS_GLOBAL_COST = 0.5
"""同上（④ 段の `max_cost_per_kickoff`）。"""

AREA_A_LIMITS_PJ_REQUESTS = 9
"""`global/limits.yaml` の `overrides[<pj>]`（CFG-39 の ⑤ 段）。③ とも ④ とも異なる値にする。"""

AREA_A_LIMITS_PJ_COST = 1.25
"""同上（⑤ 段の `max_cost_per_kickoff`）。"""

AREA_A_TASK_OVERRIDE_KEY = "child"
"""PJ の `tasks.yaml` が**丸ごと**上書きする唯一の段キー（残る 6 キーは global が効く）。"""

AREA_A_TASK_PJ_DESCRIPTION = "PJ 上書きの作業指示: {acceptance}"
"""⑤ 段のテンプレート本文（`child` の段別必須変数 `{acceptance}` を含む）。"""

AREA_A_TASK_PJ_EXPECTED_OUTPUT = "PJ 上書きの期待成果"
"""同上（`expected_output`）。"""

AREA_A_GLOBAL_ROLE = "契約テストの共通ロール"
"""`global/agents.yaml` 側の `role`（PJ が上書き**しない**フィールド）。"""

AREA_A_GLOBAL_GOAL = "契約テストの共通ゴール"
"""`global/agents.yaml` 側の `goal`（PJ が上書き**する**フィールドの元の値）。"""

AREA_A_GLOBAL_BACKSTORY = "契約テストの共通バックストーリ"
"""`global/agents.yaml` 側の `backstory`（PJ が上書き**しない**フィールド）。"""

AREA_A_GLOBAL_TOOLS = ("read_issue", "record_progress")
"""`global/agents.yaml` 側の `tools`（PJ 側で**丸ごと置換**される＝和集合にしない）。"""

AREA_A_GLOBAL_SKILL = "contract-skill"
"""`global/agents.yaml` 側の `skills` の唯一の要素。"""

AREA_A_GLOBAL_MCP_NAME = "contract-mcp"
"""`global/agents.yaml` 側の `mcps` の唯一の宣言名。"""

AREA_A_GLOBAL_MAX_ITER = 3
"""`global/agents.yaml` 側の `max_iter`（PJ が上書き**しない**フィールド）。"""

AREA_A_PJ_GOAL = "契約テストの PJ 固有ゴール"
"""PJ の `agents.yaml` が上書きする `goal`（④→⑤ merge の観測点）。"""

AREA_A_PJ_TOOLS = ("read_issue",)
"""PJ の `agents.yaml` が上書きする `tools`（global の 2 件を 1 件へ**絞る**）。"""

AGENTS_GLOBAL_BODY = f"""\
{AREA_A_PERSONA}:
  role: |-
    {AREA_A_GLOBAL_ROLE}
  goal: |-
    {AREA_A_GLOBAL_GOAL}
  backstory: |-
    {AREA_A_GLOBAL_BACKSTORY}
  tools:
{chr(10).join(f"    - {name}" for name in AREA_A_GLOBAL_TOOLS)}
  skills:
    - {AREA_A_GLOBAL_SKILL}
  mcps:
    - name: {AREA_A_GLOBAL_MCP_NAME}
      transport: stdio
      command: /bin/echo
      args:
        - hello
  max_iter: {AREA_A_GLOBAL_MAX_ITER}
"""
"""④ 段の `global/agents.yaml`（V-04 の必須 3 種＋許可 10 のうち 7 種を埋める）。

**出荷時既定を写させない**（`_scaffold` は不在のときだけ書くので、先に置けば上書きされない）。
狙いは merge の**両側**をテストから既知にすることで、こうしないと「上書きしなかったものが
global の値である」ことを、出荷時既定の本文を読まずに主張できない。
"""

AGENTS_PJ_BODY = f"""\
{AREA_A_PERSONA}:
  goal: |-
    {AREA_A_PJ_GOAL}
  tools:
{chr(10).join(f"    - {name}" for name in AREA_A_PJ_TOOLS)}
"""
"""⑤ 段の `projects/<pj>/agents.yaml`（**一部フィールドだけ**を上書きする）。

`goal` は値の置換、`tools` はリストの**丸ごと置換**（和集合にしない＝ PJ で装備を絞れる）を
観測させる。`role`・`backstory`・`skills`・`mcps`・`max_iter` は書かないので global が残る。
"""

LIMITS_BODY = f"""\
global:
  max_requests_per_kickoff: {AREA_A_LIMITS_GLOBAL_REQUESTS}
  max_cost_per_kickoff: {AREA_A_LIMITS_GLOBAL_COST}
overrides:
  {AREA_A_PROJECT_KEY}:
    max_requests_per_kickoff: {AREA_A_LIMITS_PJ_REQUESTS}
    max_cost_per_kickoff: {AREA_A_LIMITS_PJ_COST}
"""
"""`global/limits.yaml`（CFG-39 の ④ と ⑤ を**どちらも**置く）。

`_scaffold` はこのファイルを生成しない（③ 段を永久に殺さないため）ので、置かなければ解決は
③ 段しか通らない。V-27（上限はいずれも 0 より大きい）を満たす値にしてある。
"""

TASKS_PJ_BODY = f"""\
{AREA_A_TASK_OVERRIDE_KEY}:
  description: |-
    {AREA_A_TASK_PJ_DESCRIPTION}
  expected_output: |-
    {AREA_A_TASK_PJ_EXPECTED_OUTPUT}
"""
"""`projects/<pj>/tasks.yaml`（1 キーだけの部分上書き。PJ 側は 7 キー必須ではない）。

`get_tasks` の上書きは**キー単位で丸ごと**であり、フィールド単位の merge はしない（D16-a）。
"""

PROFILE_BODY = """\
profiles:
  default:
    provider_kind: openai_compatible
    base_url: http://127.0.0.1:8080/v1
    model_id: demo-model
    context_window: 32768
    api_key_ref: DEMO_API_KEY_REF
default: default
"""
"""検査を通る最小の `llm_profiles.yaml`（`api_key` の**実値**は持たない）。"""

COMMANDS_BODY = f"""\
commands:
  {AREA_A_COMMAND}:
    argv:
{chr(10).join(f"      - {word}" for word in AREA_A_COMMAND_ARGV)}
"""
"""トップレベルは `commands:`（読み手が `commands` の部分木だけを見る）。`argv` は空でない
文字列の一覧（V-26）。**並びは `AREA_A_COMMAND_ARGV` が正本**で、テストの期待値も同じ定数から
取る（宣言と期待値が別々に書かれていると、分かち書きの一致を主張しても意味が無い）。"""

HUMANS_BODY = f"""\
human_actors:
  - {AREA_A_HUMAN_ACTOR}
"""

RULES_BODY = "PJ 共通ルールの本文\n"

HOOKS_BODY = f"""\
hooks:
  - name: {AREA_A_HOOK_NAME}
    points: {AREA_A_HOOK_POINTS}
    agents:
{chr(10).join(f"      - {name}" for name in AREA_A_HOOK_AGENTS)}
"""
"""`projects/<pj>/hooks.yaml`（**非空**の宣言を 1 件持つ）。

`get_hooks` は宣言が無いプロジェクトでは防御的に空の `HooksConfig` へ落ちる（`snap.hooks.get`
の既定）ので、宣言が空だと「読み取った」と「既定に落ちた」を区別できない —— `hooks_raw` を
丸ごと捨てる実装でも緑になる。**V-18 の形式検査**（`name` は文字列・`points` は許可値・
`agents` は文字列の一覧）を通る形にしてある。
"""


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


def _write(root: Path, rel: str, text: str) -> None:
    """領域 A ルート相対のパスへ本文を書く（親フォルダも作る）。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def make_temp_area_a(
    tmp_path: Path,
    *,
    project_key: str = AREA_A_PROJECT_KEY,
    persona: PersonaKey = AREA_A_PERSONA,
) -> TempAreaA:
    """`build()` の整合検査 3 種を通る最小の領域 A を組む（data-model §3-3・K-1）。

    `build()` の `_scaffold` が**不在のときだけ**生成するのは 6 件
    （`global/model.conf`・`global/agents.yaml`・`global/tasks.yaml`・`global/llm_profiles.yaml`・
    `global/commands.yaml`・`global/humans.yaml`）である。このうち後ろ 3 件は**空の骨格**が
    入るため、IF-27（`llm_profile` 解決）・IF-30（`resolve_command`）・IF-28（`is_human`）の
    正常系を作るには**先に自前の内容を置いて**上書きを避ける必要がある
    （`_write_if_absent` は既存を上書きしない）。

    Args:
        tmp_path: 一時ディレクトリ。
        project_key: 登録するプロジェクトキー。
        persona: 登録するメンバのペルソナ。

    Returns:
        組み上がった一時領域 A の記述。
    """
    root = tmp_path / "area_a"
    root.mkdir(parents=True)
    # `_scaffold` が空の骨格で埋めてしまう 3 件を先に置く。
    _write(root, "global/llm_profiles.yaml", PROFILE_BODY)
    _write(root, "global/commands.yaml", COMMANDS_BODY)
    _write(root, "global/humans.yaml", HUMANS_BODY)
    # `_scaffold` が出荷時既定で埋めてしまう `agents.yaml` も先に置く（merge の ④ 段を
    # テストから既知にするため）。`limits.yaml` は `_scaffold` の対象外なので、置かないと
    # 解決が ③ 段しか通らない。
    _write(root, "global/agents.yaml", AGENTS_GLOBAL_BODY)
    _write(root, "global/limits.yaml", LIMITS_BODY)
    # PJ 側は `_scaffold` の対象外（登録は F11＝ CRUD の領分）なのですべて自前で置く。
    _write(
        root,
        f"projects/{project_key}/project.yaml",
        "name: 契約テスト\n"
        "is_reserved: false\n"
        "folders:\n"
        "  pj_input_dir: pj\n"
        "  role_dirs:\n"
        f"    {persona}: pj/roles/{persona}\n"
        "  outcome_dir: pj/outcome\n",
    )
    _write(root, f"projects/{project_key}/agents.yaml", AGENTS_PJ_BODY)
    _write(root, f"projects/{project_key}/tasks.yaml", TASKS_PJ_BODY)
    _write(
        root,
        f"projects/{project_key}/policy.csv",
        f"p, member:{project_key}.{persona}, tool:read_issue, execute\n"
        f"p, member:{project_key}.{persona}, cmd:{AREA_A_COMMAND}, execute\n",
    )
    _write(root, f"projects/{project_key}/rules.md", RULES_BODY)
    _write(root, f"projects/{project_key}/hooks.yaml", HOOKS_BODY)
    return TempAreaA(
        root=root,
        project_key=project_key,
        persona=persona,
        command_name=AREA_A_COMMAND,
        human_actor=AREA_A_HUMAN_ACTOR,
    )
