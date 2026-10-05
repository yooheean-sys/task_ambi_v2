# モジュール依存関係 説明（第4フェーズ）

**正本の所在**: 裁定は [D09](./設計判断_V4-D09_フェーズ4前提とモジュール分割の裁定.md)。本書は図 `p4_module_dependency.mmd` と契約 `p4_module_contract.csv` の中の短文の正本であり、食い違えば D09 が勝つ。
**レジーム**: 改訂可（図・csv と同時に更新する）。

## 0. 一言でいうと

第3フェーズの 6 BC を **8 MOD**（feature パッケージ）に落とし、依存は全て common/interfaces の Protocol（提供 MOD 単位に 1 ファイル、役割別 14 Protocol）に向けた。feature 間の直接辺は 0 本、composition root は apps/api。BC:MOD が 1:1 でないのは BC-03（3 MOD）のみ。

## 1. MOD 一覧（BC:MOD 対応表）

| MOD | BC | 責務（1 行） | 所有 DM／ストア | 概算行数（PoC 実測ベース） | UI | 使う Protocol（利用側） | 提供 Protocol |
|---|---|---|---|---|---|---|---|
| `settings` | BC-01 | PJ・agents.yaml（メンバ・ペルソナ既定）・tasks.yaml（段テンプレート）・台帳・policy・rules・hooks・LLM プロファイル・上限・コマンド定義の CRUD、validate（順・逆・cmd・agents/tasks D16）、実行時 enforce／resolve_command、設定 UI | DM-01〜07/09/11/12/26/27／設定ファイル領域 A | ≈2,500〜3,000（casbin PoC の config・authz・fleet・fleet_ui 相当。demo scenes・mock_llm 除く）。**憲章 §1 目安超過を受容**（D09-a） | あり（UC-07/08） | BoardReader.has_open_issues／VaultReader.check_project_folders | SettingsReader・MemberResolver・Authorizer・CommandRegistry |
| `board` | BC-02 | beads アダプタ（bd CLI のみ）。盤面導出 children_of／open_question_of／確認読取＝D01 判定の唯一の出所 | DM-13〜16／beads | ≈400（renkei `bd.py` 335） | なし | — | BoardReader・BoardWriter |
| `orchestrator` | BC-03 | 外側ループ・claim・段解決・recover_orphans・盤面/vault 依存ゲート（D01 二重ガード・outcome 実在）・起票（1 箇所）・close・確認読取・single-flight・kickoff（DM-18/19）保持・human 起動 `POST /kickoffs` | DM-18/19／なし | ≈1,200（`loop.py` 1,823 からゲート・テンプレート分を除く） | なし（router のみ） | BoardReader/Writer・SettingsReader・MemberResolver・VaultReader（存在検証）・RunMonitor・StageCatalog・ProposalGate・CrewExecutor | KickoffRunner |
| `stages` | BC-03 | プロンプト組立（テンプレート TaskTemplates・ペルソナ本文は Member として**引数で受ける** D16-h）・段の構造属性（DM-08）・提案の形のゲート（min_items・allowed_personas・acceptance 書式・衝突・coverage）・outcome トークン抽出。**他 Protocol を一切使わない純粋 MOD** | DM-08（構造属性）／なし | ≈500（`backlog.py`/`acceptance.py` の検証部＋組立。テンプレート・ペルソナ本文は領域 A へ D16） | なし | —（kernel のみ） | StageCatalog・ProposalGate |
| `executor` | BC-03 | CrewAI Crew/Agent/Task 構築・LLM 構築（Hook 装着）・Tools（read_issue／record_progress／propose_breakdown／complete／escalate／submit_review／vault 読書／run_command）・提案収集・CrewAI 例外／イベントの中立型への翻訳 | なし | ≈1,200（`crew.py` 528＋`tools.py` 716 相当） | なし | BoardReader.show・BoardWriter.comment・VaultReader・OutcomeWriter・Authorizer・CommandRegistry・LLMHttpHook（構築時に受け取る） | CrewExecutor |
| `vault` | BC-04 | vault 領域アダプタ・P1 ゲート（外縁・Windows 禁止文字・不在検出） | DM-17／Obsidian vault | ≈600（casbin `safefs.py` 586 相当） | なし | — | VaultReader・OutcomeWriter |
| `monitor` | BC-05 | LLM HTTP Hook（計測・上限停止）・失敗検出（events・出力検査）・等級 F/W/I・封筒・bd 写像・確認読取 | DM-20/21／なし | ≈900（ai_control `probe/p1,p2/interceptor.py`・listener 相当） | なし | BoardReader（show・comments）・BoardWriter（set_labels・release・close・comment） | RunMonitor・LLMHttpHook（＋RunHalted 例外型） |
| `trigger` | BC-06 | daemon・APScheduler・watchdog・REST・実行履歴 csv・トリガー UI | DM-22〜25／領域 B・履歴 csv・DL フォルダ | ≈900（新規。付録トリガー） | あり（UC-09/10） | KickoffRunner | — |

