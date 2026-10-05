"""vault が提供する契約（IF-31〜37）。

Obsidian vault 上のフォルダ・ファイルの読取と outcome の書込。

正本: Protocol＝`docs/design/phase4/p4_module_contract.csv`／受け渡し型＝
`docs/design/phase5/vault/p5_vault_data_model.mmd` の `namespace common_interfaces_vault_py`。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from common.interfaces.settings import ProjectFolders
from common.kernel.enums import PersonaKey
from common.kernel.errors import NotFoundError, ViolationError


class VaultArea(StrEnum):
    """vault 上の領域。"""

    pj_input = "pj_input"
    role = "role"
    outcome = "outcome"


class FolderCheck(BaseModel):
    """フォルダ存在検証の結果。`ok` は導出だが通常属性として宣言する（FR-039a）。"""

    model_config = ConfigDict(frozen=True)

    missing: list[str]
    ok: bool


class FileRef(BaseModel):
    """読める 1 ファイル。`rel_path` は vault ルート相対（OB-6）。"""

    model_config = ConfigDict(frozen=True)

    area: VaultArea
    rel_path: str
    name: str
    size_bytes: int
    mtime: datetime


class WrittenPath(BaseModel):
    """書込結果。"""

    model_config = ConfigDict(frozen=True)

    rel_path: str
    bytes: int
    overwritten: bool
    archived_to: str | None


class VaultNotFound(NotFoundError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_vault_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """指定のファイルが vault に無い。"""

    rel_path: str
    role: PersonaKey | None

    def __init__(
        self,
        message: str,
        rel_path: str,
        role: PersonaKey | None,
        cause: BaseException | None = None,
    ) -> None:
        """不在の相対パスと役割を保持する。"""
        super().__init__(message, cause)
        self.rel_path = rel_path
        self.role = role


class VaultPathViolation(ViolationError):  # noqa: N818  # 例外名は設計が正本（P5D-001・p5_vault_data_model.mmd）。改名すると FR-040 の一致が壊れる
    """パス・名前・内容の規則に違反した。"""

    kind: Literal[
        "outside_root",
        "outside_area",
        "forbidden_name",
        "reserved",
        "too_large",
        "not_text",
        "empty",
    ]
    detail: str

    def __init__(
        self,
        message: str,
        kind: Literal[
            "outside_root",
            "outside_area",
            "forbidden_name",
            "reserved",
            "too_large",
            "not_text",
            "empty",
        ],
        detail: str,
        cause: BaseException | None = None,
    ) -> None:
        """違反の種類と、違反した文字・上限などの詳細を保持する。"""
        super().__init__(message, cause)
        self.kind = kind
        self.detail = detail


class VaultReader(Protocol):
    """vault の読取（IF-31〜36）。"""

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck:
        """フォルダの存在を確認する。不在は例外にせず `FolderCheck.missing` に載せる。

        Raises:
            VaultPathViolation: 宣言が vault ルートの外を指した（不在に倒さない）。
        """
        ...

    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]:
        """PJ 入力・役割フォルダ・兄弟成果物を列挙する。

        Raises:
            VaultNotFound: 役割が宣言に無い／領域フォルダが実在しない（空の一覧に倒さない）。
            VaultPathViolation: パス規則に違反した。
        """
        ...

    def read_input(self, folders: ProjectFolders, ref: FileRef) -> str:
        """入力ファイルを読む。

        Raises:
            VaultNotFound: 指定のファイルが無い。
            VaultPathViolation: パス規則に違反した。
        """
        ...

    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str:
        """成果物（outcome）ファイルを読む。不在は例外で明示する（黙って空を返さない）。

        Raises:
            VaultNotFound: 指定のファイルが無い。
            VaultPathViolation: パス規則に違反した。
        """
        ...

    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool:
        """成果物（outcome）ファイルの存在を確認する（完了ゲート）。

        Raises:
            VaultPathViolation: パス規則に違反した。
        """
        ...


class OutcomeWriter(Protocol):
    """outcome の書込（IF-37）。"""

    def write_outcome(self, folders: ProjectFolders, filename: str, content: str) -> WrittenPath:
        """成果物（outcome）を書き、通過した実パスを返す。

        Raises:
            VaultNotFound: `outcome_dir` が実在しない（フォルダを作らない）。
            VaultPathViolation: パス規則に違反した。
        """
        ...
