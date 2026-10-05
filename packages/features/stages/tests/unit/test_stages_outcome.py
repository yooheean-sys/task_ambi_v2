"""完了トークンの抽出（`extract_outcome_tokens`。IF-47・stages-F05・US4）。

正本は [contracts/proposal-gate.md](../../../../specs/006-stages/contracts/proposal-gate.md) §3。
**丸めない**（正準形でない書き方も原文のまま返す）・**例外を 1 つも送出しない**。
"""

from __future__ import annotations

import pytest
from conftest import make_service

from stages.models import outcome_filename

EXTRACT_CASES: tuple[tuple[str, list[str]], ...] = (
    ("- [ ] outcome/report.md を書く", ["outcome/report.md"]),
    ("成果物は outcome/a.md です。", ["outcome/a.md"]),
    ("outcome/plan.txt", ["outcome/plan.txt"]),
    (
        "outcome/a.md と outcome/b.md の両方",
        ["outcome/a.md", "outcome/b.md"],
    ),
    ("../outcome/x.md に置く", ["../outcome/x.md"]),
    ("/home/u/outcome/x.md に置く", ["/home/u/outcome/x.md"]),
    ("outcome/sub/x.md に置く", ["outcome/sub/x.md"]),
    ("成果物はまだ決めていない", []),
    ("", []),
    ("\n\n\n", []),
    ("先頭（outcome/x.md）末尾", ["outcome/x.md"]),
    ("outcome/x.md、次に outcome/y.md。", ["outcome/x.md", "outcome/y.md"]),
    ("outcome/a.md\noutcome/a.md\noutcome/b.md", ["outcome/a.md", "outcome/b.md"]),
    ("結果を outcome/a.md にまとめる", ["outcome/a.md"]),
    ("~/outcome/x.md", ["~/outcome/x.md"]),
)

EXTRACT_IDS = (
    "US4-AC1-canonical-checkbox",
    "US4-AC1-canonical-inline",
    "US4-AC1-canonical-bare",
    "US4-AC2-multiple-in-order",
    "US4-AC3-relative-prefix",
    "US4-AC3-absolute-path",
    "US4-AC3-nested-dir",
    "US4-AC4-no-declaration",
    "US4-AC5-empty-string",
    "US4-AC5-blank-lines",
    "EDGE-fullwidth-parens",
    "EDGE-punctuation-terminates",
    "US4-dedupe-in-order",
    "EDGE-embedded-in-sentence",
    "US4-AC3-tilde-prefix",
)

FILENAME_CASES: tuple[tuple[str, str | None], ...] = (
    ("outcome/report.md", "report.md"),
    ("  outcome/report.md  ", "report.md"),
    ("../outcome/x.md", None),
    ("/home/u/outcome/x.md", None),
    ("outcome/sub/x.md", None),
    ("outcome/sub\\x.md", None),
    ("outcome/", None),
    ("outcome/.", None),
    ("outcome/..", None),
    ("outcome/~x.md", None),
    ("outcome/C:x.md", None),
    ("results/x.md", None),
    ("", None),
)

FILENAME_IDS = (
    "canonical",
    "canonical-with-spaces",
    "reject-relative-prefix",
    "reject-absolute",
    "reject-slash-in-rest",
    "reject-backslash-in-rest",
    "reject-empty-rest",
    "reject-dot",
    "reject-dotdot",
    "reject-tilde-head",
    "reject-drive-letter",
    "reject-other-prefix",
    "reject-empty-token",
)


@pytest.mark.parametrize(("acceptance", "expected"), EXTRACT_CASES, ids=EXTRACT_IDS)
def test_extract_outcome_tokens(acceptance: str, expected: list[str]) -> None:
    assert make_service().extract_outcome_tokens(acceptance) == expected


@pytest.mark.parametrize(("token", "expected"), FILENAME_CASES, ids=FILENAME_IDS)
def test_outcome_filename(token: str, expected: str | None) -> None:
    """US4 AC-6: 正準形でなければ `None`（**丸めない**）。"""
    assert outcome_filename(token) == expected


def test_extract_never_raises_on_large_input() -> None:
    """US4 AC-5・SC-007: 数千行の入力でも例外 0 件。"""
    text = "\n".join(f"- [ ] outcome/f{i}.md を書く" for i in range(3000))
    assert len(make_service().extract_outcome_tokens(text)) == 3000
