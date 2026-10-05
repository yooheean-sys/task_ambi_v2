"""US2: プロジェクト CRUD（settings-F11）。plan (ii) F11-a〜c。

**利用契約 IF-32（`VaultReader.check_project_folders`）と IF-09（`BoardReader.has_open_issues`）が
初めて実際に呼ばれる**。検証行: SC-005・SC-009・SC-010・SC-011・S-16 ＋ FR-033〜FR-044。

表は**契約からの写し**である（期待値を発明しない）—— キー規則は contracts/save-pipeline.md §5、
削除ガードの応答は contracts/settings-http.md §4 D-1。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    StubBoardReader,
    StubVaultReader,
    area_bytes,
    make_crud,
    make_data,
    make_snapshot,
    write_minimal_area_a,
)

from common.interfaces.settings import ProjectNotFound
from settings.exceptions import ProjectDeletionBlocked, SettingsValidationError
from settings.repository import AreaAReader
from settings.schemas import ProjectFoldersSpec, ProjectReposSpec, ProjectSpec
from settings.service import projects as project_service
from settings.service.crud import SettingsCrudService

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from settings.models import AreaAData


def _spec(
    key: str,
    *,
    personas: Sequence[str] = ("programmer",),
    name: str = "新規",
    repos: ProjectReposSpec | None = None,
) -> ProjectSpec:
    """登録・編集の本文（フォルダ対応付け 3 種は必須。`repos` は任意＝ CFG-74）。"""
    return ProjectSpec(
        project_key=key,
        name=name,
        folders=ProjectFoldersSpec(
            pj_input_dir=key,
            role_dirs={persona: f"{key}/roles/{persona}" for persona in personas},  # pyright: ignore[reportArgumentType]  # PersonaKey は StrEnum なので pydantic が文字列から写す
            outcome_dir=f"{key}/outcome",
        ),
        repos=repos,
    )


def _create(
    data: AreaAData, spec: ProjectSpec, *, vault: StubVaultReader | None = None
) -> tuple[AreaAData, object]:
    """`build_create` を既定の挙動設定で呼ぶ（`config.json` の `key_forbidden_chars` を使う）。"""
    from settings_testing import config_for_tests  # noqa: PLC0415

    return project_service.build_create(
        data,
        spec,
        config=config_for_tests(),
        vault_reader=vault if vault is not None else StubVaultReader(),
    )


# -- T037 キー規則（`key_rule`。CFG-36 の初使用点・FR-035） ------------------------------

KEY_RULE_CASES: tuple[tuple[str, str], ...] = (
    ("dot", "pj.a"),
    ("slash", "pj/a"),
    ("backslash", "pj\\a"),
    ("colon", "pj:a"),
    ("space", "pj a"),
    ("duplicate", "pj"),
    ("underscore_not_program", "_secret"),
    ("empty", ""),
)


@pytest.mark.parametrize(("label", "key"), KEY_RULE_CASES, ids=[case[0] for case in KEY_RULE_CASES])
def test_key_rule_rejects_and_touches_nothing(label: str, key: str, settings_root: Path) -> None:
    """FR-035・SC-005: 禁止文字・重複・予約接頭辞が **`key_rule`** で拒否され、何も作られない。"""
    del label
    write_minimal_area_a(settings_root)
    before = area_bytes(settings_root)
    data = make_data(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        _create(data, _spec(key))

    assert {item.kind for item in caught.value.problems} == {"key_rule"}
    assert area_bytes(settings_root) == before


def test_key_rule_does_not_call_the_vault(settings_root: Path) -> None:
    """FR-034: **前段で落ちたら後段を実行しない**（キー規則違反のとき保管庫を呼ばない）。"""
    write_minimal_area_a(settings_root)
    vault = StubVaultReader(missing=["どこか"])

    with pytest.raises(SettingsValidationError):
        _create(make_data(settings_root), _spec("pj.a"), vault=vault)

    assert vault.calls == [], "キー規則で落ちたのに保管庫を呼んでいる"


def test_the_reserved_key_itself_is_allowed_at_registration(settings_root: Path) -> None:
    """P3: 先頭 `_` は **`_program` だけ**許す（予約の意味をそれ以外に広げない）。"""
    write_minimal_area_a(settings_root)

    candidate, _changes = _create(make_data(settings_root), _spec("_program"))

    assert "_program" in candidate.projects_raw


def test_key_rule_is_not_applied_retroactively(settings_root: Path) -> None:
    """P8・Edge Cases: 適用点は**新規キーだけ**（既に居る禁止文字入りのキーを違反にしない）。"""
    write_minimal_area_a(settings_root, projects=("pj", "old.key"))
    data = make_data(settings_root)

    candidate, _changes = _create(data, _spec("newpj"))

    assert "old.key" in candidate.projects_raw


# -- T038 フォルダの実在確認（`folder_missing`。IF-32・FR-036・FR-037） -----------------


def test_folder_missing_reports_every_absent_path(settings_root: Path) -> None:
    """FR-037・SC-005: 不在の**件数だけ**違反が積まれ、どのパスが無いかが全件返る。"""
    write_minimal_area_a(settings_root)
    missing = ["newpj", "newpj/roles/programmer", "newpj/outcome"]

    with pytest.raises(SettingsValidationError) as caught:
        _create(make_data(settings_root), _spec("newpj"), vault=StubVaultReader(missing=missing))

    assert [item.kind for item in caught.value.problems] == ["folder_missing"] * len(missing)
    for path in missing:
        assert any(path in item.reason for item in caught.value.problems)


def test_a_vault_path_violation_becomes_one_folder_missing(settings_root: Path) -> None:
    """D-2・FR-036: `VaultPathViolation` は **`folder_missing` の 1 件**に翻訳する。"""
    write_minimal_area_a(settings_root)

    with pytest.raises(SettingsValidationError) as caught:
        _create(make_data(settings_root), _spec("newpj"), vault=StubVaultReader(violate=True))

    assert [item.kind for item in caught.value.problems] == ["folder_missing"]


def test_the_vault_is_consulted_on_edit_too(settings_root: Path) -> None:
    """DIFF-14: **編集でも実在確認を呼ぶ**（副図に従う）。"""
    write_minimal_area_a(settings_root)
    vault = StubVaultReader()

    project_service.build_update(make_data(settings_root), "pj", _spec("pj"), vault_reader=vault)

    assert len(vault.calls) == 1


# -- T039 削除ガード 3 分岐（IF-09・FR-041〜FR-043・P2・P3） -----------------------------


def test_deleting_a_reserved_key_is_blocked_without_touching_the_board(settings_root: Path) -> None:
    """FR-041・P3: 予約キーは **409**。**前段で落ちたら後段を実行しない**（盤面を呼ばない）。"""
    write_minimal_area_a(settings_root, projects=("_program",))
    board = StubBoardReader(open_issues=True)

    with pytest.raises(ProjectDeletionBlocked, match="予約"):
        project_service.build_delete(make_data(settings_root), "_program", board_reader=board)

    assert board.calls == []


def test_deleting_a_project_with_open_issues_is_blocked(settings_root: Path) -> None:
    """FR-042: 未完了課題があれば **409**（先に盤面側で片付ける旨）。"""
    write_minimal_area_a(settings_root)

    with pytest.raises(ProjectDeletionBlocked, match="未完了"):
        project_service.build_delete(
            make_data(settings_root), "pj", board_reader=StubBoardReader(open_issues=True)
        )


def test_an_unreachable_board_falls_to_the_refusing_side(settings_root: Path) -> None:
    """FR-042・D06-j(4)・憲章 §10: `BoardUnavailable` も **409**（推測も再試行もしない）。"""
    write_minimal_area_a(settings_root)

    with pytest.raises(ProjectDeletionBlocked, match="盤面"):
        project_service.build_delete(
            make_data(settings_root), "pj", board_reader=StubBoardReader(unavailable=True)
        )


def test_deleting_an_unknown_key_is_not_found(settings_root: Path) -> None:
    """FR-075: 未登録キーは **404**（409 に畳まない）。"""
    write_minimal_area_a(settings_root)

    with pytest.raises(ProjectNotFound):
        project_service.build_delete(
            make_data(settings_root), "居ない", board_reader=StubBoardReader()
        )


# -- T040 拒否 8 通りが別メッセージ（SC-009） -------------------------------------------


def test_sc009_the_eight_refusals_have_eight_distinct_messages(settings_root: Path) -> None:
    """SC-009: 8 通りの拒否が**それぞれ別のメッセージ**で返る（同じ文言に畳まない）。"""
    write_minimal_area_a(
        settings_root, projects=("pj", "_program"), personas=("programmer", "analyst")
    )
    data = make_data(settings_root)
    messages: list[str] = []

    for key in ("pj.a", "pj", "_secret"):  # 禁止文字・重複・予約接頭辞
        with pytest.raises(SettingsValidationError) as caught:
            _create(data, _spec(key))
        messages.append(caught.value.problems[0].reason)

    with pytest.raises(SettingsValidationError) as caught:  # フォルダ不在
        _create(data, _spec("newpj"), vault=StubVaultReader(missing=["newpj"]))
    messages.append(caught.value.problems[0].reason)

    crud = make_crud(settings_root, vault=StubVaultReader())
    with pytest.raises(SettingsValidationError) as caught:  # 役割フォルダを外す（V-17）
        crud.update_project("pj", _spec("pj", personas=("programmer",)))
    messages.append(caught.value.problems[0].reason)

    for board, key in (  # 予約キーの解除・未完了課題あり・盤面が読めない
        (StubBoardReader(), "_program"),
        (StubBoardReader(open_issues=True), "pj"),
        (StubBoardReader(unavailable=True), "pj"),
    ):
        with pytest.raises(ProjectDeletionBlocked) as blocked:
            project_service.build_delete(data, key, board_reader=board)
        messages.append(str(blocked.value))

    assert len(set(messages)) == len(messages) == 8, f"重複したメッセージがある: {messages}"


# -- T041 登録・編集・解除の本筋 ---------------------------------------------------------


def test_registration_writes_exactly_three_files(settings_root: Path) -> None:
    """FR-033: `projects/<k>/{project.yaml, agents.yaml, policy.csv}` の **3 つだけ**が置かれる。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())

    view, outcome = crud.create_project(_spec("newpj"))

    created = sorted(path.name for path in (settings_root / "projects/newpj").iterdir())
    assert created == ["agents.yaml", "policy.csv", "project.yaml"]
    assert view.project_key == "newpj"
    assert outcome.generation == 2


