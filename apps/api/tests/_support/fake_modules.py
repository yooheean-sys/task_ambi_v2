"""偽の契約・偽のモジュール登録・スタブ（配線テストと統合テストで共有する）。

`_support/` には `__init__.py` を置かない（`001` C-10 の実測。通常パッケージ化すると
`packages/common/tests/_support` と共存できず、リポジトリ全体の pytest が壊れる）。
本 feature には実在する feature モジュールが 1 つも無いため、登録を必要とする検証は
すべてここが用意する偽の登録で行う（research R-01・quickstart S7）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from api.wiring import ModuleRegistration, WiredModule

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from fastapi import APIRouter

    from api.wiring import LifespanFactory


class FakeContract(Protocol):
    """偽の契約の共通形。実装を見分けるためだけの 1 メソッドを持つ。"""

    def label(self) -> str:
        """この実装の由来（モジュール名、または override の印）。"""
        ...


class BoardContract(FakeContract, Protocol):
    """board 相当の偽の契約。"""


class VaultContract(FakeContract, Protocol):
    """vault 相当の偽の契約。"""


class SettingsContract(FakeContract, Protocol):
    """settings 相当の偽の契約。"""


class MonitorContract(FakeContract, Protocol):
    """monitor 相当の偽の契約。"""


class StagesContract(FakeContract, Protocol):
    """stages 相当の偽の契約。"""


class ExecutorContract(FakeContract, Protocol):
    """executor 相当の偽の契約。"""


class OrchestratorContract(FakeContract, Protocol):
    """orchestrator 相当の偽の契約。"""


class TriggerContract(FakeContract, Protocol):
    """trigger 相当の偽の契約。"""


class RepoContract(FakeContract, Protocol):
    """repo 相当の偽の契約（D25）。"""


class UnusedContract(FakeContract, Protocol):
    """どの偽モジュールも要求しない契約（余分な override の検証に使う）。"""


@dataclass(frozen=True, slots=True)
class StubImplementation:
    """契約実装のスタブ。構造的に `FakeContract` を満たす。"""

    origin: str

    def label(self) -> str:
        """この実装の由来。"""
        return self.origin


@dataclass(slots=True)
class CallRecorder:
    """組み立て口・後始末の呼び出しを記録する（SC-003・SC-004・FR-043・FR-044 の観測点）。"""

    builds: list[str] = field(default_factory=list[str])
    lifespan_starts: list[str] = field(default_factory=list[str])
    lifespan_stops: list[str] = field(default_factory=list[str])
    override_cleanups: list[str] = field(default_factory=list[str])
    seen_deps: dict[str, dict[type[object], object]] = field(
        default_factory=dict[str, dict[type[object], object]]
    )

    def build_count(self, name: str) -> int:
        """`name` の組み立て口が呼ばれた回数。"""
        return self.builds.count(name)


@dataclass(slots=True)
class StubWithCleanup:
    """後始末を持つ override 用のスタブ。**呼ばれないこと**を確かめるための罠（FR-043）。"""

    origin: str
    recorder: CallRecorder

    def label(self) -> str:
        """この実装の由来。"""
        return self.origin

    def lifespan(self) -> LifespanFactory:
        """配線がここに触れたら記録が残る（触れないのが正しい）。"""
        return make_lifespan(f"override:{self.origin}", self.recorder)


def make_lifespan(name: str, recorder: CallRecorder) -> LifespanFactory:
    """起動・終了を記録する後始末を作る。

    Args:
        name: 記録に残すモジュール名。
        recorder: 記録先。

    Returns:
        呼ぶたびに新しいコンテキストを返す `LifespanFactory`（再入可能。research R-06）。
    """

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[None]:
        recorder.lifespan_starts.append(name)
        try:
            yield
        finally:
            recorder.lifespan_stops.append(name)

    return lifespan


def make_registration(
    *,
    name: str,
    recorder: CallRecorder,
    requires: tuple[type[object], ...] = (),
    provides: tuple[type[object], ...] = (),
    returns: tuple[type[object], ...] | None = None,
    router: APIRouter | None = None,
    with_lifespan: bool = False,
    fail_with: Exception | None = None,
    has_ui_page: bool = False,
) -> ModuleRegistration:
    """偽のモジュール登録を 1 件組む。

    Args:
        name: モジュール名。
        recorder: 組み立て口の呼び出しと渡された写像の記録先。
        requires: 要求する契約。
        provides: 宣言する提供契約。
        returns: 組み立て口が**実際に**返す契約。`None` なら `provides` と同じ
            （食い違わせると `HandleContractMismatchError` の検証になる）。
        router: 公開経路。
        with_lifespan: 起動終了の後始末を持たせるか。
        fail_with: 組み立て口が送出する例外（`None` なら成功する）。
        has_ui_page: UI ページを持つ宣言（REG-8h の検証に使う）。

    Returns:
        組んだ `ModuleRegistration`。
    """
    actual = provides if returns is None else returns

    def build(deps: Mapping[type[object], object]) -> WiredModule:
        recorder.builds.append(name)
        recorder.seen_deps[name] = dict(deps)
        if fail_with is not None:
            raise fail_with
        return WiredModule(
            provides={contract: StubImplementation(name) for contract in actual},
            router=router,
            lifespan=make_lifespan(name, recorder) if with_lifespan else None,
        )

    return ModuleRegistration(
        name=name,
        requires=requires,
        provides=provides,
        build=build,
        has_router=router is not None,
        has_ui_page=has_ui_page,
    )


DESIGN_DEPENDENCY_LEVELS: tuple[tuple[str, ...], ...] = (
    ("board", "vault", "repo"),
    ("settings",),
    ("monitor",),
    ("stages", "executor"),
    ("orchestrator",),
    ("trigger",),
)
"""設計側の依存順（p4 の 9 モジュール）。導出順の**照合先**であり、同層は順序を持たない。"""

TOPOLOGY_MODULE_NAMES: tuple[str, ...] = tuple(
    name for level in DESIGN_DEPENDENCY_LEVELS for name in level
)
"""`make_topology()` が返す 9 件のモジュール名（設計側の並びのまま）。"""


def make_topology(recorder: CallRecorder) -> tuple[ModuleRegistration, ...]:
    """設計側の 9 モジュールを模した偽の登録集合を組む（依存関係だけを写す）。

    Args:
        recorder: 組み立て口の呼び出しの記録先。

    Returns:
        9 件の `ModuleRegistration`。**登録の並びは設計順ではなく辞書順**にしてあり、
        導出が入力の並びに依存していないことを確かめられる。
    """
    definitions: tuple[tuple[str, tuple[type[object], ...], tuple[type[object], ...]], ...] = (
        ("board", (), (BoardContract,)),
        ("executor", (MonitorContract,), (ExecutorContract,)),
        ("monitor", (SettingsContract,), (MonitorContract,)),
        ("orchestrator", (StagesContract, ExecutorContract), (OrchestratorContract,)),
        ("repo", (), (RepoContract,)),
        ("settings", (BoardContract, VaultContract), (SettingsContract,)),
        ("stages", (MonitorContract,), (StagesContract,)),
        ("trigger", (OrchestratorContract,), (TriggerContract,)),
        ("vault", (), (VaultContract,)),
    )
    return tuple(
        make_registration(name=name, recorder=recorder, requires=requires, provides=provides)
        for name, requires, provides in definitions
    )
