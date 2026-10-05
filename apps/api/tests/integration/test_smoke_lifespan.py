"""US3: 起動終了の後始末は「組み立てられた Handle が持つもの」だけを通る。

検証行: CR-13・H-7（US3 AC3／AC4／AC5・FR-043・FR-038）。
"""

from __future__ import annotations

import pathlib
import tomllib
from typing import Any

from _support.composition import ClientFactory, make_root_config
from _support.fake_modules import (
    BoardContract,
    CallRecorder,
    SettingsContract,
    StubWithCleanup,
    make_registration,
)


def test_cr13_only_built_lifespans_run_and_override_cleanup_is_never_called(
    client_factory: ClientFactory,
) -> None:
    """CR-13・US3 AC3・FR-043: 組み立てた後始末だけが通り、override の後始末は 0 回。"""
    recorder = CallRecorder()
    first = make_registration(
        name="first", recorder=recorder, provides=(SettingsContract,), with_lifespan=True
    )
    second = make_registration(
        name="second",
        recorder=recorder,
        requires=(BoardContract, SettingsContract),
        with_lifespan=True,
    )

    with client_factory(
        enabled_modules=["first", "second"],
        config=make_root_config(),
        registrations=(first, second),
        overrides={BoardContract: StubWithCleanup("fake-board", recorder)},
    ) as client:
        assert client.get("/health").status_code == 200
        assert recorder.lifespan_starts == ["first", "second"], "組み立て順に積まれていない"
        assert recorder.lifespan_stops == []

    assert recorder.lifespan_stops == ["second", "first"], "終了が積んだ逆順でない"
    assert recorder.override_cleanups == [], "override の後始末が呼ばれた（FR-043 違反）"


def test_lifespan_factory_is_reentrant(client_factory: ClientFactory) -> None:
    """research R-06: 同じ登録で 2 回 `TestClient` に入っても後始末が使い回されない。"""
    recorder = CallRecorder()
    resident = make_registration(name="resident", recorder=recorder, with_lifespan=True)

    for _ in range(2):
        with client_factory(
            enabled_modules=["resident"],
            config=make_root_config(),
            registrations=(resident,),
        ) as client:
            assert client.get("/health").status_code == 200

    assert recorder.lifespan_starts == ["resident", "resident"]
    assert recorder.lifespan_stops == ["resident", "resident"]


def test_h7_env_label_from_the_injected_config_appears_in_health(
    client_factory: ClientFactory,
) -> None:
    """H-7・US3 AC4・FR-004: ルート設定を差し替えると `/health` の `env_label` が変わる。"""
    with client_factory(
        enabled_modules=[],
        config=make_root_config(app_name="task_ambi_x", env_label="test"),
        registrations=(),
    ) as client:
        body = client.get("/health").json()

    assert body["env_label"] == "test"
    assert body["app"] == "task_ambi_x"


def test_us3_ac5_integration_tests_are_collected_by_the_default_test_run() -> None:
    """US3 AC5・FR-038: `integration/` が `uv run pytest` の既定収集の範囲に入っている。"""
    here = pathlib.Path(__file__).resolve()
    root = next(
        parent
        for parent in here.parents
        if (parent / "pyproject.toml").is_file()
        and "workspace"
        in tomllib.loads((parent / "pyproject.toml").read_text(encoding="utf-8"))
        .get("tool", {})
        .get("uv", {})
    )
    settings: dict[str, Any] = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]
    testpaths: list[str] = [str(entry) for entry in settings["testpaths"]]

    assert any(here.is_relative_to(root / entry) for entry in testpaths), (
        f"本ファイルが既定の testpaths({testpaths}) の外にある"
    )
    assert here.parent.name == "integration"
