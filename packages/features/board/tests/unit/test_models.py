"""`models.py` の写像（FR-036〜040・M5-1〜M5-6）。キー名の正本は contracts/json-mapping.md。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from board_testing import comment_payload, issue_payload, issue_ref_payload

from board.exceptions import BdMappingError
from board.models import as_objects, first_object, to_comment, to_issue, to_issue_ref
from common.interfaces.board import IssueStatus


class TestFirstObjectAndAsObjects:
    """単一オブジェクトと配列の 2 形を吸収する（M-16）。"""

    def test_first_object_accepts_a_bare_object(self) -> None:
        assert first_object({"id": "pj-1"}) == {"id": "pj-1"}

    def test_first_object_takes_the_head_of_an_array(self) -> None:
        assert first_object([{"id": "pj-1"}, {"id": "pj-2"}]) == {"id": "pj-1"}

    def test_first_object_rejects_an_empty_array(self) -> None:
        with pytest.raises(BdMappingError):
            first_object([])

    def test_first_object_rejects_a_scalar(self) -> None:
        with pytest.raises(BdMappingError):
            first_object("pj-1")

    def test_as_objects_wraps_a_bare_object(self) -> None:
        assert as_objects({"id": "pj-1"}) == [{"id": "pj-1"}]

    def test_as_objects_keeps_an_array(self) -> None:
        assert as_objects([{"id": "pj-1"}, {"id": "pj-2"}]) == [{"id": "pj-1"}, {"id": "pj-2"}]

    def test_as_objects_accepts_an_empty_array(self) -> None:
        assert as_objects([]) == []

    def test_as_objects_rejects_a_non_object_element(self) -> None:
        with pytest.raises(BdMappingError):
            as_objects(["pj-1"])


class TestToIssue:
    """`Issue` への写像（contracts/json-mapping.md §2）。"""

    def test_maps_every_attribute(self) -> None:
        issue = to_issue(
            issue_payload(
                id="pj-3",
                title="子課題",
                description="説明",
                status="in_progress",
                labels=["kind:child", "state:working"],
                parent="pj-1",
                assignee="demo_pj.programmer",
                acceptance_criteria="- [ ] A\n- [ ] B",
                dependencies=[issue_ref_payload()],
                created_at="2026-08-21T01:02:03Z",
                updated_at="2026-08-21T04:05:06Z",
            )
        )
        assert issue.id == "pj-3"
        assert issue.title == "子課題"
        assert issue.description == "説明"
        assert issue.status is IssueStatus.in_progress
        assert issue.labels == ["kind:child", "state:working"]
        assert issue.parent == "pj-1"
        assert issue.assignee == "demo_pj.programmer"
        assert issue.acceptance == "- [ ] A\n- [ ] B"
        assert [ref.id for ref in issue.dependencies] == ["pj-2"]
        assert issue.created_at == datetime(2026, 8, 21, 1, 2, 3, tzinfo=UTC)
        assert issue.updated_at == datetime(2026, 8, 21, 4, 5, 6, tzinfo=UTC)

    def test_acceptance_comes_from_acceptance_criteria(self) -> None:
        raw = issue_payload()
        del raw["acceptance_criteria"]
        raw["acceptance"] = "この綴りは bd のキーではない"
        assert to_issue(raw).acceptance == ""

    @pytest.mark.parametrize(
        ("key", "expected_attribute", "expected_value"),
        [
            ("title", "title", ""),
            ("description", "description", ""),
            ("labels", "labels", []),
            ("parent", "parent", None),
            ("assignee", "assignee", None),
            ("acceptance_criteria", "acceptance", ""),
            ("dependencies", "dependencies", []),
        ],
    )
    def test_omitted_optional_keys_fall_back_to_defaults(
        self, key: str, expected_attribute: str, expected_value: object
    ) -> None:
        """値が空の任意キーは bd が省略する（M-17）。"""
        raw = issue_payload()
        del raw[key]
        assert getattr(to_issue(raw), expected_attribute) == expected_value

    @pytest.mark.parametrize("key", ["id", "status", "created_at", "updated_at"])
    def test_missing_required_key_raises(self, key: str) -> None:
        """必須キーの欠落は黙って埋めない（M5-4・FR-040）。"""
        raw = issue_payload()
        del raw[key]
        with pytest.raises(BdMappingError) as exc:
            to_issue(raw)
        assert exc.value.key == key

    def test_unknown_status_raises_instead_of_defaulting(self) -> None:
        """bd の `deferred` など契約に無い状態は例外（M5-5。既定へ倒さない）。"""
        with pytest.raises(BdMappingError) as exc:
            to_issue(issue_payload(status="deferred"))
        assert exc.value.key == "status"

    def test_unknown_keys_are_ignored(self) -> None:
        """`priority`・`schema_version` などは写像しない（M5-3）。"""
        issue = to_issue(issue_payload(owner="dolt", close_reason="done", comment_count=3))
        assert issue.id == "pj-1"

    def test_labels_are_kept_verbatim_without_dimension_check(self) -> None:
        """自由ラベル（`Labels.parse` が `None` を返す）でも落とさない（M5-1・FR-037）。"""
        issue = to_issue(issue_payload(labels=["自由ラベル", "zzz", "kind:child"]))
        assert issue.labels == ["自由ラベル", "zzz", "kind:child"]

    def test_dependencies_keep_closed_and_parent_child_edges(self) -> None:
        """closed の依存も `parent-child` の辺も落とさない（FR-039・M-18）。"""
        issue = to_issue(
            issue_payload(
                dependencies=[
                    issue_ref_payload(id="pj-4", status="closed", dependency_type="blocks"),
                    issue_ref_payload(id="pj-5", dependency_type="parent-child"),
                ]
            )
        )
        assert [(ref.id, ref.status, ref.dependency_type) for ref in issue.dependencies] == [
            ("pj-4", IssueStatus.closed, "blocks"),
            ("pj-5", IssueStatus.open, "parent-child"),
        ]

    def test_nanosecond_timestamps_are_accepted(self) -> None:
        """`bd create` の応答のみナノ秒精度（M5-6）。"""
        issue = to_issue(issue_payload(created_at="2026-08-21T01:02:03.123456789Z"))
        assert issue.created_at.year == 2026

    def test_broken_timestamp_raises(self) -> None:
        with pytest.raises(BdMappingError) as exc:
            to_issue(issue_payload(created_at="21/08/2026"))
        assert exc.value.key == "created_at"

    def test_wrong_type_for_labels_raises(self) -> None:
        with pytest.raises(BdMappingError) as exc:
            to_issue(issue_payload(labels="kind:child"))
        assert exc.value.key == "labels"


class TestToIssueRef:
    """`IssueRef` への写像（contracts/json-mapping.md §3）。"""

    def test_maps_every_attribute(self) -> None:
        ref = to_issue_ref(issue_ref_payload(labels=["kind:question"]))
        assert (ref.id, ref.title, ref.status, ref.labels, ref.dependency_type) == (
            "pj-2",
            "依存先",
            IssueStatus.open,
            ["kind:question"],
            "blocks",
        )

    @pytest.mark.parametrize(
        ("key", "attribute", "value"), [("title", "title", ""), ("labels", "labels", [])]
    )
    def test_optional_keys_fall_back(self, key: str, attribute: str, value: object) -> None:
        raw = issue_ref_payload()
        del raw[key]
        assert getattr(to_issue_ref(raw), attribute) == value

    @pytest.mark.parametrize("key", ["id", "status", "dependency_type"])
    def test_missing_required_key_raises(self, key: str) -> None:
        raw = issue_ref_payload()
        del raw[key]
        with pytest.raises(BdMappingError) as exc:
            to_issue_ref(raw)
        assert exc.value.key == key

    def test_unknown_dependency_type_raises(self) -> None:
        """契約の `Literal["blocks", "parent-child"]` に無い値は例外。"""
        with pytest.raises(BdMappingError) as exc:
            to_issue_ref(issue_ref_payload(dependency_type="related"))
        assert exc.value.key == "dependency_type"


class TestToComment:
    """`Comment` への写像（contracts/json-mapping.md §4）。"""

    def test_actor_comes_from_author_and_body_from_text(self) -> None:
        comment = to_comment(comment_payload(author="人間", text="- 箇条書き"))
        assert comment.actor == "人間"
        assert comment.body == "- 箇条書き"
        assert comment.issue_id == "pj-1"
        assert comment.created_at == datetime(2026, 8, 21, tzinfo=UTC)

    @pytest.mark.parametrize("key", ["id", "issue_id", "author", "text", "created_at"])
    def test_every_key_is_required(self, key: str) -> None:
        raw = comment_payload()
        del raw[key]
        with pytest.raises(BdMappingError) as exc:
            to_comment(raw)
        assert exc.value.key == key
