"""リポジトリの成果物トークン（`repo/<パス>`。D25-b）。

抽出（`extract_repo_tokens`）・正準形（`repo_path`）・分解検査での扱い（`require_outcome_path` の
段で outcome と同列に「成果物の宣言」と数える）を見る。**例外を 1 つも送出しない**。
"""

from __future__ import annotations

import pytest
from conftest import make_item, make_proposal, make_service, stage_spec

from common.kernel.enums import StageName
from stages.models import repo_path

SUBREQUEST = stage_spec(StageName.subrequest)

EXTRACT_CASES: tuple[tuple[str, list[str]], ...] = (
    ("- [ ] repo/src/app.py が存在する", ["repo/src/app.py"]),
    ("`repo/tests/test_app.py` を足す", ["repo/tests/test_app.py"]),
    ("repo/a.py と repo/b.py の両方", ["repo/a.py", "repo/b.py"]),
    ("repo/a.py、次に repo/b.py。", ["repo/a.py", "repo/b.py"]),
    ("repo/a.py\nrepo/a.py", ["repo/a.py"]),
    ("myrepo/x.py は対象外", []),
    ("../repo/x.py は対象外", []),
    ("成果物はまだ決めていない", []),
    ("", []),
)


@pytest.mark.parametrize(("acceptance", "expected"), EXTRACT_CASES)
def test_extract_repo_tokens(acceptance: str, expected: list[str]) -> None:
    assert make_service().extract_repo_tokens(acceptance) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("repo/src/app.py", "src/app.py"),
        ("  repo/src/app.py  ", "src/app.py"),
        ("repo/./src//app.py", "src/app.py"),
        ("repo/", None),
        ("repo//etc/passwd", None),
        ("repo/~/x", None),
        ("repo/../etc", None),
        ("repo/src/../../x", None),
        ("repo/.git/config", None),
        ("repo/src\\app.py", None),
        ("outcome/a.md", None),
    ],
)
def test_repo_path(token: str, expected: str | None) -> None:
    assert repo_path(token) == expected


def test_outcome_tokens_exclude_repo_paths_containing_outcome() -> None:
    """`repo/docs/outcome/x.md` はリポジトリの成果物で、outcome の成果物ではない。"""
    service = make_service()
    text = "- [ ] repo/docs/outcome/x.md と outcome/y.md"
    assert service.extract_outcome_tokens(text) == ["outcome/y.md"]
    assert service.extract_repo_tokens(text) == ["repo/docs/outcome/x.md"]


def test_repo_token_satisfies_required_deliverable() -> None:
    """`require_outcome_path` の段で、`repo/` だけの宣言でも合格する。"""
    proposal = make_proposal([make_item(acceptance=["- [ ] repo/src/app.py に関数を足す"])])
    result = make_service().validate_proposal(proposal, SUBREQUEST, [])
    assert result.problems == []


def test_non_canonical_repo_token_is_reported_but_counts_as_present() -> None:
    proposal = make_proposal([make_item(acceptance=["- [ ] repo/../etc/passwd を書き換える"])])
    result = make_service().validate_proposal(proposal, SUBREQUEST, [])
    assert any("リポジトリの成果物" in p for p in result.problems)
    assert not any("1 つも" in p for p in result.problems)


def test_no_deliverable_at_all_is_still_reported() -> None:
    proposal = make_proposal([make_item(acceptance=["- [ ] 結果をまとめて報告する"])])
    result = make_service().validate_proposal(proposal, SUBREQUEST, [])
    assert any("1 つも" in p for p in result.problems)


def test_siblings_may_share_a_repo_file() -> None:
    """コーダーと QA が同じファイルを条文に書いても衝突にしない（outcome の重複検査は当てない）。"""
    proposal = make_proposal(
        [
            make_item(title="実装", acceptance=["- [ ] repo/src/app.py に関数を足す"]),
            make_item(title="試験", acceptance=["- [ ] repo/src/app.py の関数を試験で確かめる"]),
        ]
    )
    result = make_service().validate_proposal(proposal, SUBREQUEST, [])
    assert result.problems == []
