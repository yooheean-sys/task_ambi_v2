# pyright: reportUnnecessaryTypeIgnoreComment=true
"""SC-010: `SettingsReader`・`MemberResolver`・`Authorizer`・`CommandRegistry`。"""

from __future__ import annotations

from common.interfaces.settings import (
    Authorizer,
    CommandRegistry,
    HooksConfig,
    LimitPolicy,
    Member,
    MemberResolver,
    Project,
    Rules,
    SettingsReader,
    TaskTemplates,
)
from common.kernel.enums import PersonaKey


class _SettingsOk:
    def get_project(self, project_key: str) -> Project: ...
    def get_limits(self, project_key: str) -> LimitPolicy: ...
    def get_rules(self, project_key: str) -> Rules: ...
    def get_hooks(self, project_key: str) -> HooksConfig: ...
    def get_tasks(self, project_key: str) -> TaskTemplates: ...


class _SettingsMissingGetTasks:
    """`get_tasks`（IF-50）を欠く。"""

    def get_project(self, project_key: str) -> Project: ...
    def get_limits(self, project_key: str) -> LimitPolicy: ...
    def get_rules(self, project_key: str) -> Rules: ...
    def get_hooks(self, project_key: str) -> HooksConfig: ...


class _SettingsRenamedArg:
    """`get_project` の引数名が違う（`project_key` ではなく `key`）。"""

    def get_project(self, key: str) -> Project: ...
    def get_limits(self, project_key: str) -> LimitPolicy: ...
    def get_rules(self, project_key: str) -> Rules: ...
    def get_hooks(self, project_key: str) -> HooksConfig: ...
    def get_tasks(self, project_key: str) -> TaskTemplates: ...


settings_ok: SettingsReader = _SettingsOk()
settings_missing: SettingsReader = _SettingsMissingGetTasks()  # type: ignore
settings_renamed: SettingsReader = _SettingsRenamedArg()  # type: ignore


class _ResolverOk:
    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member: ...
    def is_human(self, actor: str) -> bool: ...


class _ResolverMissingIsHuman:
    """`is_human` を欠く。"""

    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member: ...


class _ResolverRenamedArg:
    """`is_human` の引数名が違う（`actor` ではなく `who`）。"""

    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member: ...
    def is_human(self, who: str) -> bool: ...


resolver_ok: MemberResolver = _ResolverOk()
resolver_missing: MemberResolver = _ResolverMissingIsHuman()  # type: ignore
resolver_renamed: MemberResolver = _ResolverRenamedArg()  # type: ignore


class _AuthorizerOk:
    def enforce(self, subject: str, obj: str, act: str) -> bool: ...


class _AuthorizerMissingEnforce:
    """`enforce` を欠く。"""


class _AuthorizerRenamedArg:
    """`enforce` の引数名が違う（`obj` ではなく `resource`）。"""

    def enforce(self, subject: str, resource: str, act: str) -> bool: ...


authorizer_ok: Authorizer = _AuthorizerOk()
authorizer_missing: Authorizer = _AuthorizerMissingEnforce()  # type: ignore
authorizer_renamed: Authorizer = _AuthorizerRenamedArg()  # type: ignore


class _RegistryOk:
    def resolve_command(self, name: str) -> list[str]: ...


class _RegistryMissingResolve:
    """`resolve_command` を欠く。"""


class _RegistryRenamedArg:
    """`resolve_command` の引数名が違う（`name` ではなく `command_name`）。"""

    def resolve_command(self, command_name: str) -> list[str]: ...


registry_ok: CommandRegistry = _RegistryOk()
registry_missing: CommandRegistry = _RegistryMissingResolve()  # type: ignore
registry_renamed: CommandRegistry = _RegistryRenamedArg()  # type: ignore