依存方向（MOD 粒度）: trigger → orchestrator → {stages, executor, settings, board, vault, monitor}；executor → {settings, board, vault, monitor}；monitor → board；settings → {board, vault}。**循環なし**（構築順 §4 が存在することが証明）。

## 2. Protocol 一覧（common/interfaces。定義の正本は csv）

| ファイル | Protocol | 実装 | 利用 | 役割 |
|---|---|---|---|---|
| `board.py` | `BoardReader` | board | orchestrator・executor・monitor・settings | show／children_of／open_question_of／list_working／comments／has_open_issues |
| `board.py` | `BoardWriter` | board | orchestrator・executor・monitor | claim_next／create／add_dependency／move_dependents／release／comment／close／set_labels |
| `settings.py` | `SettingsReader` | settings | orchestrator | get_project／get_limits／get_rules／get_hooks／get_tasks（IF-50 D16-h） |
| `settings.py` | `MemberResolver` | settings | orchestrator | resolve（project×persona→Member。CrewAI フィールドを載せる D16-h）／is_human（台帳照合） |
| `settings.py` | `Authorizer` | settings | executor | enforce |
| `settings.py` | `CommandRegistry` | settings | executor | resolve_command |
| `vault.py` | `VaultReader` | vault | orchestrator・executor・settings | check_project_folders／list_inputs／read_input／read_outcome_file／outcome_exists |
| `vault.py` | `OutcomeWriter` | vault | executor | write_outcome |
| `monitor.py` | `RunMonitor` | monitor | orchestrator | begin／finish（写像込み） |
| `monitor.py` | `LLMHttpHook` | monitor | executor | on_outbound／on_inbound／aon_outbound／aon_inbound。`RunHalted(BaseException)` を同ファイルに置く |
| `stages.py` | `StageCatalog` | stages | orchestrator | resolve_stage／build_prompt |
| `stages.py` | `ProposalGate` | stages | orchestrator | validate_proposal／extract_outcome_tokens |
| `executor.py` | `CrewExecutor` | executor | orchestrator | run |
| `orchestrator.py` | `KickoffRunner` | orchestrator | trigger | run（同期） |

受け渡し型（Pydantic・振る舞いなし）37 件は **Protocol と同じ `common/interfaces/<提供 MOD>.py` に置く**（憲章 §1・§3。**2026-08-18 第4.5フェーズ DEC-002 で kernel から移動**。置き場の規則: その型をシグネチャに使う Protocol の提供 MOD のファイル。複数 Protocol が使う型は最初に生む側の提供 MOD）:

| ファイル | 受け渡し型 |
|---|---|
| `board.py` | `Issue`・`Comment`・`IssueSpec`・`ClaimFilter` |
| `settings.py` | `Project`・`ProjectFolders`・`Member`・`LLMProfile`・`LimitPolicy`・`Rules`・`HooksConfig`・`TaskTemplates`（D16-h） |
| `vault.py` | `FolderCheck`・`FileRef`・`WrittenPath`（＋列挙 `VaultArea`。契約 csv IF-31〜37 の戻り型。P5D-012） |
| `stages.py` | `StageSpec`・`PromptBundle`・`Proposal`・`GateResult` |
| `executor.py` | `CrewSpec`・`CrewOutcome`・`CrewEvent` |
| `monitor.py` | `RunVerdict`・`Measurement`（＋例外型 `RunHalted`） |
| `orchestrator.py` | `ExecutionContext`（DM-19）・`KickoffResult` |

