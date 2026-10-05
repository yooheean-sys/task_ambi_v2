"""組み立てと起動時検査（F15・FR-079〜FR-085・SC-034・SC-034a・SC-035）。

正本は `specs/009-orchestrator/contracts/orchestrator-config.md` §3・§5・§6。
**設定読取のテストだけ `tmp_path` を使う**（bd も一時 vault も使わない）。
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    Stubs,
    make_config,
    make_ctx,
    make_issue,
    make_outcome,
    make_review,
    make_stubs,
    persona_label,
    project_label,
)
from fastapi import APIRouter

import orchestrator as orchestrator_package
from common.interfaces.orchestrator import KickoffRunner
from common.kernel.enums import PersonaKey
from orchestrator import OrchestratorConfig, OrchestratorStartupError, build, config_path

VALID: dict[str, Any] = {
    "max_returns": 4,
    "recent_results_n": 20,
    "system_actor": "system",
    "comment_marker": "[orchestrator]",
    "review_persona": "planner",
}


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _point_at(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(orchestrator_package, "config_path", lambda: path)


def _build_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict[str, Any], stubs: Stubs
) -> None:
    _point_at(monkeypatch, _write(tmp_path, payload))
    build(stubs.deps())


def _record_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`builtins.open` と `Path.open` の**両方**を通るファイル I/O を記録する。

    片方だけを計測すると素通りする —— `Path.read_text` は `Path.open` を、素の `open()` は
    `builtins.open` を通るので、塞ぐのは両方でなければならない（SC-034a・SC-035）。
    """
    opened: list[str] = []
    original_path_open: Any = Path.open
    original_builtin_open: Any = builtins.open

    def path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(self))
        return original_path_open(self, *args, **kwargs)

    def builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return original_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(builtins, "open", builtin_open)
    return opened


def _offer_one(stubs: Stubs) -> None:
    """周回が**実際に 1 課題を処理する**台本（空回りでは I/O 0 件が測れない）。"""
    stubs.state.offer(make_issue("bd-1", labels=[project_label(), persona_label()]))
    stubs.proposal_gate.tokens[""] = []
    stubs.crew_executor.default_outcome = make_outcome(completed=True, review=make_review())


# --------------------------------------------------------------------------------------
# (a) 設定ファイルが読めない（SC-034）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        ({**VALID, "foo": 1}, "未知キー"),
        ({**VALID, "max_returns": "2"}, "型の不一致"),
        ({key: value for key, value in VALID.items() if key != "review_persona"}, "必須値欠け"),
    ],
)
def test_broken_config_aborts_with_the_absolute_path(
    stubs: Stubs,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, Any],
    label: str,
) -> None:
    path = _write(tmp_path, payload)
    _point_at(monkeypatch, path)

    with pytest.raises(OrchestratorStartupError) as caught:
        build(stubs.deps())

    assert str(path) in str(caught.value), label


