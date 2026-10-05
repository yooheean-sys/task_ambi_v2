"""`/settings/*` の 18 パス・39 操作（settings-F21）。正本は `docs/design/phase6/openapi.yaml`。

D26 で役割ごとの実効設定の読取（`projects.py`）を 1 本足した（17 パス・38 操作から）。

**`prefix` を付けない**（正本のパスがそのまま `/settings` で始まる）。経路の登録は
`router.add_api_route(...)` の**明示登録**にする（デコレータで束ねた局所関数は pyright strict の
`reportUnusedFunction` に触れる）。

**`router/` の 6 ファイルとも `from __future__ import annotations` を置かない** —— 口の依存注入は
`Annotated[SettingsCrudService, Depends(provider)]` で表すが、`provider` は
`create_settings_router()` の**局所変数**である。注釈を文字列にすると FastAPI は module の
globals だけで解決を試み、局所名を見つけられずに素のクエリ引数と誤読する（F-20 の実測）。

⚠ **`EffectClass.next_kickoff`（アンダースコア）→ 応答ヘッダ `next-kickoff`（ハイフン）の写像は
本ファイルの `_EFFECT_HEADER_VALUES` の 1 箇所にだけ置く**（010 SD-18）。画面・テスト・契約の
どこにも第 2 の写像を作らない —— 偽の応答を画面と同じ綴りで組むと、綴りがずれていても両方が
緑になり検出できないためである。
"""

from fastapi import APIRouter

from settings.dependencies import service_provider
from settings.models import EffectClass, SaveOutcome
from settings.router import agents, ai_config, policies, projects
from settings.service.crud import SettingsCrudService

GENERATION_HEADER = "X-Settings-Generation"
"""差し替え後の世代（10 進整数）。**書込 22 操作すべて**に載る（D-1・010 SD-12）。"""

EFFECT_HEADER = "X-Settings-Effect"
"""反映区分（`immediate` ｜ `next-kickoff`）。同上。"""

_EFFECT_HEADER_VALUES: dict[EffectClass, str] = {
    EffectClass.immediate: "immediate",
    EffectClass.next_kickoff: "next-kickoff",
}
"""内部表現 → **ヘッダの綴り**（010 SD-18。**この写像はここにしか無い**）。"""


def save_headers(outcome: SaveOutcome) -> dict[str, str]:
    """保存結果を応答ヘッダへ写す（**読取 16 操作には載せない**）。

    Args:
        outcome: 保存経路の戻り（差し替え後の世代と反映区分）。

    Returns:
        `X-Settings-Generation` と `X-Settings-Effect` の 2 つ。
    """
    return {
        GENERATION_HEADER: str(outcome.generation),
        EFFECT_HEADER: _EFFECT_HEADER_VALUES[outcome.effect],
    }


def create_settings_router(service: SettingsCrudService) -> APIRouter:
    """`/settings/*` の 39 操作を持つ `APIRouter` を返す。

    Args:
        service: 39 の口が共有する CRUD サービス（`build()` が組んだもの＝ FR-082）。

    Returns:
        経路を登録済みの router（`prefix` なし）。
    """
    router = APIRouter()
    provider = service_provider(service)
    projects.register(router, provider, save_headers)
    agents.register(router, provider, save_headers)
    ai_config.register(router, provider, save_headers)
    policies.register(router, provider, save_headers)
    return router
