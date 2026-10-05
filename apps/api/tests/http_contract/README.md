# HTTP 契約テスト（層 5）—— `apps/api/tests/http_contract/`

> **本ファイルは転記である。** 規約の正本は
> `specs/014-http-contracts/contracts/http-contract-test-conventions.md`（非公開）。
> **食い違ったら正本が勝つ。**

## 1. この層が主張すること／しないこと

**主張する**: 実 composition root に載った提供実装が、正本 `docs/design/phase6/openapi.yaml` の
宣言どおりの **status・応答本文・エラー形式・応答ヘッダ**を**実際に返す**。

**主張しない**（他層が担保済み。やり直さない）:

| 観点 | 担保している層 |
|---|---|
| パス集合・操作集合の面の一致、`operationId` の一致、員数 | 層 1（各 feature の router テスト） |
| FastAPI が生成する `openapi.json` の schema 構造と正本の突合 | 層 1・層 2 |
| Protocol 契約行（IF-01〜50）の振る舞い、契約例外の送出条件 | 層 4（`apps/api/tests/contract/`） |
| 発火・周回・実行の業務的な帰結 | 層 3（`specs/012-integration-clusters`） |
| 配線の失敗経路・lifespan・override の意味論 | 層 2（`apps/api/tests/integration/`） |
| 相関 ID の発番規則・並行分離・contextvar の解放・別名往復・ミドルウェア数 | 層 2（`test_smoke_correlation.py`） |

## 2. 1 テストの構成（**ダブルは 4 つだけ**）

1. **router を持つ提供実装ちょうど 1 つ** —— `settings` ｜ `trigger` ｜ `orchestrator` ｜ `apps/api` の `/health`
2. **その所有外部ストアの実物**（一時ディレクトリ）
3. **依存 Protocol の契約準拠スタブ**（`_support/contract_stubs.py`）
4. **実 composition root で組んだ app への `TestClient`**

**消費側の実装を登場させない** —— `settings.ui`・`trigger.ui`・`apps/ui`・PowerShell・実ブラウザ。

## 3. 配線

```python
overrides: ProtocolOverrides = {BoardReader: board_reader, VaultReader: vault_reader}
app = build_test_app(
    enabled_modules=["settings"],
    overrides=overrides,
    config=make_root_config(enabled_modules=["settings"]),
)
```

| 規則 | 理由 |
|---|---|
| `_support/composition.py::build_test_app` を使う | 素の `FastAPI()` 直 include だと相関 ID ミドルウェアが落ち、層 1 と同構成になる |
| **`config=` を必ず渡す** | `None` にすると `load_root_config()` が実リポジトリの `config.json`・`.env` を読む |
| `enabled_modules` は**当該 MOD 1 件だけ** | 1 テスト 1 提供実装 |
| **Protocol 型注釈を例外なく付ける** | Protocol は構造的部分型。注釈のない代入では pyright の適合検査が発火しない（憲章 §3） |
| 依存の差し替えは `overrides` のみ | `registry.py` を触らない（変更対象外） |

必要な override: settings＝`BoardReader`・`VaultReader` の 2、trigger＝`KickoffRunner` の 1、
orchestrator＝**9 契約**、`/health`＝0。

## 4. 止め木（**この層の核**）

`conftest.py` に autouse で置く（層 4 の `contract/conftest.py` は**共有されない**ので独立に持つ）。

- 空の `.env` を作り、**5 差し替え点**（`settings`・`trigger`・`board`・`vault`・`executor.models`
  の `env_file_path`）を `monkeypatch.setattr` する。
- OS 環境変数 `BEADS_DIR`・`BD_BIN`・`VAULT_ROOT`・`SETTINGS_ROOT`・`TRIGGER_ROOT`・
  `DOWNLOADS_DIR` と `LLM_API_KEY*` を消す（OS 環境変数は `.env` より優先されるため）。
- 各 fixture は `build()` の**前**に `assert_env_path_under(<Mod>Env, "<KEY>", tmp_path, env_file=…)`
  を通す —— settings の `build()` は不在時に**書き込む**ので事後確認では手遅れになる。

> ⚠ 止め木が効くのは、各 MOD の `<Mod>Env` の**明示 `__init__(self, **overrides)`** と `build()` の
> **`<Mod>Env(_env_file=env_file_path())`** が**対で**在るときだけである。どちらかを落とすと
> `model_config` のクラス定義時評価が残るだけになり、止め木は**黙って無効**になる（テストは緑の
> まま実運用ストアを掴む）。**この fixture を消さない・迂回しない。**

## 5. 宣言（被覆の単位）

```python
@pytest.mark.http_op("<METHOD>", "<正本のパステンプレート>", "<status>")
```