**第5フェーズでの追記（2026-08-19。属性の正本は各 MOD の `../phase5/<MOD>/p5_<MOD>_data_model.mmd` の namespace `common_interfaces_<MOD>_py`）**: `board.py` ＋`IssueRef`・`IssueStatus`／`vault.py` ＋`FolderCheck`・`FileRef`・`WrittenPath`・`VaultArea`（契約 csv IF-31〜37 の戻り型。P5D-012。**本項は 2026-08-21 に §2 の表へ昇格済み**＝上表 `vault.py` 行）／`settings.py` ＋`ProviderKind`・`HookRef`・`McpSpec`／`monitor.py` ＋`FailureEvent`・`Grade`・`FailureKind`・例外 `MappingUnverified`／`stages.py` ＋`ContextRefs`・`ProposalItem`・`PromptPurpose`（P5D-026）／`executor.py` ＋`CrewEventKind`・`Escalation`・`ReviewVerdict`・例外 `ExecutorFailure`（P5D-033）／`orchestrator.py` ＋`LaunchKind`・`TriggerSnapshot`・`KickoffStatus`・`IssueRunSummary`・例外 `KickoffBusy`（P5D-038）／**P5-R1（2026-08-20 D16）**: `settings.py` ＋`TaskTemplates`・`TaskTemplate`（IF-50 の戻り型）、`Member` に CrewAI フィールド（role・goal・backstory・tools・max_iter・max_rpm・max_execution_time）を追加・`Rules` は project_rules のみに縮小。契約例外は提供 MOD の interfaces ファイルで定義し kernel の 4 基底（`NotFoundError`／`UnavailableError`／`UnverifiedError`／`ViolationError`）を継承する（P5D-001。`RunHalted` のみ BaseException 直系）。

kernel（共有カーネル）に置くのは **列挙**（`persona_key`・段名・ラベル語彙 `kind:*`／`project:*`／`persona:*`／`role:executor`／`state:working|failing`／`note:*`。D09-d）、**log 基盤**（correlation_id contextvar・構造化ログ）、**共通例外の基底**のみ。受け渡し型は置かない。

## 3. 主要な流れが Protocol をどう通るか（シーケンスとの対応）

- **UC-11 発火→claim**: trigger が ctx（DM-19）を組み contextvar に correlation_id を立て `KickoffRunner.run(ctx)` を同期呼び出し（APScheduler executor／watchdog スレッド上）。orchestrator は single-flight を取り、`BoardReader.list_working`＋`children_of`／`open_question_of` で回収（D01・D08-e）→ `BoardWriter.claim_next(filter)`。戻り `KickoffResult` を trigger が履歴 csv に追記。
- **UC-I1 1 周回**: `BoardReader.show` → `StageCatalog.resolve_stage` → `MemberResolver.resolve` → `SettingsReader.get_project/get_rules/get_hooks/get_limits/get_tasks` → `VaultReader.check_project_folders` → `RunMonitor.begin` → `StageCatalog.build_prompt`（templates・Member を引数で受ける D16-h） → `CrewExecutor.run(CrewSpec)`（executor は Tools から `VaultReader`／`OutcomeWriter`／`BoardReader.show`／`BoardWriter.comment`／`Authorizer`／`CommandRegistry` を呼び、LLM 呼び出しは `LLMHttpHook` を通る）→ propose 段: `ProposalGate.validate_proposal` → 合格なら `BoardWriter.create`×n・`add_dependency`／`move_dependents` → `BoardReader.children_of`（確認読取）→ `close`。execute 段: `ProposalGate.extract_outcome_tokens` → `VaultReader.outcome_exists` → レビュー run → `close` または UC-04 起票。最後に `RunMonitor.finish` → `RunVerdict`。
- **UC-12/13 上限・失敗**: Hook が閾値超過で `RunHalted` を送出（BaseException 直系で CrewAI・openai SDK のリトライを素通り。Z-6）→ executor が捕捉し `CrewOutcome(halted=True)` → orchestrator が `RunMonitor.finish` → monitor が F: `set_labels(state=failing)`＋`release`＋`comment(封筒)`／W: `close`＋`comment(封筒)`／I: 付記のみ、`show`／`comments` で確認読取、読めなければ `MappingUnverified` で停止（P3）。W で monitor が close 済みのとき orchestrator は自分の close を抑止（`RunVerdict.closed`）。
- **UC-04 エスカレーション**: executor の `escalate_to_human` Tool は要求を `CrewOutcome` に載せて返すだけ。起票（`create` kind=question）・`add_dependency`（blocks）・`release` は orchestrator（起票 1 箇所）。回答検出は次周回の `BoardReader.comments`＋`MemberResolver.is_human`（FN-07/10）。
- **UC-07/08 設定**: settings の UI は HTTP で自 router を叩く。削除ガードは `BoardReader.has_open_issues`、登録時の存在確認は `VaultReader.check_project_folders`。