def test_registration_does_not_write_the_project_key_into_the_file(settings_root: Path) -> None:
    """FR-038: `project.yaml` に `project_key` を書かない（ディレクトリ名から注入する）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())

    crud.create_project(_spec("newpj"))

    body = (settings_root / "projects/newpj/project.yaml").read_text(encoding="utf-8")
    assert "project_key" not in body


def test_edit_only_remaps_folders_and_never_touches_the_vault_contents(settings_root: Path) -> None:
    """FR-039: 編集は**対応付けの付け替えだけ**（フォルダ実体を触らない）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())
    before = area_bytes(settings_root)

    view, _outcome = crud.update_project("pj", _spec("pj", name="名前を変えた"))

    after = area_bytes(settings_root)
    assert view.name == "名前を変えた"
    assert {rel for rel in after if after[rel] != before.get(rel)} == {"projects/pj/project.yaml"}


def test_dropping_a_role_dir_with_a_member_is_refused_by_v17(settings_root: Path) -> None:
    """FR-040・C-07・P4: `role_dirs` からペルソナを外すと **V-17** が落とす。"""
    write_minimal_area_a(settings_root, personas=("programmer", "analyst"))
    crud = make_crud(settings_root, vault=StubVaultReader())

    with pytest.raises(SettingsValidationError) as caught:
        crud.update_project("pj", _spec("pj", personas=("programmer",)))

    assert {item.kind for item in caught.value.problems} == {"forward"}


