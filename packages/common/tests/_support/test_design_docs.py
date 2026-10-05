"""`design_docs.py` の正規化規則そのものを固定するテスト（plan.md (viii)）。

規則が壊れると SC-001／SC-002／SC-005 の突合が静かに緩むため、規則側にもテストを置く。
"""

from __future__ import annotations

from pathlib import Path

from _support.design_docs import (
    DATETIME_ATTRS,
    LITERAL_ATTRS,
    Attribute,
    count_args,
    enumerations,
    exceptions,
    fold_contract_rows,
    load_contract_rows,
    load_interface_classes,
    load_kernel_enums,
    parse_attribute_line,
    passing_types,
    return_type,
    split_exceptions,
)


def _names(attrs: list[Attribute]) -> list[str]:
    return [a.name for a in attrs]


# --- 規則 1: `/` 連記の展開 ------------------------------------------------


def test_rule1_slash_expands_to_multiple_attributes() -> None:
    attrs = parse_attribute_line("+created_at / updated_at", "Issue")
    assert _names(attrs) == ["created_at", "updated_at"]


def test_rule1_slash_keeps_per_segment_type() -> None:
    attrs = parse_attribute_line("+halted bool / halted_reason FailureKind | None", "CrewOutcome")
    assert _names(attrs) == ["halted", "halted_reason"]
    assert attrs[0].type_text == "bool"
    assert attrs[1].type_text == "FailureKind | None"


def test_rule1_path_like_slash_is_not_split() -> None:
    """空白を伴わない `/`（パス表記）で分割しない。`_archive` を属性にしない。"""
    attrs = parse_attribute_line(
        "+archived_to str | None outcome/_archive/<filename>.<stamp>", "WrittenPath"
    )
    assert _names(attrs) == ["archived_to"]


# --- 規則 2: 列挙値は大文字小文字を問わない --------------------------------


def test_rule2_uppercase_enum_values_are_read(design_root: Path) -> None:
    classes = load_interface_classes(design_root)
    grade = enumerations(classes)["Grade"]
    assert grade.values == ("F", "W", "I")


# --- 規則 3: 識別子に一致しない行は除外 ------------------------------------


def test_rule3_note_line_is_excluded() -> None:
    attrs = parse_attribute_line("+種別（人間指示／回答／封筒）は持たない（P5D-005）", "Comment")
    assert attrs == []


def test_rule3_identifier_prefix_is_used() -> None:
    """全角括弧が続いても行頭の識別子接頭辞を名前として拾う。"""
    attrs = parse_attribute_line(
        "+task_description（TaskTemplate＋｛変数｝充填済み D16-c）", "PromptBundle"
    )
    assert _names(attrs) == ["task_description"]


# --- 規則 4: `を継承` は継承宣言 -------------------------------------------


def test_rule4_inheritance_is_not_an_attribute(design_root: Path) -> None:
    classes = load_interface_classes(design_root)
    issue_not_found = exceptions(classes)["IssueNotFound"]
    assert issue_not_found.base == "NotFoundError"
    assert _names(list(issue_not_found.attributes)) == ["issue_id"]


def test_rule4_run_halted_has_no_base(design_root: Path) -> None:
    """`RunHalted` は階層外（`BaseException` 直系）なので継承宣言を持たない。"""
    classes = load_interface_classes(design_root)
    assert exceptions(classes)["RunHalted"].base is None


# --- 規則 5: 既定 X ／ 任意 ／ 印なし（research R-17） ----------------------


def test_rule5_default_mark() -> None:
    attrs = parse_attribute_line("+priority int 既定 2", "IssueSpec")
    assert (attrs[0].required, attrs[0].default_text) == (False, "2")


def test_rule5_default_mark_with_trailing_note() -> None:
    attrs = parse_attribute_line("+min_items 既定 1（config で段別上書き）", "StageSpec")
    assert (attrs[0].required, attrs[0].default_text) == (False, "1")


def test_rule5_optional_mark_applies_to_whole_line() -> None:
    """印は行全体に掛かる。`/` 連記の先頭 2 件も省略可になる。"""
    attrs = parse_attribute_line(
        "+max_iter / max_rpm / max_execution_time 任意（未指定は executor config 既定）", "Member"
    )
    assert _names(attrs) == ["max_iter", "max_rpm", "max_execution_time"]
    assert [a.required for a in attrs] == [False, False, False]
    assert [a.default_text for a in attrs] == [None, None, None]


def test_rule5_no_mark_is_required() -> None:
    """`T | None` でも印が無ければ必須（`ClaimFilter.project_key`）。"""
    attrs = parse_attribute_line("+project_key str | None → project:<key> を追加", "ClaimFilter")
    assert attrs[0].required is True


