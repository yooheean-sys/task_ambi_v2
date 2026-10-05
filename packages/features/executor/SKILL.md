# SKILL: executor — CrewAI 実行器

**対象版: crewai 1.15.17**（依存宣言は `crewai>=1.15.17,<1.16`）

## ① 責務

1 回の kickoff を**組み立て・実行し・中立型へ翻訳して返す**（`CrewExecutor.run(CrewSpec) -> CrewOutcome`＝ IF-48）。
**AI に働かせる唯一の層**であり、crewai を独占する。
crewai の型・イベント名・例外は**契約の外へ 1 つも出さない**。

## ② 持たないもの

| 持たないもの | どこが持つか |
|---|---|
| プロンプトの文面・段や用途の判断 | stages（`PromptBundle` に組み上げて渡す） |
| 起票・close・依存張り・ラベル操作 | orchestrator（executor は**申告を持ち帰るだけ**） |
| 上限の判定・計測・等級付け | monitor（`LLMHttpHook` として注入される） |
| 認可のルール | settings（`Authorizer.enforce` として注入される） |
| HTTP 経路・UI ページ | 持たない（`router.py`・`ui.py`・`schemas.py`・`dependencies.py` を置かない） |
| 外部ストア | 持たない（盤面・保管庫・設定・リポジトリはすべて Protocol 経由。リポジトリは D25 の repo が所有） |
| `exceptions.py` | 契約例外は `common/interfaces/` が正本。内部例外は `ExecutorStartupError` 1 つで `__init__.py` に同居 |

## ③ 規約（**破ると設計が崩れる**）

- **crewai の型・イベント名・例外を契約に出さない**。`CrewEvent.detail` は `dict[str, str]` で、
  値は crewai のクラス名の**文字列**までとする。
- **道具は必ず `GuardedTool` を継承する**。派生は `_execute` を実装し、**`_run` を override しない**
  （前段を通さずに本体へ至る経路を 0 件にする）。
- **`LLM` の構築は `LlmFactory` だけ**。**`.env` の実値は `LlmFactory` だけが読む**。
- **鍵の解決に OS 環境変数を使わない**。鍵は `.env` を `dotenv_values` で直読みする
  （FR-022・憲章 §5）。`os.environ` を**書き換える**のは telemetry の `setdefault`
  （`_apply_telemetry_defaults`）と **SD-26 の復元**（`_restore_environment_after_crewai_import`）の
  **2 箇所だけ**である。`os.environ` を**読む**のは後者の **1 行だけ**で、その行末に
  **`allow-os-environ-read`** の印を付ける —— 鍵の解決ではなく**汚染の除去**だからである。
  **印を増やさない／綴りを替えて走査を避けない**（`name in os.environ` への書き換えは、
  番人から意味を奪うだけである）。走査の形と期待件数は ⑦ に書いてある。
- **`BaseException` を丸ごと捕まえる `except` を書かない**。階層外例外を捕まえるのは
  `service.run` の上限停止 1 箇所だけ。
- **段や用途ごとの道具集合をコードに戻さない**。持つのは「登録名 → 実体」の対応表だけで、
  どれを装備するかは設定の宣言（`Member.tools`／`PromptBundle.task_tools`）が決める。
- **import の向きを崩さない**: `__init__` → `service` → `tools/` → `models`。
  逆にすると `import executor` が実行時に循環で落ちる。
- **`CrewEvent.detail` のキーは 6 つだけ**（`event`・`tool`・`error`・`model`・`reason`・`path`）。
  **値は 200 文字で切る**。
- **役割の絞りは保管庫に触れる全道具で行う**。`narrow(session.folders, member.persona_key)` を
  **呼び出しのたびに**通し、絞った結果を持ち回らない。

### 罠（実測で踏んだもの）