def test_a_body_key_that_differs_from_the_path_is_a_key_rule_violation(settings_root: Path) -> None:
    """data-model §2-2: `PUT` の本文の `project_key` がパスと食い違えば **422 `key_rule`**。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())

    with pytest.raises(SettingsValidationError) as caught:
        crud.update_project("pj", _spec("別のキー"))

    assert {item.kind for item in caught.value.problems} == {"key_rule"}


def test_deletion_archives_everything_and_clears_the_registry(settings_root: Path) -> None:
    """SC-010・SC-011・FR-044: 全ファイルが退避先に揃い、台帳からメンバと権限が消える。"""
    write_minimal_area_a(settings_root, projects=("pj", "keep"))
    crud = make_crud(settings_root, board=StubBoardReader())
    before = sorted(path.name for path in (settings_root / "projects/pj").iterdir())

    outcome = crud.delete_project("pj")

    archives = sorted((settings_root / "_archive/projects").iterdir())
    assert len(archives) == 1
    assert archives[0].name.startswith("pj.")
    assert sorted(path.name for path in archives[0].iterdir()) == before
    assert not (settings_root / "projects/pj").exists()
    assert AreaAReader(settings_root).list_project_keys() == ("keep",)
    assert outcome.generation == 2


def test_s16_deletion_also_drops_the_limits_override(settings_root: Path) -> None:
    """S-16・FR-044・plan D-k: 解除で `overrides` から当該キーが消え、再登録で復活しない。"""
    write_minimal_area_a(settings_root, projects=("pj", "keep"))
    (settings_root / "global/limits.yaml").write_text(
        "overrides:\n"
        "  pj:\n    max_requests_per_kickoff: 7\n    max_cost_per_kickoff: 0.25\n"
        "  keep:\n    max_requests_per_kickoff: 9\n    max_cost_per_kickoff: 0.5\n",
        encoding="utf-8",
        newline="\n",
    )
    crud = make_crud(settings_root, board=StubBoardReader(), vault=StubVaultReader())

    crud.delete_project("pj")

    body = (settings_root / "global/limits.yaml").read_text(encoding="utf-8")
    assert "pj:" not in body
    assert "keep:" in body, "他の PJ の上書きまで落としている"
    assert "global:" not in body, "plan D-l: ③ 段を殺す `global:` を書き出している"

    crud.create_project(_spec("pj"))
    reborn = (settings_root / "global/limits.yaml").read_text(encoding="utf-8")
    assert "pj:" not in reborn, "再登録で以前の上限が復活した"


def test_deletion_leaves_the_vault_folders_alone(settings_root: Path) -> None:
    """SC-010・FR-007: 解除しても**保管庫のフォルダ実体は 1 つも消えない**。"""
    write_minimal_area_a(settings_root)
    vault = StubVaultReader()
    crud = make_crud(settings_root, board=StubBoardReader(), vault=vault)

    crud.delete_project("pj")

    assert vault.calls == [], "解除で保管庫に触っている"


def test_settings_cannot_write_to_the_board_at_all(settings_root: Path) -> None:
    """P5・FR-008・FR-045・D07-g: `BoardWriter` を要求しないので盤面へ書く手段が構造的に無い。"""
    del settings_root
    fields = SettingsCrudService.__init__.__annotations__

    assert "board_writer" not in fields
    assert set(fields) == {
        "service",
        "snapshot",
        "data",
        "reader",
        "writer",
        "board_reader",
        "vault_reader",
        "config",
        "return",
    }


# -- T042 リポジトリ対応付け `repos`（CFG-74・D24。R-13-c の据え置き解除） -----------------

HTTPS_URL = "https://gitlab.example.com/group/repo.git"
LOCAL_DIR = "/home/demo/work/repo"


def test_repos_are_written_and_returned(settings_root: Path) -> None:
    """CFG-74: repos 付きで登録すると `project.yaml` に `repos:` 節が書かれ、応答にも載る。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())
    repos = ProjectReposSpec(remote_url=HTTPS_URL, local_path=LOCAL_DIR)

    view, _outcome = crud.create_project(_spec("newpj", repos=repos))

    assert view.repos is not None
    assert view.repos.remote_url == HTTPS_URL
    assert view.repos.local_path == LOCAL_DIR
    body = (settings_root / "projects/newpj/project.yaml").read_text(encoding="utf-8")
    assert "repos:" in body
    assert HTTPS_URL in body
    assert LOCAL_DIR in body