## 4. composition root（apps/api）の構築順

Protocol 実装のインスタンス依存が一方向なので次の順で `build(deps)` を呼べる（憲章 §3 補足「構築不能＝見直しシグナル」に当たらない）:

1. `board`・`vault`・`repo`（依存なし。`repo` は D25 で追加）
2. `settings`（BoardReader・VaultReader）
3. `monitor`（BoardReader・BoardWriter）
4. `stages`（依存なし）・`executor`（BoardReader/Writer・VaultReader・OutcomeWriter・Authorizer・CommandRegistry・LLMHttpHook・RepoReader・RepoWriter）
5. `orchestrator`（上記全部＋StageCatalog・ProposalGate・CrewExecutor・RunMonitor・RepoReader）
6. `trigger`（KickoffRunner）

各 MOD の `__init__` は **`build(deps: <Mod>Deps) -> <Mod>Handle`**（ともに frozen dataclass。Deps＝使う Protocol の snake_case 属性、Handle＝提供 Protocol 実装＋`router: APIRouter | None`＋`lifespan`（常駐機能を持つ trigger のみ）。**2026-08-19 D11-f で共通形を確定し、旧記述の「ui ページ参照」は handle から外した**）を公開し、apps/api は router を include・lifespan を束ね、apps/ui は settings・trigger の `ui.py` を直接 import して束ねる（業務ロジックなし、HTTP のみ。ui は別プロセスなので apps/api の build 戻り値には載せない）。

## 5. 自己検証（D09-h 完了条件）

`p4_selfcheck.py` を `python3` で実行し、以下を機械検査した（結果 ERRORS: none）:

- 図の辺 33 本のうち **feature MOD → feature MOD の直接辺 0 本**。feature からの辺は Protocol ノード・kernel・外部システムにのみ向く。implements 辺は Protocol ファイルごとに 1 本
- csv 49 行の提供／利用 MOD が 8 MOD 内で、Protocol 名が図の該当ノードのラベルに存在し、利用 MOD から該当ノードへの uses 辺がある。逆に uses 辺には 1 行以上の csv 行がある
- p3 notes §3 の BC 間参照 8 系統（REL-06-03／03-01／03-02／03-04／03-05／05-02／01-02／01-04）＋BC-03 内 2 系統（REL-03-03a/b）が csv の source_id に全て現れる
- linter 断片の root_packages・independence・layers 中段が 8 MOD と一致
- `mmdc` で描画できる（svg／png）

## 6. p3 からの差分・気づき

