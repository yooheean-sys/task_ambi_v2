"""相関 ID の往復 —— **53 操作すべて**の応答にヘッダが載る（US5・FR-030〜FR-032・SC-006）。

層 1 は構造的に検証できず（3 MOD とも素の `FastAPI()` に router を直載せ）、層 2 は `/health`
1 パスしか見ていない。ここは**実配線（相関 ID ミドルウェア込み）**で 53 操作の代表 1 本ずつを
叩く。

**ヘッダ名はルート設定から引く**（CFG-18・FR-031。テストに直書きしない）。発番規則・並行分離・
`contextvars` の解放・ミドルウェア数・別名往復は**検証しない**（層 2 の
`test_smoke_correlation.py` の領分）。

**409 は代表集合の外**にある（`deleteProject`・`createKickoff` はどちらも成功系が代表）ため、
US5 AC4 の「エラー応答にも載る」を 409 で確かめる検証を**別に**持つ（裁定 J-1・J-3）。
代表集合が正本の宣言 status の**種別すべて**を踏むことも正本から数えて突き合わせる（裁定 J-2）。

代表 1 本につき**発番側と往復側を別のテスト関数に割る**（裁定 K-1）。関数スコープの fixture は
**1 テスト 1 インスタンス**なので、2 本とも**土台の初期状態**を叩く。⚠ 1 本のテストの中で
`request.getfixturevalue` を 2 度呼んでも**同じ app が返る**（同一テスト内で値がキャッシュされる）
ため、割らないと 2 度目は土台が変化した後を叩くことになる。

どちらのテストも **recipe が名乗る status を実際に踏んだこと**を表明する（裁定 K-2）。表明するのは
**status だけ**で、本文の適合は各提供フェーズの所有テストの領分のままである（規約 §5）。この表明が
無いと、代表が宣言 status と違う応答に化けても `assert_correlation_issued` だけ通って緑になり、
`test_the_representative_set_exercises_every_declared_status` の「代表が宣言 status を**実際に**
踏む」は recipe の**ラベル**の上でしか成り立たなくなる。

このファイルは `http_op` を**宣言しない**（規約 §5。対の所有は各提供フェーズ）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from _support.openapi_source import load_document, openapi_path, operations
from http_asserts import (
    assert_correlation_echoed,
    assert_correlation_issued,
    correlation_header_name,
)
from http_calls import (
    CONFLICT_REPRESENTATIVES,
    DROPPED_PATH_PLACEHOLDER,
    HAPPY_CALLS,
    OUTSIDE_PATH_PLACEHOLDER,
    HttpCall,
    call_of,
)
from http_wiring import DRAIN_TIMEOUT_SECONDS, client_of, drain_kickoff

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    import httpx
    from fastapi import FastAPI
    from http_wiring import GatedBoardReader

    ConflictDriver = Callable[
        [pytest.FixtureRequest, HttpCall], tuple[httpx.Response, httpx.Response]
    ]

SENT_CORRELATION_ID = "http-contract-0001"
"""要求に付ける相関 ID（正規化の棄却規則に触れない素直な値）。"""

KICKOFF_OPERATIONS = frozenset({"createKickoff"})
"""叩いた後に周回の排出を待つ操作（FR-043）。"""

REPRESENTATIVE_CALLS: tuple[HttpCall, ...] = HAPPY_CALLS + CONFLICT_REPRESENTATIVES
"""status 網羅の判定が読む代表集合（53 操作の代表 ＋ 409 の 2 本）。

