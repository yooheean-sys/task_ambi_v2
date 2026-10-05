# pyright: reportUnnecessaryTypeIgnoreComment=true
"""SC-010: `VaultReader`・`OutcomeWriter`。"""

from __future__ import annotations

from common.interfaces.settings import ProjectFolders
from common.interfaces.vault import (
    FileRef,
    FolderCheck,
    OutcomeWriter,
    VaultReader,
    WrittenPath,
)
from common.kernel.enums import PersonaKey


class _VaultReaderOk:
    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck: ...
    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]: ...
    def read_input(self, folders: ProjectFolders, ref: FileRef) -> str: ...
    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str: ...
    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool: ...


class _VaultReaderMissingReadInput:
    """`read_input` を欠く。"""

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck: ...
    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]: ...
    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str: ...
    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool: ...


class _VaultReaderRenamedArg:
    """`read_input` の引数名が違う（`ref` ではなく `file_ref`）。"""

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck: ...
    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]: ...
    def read_input(self, folders: ProjectFolders, file_ref: FileRef) -> str: ...
    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str: ...
    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool: ...


vault_reader_ok: VaultReader = _VaultReaderOk()
vault_reader_missing: VaultReader = _VaultReaderMissingReadInput()  # type: ignore
vault_reader_renamed: VaultReader = _VaultReaderRenamedArg()  # type: ignore


class _OutcomeWriterOk:
    def write_outcome(
        self, folders: ProjectFolders, filename: str, content: str
    ) -> WrittenPath: ...


class _OutcomeWriterMissing:
    """`write_outcome` を欠く。"""


class _OutcomeWriterRenamedArg:
    """`write_outcome` の引数名が違う（`content` ではなく `body`）。"""

    def write_outcome(self, folders: ProjectFolders, filename: str, body: str) -> WrittenPath: ...


outcome_writer_ok: OutcomeWriter = _OutcomeWriterOk()
outcome_writer_missing: OutcomeWriter = _OutcomeWriterMissing()  # type: ignore
outcome_writer_renamed: OutcomeWriter = _OutcomeWriterRenamedArg()  # type: ignore
