"""一時領域 A の組み立て（共有部品。R-25）。

`conftest.py` と**別名**にする — `unit/` と `integration/` の双方に `conftest` があると
`from conftest import …` が曖昧になるためである（board が実測済み・vault も同じ理由で
`vault_testing.py` を置いている）。

後半で `SettingsDeps` が **2 依存**（IF-09・IF-32）を持つようになったため、依存 Protocol の
**スタブ 2 種**を置く（N-06）。**他 feature を import しない**（憲章 §1。
`apps/api/tests/foundation/test_feature_test_imports.py` が全 feature の tests を走査する）ので、
契約型だけを `common.interfaces` から取り、実装は自前で書く。**`bd`・実 vault・LLM には
接続しない。**
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import httpx
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.interfaces.board import BoardUnavailable
from common.interfaces.vault import FolderCheck, VaultPathViolation
from settings import SettingsConfig, SettingsDeps, defaults_path
from settings.repository import AreaAReader, AreaAScaffolder, AreaAWriter
from settings.router import create_settings_router
from settings.service import SettingsService
from settings.service.crud import SettingsCrudService
from settings.service.loader import build_snapshot, read_area
from settings.service.validate import validate_commands, validate_forward, validate_reverse

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from common.interfaces.board import BoardReader, ClaimFilter, Comment, Issue
    from common.interfaces.settings import ProjectFolders
    from common.interfaces.vault import FileRef, VaultReader
    from common.kernel.enums import PersonaKey
    from settings.models import AreaAData, SettingsSnapshot, ValidationProblem

DEFAULT_PROFILE = "default"
"""最小構成が置く LLM プロファイルの名前。"""

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
"""検査を通る最小の `llm_profiles.yaml`（`api_key` の**実値**は持たない＝ FR-049）。"""

SYSTEM_ACTOR = "system"
"""最小構成が使うシステムの行為者名（`config.json` の CFG-40 と同じ値）。"""


def scaffold(root: Path) -> tuple[str, ...]:
    """共通側の雛形 6 件を生成する（実装と同じ経路を通す）。"""
    return AreaAScaffolder(root, defaults_path()).ensure()


def write(root: Path, rel: str, text: str) -> Path:
    """領域 A ルート相対のパスへ本文を書く（親フォルダも作る）。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def break_(root: Path, rel: str, text: str) -> Path:
    """壊れた内容を書き込む（表駆動の 1 行から呼ぶ）。"""
    return write(root, rel, text)


def write_llm_profiles(root: Path, body: str = PROFILE_BODY) -> None:
    """`global/llm_profiles.yaml` を差し替える。"""
    write(root, "global/llm_profiles.yaml", body)


def write_project(
    root: Path, key: str, personas: Iterable[str] = ("programmer",), *, name: str = "デモ"
) -> None:
    """`projects/<key>/project.yaml` を書く（`role_dirs` は渡したペルソナ分）。"""
    lines = [f"name: {name}\n", "is_reserved: false\n", "folders:\n"]
    lines.append(f"  pj_input_dir: {key}\n")
    lines.append("  role_dirs:\n")
    lines.extend(f"    {persona}: {key}/roles/{persona}\n" for persona in personas)
    lines.append(f"  outcome_dir: {key}/outcome\n")
    write(root, f"projects/{key}/project.yaml", "".join(lines))


def write_agents(root: Path, key: str, mapping: Mapping[str, str]) -> None:
    """`projects/<key>/agents.yaml` を書く（値は YAML の断片文字列）。"""
    body = "".join(f"{persona}:{value}\n" for persona, value in mapping.items())
    write(root, f"projects/{key}/agents.yaml", body or "{}\n")


def write_global_agents(root: Path, body: str) -> None:
    """`global/agents.yaml` を差し替える。"""
    write(root, "global/agents.yaml", body)


def write_tasks(root: Path, key: str, body: str) -> None:
    """`projects/<key>/tasks.yaml` を書く。"""
    write(root, f"projects/{key}/tasks.yaml", body)


def write_policy(root: Path, key: str, rows: Sequence[str]) -> None:
    """`projects/<key>/policy.csv` を書く（**引用符を書かない**＝ M-09）。"""
    write(root, f"projects/{key}/policy.csv", "".join(f"{row}\n" for row in rows))