def test_rule5_optional_union_none_and_no_mark_stays_required(design_root: Path) -> None:
    """`TaskTemplate.tools` は `| None` だが印が無いので必須のまま（R-17）。"""
    classes = load_interface_classes(design_root)
    tools = next(a for a in passing_types(classes)["TaskTemplate"].attributes if a.name == "tools")
    assert tools.required is True


# --- 型の読み取り ----------------------------------------------------------


def test_value_enumeration_is_not_read_as_a_type() -> None:
    """`global | project_key` は値域であって型ではない（`LimitPolicy.scope`）。"""
    attrs = parse_attribute_line("+scope global | project_key", "LimitPolicy")
    assert attrs[0].type_text is None


def test_mermaid_generics_are_normalized() -> None:
    attrs = parse_attribute_line("+role_dirs dict~PersonaKey,str~ メンバ全員分", "ProjectFolders")
    assert attrs[0].type_text == "dict[PersonaKey, str]"


def test_japanese_none_marker_becomes_optional_union() -> None:
    attrs = parse_attribute_line("+persona PersonaKey | 空", "ProposalItem")
    assert attrs[0].type_text == "PersonaKey | None"


def test_untyped_attribute_reports_no_type() -> None:
    """mmd が型を書いていない属性は `None` を返す（R-15 規則 1 は機械的に導けない）。"""
    attrs = parse_attribute_line("+generation スナップショット世代", "TaskTemplates")
    assert attrs[0].type_text is None


def test_named_datetime_attrs_get_datetime(design_root: Path) -> None:
    """R-15 規則 2 の 11 件は名指しで `datetime` になる。"""
    assert len(DATETIME_ATTRS) == 11
    classes = load_interface_classes(design_root)
    for class_name, attr_name in DATETIME_ATTRS:
        attr = next(a for a in classes[class_name].attributes if a.name == attr_name)
        assert attr.type_text == "datetime", f"{class_name}.{attr_name}"


def test_named_literal_attrs_get_literal(design_root: Path) -> None:
    """R-15 規則 3 の 8 件は名指しで `Literal[...]` になる（8 件目は D25）。"""
    assert len(LITERAL_ATTRS) == 8
    classes = load_interface_classes(design_root)
    ref = next(a for a in classes["IssueRef"].attributes if a.name == "dependency_type")
    assert ref.type_text == 'Literal["blocks", "parent-child"]'
    phase = next(a for a in classes["ExecutorFailure"].attributes if a.name == "phase")
    assert phase.type_text == 'Literal["build", "kickoff"]'


# --- 契約台帳 csv ----------------------------------------------------------


def test_exception_column_strips_nested_note() -> None:
    """注記の中の `／` で分割しない（IF-39）。"""
    cell = "MappingUnverified（写像が読めない／bd 不達→停止痕跡を残して停止 P3）"
    assert split_exceptions(cell) == ("MappingUnverified",)


def test_exception_column_dash_is_empty() -> None:
    assert split_exceptions("—") == ()


def test_arg_count_ignores_note_parentheses() -> None:
    """注記の中の `・` を引数に数えない（IF-11）。"""
    assert count_args("IssueSpec（title・summary・labels・parent・acceptance）・actor") == 2


def test_return_type_takes_leading_expression() -> None:
    claim = "Issue | None（bd ready --claim 相当。state=working を付与）"
    argv = "argv: list[str]（DM-26。shell=False・引数は AI に渡させない）"
    assert return_type(claim) == "Issue | None"
    assert return_type(argv) == "list[str]"
    assert return_type("issue_id（起票は orchestrator の 1 箇所のみ）") == "str"


def test_contract_fold_is_computed_from_the_ledger(design_root: Path) -> None:
    """56 行 → 47 メソッドの畳み込みを台帳から機械的に再計算する（手書き一覧に依存しない）。"""
    rows = load_contract_rows(design_root)
    assert len(rows) == 56
    folded = fold_contract_rows(rows)
    assert len(folded) == 47
    assert len({(m.provider, m.protocol) for m in folded}) == 16


# --- 母数 ------------------------------------------------------------------


def test_design_totals_match_the_data_model(design_root: Path) -> None:
    classes = load_interface_classes(design_root)
    assert len(passing_types(classes)) == 38
    assert len(enumerations(classes)) == 9
    assert len(exceptions(classes)) == 16
    assert sum(len(c.attributes) for c in passing_types(classes).values()) == 206


def test_kernel_enums_are_six(design_root: Path) -> None:
    assert len(load_kernel_enums(design_root)) == 6