def test_repos_key_is_omitted_when_unregistered(settings_root: Path) -> None:
    """CFG-74・運用要件: 未登録のプロジェクトには `repos` キー自体を作らない。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())

    view, _outcome = crud.create_project(_spec("newpj"))

    assert view.repos is None
    body = (settings_root / "projects/newpj/project.yaml").read_text(encoding="utf-8")
    assert "repos" not in body


def test_repos_accept_only_one_side(settings_root: Path) -> None:
    """裁定（形式のみ検査）: 片方だけの登録を許す（両項目とも任意）。"""
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())

    view, _outcome = crud.create_project(
        _spec("newpj", repos=ProjectReposSpec(local_path=LOCAL_DIR))
    )

    assert view.repos is not None
    assert view.repos.remote_url is None
    assert view.repos.local_path == LOCAL_DIR
    body = (settings_root / "projects/newpj/project.yaml").read_text(encoding="utf-8")
    assert "local_path" in body
    assert "remote_url" not in body


def test_blank_repos_strings_are_normalized_to_unregistered(settings_root: Path) -> None:
    """裁定（空文字の扱い）: 空文字・空白のみは未登録と同一視する。

    両方空なら `repos` キー自体を書かないので、応答も未登録と同じ `None` になる。
    """
    write_minimal_area_a(settings_root)
    crud = make_crud(settings_root, vault=StubVaultReader())

    view, _outcome = crud.create_project(
        _spec("newpj", repos=ProjectReposSpec(remote_url="", local_path="  "))
    )

    assert view.repos is None
    body = (settings_root / "projects/newpj/project.yaml").read_text(encoding="utf-8")
    assert "repos" not in body


def test_repos_format_violations_are_reported_together_and_touch_nothing(
    settings_root: Path,
) -> None:
    """FR-012 踏襲: 形式違反は `format` で**全件**積まれ、何も書かれない（実在は見ない）。"""
    write_minimal_area_a(settings_root)
    before = area_bytes(settings_root)
    repos = ProjectReposSpec(remote_url="not-a-url", local_path="relative/path")

    with pytest.raises(SettingsValidationError) as caught:
        _create(make_data(settings_root), _spec("newpj", repos=repos))

    assert {item.kind for item in caught.value.problems} == {"format"}
    assert {item.where for item in caught.value.problems} == {
        "projects:repos.remote_url",
        "projects:repos.local_path",
    }
    assert area_bytes(settings_root) == before


FORMAT_OK_CASES: tuple[tuple[str, str, str], ...] = (
    ("https", "https://gitlab.example.com/group/repo.git", "/home/demo/work/repo"),
    ("http", "http://git.local/group/repo", "/repo"),
    ("scp_like", "git@gitlab.example.com:group/repo.git", "/"),
)


@pytest.mark.parametrize(
    ("label", "url", "path"), FORMAT_OK_CASES, ids=[c[0] for c in FORMAT_OK_CASES]
)
def test_repos_format_accepts_https_scp_like_and_absolute_path(
    label: str, url: str, path: str, settings_root: Path
) -> None:
    """裁定（形式検査の強度）: http(s)://・SCP ライク構文・POSIX 絶対パスを許す。"""
    del label
    write_minimal_area_a(settings_root)

    candidate, _changes = _create(
        make_data(settings_root),
        _spec("newpj", repos=ProjectReposSpec(remote_url=url, local_path=path)),
    )

    assert "newpj" in candidate.projects_raw


