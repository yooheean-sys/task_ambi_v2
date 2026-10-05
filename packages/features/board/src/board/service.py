"""`BoardService`（`BoardReader` 6 ＋ `BoardWriter` 8 の 14 メソッド）。board-F02〜F15。

**1 つの実装クラス**が両 Protocol を満たす（FR-005）。2 クラスに割ると `BdCli` のロックが
2 インスタンスに割れる恐れがある（R-07）。argv と bd 呼び出し回数の正本は plan (ii) の表。

board は**盤面導出の意味論を持たない**（何が残骸か・再投入してよいかは呼び手＝
orchestrator／monitor。FR-007）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from board.exceptions import BdCommandError, BdErrorKind, BdMappingError
from board.models import as_objects, first_object, to_comment, to_issue, to_issue_ref
from board.repository import BdCommand
from common.interfaces.board import (
    BoardUnavailable,
    BoardWriteUnverified,
    IssueNotFound,
    IssueStatus,
)
from common.kernel.enums import KindLabel, LabelDim, Labels, StateLabel
from common.kernel.log import LoggerFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from board.repository import BdResult, BdRunner
    from common.interfaces.board import ClaimFilter, Comment, Issue, IssueSpec

__all__ = ["BoardService", "translate_failure"]

JSON_FLAG = "--json"
"""機械可読出力の要求。**必ず付ける**（FR-014）。`--` の**前**に置く（M-28）。"""

ARGV_SEPARATOR = "--"
"""`bd comment` の本文を隔てる区切り（M-28。無いと `-` 始まりの本文が落ちる）。"""

NO_LIMIT: tuple[str, ...] = ("-n", "0")
"""上限なしの明示（M-13。既定は 50 件で**黙って切り詰められる**）。CFG-26 が切り替える。"""

_SERVICE_LOGGER_NAME = "board.service"
"""自己ループ除外の件数を残すためだけのロガ名（R-20）。"""


def translate_failure(
    error: BdCommandError,
    *,
    issue_id: str | None = None,
    result: BdResult | None = None,
) -> IssueNotFound | BoardUnavailable:
    """内部の失敗を契約例外へ写す**単一の翻訳関数**（plan (v)・FR-041〜044）。

    `repository.py` は契約例外を送出しない（層の分離）。**not-found の翻訳は 14 メソッド一律**
    （FR-042・裁定 2026-08-21。契約 docstring の不足 13 件は design-sync B-4）。唯一の例外は
    `create` の読み戻しで、呼び出し側が `BoardWriteUnverified` に差し替える。

    Args:
        error: `repository.py` が上げた内部例外。
        issue_id: 呼び出しの対象 ID（`BdCommand.issue_id`）。列挙系は `None`。
        result: 写像失敗のとき、直前の成功応答（`argv`・`rc`・`attempts` の出所＝ R-11）。

    Returns:
        送出すべき契約例外（呼び手が `raise` する）。
    """
    if error.kind is BdErrorKind.not_found and issue_id is not None:
        return IssueNotFound(f"盤面に課題が無い: {issue_id}", issue_id=issue_id, cause=error)
    argv = result.safe_argv if result is not None else error.argv
    rc = result.rc if result is not None else error.rc
    attempts = result.attempts if result is not None else error.attempts
    return BoardUnavailable(
        str(error),
        argv=list(argv),
        rc=rc,
        stderr_head=error.stderr,
        attempts=attempts,
        cause=error,
    )


def _labels_argv(labels: list[str], flag: str = "-l") -> tuple[str, ...]:
    """ラベルを**フラグの繰返し**で並べる（区切り文字で連結しない＝ R-04・R2-6）。"""
    argv: list[str] = []
    for label in labels:
        argv.extend((flag, label))
    return tuple(argv)


def _filter_labels(claim_filter: ClaimFilter) -> list[str]:
    """`ClaimFilter` を `-l` の並びへ写す（`frozenset` は `sorted()` してから＝ R-04）。"""
    labels = sorted(claim_filter.labels)
    if claim_filter.project_key is not None:
        labels.append(Labels.project(claim_filter.project_key))
    return labels


def _state_values(labels: list[str]) -> list[str]:
    """`state` 次元の値だけを重複なく順序を保って取り出す（`Labels.parse` はここだけ＝ R-16）。"""
    values: list[str] = []
    for label in labels:
        parsed = Labels.parse(label)
        if parsed is not None and parsed[0] is LabelDim.state and parsed[1] not in values:
            values.append(parsed[1])
    return values


class BoardService:
    """`BoardReader` と `BoardWriter` を 1 インスタンスで満たす実装（FR-005）。"""

    def __init__(self, runner: BdRunner, *, list_limit_none: bool = True) -> None:
        """`bd` の実行口を受け取る（R-05）。

        Args:
            runner: `bd` の実行口。単体テストはスタブを注入する。
            list_limit_none: `children_of`／`list_working` に `-n 0`（上限なし）を付けるか。
                CFG-26 の値をそのまま渡す。**`false` にすると 2 経路が既定 50 件で黙って切れる**
                （M-13・R-17）。`has_open_issues` には効かせない。
        """
        self._runner = runner
        self._list_limit: tuple[str, ...] = NO_LIMIT if list_limit_none else ()

    # ------------------------------------------------------------------ 読取（BoardReader）

    def show(self, issue_id: str) -> Issue:
        """課題 1 件を読む（board-F02）。

        Raises:
            IssueNotFound: 指定の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        result = self._read(("show", issue_id, JSON_FLAG), issue_id=issue_id)
        return self._map(result, lambda payload: to_issue(first_object(payload)), issue_id)

    def children_of(self, parent_id: str) -> list[Issue]:
        """直下の子課題を closed 込み・上限なしで読む（board-F03）。

        bd が返すのは**直下の子だけ**（孫を含まない・closed を含む＝ M-31）なので、board 側で
        深さを絞る処理は要らない。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        argv = ("list", "--parent", parent_id, "--all", *self._list_limit, JSON_FLAG)
        result = self._read(argv, issue_id=parent_id)
        return self._map(result, self._issues_of, parent_id)

    def open_question_of(self, issue_id: str) -> Issue | None:
        """未回答の `kind:question` 子を 1 件返す。無ければ `None`（board-F04）。

        `dependencies` から「`kind:question` を含み `status != closed`」の**最初の 1 件**を選び
        （bd の順。board は並べ替えない）、その id で **2 回目の `show`** を打つ（R-19・DIFF-05）。
        `dependency_type` では絞らない（張り方は呼び手の起票の仕方に属するため）。

        Raises:
            IssueNotFound: 指定の課題（または選ばれた質問）が無い。
            BoardUnavailable: `bd` に届かない。
        """
        parent = self.show(issue_id)
        question_label = Labels.kind(KindLabel.question)
        for ref in parent.dependencies:
            if question_label in ref.labels and ref.status is not IssueStatus.closed:
                return self.show(ref.id)
        return None

    def list_working(self, claim_filter: ClaimFilter) -> list[Issue]:
        """`state:working` の残骸を列挙する（closed は除く。board-F05）。

        `--all` で取ってから board 側で closed を除く（R-18。bd の既定フィルタが将来変わっても
        結果が変わらない）。**何を再投入するかは呼び手**（D01・D09-d）。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        argv = (
            "list",
            "-l",
            Labels.state(StateLabel.working),
            *_labels_argv(_filter_labels(claim_filter)),
            "--all",
            *self._list_limit,
            JSON_FLAG,
        )
        result = self._read(argv)
        issues = self._map(result, self._issues_of, None)
        return [issue for issue in issues if issue.status is not IssueStatus.closed]

    def comments(self, issue_id: str) -> list[Comment]:
        """コメントを bd の返す順のまま読む（board-F06）。

        **並べ替えない**（M-21 で `created_at` 昇順が確認済み）。**種別を付けない**（P5D-005）。

        Raises:
            IssueNotFound: 指定の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        result = self._read(("comments", issue_id, JSON_FLAG), issue_id=issue_id)
        return self._map(
            result,
            lambda payload: [to_comment(entry) for entry in as_objects(payload)],
            issue_id,
        )

    def has_open_issues(self, project_key: str) -> bool:
        """`project:<key>` の未完了課題があるか（削除ガード。board-F07）。

        `--all` も `-n 0` も付けない（R-17）。bd の既定フィルタ（closed を除く全状態＝ M-25）が
        「未完了」の意図と一致し、既定の上限は「1 件以上あるか」の答えを変えない。
        **読めないときに `False` に倒さない**（P3・FR-024）。

        Raises:
            BoardUnavailable: `bd` に届かない。
        """
        argv = ("list", "-l", Labels.project(project_key), JSON_FLAG)
        result = self._read(argv)
        return len(self._map(result, self._issues_of, None)) > 0

    # ------------------------------------------------------------------ 書込（BoardWriter）

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """次の 1 件を確保する。対象が無ければ `None`（board-F08）。

        ①の応答は `Issue` を組むのに十分なので**再読取しない**（M-23）。②が失敗しても
        **解放しない**（確保を保ったまま `BoardUnavailable`＝ P5D-007）。FR-026（後段照合と
        解放）は**実装しない**＝ 否定の要求として単体テストが argv を見張る。

        Raises:
            IssueNotFound: 対象の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        claimed = self._write(
            (
                "ready",
                "--claim",
                "--limit",
                "1",
                *_labels_argv(_filter_labels(claim_filter)),
                JSON_FLAG,
            ),
            actor=actor,
        )
        issues = self._map(claimed, self._issues_of, None)
        if not issues:
            return None
        issue = issues[0]
        self._write(
            ("update", issue.id, "--add-label", Labels.state(StateLabel.working), JSON_FLAG),
            actor=actor,
            issue_id=issue.id,
        )
        return issue

    def create(self, spec: IssueSpec, actor: str) -> str:
        """課題を起票し、読み戻しで確かめてから新しい issue_id を返す（board-F09）。

        表題は位置引数でなく **`--title`**（R-03・M-29）。**`--no-inherit-labels` を必ず付ける**
        （親のラベル継承が入ると段判定が壊れる＝ M-22）。

        Raises:
            BoardUnavailable: `bd` に届かない。
            BoardWriteUnverified: 書込を確認読取で確かめられない。
        """
        argv: tuple[str, ...] = (
            "create",
            "--title",
            spec.title,
            "-d",
            spec.description,
            "-p",
            str(spec.priority),
            "-t",
            spec.issue_type,
            *(() if spec.parent is None else ("--parent", spec.parent)),
            "--no-inherit-labels",
            *_labels_argv(spec.labels),
            "--acceptance",
            spec.acceptance,
            JSON_FLAG,
        )
        created = self._write(argv, actor=actor)
        new_id = self._map(created, lambda payload: to_issue(first_object(payload)).id, None)
        return self._verify_created(new_id, spec)

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """兄弟依存（blocks）を張る（board-F10）。

        **冪等**: 既存の辺への再実行も rc=0（M-20）。board 側で事前確認しない。

        Raises:
            IssueNotFound: 対象の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        self._write(
            ("dep", "add", from_id, "--blocked-by", to_id, JSON_FLAG),
            actor=actor,
            issue_id=from_id,
        )

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """`old_id` に blocks 依存する側を `new_id` へ付け替える（board-F11）。

        `-t blocks` で `parent-child` を除外し（M-19）、`dep_id == new_id` は**除いてから**張る
        （自己ループ回避。R-20）。①が失敗したら**何も張らない**。②の途中失敗は張れた辺を残す
        （FR-035。board は巻き戻さない）。

        Raises:
            IssueNotFound: 対象の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        listed = self._read(
            ("dep", "list", old_id, "--direction=up", "-t", "blocks", JSON_FLAG),
            issue_id=old_id,
        )
        refs = self._map(
            listed,
            lambda payload: [to_issue_ref(entry) for entry in as_objects(payload)],
            old_id,
        )
        targets = [ref.id for ref in refs if ref.id != new_id]
        skipped = len(refs) - len(targets)
        if skipped:
            self._note_self_loops(old_id, new_id, skipped)
        for dependent_id in targets:
            self._write(
                ("dep", "add", dependent_id, "--blocked-by", new_id, JSON_FLAG),
                actor=actor,
                issue_id=dependent_id,
            )

    def release(self, issue_id: str, actor: str) -> None:
        """確保を解放する（**2 手のまま畳まない**＝ R-22。board-F12）。

        1 手目だけ成功しても `state:working` が残るので `list_working` が回収できる。

        Raises:
            IssueNotFound: 対象の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        self._write(
            ("update", issue_id, "--status", "open", "--assignee", "", JSON_FLAG),
            actor=actor,
            issue_id=issue_id,
        )
        self._write(
            ("update", issue_id, "--remove-label", Labels.state(StateLabel.working), JSON_FLAG),
            actor=actor,
            issue_id=issue_id,
        )

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """コメントを 1 件付ける（反映確認をしない。board-F13）。

        `--` 区切りが必須で `--json` は `--` の**前**に置く（M-28）。本システムが盤面へ書く
        封筒・停止理由・受け入れ条件は Markdown 箇条書き＝ `-` 始まりが**通常経路**である。

        Raises:
            IssueNotFound: 対象の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        self._write(
            ("comment", issue_id, JSON_FLAG, ARGV_SEPARATOR, body),
            actor=actor,
            issue_id=issue_id,
        )

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """課題を close する（既に closed なら**書込 0 回**。board-F14・M-24）。

        ①の読取は「書込を省くための事前読取」であり確認読取ではない（SC-003）。

        Raises:
            IssueNotFound: 対象の課題が無い（「既に無い」を成功に倒さない）。
            BoardUnavailable: `bd` に届かない。
        """
        if self.show(issue_id).status is IssueStatus.closed:
            return
        self._write(("close", issue_id, "-r", reason, JSON_FLAG), actor=actor, issue_id=issue_id)

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """ラベルを **1 回の `bd update`** で付け外しする（board-F15・FR-033・DIFF-10）。

        1 枚ずつだと途中失敗で「`state` 次元の値が 1 つも無い課題」が残り、`list_working` の
        残骸列挙から漏れて無症状の停滞になる。正規化後に両集合が空なら **bd を打たない**。

        Raises:
            ValueError: `add` に `state` 次元の値が 2 つ以上ある（R-21。bd を打たない）。
            IssueNotFound: 対象の課題が無い。
            BoardUnavailable: `bd` に届かない。
        """
        to_add, to_remove = self._normalise_labels(add, remove)
        if not to_add and not to_remove:
            return
        argv = (
            "update",
            issue_id,
            *_labels_argv(to_remove, "--remove-label"),
            *_labels_argv(to_add, "--add-label"),
            JSON_FLAG,
        )
        self._write(argv, actor=actor, issue_id=issue_id)

    # ------------------------------------------------------------------ 内部

    def _read(self, argv: tuple[str, ...], *, issue_id: str | None = None) -> BdResult:
        """読取を 1 回実行する（`actor` を載せない＝ `BEADS_ACTOR` を設定しない）。"""
        return self._run(BdCommand(argv=argv, actor=None, write=False, issue_id=issue_id))

    def _write(self, argv: tuple[str, ...], *, actor: str, issue_id: str | None = None) -> BdResult:
        """書込を 1 回実行する（`actor` は env `BEADS_ACTOR` に載る＝ FR-012）。"""
        return self._run(BdCommand(argv=argv, actor=actor, write=True, issue_id=issue_id))

    def _run(self, command: BdCommand) -> BdResult:
        """失敗を必ず**単一の翻訳関数**に通す（FR-041）。"""
        try:
            return self._runner.run(command)
        except BdCommandError as error:
            raise translate_failure(error, issue_id=command.issue_id) from error

    def _map[T](self, result: BdResult, mapper: Callable[[object], T], issue_id: str | None) -> T:
        """写像の失敗を `BoardUnavailable` へ翻訳する（`argv`・`rc`・`attempts` は R-11）。"""
        try:
            return mapper(result.json)
        except BdMappingError as error:
            raise translate_failure(error, issue_id=issue_id, result=result) from error

    @staticmethod
    def _issues_of(payload: object) -> list[Issue]:
        """`bd list`／`bd ready` の応答を `Issue` の列へ写す。

        **`dependencies` は写さない**（`with_dependencies=False`）。bd 1.1.2 の `bd list` は
        依存を**辺の形**（`depends_on_id`／`type`。`status` も `labels` も無い）でしか返さず、
        `IssueRef` を作ると捏造になるためである。依存を見たい呼び手は `show` を使う。
        """
        return [to_issue(entry, with_dependencies=False) for entry in as_objects(payload)]

    def _verify_created(self, new_id: str, spec: IssueSpec) -> str:
        """起票の読み戻し（**唯一の書込検証**＝ P5D-007・FR-027）。

        照合は **`set(labels)` の完全一致と `parent` の一致だけ**（表題・受け入れ条件は見ない）。
        読み戻せない（not-found を含む）ことは「未検証」であって「対象が無い」ではない
        （`create` が id を返した以上その実在は board が保証する）。

        Raises:
            BoardWriteUnverified: 読み戻せない／spec と一致しない。
        """
        try:
            readback = self.show(new_id)
        except IssueNotFound as error:
            raise BoardWriteUnverified(
                f"起票した課題を読み戻せない: {new_id}",
                issue_id=new_id,
                mismatch="readback: IssueNotFound",
                cause=error,
            ) from error
        mismatches: list[str] = []
        expected_labels = sorted(set(spec.labels))
        actual_labels = sorted(set(readback.labels))
        if expected_labels != actual_labels:
            mismatches.append(f"labels: expected {expected_labels} / actual {actual_labels}")
        if readback.parent != spec.parent:
            mismatches.append(f"parent: expected {spec.parent} / actual {readback.parent}")
        if mismatches:
            raise BoardWriteUnverified(
                f"起票した課題が spec と一致しない: {new_id}",
                issue_id=new_id,
                mismatch="; ".join(mismatches),
            )
        return new_id

    @staticmethod
    def _normalise_labels(add: list[str], remove: list[str]) -> tuple[list[str], list[str]]:
        """`state` 次元の排他を保証し、`remove ← remove ∖ add` へ正規化する（M-12）。

        `add` に `state` 次元の値が 1 つあれば、同次元の**他の値**を `remove` へ自動で足す
        （1 次元 1 値＝ loop.py:68-69 の規律を board で保証する）。

        Args:
            add: 付けるラベル。
            remove: 外すラベル。

        Returns:
            正規化後の `(add, remove)`（いずれも `sorted()` 済み＝ argv が決定的になる）。

        Raises:
            ValueError: `add` に `state` 次元の値が 2 つ以上ある（R-21。bd を打たない）。
        """
        state_values = _state_values(add)
        if len(state_values) > 1:
            msg = f"add に state 次元の値が 2 つ以上ある（1 次元 1 値）: {state_values}"
            raise ValueError(msg)
        excluded: set[str] = (
            {Labels.state(member) for member in StateLabel if member.value not in state_values}
            if state_values
            else set()
        )
        add_set = set(add)
        remove_set = (set(remove) | excluded) - add_set
        return sorted(add_set), sorted(remove_set)

    @staticmethod
    def _note_self_loops(old_id: str, new_id: str, skipped: int) -> None:
        """自己ループ除外の件数を残す（R-20。設計 board-F11 に無い追加なので可視化する）。"""
        LoggerFactory.get_logger(_SERVICE_LOGGER_NAME).info(
            "move_dependents",
            extra={
                "detail": "self_loop_skipped",
                "old_id": old_id,
                "issue_id": new_id,
                "skipped": skipped,
            },
        )