| 規則 | 内容 |
|---|---|
| 引数は**文字列リテラル 3 つ**だけ | AST の静的走査で拾う。変数・f-string は `UnresolvedDeclaration` として落とす |
| パスは**正本のテンプレート**のまま | `/settings/projects/{project_key}`。実値を埋めない |
| 表駆動では `pytest.param(..., marks=pytest.mark.http_op(...))` で**ケースごとに 1 件** | 1 テスト 1 宣言と両立する |
| **所有テストだけが宣言する** | (操作, status) 対は 1 本のテストが所有する。相関 ID のテスト・被覆検査は宣言しない |
| docstring に **(パス, 操作, status)** を書く | 憲章 §7-3・テストファーストの読み替え |
| **宣言は本体と一致する** | 宣言を持つテストは、**その対を `assert_conforms` に通している**こと。`conftest.py` の autouse fixture が teardown で突き合わせる（被覆検査は「宣言が在るか」しか見ないので、宣言と本体が食い違っても緑になる）。⚠ 宣言を**持たない**補助テストは対象外 |

マーカは本ディレクトリの `conftest.py` の `pytest_configure` で登録する（ルート `pyproject.toml` は触らない）。

## 6. 期待値の出所

| 何 | 出所 | 禁止 |
|---|---|---|
| status・応答本文の schema | **正本 `openapi.yaml`**（`_support/openapi_source.py` が `$ref` を解決して渡す） | パス名・status の一覧・schema をテストに写す |
| 保存ヘッダの**名前** | `settings.router` の公開定数 `GENERATION_HEADER`・`EFFECT_HEADER` | 文字列で直書きする |
| 反映区分の**値の綴り** | **テストのリテラル**（`"immediate"` / `"next-kickoff"`） | `_EFFECT_HEADER_VALUES`・`save_headers()`・`EffectClass.*.value` を呼ぶ（`010` SD-18） |
| どの操作がどちらの反映区分か | 設計の宣言（P5D-016・D16）を `http_calls.py` の `effect` 列に持つ | — |
| 相関 ID の**ヘッダ名** | **テスト対象 app に渡すのと同じ源**（`_support/composition.py` の `make_root_config().log.correlation_header`。CFG-18） | テストに直書きする／`RootConfig()` を直に構築する（**実リポジトリのルート `config.json`** を読むため、`LogConfig()` の既定を受け取る app と源が別になり偽の赤を生む） |
| 世代の**値** | 表明しない（`int(value)` が通ることだけ見る） | 特定の数（`"2"` 等）を書く |

## 7. schema 適合の検査器（`http_asserts.py`）

正本が実際に使う **13 語彙だけ**を扱う自前の検査で賄う（新しい依存を足さない）。
`Problem`／`Detail` 専用のアサートは**作らない** —— 204 を除く 99 対はすべて正本内で schema へ
解決できるので、同じ 1 つの検査器で扱う。

| 語彙 | 規則 |
|---|---|
| `type` | `["x","null"]` の配列形も扱う。**null は `type` が null を含むときだけ許す** |
| `properties` / `required` | `required` はキーの**存在**だけを見る（値が null でも「在る」） |
| `$ref` | 正本内で解決（多段・循環上限つき） |
| `items` / `minItems` | 配列の各要素と下限 |
| `enum` | 値が列挙に在ること |
| `additionalProperties: false` | **宣言された schema でだけ**未知キーを落とす |
| `additionalProperties: {schema}` | `properties` に無いキーの値を当該 schema で検査 |
| `allOf` | **各枝をオブジェクト全体へ**適用する（緩めない） |
| `oneOf` | 適合する枝が**ちょうど 1 つ** |
| `minimum` | 数値の下限 |
| `default` / `format` | 検証に使わない（正本の注釈として読み飛ばす） |

**正本より厳しくも緩くもしない**。「非 required なら null を欠落とみなす」等の緩和を入れてはならない。
失敗時は `$.profiles[0]/allOf[1].threshold_T: required なのに無い` の形で**場所と理由**を出す。

## 8. 2 つの台帳（`http_ledger.py`）

| 表 | 意味 | 検査 |
|---|---|---|
| `UNREACHED` | 実配線 ＋ 契約準拠スタブ ＋ 実自ストアでは**踏めない** (操作, status) と理由 | 被覆検査が「宣言 ∪ 台帳 ⊇ 正本」を見る。**理由が空なら落とす** |
| `KNOWN_DIVERGENCES` | 正本と実装が**食い違う** (操作, status)。検証は書き、`xfail(strict=True)` を付ける | 各対に**宣言が在る**ことを見る（xfail だけ残る事故を防ぐ） |

- 2 つの表は**交わってはならない**（被覆検査が `overlapping_pairs` で落とす）。
- `xfail` の `reason` には design-sync の **B 表の行 ID** を必ず入れる。
- `strict=True` を外さない —— 正本か実装が直ったときに `XPASS` で赤くなり、台帳の更新を強制する。

## 9. 後始末

