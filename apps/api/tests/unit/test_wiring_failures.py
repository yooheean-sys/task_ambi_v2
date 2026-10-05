"""配線の不備がすべて**起動時に**中断することの検証。

US2 の分（本ファイル前半）: CR-3・CR-4・CR-5・CR-12。
US4 の分（本ファイル後半）: CR-8・CR-9・CR-10・CR-14。
いずれも `build_modules()` / `create_app()` の**呼び出しそのもの**が例外になる
（TestClient に入る前。quickstart S4）。
"""

from __future__ import annotations

import pytest
from _support.composition import build_test_app, make_root_config
from _support.fake_modules import (
    BoardContract,
    CallRecorder,
    MonitorContract,
    SettingsContract,
    StubImplementation,
    StubWithCleanup,
    UnusedContract,
    VaultContract,
    make_registration,
)

from api.exceptions import (
    CircularDependencyError,
    DuplicateModuleError,
    DuplicateProviderError,
    ExtraneousOverrideError,
    UnknownModuleError,
    UnsatisfiedContractError,
)
from api.wiring import build_modules, contract_label


def test_cr3_two_party_cycle_names_both_parties() -> None:
    """CR-3・US2 AC3・FR-010: 相互依存 2 者で本文に**両方**の名前が出る。"""
    recorder = CallRecorder()
    left = make_registration(
        name="left", recorder=recorder, requires=(VaultContract,), provides=(BoardContract,)
    )
    right = make_registration(
        name="right", recorder=recorder, requires=(BoardContract,), provides=(VaultContract,)
    )

    with pytest.raises(CircularDependencyError) as caught:
        build_modules(
            enabled_modules=["left", "right"],
            config=make_root_config(),
            registrations=(left, right),
        )

    message = str(caught.value)
    assert "left" in message
    assert "right" in message
    assert contract_label(BoardContract) in message
    assert contract_label(VaultContract) in message
    assert recorder.builds == [], "輪は組み立てを 1 つも始める前に落ちること（contracts §3-8）"


def test_cr4_three_party_cycle_is_detected() -> None:
    """CR-4: 3 者以上の輪でも検出され、当事者全員が本文に出る。"""
    recorder = CallRecorder()
    first = make_registration(
        name="first", recorder=recorder, requires=(MonitorContract,), provides=(BoardContract,)
    )
    second = make_registration(
        name="second", recorder=recorder, requires=(BoardContract,), provides=(VaultContract,)
    )
    third = make_registration(
        name="third", recorder=recorder, requires=(VaultContract,), provides=(MonitorContract,)
    )

    with pytest.raises(CircularDependencyError) as caught:
        build_modules(
            enabled_modules=["first", "second", "third"],
            config=make_root_config(),
            registrations=(first, second, third),
        )

    message = str(caught.value)
    for name in ("first", "second", "third"):
        assert name in message, f"輪の当事者 {name} が本文に出ていない"
    assert recorder.builds == []


def test_cr5_unsatisfied_contract_reports_contract_requester_and_remedy() -> None:
    """CR-5・US2 AC4・FR-011・SC-006: 契約・要求元・充足手段の 3 つが本文に出る。"""
    recorder = CallRecorder()
    consumer = make_registration(
        name="consumer", recorder=recorder, requires=(BoardContract,), provides=(SettingsContract,)
    )

    with pytest.raises(UnsatisfiedContractError) as caught:
        build_modules(
            enabled_modules=["consumer"], config=make_root_config(), registrations=(consumer,)
        )

    message = str(caught.value)
    assert contract_label(BoardContract) in message, "欠けている契約が出ていない"
    assert "consumer" in message, "要求元モジュールが出ていない"
    assert "overrides" in message, "充足手段（override を渡す）が出ていない"
    assert "有効 MOD 一覧" in message, "充足手段（有効化する）が出ていない"
    assert recorder.builds == []


def test_contract_label_uses_module_and_qualname() -> None:
    """contracts §4: 契約の型は `<module>.<qualname>` で表示する。"""
    assert contract_label(BoardContract) == "_support.fake_modules.BoardContract"


def test_cr12_failure_during_build_does_not_roll_back_and_propagates() -> None:
    """CR-12・FR-044: 途中で例外が出ても既に組み立てた分の後始末は 0 回、例外はそのまま伝播。"""
    recorder = CallRecorder()
    failure = RuntimeError("組み立て口が落ちた")
    provider = make_registration(
        name="provider", recorder=recorder, provides=(BoardContract,), with_lifespan=True
    )
    breaking = make_registration(
        name="zbreaking", recorder=recorder, requires=(BoardContract,), fail_with=failure
    )

    with pytest.raises(RuntimeError) as caught:
        build_modules(
            enabled_modules=["provider", "zbreaking"],
            config=make_root_config(),
            registrations=(provider, breaking),
        )

    assert caught.value is failure, "例外は包み直さずそのまま伝播する"
    assert recorder.builds == ["provider", "zbreaking"]
    assert recorder.lifespan_starts == []
    assert recorder.lifespan_stops == [], "巻き戻し（後始末の呼び出し）が起きている"


