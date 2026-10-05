"""`set_labels` のラベル組立（`state` 次元の排他・`remove ∖ add` の正規化。FR-033・M-12・R-21）。"""

from __future__ import annotations

import pytest
from board_testing import StubRunner, bd_ok, issue_payload

from board.service import BoardService

ACTOR = "demo_pj.programmer"


def _labels_of(argv: tuple[str, ...], flag: str) -> list[str]:
    """argv から `flag` の値だけを取り出す。"""
    return [argv[i + 1] for i, token in enumerate(argv) if token == flag]


class TestStateExclusivity:
    """`state` 次元は 1 次元 1 値（loop.py:68-69 の規律を board で保証する）。"""

    def test_adding_a_state_removes_the_other_values_of_the_dimension(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=["state:working"], remove=[], actor=ACTOR)
        argv = runner.argvs[0]
        assert _labels_of(argv, "--add-label") == ["state:working"]
        assert _labels_of(argv, "--remove-label") == ["state:failing"]

    def test_adding_the_other_state_is_symmetric(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=["state:failing"], remove=[], actor=ACTOR)
        assert _labels_of(runner.argvs[0], "--remove-label") == ["state:working"]

    def test_two_state_values_in_add_raise_before_touching_bd(self) -> None:
        """R-21: 相反指定は `ValueError`（bd を打たない）。"""
        runner = StubRunner()
        with pytest.raises(ValueError, match="state"):
            BoardService(runner).set_labels(
                "pj-1", add=["state:working", "state:failing"], remove=[], actor=ACTOR
            )
        assert runner.calls == 0

    def test_duplicate_state_value_is_not_two_values(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels(
            "pj-1", add=["state:working", "state:working"], remove=[], actor=ACTOR
        )
        assert _labels_of(runner.argvs[0], "--add-label") == ["state:working"]

    def test_other_dimensions_are_not_made_exclusive(self) -> None:
        """排他は `state` 次元だけ（`note` や `kind` は複数付いてよい）。"""
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=["note:a", "note:b"], remove=[], actor=ACTOR)
        assert _labels_of(runner.argvs[0], "--remove-label") == []

    def test_unknown_dimension_labels_are_passed_through(self) -> None:
        """`Labels.parse` が `None` を返す自由ラベルでも落とさない（FR-037）。"""
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=["自由ラベル"], remove=[], actor=ACTOR)
        assert _labels_of(runner.argvs[0], "--add-label") == ["自由ラベル"]


class TestNormalisation:
    """`remove ← remove ∖ add`（M-12: 同一ラベルが双方にあると bd は remove を優先）。"""

    def test_a_label_in_both_sets_survives_only_in_add(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels(
            "pj-1", add=["note:a"], remove=["note:a", "note:b"], actor=ACTOR
        )
        argv = runner.argvs[0]
        assert _labels_of(argv, "--add-label") == ["note:a"]
        assert _labels_of(argv, "--remove-label") == ["note:b"]

    def test_normalisation_can_empty_the_remove_set(self) -> None:
        """`add` は残るので bd は 1 回打つ（両集合が空になるのは `add` も空のときだけ）。"""
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=["note:a"], remove=["note:a"], actor=ACTOR)
        assert runner.argvs == [("update", "pj-1", "--add-label", "note:a", "--json")]

    def test_state_exclusivity_survives_normalisation(self) -> None:
        """自動で足した `state:failing` は `add` に無いので残る。"""
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels(
            "pj-1", add=["state:working"], remove=["state:working"], actor=ACTOR
        )
        argv = runner.argvs[0]
        assert _labels_of(argv, "--add-label") == ["state:working"]
        assert _labels_of(argv, "--remove-label") == ["state:failing"]

    def test_removing_a_label_that_is_not_attached_is_allowed(self) -> None:
        """付いていないラベルの除去も rc=0（冪等。SC-004）。"""
        runner = StubRunner(bd_ok([issue_payload()]))
        BoardService(runner).set_labels("pj-1", add=[], remove=["note:missing"], actor=ACTOR)
        assert _labels_of(runner.argvs[0], "--remove-label") == ["note:missing"]

    def test_inputs_are_not_mutated(self) -> None:
        runner = StubRunner(bd_ok([issue_payload()]))
        add = ["state:working"]
        remove = ["note:a"]
        BoardService(runner).set_labels("pj-1", add=add, remove=remove, actor=ACTOR)
        assert add == ["state:working"]
        assert remove == ["note:a"]
