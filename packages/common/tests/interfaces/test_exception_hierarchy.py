"""SC-004: 契約例外 15 件が `except AppError` で 15/15 捕まり、`RunHalted` だけが素通りする。

14・15 件目は D25 の repo（`RepoNotFound`・`RepoPathViolation`）。

実ライブラリ（openai・httpx・crewai）は**再実測しない**。凍結 PoC
`P1_計測基盤報告.md:213`（Z-6）を出典として引くのみ（common はこれらを import しない＝FR-025）。
ここでは同形の自前リトライ模型で素通りを確かめる。
"""

from __future__ import annotations

import pytest
from _support.declarations import declared_exceptions

from common.interfaces.board import BoardUnavailable, BoardWriteUnverified, IssueNotFound
from common.interfaces.executor import ExecutorFailure
from common.interfaces.monitor import FailureKind, MappingUnverified, RunHalted
from common.interfaces.orchestrator import KickoffBusy
from common.interfaces.repo import RepoNotFound, RepoPathViolation
from common.interfaces.settings import MemberNotFound, ProjectNotFound, UnknownCommand
from common.interfaces.stages import UnknownPersona, UnknownStage
from common.interfaces.vault import VaultNotFound, VaultPathViolation
from common.kernel.errors import (
    AppError,
    NotFoundError,
    UnavailableError,
    UnverifiedError,
    ViolationError,
)

# contracts/kernel.md §2 の系統表。ここが正本。
CONTRACT_EXCEPTIONS: list[tuple[AppError, type[AppError]]] = [
    (IssueNotFound("無い", "bd-1"), NotFoundError),
    (ProjectNotFound("無い"), NotFoundError),
    (MemberNotFound("無い"), NotFoundError),
    (UnknownCommand("無い"), NotFoundError),
    (VaultNotFound("無い", "demo/a.md", None), NotFoundError),
    (UnknownStage("無い"), NotFoundError),
    (UnknownPersona("無い"), NotFoundError),
    (RepoNotFound("無い", "src/a.py"), NotFoundError),
    (BoardUnavailable("不達", ["bd", "show"], 1, "err", 3), UnavailableError),
    (BoardWriteUnverified("不一致", "bd-1", "labels"), UnverifiedError),
    (MappingUnverified("不一致", "bd-1", "closed", "open"), UnverifiedError),
    (VaultPathViolation("違反", "outside_root", "../etc"), ViolationError),
    (ExecutorFailure("失敗", "build", "crewai error"), ViolationError),
    (KickoffBusy("実行中", "k-1"), ViolationError),
    (RepoPathViolation("違反", "outside_repo", "../etc"), ViolationError),
]

FAMILY_SIZES = {NotFoundError: 8, UnavailableError: 1, UnverifiedError: 2, ViolationError: 4}


def test_there_are_fifteen_contract_exceptions() -> None:
    """母数 15 は `RunHalted` を含まない（16 件目は階層外）。"""
    assert len(CONTRACT_EXCEPTIONS) == 15
    declared = declared_exceptions()
    assert len(declared) == 16
    assert set(declared) - {type(e).__name__ for e, _ in CONTRACT_EXCEPTIONS} == {"RunHalted"}


def test_every_declared_contract_exception_is_covered() -> None:
    """手書き一覧が宣言の全件を覆うこと（17 件目が増えたら落ちる）。"""
    covered = {type(e).__name__ for e, _ in CONTRACT_EXCEPTIONS} | {"RunHalted"}
    assert covered == set(declared_exceptions())


@pytest.mark.parametrize(
    "error", [e for e, _ in CONTRACT_EXCEPTIONS], ids=lambda e: type(e).__name__
)
def test_caught_by_app_error(error: AppError) -> None:
    with pytest.raises(AppError):
        raise error


@pytest.mark.parametrize(
    ("error", "family"), CONTRACT_EXCEPTIONS, ids=lambda v: getattr(v, "__name__", type(v).__name__)
)
def test_family_assignment(error: AppError, family: type[AppError]) -> None:
    assert isinstance(error, family)
    others = [f for f in FAMILY_SIZES if f is not family]
    assert not any(isinstance(error, other) for other in others)


