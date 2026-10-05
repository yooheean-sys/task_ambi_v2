"""LLM プロファイル・上限・コマンド定義の 14 操作（#17〜#30）。

**`from __future__ import annotations` を置かない**（F-20）。

⚠ **`POST /settings/commands`（#27）は V-24／V-25 の相互ロックで原理的に到達不能**
（**B-40**・DIFF-17）。**契約を 1 行も変えず規則どおり登録する。**
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse

from settings.router import _translate
from settings.router._translate import HeaderFactory
from settings.router.projects import NOT_FOUND, PROBLEM, TAGS
from settings.schemas import (
    CommandDefSchema,
    LimitSpec,
    LimitsView,
    LlmProfileSpec,
    LlmProfilesView,
    LlmProfileView,
)
from settings.service.crud import SettingsCrudService

__all__ = ["register"]


def register(  # noqa: C901, PLR0915  # 14 経路の登録関数。分岐は無く、経路ごとの宣言が並ぶだけ
    router: APIRouter, provider: Callable[[], SettingsCrudService], headers: HeaderFactory
) -> None:
    """`/settings/llm-profiles`・`/settings/limits`・`/settings/commands` の 14 経路を登録する。"""

    def list_llm_profiles(
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> LlmProfilesView:
        """#17 LLM プロファイル一覧。"""
        return service.list_llm_profiles()

    def create_llm_profile(
        body: LlmProfileSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> LlmProfileView | JSONResponse:
        """#18 LLM プロファイル作成（201）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.create_llm_profile(body))
        )

    def get_llm_profile(
        name: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> LlmProfileView | JSONResponse:
        """#19 LLM プロファイル 1 件（**`threshold_t` は小文字**＝ A-10・B-43）。"""
        return _translate.run(lambda: service.get_llm_profile(name))

    def update_llm_profile(
        name: str,
        body: LlmProfileSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> LlmProfileView | JSONResponse:
        """#20 LLM プロファイル編集。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.update_llm_profile(name, body))
        )

    def delete_llm_profile(
        name: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> Response | JSONResponse:
        """#21 LLM プロファイル削除（204。**既定の削除は 422**＝ 010 SD-13）。"""
        return _translate.run(
            lambda: _translate.no_content(headers, service.delete_llm_profile(name))
        )

    def get_limits(service: Annotated[SettingsCrudService, Depends(provider)]) -> LimitsView:
        """#22 上限（`global` は**解決値**。そのまま書き戻さない＝ 010 SD-16）。"""
        return service.get_limits()

    def put_global_limits(
        body: LimitSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> LimitsView | JSONResponse:
        """#23 全体既定の保存（**ここだけが `global:` を書く**＝ plan D-l）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.put_global_limits(body))
        )

    def put_project_limits(
        project_key: str,
        body: LimitSpec,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> LimitsView | JSONResponse:
        """#24 PJ 上書きの保存（**`overrides` だけ**を書く）。"""
        return _translate.run(
            lambda: _translate.saved(
                response, headers, service.put_project_limits(project_key, body)
            )
        )

    def delete_project_limits(
        project_key: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> Response | JSONResponse:
        """#25 PJ 上書きの解除（204）。"""
        return _translate.run(
            lambda: _translate.no_content(headers, service.delete_project_limits(project_key))
        )

    def list_commands(
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> list[CommandDefSchema]:
        """#26 コマンド定義一覧。"""
        return service.list_commands()

    def create_command(
        body: CommandDefSchema,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> CommandDefSchema | JSONResponse:
        """#27 コマンド定義の追加（201。**到達不能**＝ B-40。V-25 が必ず 422 にする）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.create_command(body))
        )

    def get_command(
        name: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> CommandDefSchema | JSONResponse:
        """#28 コマンド定義 1 件。"""
        return _translate.run(lambda: service.get_command(name))

    def update_command(
        name: str,
        body: CommandDefSchema,
        response: Response,
        service: Annotated[SettingsCrudService, Depends(provider)],
    ) -> CommandDefSchema | JSONResponse:
        """#29 コマンド定義の編集（**即時**に効く＝ P5D-016）。"""
        return _translate.run(
            lambda: _translate.saved(response, headers, service.update_command(name, body))
        )

    def delete_command(
        name: str, service: Annotated[SettingsCrudService, Depends(provider)]
    ) -> Response | JSONResponse:
        """#30 コマンド定義の削除（204。参照中は **V-24** が 422 にする）。"""
        return _translate.run(lambda: _translate.no_content(headers, service.delete_command(name)))

    router.add_api_route(
        "/settings/llm-profiles",
        list_llm_profiles,
        methods=["GET"],
        operation_id="listLlmProfiles",
        tags=TAGS,
        response_model=LlmProfilesView,
    )
    router.add_api_route(
        "/settings/llm-profiles",
        create_llm_profile,
        methods=["POST"],
        operation_id="createLlmProfile",
        tags=TAGS,
        status_code=status.HTTP_201_CREATED,
        response_model=LlmProfileView,
        responses=PROBLEM,
    )
    router.add_api_route(
        "/settings/llm-profiles/{name}",
        get_llm_profile,
        methods=["GET"],
        operation_id="getLlmProfile",
        tags=TAGS,
        response_model=LlmProfileView,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/llm-profiles/{name}",
        update_llm_profile,
        methods=["PUT"],
        operation_id="updateLlmProfile",
        tags=TAGS,
        response_model=LlmProfileView,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/llm-profiles/{name}",
        delete_llm_profile,
        methods=["DELETE"],
        operation_id="deleteLlmProfile",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/limits",
        get_limits,
        methods=["GET"],
        operation_id="getLimits",
        tags=TAGS,
        response_model=LimitsView,
    )
    router.add_api_route(
        "/settings/limits",
        put_global_limits,
        methods=["PUT"],
        operation_id="putGlobalLimits",
        tags=TAGS,
        response_model=LimitsView,
        responses=PROBLEM,
    )
    router.add_api_route(
        "/settings/limits/{project_key}",
        put_project_limits,
        methods=["PUT"],
        operation_id="putProjectLimits",
        tags=TAGS,
        response_model=LimitsView,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/limits/{project_key}",
        delete_project_limits,
        methods=["DELETE"],
        operation_id="deleteProjectLimits",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/commands",
        list_commands,
        methods=["GET"],
        operation_id="listCommands",
        tags=TAGS,
        response_model=list[CommandDefSchema],
    )
    router.add_api_route(
        "/settings/commands",
        create_command,
        methods=["POST"],
        operation_id="createCommand",
        tags=TAGS,
        status_code=status.HTTP_201_CREATED,
        response_model=CommandDefSchema,
        responses=PROBLEM,
    )
    router.add_api_route(
        "/settings/commands/{name}",
        get_command,
        methods=["GET"],
        operation_id="getCommand",
        tags=TAGS,
        response_model=CommandDefSchema,
        responses=NOT_FOUND,
    )
    router.add_api_route(
        "/settings/commands/{name}",
        update_command,
        methods=["PUT"],
        operation_id="updateCommand",
        tags=TAGS,
        response_model=CommandDefSchema,
        responses={**NOT_FOUND, **PROBLEM},
    )
    router.add_api_route(
        "/settings/commands/{name}",
        delete_command,
        methods=["DELETE"],
        operation_id="deleteCommand",
        tags=TAGS,
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        responses={**NOT_FOUND, **PROBLEM},
    )
