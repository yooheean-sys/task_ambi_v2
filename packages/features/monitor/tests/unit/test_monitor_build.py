"""US4: 組み立てと起動中断（monitor-F12・contracts/monitor-config.md §5）。quickstart S9。

**黙って別の方式に落とさない**（FR-065）。壊れた設定は**見に行った絶対パス**を示して起動を止める。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import (
    DEFAULT_GRADE_MAP,
    TRUNCATION_PHRASE,
    StubBoardReader,
    StubBoardWriter,
    chat_payload,
    make_config,
    make_linked_board,
    make_outcome,
    make_request,
    make_state,
    without_config_file,
)
from pydantic import ValidationError

import monitor as monitor_package
from common.interfaces.monitor import LLMHttpHook, RunMonitor
from monitor import MonitorConfig, MonitorDeps, MonitorStartupError, build, config_path

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter


def _deps(
    reader: StubBoardReader | None = None, writer: StubBoardWriter | None = None
) -> MonitorDeps:
    board_reader: BoardReader = StubBoardReader() if reader is None else reader
    board_writer: BoardWriter = StubBoardWriter() if writer is None else writer
    return MonitorDeps(board_reader=board_reader, board_writer=board_writer)


def _write_config(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _build_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict[str, object]
) -> None:
    """`config.json` の位置を差し替えて `build()` を呼ぶ（実ファイルを読まない）。"""
    path = _write_config(tmp_path, payload)
    monkeypatch.setattr(monitor_package, "config_path", lambda: path)
    build(_deps())


def _valid() -> dict[str, object]:
    return {
        "grade_map": dict(DEFAULT_GRADE_MAP),
        "envelope_marker": "[monitor]",
        "token_estimator": "chars",
        "token_encoding": "cl100k_base",
        "cost_field_path": "usage.cost",
        "truncation_phrases": [],
    }


# --------------------------------------------------------------------------------------
# 起動中断の 4 条件 ＋ tiktoken 未導入
# --------------------------------------------------------------------------------------


def test_grade_map_value_outside_fwi_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """① `grade_map` の値が `F`／`W`／`I` 以外なら起動を止める。"""
    payload = _valid()
    payload["grade_map"] = dict(DEFAULT_GRADE_MAP) | {"no_output": "X"}
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    message = str(caught.value)
    assert str(tmp_path / "config.json") in message
    assert "no_output" in message
    assert "F" in message and "W" in message and "I" in message


def test_unknown_grade_map_key_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """② `grade_map` のキーが `FailureKind` の 12 種に無いなら起動を止める。"""
    payload = _valid()
    payload["grade_map"] = dict(DEFAULT_GRADE_MAP) | {"unknown_kind": "F"}
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    assert str(tmp_path / "config.json") in str(caught.value)
    assert "unknown_kind" in str(caught.value)


def test_missing_required_key_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """必須キーが欠けていれば起動を止める（FR-062・SC-013 の「必須値欠け」）。

    6 フィールドはフィールド既定を持たない。既定を置くと `ValidationError` が起きず、
    契約 §5 の段 (a) が**到達不能**になる（黙って `grade_map={}` で起動し、全 12 種が
    `_grade_of()` の F 倒しに落ちる無症状の劣化になる）。
    """
    payload = _valid()
    del payload["envelope_marker"]
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    message = str(caught.value)
    assert str(tmp_path / "config.json") in message
    assert "envelope_marker" in message


def test_unknown_top_level_key_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """③ 未知のトップレベルキーがあれば起動を止める（`extra="forbid"`）。"""
    payload = _valid()
    payload["surprise"] = 1
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    assert str(tmp_path / "config.json") in str(caught.value)


def test_wrong_type_stops_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """④ 必須値の型が合わなければ起動を止める。"""
    payload = _valid()
    payload["truncation_phrases"] = "文字列"
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    assert str(tmp_path / "config.json") in str(caught.value)


def test_tiktoken_without_the_package_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⑤ `token_estimator: "tiktoken"` で未導入なら起動を止める（**黙って落とさない**）。

    `tiktoken` は本 feature の裁定で**依存に加えない**（SD-14）ため、この環境では実際に未導入
    である（`importlib.util.find_spec("tiktoken") is None` を実測）。偽装を挟まない。
    """
    payload = _valid()
    payload["token_estimator"] = "tiktoken"
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    assert str(tmp_path / "config.json") in str(caught.value)
    assert "tiktoken" in str(caught.value)


