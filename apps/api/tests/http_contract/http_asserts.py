"""正本への適合・応答ヘッダの共通アサート（規約 §6・§7）。

- `assert_conforms` —— 宣言 status と応答本文 schema の適合（**204 は本文が空**）。
- `assert_save_headers` / `assert_no_save_headers` —— settings の保存ヘッダ 2 つ。
- `assert_correlation_issued` / `assert_correlation_echoed` —— 相関 ID の往復。

**`Problem`／`Detail` 専用のアサートは作らない**（HD-12）—— 204 を除く 99 対はすべて正本内で
schema へ解決できるので、200 系も 404／409／422 も同じ 1 つの検査器で扱う。

**正本より厳しくも緩くもしない**（FR-042）。「非 required なら null を欠落とみなす」等の緩和を
入れてはならない —— 緩めると既知差分（B-09〜B-12）が黙って通り、`xfail(strict=True)` の
仕掛けが死ぬ。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from _support.composition import make_root_config
from _support.openapi_source import resolve, response_schema

from settings.router import EFFECT_HEADER, GENERATION_HEADER

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

EFFECT_IMMEDIATE = "immediate"
"""保存と同時に効く（権限ポリシー・コマンド定義。P5D-016・D16）。**綴りはテストのリテラル**。"""

EFFECT_NEXT_KICKOFF = "next-kickoff"
"""次回 kickoff から効く（その他すべて）。**綴りはテストのリテラル**（`010` SD-18・B-06）。"""

_KNOWN_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "$ref",
        "items",
        "enum",
        "additionalProperties",
        "allOf",
        "oneOf",
        "anyOf",
        "minimum",
        "minItems",
        "default",
        "format",
        "description",
    }
)
"""正本が実際に使う語彙。**未知の語彙が現れたら黙って無視せず落とす**（FR-042）。

