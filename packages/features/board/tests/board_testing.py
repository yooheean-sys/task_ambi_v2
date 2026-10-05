"""board のテスト共通部品（`StubRunner`・偽 `subprocess.run`・応答の組み立て補助）。

`StubRunner` は `BdRunner` を構造的に満たすスタブで、**`subprocess` を 1 度も起動しない**
（FR-060・R-05）。「受け取った `BdCommand` の記録」と「返す `BdResult` の台本」を持ち、
plan (ii) の表の argv を期待値として直接検査できる。

**`conftest.py` に置かない**: `tests/unit/` と `tests/integration/` の双方に `conftest.py` が
あり、いずれも top-level モジュール名 `conftest` として読み込まれるため、`from conftest import …`
は先に読まれた方に解決してしまう（実測）。一意な名前のモジュールに置いて曖昧さを消す。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from board.exceptions import BdCommandError
from board.repository import BdResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from board.repository import BdCommand

FAKE_BD_BIN = "/opt/bd/bin/bd"
"""台本が返す argv の先頭（build 時に解決した絶対パスの代役。R2-7）。"""

BD_BIN = FAKE_BD_BIN
"""`BdCli` に渡す解決済みの `bd` 絶対パス。"""

BEADS_DIR = Path("/srv/ambi/ws/.beads")
"""`BdCli` に渡す `.beads` ディレクトリ自身の絶対パス（D19-a）。"""


class FakeRun:
    """`subprocess.run` の代役。呼び出しを記録し、台本の応答を返す。

    `BdCli` の内部（argv 組立・env 注入・ロック待ち）を検査するためだけに使う。
    **実行ファイルは起動しない**ので `requires_bd` は付けない（plan (viii)）。
    """

    def __init__(self, *script: subprocess.CompletedProcess[str] | BaseException) -> None:
        """成功応答と送出する例外を混ぜた台本を受け取る。"""
        self.script: list[subprocess.CompletedProcess[str] | BaseException] = list(script)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """呼び出しを記録し、台本の次の 1 つを返す（例外なら送出する）。"""
        self.calls.append({"argv": list(argv), **kwargs})
        if not self.script:
            msg = f"台本を使い切ったのに subprocess が呼ばれた: {list(argv)}"
            raise AssertionError(msg)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def completed(
    stdout: str = "[]", *, rc: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` の戻りを組み立てる。"""
    return subprocess.CompletedProcess(args=["bd"], returncode=rc, stdout=stdout, stderr=stderr)


def log_extra(record: logging.LogRecord, key: str) -> object:
    """`extra` で足した項目を取り出す（`LogRecord` の属性は静的に見えないため `__dict__` から）。"""
    return record.__dict__[key]


def log_argv(record: logging.LogRecord) -> list[str]:
    """ログレコードの `argv`（実値）を取り出す。"""
    return cast("list[str]", log_extra(record, "argv"))


class StubRunner:
    """`BdRunner` を満たすスタブ。台本を先頭から 1 つずつ返す。"""

    def __init__(self, *script: BdResult | BdCommandError) -> None:
        """成功応答と送出する例外を混ぜた台本を受け取る。"""
        self._script: list[BdResult | BdCommandError] = list(script)
        self.commands: list[BdCommand] = []

    def run(self, command: BdCommand) -> BdResult:
        """依頼を記録し、台本の次の 1 つを返す（例外なら送出する）。"""
        self.commands.append(command)
        if not self._script:
            msg = f"台本を使い切ったのに bd が呼ばれた: {command.argv}"
            raise AssertionError(msg)
        item = self._script.pop(0)
        if isinstance(item, BdCommandError):
            raise item
        return item

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        """記録した依頼の argv だけを取り出す。"""
        return [command.argv for command in self.commands]

    @property
    def calls(self) -> int:
        """bd を起動した回数（＝依頼の件数）。"""
        return len(self.commands)

    @property
    def remaining(self) -> int:
        """台本の残り。0 でないと「打つはずの手を打っていない」。"""
        return len(self._script)


def bd_ok(
    payload: object, *, attempts: int = 1, argv: tuple[str, ...] = (FAKE_BD_BIN,)
) -> BdResult:
    """`rc == 0` かつ機械可読出力が読めた応答を組み立てる。"""
    return BdResult(
        rc=0,
        stdout_head="",
        stderr_head="",
        attempts=attempts,
        elapsed_sec=0.01,
        json=payload,
        argv=argv,
        safe_argv=argv,
    )


def issue_payload(**overrides: Any) -> dict[str, object]:
    """`bd show` / `list` / `ready` の 1 要素（実測のキー名。contracts/json-mapping.md §2）。"""
    payload: dict[str, object] = {
        "id": "pj-1",
        "title": "表題",
        "description": "本文",
        "status": "open",
        "labels": ["kind:child", "project:pj"],
        "parent": None,
        "assignee": None,
        "acceptance_criteria": "- [ ] done",
        "dependencies": [],
        "created_at": "2026-08-21T00:00:00Z",
        "updated_at": "2026-08-21T00:00:00Z",
        "priority": 2,
        "schema_version": 1,
    }
    payload.update(overrides)
    return payload


def issue_ref_payload(**overrides: Any) -> dict[str, object]:
    """`Issue.dependencies` / `bd dep list` の 1 要素（contracts/json-mapping.md §3）。"""
    payload: dict[str, object] = {
        "id": "pj-2",
        "title": "依存先",
        "status": "open",
        "labels": [],
        "dependency_type": "blocks",
    }
    payload.update(overrides)
    return payload


def comment_payload(**overrides: Any) -> dict[str, object]:
    """`bd comments` の 1 要素（contracts/json-mapping.md §4）。"""
    payload: dict[str, object] = {
        "id": "5f0d1c9e-0000-4000-8000-000000000001",
        "issue_id": "pj-1",
        "author": "demo_pj.programmer",
        "text": "本文",
        "created_at": "2026-08-21T00:00:00Z",
    }
    payload.update(overrides)
    return payload
