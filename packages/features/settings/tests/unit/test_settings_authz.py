"""認可（IF-29・settings-F09）。判定器の構築と 3 つの担保 ＋ `enforce` の表駆動。

contracts/authorizer.md §2〜§5。**語彙の正しさは検査しない**（写像は executor の仕事＝ FR-007）。
表は「実際に使われる形の例」として obj 語彙 3 系統 × 許可／不許可で組む。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from settings_testing import (
    make_service,
    make_snapshot,
    write,
    write_minimal_area_a,
    write_policy,
    write_project,
)

from settings.exceptions import SettingsAreaError
from settings.models import PROBE_TRIPLE
from settings.repository import AreaAReader
from settings.service.loader import build_enforcer

if TYPE_CHECKING:
    from pathlib import Path

BROKEN_MODEL = """\
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[policy_effect]
e = some(where (p_eft == allow))

[matchers]
m = g(r.sub, p.sub) && keyMatch(r.obj, p.obj) && r.act == p.act
"""
"""`[role_definition]` を欠いた `model.conf`。**構築時には落ちない**（M-07）。"""

BROKEN_MATCHER = """\
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[role_definition]
g = _, _

[policy_effect]
e = some(where (p_eft == allow))

[matchers]
m = bogusFunc(r.sub, p.sub) && r.act == p.act
"""
"""照合式が未定義の機能を参照する `model.conf`。**最初の `enforce()`** で初めて落ちる（M-08）。"""

ROWS = [
    "p, member:pj.programmer, tool:read_issue, execute",
    "p, member:pj.programmer, cmd:pytest, execute",
    "p, member:pj.writer, vault:*, read",
    "g, member:pj.writer, role_writer",
    "p, role_writer, tool:write_outcome, execute",
]


def _authz_area(root: Path, rows: list[str] | None = None) -> None:
    write_minimal_area_a(root, personas=("programmer",))
    write_project(root, "pj", ("programmer", "writer"))
    write(root, "projects/pj/agents.yaml", "programmer: {}\nwriter: {}\n")
    write(root, "global/commands.yaml", "commands:\n  pytest:\n    argv:\n      - pytest\n")
    write_policy(root, "pj", ROWS if rows is None else rows)


# -- 構築と 3 つの担保（§2・§3） --------------------------------------------------------


def test_one_enforcer_per_project(settings_root: Path) -> None:
    """R-11: 判定器は **PJ ごとに 1 個**。"""
    write_minimal_area_a(settings_root, projects=("pj", "other"))
    enforcers = make_snapshot(settings_root).enforcers
    assert set(enforcers) == {"pj", "other"}
    assert enforcers["pj"] is not enforcers["other"]


def test_missing_policy_file_aborts_startup(settings_root: Path) -> None:
    """R-12: `policy.csv` 不在は**起動中断**（空ファイルやアダプタ無しで代替しない）。"""
    write_minimal_area_a(settings_root)
    (settings_root / "projects/pj/policy.csv").unlink()
    with pytest.raises(SettingsAreaError) as excinfo:
        make_snapshot(settings_root)
    assert any("policy.csv" in issue for issue in excinfo.value.issues)


def test_row_width_violation_is_detected_at_build(settings_root: Path) -> None:
    """担保 2（M-09・FR-048c）: 不正な arity は構築時に通り `enforce()` で落ちる。

    列数を build 時に検査しないと「起動は通ったが最初の Tool 実行で落ちる」状態になる。
    """
    _authz_area(settings_root, ["p, member:pj.programmer, tool:read_issue"])
    reader = AreaAReader(settings_root)
    with pytest.raises(SettingsAreaError) as excinfo:
        build_enforcer(reader.model_conf_path(), reader.policy_csv_path("pj"))
    assert any("列数" in issue for issue in excinfo.value.issues)


def test_missing_role_definition_is_detected_at_build(settings_root: Path) -> None:
    """M-07: `[role_definition]` を欠くと `get_grouping_policy()` が `TypeError` を送出する。

    構築そのものは成功するため、build 時に触れないと**後半の F16 が CRUD する `g` 行が
    黙って無効化される**。担保 2 の入口で捕まえ、原因をモデル側として報告する。
    """
    _authz_area(settings_root)
    write(settings_root, "global/model.conf", BROKEN_MODEL)
    reader = AreaAReader(settings_root)
    with pytest.raises(SettingsAreaError, match="権限モデルが壊れている"):
        build_enforcer(reader.model_conf_path(), reader.policy_csv_path("pj"))


def test_broken_model_conf_names_the_model_not_each_policy_csv(settings_root: Path) -> None:
    """(d2) 段: 壊れた `model.conf` を **PJ ごとの `policy.csv` のせいにしない**（FR-062）。

    `read_policy` は自前で判定器を組むため、`[role_definition]` を欠くと PJ の数だけ
    `TypeError` が上がる。それを `policy.csv` の失敗として積むと、原因ファイルと違う絶対パスを
    N 件並べたうえで**壊れているファイルの絶対パスがどこにも出ない**（原因が 1 つに定まらない）。
    """
    write_minimal_area_a(settings_root, projects=("pj", "pj2"))
    write(settings_root, "global/model.conf", BROKEN_MODEL)
    with pytest.raises(SettingsAreaError) as excinfo:
        make_snapshot(settings_root)
    issues = excinfo.value.issues
    assert len(issues) == 1, issues
    assert "権限モデルが壊れている" in issues[0]
    assert str(settings_root / "global/model.conf") in issues[0]
    assert not [issue for issue in issues if "policy.csv" in issue]


def test_auto_save_is_disabled_on_the_built_enforcer(
    settings_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """担保 1（M-12・FR-048d）: 構築直後に `enable_auto_save(False)` を掛ける。

    有効なままだと、後半（F16）の `g` 行 CRUD が**判定器経由で `policy.csv` を書き戻す**経路を
    持ってしまう。書込は所有モジュールの明示的な保存経路に限る、という意図をここで固定する。
    """
    calls: list[bool] = []

    def _record(_self: object, value: bool) -> None:  # noqa: FBT001
        calls.append(value)

    write_minimal_area_a(settings_root)
    reader = AreaAReader(settings_root)
    model_conf, policy_csv = reader.model_conf_path(), reader.policy_csv_path("pj")
    probe = build_enforcer(model_conf, policy_csv)
    monkeypatch.setattr(type(probe), "enable_auto_save", _record)
    build_enforcer(model_conf, policy_csv)
    assert calls == [False]


def test_broken_matcher_is_detected_only_by_the_probe(settings_root: Path) -> None:
    """担保 3（M-08・FR-048b）: 照合式の壊れは**最初の判定**で初めて落ちる。

    構築も `get_grouping_policy()` も通るので、空打ちを 1 回しないと「起動は通ったが最初の
    Tool 実行で落ちる」＝憲章 §5 が避けようとした状態になる。
    """
    _authz_area(settings_root)
    write(settings_root, "global/model.conf", BROKEN_MATCHER)
    reader = AreaAReader(settings_root)
    from settings.repository import new_enforcer  # noqa: PLC0415

    raw = new_enforcer(reader.model_conf_path(), reader.policy_csv_path("pj"))
    assert raw.get_grouping_policy() is not None  # 構築も g 行の列挙も通ってしまう
    with pytest.raises(SettingsAreaError, match="権限モデルが壊れている"):
        build_enforcer(reader.model_conf_path(), reader.policy_csv_path("pj"))


def test_probe_result_is_not_used_as_a_verdict(settings_root: Path) -> None:
    """担保 3 の**戻り値は判定に使わない**（M-03 (3)。`*` の行があると真が返りうる）。"""
    _authz_area(settings_root, ["p, member:pj.programmer, *, __probe__"])
    reader = AreaAReader(settings_root)
    enforcer = build_enforcer(reader.model_conf_path(), reader.policy_csv_path("pj"))
    assert enforcer.enforce(*PROBE_TRIPLE) is False
    assert enforcer.enforce("member:pj.programmer", "何でも", "__probe__") is True


def test_enforce_survives_deleting_the_files(settings_root: Path) -> None:
    """M-11: 構築後にファイルを消しても同じ結果を返す（P5D-016 が casbin 側でも成立する）。"""
    _authz_area(settings_root)
    service = make_service(settings_root)
    before = service.enforce("member:pj.programmer", "tool:read_issue", "execute")
    (settings_root / "projects/pj/policy.csv").unlink()
    (settings_root / "global/model.conf").unlink()
    assert service.enforce("member:pj.programmer", "tool:read_issue", "execute") == before is True


# -- enforce の表駆動（§5。obj 語彙 3 系統 × 許可／不許可） ------------------------------


@pytest.mark.parametrize(
    ("subject", "obj", "act", "expected"),
    [
        pytest.param("member:pj.programmer", "tool:read_issue", "execute", True, id="tool 許可"),
        pytest.param(
            "member:pj.programmer", "tool:read_input_file", "execute", False, id="tool 別名"
        ),
        pytest.param(
            "member:pj.programmer", "tool:read", "execute", False, id="tool 前方一致は不可"
        ),
        pytest.param("member:pj.programmer", "cmd:pytest", "execute", True, id="cmd 許可"),
        pytest.param("member:pj.programmer", "cmd:rm", "execute", False, id="cmd 未登録"),
        pytest.param("member:pj.writer", "vault:pj_input", "read", True, id="vault ワイルドカード"),
        pytest.param("member:pj.writer", "vault:pj_input", "write", False, id="vault act 違い"),
        pytest.param("member:pj.writer", "tool:write_outcome", "execute", True, id="g 経由の許可"),
        pytest.param("member:pj.programmer", "vault:pj_input", "read", False, id="権限行が無い"),
    ],
)
def test_enforce_table(
    settings_root: Path, subject: str, obj: str, act: str, expected: bool
) -> None:
    """M-02・M-03・M-06 が確かめた `keyMatch` の意味論をそのまま表にする。"""
    _authz_area(settings_root)
    assert make_service(settings_root).enforce(subject, obj, act) is expected


def test_removing_the_grouping_row_denies_the_role_grant(settings_root: Path) -> None:
    """M-07 の実害の再現: `g` 行を消すと `p, role_*, …` 行が到達不能になる。"""
    _authz_area(settings_root, [row for row in ROWS if not row.startswith("g,")])
    assert (
        make_service(settings_root).enforce("member:pj.writer", "tool:write_outcome", "execute")
        is False
    )


@pytest.mark.parametrize(
    ("request_obj", "policy_obj", "expected"),
    [
        pytest.param("vault:role", "vault:role:*", False, id="vault:role:* は vault:role に不一致"),
        pytest.param("vault:roles_other", "vault:role*", True, id="コロンなしは別領域にも一致"),
        pytest.param("cmd:何でも", "*", True, id="obj が * だけの行は全対象を許可"),
    ],
)
def test_key_match_pitfalls(
    settings_root: Path, request_obj: str, policy_obj: str, expected: bool
) -> None:
    """M-03 の落とし穴 3 件（`SKILL.md` に載せる）。"""
    _authz_area(settings_root, [f"p, member:pj.programmer, {policy_obj}, read"])
    assert make_service(settings_root).enforce("member:pj.programmer", request_obj, "read") is (
        expected
    )


# -- 「黙って許可に倒れない」（§4・P3） -------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("agent:programmer", id="形式が違う"),
        pytest.param("", id="空文字"),
        pytest.param("member:other.programmer", id="未登録 PJ"),
        pytest.param('"member:pj.programmer"', id="引用符つき（M-09 の罠）"),
    ],
)
def test_unknown_subject_is_denied_with_a_mark(
    settings_root: Path, subject: str, caplog: pytest.LogCaptureFixture
) -> None:
    """US2 AC-3: 主体から PJ を引けないときは**拒否 ＋ 構造化ログ**（例外を送出しない）。"""
    _authz_area(settings_root)
    service = make_service(settings_root)
    with caplog.at_level(logging.WARNING, logger="settings.service"):
        assert service.enforce(subject, "tool:read_issue", "execute") is False
    assert "settings.enforce.unknown_subject" in caplog.text


def test_missing_enforcer_is_denied_with_a_mark(
    settings_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """US2 AC-4: PJ は在るが判定器が無いときも拒否（起動時検査を通っていれば起きない）。"""
    _authz_area(settings_root)
    from dataclasses import replace  # noqa: PLC0415

    from settings.service import SettingsService  # noqa: PLC0415

    snapshot = make_snapshot(settings_root)
    service = SettingsService(replace(snapshot, enforcers={}))
    with caplog.at_level(logging.WARNING, logger="settings.service"):
        assert service.enforce("member:pj.programmer", "tool:read_issue", "execute") is False
    assert "settings.enforce.enforcer_missing" in caplog.text


def test_enforce_does_not_parse_the_subject_string(settings_root: Path) -> None:
    """§4: 主体から PJ を引くのに**文字列解析をしない**（台帳を引く＝二重宣言を作らない）。"""
    import inspect  # noqa: PLC0415

    from settings.service import SettingsService  # noqa: PLC0415

    source = inspect.getsource(SettingsService.enforce)
    for token in ("split", "partition", "removeprefix", "[len("):
        assert token not in source
