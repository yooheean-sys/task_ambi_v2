"""権限・ルール・Hook・人間の 8 操作（#31〜#38）。

**`from __future__ import annotations` を置かない**（F-20）。

`PUT /settings/rules/projects/{k}` は **200／404 のみ**（**422 を持たない**＝ M-03。本文を見る
規則が 1 つも無く、空文字も可＝ FR-065）。
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from settings.router import _translate
from settings.router._translate import HeaderFactory
from settings.router.projects import NOT_FOUND, PROBLEM, TAGS
from settings.schemas import HooksConfigSchema, HumansView, PolicySpec, PolicyView, TextBody
from settings.service.crud import SettingsCrudService

__all__ = ["register"]


def register(
    router: APIRouter, provider: Callable[[], SettingsCrudService], headers: HeaderFactory
) -> None:
    """`/settings/policies`・`/rules`・`/hooks`・`/humans` の 8 経路を登録する。"""

    def get_policy(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> PolicyView | JSONResponse:
        """#31 権限表（`model_conf` は**参照用**＝ CRUD 対象外）。"""
        return _translate.run(lambda: service.get_policy(project_key))

    def put_policy(
        project_key: str,
        body: PolicySpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> PolicyView | JSONResponse:
        """#32 権限表の編集（**即時**に効く＝ P5D-016）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.put_policy(project_key, body))
        )

    def get_project_rules(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> TextBody | JSONResponse:
        """#33 共通ルール（不在なら空文字＝ FR-027）。"""
        return _translate.run(lambda: service.get_project_rules(project_key))

    def put_project_rules(
        project_key: str,
        body: TextBody,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> TextBody | JSONResponse:
        """#34 共通ルールの編集（**200／404 のみ**。`DELETE` は空文字の `PUT` で代替）。"""
        return _translate.run(
            lambda: _translate.saved(
                response, headers, service.put_project_rules(project_key, body)
            )
        )

    def get_hooks(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> HooksConfigSchema | JSONResponse:
        """#35 Hook 宣言。"""
        return _translate.run(lambda: service.get_hooks(project_key))

    def put_hooks(
        project_key: str,
        body: HooksConfigSchema,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> HooksConfigSchema | JSONResponse:
        """#36 Hook 宣言の編集（**V-18** は形式検査だけ＝ REC-02・B-41）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.put_hooks(project_key, body))
        )

    def get_humans(service: Annotated[SettingsCrudService, Depends(provider)]) -> HumansView:
        """#37 人間の行為者。"""
        return service.get_humans()

    def put_humans(
        body: HumansView,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> HumansView | JSONResponse:
        """#38 人間の行為者の編集（**V-20** が 3 対の衝突を見る）。"""
        return _translate.run(lambda: _translate.saved(response, headers, service.put_humans(body)))

    router.add_api_route(
        "/settings/policies/{project_key}",
        get_policy,
        methods=["GET"],
        operation_id="getPolicy",
        tags=TAGS,
        response_model=PolicyView,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/policies/{project_key}",
        put_policy,
        methods=["PUT"],
        operation_id="putPolicy",
        tags=TAGS,
        response_model=PolicyView,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/rules/projects/{project_key}",
        get_project_rules,
        methods=["GET"],
        operation_id="getProjectRules",
        tags=TAGS,
        response_model=TextBody,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/rules/projects/{project_key}",
        put_project_rules,
        methods=["PUT"],
        operation_id="putProjectRules",
        tags=TAGS,
        response_model=TextBody,
        # M-03: **422 を持たない**（正本どおり。本文を見る規則が 1 つも無い）。
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/hooks/{project_key}",
        get_hooks,
        methods=["GET"],
        operation_id="getHooks",
        tags=TAGS,
        response_model=HooksConfigSchema,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/hooks/{project_key}",
        put_hooks,
        methods=["PUT"],
        operation_id="putHooks",
        tags=TAGS,
        response_model=HooksConfigSchema,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/humans",
        get_humans,
        methods=["GET"],
        operation_id="getHumans",
        tags=TAGS,
        response_model=HumansView,
    )
    router.add_api_route(
        "/settings/humans",
        put_humans,
        methods=["PUT"],
        operation_id="putHumans",
        tags=TAGS,
        response_model=HumansView,
        responses=PROBLEM,
    )
