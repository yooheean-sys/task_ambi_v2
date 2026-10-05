"""書込 8 メソッドを**実 `bd`** に対して検証する（US2・SC-003〜005）。

ロック競合は**実物で再現できない**（M-07: 6 並行・20 並行でも再現せず、embedded Dolt が内部で
直列化する模様）ため、ここでは検査しない。機構の検査は `tests/unit/test_repository_retry.py`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from common.interfaces.board import (
    BoardWriteUnverified,
    ClaimFilter,
    IssueNotFound,
    IssueSpec,
    IssueStatus,
)

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader, BoardWriter

pytestmark = pytest.mark.requires_bd

ACTOR = "demo_pj.programmer"
PROJECT = "boardtest"
EXECUTOR_FILTER = ClaimFilter(labels=frozenset({"role:executor"}), project_key=PROJECT)


def _spec(title: str, labels: list[str], parent: str | None = None) -> IssueSpec:
    return IssueSpec(
        title=title,
        description="説明",
        labels=labels,
        parent=parent,
        acceptance="- [ ] 受け入れ条件",
    )


@pytest.fixture
def parent_id(writer: BoardWriter) -> str:
    """親 1 件（`--no-inherit-labels` の効果を見るためラベルを持たせる）。"""
    return writer.create(_spec("親", ["kind:program", f"project:{PROJECT}", "note:parent"]), ACTOR)


@pytest.fixture
def task_id(writer: BoardWriter, parent_id: str) -> str:
    """確保できる子 1 件。"""
    return writer.create(
        _spec("実行対象", ["kind:child", f"project:{PROJECT}", "role:executor"], parent_id), ACTOR
    )


class TestCreate:
    def test_labels_match_exactly_without_inheriting_the_parent(
        self, reader: BoardReader, writer: BoardWriter, parent_id: str
    ) -> None:
        """M-22・SC-003: `--no-inherit-labels` により親のラベルが混ざらない。"""
        child = writer.create(_spec("子", ["kind:child", f"project:{PROJECT}"], parent_id), ACTOR)
        readback = reader.show(child)
        assert set(readback.labels) == {"kind:child", f"project:{PROJECT}"}
        assert "note:parent" not in readback.labels
        assert readback.parent == parent_id

    def test_accepts_markdown_bullets_in_title_and_acceptance(
        self, reader: BoardReader, writer: BoardWriter
    ) -> None:
        """フラグ形式の値は `-` 始まりでも安全に渡る（M-29）。"""
        spec = IssueSpec(
            title="-- ハイフンで始まる表題",
            description="-d のように見える説明",
            labels=[f"project:{PROJECT}"],
            parent=None,
            acceptance="- [ ] 箇条書き\n- [ ] 2 行目",
        )
        new_id = writer.create(spec, ACTOR)
        readback = reader.show(new_id)
        assert readback.title == "-- ハイフンで始まる表題"
        assert readback.acceptance == "- [ ] 箇条書き\n- [ ] 2 行目"

    def test_creating_under_a_missing_parent_fails_loudly(self, writer: BoardWriter) -> None:
        """P3・FR-024: 読めない／書けないときに黙って成功へ倒さない。"""
        from common.interfaces.board import BoardUnavailable

        with pytest.raises((BoardUnavailable, BoardWriteUnverified, IssueNotFound)):
            writer.create(_spec("孤児", [f"project:{PROJECT}"], "boardtest-zzzzzz"), ACTOR)


class TestClaimAndRelease:
    def test_claim_sets_assignee_status_and_state_label(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        claimed = writer.claim_next(EXECUTOR_FILTER, ACTOR)
        assert claimed is not None
        assert claimed.id == task_id
        readback = reader.show(task_id)
        assert readback.status is IssueStatus.in_progress
        assert readback.assignee == ACTOR
        assert "state:working" in readback.labels

    def test_nothing_ready_returns_none(self, writer: BoardWriter, task_id: str) -> None:
        assert writer.claim_next(EXECUTOR_FILTER, ACTOR) is not None
        assert task_id
        assert writer.claim_next(EXECUTOR_FILTER, ACTOR) is None

    def test_release_restores_all_three_and_allows_re_claim(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        """SC-005・M-32: 状態・担当・`state:working` の 3 つすべてが戻る。"""
        first = writer.claim_next(EXECUTOR_FILTER, ACTOR)
        assert first is not None
        writer.release(task_id, ACTOR)
        released = reader.show(task_id)
        assert released.status is IssueStatus.open
        assert released.assignee is None
        assert "state:working" not in released.labels
        again = writer.claim_next(EXECUTOR_FILTER, ACTOR)
        assert again is not None
        assert again.id == task_id


class TestDependencies:
    def test_add_dependency_is_idempotent(
        self, reader: BoardReader, writer: BoardWriter, parent_id: str, task_id: str
    ) -> None:
        """M-20: 既存の辺への再実行も rc=0。"""
        blocker = writer.create(_spec("先行", [f"project:{PROJECT}"], parent_id), ACTOR)
        writer.add_dependency(task_id, blocker, ACTOR)
        writer.add_dependency(task_id, blocker, ACTOR)
        assert blocker in {ref.id for ref in reader.show(task_id).dependencies}

    def test_move_dependents_repoints_blocks_edges(
        self, reader: BoardReader, writer: BoardWriter, parent_id: str
    ) -> None:
        old = writer.create(_spec("旧", [f"project:{PROJECT}"], parent_id), ACTOR)
        new = writer.create(_spec("新", [f"project:{PROJECT}"], parent_id), ACTOR)
        dependent = writer.create(_spec("後続", [f"project:{PROJECT}"], parent_id), ACTOR)
        writer.add_dependency(dependent, old, ACTOR)
        writer.move_dependents(old, new, ACTOR)
        assert new in {ref.id for ref in reader.show(dependent).dependencies}

    def test_move_dependents_skips_the_self_loop(
        self, reader: BoardReader, writer: BoardWriter, parent_id: str
    ) -> None:
        """R-20: `dependent_id == new_id` は張らない。"""
        old = writer.create(_spec("旧", [f"project:{PROJECT}"], parent_id), ACTOR)
        new = writer.create(_spec("新", [f"project:{PROJECT}"], parent_id), ACTOR)
        writer.add_dependency(new, old, ACTOR)
        writer.move_dependents(old, new, ACTOR)
        assert new not in {ref.id for ref in reader.show(new).dependencies}

    def test_move_dependents_without_dependents_is_a_no_op(
        self, writer: BoardWriter, parent_id: str
    ) -> None:
        old = writer.create(_spec("旧", [f"project:{PROJECT}"], parent_id), ACTOR)
        new = writer.create(_spec("新", [f"project:{PROJECT}"], parent_id), ACTOR)
        writer.move_dependents(old, new, ACTOR)


class TestCommentAndClose:
    def test_comment_body_starting_with_a_dash_is_stored_verbatim(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        """M-28: `--` 区切りが無いと `unknown shorthand flag` で落ちる。"""
        body = "- [ ] 封筒の 1 行目\n- [ ] 2 行目"
        writer.comment(task_id, body, ACTOR)
        assert [c.body for c in reader.comments(task_id)] == [body]

    def test_close_then_close_again_is_idempotent(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        writer.close(task_id, "完了した", ACTOR)
        assert reader.show(task_id).status is IssueStatus.closed
        writer.close(task_id, "2 回目", ACTOR)
        assert reader.show(task_id).status is IssueStatus.closed

    def test_closing_a_missing_issue_raises(self, writer: BoardWriter) -> None:
        """「既に無い」を成功に倒さない（P3）。"""
        with pytest.raises(IssueNotFound):
            writer.close("boardtest-zzzzzz", "完了", ACTOR)


class TestSetLabels:
    def test_add_and_remove_in_one_update(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        writer.set_labels(task_id, add=["note:a"], remove=["role:executor"], actor=ACTOR)
        labels = set(reader.show(task_id).labels)
        assert "note:a" in labels
        assert "role:executor" not in labels

    def test_state_dimension_stays_exclusive(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        writer.set_labels(task_id, add=["state:failing"], remove=[], actor=ACTOR)
        writer.set_labels(task_id, add=["state:working"], remove=[], actor=ACTOR)
        labels = set(reader.show(task_id).labels)
        assert "state:working" in labels
        assert "state:failing" not in labels

    def test_removing_an_absent_label_succeeds(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        """SC-004: 付いていないラベルの除去も成功する。"""
        writer.set_labels(task_id, add=[], remove=["note:never-attached"], actor=ACTOR)
        assert "note:never-attached" not in set(reader.show(task_id).labels)

    def test_free_form_labels_survive(
        self, reader: BoardReader, writer: BoardWriter, task_id: str
    ) -> None:
        """FR-037: `Labels.parse` が `None` を返す自由ラベルでも落とさない。"""
        writer.set_labels(task_id, add=["人間がつけた自由ラベル"], remove=[], actor=ACTOR)
        assert "人間がつけた自由ラベル" in set(reader.show(task_id).labels)


def test_writes_do_not_read_back_except_create(
    reader: BoardReader, writer: BoardWriter, task_id: str
) -> None:
    """SC-003: 起票以外の書込は確認読取を持たない（呼び手が確かめる）。

    実 bd では回数を数えられないので、**呼び手が読まなければ状態が見えない**ことを確かめる。
    """
    writer.set_labels(task_id, add=["note:x"], remove=[], actor=ACTOR)
    assert "note:x" in set(reader.show(task_id).labels)
