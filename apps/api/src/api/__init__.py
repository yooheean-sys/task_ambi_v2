"""FastAPI 起動エントリのパッケージ（composition root）。apps 向けのエクスポート点。

`main.py` が実行エントリ、`app.py`／`wiring.py`／`registry.py` が組み立てを担う。
モジュールを 1 つ足すときに編集するのは `registry.py` の 1 箇所だけ（FR-007・SC-005）。
"""

from api.app import create_app
from api.exceptions import (
    CircularDependencyError,
    ConfigurationError,
    DuplicateModuleError,
    DuplicateProviderError,
    ExtraneousOverrideError,
    HandleContractMismatchError,
    StartupError,
    UnknownModuleError,
    UnsatisfiedContractError,
    WiringError,
)
from api.wiring import (
    LifespanFactory,
    ModuleFactory,
    ModuleRegistration,
    ProtocolOverrides,
    WiredModule,
    Wiring,
    build_modules,
    require,
)

__all__ = [
    "CircularDependencyError",
    "ConfigurationError",
    "DuplicateModuleError",
    "DuplicateProviderError",
    "ExtraneousOverrideError",
    "HandleContractMismatchError",
    "LifespanFactory",
    "ModuleFactory",
    "ModuleRegistration",
    "ProtocolOverrides",
    "StartupError",
    "UnknownModuleError",
    "UnsatisfiedContractError",
    "WiredModule",
    "Wiring",
    "WiringError",
    "build_modules",
    "create_app",
    "require",
]
