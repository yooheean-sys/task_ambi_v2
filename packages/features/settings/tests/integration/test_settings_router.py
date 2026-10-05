"""US5: `/settings/*` の 18 パス・39 操作（settings-F21）。contracts/settings-http.md §6。

D26 で役割ごとの実効設定の読取（`listProjectRoles`）を 1 本足した（17 パス・38 操作から）。

検証行: H-1（正本との突合）・H-2（422 と領域 A の不変）・H-3（404）・H-4（409）・
H-5（応答ヘッダ）・H-6（口の型）・H-7（否定側）・H-8（相関 ID）。

`app` 全体を組まず、`FastAPI()` を 1 つ作って `include_router(create_settings_router(crud))` する
（自モジュール内に閉じる＝憲章 §1）。**`openapi.yaml` は読むだけで 1 バイトも変更しない**。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from settings_testing import (
    HttpClient,
    StubBoardReader,
    StubVaultReader,
    area_bytes,
    make_client,
    make_crud,
    write_commands,
    write_llm_profiles,
    write_minimal_area_a,
    write_policy,
)

from settings.router import EFFECT_HEADER, GENERATION_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from settings.service.crud import SettingsCrudService

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})
"""正本の path 項目には `parameters` などメソッド以外のキーも並ぶので、集合比較の前に絞る。"""

OK = 200
CREATED = 201
NO_CONTENT = 204
NOT_FOUND = 404
CONFLICT = 409
UNPROCESSABLE = 422

EXPECTED_PATHS = 18
EXPECTED_OPERATIONS = 39
WRITE_OPERATIONS = 22
READ_OPERATIONS = 17


def _openapi_source() -> Path:
    """正本（`docs/design/phase6/openapi.yaml`）。**読むだけ**。"""
    return Path(__file__).resolve().parents[4].parent / "docs/design/phase6/openapi.yaml"


def _reference() -> dict[str, set[str]]:
    """正本の settings のパス集合と操作集合。"""
    spec = yaml.safe_load(_openapi_source().read_text(encoding="utf-8"))
    return {
        path: {name for name in ops if name in HTTP_METHODS}
        for path, ops in spec["paths"].items()
        if path.startswith("/settings")
    }


def _client(
    root: Path, *, board: StubBoardReader | None = None
) -> tuple[HttpClient, SettingsCrudService]:
    """一時領域 A に載せた 39 操作の口。"""
    crud = make_crud(root, board=board or StubBoardReader(), vault=StubVaultReader())
    return make_client(crud), crud


@pytest.fixture
def area(settings_root: Path) -> Iterator[Path]:
    """検査を通る最小の領域 A（PJ 1 件・メンバ 1 人）。"""
    write_minimal_area_a(settings_root)
    yield settings_root


# -- H-1 正本との突合（SC-014・US5 AC-1） ------------------------------------------------


def test_h1_the_generated_spec_matches_the_source_of_truth(area: Path) -> None:
    """H-1・SC-014: 生成された `openapi.json` の**パス集合と操作集合が正本と完全一致**。"""
    client, _crud = _client(area)
    generated = {
        path: {name for name in ops if name in HTTP_METHODS}
        for path, ops in client.get("/openapi.json").json()["paths"].items()
        if path.startswith("/settings")
    }
    reference = _reference()

    assert set(generated) == set(reference)
    assert generated == reference
    assert len(generated) == EXPECTED_PATHS
    assert sum(len(ops) for ops in generated.values()) == EXPECTED_OPERATIONS


def test_h1_the_operation_ids_match_the_source_of_truth(area: Path) -> None:
    """SC-014: `operation_id` も正本と一致する（名前で引ける）。"""
    client, _crud = _client(area)
    spec = yaml.safe_load(_openapi_source().read_text(encoding="utf-8"))
    expected = {
        op["operationId"]
        for path, ops in spec["paths"].items()
        if path.startswith("/settings")
        for name, op in ops.items()
        if name in HTTP_METHODS
    }
    generated = {
        op["operationId"]
        for path, ops in client.get("/openapi.json").json()["paths"].items()
        if path.startswith("/settings")
        for name, op in ops.items()
        if name in HTTP_METHODS
    }

    assert generated == expected


# -- H-5 応答ヘッダ（D-1・010 SD-12・SD-18） ---------------------------------------------

WRITE_CALLS: tuple[tuple[str, str, str, object], ...] = (
    ("createProject", "POST", "/settings/projects", "PROJECT"),
    ("updateProject", "PUT", "/settings/projects/pj", "PROJECT_PJ"),
    ("deleteProject", "DELETE", "/settings/projects/pj", None),
    ("putGlobalAgent", "PUT", "/settings/agents/global/programmer", "GLOBAL_AGENT"),
    ("putAgent", "PUT", "/settings/agents/pj/programmer", {}),
    ("putGlobalTasks", "PUT", "/settings/tasks/global", "TASKS"),
    ("putProjectTasks", "PUT", "/settings/tasks/pj", "TASKS"),
    ("deleteProjectTasks", "DELETE", "/settings/tasks/pj", None),
    ("createLlmProfile", "POST", "/settings/llm-profiles", "PROFILE"),
    ("updateLlmProfile", "PUT", "/settings/llm-profiles/default", "PROFILE_DEFAULT"),
    ("putGlobalLimits", "PUT", "/settings/limits", "LIMIT"),
    ("putProjectLimits", "PUT", "/settings/limits/pj", "LIMIT"),
    ("deleteProjectLimits", "DELETE", "/settings/limits/pj", None),
    ("putPolicy", "PUT", "/settings/policies/pj", "POLICY"),
    ("putProjectRules", "PUT", "/settings/rules/projects/pj", {"body": "本文"}),
    ("putHooks", "PUT", "/settings/hooks/pj", {"hooks": []}),
    ("putHumans", "PUT", "/settings/humans", {"human_actors": ["人"]}),
)
"""ヘッダの観測に使う書込操作のうち、**最小の領域 A でそのまま通る 17 件**。

