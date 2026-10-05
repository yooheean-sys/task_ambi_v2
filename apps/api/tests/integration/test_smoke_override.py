"""US3: 実配線のまま、有効 MOD 一覧の絞り込みと override でクラスタ外を差し替えられる。

検証行: CR-6・CR-7（US3 AC1／AC2・SC-003・SC-004）。
**手配線をしない**（`_support/composition.py` 経由で実際の `create_app` を呼ぶ。憲章 §8）。
"""

from __future__ import annotations

from _support.composition import ClientFactory, build_test_app, make_root_config
from _support.fake_modules import (
    BoardContract,
    CallRecorder,
    SettingsContract,
    StubImplementation,
    make_registration,
)


def test_cr6_modules_left_out_of_enabled_modules_are_never_built(
    client_factory: ClientFactory,
) -> None:
    """CR-6・US3 AC1・SC-004: 有効 MOD 一覧から外した登録の組み立て口の呼び出しが 0 回。"""
    recorder = CallRecorder()
    kept = make_registration(name="kept", recorder=recorder, provides=(BoardContract,))
    dropped = make_registration(name="dropped", recorder=recorder, provides=(SettingsContract,))

    with client_factory(
        enabled_modules=["kept"],
        config=make_root_config(),
        registrations=(kept, dropped),
    ) as client:
        body = client.get("/health").json()

    assert recorder.build_count("kept") == 1
    assert recorder.build_count("dropped") == 0, "外したモジュールが組み立てられている"
    assert body["modules"] == ["kept"], "/health は組み立て結果を返す（FR-017）"


def test_cr7_override_replaces_the_provider_and_reaches_the_consumer() -> None:
    """CR-7・US3 AC2・SC-003: 提供モジュールを外し override を渡すと利用側にその偽実装が渡る。"""
    recorder = CallRecorder()
    provider = make_registration(name="provider", recorder=recorder, provides=(BoardContract,))
    consumer = make_registration(
        name="consumer", recorder=recorder, requires=(BoardContract,), provides=(SettingsContract,)
    )
    fake = StubImplementation("fake-board")

    app = build_test_app(
        enabled_modules=["consumer"],
        config=make_root_config(),
        registrations=(provider, consumer),
        overrides={BoardContract: fake},
    )

    assert app.title == "task_ambi"
    assert recorder.build_count("provider") == 0, "override したのに提供モジュールが組み立てられた"
    assert recorder.build_count("consumer") == 1
    delivered = recorder.seen_deps["consumer"][BoardContract]
    assert delivered is fake, "利用側に渡ったのが override の偽実装ではない"


def test_override_implementation_is_included_in_provided() -> None:
    """data-model §5: `Wiring.provided` は override で与えた実装を**含む**。"""
    from api.wiring import build_modules

    recorder = CallRecorder()
    consumer = make_registration(name="consumer", recorder=recorder, requires=(BoardContract,))
    fake = StubImplementation("fake-board")

    wiring = build_modules(
        enabled_modules=["consumer"],
        config=make_root_config(),
        registrations=(consumer,),
        overrides={BoardContract: fake},
    )

    assert wiring.provided[BoardContract] is fake
    assert wiring.modules == ("consumer",)


def test_fr017_health_modules_do_not_include_override_names(
    client_factory: ClientFactory,
) -> None:
    """FR-017: `/health` の `modules` は組み立てられたモジュール名だけ。

    差し替え写像の鍵に対応するモジュール名は現れない。
    """
    recorder = CallRecorder()
    consumer = make_registration(name="consumer", recorder=recorder, requires=(BoardContract,))

    with client_factory(
        enabled_modules=["consumer"],
        config=make_root_config(),
        registrations=(consumer,),
        overrides={BoardContract: StubImplementation("fake-board")},
    ) as client:
        body = client.get("/health").json()

    assert body["modules"] == ["consumer"]