def write_commands(root: Path, body: str) -> None:
    """`global/commands.yaml` を差し替える。"""
    write(root, "global/commands.yaml", body)


def write_humans(root: Path, actors: Sequence[str]) -> None:
    """`global/humans.yaml` を差し替える（空の宣言は `human_actors: []`）。"""
    body = "human_actors:\n" + "".join(f"  - {actor}\n" for actor in actors)
    write(root, "global/humans.yaml", body if actors else "human_actors: []\n")


class Raw(str):
    """YAML へ**そのまま**出す値（素の flow mapping 等、壊れた書き方を仕込むため）。"""

    __slots__ = ()


def _emit(value: object, indent: str) -> str:
    """テスト用の最小 YAML 出力（本文は必ずブロックスカラー＝ Y-6）。"""
    if isinstance(value, Raw):
        return f" {value}\n"
    if isinstance(value, str):
        body = "\n".join((indent + line) if line.strip() else "" for line in value.split("\n"))
        return f" |-\n{body}\n" if not value.endswith("\n") else f" |\n{body}"
    if isinstance(value, bool):
        return f" {'true' if value else 'false'}\n"
    if value is None:
        return " null\n"
    if isinstance(value, (int, float)):
        return f" {value}\n"
    if isinstance(value, list):
        items = cast("list[object]", value)
        if not items:
            return " []\n"
        return "\n" + "".join(f"{indent}-{_emit(item, indent + '  ')}" for item in items)
    if isinstance(value, dict):
        fields = cast("dict[str, object]", value)
        if not fields:
            return " {}\n"
        return "\n" + "".join(
            f"{indent}{key}:{_emit(item, indent + '  ')}" for key, item in fields.items()
        )
    raise TypeError(type(value).__name__)


def dump_mapping(mapping: Mapping[str, object]) -> str:
    """トップレベル mapping を YAML テキストにする。"""
    return "".join(f"{key}:{_emit(value, '  ')}" for key, value in mapping.items())


def _load_nested(name: str) -> dict[str, dict[str, object]]:
    """同梱既定を「キー → フィールド」の可変な dict として読む（テストが差分を仕込む土台）。"""
    loaded = cast(
        "dict[object, object]",
        yaml.safe_load((defaults_path() / name).read_text(encoding="utf-8")),
    )
    return {
        str(key): {str(field): item for field, item in cast("dict[object, object]", entry).items()}
        for key, entry in loaded.items()
    }


def default_agents() -> dict[str, dict[str, object]]:
    """同梱既定のペルソナ 9 件。"""
    return _load_nested("agents.yaml")


def default_tasks() -> dict[str, dict[str, object]]:
    """同梱既定の段テンプレート 7 件。"""
    return _load_nested("tasks.yaml")


def write_minimal_area_a(
    root: Path, *, projects: Sequence[str] = ("pj",), personas: Sequence[str] = ("programmer",)
) -> None:
    """検査を通る最小の領域 A（共通側 7 ＋ PJ ごと 3）を書く。"""
    scaffold(root)
    write_llm_profiles(root)
    for key in projects:
        write_project(root, key, personas)
        write_agents(root, key, dict.fromkeys(personas, " {}"))
        write_policy(
            root,
            key,
            [f"p, member:{key}.{persona}, tool:read_issue, execute" for persona in personas],
        )


def config_for_tests(**overrides: object) -> SettingsConfig:
    """テスト用のモジュール設定（`config.json` を土台に差し替える）。"""
    return SettingsConfig(**overrides)


def collect_problems(root: Path, config: SettingsConfig | None = None) -> list[ValidationProblem]:
    """整合検査 3 種を通しで走らせ、違反を集める（build を通さない）。"""
    settings = config if config is not None else config_for_tests()
    data = read_area(AreaAReader(root))
    problems = validate_forward(
        data,
        context_reserve_tokens=settings.context_reserve_tokens,
        system_actor=settings.system_actor,
    )
    problems.extend(validate_reverse(data))
    problems.extend(validate_commands(data))
    return problems