def test_unknown_estimator_stops_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """⑥ `chars`／`tiktoken` 以外は起動中断（**黙って `chars` に落とさない**＝ FR-065）。"""
    payload = _valid()
    payload["token_estimator"] = "guess"
    with pytest.raises(MonitorStartupError) as caught:
        _build_from(monkeypatch, tmp_path, payload)
    assert "guess" in str(caught.value)
    assert "chars" in str(caught.value)


def test_missing_grade_map_key_still_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⑦ `grade_map` のキーが欠けているだけでは起動する（FR-061b）。"""
    payload = _valid()
    payload["grade_map"] = {
        key: value for key, value in DEFAULT_GRADE_MAP.items() if key != "no_output"
    }
    _build_from(monkeypatch, tmp_path, payload)  # 例外が飛ばないことが期待


def test_default_config_json_starts() -> None:
    """出荷する `config.json` はそのまま起動する（12 キー・6 項目）。"""
    handle = build(_deps())
    assert handle.router is None
    assert config_path().exists()


# --------------------------------------------------------------------------------------
# 環境変数で上書きできない（FR-063・US4 AC-5）
# --------------------------------------------------------------------------------------


def test_environment_variables_do_not_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """⑧ `MONITOR_ENVELOPE_MARKER` を置いても効かない。"""
    monkeypatch.setenv("MONITOR_ENVELOPE_MARKER", "[hacked]")
    monkeypatch.setenv("ENVELOPE_MARKER", "[hacked]")
    assert MonitorConfig().envelope_marker == "[monitor]"


def test_dotenv_is_not_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`.env` も読まない（ソースを 2 つに絞っている＝ FR-010）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("envelope_marker=[dotenv]\n", encoding="utf-8")
    assert MonitorConfig().envelope_marker == "[monitor]"


# --------------------------------------------------------------------------------------
# 戻りの形と、設定だけで写像経路が変わること（US4 AC-1）
# --------------------------------------------------------------------------------------


def test_handle_holds_two_distinct_instances() -> None:
    """⑩ `run_monitor` と `llm_http_hook` は**別インスタンス**（stages の「同一」とは違う）。"""
    handle = build(_deps())
    run_monitor: RunMonitor = handle.run_monitor
    llm_http_hook: LLMHttpHook = handle.llm_http_hook
    assert run_monitor is not llm_http_hook
    assert handle.router is None


def test_config_argument_replaces_the_file() -> None:
    """`build(deps, config=...)` は `config.json` を読まずに組む（contracts §6）。"""
    handle = build(_deps(), config=make_config(envelope_marker="[test]"))
    handle.run_monitor.begin("k-1", make_state().limits, 1000)
    verdict = handle.run_monitor.finish("k-1", "i-1", "bot", make_outcome())
    assert verdict.envelope.startswith("[test] ")


@pytest.mark.parametrize(
    ("grade", "expected_calls"),
    [("W", ["comment", "close"]), ("F", ["comment", "set_labels", "release"])],
)
def test_grade_map_alone_changes_the_mapping_route(grade: str, expected_calls: list[str]) -> None:
    """⑨ `truncated_answer` を W から F に変えると写像経路が変わる（コードに写しが無い）。"""
    grade_map = dict(DEFAULT_GRADE_MAP) | {"truncated_answer": grade}
    config = make_config(grade_map=grade_map)
    reader, writer = make_linked_board(labels=("state:working",))
    handle = build(MonitorDeps(board_reader=reader, board_writer=writer), config=config)
    hook = handle.llm_http_hook
    handle.run_monitor.begin("k-1", make_state().limits, 1000)
    hook.on_outbound(
        make_request(chat_payload(messages=[{"role": "assistant", "content": TRUNCATION_PHRASE}]))
    )
    handle.run_monitor.finish("k-1", "i-1", "bot", make_outcome())
    assert writer.names == expected_calls


def test_config_path_is_cwd_independent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`config_path()` は cwd に依存しない（憲章 §5）。"""
    expected = config_path()
    monkeypatch.chdir(tmp_path)
    assert config_path() == expected
    assert expected.name == "config.json"
    assert expected.parent.name == "monitor"


def test_without_config_file_helper_yields_an_empty_source() -> None:
    """テストヘルパが実ファイルを読まないこと自体の確認。

    6 キーが必須なので、JSON ソースが空なら素の `MonitorConfig()` は 6 件そろって
    欠落を訴える。実ファイルを読んでしまっていれば 6 キーが揃って通る。
    """
    with without_config_file(), pytest.raises(ValidationError) as caught:
        MonitorConfig()
    missing = {error["loc"][0] for error in caught.value.errors()}
    assert missing == {
        "grade_map",
        "envelope_marker",
        "token_estimator",
        "token_encoding",
        "cost_field_path",
        "truncation_phrases",
    }
