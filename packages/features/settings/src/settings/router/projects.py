"""プロジェクトの 5 操作（#1〜#5）＋役割ごとの実効設定の読取（D26）。

正本は `docs/design/phase6/openapi.yaml`。

**`from __future__ import annotations` を置かない**（F-20 の実測。`Annotated[…, Depends(局所
provider)]` を FastAPI が解決できなくなる）。
"""

from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse

from settings.router import _translate
from settings.router._translate import HeaderFactory
from settings.schemas import DetailBody, ProblemBody, ProjectSpec, ProjectView, RolesView
from settings.service.crud import SettingsCrudService

TAGS: list[str | Enum] = ["settings"]
"""正本の `tags`（settings の 39 操作すべてに付く）。"""

NOT_FOUND: dict[int | str, dict[str, Any]] = {status.HTTP_404_NOT_FOUND: {"model": DetailBody}}
"""**404** の本文スキーマ（`Detail`＝単一文字列）。"""

CONFLICT: dict[int | str, dict[str, Any]] = {status.HTTP_409_CONFLICT: {"model": DetailBody}}
"""**409** の本文スキーマ（`Conflict`＝ `Detail` と同じ。**違反一覧は返らない**＝ 010 SD-03）。"""

PROBLEM: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemBody}
}
"""**422** の本文スキーマ（`Problem`＝違反の**全件**）。"""


def register(
    router: APIRouter, provider: Callable[[], SettingsCrudService], headers: HeaderFactory
) -> None:
    """`/settings/projects` の 6 経路を登録する（`add_api_route` の明示登録）。"""

    def list_projects(
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> list[ProjectView]:
        """#1 プロジェクト一覧。"""
        return service.list_projects()

    def create_project(
        body: ProjectSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> ProjectView | JSONResponse:
        """#2 プロジェクト登録（201）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.create_project(body))
        )

    def get_project(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> ProjectView | JSONResponse:
        """#3 プロジェクト 1 件。"""
        return _translate.run(lambda: service.get_project(project_key))

    def update_project(
        project_key: str,
        body: ProjectSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> ProjectView | JSONResponse:
        """#4 プロジェクト編集。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.update_project(project_key, body))
        )

    def delete_project(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> Response | JSONResponse:
        """#5 プロジェクト解除（204。拒否は **409**＝ 010 SD-03）。"""
        return _translate.run(
            lambda: _translate.no_content(headers, service.delete_project(project_key))
        )

    def list_project_roles(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> RolesView | JSONResponse:
        """役割ごとの実効設定（D26。閲覧のみ）。"""
        return _translate.run(lambda: service.list_project_roles(project_key))

    router.add_api_route(
        "/settings/projects",
        list_projects,
        methods=["GET"],
        operation_id="listProjects",
        tags=TAGS,
        response_model=list[ProjectView],
    )
    router.add_api_route(
        "/settings/projects",
        create_project,
        methods=["POST"],
        operation_id="createProject",
        tags=TAGS,
        status_code=status.HTTP_201_CREATED,
        response_model=ProjectView,
        responses=PROBLEM,
    )
    router.add_api_route(
        "/settings/projects/{project_key}",
        get_project,
        methods=["GET"],
        operation_id="getProject",
        tags=TAGS,
        response_model=ProjectView,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/projects/{project_key}",
        update_project,
        methods=["PUT"],
        operation_id="updateProject",
        tags=TAGS,
        response_model=ProjectView,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/projects/{project_key}",
        delete_project,
        methods=["DELETE"],
        operation_id="deleteProject",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses={**NOT_FOUND, **CONFLICT},
    )
    router.add_api_route(
        "/settings/projects/{project_key}/roles",
        list_project_roles,
        methods=["GET"],
        operation_id="listProjectRoles",
        tags=TAGS,
        response_model=RolesView,
        responses=NOT_FOUND,
    )
