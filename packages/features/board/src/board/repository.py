"""`bd` CLI の実行アダプタ（board-F01）。契約は `specs/003-board/contracts/bd-cli.md`。

board は beads の**唯一のアダプタ**であり、内部ストア（embedded Dolt）を直接読み書きしない
（読取を含む。憲章 §2・FR-009）。本ファイルは契約例外を送出せず、`BdCommandError` だけを上げる
（翻訳は `service.py` の単一関数が担う＝ FR-041）。
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404  外部ツールは公約インターフェース（CLI）経由でのみ触る（憲章 §2）
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from board.exceptions import BdCommandError, BdErrorKind
from common.kernel.log import LoggerFactory

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from board import BoardConfig

WHITELIST: frozenset[str] = frozenset(
    {"ready", "show", "list", "comments", "create", "update", "close", "comment", "dep"}
)
"""実行してよいサブコマンド 9 種（FR-010・SC-006）。

**`init` を含めない**: `bd init` は `.beads/` だけでなく git リポジトリを作り commit まで行う
（M-10・D19-d）。
**`set-state` を含めない**: `state` 次元の排他は保証するが event bead を作る副作用がある（M-11）。
排他は `service.py` の `set_labels` が保証する（FR-033）。
"""

OUTPUT_HEAD_CHARS = 500
"""標準出力／標準エラー出力を載せる先頭文字数（R-09。ログと例外で同じ長さ）。"""

ARGV_ELEMENT_CHARS = 200
"""argv の 1 要素を切り詰める文字数（R-09。呼び手が盤面へ書き出す経路があるため）。"""

FREE_TEXT_FLAGS: tuple[str, ...] = ("--title", "-d", "--acceptance", "-r")
"""**例外の `argv`** で値をプレースホルダに置換するフラグ（R-09）。