| 対象 | やること | やってはいけないこと |
|---|---|---|
| orchestrator の周回 | `GET /kickoffs/current` の `running` が false になるまでの**上限つきポーリング**（既定 4 秒・20 ms 刻み）。上限に達したら失敗させる | ワーカースレッドを `join` する（`start_background` は daemon スレッドを起こすだけで join の口が無い）／上限で待ち直す（憲章 §10 の自前リトライになる） |
| 409 の in-flight 固定 | `BoardReader.list_working` を最初の 1 回だけ塞ぐ包みを使い、表明後に `try/finally` で**必ず解放**する | `MemberResolver.is_human` を塞ぐ（`build()` が組み立て時に呼ぶだけで周回に居ない —— 409 を踏めない） |
| trigger の常駐 | `with TestClient(app)` に入って出るだけ | 追加の起動制御・明示 join を置く |

## 10. 禁止事項

| # | 禁止 | 根拠 |
|---|---|---|
| 1 | `apps/api/tests/contract/` の module（`contract_stores`・`contract_shims`）を import する | `sys.path` に載らず単独実行で `ModuleNotFoundError` |
| 2 | feature の `tests/`（`settings_testing`・trigger の `conftest` 等）を import する | 憲章 §1。`foundation/test_feature_test_imports.py` が走査する |
| 3 | `requires_bd` マーカを付ける | 実 `bd` を呼ばない |
| 4 | `httpx` の `MockTransport` を使う | D14-b |
| 5 | 雛形データを `_support/` に置く | 層 4 の `contract_stores` と二重管理になる |
| 6 | 口の型違反による FastAPI 内蔵 422（`{"detail":[…]}`）に適合検証を置く | 正本の `Problem` と形が違う。乖離は記録するだけ |
| 7 | 新しい実行時依存・開発依存を足す | ルート `pyproject.toml` は変更対象外 |
| 8 | 変更対象外に触れる（`docs/design/`・`packages/`・`apps/api/src`・`apps/ui`・`registry.py`・`.importlinter`・ルート `pyproject.toml`／`config.json`・`integration/`・`contract/`・先行 feature の `specs/`） | 必要が生じたら**実装を止め** `design-sync.md` の B 表へ記録して報告する |
| 9 | テスト間に実行順序依存を作る | fixture は関数スコープ、状態はモジュール大域に持たない |
| 10 | `xfail` から `strict=True` を外す／`xfail` で踏めない対をごまかす | 台帳の 2 表を混ぜない（§8） |

## 11. 命名

- テスト: `test_http_<mod>_<観点>.py`（`<mod>` は `settings`・`trigger`・`orchestrator`・`health`・`coverage`・`correlation`）
- 補助（非テスト）: `http_` 接頭辞（`http_stores.py`・`http_calls.py`・`http_asserts.py`・`http_ledger.py`・`http_wiring.py`）
- **基名はリポジトリ全体で一意**（`tests/` に `__init__.py` を置かないため基名がそのまま
  top-level モジュール名になる）。`ledger.py`・`stores.py` のような一般名を使わない。
- 1 ファイル **400 行**を超えたら観点で割る（データだけの `http_calls.py` は例外）。

## 12. ファイル一覧（**付録**）

> 現物の目録であって規約そのものではないため、正本（規約）は本節を持たない（T084 の裁定）。

| ファイル | 役割 |
|---|---|
| `conftest.py` | 止め木・マーカ登録・提供実装別の fixture・**宣言と本体の突合**（autouse）（**fixture の登録点に徹する**） |
| `http_wiring.py` | 配線ヘルパ（`build_module_app`・`client_of`）・止め木の能動検査・`drain_kickoff`・`GatedBoardReader`・**宣言と本体の突合の部品**（`declared_pair` ほか） |
| `http_asserts.py` | schema 適合の検査器・保存ヘッダ／相関 ID のアサート |
| `http_calls.py` | 要求の recipe（データだけ。振る舞いを持たない） |
| `http_ledger.py` | 2 つの台帳（`UNREACHED`・`KNOWN_DIVERGENCES`） |
| `http_stores.py` | 領域 A の雛形と変種・`area_bytes` |
| `test_http_coverage_ledger.py` | 面被覆の機械検査（C-1〜C-6。判定側なので宣言しない） |
| `test_http_health_liveness.py` | `/health` |
| `test_http_orchestrator_kickoffs.py` | `/kickoffs`・`/kickoffs/current`・`/kickoffs/{kickoff_id}` |
| `test_http_trigger_ops.py` ／ `test_http_trigger_errors.py` | trigger の 6 パス |
| `test_http_settings_reads.py` ／ `test_http_settings_writes.py` ／ `test_http_settings_errors.py` | settings の 18 パス |
| `test_http_correlation_roundtrip.py` | 53 操作の相関 ID 往復（宣言しない） |

正本の読み手は `apps/api/tests/_support/openapi_source.py`（本ディレクトリの外）。

⚠ **recipe は `http_calls` から引く**（`HttpCall` 型・`call_of`・`ALL_CALLS`・`HAPPY_CALLS` の
すべてがここに在る）。`http_calls.py` は**データだけ**を持つため 400 行の目安を超えるが、提供別に
割ると型と集約点が相互参照になるため 1 ファイルのままにしてある（`specs/014-http-contracts/design-sync.md`
の **B-15**／**HD-36**）。
