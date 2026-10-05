# monitor（BC-05 実行監視）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_monitor_sequence.mmd`／`p5_monitor_data_model.mmd`／`p5_monitor_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-020〜024。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-38〜43（提供 6 行）・IF-03/08/15/18/20/22（利用 6 行）／p4 notes §1・§3／`../../phase2/p2_domain_model.mmd` DM-20/21／`p2_sequence_UC-12-13.mmd`／`docs/initial_memo/付録：AI 制御機能 — 要求と解決策.md` §3.1〜3.3・§4・§5／D04-a,b・D06-g,i,k・D09-b／PoC `crewai_ai_control`（Z-1〜Z-6）・`probe/p2/interceptor.py`（コスト上限 `ProbeCostLimitExceeded`）・`probe/lib/listener.py`（読取のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

monitor は **「止めて正確に報告する」層**で、回復の判断を持たない（付録 §3.3(a)-5）。`LLMHttpHook`（HTTP transport 層の計測と上限停止。`RunHalted` は BaseException 直系）と `RunMonitor`（begin／finish。finish で失敗検出 → 等級 F/W/I → 封筒 → bd 写像 → 確認読取）を実装する。等級表は承認済みで割当は config.json（P5D-020）。Hook の帰属は single-flight 前提の「進行中の 1 kickoff」（P5D-021）。CrewAI のイベントバスは購読せず executor が翻訳した `CrewEvent` を finish で一括評価（P5D-022）。永続ファイルを所有せず、写像先 beads は board 経由、I 級は呼び手が実行履歴へ。

## 1. ファイル構成（憲章 §3）

```
packages/features/monitor/
├── pyproject.toml
├── config.json          # MonitorConfig: grade_map（kind→F/W/I）・envelope_marker・token_estimator／token_encoding・cost_field_path・truncation_phrases
├── SKILL.md             # crewai を import しない（linter (3)）／RunHalted は BaseException 直系を守る／Hook でヘッダ値を記録しない
├── src/monitor/
│   ├── py.typed         # 空ファイル。無いと pyright の型検査対象から外れる（先行 4 モジュールが全て持つ）
│   ├── __init__.py      # build(deps: MonitorDeps, *, config: MonitorConfig | None = None) -> MonitorHandle（run_monitor／llm_http_hook／router=None）
│   ├── service.py       # MonitorService(RunMonitor): begin／finish・検出規則・等級付け・封筒・bd 写像・確認読取
│   ├── repository.py    # HttpHook(LLMHttpHook): httpx transport 層のアダプタ（on_outbound／on_inbound／aon_*）・TokenEstimator・RunState への帰属
│   ├── models.py        # RunState／Envelope／DetectionRule（内部 VO）
│   └── （exceptions.py は置かない）  # 内部例外は起動時の MonitorStartupError 1 つだけなので __init__.py に同居（契約例外 RunHalted・MappingUnverified は common/interfaces/monitor.py）
└── tests/               # 単体: BoardReader／BoardWriter をスタブ、Hook は httpx の Request/Response を直接渡す（crewai 不要）
```

`build(deps: MonitorDeps, *, config: MonitorConfig | None = None) -> MonitorHandle`: `MonitorDeps(board_reader, board_writer)`。**任意引数 `config` で設定を差し替えられる**（テストは `config.json` を読まずに組む）。`MonitorHandle(run_monitor, llm_http_hook, router=None)`。`llm_http_hook` は composition root が `executor.build(deps)` に渡し、executor が CrewAI の `BaseInterceptor` に包んで `LLM(interceptor=)` に装着する（D09-b。monitor は crewai を知らない）。

## 2. Protocol 実装の対応

| Protocol.メソッド | if_id | fn | 要点 |
|---|---|---|---|
| RunMonitor.begin | IF-38 | F06 | RunState 開始・Hook に attach。二重 begin は警告して差替え（P5D-024） |
| RunMonitor.finish | IF-39 | F07（＋F08〜F11） | 検出→等級→封筒→写像→確認読取→RunVerdict／MappingUnverified |
| LLMHttpHook.on_outbound | IF-40 | F02（＋F05） | 送信前に T・回数・コストを判定、超過は RunHalted で送信しない |
| LLMHttpHook.on_inbound | IF-41 | F03 | usage・cost・finish_reason の観測 |
| LLMHttpHook.aon_outbound／aon_inbound | IF-42/43 | F04 | 非同期側も同内容（Z-4） |
| （利用）BoardWriter.comment／set_labels／release／close | IF-18/22/15/20 | F10 | F: 封筒→failing→release／W: 封筒→close |
| （利用）BoardReader.show／comments | IF-03/08 | F11 | 確認読取 |