def test_missing_config_file_aborts_with_the_absolute_path(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    _point_at(monkeypatch, path)

    with pytest.raises(OrchestratorStartupError) as caught:
        build(stubs.deps())

    assert str(path) in str(caught.value)


# --------------------------------------------------------------------------------------
# (b)(c)(d) 値の検査
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["max_returns", "recent_results_n"])
def test_thresholds_must_be_at_least_one(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    with pytest.raises(OrchestratorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, {**VALID, key: 0}, stubs)

    message = str(caught.value)
    assert key in message
    assert "0" in message


def test_unknown_review_persona_lists_the_nine_values(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(OrchestratorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, {**VALID, "review_persona": "reviewer"}, stubs)

    message = str(caught.value)
    assert "reviewer" in message
    for persona in PersonaKey:
        assert persona.value in message


def test_empty_comment_marker_aborts(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(OrchestratorStartupError):
        _build_from(monkeypatch, tmp_path, {**VALID, "comment_marker": ""}, stubs)


# --------------------------------------------------------------------------------------
# (e) 台帳照合（FR-084a・SC-034a）
# --------------------------------------------------------------------------------------


def test_system_actor_known_as_human_aborts(stubs: Stubs) -> None:
    stubs.member_resolver.humans = {"system"}

    with pytest.raises(OrchestratorStartupError) as caught:
        build(stubs.deps(), config=make_config())

    message = str(caught.value)
    assert "system" in message
    assert stubs.log.count("MemberResolver.is_human") == 1


def test_the_ledger_check_reads_no_other_module_config(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """照合は**依存の契約を 1 回叩くだけ**（他モジュールの `config.json` を読まない）。

    契約の呼び出し名だけを見ていたときは、`build()` の中で他モジュールの設定ファイルを
    直接開いても緑だった（変異実測）。**ファイル I/O としても 0 件**であることを測る。
    """
    config = make_config()  # **計測の外**で組む（構築のたびに自分の config.json を開くため）
    opened = _record_opens(monkeypatch)
    opened.clear()

    build(stubs.deps(), config=config)

    assert stubs.log.targets() == ["MemberResolver.is_human"]
    assert opened == []  # 設定を渡した組み立てはファイルを 1 つも開かない（SC-034a・FR-084）


# --------------------------------------------------------------------------------------
# 段の順（最初に失敗した段だけを報告する）
# --------------------------------------------------------------------------------------


def test_only_the_first_failing_stage_is_reported(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stubs.member_resolver.humans = {"system"}

    with pytest.raises(OrchestratorStartupError) as caught:
        _build_from(
            monkeypatch,
            tmp_path,
            {**VALID, "max_returns": 0, "review_persona": "reviewer"},
            stubs,
        )

    message = str(caught.value)
    assert "max_returns" in message
    assert "reviewer" not in message
    assert stubs.log.count("MemberResolver.is_human") == 0


# --------------------------------------------------------------------------------------
# 正常系（contracts §6 の #8）
# --------------------------------------------------------------------------------------


def test_build_returns_a_handle_with_a_runner_and_a_router(stubs: Stubs) -> None:
    handle = build(stubs.deps(), config=make_config())

    runner: KickoffRunner = handle.kickoff_runner  # 憲章 §3: Protocol 型を必ず注釈する
    assert callable(runner.run)
    assert isinstance(handle.router, APIRouter)


def test_build_reads_the_config_file_once_and_the_cycle_reads_nothing(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """実行時のファイル I/O は**組み立て時の 1 回だけ**（FR-085・SC-035）。

    以前は 2 重に空振りしていた（変異実測）: 計測が `Path.open` だけで `builtins.open` を
    素通りし、しかも周回が**課題を 1 件も拾わない**台本だったため、周回中にファイルを
    読んでも緑のままだった。**両方を計測し、実際に 1 課題を処理させて**測る。
    """
    path = _write(tmp_path, VALID)
    _offer_one(stubs)
    opened = _record_opens(monkeypatch)
    _point_at(monkeypatch, path)

    opened.clear()
    handle = build(stubs.deps())
    assert opened.count(str(path)) == 1

    opened.clear()
    result = handle.kickoff_runner.run(make_ctx())

    assert [summary.issue_id for summary in result.issues] == ["bd-1"]  # 空回りでないこと
    assert opened == []  # 周回中のファイル読み書きが 0 件


# --------------------------------------------------------------------------------------
# `.env` を読まない（FR-082）／`config_path()` は呼び出し時に解決（R-08）
# --------------------------------------------------------------------------------------


def test_environment_variables_do_not_override(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("max_returns", "99")
    monkeypatch.setenv("MAX_RETURNS", "99")
    monkeypatch.setenv("comment_marker", "[hacked]")
    _point_at(monkeypatch, _write(tmp_path, VALID))

    settings = OrchestratorConfig()

    assert settings.max_returns == 4
    assert settings.comment_marker == "[orchestrator]"


def test_config_path_is_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`config_path()` を module ロード時に評価しない（差し替えが効く＝ R-08）。"""
    path = _write(tmp_path, {**VALID, "max_returns": 7})
    _point_at(monkeypatch, path)

    assert OrchestratorConfig().max_returns == 7


def test_shipped_config_file_matches_the_five_keys() -> None:
    """出荷時の `config.json` が 5 キーちょうどで、テスト側の直書きと一致する。"""
    payload: dict[str, Any] = json.loads(config_path().read_text(encoding="utf-8"))

    assert payload == VALID


def test_default_config_builds(stubs: Stubs) -> None:
    handle = build(stubs.deps())

    assert isinstance(handle.router, APIRouter)


def test_each_build_gets_its_own_flight_and_records() -> None:
    """`SingleFlight`・`RecordStore` は `build()` が 1 つずつ作る（FR-006 の実装形）。"""
    first = build(make_stubs().deps(), config=make_config())
    second = build(make_stubs().deps(), config=make_config())

    assert first.kickoff_runner is not second.kickoff_runner