def test_repos_can_be_updated_and_unregistered(settings_root: Path) -> None:
    """編集の往復: 追加 → 更新 → 解除（`None`）で `repos` キーが消える。"""
    write_minimal_area_a(settings_root, projects=("pj",))
    crud = make_crud(settings_root, vault=StubVaultReader())

    added, _ = crud.update_project("pj", _spec("pj", repos=ProjectReposSpec(remote_url=HTTPS_URL)))
    assert added.repos is not None and added.repos.remote_url == HTTPS_URL
    path = settings_root / "projects/pj/project.yaml"
    assert "repos:" in path.read_text(encoding="utf-8")

    changed, _ = crud.update_project(
        "pj", _spec("pj", repos=ProjectReposSpec(remote_url=HTTPS_URL, local_path=LOCAL_DIR))
    )
    assert changed.repos is not None and changed.repos.local_path == LOCAL_DIR

    dropped, _ = crud.update_project("pj", _spec("pj"))
    assert dropped.repos is None
    assert "repos" not in path.read_text(encoding="utf-8"), "解除したのに repos キーが残っている"


def test_project_yaml_without_repos_still_loads(settings_root: Path) -> None:
    """後方互換: `repos` を持たない既存の `project.yaml` はそのまま読める（`None` になる）。"""
    write_minimal_area_a(settings_root)

    snapshot = make_snapshot(settings_root)

    assert snapshot.projects["pj"].repos is None


def test_project_yaml_with_repos_loads_into_the_passing_type(settings_root: Path) -> None:
    """CFG-74: `repos` 付きの `project.yaml` が受け渡し型 `Project.repos` に写る。"""
    write_minimal_area_a(settings_root)
    path = settings_root / "projects/pj/project.yaml"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"repos:\n  remote_url: {HTTPS_URL}\n  local_path: {LOCAL_DIR}\n")

    snapshot = make_snapshot(settings_root)

    repos = snapshot.projects["pj"].repos
    assert repos is not None
    assert repos.remote_url == HTTPS_URL
    assert repos.local_path == LOCAL_DIR