monitor が持たないもの: 要約器（作らない。付録 §3.3）、巻き戻し（§5）、回復の判断（タスク分割・再実行は人間／上位層）、CrewAI イベントバスの購読（executor が翻訳）、LLM の構築（executor）、上限値・T の決定（settings。monitor は受け取るだけ）、実行履歴への書込（trigger）。

## 3. 受け渡し型（common/interfaces/monitor.py）

`Grade`（F/W/I）／`FailureKind`（12 種）／`FailureEvent`（grade・kind・evidence・at。**追記**）／`Measurement`（kickoff_id・requests・prompt/completion tokens・cost_usd・cost_missing_count・max_tokens_before_send・halted_reason・started/finished・unattributed_requests）／`RunVerdict`（grade・kinds・halted・closed・envelope・measurement・failures）／例外 `RunHalted(BaseException)`（kind・measured・limit・request_no）・`MappingUnverified(UnverifiedError)`（**追記**）。`CrewOutcome`・`CrewEvent` は executor の型（**現物 `common/interfaces/executor.py` で確定済み**。`CrewOutcome` は 11 フィールド、`CrewEventKind` は 12 種。monitor は events・proposal・task_output・halted・halted_reason・completed を読む）。

## 4. シーケンス図

`p5_monitor_sequence.mmd`（主図のみ）: begin → loop（on_outbound 判定／RunHalted／on_inbound 観測）→ finish（検出・等級・封筒・F/W/I 写像・確認読取・RunVerdict／MappingUnverified）。レーン: `RunMonitor`・`LLMHttpHook`（呼び手）／`monitor/service.py`・`monitor/repository.py`／`BoardReader`・`BoardWriter`（利用先）。

## 5. PoC からの継承と変更（証跡: crewai_ai_control）

| 項目 | PoC | V4 |
|---|---|---|
| 差し込み点 | `LLM(interceptor=X)`（Z-1、provider の同期・非同期 httpx に装着） | 同。装着は executor、monitor は httpx 型の Hook のみ（D09-b） |
| 上限停止例外 | `ProbeCostLimitExceeded(BaseException)`（p2） | `RunHalted(BaseException)`（kind 付き。回数・コスト・送信前トークンの 3 種） |
| 記録方針 | ヘッダ値を残さない／event-stream を読まない／body sha256 | 同（sha256・故障注入・応答差替えは移植しない。付録 §4） |
| コスト | 応答 body の `usage.cost`（OpenRouter） | 同。無ければ usage_missing（I）。ローカル LLM は常に欠落＝コスト上限は効かない（回数・T で守る） |
| 送信前トークン | 無し（A-08） | TokenEstimator（**既定は chars/4 の近似**。tiktoken は明示指定したときだけで未導入なら起動中断。**tiktoken を実行時依存に加えない**＝ SD-14。▲ M1 で精度実測） |
| 検出器 | `ProbeListener`（全 BaseEvent 購読） | 購読しない。executor 翻訳の CrewEvent を finish で評価（P5D-022） |
| 等級・写像 | 草案（§3.2(b)） | 承認・config 化（P5D-020）、写像 D06-i、順序 P5D-024 |
| kickoff 帰属 | プロセス＝1 run | RunState（single-flight 前提。P5D-021） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py monitor --render` → ERRORS: none
- participant は Protocol 名 4・内部ファイル 2 のみ（外部実体なし）
- 提供 6 行・利用 6 行の if_id が feature_list に現れ、6 メソッド全てに実装 fn

## 7. 未決（monitor 固有）

1. **contextvar の貫通**（D09 §6-3 の実測は M1 のまま）: 本設計は帰属に使わないが、警告ログの発生状況で実測結果が得られる
2. **openrouter provider 経路での Hook 装着**（P5D-013 の帰結）: executor の未決。monitor の Hook は httpx 型なので provider に依らず同じ
3. **`no_output` の「定型のみ」の判定語彙**: 設計・設定台帳・共有側のどこにも無く、`config.json` が 6 項目に固定されているため置き場も無い。実装は `None`／空文字／空白のみの 3 つに限った（**spec FR-040 の一部を意図して未達**）。裁定待ち＝ SD-15・B-25

**決着した項目**（007-monitor の実装で確定）:

- **送信前トークン推定の方式**（旧 1）: 既定を **`chars/4` の近似**に確定し `tiktoken` は依存に加えないことにした（SD-14）。精度の実測は M1 のまま
- **強制最終回答の照合文言**（旧 3）: 対象版を **`crewai 1.15.10`** に固定した（実体＝ `crewai/translations/en.json:47` の `errors.force_final_answer`）。現物と 1 バイト違わないことを確認して `config.json` に置き、`SKILL.md` §5 に版を記録した
- **`exceptions.py` を置かない**（旧 5）: 内部例外が `MonitorStartupError` 1 つだけなので `__init__.py` に同居させた（stages と同型）