def make_snapshot(root: Path, config: SettingsConfig | None = None) -> SettingsSnapshot:
    """`build()` を通さずにスナップショットを組む（単体用）。"""
    settings = config if config is not None else config_for_tests()
    reader = AreaAReader(root)
    data = read_area(reader)
    return build_snapshot(
        data,
        reader,
        context_reserve_tokens=settings.context_reserve_tokens,
        default_requests=settings.limits_defaults.max_requests_per_kickoff,
        default_cost=settings.limits_defaults.max_cost_per_kickoff,
        system_actor=settings.system_actor,
    )


def make_service(root: Path, config: SettingsConfig | None = None) -> SettingsService:
    """`build()` を通さずに `SettingsService` を組む（単体用）。"""
    return SettingsService(make_snapshot(root, config))


def mtimes(root: Path) -> dict[str, int]:
    """領域 A 配下の更新時刻の写し（SC-016 の比較用）。"""
    return {
        str(path.relative_to(root)): path.stat().st_mtime_ns
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def kinds(problems: Sequence[ValidationProblem]) -> set[str]:
    """違反の種別の集合（表駆動の照合用）。"""
    return {item.kind for item in problems}


# --------------------------------------------------------------------------------------
# 依存 Protocol のスタブ（N-06。**settings が初めて依存のスタブを持つ**）
# --------------------------------------------------------------------------------------


class StubVaultReader:
    """IF-32 の偽物。settings が呼ぶのは `check_project_folders` の 1 つだけ（FR-007）。

    **残る 4 メソッドは `NotImplementedError`** にしてある。settings が保管庫の他のメソッドを
    呼んでいないことが、これで**機械的に観測できる**（呼べばテストが落ちる）。
    """

    def __init__(self, missing: Sequence[str] = (), *, violate: bool = False) -> None:
        """不在にするパスと、保管庫の外を指す宣言かどうかを受け取る。"""
        self.missing = list(missing)
        self.violate = violate
        self.calls: list[ProjectFolders] = []

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck:
        """宣言された 3 種のフォルダの実在を返す（`violate` なら `VaultPathViolation`）。"""
        self.calls.append(folders)
        if self.violate:
            raise VaultPathViolation(
                "宣言が保管庫の外を指している",
                "outside_root",
                folders.pj_input_dir,
            )
        return FolderCheck(missing=list(self.missing), ok=not self.missing)

    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]:
        """settings は呼ばない。"""
        raise NotImplementedError

    def read_input(self, folders: ProjectFolders, ref: FileRef) -> str:
        """settings は呼ばない。"""
        raise NotImplementedError

    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str:
        """settings は呼ばない。"""
        raise NotImplementedError

    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool:
        """settings は呼ばない。"""
        raise NotImplementedError


class StubBoardReader:
    """IF-09 の偽物。settings が呼ぶのは `has_open_issues` の 1 つだけ（FR-008）。

    **残る 5 メソッドは `NotImplementedError`**（`StubVaultReader` と同じ理由）。
    """

    def __init__(self, *, open_issues: bool = False, unavailable: bool = False) -> None:
        """未完了課題の有無と、盤面に届かない状態かを受け取る。"""
        self.open_issues = open_issues
        self.unavailable = unavailable
        self.calls: list[str] = []

    def has_open_issues(self, project_key: str) -> bool:
        """未完了課題の有無（`unavailable` なら `BoardUnavailable`）。"""
        self.calls.append(project_key)
        if self.unavailable:
            raise BoardUnavailable("bd に届かない", ["bd", "list"], 1, "", 1)
        return self.open_issues

    def show(self, issue_id: str) -> Issue:
        """settings は呼ばない。"""
        raise NotImplementedError

    def children_of(self, parent_id: str) -> list[Issue]:
        """settings は呼ばない。"""
        raise NotImplementedError

    def open_question_of(self, issue_id: str) -> Issue | None:
        """settings は呼ばない。"""
        raise NotImplementedError

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """settings は呼ばない。"""
        raise NotImplementedError

    def comments(self, issue_id: str) -> list[Comment]:
        """settings は呼ばない。"""
        raise NotImplementedError


