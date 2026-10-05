"""メンバと段テンプレートの 11 操作（agents 6＝#6〜#11・tasks 5＝#12〜#16）。

**`from __future__ import annotations` を置かない**（F-20）。

⚠ **`PUT`／`DELETE /settings/agents/{pj}/{persona}` は新規作成と削除については原理的に到達
不能**である（**B-49**。V-19／V-21 の相互ロック）。**契約を 1 行も変えず規則どおり登録する。**
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse

from common.kernel.enums import PersonaKey
from settings.router import _translate
from settings.router._translate import HeaderFactory
from settings.router.projects import CONFLICT, NOT_FOUND, PROBLEM, TAGS
from settings.schemas import AgentSpec, AgentView, TaskTemplatesView
from settings.service.crud import SettingsCrudService

__all__ = ["register"]

_ = CONFLICT  # 段テンプレート・メンバの経路は 409 を持たない（正本どおり）


def register(  # noqa: C901, PLR0915  # 11 経路の登録関数。分岐は無く、経路ごとの宣言が並ぶだけ
    router: APIRouter, provider: Callable[[], SettingsCrudService], headers: HeaderFactory
) -> None:
    """`/settings/agents` と `/settings/tasks` の 11 経路を登録する。"""

    def list_agents(
        service: Annotated[SettingsCrudService, Depends(provider)],
        project_key: Annotated[str | None, Query()] = None,
    ) -> list[AgentView] | JSONResponse:
        """#6 メンバ一覧（**無指定なら全体の既定行**＝ `project_key` が `null`）。"""
        return _translate.run(lambda: service.list_agents(project_key))

    def get_global_agent(
        persona_key: PersonaKey, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> AgentSpec | JSONResponse:
        """#7 全体のペルソナ既定 1 件。"""
        return _translate.run(lambda: service.get_global_agent(persona_key))

    def put_global_agent(
        persona_key: PersonaKey,
        body: AgentSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> AgentSpec | JSONResponse:
        """#8 全体のペルソナ既定の編集。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.put_global_agent(persona_key, body))
        )

    def get_agent(
        project_key: str,
        persona_key: PersonaKey,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> AgentView | JSONResponse:
        """#9 メンバ 1 件（merge 済み）。"""
        return _translate.run(lambda: service.get_agent(project_key, persona_key))

    def put_agent(
        project_key: str,
        persona_key: PersonaKey,
        body: AgentSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> AgentView | JSONResponse:
        """#10 メンバの upsert（未登録 PJ は **404**＝ D-3。正本に無いが FR-075 が定める）。"""
        return _translate.run(
            lambda: _translate.saved(
                response, headers, service.put_agent(project_key, persona_key, body)
            )
        )

    def delete_agent(
        project_key: str,
        persona_key: PersonaKey,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> Response | JSONResponse:
        """#11 メンバ削除（204。孤児の権限行は **V-21** が 422 で落とす）。"""
        return _translate.run(
            lambda: _translate.no_content(headers, service.delete_agent(project_key, persona_key))
        )

    def get_global_tasks(
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> TaskTemplatesView:
        """#12 全体の段テンプレート（7 キー）。"""
        return service.get_global_tasks()

    def put_global_tasks(
        body: TaskTemplatesView,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> TaskTemplatesView | JSONResponse:
        """#13 全体の段テンプレートの編集。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.put_global_tasks(body))
        )

    def get_project_tasks(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> TaskTemplatesView | JSONResponse:
        """#14 PJ の**上書きだけ**（merge 結果ではない）。"""
        return _translate.run(lambda: service.get_project_tasks(project_key))

    def put_project_tasks(
        project_key: str,
        body: TaskTemplatesView,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> TaskTemplatesView | JSONResponse:
        """#15 PJ の上書きの編集（キー単位の丸ごと置換）。"""
        return _translate.run(
            lambda: _translate.saved(
                response, headers, service.put_project_tasks(project_key, body)
            )
        )

    def delete_project_tasks(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> Response | JSONResponse:
        """#16 PJ の上書きを全解除（204。全体の 7 キーに戻る）。"""
        return _translate.run(
            lambda: _translate.no_content(headers, service.delete_project_tasks(project_key))
        )

    router.add_api_route(
        "/settings/agents",
        list_agents,
        methods=["GET"],
        operation_id="listAgents",
        tags=TAGS,
        response_model=list[AgentView],
    )
    router.add_api_route(
        "/settings/agents/global/{persona_key}",
        get_global_agent,
        methods=["GET"],
        operation_id="getGlobalAgent",
        tags=TAGS,
        response_model=AgentSpec,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/agents/global/{persona_key}",
        put_global_agent,
        methods=["PUT"],
        operation_id="putGlobalAgent",
        tags=TAGS,
        response_model=AgentSpec,
        responses=PROBLEM,
    )
    router.add_api_route(
        "/settings/agents/{project_key}/{persona_key}",
        get_agent,
        methods=["GET"],
        operation_id="getAgent",
        tags=TAGS,
        response_model=AgentView,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/agents/{project_key}/{persona_key}",
        put_agent,
        methods=["PUT"],
        operation_id="putAgent",
        tags=TAGS,
        response_model=AgentView,
        # D-3: 未登録 PJ の 404 を**宣言にも載せる**（正本に無い件は B-44 (c) に記録済み）。
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/agents/{project_key}/{persona_key}",
        delete_agent,
        methods=["DELETE"],
        operation_id="deleteAgent",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/tasks/global",
        get_global_tasks,
        methods=["GET"],
        operation_id="getGlobalTasks",
        tags=TAGS,
        response_model=TaskTemplatesView,
    )
    router.add_api_route(
        "/settings/tasks/global",
        put_global_tasks,
        methods=["PUT"],
        operation_id="putGlobalTasks",
        tags=TAGS,
        response_model=TaskTemplatesView,
        responses=PROBLEM,
    )
    router.add_api_route(
        "/settings/tasks/{project_key}",
        get_project_tasks,
        methods=["GET"],
        operation_id="getProjectTasks",
        tags=TAGS,
        response_model=TaskTemplatesView,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/tasks/{project_key}",
        put_project_tasks,
        methods=["PUT"],
        operation_id="putProjectTasks",
        tags=TAGS,
        response_model=TaskTemplatesView,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/tasks/{project_key}",
        delete_project_tasks,
        methods=["DELETE"],
        operation_id="deleteProjectTasks",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=NOT_FOUND,
    )
