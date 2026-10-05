"""全モジュールが共有する語彙（6 列挙）とラベル文字列の組立／分解。

正本は `docs/design/phase5/common/p5_common_data_model.mmd` の
`namespace common_kernel_enums_py`。6 列挙はいずれも `StrEnum` で **メンバ名 == 値**。

`Labels` は文字列操作だけを行い、値の妥当性（プロジェクトキーの命名規則・
note の値語彙・「残骸」「未回答」の判断）は扱わない（P5D-002・FR-009・FR-015）。
"""

from __future__ import annotations

from enum import StrEnum


class PersonaKey(StrEnum):
    """ペルソナのキー（DM-09）。"""

    planner = "planner"
    project_lead = "project_lead"
    system_engineer = "system_engineer"
    doc_lead = "doc_lead"
    architect = "architect"
    programmer = "programmer"
    analyst = "analyst"
    writer = "writer"
    general = "general"


class StageName(StrEnum):
    """段の名前（DM-08）。"""

    program = "program"
    project = "project"
    subrequest = "subrequest"
    child = "child"
    plain = "plain"


class ToolName(StrEnum):
    """Tool 登録名（D16-b・P5D-045）。"""

    read_issue = "read_issue"
    record_progress = "record_progress"
    list_input_files = "list_input_files"
    read_input_file = "read_input_file"
    list_outcome_files = "list_outcome_files"
    read_outcome_file = "read_outcome_file"
    escalate_to_human = "escalate_to_human"
    propose_breakdown = "propose_breakdown"
    write_outcome = "write_outcome"
    complete_issue = "complete_issue"
    run_command = "run_command"
    submit_review = "submit_review"
    list_repo_files = "list_repo_files"
    read_repo_file = "read_repo_file"
    write_repo_file = "write_repo_file"
    delete_repo_file = "delete_repo_file"


class KindLabel(StrEnum):
    """`kind` 次元の値。"""

    program = "program"
    request = "request"
    subrequest = "subrequest"
    child = "child"
    question = "question"


class StateLabel(StrEnum):
    """`state` 次元の値（排他）。"""

    working = "working"
    failing = "failing"


class LabelDim(StrEnum):
    """ラベル次元の接頭辞。"""

    kind = "kind"
    project = "project"
    persona = "persona"
    role = "role"
    state = "state"
    note = "note"


_SEPARATOR = ":"


class Labels:
    """ラベル文字列の組立と分解（`@staticmethod` だけの名前空間クラス）。

    インスタンス化しない。`__init__` を持たない。
    """

    @staticmethod
    def kind(value: KindLabel) -> str:
        """`kind:<value>` を組み立てる。"""
        return f"{LabelDim.kind}{_SEPARATOR}{value}"

    @staticmethod
    def project(project_key: str) -> str:
        """`project:<project_key>` を組み立てる。入力は検証しない。"""
        return f"{LabelDim.project}{_SEPARATOR}{project_key}"

    @staticmethod
    def persona(value: PersonaKey) -> str:
        """`persona:<value>` を組み立てる。"""
        return f"{LabelDim.persona}{_SEPARATOR}{value}"

    @staticmethod
    def role_executor() -> str:
        """`role:executor`（固定）を返す。

        本メソッドだけは宣言の段で本体を持つ。`ClaimFilter.labels` の既定値
        `frozenset({Labels.role_executor()})` が**クラス定義時に評価される**ため、
        未実装のままだと `common.interfaces.board` の import が落ちる（T015 の罠）。
        """
        return f"{LabelDim.role}{_SEPARATOR}executor"

    @staticmethod
    def state(value: StateLabel) -> str:
        """`state:<value>` を組み立てる。"""
        return f"{LabelDim.state}{_SEPARATOR}{value}"

    @staticmethod
    def note(name: str) -> str:
        """`note:<name>` を組み立てる。入力は検証しない。"""
        return f"{LabelDim.note}{_SEPARATOR}{name}"

    @staticmethod
    def parse(label: str) -> tuple[LabelDim, str] | None:
        """`<次元>:<値>` を分解する。既知 6 次元でなければ `None`（例外を送出しない）。

        最初の `:` だけで分割し、**値の妥当性は判断しない**（`kind:` は `(kind, "")`、
        `note:a:b` は `(note, "a:b")`）。`:` を含まない文字列・空文字・既知 6 次元に
        無い接頭辞はいずれも `None`。未知と判別したあとの扱いは呼び側の責務
        （P5D-002・FR-009・FR-015）。
        """
        prefix, separator, value = label.partition(_SEPARATOR)
        if not separator:
            return None
        try:
            dimension = LabelDim(prefix)
        except ValueError:
            return None
        return dimension, value