def test_cr8_extraneous_override_is_rejected() -> None:
    """CR-8・FR-045: 有効化されたどのモジュールも要求しない鍵の override で中断する。"""
    recorder = CallRecorder()
    provider = make_registration(name="provider", recorder=recorder, provides=(BoardContract,))
    consumer = make_registration(name="consumer", recorder=recorder, requires=(BoardContract,))

    with pytest.raises(ExtraneousOverrideError) as caught:
        build_modules(
            enabled_modules=["provider", "consumer"],
            config=make_root_config(),
            registrations=(provider, consumer),
            overrides={UnusedContract: StubImplementation("override")},
        )

    message = str(caught.value)
    assert contract_label(UnusedContract) in message
    assert contract_label(BoardContract) in message, "要求されている契約の一覧が出ていない"
    assert recorder.builds == []


def test_cr9_override_of_an_enabled_providers_contract_is_rejected() -> None:
    """CR-9・FR-046: 有効モジュールが提供する契約への override は二重供給として中断する。"""
    recorder = CallRecorder()
    provider = make_registration(name="provider", recorder=recorder, provides=(BoardContract,))
    consumer = make_registration(name="consumer", recorder=recorder, requires=(BoardContract,))

    with pytest.raises(DuplicateProviderError) as caught:
        build_modules(
            enabled_modules=["provider", "consumer"],
            config=make_root_config(),
            registrations=(provider, consumer),
            overrides={BoardContract: StubImplementation("override")},
        )

    message = str(caught.value)
    assert contract_label(BoardContract) in message
    assert "provider" in message, "提供モジュール名が出ていない"
    assert "有効 MOD 一覧から外す" in message, "充足手段が出ていない"


def test_cr10_unknown_module_name_is_rejected() -> None:
    """CR-10・US4 AC4・FR-012: 登録の無い名前を含む有効 MOD 一覧で中断する。"""
    recorder = CallRecorder()
    provider = make_registration(name="provider", recorder=recorder, provides=(BoardContract,))

    with pytest.raises(UnknownModuleError) as caught:
        build_modules(
            enabled_modules=["boad"], config=make_root_config(), registrations=(provider,)
        )

    message = str(caught.value)
    assert "boad" in message
    assert "provider" in message, "既知の登録名の一覧が出ていない"
    assert "registry.py" in message, "充足手段が出ていない"


def test_cr14_duplicate_module_is_rejected_before_the_duplicate_provider_check() -> None:
    """CR-14・FR-049: 有効 MOD 一覧の重複が**二重供給の検査より先に**落ちる。"""
    recorder = CallRecorder()
    provider = make_registration(name="provider", recorder=recorder, provides=(BoardContract,))

    with pytest.raises(DuplicateModuleError) as caught:
        build_modules(
            enabled_modules=["provider", "provider"],
            config=make_root_config(),
            registrations=(provider,),
        )

    assert not isinstance(caught.value, DuplicateProviderError)
    message = str(caught.value)
    assert "provider" in message
    assert "enabled_modules" in message, "充足手段が出ていない"


def test_create_app_itself_raises_before_any_client_is_built() -> None:
    """US4 の Independent Test: 失敗は `create_app()` の呼び出しそのもので観測できる。"""
    recorder = CallRecorder()
    consumer = make_registration(name="consumer", recorder=recorder, requires=(BoardContract,))

    with pytest.raises(UnsatisfiedContractError):
        build_test_app(
            enabled_modules=["consumer"],
            config=make_root_config(),
            registrations=(consumer,),
        )


def test_override_with_cleanup_is_never_touched_by_the_wiring() -> None:
    """FR-043 の構造的な担保: override 写像の値に配線が触れない（後始末の記録が 0）。"""
    recorder = CallRecorder()
    consumer = make_registration(name="consumer", recorder=recorder, requires=(BoardContract,))

    build_modules(
        enabled_modules=["consumer"],
        config=make_root_config(),
        registrations=(consumer,),
        overrides={BoardContract: StubWithCleanup("fake-board", recorder)},
    )

    assert recorder.lifespan_starts == []
    assert recorder.lifespan_stops == []
    assert recorder.override_cleanups == []
