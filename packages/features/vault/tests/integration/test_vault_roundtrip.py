"""`build()` が返す `VaultHandle` を一時 vault に対して通しで動かす（US7・SC-016）。

**実運用の Obsidian vault には触れない**（`conftest.py` の autouse 止め木 ＋
`monkeypatch.setenv("VAULT_ROOT", …)`＝ FR-019・SC-015）。**新しい印（marker）を付けない**
（FR-071）ため、この 1 本もどの環境でも走る（外部ツールを起動しない）。
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from vault_testing import rels_under, write_file

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import FileRef, VaultArea, VaultNotFound, VaultPathViolation
from common.kernel.config import RootConfig
from common.kernel.enums import PersonaKey
from common.kernel.log import CorrelationContext, LoggerFactory
from vault import VaultConfig, VaultDeps, build

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

SECRET_BODY = "SECRET-BODY-TOKEN-DO-NOT-LOG"
"""ログに本文が漏れていないことを見分けるための目印（FR-060）。"""

CORRELATION_ID = "vault-roundtrip-0001"

NOW = datetime(2026, 8, 24, tzinfo=UTC)
"""`FileRef` を手で組むときの `mtime`（再解決されるので値は使われない＝ FR-026）。"""

ALLOWED_EXTRA_KEYS = frozenset(
    {
        "op",
        "area",
        "rel_path",
        "bytes",
        "count",
        "archived_to",
        "tmp_name",
        "elapsed_ms",
        "result",
        "kind",
        "violations",
        "overwritten",
    }
)
"""`service.py`／`repository.py` が `extra` に載せてよい 12 種（contracts/fs-adapter.md §7）。"""


@pytest.fixture
def kickoff_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """1 kickoff 分の一時 vault（ルートを環境変数で差し替える＝ ⑦ が ⑥ に勝つ）。"""
    root = tmp_path / "vault"
    (root / "pj/outcome").mkdir(parents=True)
    (root / "pj/roles/planner").mkdir(parents=True)
    write_file(root, "pj/brief.md", SECRET_BODY)
    write_file(root, "pj/roles/planner/guide.md", "GUIDE")
    monkeypatch.setenv("VAULT_ROOT", str(root))
    return root


@pytest.fixture
def folders_for_kickoff() -> ProjectFolders:
    return ProjectFolders(
        pj_input_dir="pj",
        role_dirs={PersonaKey.planner: "pj/roles/planner"},
        outcome_dir="pj/outcome",
    )


@pytest.fixture
def json_log_stream() -> Iterator[io.StringIO]:
    """ルート設定どおりの構造化ログを**明示の受け皿**へ立て、テスト後にハンドラを元へ戻す。

    `LoggerFactory.configure` は `logging.StreamHandler(sys.stdout)` を作るため、`configure` を
    `redirect_stdout` の中で呼ぶと以後の出力がこの受け皿に入る。pytest の捕捉層（`capsys` と
    fd 捕捉）に依存しないので、束縛の順序で取りこぼさない。
    """
    buffer = io.StringIO()
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level
    with contextlib.redirect_stdout(buffer):
        LoggerFactory.configure(RootConfig())
    yield buffer
    root_logger.handlers = saved_handlers
    root_logger.setLevel(saved_level)


@dataclass(frozen=True, slots=True)
class _Line:
    """構造化ログ 1 行のうち、本テストが見る 4 つ。

    ⚠ `module` は**ロガ名**（`vault.service` / `vault.repository`）であって、`_logger.warning`
    の第 1 引数（`vault.not_found` など）は `event` に載る。取り違えると絞り込みが黙って
    空になる（T097 で 1 度踏んだ）。
    """

    module: str
    event: str
    correlation_id: str
    extra: dict[str, object]


def _vault_lines(captured: str) -> list[_Line]:
    """標準出力の JSON 1 行ログのうち vault のものだけを拾う。"""
    lines: list[_Line] = []
    for raw in captured.splitlines():
        if not raw.startswith("{"):
            continue
        record = cast("dict[str, object]", json.loads(raw))
        module = str(record["module"])
        if not module.startswith("vault."):
            continue
        extra = record["extra"]
        lines.append(
            _Line(
                module=module,
                event=str(record["event"]),
                correlation_id=str(record["correlation_id"]),
                extra=cast("dict[str, object]", extra) if isinstance(extra, dict) else {},
            )
        )
    return lines


def test_one_kickoff_through_the_six_methods(
    kickoff_vault: Path,
    folders_for_kickoff: ProjectFolders,
    json_log_stream: io.StringIO,
) -> None:
    """6 メソッドを 1 kickoff の順で通し、ログと「vault の外に作らない」を確かめる。"""
    before = rels_under(kickoff_vault)
    handle = build(VaultDeps(), config=VaultConfig(max_outcome_bytes=64))
    reader = handle.vault_reader
    writer = handle.outcome_writer
    folders = folders_for_kickoff

    token = CorrelationContext.bind(CORRELATION_ID)
    try:
        # (1) フォルダの確認 → (2) 一覧 → (3) 入力の読取
        assert reader.check_project_folders(folders).ok is True
        listed = reader.list_inputs(folders, PersonaKey.planner)
        assert [ref.rel_path for ref in listed] == [
            "pj/brief.md",
            "pj/roles/planner/guide.md",
        ]
        assert reader.read_input(folders, listed[0]) == SECRET_BODY

        # (4) 完了ゲート → (5) 書込（新規 → 上書きで退避）→ (6) 成果物の読取
        assert reader.outcome_exists(folders, "result.md") is False
        first = writer.write_outcome(folders, "result.md", "FIRST")
        assert first.overwritten is False
        assert first.archived_to is None
        second = writer.write_outcome(folders, "result.md", "SECOND")
        assert second.overwritten is True
        archived = str(second.archived_to)
        assert archived.startswith("pj/outcome/_archive/result.md.")
        assert reader.outcome_exists(folders, "result.md") is True
        assert reader.read_outcome_file(folders, "result.md") == "SECOND"

        # `config=` で絞った上限が効く（FR-057）
        with pytest.raises(VaultPathViolation) as exc:
            writer.write_outcome(folders, "big.md", "x" * 65)
        assert exc.value.kind == "too_large"
    finally:
        CorrelationContext.reset(token)

    captured = json_log_stream.getvalue()
    lines = _vault_lines(captured)

    # (a) 操作ごとに 1 件以上記録される
    operations = {str(line.extra["op"]) for line in lines}
    assert {"check", "list", "read", "exists", "write", "archive"} <= operations

    # (b) 相関 ID を束縛した文脈では**全行に載る**
    assert {line.correlation_id for line in lines} == {CORRELATION_ID}

    # (c) ファイルの本文が 1 行も現れない・vault ルートの絶対パスも載せない（FR-060）
    assert SECRET_BODY not in captured
    assert str(kickoff_vault) not in captured

    # (d) 退避元と退避先が同じ行に残る
    archive_lines = [line for line in lines if line.extra.get("op") == "archive"]
    assert archive_lines
    assert archive_lines[0].extra["rel_path"] == "pj/outcome/result.md"
    assert archive_lines[0].extra["archived_to"] == archived

    # SC-016: 成果物と退避先以外のファイルが 1 件も増えていない（一時ファイルも残らない）
    added = rels_under(kickoff_vault) - before
    assert added == {"pj/outcome/result.md", "pj/outcome/_archive", archived}


def test_every_rejection_leaves_a_log_line(
    kickoff_vault: Path,
    folders_for_kickoff: ProjectFolders,
    json_log_stream: io.StringIO,
) -> None:
    """FR-059: **違反と不在のすべて**が構造化ログに 1 行以上残る（T085）。

    `/speckit-converge` 第 5 回の実測では、`empty`・`too_large`（内容と入力）・`not_text` と
    すべての `VaultNotFound` 経路が 1 行も残さなかった。ログが出るのは `name_gate` と
    `resolve` の 2 経路だけで、**`read_input` が `not_text` で落ちた 1 回の操作は journal に
    痕跡が無かった**。ここで 8 経路を送出させ、経路ごとに 1 行以上出ることを固定する。
    """
    (kickoff_vault / "pj/bin.md").write_bytes(b"\xff\xfe\xfa")
    write_file(kickoff_vault, "pj/big.md", "x" * 200)
    handle = build(VaultDeps(), config=VaultConfig(max_outcome_bytes=100))
    reader = handle.vault_reader
    writer = handle.outcome_writer
    folders = folders_for_kickoff
    absent_outcome = folders.model_copy(update={"outcome_dir": "pj/nope"})

    def _ref(rel: str, area: VaultArea) -> FileRef:
        return FileRef(
            area=area, rel_path=rel, name=rel.rsplit("/", 1)[-1], size_bytes=1, mtime=NOW
        )

    cases: list[tuple[str, Callable[[], object], type[Exception]]] = [
        ("write/empty", lambda: writer.write_outcome(folders, "o.md", ""), VaultPathViolation),
        (
            "write/too_large",
            lambda: writer.write_outcome(folders, "o.md", "x" * 101),
            VaultPathViolation,
        ),
        (
            "write/outcome_dir_absent",
            lambda: writer.write_outcome(absent_outcome, "o.md", "BODY"),
            VaultNotFound,
        ),
        (
            "read_input/too_large",
            lambda: reader.read_input(folders, _ref("pj/big.md", VaultArea.pj_input)),
            VaultPathViolation,
        ),
        (
            "read_input/not_text",
            lambda: reader.read_input(folders, _ref("pj/bin.md", VaultArea.pj_input)),
            VaultPathViolation,
        ),
        (
            "read_input/absent",
            lambda: reader.read_input(folders, _ref("pj/nope.md", VaultArea.pj_input)),
            VaultNotFound,
        ),
        (
            "read_outcome_file/absent",
            lambda: reader.read_outcome_file(folders, "nope.md"),
            VaultNotFound,
        ),
        (
            "list_inputs/role_not_declared",
            lambda: reader.list_inputs(folders, PersonaKey.writer),
            VaultNotFound,
        ),
    ]

    token = CorrelationContext.bind(CORRELATION_ID)
    try:
        for label, call, expected in cases:
            before = len(_vault_lines(json_log_stream.getvalue()))
            with pytest.raises(expected):
                call()
            after = _vault_lines(json_log_stream.getvalue())
            assert len(after) > before, f"{label} がログを 1 行も残していない（FR-059）"
            assert after[-1].correlation_id == CORRELATION_ID, label
    finally:
        CorrelationContext.reset(token)

    captured = json_log_stream.getvalue()
    # 本文は 1 行も現れない（FR-060）。`not_text` の detail は文字コード名と位置だけを載せる
    assert SECRET_BODY not in captured
    assert str(kickoff_vault) not in captured
    # `extra` のキーは contracts/fs-adapter.md §7 の 12 種に収まる
    assert {key for line in _vault_lines(captured) for key in line.extra} <= ALLOWED_EXTRA_KEYS
    # 不在の行の `result` は **`not_found` で始まる**（VD-42）。役割未宣言の経路だけが
    # `rel_path` に載せられない識別子を `"not_found:<役割>"` として畳むため、見分けの印は
    # 完全一致ではなく前方一致で残る。ここを緩めると §7 の 12 種を増やさずに値を足せてしまう
    not_found_results = [
        line.extra["result"] for line in _vault_lines(captured) if line.event == "vault.not_found"
    ]
    assert not_found_results, "不在の行が 1 つも出ていない（8 経路のうち 4 経路が not_found）"
    assert all(str(value).startswith("not_found") for value in not_found_results), (
        f"`result` が印を失っている: {not_found_results}"
    )


def test_reader_and_writer_are_one_instance_through_build(
    kickoff_vault: Path,
) -> None:
    """FR-005: 実際に `build()` を通しても同一インスタンスである。"""
    del kickoff_vault
    handle = build(VaultDeps())
    assert handle.vault_reader is handle.outcome_writer
