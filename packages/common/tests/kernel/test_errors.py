"""共通例外の基底（contracts/kernel.md §2）。"""

from __future__ import annotations

import pytest

from common.kernel.errors import (
    AppError,
    NotFoundError,
    UnavailableError,
    UnverifiedError,
    ViolationError,
)

FAMILIES: list[type[AppError]] = [NotFoundError, UnavailableError, UnverifiedError, ViolationError]


def test_app_error_holds_message() -> None:
    error = AppError("壊れた")
    assert error.message == "壊れた"
    assert str(error) == "壊れた"


def test_app_error_cause_defaults_to_none() -> None:
    assert AppError("壊れた").cause is None


def test_app_error_holds_cause() -> None:
    """`cause` に起因例外を保持できる（FR-016・約束 4）。"""
    original = ValueError("原因")
    error = AppError("壊れた", original)
    assert error.cause is original


@pytest.mark.parametrize("family", FAMILIES)
def test_families_inherit_app_error(family: type[AppError]) -> None:
    assert issubclass(family, AppError)


@pytest.mark.parametrize("family", FAMILIES)
def test_families_are_caught_by_app_error(family: type[AppError]) -> None:
    with pytest.raises(AppError):
        raise family("壊れた")


@pytest.mark.parametrize("family", FAMILIES)
def test_families_add_no_attributes(family: type[AppError]) -> None:
    """4 系統は追加属性を持たない（data-model.md §2）。"""
    assert family.__init__ is AppError.__init__


def test_there_are_exactly_four_families() -> None:
    assert len(FAMILIES) == 4
    assert {f.__name__ for f in FAMILIES} == {
        "NotFoundError",
        "UnavailableError",
        "UnverifiedError",
        "ViolationError",
    }