def stub_deps(
    *, board: StubBoardReader | None = None, vault: StubVaultReader | None = None
) -> SettingsDeps:
    """`build()` に渡す 2 依存をスタブで組む（**Protocol 型を明示注釈する**＝憲章 §3）。"""
    board_reader: BoardReader = board if board is not None else StubBoardReader()
    vault_reader: VaultReader = vault if vault is not None else StubVaultReader()
    return SettingsDeps(board_reader=board_reader, vault_reader=vault_reader)


# --------------------------------------------------------------------------------------
# 観測ヘルパ（SC-001 のバイト列比較・一時ファイルの残骸）
# --------------------------------------------------------------------------------------

TEMP_PREFIX = "tmp"
"""`tempfile.mkstemp` の既定の接頭辞（一時ファイルの残骸を数えるため）。"""


def area_bytes(root: Path) -> dict[str, bytes]:
    """領域 A の**全ファイルのバイト列**（SC-001 の「完全一致」を中身で見る）。

    `mtime` は書かなくても環境によって動くため、比較には使わない（quickstart S2 の要点）。
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def temp_files(root: Path) -> list[str]:
    """領域 A に残っている一時ファイルの一覧（**0 件**であることを確かめる）。"""
    return [
        str(path.relative_to(root))
        for path in sorted(root.rglob(f"{TEMP_PREFIX}*"))
        if path.is_file()
    ]


def make_crud(
    root: Path,
    config: SettingsConfig | None = None,
    *,
    board: StubBoardReader | None = None,
    vault: StubVaultReader | None = None,
) -> SettingsCrudService:
    """`build()` を通さずに `SettingsCrudService` を組む（単体用。`make_service` と同型）。

    **止め木は `conftest.py` の autouse fixture が担う**ので、ここでは `.env` を読まない。
    """
    settings = config if config is not None else config_for_tests()
    reader = AreaAReader(root)
    data = read_area(reader)
    snapshot = build_snapshot(
        data,
        reader,
        context_reserve_tokens=settings.context_reserve_tokens,
        default_requests=settings.limits_defaults.max_requests_per_kickoff,
        default_cost=settings.limits_defaults.max_cost_per_kickoff,
        system_actor=settings.system_actor,
    )
    board_reader: BoardReader = board if board is not None else StubBoardReader()
    vault_reader: VaultReader = vault if vault is not None else StubVaultReader()
    return SettingsCrudService(
        service=SettingsService(snapshot),
        snapshot=snapshot,
        data=data,
        reader=reader,
        writer=AreaAWriter(root),
        board_reader=board_reader,
        vault_reader=vault_reader,
        config=settings,
    )


def make_data(root: Path) -> AreaAData:
    """領域 A を読み切った**生の構造**（候補の土台。`dataclasses.replace` で変更案を重ねる）。"""
    return read_area(AreaAReader(root))


class HttpClient(Protocol):
    """router のテストが使う `TestClient` の面（orchestrator の先例に倣う）。

    starlette の `testclient` は型検査時に `httpx2` を import する作りで、`httpx2` を入れて
    いない本リポジトリでは応答の型が解決できない。実行時に返るのは `httpx.Response`
    （`httpx` はルートの dev グループにある）なので、**使う面だけをここで型として固定する**。
    """

    def get(self, url: str) -> httpx.Response:
        """GET 要求を送る。"""
        ...

    def request(self, method: str, url: str, *, json: object | None = None) -> httpx.Response:
        """任意のメソッドで要求を送る（表駆動のテストが使う）。"""
        ...

    def put(self, url: str, *, json: object | None = None) -> httpx.Response:
        """PUT 要求を送る。"""
        ...

    def post(self, url: str, *, json: object | None = None) -> httpx.Response:
        """POST 要求を送る。"""
        ...

    def delete(self, url: str) -> httpx.Response:
        """DELETE 要求を送る。"""
        ...


def make_client(crud: SettingsCrudService) -> HttpClient:
    """`create_settings_router(crud)` だけを載せた `FastAPI` に `TestClient` を繋ぐ。

    **`app` 全体を組まない**（自モジュール内に閉じる＝憲章 §1）。
    """
    app = FastAPI()
    app.include_router(create_settings_router(crud))
    client: HttpClient = TestClient(app)
    return client