- **`import crewai` がリポジトリの `.env` を `os.environ` へ丸ごと載せる**（**SD-26**）。`crewai.llm` は
  module 直下で `dotenv.load_dotenv()` を**無条件に**呼び、`find_dotenv()` が `.venv` から上へ辿って
  リポジトリ直下の `.env` に当たる（**逃げ道の環境変数は無い**）。放置すると鍵の実値がプロセス全体へ
  公開され（D15-i・FR-024）、先行モジュールの試験の隔離まで壊れる（`BEADS_DIR` が載ると board の
  `bd` 統合試験 33 件が setup で落ちる＝実測）。`executor/__init__.py` は crewai の import 直後に、
  **(1) crewai の import 前に無かった名前**と **(2) `.env` に定義があり現在の値が `.env` の値と
  一致する名前**の**和**だけを取り除く。**元からあって値が違う項目には触れない** ——
  `load_dotenv` は既存値を上書きしないので、値が違うならそれは利用者が意図して置いたものである。
  **2 本立てにするのは import 順に依存させないため**: テストの `conftest` のように **executor より先に
  crewai を import する経路**では、`_ENVIRON_BEFORE_CREWAI` の写しが既に汚れており 1 本目だけでは
  取りこぼす（実測で踏んだ）。取り除いた**名前しか返さず、値はログにも例外にも出さない**。
- **`.env` の差し替え点は `executor.models.env_file_path`** である。`executor.env_file_path`
  （再エクスポート）を差し替えても効かない —— `read_env_values()` は自分の module 大域を引く。
  `config_path` も同じ（`executor/__init__.py` 側も `executor_models.config_path()` で引いている）。
- **`tools/base.py` だけ `from __future__ import annotations` を置かない**。`GuardedTool` は
  pydantic のモデルで、派生は別モジュールにある。注釈を文字列のままにすると pydantic が
  派生側で `ToolContext` の欄を再解決しようとして `... is not fully defined` で落ちる。
- **`GuardedTool.ctx` は実行時 `Any`・型検査時 `ToolContext`** にしている。実行時に
  `ToolContext` のまま置くと pydantic が `ExecutorDeps` の 7 欄（**いずれも非 `runtime_checkable`
  な Protocol**）へ再帰し、スキーマの構築ごと落ちる。`SkipValidation`・`InstanceOf`・
  `PlainValidator` はいずれも**内側のスキーマを一度作ってから**包むので使えない。
- **`ExecutorConfig` は `strict=True`**。既定の緩い変換では `"30"` が黙って `30` に直り、
  「型が合わない」の起動中断が発火しない。
- **切り詰め量（2000 バイト）だけがコード定数**である。設定台帳に対応する欄が無く、
  `ExecutorConfig` は 6 キーに固定（過不足を許さない）なので、設定へ開くと契約が破れる。

## ④ Hook 装着の実測メモ

- 差し込み（`BaseInterceptor`）は **HTTP トランスポート層にしか装着されない**。基底クライアントを
  継承した台本 AI では**1 度も呼ばれない**。テストでは `httpx.Request`／`Response` を素で組んで
  アダプタを**直接呼ぶ**。
- `on_outbound`／`on_inbound` は抽象・`aon_outbound`／`aon_inbound` の既定は `NotImplementedError`。
  引数名は 4 本とも `message`。**4 つすべてを実装する**。
- `LLM.model_fields["interceptor"]` の注釈は `Any` だが、**実体側の検証器が `BaseInterceptor`
  派生のみ受理する**。注釈だけを見て「型で守られない」と判断しないこと。
- 上限停止（`RunHalted`＝ `BaseException` 直系）は、途中の `except Exception` を素通りする。
  アダプタは**握らない**。
- `@on(point, agents=…)` は装飾対象に **`_registered_hook`** を生やし、**元の関数を返す**。
  解除は `unregister_hook(point, 元の関数)`。**`agents` 付きで同じ関数を 2 回装飾すると 1 個目の
  包みが解除不能になる**（2 回目の解除が `False`）ので、**HookRef 1 件ごとに新しい関数オブジェクト
  を作ってから装飾する**。
- **差し込み点は列挙で渡す**。`InterceptionPoint` は `str` 継承の列挙だが**値は小文字**
  （`PRE_TOOL_CALL` → `"pre_tool_call"`）で、`HookRef.points` は**メンバ名**に一致する。
  `InterceptionPoint[ref.points]` で変換する（文字列をそのまま渡すと `KeyError`）。