**`HAPPY_CALLS` の側は 1 操作 1 代表のまま**である（`CONFLICT_REPRESENTATIVES` を
`HAPPY_OPERATION_IDS` に混ぜると「1 操作 1 代表」の判定が落ちる）。
"""

ACCEPTED_KICKOFF_STATUS = int(call_of("createKickoff").status)
"""1 本目の kickoff が受理されたことの前提表明に使う（recipe から引く）。"""


def _resolved_body(call: HttpCall, tmp_path: Path) -> object | None:
    """実行時にしか決まらないパスを埋める（投下ファイルの絶対パス）。"""
    body = call.body
    if not isinstance(body, dict):
        return body
    fields = cast("Mapping[str, object]", body)
    path_value = fields.get("path")
    if path_value == DROPPED_PATH_PLACEHOLDER:
        dropped = tmp_path / "dropped.md"
        dropped.write_text("投下\n", encoding="utf-8")
        return {**fields, "path": str(dropped)}
    if path_value == OUTSIDE_PATH_PLACEHOLDER:
        outside = tmp_path / "outside.md"
        outside.write_text("監視対象の外\n", encoding="utf-8")
        return {**fields, "path": str(outside)}
    return fields


def _drive_representative(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    call: HttpCall,
    *,
    headers: Mapping[str, str] | None,
) -> httpx.Response:
    """代表 1 本を**まっさらな土台**で 1 度だけ叩く（周回を持つ操作は排出まで待つ）。

    ⚠ **1 テストにつき 1 度だけ呼ぶ**（裁定 K-1）—— `request.getfixturevalue` は同一テストの中で
    値をキャッシュするので、2 度呼んでも同じ app が返り、2 度目は土台が変化した後を叩く。

    Args:
        request: fixture を名前で引くための要求。
        tmp_path: 投下ファイルを置く一時ディレクトリ。
        call: 叩く代表の recipe。
        headers: 付ける要求ヘッダ（付けないなら `None`）。

    Returns:
        応答。
    """
    body = _resolved_body(call, tmp_path)
    app: FastAPI = request.getfixturevalue(call.fixture)
    with client_of(app) as client:
        response = client.request(call.method, call.url, json=body, headers=headers)
        if call.operation_id in KICKOFF_OPERATIONS:
            drain_kickoff(client)
    return response


def _assert_declared_status(response: httpx.Response, call: HttpCall, *, leg: str) -> None:
    """recipe が名乗る status を**実際に**踏んだことを表明する（裁定 K-2）。

    status 名も件数も書かない（FR-003）—— `call.status` から引く。

    Args:
        response: 実際の応答。
        call: 叩いた代表の recipe。
        leg: どちらの側で踏んだか（失敗時の案内に出す）。
    """
    expected = int(call.status)
    assert response.status_code == expected, (
        f"{call.operation_id}（{leg}）が recipe の名乗る {expected} でなく"
        f" {response.status_code} を返した —— 代表が宣言 status を踏んでいない\n"
        f"  本文: {response.text[:400]}"
    )


@pytest.mark.parametrize(
    "operation_id", [call.operation_id for call in HAPPY_CALLS], ids=lambda name: str(name)
)
def test_correlation_id_is_issued_on_every_operation(
    request: pytest.FixtureRequest, tmp_path: Path, operation_id: str
) -> None:
    """28 パス × **53 操作**の代表で、ヘッダを**付けずに**叩くと発番される（US5 AC1・SC-006）。

    エラー応答（404・422）にも載ることを含む（US5 AC4）。代表がどの status を踏むかは
    `call.status` から引いて表明する（裁定 K-2）。

    ⚠ **409 はここに居ない** —— 代表は 1 操作 1 本で、409 を宣言する 2 操作
    （`deleteProject`・`createKickoff`）はどちらも成功系（204・202）が代表だからである。
    US5 AC4 の 409 は `test_correlation_id_round_trips_on_conflict_responses` が踏む（裁定 J-1）。
    代表がどの status 種別を踏むかは
    `test_the_representative_set_exercises_every_declared_status` が正本と突き合わせる（裁定 J-2）。
    """
    call = call_of(operation_id)

    issued = _drive_representative(request, tmp_path, call, headers=None)

    _assert_declared_status(issued, call, leg="ヘッダ無し")
    assert_correlation_issued(issued)


@pytest.mark.parametrize(
    "operation_id", [call.operation_id for call in HAPPY_CALLS], ids=lambda name: str(name)
)
def test_correlation_id_is_echoed_on_every_operation(
    request: pytest.FixtureRequest, tmp_path: Path, operation_id: str
) -> None:
    """28 パス × **53 操作**の代表で、ヘッダを**付けて**叩くと同じ値が返る（US5 AC2・FR-032）。

    発番側（`test_correlation_id_is_issued_on_every_operation`）と**別のテスト**にしてあるので、
    こちらも土台の初期状態を叩く（裁定 K-1）。同じテストに畳むと `createProject`・
    `deleteProject`・`deleteLlmProfile` が 1 度目の書込の後を叩き、宣言 status を踏まなくなる。
    """
    call = call_of(operation_id)

    echoed = _drive_representative(
        request, tmp_path, call, headers={correlation_header_name(): SENT_CORRELATION_ID}
    )

    _assert_declared_status(echoed, call, leg="ヘッダ有り")
    assert_correlation_echoed(echoed, SENT_CORRELATION_ID)


def test_the_representative_set_covers_every_operation_exactly_once() -> None:
    """代表が正本の**全操作**をちょうど 1 本ずつ覆う（欠落・余分・重複のいずれも無い）。

    正本 `openapi.yaml` から `(METHOD, パステンプレート)` を数え、代表集合と**両方向**で
    突き合わせる。**件数をここに書かない**（FR-003）—— 正本に操作が 1 件足されたら、本ファイルを
    1 行も変えずに「代表が無い」側に出て落ちる（SC-002 の筋書きが SC-006 にも効く）。

    この検査が無いと `HAPPY_OPERATION_IDS` は手で保つだけの一覧になり、被覆検査（`http_op` の
    宣言を強制する側）が緑のまま SC-006 の「53 操作すべて」が黙って崩れる。

    このファイルは `http_op` を**宣言しない**（規約 §5）ので、被覆台帳には影響しない。
    """
    source = {operation.key for operation in operations(load_document(openapi_path()))}
    keys = [(call.method, call.template) for call in HAPPY_CALLS]
    represented = set(keys)
    failures: list[str] = []
    for label, offenders in (
        ("正本に在って代表が無い操作（SC-006 が崩れる）", sorted(source - represented)),
        ("正本に無い操作を代表が指している", sorted(represented - source)),
        ("代表が重複している操作", sorted({key for key in keys if keys.count(key) > 1})),
    ):
        if offenders:
            listed = "\n".join(f"    - {method} {template}" for method, template in offenders)
            failures.append(f"  {label}:\n{listed}")
    assert not failures, "代表集合が正本の操作と一致していない:\n" + "\n".join(failures)


@pytest.mark.parametrize("operation_id", [call.operation_id for call in HAPPY_CALLS])
def test_every_representative_is_a_known_recipe(operation_id: str) -> None:
    """代表の `operation_id` がすべて recipe に在る（表と代表が食い違わない）。"""
    call = call_of(operation_id)
    assert call.url, f"{operation_id} の recipe に URL が無い"


# --------------------------------------------------------------------------------------
# 409（US5 AC4 の「エラー応答にも載る」。裁定 J-1・J-3）
# --------------------------------------------------------------------------------------


def _drive_direct_conflict(
    request: pytest.FixtureRequest, call: HttpCall
) -> tuple[httpx.Response, httpx.Response]:
    """土台の状態だけで 409 になる操作を、ヘッダ**無し**・**有り**の 2 回叩く。"""
    app: FastAPI = request.getfixturevalue(call.fixture)
    with client_of(app) as client:
        issued = client.request(call.method, call.url, json=call.body)
        echoed = client.request(
            call.method,
            call.url,
            json=call.body,
            headers={correlation_header_name(): SENT_CORRELATION_ID},
        )
    return issued, echoed


def _drive_kickoff_conflict(
    request: pytest.FixtureRequest, call: HttpCall
) -> tuple[httpx.Response, httpx.Response]:
    """1 本目を `list_working` で塞いで in-flight を固定し、2 本目・3 本目を叩く（HD-25）。

    塞いでいる間は追加の `POST /kickoffs` がすべて 409 になるので、ヘッダ**無し**・**有り**の
    2 本を**同じ窓の中で**踏む。門は app ごとに 1 回しか閉じられないため、窓を分けると
    2 度目が 202 に化ける。表明の後は `try/finally` で**必ず解放**して排出まで待つ（FR-043）。
    """
    app: FastAPI = request.getfixturevalue(call.fixture)
    gate: GatedBoardReader = request.getfixturevalue("gated_board_reader")
    with client_of(app) as client:
        first = client.post(call.url, json=call.body)
        assert first.status_code == ACCEPTED_KICKOFF_STATUS, (
            f"1 本目が受理されなかった（{first.status_code}）—— 409 を踏めない"
        )
        try:
            entered = gate.entered.wait(timeout=DRAIN_TIMEOUT_SECONDS)
            assert entered, "1 本目が周回（list_working）に入らなかった —— 409 を踏めない"
            issued = client.request(call.method, call.url, json=call.body)
            echoed = client.request(
                call.method,
                call.url,
                json=call.body,
                headers={correlation_header_name(): SENT_CORRELATION_ID},
            )
        finally:
            gate.release.set()
            drain_kickoff(client)
    return issued, echoed


CONFLICT_DRIVERS: Mapping[str, ConflictDriver] = {
    "deleteProject_409": _drive_direct_conflict,
    "createKickoff_409": _drive_kickoff_conflict,
}
"""409 まで運ぶ手順（`CONFLICT_REPRESENTATIVES` の各行に 1 つ）。"""


@pytest.mark.parametrize(
    "operation_id",
    [call.operation_id for call in CONFLICT_REPRESENTATIVES],
    ids=lambda name: str(name),
)
def test_correlation_id_round_trips_on_conflict_responses(
    request: pytest.FixtureRequest, operation_id: str
) -> None:
    """**409** の応答にも相関 ID が載り、送った値が返る（US5 AC4・裁定 J-1・J-3）。

    代表集合は 1 操作 1 代表なので `deleteProject`（204）・`createKickoff`（202）は**成功系**が
    選ばれており、409 は一度も踏まれていなかった。2 操作とも踏む（裁定 J-1）。

    **応答が実際に 409 であることを先に表明する** —— 成功系に化けたまま
    `assert_correlation_issued` だけ通って緑になる空振りを塞ぐ。

    このテストは `http_op` を**宣言しない**（規約 §5。409 の対の所有は
    `test_http_settings_errors.py` ／ `test_http_orchestrator_kickoffs.py` の側）。
    """
    call = call_of(operation_id)
    drive = CONFLICT_DRIVERS.get(operation_id)
    assert drive is not None, (
        f"{operation_id} を 409 まで運ぶ手順が CONFLICT_DRIVERS に無い"
        "（CONFLICT_REPRESENTATIVES に足したら運び手も足す）"
    )
    expected = int(call.status)

    issued, echoed = drive(request, call)

    assert issued.status_code == expected, (
        f"{operation_id}（ヘッダ無し）が {expected} でなく {issued.status_code} を返した"
        f" —— 409 を踏めていない: {issued.text[:200]}"
    )
    assert echoed.status_code == expected, (
        f"{operation_id}（ヘッダ有り）が {expected} でなく {echoed.status_code} を返した"
        f" —— 409 を踏めていない: {echoed.text[:200]}"
    )
    assert_correlation_issued(issued)
    assert_correlation_echoed(echoed, SENT_CORRELATION_ID)


def test_the_representative_set_exercises_every_declared_status() -> None:
    """代表が正本の宣言 status の**種別すべて**を実際に踏む（裁定 J-2）。

    正本 `openapi.yaml` が宣言する status の種別集合と、代表集合（53 操作の代表 ＋ 409 の
    2 本）の status 集合を**両方向**で突き合わせる。**status 名も件数もここに書かない**
    （FR-003）—— 正本に新しい status が入ったら、本ファイルを 1 行も変えずに「代表が踏まない」
    側に出て落ちる。

    この検査が無いと、T076 が塞いだのと同じ空振り（「エラー応答にも載る」と主張しながらその
    status を一度も踏んでいない）が**別の status で再発する**。

    台帳（`UNREACHED` 6 対）が持つ status はいずれも他の操作の代表が持っているので、台帳を
    除外する特例は要らない（実測）。特例が要る形になったら**緩めずに**落とし、`design-sync.md`
    の B 表へ記録する。

    このファイルは `http_op` を**宣言しない**（規約 §5）ので、被覆台帳には影響しない。
    """
    source = {
        status
        for operation in operations(load_document(openapi_path()))
        for status in operation.statuses
    }
    represented = {call.status for call in REPRESENTATIVE_CALLS}
    failures: list[str] = []
    for label, offenders in (
        ("正本が宣言していて代表が 1 本も踏まない status", sorted(source - represented)),
        (
            "正本のどの操作も宣言していない status を代表が踏むと言っている",
            sorted(represented - source),
        ),
    ):
        if offenders:
            failures.append(f"  {label}: {', '.join(offenders)}")
    assert not failures, "代表集合の status 種別が正本と一致していない:\n" + "\n".join(failures)