残る 5 件は最小の領域 A では通らないので別の表で見る（T124）——
`updateCommand`・`deleteLlmProfile` は**専用の土台を用意すれば通る**ので `EXTRA_WRITE_CALLS`
が同じヘッダを観測し、`createCommand`（**B-40**）・`deleteAgent`（**B-49**）・`deleteCommand`
（**B-51**）は**構造的に到達不能**なので `DEAD_END_CALLS` が「422 でヘッダが載らない」ことを
表明する。**書込 22 操作の `operation_id` はすべて本ファイルに現れる**
（`test_every_write_operation_id_is_observed_in_this_file`）。
"""

BODIES: dict[str, object] = {
    "PROJECT": {
        "project_key": "newpj",
        "name": "新規",
        "folders": {
            "pj_input_dir": "newpj",
            "role_dirs": {"programmer": "newpj/roles/programmer"},
            "outcome_dir": "newpj/outcome",
        },
    },
    "PROJECT_PJ": {
        "project_key": "pj",
        "name": "改名",
        "folders": {
            "pj_input_dir": "pj",
            "role_dirs": {"programmer": "pj/roles/programmer"},
            "outcome_dir": "pj/outcome",
        },
    },
    "GLOBAL_AGENT": {"role": "r", "goal": "g", "backstory": "b"},
    "TASKS": {
        key: {"description": "{persona_guide} {acceptance}", "expected_output": "成果"}
        for key in (
            "program",
            "project",
            "subrequest",
            "child",
            "plain",
            "review_normal",
            "review_final",
        )
    },
    "PROFILE": {
        "name": "extra",
        "provider_kind": "openai_compatible",
        "base_url": "http://127.0.0.1:8080/v1",
        "model_id": "m",
        "context_window": 32768,
        "api_key_ref": "EXTRA_API_KEY_REF",
    },
    "PROFILE_DEFAULT": {
        "name": "default",
        "provider_kind": "openai_compatible",
        "base_url": "http://127.0.0.1:8080/v1",
        "model_id": "m2",
        "context_window": 32768,
        "api_key_ref": "DEMO_API_KEY_REF",
    },
    "LIMIT": {"max_requests_per_kickoff": 5, "max_cost_per_kickoff": 0.5},
    "POLICY": {
        "rows": [{"ptype": "p", "fields": ["member:pj.programmer", "tool:read_issue", "execute"]}]
    },
}


@pytest.mark.parametrize(
    ("operation", "method", "path", "body"), WRITE_CALLS, ids=[case[0] for case in WRITE_CALLS]
)
def test_h5_every_write_carries_the_save_headers(
    operation: str, method: str, path: str, body: object, area: Path
) -> None:
    """H-5・D-1・010 SD-12: 書込操作は 201・200・204 のいずれでも保存結果のヘッダを載せる。

    ⚠ **`X-Settings-Effect` の値は文字列リテラルで直接表明する**（010 SD-18）——
    `EffectClass.next_kickoff.value` を経由すると綴りのずれが検出できない。
    """
    del operation
    client, _crud = _client(area)
    payload = BODIES[body] if isinstance(body, str) else body

    response = client.request(method, path, json=payload)

    assert response.status_code in {OK, CREATED, NO_CONTENT}, response.text
    assert response.headers[GENERATION_HEADER] == "2"
    assert response.headers[EFFECT_HEADER] in {"immediate", "next-kickoff"}


def test_h5_the_effect_header_uses_a_hyphen_not_an_underscore(area: Path) -> None:
    """010 SD-18: ヘッダの綴りは **`next-kickoff`（ハイフン）**。内部表現の値を経由しない。"""
    client, _crud = _client(area)

    response = client.put("/settings/humans", json={"human_actors": ["人"]})

    assert response.headers[EFFECT_HEADER] == "next-kickoff"
    assert "_" not in response.headers[EFFECT_HEADER]


def test_h5_the_immediate_effect_is_reported_for_policies(area: Path) -> None:
    """P5D-016: 権限ポリシーは **即時**（`immediate`）。"""
    client, _crud = _client(area)

    response = client.put("/settings/policies/pj", json=BODIES["POLICY"])

    assert response.headers[EFFECT_HEADER] == "immediate"


def _area_with_a_live_command(root: Path) -> None:
    """定義と参照行が揃った領域 A（`PUT /settings/commands/{name}` が届く**唯一の形**）。

    V-24（policy の `cmd:` ⊆ 定義）と V-25（死に定義の禁止）が同時に成立している必要がある
    ので、**定義と参照行を最初から両方置く** —— 口から片方だけ足すことも消すこともできない
    （**B-40**・**B-51**）。
    """
    write_minimal_area_a(root)
    write_commands(root, "commands:\n  run_tests:\n    argv: [uv, run, pytest]\n")
    write_policy(
        root,
        "pj",
        [
            "p, member:pj.programmer, tool:read_issue, execute",
            "p, member:pj.programmer, cmd:run_tests, execute",
        ],
    )


SPARE_PROFILES = """\
profiles:
  default:
    provider_kind: openai_compatible
    base_url: http://127.0.0.1:8080/v1
    model_id: demo-model
    context_window: 32768
    api_key_ref: DEMO_API_KEY_REF
  spare:
    provider_kind: openrouter
    base_url: https://openrouter.ai/api/v1
    model_id: spare-model
    context_window: 16384
    api_key_ref: SPARE_API_KEY_REF