`default`・`format`・`description` は適合の判定に使わない（正本の注記として読み飛ばす）。
"""

_TYPE_NAMES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


def _matches_type(declared: str, value: object) -> bool:
    """宣言された JSON 型に値が合うか（`integer`／`number` は `bool` を除く）。"""
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    return value is None


def _type_names(declared: object) -> list[str]:
    """`type` の宣言を名前の並びにする（`["string","null"]` の配列形を含む）。"""
    raw: Sequence[object] = (
        cast("Sequence[object]", declared) if isinstance(declared, list) else [declared]
    )
    names = [str(item) for item in raw]
    unknown = [name for name in names if name not in _TYPE_NAMES]
    if unknown:
        message = f"検査器が扱えない type が正本に現れた: {unknown}"
        raise ValueError(message)
    return names


def _conform(doc: Mapping[str, Any], schema_node: object, value: object, loc: str) -> list[str]:
    """1 つの schema へ 1 つの値を通し、違反を**場所と理由**の並びで返す。

    Args:
        doc: 正本（`$ref` の解決に使う）。
        schema_node: 検査する schema（`$ref` でもよい）。
        value: 応答から取り出した値。
        loc: 失敗時に出す場所（`$.profiles[0]/allOf[1].threshold_T`）。

    Returns:
        違反の説明（空なら適合）。
    """
    resolved = resolve(doc, schema_node)
    if not isinstance(resolved, dict):
        return [f"{loc}: schema が写像でない: {resolved!r}"]
    schema = cast("Mapping[str, Any]", resolved)
    unknown_keywords = sorted(set(schema) - _KNOWN_KEYWORDS)
    if unknown_keywords:
        return [f"{loc}: 検査器が扱えない schema 語彙が正本に現れた: {unknown_keywords}"]

    problems: list[str] = []
    problems.extend(_conform_all_of(doc, schema, value, loc))
    problems.extend(_conform_one_of(doc, schema, value, loc))
    problems.extend(_conform_any_of(doc, schema, value, loc))

    declared_type = schema.get("type")
    if declared_type is not None:
        names = _type_names(declared_type)
        if not any(_matches_type(name, value) for name in names):
            problems.append(f"{loc}: type {names} でない ({type(value).__name__})")
            return problems

    enum = schema.get("enum")
    if enum is not None and value not in cast("Sequence[object]", enum):
        problems.append(f"{loc}: enum 外の値 {value!r}")

    if isinstance(value, dict):
        problems.extend(_conform_object(doc, schema, cast("Mapping[str, Any]", value), loc))
    elif isinstance(value, list):
        problems.extend(_conform_array(doc, schema, cast("Sequence[Any]", value), loc))
    else:
        problems.extend(_conform_minimum(schema, value, loc))
    return problems


def _conform_all_of(
    doc: Mapping[str, Any], schema: Mapping[str, Any], value: object, loc: str
) -> list[str]:
    """`allOf` の**各枝をオブジェクト全体へ**適用する（JSON Schema の意味論。緩めない）。"""
    branches = schema.get("allOf")
    if branches is None:
        return []
    problems: list[str] = []
    for index, branch in enumerate(cast("Sequence[object]", branches)):
        problems.extend(_conform(doc, branch, value, f"{loc}/allOf[{index}]"))
    return problems


def _conform_one_of(
    doc: Mapping[str, Any], schema: Mapping[str, Any], value: object, loc: str
) -> list[str]:
    """`oneOf` に適合する枝が**ちょうど 1 つ**であることを見る。"""
    branches = schema.get("oneOf")
    if branches is None:
        return []
    all_branches = list(cast("Sequence[object]", branches))
    matched = [
        index
        for index, branch in enumerate(all_branches)
        if not _conform(doc, branch, value, f"{loc}/oneOf[{index}]")
    ]
    if len(matched) == 1:
        return []
    return [
        f"{loc}: oneOf に適合する枝が {len(matched)} 個（ちょうど 1 つであるべき。"
        f"枝は {len(all_branches)} 個）"
    ]


def _conform_any_of(
    doc: Mapping[str, Any], schema: Mapping[str, Any], value: object, loc: str
) -> list[str]:
    """`anyOf` に適合する枝が**1 つ以上**あることを見る（D24 の nullable `repos`）。

    `$ref` と `null` を併せる形（`anyOf: [{$ref...}, {type: null}]`）は OpenAPI 3.1 の
    nullable の標準形であり、検査器は `$ref`・`null` を既に扱えるので枝ごとに再帰する。
    """
    branches = schema.get("anyOf")
    if branches is None:
        return []
    all_branches = list(cast("Sequence[object]", branches))
    matched = [
        index
        for index, branch in enumerate(all_branches)
        if not _conform(doc, branch, value, f"{loc}/anyOf[{index}]")
    ]
    if matched:
        return []
    return [f"{loc}: anyOf のどの枝にも適合しない（枝は {len(all_branches)} 個）"]


def _conform_object(
    doc: Mapping[str, Any], schema: Mapping[str, Any], value: Mapping[str, Any], loc: str
) -> list[str]:
    """`properties`／`required`／`additionalProperties` を見る。"""
    problems: list[str] = []
    required = schema.get("required")
    if required is not None:
        for key in cast("Sequence[object]", required):
            name = str(key)
            if name not in value:
                problems.append(f"{loc}.{name}: required なのに無い")

    properties = schema.get("properties")
    known: set[str] = set()
    if properties is not None:
        declared_props = cast("Mapping[str, Any]", properties)
        known = set(declared_props)
        for name, sub_schema in declared_props.items():
            if name in value:
                problems.extend(_conform(doc, sub_schema, value[name], f"{loc}.{name}"))

    extra = sorted(name for name in value if name not in known)
    additional = schema.get("additionalProperties")
    if additional is False and extra:
        problems.append(f"{loc}: additionalProperties:false なのに未知キー {extra}")
    elif isinstance(additional, dict):
        additional_schema = cast("Mapping[str, Any]", additional)
        for name in extra:
            problems.extend(_conform(doc, additional_schema, value[name], f"{loc}.{name}"))
    return problems


def _conform_array(
    doc: Mapping[str, Any], schema: Mapping[str, Any], value: Sequence[Any], loc: str
) -> list[str]:
    """`items`／`minItems` を見る。"""
    problems: list[str] = []
    items = schema.get("items")
    if items is not None:
        for index, item in enumerate(value):
            problems.extend(_conform(doc, items, item, f"{loc}[{index}]"))
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        problems.append(f"{loc}: minItems {min_items} を下回る（{len(value)} 件）")
    return problems


def _conform_minimum(schema: Mapping[str, Any], value: object, loc: str) -> list[str]:
    """`minimum` を見る（数値のときだけ）。"""
    minimum = schema.get("minimum")
    if minimum is None or not isinstance(minimum, (int, float)):
        return []
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return []
    if value < minimum:
        return [f"{loc}: minimum {minimum} を下回る（{value}）"]
    return []


def assert_conforms(
    doc: Mapping[str, Any],
    *,
    path: str,
    method: str,
    status: str,
    response: httpx.Response,
) -> None:
    """応答が正本の宣言（status と本文 schema）に適合することを主張する（FR-019・FR-020）。

    204（`response_schema` が `None` を返す宣言）は**本文が空**であることだけを見る（FR-024）。

    Args:
        doc: 正本。
        path: 正本のパステンプレート。
        method: HTTP メソッド。
        status: 宣言 status（文字列）。
        response: 実際の応答。
    """
    if str(response.status_code) != status:
        pytest.fail(
            f"{method} {path}: 宣言 status {status} を期待したが {response.status_code} が返った\n"
            f"  本文: {response.text[:400]}"
        )
    schema = response_schema(doc, path=path, method=method, status=status)
    if schema is None:
        if response.content != b"":
            pytest.fail(
                f"{method} {path} {status}: 本文を持たない宣言なのに本文が返った\n"
                f"  本文: {response.text[:400]}"
            )
        return
    try:
        body: object = json.loads(response.content)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"{method} {path} {status}: 応答を JSON として読めない: {exc}\n"
            f"  本文: {response.text[:400]}"
        )
    problems = _conform(doc, schema, body, "$")
    if problems:
        listed = "\n".join(f"    - {item}" for item in problems)
        pytest.fail(
            f"{method} {path} {status}: 応答本文が正本の宣言に適合しない\n{listed}\n"
            f"  本文: {response.text[:800]}"
        )


# --------------------------------------------------------------------------------------
# 保存ヘッダ（FR-027〜FR-029・規約 §6・HD-11・HD-20）
# --------------------------------------------------------------------------------------


def assert_save_headers(response: httpx.Response, *, effect: str) -> None:
    """保存ヘッダ 2 つが載り、世代が 10 進整数で、反映区分が `effect` と一致する。

    **ヘッダ名は `settings.router` の公開定数を import**する。**値そのもの（世代）は表明しない**
    —— 世代は土台の生成回数に依存する。

    Args:
        response: 実際の応答。
        effect: 期待する反映区分（`"immediate"` ／ `"next-kickoff"`。テストのリテラル）。
    """
    generation = response.headers.get(GENERATION_HEADER)
    if generation is None:
        pytest.fail(
            f"保存ヘッダ {GENERATION_HEADER} が載っていない\n"
            f"  載っていたヘッダ: {sorted(response.headers)}"
        )
    try:
        int(generation)
    except ValueError:
        pytest.fail(f"{GENERATION_HEADER} が 10 進整数として読めない: {generation!r}")
    actual_effect = response.headers.get(EFFECT_HEADER)
    if actual_effect != effect:
        pytest.fail(f"{EFFECT_HEADER} が期待と違う: 期待 {effect!r} ／ 実際 {actual_effect!r}")


def assert_no_save_headers(response: httpx.Response) -> None:
    """保存ヘッダ 2 つが**載っていない**ことを主張する（読取・エラー応答）。

    Args:
        response: 実際の応答。
    """
    present = [name for name in (GENERATION_HEADER, EFFECT_HEADER) if name in response.headers]
    if present:
        pytest.fail(
            f"保存していないのに保存ヘッダが載っている: {present}\n"
            f"  値: {[(name, response.headers[name]) for name in present]}"
        )


# --------------------------------------------------------------------------------------
# 相関 ID（FR-030〜FR-032・CFG-18）
# --------------------------------------------------------------------------------------


def correlation_header_name() -> str:
    """相関 ID のヘッダ名を**テスト対象 app に渡すのと同じ源**から引く（CFG-18・FR-031）。

    `make_root_config()` は `http_wiring.build_module_app` が `config=` へ渡すものと
    **同一**である。

    ⚠ **`RootConfig()` を直に構築してはならない** —— `RootConfig` は
    `settings_customise_sources` でソースを `(init_settings, JsonConfigSettingsSource)` に絞る
    作りなので、引数なしの構築は**実リポジトリのルート `config.json` を読む**。一方テスト対象の
    app が受け取るのは `LogConfig()` の**既定**である。つまり期待値の源と被検査系の源が別に
    なり、`config.json` の `log.correlation_header` を変えると**期待だけが動いて偽の赤**に
    なる。規約 R-02（`config=` を必ず渡して実 `config.json`・`.env` を読ませない）の趣旨にも
    反する。

    ヘッダ名をテストに直書きしない点は保たれる（FR-031）。

    Returns:
        `make_root_config().log.correlation_header` の値。
    """
    return make_root_config().log.correlation_header


def assert_correlation_issued(response: httpx.Response) -> None:
    """ヘッダを**付けずに**叩いた応答に、同名ヘッダが載り値が発番されていることを主張する。

    発番規則・並行分離・正規化の棄却規則は**検証しない**（層 2 の領分）。

    Args:
        response: 実際の応答。
    """
    name = correlation_header_name()
    issued = response.headers.get(name)
    if not issued:
        pytest.fail(
            f"相関 ID ヘッダ {name} が応答に載っていない（または空）\n"
            f"  載っていたヘッダ: {sorted(response.headers)}"
        )


def assert_correlation_echoed(response: httpx.Response, sent: str) -> None:
    """**付けて**叩いた応答に、同じ値が返ることを主張する（FR-032）。

    Args:
        response: 実際の応答。
        sent: 要求に付けた相関 ID。
    """
    name = correlation_header_name()
    echoed = response.headers.get(name)
    if echoed != sent:
        pytest.fail(
            f"相関 ID が往復していない: 送った {sent!r} ／ 返った {echoed!r}（ヘッダ名 {name}）"
        )