- p3 notes §3 の「BC-03 → BC-01: 設定読取・メンバ解決・台帳照合・enforce・resolve_command」は、利用側が **orchestrator（読取・解決・照合）と executor（enforce・resolve_command）に分かれた**。executor は LLM プロファイル・上限・rules を自分では引かず、orchestrator が `CrewSpec` に詰めて渡す（executor の依存を Tools 用に絞る）
- CrewAI のイベントバス（UC-12/13 の `BaseEvent`）は monitor が直接購読しない（`crewai` は executor のみ。linter (3)）。executor が中立型 `CrewEvent` に翻訳して `CrewOutcome.events` で返し、orchestrator が `RunMonitor.finish` に渡す。逐次購読が必要になったら `CrewEventSink` Protocol を足す（D09 §6）
- `RunHalted` は common/interfaces/monitor.py に置く例外型（monitor が送出・executor が捕捉。両者は互いを import できないため）
- human 起動（`POST /kickoffs`）は trigger を通らないので DM-24 履歴に残らない（D09-e）

## 7. 改訂履歴

- 2026-08-18（第4.5フェーズ DEC-002）: §2 の受け渡し型 22 件を kernel から `common/interfaces/<提供 MOD>.py` へ移動（憲章 §1・§3 との突合 `../phase45/p45_constitution_diff.csv` DIFF-02）。図の IFACES／KERNEL ラベルと FEATURES→KERNEL 辺のラベルを同時に改訂。契約 csv（メソッド定義）は不変
- 2026-08-19（第5フェーズ D11-f）: §4 の `build(deps) -> handle` を共通形 `build(deps: <Mod>Deps) -> <Mod>Handle` に確定し、handle から「ui ページ参照」を外した（apps/ui は `<mod>.ui` を直接 import。linter (3) と整合）。図は不変
- 2026-08-19（第5フェーズ）: §2 に受け渡し型の追記（22 件 → 各 MOD の詳細設計で確定した型を追加。vault の戻り型 4 件は契約 csv にあり notes に漏れていたもの）と契約例外の基底（P5D-001）を記載。図・契約 csv（メソッド定義）は不変
- 2026-08-21（`001` common 実装。design-sync A-8）: §2 の受け渡し型の**表**に `vault.py` 行を新設し `FolderCheck`・`FileRef`・`WrittenPath`・`VaultArea` を載せた（P5D-012 の裁定は「表に追記する」だったが表に行が無く、追記段落にのみ在って二重管理になっていた＝DIFF-02）。あわせて §2 本文の件数表記を実数に改めた（受け渡し型 22 件 → **37 件**＝DIFF-04）。図・契約 csv・辺・linter は不変
- 2026-08-20（P5-R1 D16）: AI 設定の CrewAI 規約化（D15-j）を反映。契約 csv IF-25（get_rules は project_key のみ）・IF-27（Member に CrewAI フィールド）・IF-45（build_prompt が Member・TaskTemplates を受ける）・IF-48（注記）を改訂し **IF-50 `SettingsReader.get_tasks` を新設**（50 行）。§1 settings／stages 行・§2（get_tasks・TaskTemplates）・§3 UC-I1 連鎖・図の settings／stages ラベルを改訂。辺・linter は不変
- 2026-08-24（`004` vault 実装。design-sync B-4〜B-6）: 契約 csv の**例外列**を vault の 4 行だけ補記した。**IF-31・IF-32**（`check_project_folders`）の `—` → `VaultPathViolation`（宣言が vault ルートの外を指す場合。不在は例外にせず `FolderCheck.missing` に載せる＝ FR-035・DIFF-05 の裁定）。**IF-33**（`list_inputs`）の `VaultPathViolation` → `VaultNotFound`（役割が宣言に無い／領域フォルダが実在しない。空の一覧に倒さない＝ FR-039／040・DIFF-07）を追加。**IF-37**（`write_outcome`）に `VaultNotFound`（`outcome_dir` が実在しない＝ VD-19）を追加し、出力列に「返すのは vault ルート相対」を明記した。退避失敗の `FsArchiveError` は **vault 内部型で呼び手が import できない**（feature 独立契約）ため台帳には載せず、`vault/SKILL.md` と D22 の記録に留めた（FR-048・DIFF-06）。`—` が「例外を一切上げない」と読めて呼び手が捕捉を書かないのを避けるための補記であり、**メソッド定義・図・辺・linter は不変**