default: default
"""
"""既定（`default`）と、**既定でも参照中でもない** `spare` の 2 件。"""


def _area_with_a_spare_profile(root: Path) -> None:
    """`DELETE /settings/llm-profiles/{name}` が通る領域 A（US4 AC-1 の前半）。"""
    write_minimal_area_a(root)
    write_llm_profiles(root, SPARE_PROFILES)


def test_h5_the_command_effect_is_immediate_too(settings_root: Path) -> None:
    """P5D-016: コマンド定義も **即時**（`immediate`）。"""
    _area_with_a_live_command(settings_root)
    client, _crud = _client(settings_root)

    response = client.put(
        "/settings/commands/run_tests", json={"name": "run_tests", "argv": ["make"]}
    )

    assert response.status_code == OK
    assert response.headers[EFFECT_HEADER] == "immediate"


EXTRA_WRITE_CALLS: tuple[tuple[str, str, str, object, Callable[[Path], None]], ...] = (
    (
        "updateCommand",
        "PUT",
        "/settings/commands/run_tests",
        {"name": "run_tests", "argv": ["make"]},
        _area_with_a_live_command,
    ),
    (
        "deleteLlmProfile",
        "DELETE",
        "/settings/llm-profiles/spare",
        None,
        _area_with_a_spare_profile,
    ),
)
"""**専用の土台を用意すれば通る 2 件**（T124 (a)）。最小の領域 A では 404／422 になるだけで
ヘッダの有無を観測できないため、`WRITE_CALLS` から分けてある。"""


@pytest.mark.parametrize(
    ("operation", "method", "path", "body", "setup"),
    EXTRA_WRITE_CALLS,
    ids=[case[0] for case in EXTRA_WRITE_CALLS],
)
def test_h5_the_two_remaining_reachable_writes_carry_the_headers(
    operation: str,
    method: str,
    path: str,
    body: object,
    setup: Callable[[Path], None],
    settings_root: Path,
) -> None:
    """H-5・T124: 到達可能な残り 2 件も保存結果のヘッダを載せる。"""
    del operation
    setup(settings_root)
    client, _crud = _client(settings_root)

    response = client.request(method, path, json=body)

    assert response.status_code in {OK, CREATED, NO_CONTENT}, response.text
    assert response.headers[GENERATION_HEADER] == "2"
    assert response.headers[EFFECT_HEADER] in {"immediate", "next-kickoff"}


DEAD_END_CALLS: tuple[tuple[str, str, str, object, Callable[[Path], None]], ...] = (
    (
        "createCommand",
        "POST",
        "/settings/commands",
        {"name": "run_tests", "argv": ["uv", "run", "pytest"]},
        write_minimal_area_a,
    ),
    ("deleteAgent", "DELETE", "/settings/agents/pj/programmer", None, write_minimal_area_a),
    (
        "deleteCommand",
        "DELETE",
        "/settings/commands/run_tests",
        None,
        _area_with_a_live_command,
    ),
)
"""**構造的に到達不能な 3 件**（T124 (b)）—— `createCommand` は **B-40**（V-24／V-25 の
相互ロック）、`deleteAgent` は **B-49**（V-19／V-21 の相互ロック）、`deleteCommand` は
**B-51**（B-40 の削除側）。**行き止まりであること自体を表明する**（塞がず・埋めず）。"""


@pytest.mark.parametrize(
    ("operation", "method", "path", "body", "setup"),
    DEAD_END_CALLS,
    ids=[case[0] for case in DEAD_END_CALLS],
)
def test_h5_the_three_unreachable_writes_are_422_without_headers(
    operation: str,
    method: str,
    path: str,
    body: object,
    setup: Callable[[Path], None],
    settings_root: Path,
) -> None:
    """B-40・B-49・B-51: 相互ロックで届かない 3 操作は **422** で、**ヘッダも載らない**。

    保存していないのだからヘッダが載らないのは D-1 のとおりで、**領域 A も変わらない**
    （H-2）。**契約・`openapi.yaml`・実装は 1 行も変えていない** —— 口は正本どおり在り、
    起動時検査の規則も 1 つも足していない。塞ぐには契約の改訂が要る（B 表参照）。
    """
    del operation
    setup(settings_root)
    client, _crud = _client(settings_root)
    before = area_bytes(settings_root)

    response = client.request(method, path, json=body)

    assert response.status_code == UNPROCESSABLE, response.text
    assert GENERATION_HEADER not in response.headers
    assert EFFECT_HEADER not in response.headers
    assert area_bytes(settings_root) == before, "422 なのに領域 A が変わっている"


def test_every_write_operation_id_is_observed_in_this_file(area: Path) -> None:
    """T124: **書込 22 操作すべて**の `operation_id` が本ファイルに現れる。

    編集前は `WRITE_CALLS` が 17 件で、docstring が残る 5 件を「別に組む」と書きながら
    そのテストが**存在しなかった**（`GENERATION_HEADER` の表明がリポジトリ全体で 2 箇所
    だけだった）。

    ⚠ **本文に名前が現れることでは見ない**（docstring に書けば通ってしまい、編集前の
    ファイルでも緑になる）。**3 つの表のどれかに載っていること**＝ヘッダの有無を実際に
    観測するテストへ parametrize されていることを見る。
    """
    client, _crud = _client(area)
    writes = {
        op["operationId"]
        for path, ops in client.get("/openapi.json").json()["paths"].items()
        if path.startswith("/settings")
        for name, op in ops.items()
        if name in HTTP_METHODS and name != "get"
    }
    observed = {case[0] for case in (*WRITE_CALLS, *EXTRA_WRITE_CALLS, *DEAD_END_CALLS)}

    assert len(writes) == WRITE_OPERATIONS
    missing = sorted(writes - observed)
    assert writes == observed, f"ヘッダの有無が観測されていない書込操作: {missing}"


READ_CALLS: tuple[tuple[str, str], ...] = (
    ("GET", "/settings/projects"),
    ("GET", "/settings/projects/pj"),
    ("GET", "/settings/projects/pj/roles"),
    ("GET", "/settings/agents"),
    ("GET", "/settings/agents/global/programmer"),
    ("GET", "/settings/agents/pj/programmer"),
    ("GET", "/settings/tasks/global"),
    ("GET", "/settings/tasks/pj"),
    ("GET", "/settings/llm-profiles"),
    ("GET", "/settings/llm-profiles/default"),
    ("GET", "/settings/limits"),
    ("GET", "/settings/commands"),
    ("GET", "/settings/policies/pj"),
    ("GET", "/settings/rules/projects/pj"),
    ("GET", "/settings/hooks/pj"),
    ("GET", "/settings/humans"),
)


@pytest.mark.parametrize(("method", "path"), READ_CALLS, ids=[path for _m, path in READ_CALLS])
def test_h5_reads_never_carry_the_save_headers(method: str, path: str, area: Path) -> None:
    """D-1: **読取 17 操作にはヘッダを載せない**（保存していないため）。"""
    client, _crud = _client(area)

    response = client.request(method, path)

    assert response.status_code == OK, response.text
    assert GENERATION_HEADER not in response.headers
    assert EFFECT_HEADER not in response.headers


def test_the_write_and_read_counts_match_the_contract(area: Path) -> None:
    """contracts/settings-http.md §1: **書込 22・読取 16**（合計 38）＋ D26 の読取 1（合計 39）。"""
    client, _crud = _client(area)
    generated = {
        (path, name)
        for path, ops in client.get("/openapi.json").json()["paths"].items()
        if path.startswith("/settings")
        for name in ops
        if name in HTTP_METHODS
    }
    reads = {pair for pair in generated if pair[1] == "get"}

    assert len(reads) == READ_OPERATIONS
    assert len(generated) - len(reads) == WRITE_OPERATIONS


def test_project_roles_are_read_through_the_router(area: Path) -> None:
    """D26: 役割ごとの実効設定が口から JSON で読める（装備と許可の対照を含む）。"""
    client, _crud = _client(area)

    response = client.get("/settings/projects/pj/roles")

    assert response.status_code == OK, response.text
    body = response.json()
    assert body["project_key"] == "pj"
    assert [role["persona_key"] for role in body["roles"]] == ["programmer"]
    assert {"tool": "read_issue", "equipped": True, "allowed": True} in body["roles"][0]["tools"]
    assert [row["stage"] for row in body["stage_tools"]] == ["review_normal", "review_final"]


# -- H-3 404 の表（FR-075・SC-015・US5 AC-3） -------------------------------------------

NOT_FOUND_CALLS: tuple[tuple[str, str, str, object], ...] = (
    ("unknown_project_get", "GET", "/settings/projects/居ない", None),
    ("unknown_project_roles", "GET", "/settings/projects/居ない/roles", None),
    ("unknown_project_delete", "DELETE", "/settings/projects/居ない", None),
    ("unknown_project_agents", "PUT", "/settings/agents/居ない/programmer", {}),
    ("unknown_project_tasks", "GET", "/settings/tasks/居ない", None),
    ("unknown_project_policy", "GET", "/settings/policies/居ない", None),
    ("unknown_project_rules", "GET", "/settings/rules/projects/居ない", None),
    ("unknown_project_hooks", "GET", "/settings/hooks/居ない", None),
    ("unknown_project_limits", "DELETE", "/settings/limits/居ない", None),
    ("unknown_profile", "GET", "/settings/llm-profiles/居ない", None),
    ("unknown_command", "GET", "/settings/commands/居ない", None),
    ("unknown_member", "GET", "/settings/agents/pj/analyst", None),
)


@pytest.mark.parametrize(
    ("label", "method", "path", "body"),
    NOT_FOUND_CALLS,
    ids=[case[0] for case in NOT_FOUND_CALLS],
)
def test_h3_unknown_identifiers_are_404_with_a_detail(
    label: str, method: str, path: str, body: object, area: Path
) -> None:
    """H-3・FR-075・SC-015: 未登録の識別子は **404** ＋ `{"detail": "…"}`。"""
    del label
    client, _crud = _client(area)

    response = client.request(method, path, json=body)

    assert response.status_code == NOT_FOUND, response.text
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]


# -- H-4 409 の表（FR-076・US5 AC-4・010 SD-03） ----------------------------------------

CONFLICT_CASES: tuple[tuple[str, StubBoardReader, str], ...] = (
    ("reserved_key", StubBoardReader(), "_program"),
    ("open_issues", StubBoardReader(open_issues=True), "pj"),
    ("board_unavailable", StubBoardReader(unavailable=True), "pj"),
)


@pytest.mark.parametrize(
    ("label", "board", "key"), CONFLICT_CASES, ids=[case[0] for case in CONFLICT_CASES]
)
def test_h4_the_three_deletion_refusals_are_409(
    label: str, board: StubBoardReader, key: str, settings_root: Path
) -> None:
    """H-4・010 SD-03: 解除の拒否 3 通りは **409** ＋ `detail` に理由。**違反一覧は返らない**。"""
    del label
    write_minimal_area_a(settings_root, projects=("pj", "_program"))
    client, _crud = _client(settings_root, board=board)

    response = client.delete(f"/settings/projects/{key}")

    assert response.status_code == CONFLICT, response.text
    assert set(response.json()) == {"detail"}, "409 に機械可読な違反一覧が付いている"
    assert "problems" not in response.text


# -- H-2・H-6 422 の表（SC-001・SC-026・US5 AC-2） ---------------------------------------

UNPROCESSABLE_CASES: tuple[tuple[str, str, str, object], ...] = (
    (
        "candidate_key_rule",
        "POST",
        "/settings/projects",
        {
            "project_key": "pj",
            "name": "重複",
            "folders": {"pj_input_dir": "a", "role_dirs": {}, "outcome_dir": "c"},
        },
    ),
    ("candidate_actor_collision", "PUT", "/settings/humans", {"human_actors": ["pj.programmer"]}),
    (
        "candidate_orphan_subject",
        "PUT",
        "/settings/policies/pj",
        {"rows": [{"ptype": "p", "fields": ["member:pj.analyst", "tool:read_issue", "execute"]}]},
    ),
    ("dto_unknown_field", "PUT", "/settings/agents/pj/programmer", {"nickname": "未知"}),
    ("dto_bad_enum", "PUT", "/settings/policies/pj", {"rows": [{"ptype": "q", "fields": ["a"]}]}),
    (
        "dto_missing_field",
        "PUT",
        "/settings/limits/pj",
        {"max_requests_per_kickoff": 1},
    ),
)


@pytest.mark.parametrize(
    ("label", "method", "path", "body"),
    UNPROCESSABLE_CASES,
    ids=[case[0] for case in UNPROCESSABLE_CASES],
)
def test_h2_h6_rejections_are_422_and_area_a_never_changes(
    label: str, method: str, path: str, body: object, area: Path
) -> None:
    """H-2・H-6・SC-001: 口の型でも候補の検査でも **422**、かつ**バイト列が変わらない**。"""
    client, _crud = _client(area)
    before = area_bytes(area)

    response = client.request(method, path, json=body)

    assert response.status_code == UNPROCESSABLE, response.text
    if label.startswith("candidate_"):
        # 候補全体の検査による 422 は `Problem`（違反の全件）で返る（B-42: `kind` は 13 語）。
        assert set(response.json()) == {"problems"}
        assert response.json()["problems"]
        assert set(response.json()["problems"][0]) == {"where", "kind", "reason"}
    assert area_bytes(area) == before, "422 なのに領域 A が変わっている"


def test_h2_a_where_never_contains_an_absolute_path(area: Path) -> None:
    """SC-007・FR-013: 422 の `where` は領域 A ルート相対（絶対パスを載せない）。"""
    client, _crud = _client(area)

    response = client.put("/settings/humans", json={"human_actors": ["pj.programmer"]})

    wheres = [item["where"] for item in response.json()["problems"]]
    assert wheres
    assert not [where for where in wheres if where.startswith("/")]
    assert str(area) not in response.text


# -- H-7 否定側（SC-015。**設計の条件でのみ返る**） -------------------------------------


def test_h7_a_valid_request_is_not_404_409_or_422(area: Path) -> None:
    """H-7・SC-015: 条件を満たさない要求で 404・409・422 が**誤って返らない**。"""
    client, _crud = _client(area)

    assert client.get("/settings/projects/pj").status_code == OK
    assert client.put("/settings/humans", json={"human_actors": ["人間 A"]}).status_code == OK
    assert client.delete("/settings/projects/pj").status_code == NO_CONTENT


def test_h7_an_unregistered_project_is_404_not_409(area: Path) -> None:
    """H-7: 未登録キーの解除は **404**（409 に畳まない）。"""
    client, _crud = _client(area)

    assert client.delete("/settings/projects/居ない").status_code == NOT_FOUND


def test_h7_a_deletable_project_is_not_409(settings_root: Path) -> None:
    """H-7: 削除ガードの 3 条件をどれも満たさなければ **409 にならない**。"""
    write_minimal_area_a(settings_root, projects=("pj", "keep"))
    client, _crud = _client(settings_root, board=StubBoardReader(open_issues=False))

    assert client.delete("/settings/projects/pj").status_code == NO_CONTENT


# -- H-8 相関 ID（SC-023・US5 AC-6・D-6） ------------------------------------------------


def test_h8_the_save_log_carries_the_correlation_id(
    area: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """H-8・SC-023・FR-085: 保存のログに相関 ID が載る。

    **settings 側の追加実装はゼロ**である（D-6）—— 発番・正規化・contextvar への束縛は
    `apps/api` の純 ASGI ミドルウェアが済ませ、**相関 ID を載せるのは整形器**である。ここでは
    入口の代わりに `CorrelationContext.bind` で束縛し、ログ基盤の出力に載ることを確かめる
    （`apps` を import しない＝憲章 §1）。
    """
    from common.kernel.config import RootConfig  # noqa: PLC0415
    from common.kernel.log import CorrelationContext, LoggerFactory  # noqa: PLC0415

    client, _crud = _client(area)
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    LoggerFactory.configure(RootConfig())
    token = CorrelationContext.bind("test-correlation-id")
    try:
        response = client.put("/settings/humans", json={"human_actors": ["人"]})
    finally:
        CorrelationContext.reset(token)
        root.handlers, root.level = saved_handlers, saved_level

    assert response.status_code == OK
    lines = [
        line for line in capsys.readouterr().out.splitlines() if "settings.save.completed" in line
    ]
    assert lines, "保存のログが出ていない"
    assert "test-correlation-id" in lines[0]


def test_the_dependency_override_is_the_single_swap_point(area: Path) -> None:
    """FR-080・D-5: 差し替え点は `dependencies.py::service_provider` の 1 箇所。"""
    from settings.dependencies import service_provider  # noqa: PLC0415

    crud = make_crud(area, board=StubBoardReader(), vault=StubVaultReader())
    provider = service_provider(crud)

    assert provider() is crud