def test_family_sizes_match_the_contract_table() -> None:
    for family, size in FAMILY_SIZES.items():
        assert sum(1 for _, f in CONTRACT_EXCEPTIONS if f is family) == size


# --- RunHalted（階層外） ----------------------------------------------------


def _halted() -> RunHalted:
    return RunHalted(FailureKind.limit_requests, 41.0, 40.0, 41)


def test_run_halted_is_not_an_app_error() -> None:
    assert not isinstance(_halted(), AppError)
    assert not issubclass(RunHalted, Exception)


def test_run_halted_is_not_caught_by_except_exception() -> None:
    caught = 0
    with pytest.raises(RunHalted):
        try:
            raise _halted()
        except Exception:  # noqa: BLE001  # 素通りすることを確かめるための意図的な広い捕捉
            caught += 1
    assert caught == 0


def test_run_halted_passes_through_a_nested_retry_model() -> None:
    """`except Exception` で捕らえて再試行する多段の模型を素通りし、再試行回数が 0。"""
    attempts = 0

    def inner() -> None:
        nonlocal attempts
        attempts += 1
        raise _halted()

    def retry_once() -> None:
        for _ in range(3):
            try:
                inner()
            except Exception:  # noqa: BLE001  # ライブラリのリトライ機構を模す
                continue
            return

    def retry_outer() -> None:
        for _ in range(3):
            try:
                retry_once()
            except Exception:  # noqa: BLE001  # 多段ネスト
                continue
            return

    with pytest.raises(RunHalted):
        retry_outer()
    assert attempts == 1


def test_run_halted_holds_its_attributes() -> None:
    error = _halted()
    assert (error.kind, error.measured, error.limit, error.request_no) == (
        FailureKind.limit_requests,
        41.0,
        40.0,
        41,
    )


def test_run_halted_has_no_message_or_cause() -> None:
    """`AppError` を継承しない帰結（`message`・`cause` を持たない）。"""
    error = _halted()
    assert not hasattr(error, "message")
    assert not hasattr(error, "cause")


# --- 追加属性（data-model.md §5〜11） ---------------------------------------

EXPECTED_ATTRS: dict[str, dict[str, object]] = {
    "IssueNotFound": {"issue_id": "bd-1"},
    "BoardUnavailable": {"argv": ["bd", "show"], "rc": 1, "stderr_head": "err", "attempts": 3},
    "BoardWriteUnverified": {"issue_id": "bd-1", "mismatch": "labels"},
    "MappingUnverified": {"issue_id": "bd-1", "expected": "closed", "actual": "open"},
    "VaultNotFound": {"rel_path": "demo/a.md", "role": None},
    "VaultPathViolation": {"kind": "outside_root", "detail": "../etc"},
    "ExecutorFailure": {"phase": "build", "reason": "crewai error"},
    "KickoffBusy": {"current_kickoff_id": "k-1"},
}

NO_EXTRA_ATTRS = (
    "ProjectNotFound",
    "MemberNotFound",
    "UnknownCommand",
    "UnknownStage",
    "UnknownPersona",
)


@pytest.mark.parametrize(
    "error", [e for e, _ in CONTRACT_EXCEPTIONS], ids=lambda e: type(e).__name__
)
def test_contract_exception_attributes(error: AppError) -> None:
    for name, value in EXPECTED_ATTRS.get(type(error).__name__, {}).items():
        assert getattr(error, name) == value


def test_exceptions_without_extra_attributes_add_none() -> None:
    """設計が属性を挙げていない 5 件は属性を足さない（R-16）。"""
    assert len(NO_EXTRA_ATTRS) == 5
    for error, _ in CONTRACT_EXCEPTIONS:
        if type(error).__name__ in NO_EXTRA_ATTRS:
            assert vars(error) == {"message": error.message, "cause": error.cause}


@pytest.mark.parametrize(
    "error", [e for e, _ in CONTRACT_EXCEPTIONS], ids=lambda e: type(e).__name__
)
def test_contract_exceptions_carry_message(error: AppError) -> None:
    assert error.message != ""