- イベントハンドラは **`ThreadPoolExecutor` の別スレッド**で走り、`emit` は待たない。
  **追記はロックの中**で行い、kickoff の後に **`crewai_event_bus.flush()`**（既定 30 秒）を
  必ず通す。ハンドラ内の例外はバスが握り潰すので、**ハンドラが自分で握って**記録に残す。
- `emit` の配送は **`type(event)` の厳密一致**で MRO を辿らない。`BaseEvent` 1 本の購読では
  0 件しか届かないので **103 クラス全部**を購読する。
- **`dir(crewai.events)` を使わない**。モジュール `__getattr__` の遅延読込で、読み込み前の
  `dir()` には Event 名が 1 件も出ない。一覧は `__all__` ＋ `getattr` ＋ `issubclass` で導出する。
- **`Agent.skills`／`Agent.mcps` は渡さない**。2 欄は実在するが、`skills` の `str` は
  「技能の宣言名」ではなく**探索するディレクトリのパス**（実在しないと構築ごと落ちる）で、
  `mcps` は crewai 自身の設定型しか受けない。`Member` 側の値とは意味も型も合わない。
- `crew.kickoff()` の戻り `CrewOutput` から本文を取るのは **`raw`（`str`）**。
- **鍵の空文字はフレームワーク側で `None` に正規化される**。「`""` のまま続行」は
  **executor が `""` を渡すところまで**を指す。

## ⑤ `event_allowlist` の対象版と実クラス名

`crewai.events.__all__` は **108 件**・うち `BaseEvent` 派生は **103 件**（1.15.17 実測）。

| crewai のクラス名 | `CrewEventKind` |
|---|---|
| `LLMCallStartedEvent` | `llm_call_started` |
| `LLMCallCompletedEvent` | `llm_call_completed` |
| `LLMCallFailedEvent` | `llm_call_failed` |
| `ToolUsageStartedEvent` | `tool_called` |
| `ToolUsageFinishedEvent` | `tool_called` |
| `ToolUsageErrorEvent` | `tool_failed` |
| `LLMGuardrailStartedEvent` | `guardrail_failed` |
| `LLMGuardrailCompletedEvent` | `guardrail_failed` |
| `CrewKickoffCompletedEvent` | `crew_completed` |
| `CrewKickoffFailedEvent` | `crew_failed` |

- **1 クラス = 1 種類の単純な写像に固定する**。`ToolUsageFinishedEvent.failure` や
  `LLMGuardrailCompletedEvent.success` の欄で**分岐しない**。
- **`agent retry`・`summarization` は載せない**。1.15.17 に対応するイベントクラスが
  **1 件も存在しない**（`retry`／`summar` を名に含む `BaseEvent` 派生は 0 件）。載せると
  起動時検査 (b) で必ず中断する。
- 出来事 12 種の**三系統**: 購読の翻訳が 8 種／道具の層が直接積むのが 2 種
  （`tool_denied`・`outcome_overwritten`）／発生源なしが 2 種。
- **版を上げるときは必ずこの一覧を見直す**。依存宣言をマイナー上限で切っているのは、
  版を上げる作業に宣言の編集を挟ませて見直しを強制するためである。

## ⑥ 直接依存の版とライセンス

executor 自身の実行時依存は **`common`・`crewai`・`httpx`・`python-dotenv`** の 4 件。
このうち `crewai` が **31 件**の直接依存を連れてくる（`uv.lock` 全体は **167 エントリ**）。
**推移依存の全件確認は行っていない**——正本は `uv.lock` であり、記録先が未整備である
（憲章 §11 のライセンス互換成果物が不在＝ `specs/008-executor/design-sync.md` の **B-26**）。
本表は**憲章 §11 を「直接依存に絞った表」へ縮めた措置**である。