`--` の後ろは位置に依らず常に本文とみなす。**ログの `argv` は実値**であり、こちらとは別物である
（`RedactFilter` は `logging` のフィルタで例外の属性には掛からない＝ contracts/bd-cli.md §6 の ⚠）。
"""

ARGV_SEPARATOR = "--"
"""`bd comment` の本文を隔てる区切り（M-28）。"""

BODY_PLACEHOLDER_LABEL = "body"
"""`--` の後ろの本文に使うプレースホルダの名前。"""

RC_TIMEOUT = -1
"""タイムアウトで打ち切ったときの終了コード（R-08。bd は失敗時つねに rc=1 なので衝突しない）。"""

RC_NOT_SPAWNED = -2
"""実行ファイルを起動できなかったときの終了コード（R-08）。"""

NOT_FOUND_HINTS: tuple[str, ...] = ("no issue found matching", "no issues found matching")
"""`not_found` の判定文言（R-10・M-06）。コード定数（設定値にしない＝ plan C-2）。"""

UNAVAILABLE_HINTS: tuple[str, ...] = ("no beads database found", "unknown command")
"""`unavailable` の判定文言（R-10・M-06）。`unknown command` はホワイトリストの設計不良を指す。"""

_LOGGER_NAME = "board.repository"
"""共通ログ基盤へ出すロガ名（P5D-006）。board 専用の記録ファイルは作らない。"""


@dataclass(frozen=True, slots=True)
class BdCommand:
    """`bd` への 1 依頼（data-model §4）。

    `argv` は **bd バイナリを含まない**。`--json` と `--` の位置まで含めて `service.py` が完成させる
    （`BdCli` は先頭にバイナリを足すだけ＝ `--` の後ろにフラグを足す誤りを構造的に起こせなくする）。
    """

    argv: tuple[str, ...]
    actor: str | None = None
    write: bool = False
    issue_id: str | None = None


@dataclass(frozen=True, slots=True)
class BdResult:
    """`bd` の 1 応答（data-model §4）。

    `BoardService` に返るのは **`rc == 0` かつ `json is not None`** のときだけである
    （それ以外は `BdCommandError` になる＝ FR-014・FR-034）。
    """

    rc: int
    stdout_head: str
    stderr_head: str
    attempts: int
    elapsed_sec: float
    json: object | None
    argv: tuple[str, ...]
    safe_argv: tuple[str, ...]


class BdRunner(Protocol):
    """`bd` の実行口（差し替え点。R-05）。単体テストはこの Protocol を満たすスタブを注入する。"""

    def run(self, command: BdCommand) -> BdResult:
        """1 依頼を実行して応答を返す。

        Raises:
            BdCommandError: 失敗（種別つき）。ロック待ちの再実行を尽くした後に上げる。
        """
        ...


def truncate(text: str, limit: int) -> str:
    """`limit` 文字を超える部分を落とす（`…` などは足さない＝比較可能性を保つ）。"""
    return text[:limit]


def classify(stderr: str, stdout: str, lock_hints: Sequence[str]) -> BdErrorKind:
    """失敗の種別を**文言**で判定する（FR-041a・R-10）。

    終了コードでは分類できない（bd はあらゆる失敗を rc=1 で返す＝ M-06）。判定漏れは `other` に
    倒れ、**再実行されず** `BoardUnavailable` になる（安全側）。

    ⚠ ロック競合には **`lock_exhausted` を返す**。`BdErrorKind` は 4 値だけで（正本＝
    `p5_board_data_model.mmd`）「競合したが再実行の余地がある」状態に別の名前が無いためである
    （plan (iii) の擬似コードは存在しない `BdErrorKind.lock` を参照している）。この種別が
    **実際に呼び手へ届くのは上限を使い切ったときだけ**で、それまでは `BdCli` が再実行に使う。

    Args:
        stderr: 標準エラー出力（先頭 `OUTPUT_HEAD_CHARS` 文字）。
        stdout: 標準出力（同上）。`bd show --json` の not-found は stdout に来る（M-06）。
        lock_hints: ロック競合と見なす文言（CFG-25）。

    Returns:
        判定した種別。
    """
    joined = f"{stderr}\n{stdout}".lower()
    if any(hint.lower() in joined for hint in lock_hints if hint):
        return BdErrorKind.lock_exhausted
    if any(hint in joined for hint in NOT_FOUND_HINTS):
        return BdErrorKind.not_found
    if any(hint in joined for hint in UNAVAILABLE_HINTS):
        return BdErrorKind.unavailable
    return BdErrorKind.other


def stderr_head_of(stderr_head: str, stdout_head: str) -> str:
    """例外へ載せる標準エラー出力を選ぶ（plan (v)「`BoardUnavailable` の 4 属性」）。

    `bd show --json` の not-found は **stdout** に誤り記述を返す（M-06）。stderr が空のときは
    `"stdout: "` を接頭辞にして stdout の先頭 `OUTPUT_HEAD_CHARS` 文字を載せる。

    Args:
        stderr_head: 標準エラー出力の先頭。
        stdout_head: 標準出力の先頭。

    Returns:
        例外の `stderr_head` に載せる文字列。
    """
    if stderr_head:
        return truncate(stderr_head, OUTPUT_HEAD_CHARS)
    if stdout_head:
        return "stdout: " + truncate(stdout_head, OUTPUT_HEAD_CHARS)
    return ""


def _placeholder(label: str, value: str) -> str:
    """`<title:42chars>` の形（元の長さだけを残す）。"""
    return f"<{label}:{len(value)}chars>"


def safe_argv_of(argv: Sequence[str]) -> tuple[str, ...]:
    """**例外用**の argv を作る（自由文字列の値をプレースホルダへ置換する。R-09）。

    `FREE_TEXT_FLAGS` の直後の値と `--` の後ろの本文を `<title:42chars>` の形に置き換え、
    残りは `ARGV_ELEMENT_CHARS` 文字で切り詰める。完全な argv の再現は**ログ側で行う**。

    Args:
        argv: 実行した argv の全体（bd バイナリを含む）。

    Returns:
        置換済みの argv。
    """
    safe: list[str] = []
    after_separator = False
    pending_label: str | None = None
    for element in argv:
        if after_separator:
            safe.append(_placeholder(BODY_PLACEHOLDER_LABEL, element))
            continue
        if pending_label is not None:
            safe.append(_placeholder(pending_label, element))
            pending_label = None
            continue
        if element == ARGV_SEPARATOR:
            after_separator = True
            safe.append(element)
            continue
        if element in FREE_TEXT_FLAGS:
            pending_label = element.lstrip("-")
        safe.append(truncate(element, ARGV_ELEMENT_CHARS))
    return tuple(safe)


def log_argv_of(argv: Sequence[str]) -> tuple[str, ...]:
    """**ログ用**の argv を作る（実値のまま要素ごとに切り詰める。伏字は `RedactFilter`）。"""
    return tuple(truncate(element, ARGV_ELEMENT_CHARS) for element in argv)


class BdCli:
    """`subprocess` で `bd` を 1 回実行する実装（`BdRunner` の唯一の実体）。

    **1 インスタンスだけ**が `build()` で作られ、その中の `threading.Lock` が唯一の直列化点になる
    （R-07）。回数・間隔・判定文言はすべて `BoardConfig` から読む（CFG-22〜25）。
    """

    def __init__(self, bd_bin: str, beads_dir: Path, config: BoardConfig) -> None:
        """解決済みの `bd` 絶対パス・`.beads` の絶対パス・挙動設定を保持する。"""
        self._bd_bin = bd_bin
        self._beads_dir = beads_dir
        self._timeout_sec = config.timeout_sec
        self._max_attempts = config.lock_wait_max_attempts
        self._wait_base_sec = config.lock_wait_base_sec
        self._lock_hints = tuple(config.lock_hints)
        self._write_lock = threading.Lock()
        self._logger = LoggerFactory.get_logger(_LOGGER_NAME)

    def run(self, command: BdCommand) -> BdResult:
        """1 依頼を実行する（ロック待ちの再実行を含む）。

        Args:
            command: 実行する依頼。`argv[0]` は `WHITELIST` の要素でなければならない。

        Returns:
            `rc == 0` かつ機械可読出力が読めたときの応答。

        Raises:
            BdCommandError: ホワイトリスト外・失敗・ロック上限超過。
        """
        self._reject_outside_whitelist(command)
        lock = self._write_lock if command.write else nullcontext()
        with lock:
            return self._run_with_lock_retry(command)

    # ------------------------------------------------------------------ 内部

    def _reject_outside_whitelist(self, command: BdCommand) -> None:
        """`BdCommand` を受け取った時点で検査し、**bd を起動する前に**失敗させる（FR-010）。"""
        subcommand = command.argv[0] if command.argv else ""
        if subcommand in WHITELIST:
            return
        argv = self._full_argv(command)
        msg = (
            f"ホワイトリスト外のサブコマンドは実行しない: {subcommand!r}"
            f" / 実行してよいのは {sorted(WHITELIST)}"
        )
        raise BdCommandError(
            msg, kind=BdErrorKind.other, argv=safe_argv_of(argv), rc=RC_NOT_SPAWNED, stderr=msg
        )

    def _full_argv(self, command: BdCommand) -> tuple[str, ...]:
        """先頭に解決済みの `bd` 絶対パスを足すだけ（R2-7。並びは触らない）。"""
        return (self._bd_bin, *command.argv)

    def _environment(self, command: BdCommand) -> dict[str, str]:
        """`BEADS_DIR` は常に、`BEADS_ACTOR` は書込のときだけ注入する（FR-012・D19-c）。"""
        env = {**os.environ, "BEADS_DIR": str(self._beads_dir)}
        if command.actor is not None:
            env["BEADS_ACTOR"] = command.actor
        return env

    def _run_with_lock_retry(self, command: BdCommand) -> BdResult:
        """試行ループ。**ロック競合のときだけ**待って同じ呼び出しをやり直す（R5-3〜R5-5）。"""
        for attempt in range(1, self._max_attempts + 1):
            result = self._run_once(command, attempt)
            if result.rc == 0 and result.json is not None:
                return result
            kind = self._kind_of(result)
            if kind is not BdErrorKind.lock_exhausted or attempt == self._max_attempts:
                raise self._failure(command, result, kind)
            time.sleep(self._wait_base_sec * attempt)
        msg = "試行ループが結果を返さずに終わった（lock_wait_max_attempts が 1 未満）"
        raise BdCommandError(
            msg,
            kind=BdErrorKind.other,
            argv=safe_argv_of(self._full_argv(command)),
            rc=RC_NOT_SPAWNED,
            stderr=msg,
        )

    def _kind_of(self, result: BdResult) -> BdErrorKind:
        """タイムアウト・spawn 失敗・非 JSON は文言判定を通さず `unavailable`（FR-014）。"""
        if result.rc in (RC_TIMEOUT, RC_NOT_SPAWNED):
            return BdErrorKind.unavailable
        if result.rc == 0 and result.json is None:
            return BdErrorKind.unavailable
        return classify(result.stderr_head, result.stdout_head, self._lock_hints)

    def _failure(self, command: BdCommand, result: BdResult, kind: BdErrorKind) -> BdCommandError:
        """`BdResult` から内部例外を組む（`argv` は**伏せた形**＝ R-09）。"""
        stderr = stderr_head_of(result.stderr_head, result.stdout_head)
        subcommand = command.argv[0] if command.argv else ""
        return BdCommandError(
            f"bd {subcommand} が失敗した（rc={result.rc}・試行 {result.attempts} 回）: {stderr}",
            kind=kind,
            argv=result.safe_argv,
            rc=result.rc,
            stderr=stderr,
            attempts=result.attempts,
        )

    def _run_once(self, command: BdCommand, attempt: int) -> BdResult:
        """`bd` を 1 回だけ起動し、**試行 1 回につき 1 レコード**をログへ出す（P5D-006）。"""
        argv = self._full_argv(command)
        started = time.monotonic()
        rc, stdout, stderr = self._spawn(argv, command)
        elapsed_sec = time.monotonic() - started
        stdout_head = truncate(stdout, OUTPUT_HEAD_CHARS)
        stderr_head = truncate(stderr, OUTPUT_HEAD_CHARS)
        payload = self._parse_json(stdout) if rc == 0 else None
        result = BdResult(
            rc=rc,
            stdout_head=stdout_head,
            stderr_head=stderr_head,
            attempts=attempt,
            elapsed_sec=elapsed_sec,
            json=payload,
            argv=log_argv_of(argv),
            safe_argv=safe_argv_of(argv),
        )
        self._log(command, result)
        return result

    def _spawn(self, argv: tuple[str, ...], command: BdCommand) -> tuple[int, str, str]:
        """`shell=False` でリストのまま起動する（FR-011）。センチネルで打ち切りを表す（R-08）。"""
        try:
            completed = subprocess.run(  # noqa: S603  argv は WHITELIST 検査済み・shell を経由しない
                list(argv),
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
                cwd=str(self._beads_dir.parent),
                env=self._environment(command),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RC_TIMEOUT, "", f"timeout: {self._timeout_sec} 秒を超えたので打ち切った"
        except OSError as exc:
            return RC_NOT_SPAWNED, "", f"spawn failed: {exc}"
        return completed.returncode, completed.stdout, completed.stderr

    @staticmethod
    def _parse_json(stdout: str) -> object | None:
        """Stdout だけを `json.loads` する（警告は stderr にのみ出る＝ M-05）。"""
        if not stdout.strip():
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None

    def _log(self, command: BdCommand, result: BdResult) -> None:
        """`extra` に `logging` の予約キー（`module`・`name`・`args`・`msg`）を渡さない。"""
        level = "info" if result.rc == 0 else "warning"
        getattr(self._logger, level)(
            "bd",
            extra={
                "argv": list(result.argv),
                "write": command.write,
                "attempt": result.attempts,
                "rc": result.rc,
                "elapsed_sec": result.elapsed_sec,
                "stdout_head": result.stdout_head,
                "stderr_head": result.stderr_head,
                "issue_id": command.issue_id,
            },
        )