| パッケージ | 版 | ライセンス |
|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| crewai / crewai-core / crewai-cli | 1.15.17 | **配布メタデータに宣言なし（要確認 → B-26）** |
| pydantic | 2.12.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| aiofiles | 24.1.0 | Apache-2.0 |
| aiosqlite | 0.21.0 | MIT |
| appdirs | 1.4.4 | MIT |
| cel-python | 0.5.0 | **配布メタデータに宣言なし（要確認 → B-26）** |
| chromadb | 1.1.1 | Apache-2.0 |
| click | 8.4.2 | BSD-3-Clause |
| instructor | 1.15.4 | MIT |
| json-repair | 0.60.1 | MIT |
| json5 | 0.10.0 | Apache-2.0 |
| jsonref | 1.1.0 | MIT |
| lancedb | 0.30.0 | Apache-2.0 |
| mcp | 1.28.1 | MIT |
| openai | 2.54.0 | Apache-2.0 |
| openpyxl | 3.1.5 | MIT |
| opentelemetry-api / -sdk / -exporter-otlp-proto-http | 1.42.1 | Apache-2.0 |
| pdfplumber | 0.11.10 | MIT |
| portalocker | 2.7.0 | BSD-3-Clause |
| pyjwt | 2.13.0 | MIT |
| pyyaml | 6.0.3 | MIT |
| regex | 2026.1.15 | Apache-2.0 AND CNRI-Python |
| tokenizers | 0.23.1 | Apache-2.0 |
| tomli | 2.0.2 | MIT |
| tomli-w | 1.1.0 | MIT |

**注**: `crewai 1.15` は AI ルーティングライブラリを任意依存に変えたので、追加導入は不要である。
`pydantic` は crewai が `<2.13` を要求するため **2.13.4 → 2.12.5 に引き下がる**（先行 5 モジュールへ
波及するので、版を動かしたら全体テストを通すこと）。

## ⑦ テストコマンド（**1 コマンドずつ**実行する）

```bash
uv run pytest packages/features/executor/tests
uv run ruff check packages/features/executor
uv run pyright packages/features/executor
```

`&&` で連ねない（最初の失敗で後続が隠れる）。土台まで含めるなら
`uv run lint-imports` と `uv run pytest apps/api/tests/foundation` を足す。

**`os.environ` の読み取りの走査**（FR-022・憲章 §5 の機械的な番人。③ の印が付いた **1 行以外は 0 件**）:

```bash
rg -n 'getenv|environ\.get' packages/features/executor/src | grep -vc 'allow-os-environ-read'  # → 0
rg -n 'allow-os-environ-read' packages/features/executor/src | wc -l                            # → 1
```

1 本目は「印の付いていない読み取りが 0 件」、2 本目は「**印そのものが増えていない**」を見る。
2 本目が 2 以上になったら、増えた行が本当に汚染の除去かを疑う（鍵の解決なら FR-022 違反である）。
**テストは実 LLM・実 `.env` の実値・実 `bd`・実 vault・ネットワークを使わない**
（AI の偽装は `tests/conftest.py` の 4 層）。

## ⑧ 改修時の注意

- **道具を増減するとき触る 3 箇所**（現在 16 種。13〜16 種目は D25 の repo 系 `tools/repo_tools.py`）: `common/kernel/enums.py` の `ToolName`（**共有側**。
  変更には別 feature の裁定が要る）／`tools/` の実装ファイル／`tools/__init__.py` の
  `TOOL_BUILDERS`。3 つが揃わないと `build()` の起動時検査 (c) が中断する。
- **`service.py` が 500 行を超えたら `service/` に分ける**（`events.py`・`hooks.py`・`crew.py`）。
  **`models.py` が 400 行を超えたら**同様に分割を検討する（憲章 §3 の目安であり、行数そのものを
  問題にはしない）。
- **`event_allowlist` を増やすときは `EVENT_KIND_BY_CLASS` にも 1 行足す**。片方だけだと
  起動時検査 (b) が「対応する種類がコード側に無い」で中断する（黙って捨てない設計）。
- **`.importlinter` の `features-independent` と `layers` への追記漏れは、どの検査にも現れない**
  （実測）。落ちるのは `root_packages` の欠落だけである。
