# orchestrator（BC-03 分解・遂行 — 外側ループ）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_orchestrator_sequence.mmd`（主図: run の骨格）／`p5_orchestrator_sequence_propose.mmd`／`p5_orchestrator_sequence_execute.mmd`／`p5_orchestrator_sequence_recover_human.mmd`（副図 3）／`p5_orchestrator_data_model.mmd`／`p5_orchestrator_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-034〜038。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-49（提供 1 行）・利用 **29 行**（実測: board 13・settings 7・vault 2・monitor 2・stages 4・executor 1。旧記載の 32 行は誤り＝ DIFF-06）／p4 notes §1・§3・§4／`p2_sequence_UC-11.mmd`・`UC-I1.mmd`・`UC-04.mmd`／D01・D04-b,c,d・D06-a,c,f,i・D08-d,e・D09-e,f／PoC `crewai_beads_renkei`（`run_loop`・`recover_orphans`・`_handle_issue`・`_flush_proposals`・`_open_escalation`・`RETURN_MARKER`／`RECOVER_MARKER`）（PoC 基準版、読取のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

orchestrator は **外側ループ**で、9 Protocol の利用側として 1 kickoff を回す: single-flight → recover_orphans → claim → 前処理（段・主体・設定・フォルダ）→ 人間指示の収集 → monitor.begin → stages.build_prompt → executor.run → 段別処理（propose: 検証・差し戻し・起票順序不変条件／execute: 完了ゲート・レビュー・close／エスカレーション）→ monitor.finish → 次の ready。**起票・close・質問起票・依存張りは本 MOD だけ**（D01 起票 1 箇所）、判定の出所は board の盤面導出。人間へ渡す形は **state:failing＋[orchestrator] コメント＋release** に統一し、claim 直後の failing は触らない（P5D-034）。human 起動は自分の router（POST /kickoffs。P5D-035）。永続ファイルを所有しない。

## 1. ファイル構成（憲章 §3）

```
packages/features/orchestrator/
├── pyproject.toml
├── config.json          # OrchestratorConfig: max_returns・recent_results_n・system_actor・comment_marker・review_persona
├── SKILL.md             # 起票・close は service だけ／判定は盤面から都度引く（キャッシュ禁止）／人間へ渡す形は HandOff 1 経路／差し戻し回数は beads のコメントで数える
├── src/orchestrator/
│   ├── __init__.py      # build(deps: OrchestratorDeps) -> OrchestratorHandle（kickoff_runner／router）
│   ├── router.py        # POST /kickoffs・GET /kickoffs/{id}・GET /kickoffs/current
│   │                    #   **入口の correlation_id をワーカースレッドへ引き継ぐ**（入口で bind しない）
│   ├── schemas.py       # HTTP DTO（KickoffRequest／KickoffStatusResponse）
│   ├── service/         # OrchestratorService(KickoffRunner)。**8 分割**（MOD 境界は不変）
│   │                    #   service/{loop,recover,prepare,kickoff,propose,execute,handoff,writes}.py
│   │                    #   prepare＝前処理と指示収集（書かない）／kickoff＝begin〜finish と判定の受け
│   │                    #   writes＝create／close を呼ぶ唯一の場所（SC-027）
│   ├── models.py        # SingleFlight・KickoffRecord・IssueRun・HandOff（内部 VO）
│   │                    #   ＋ RecordStore・Ports・StepResult 3 形・結果の語彙の写像表・印の定数
│   ├── dependencies.py  # Depends: service・注入 Protocol の受け口
│   └── exceptions.py    # なし（契約例外 KickoffBusy は common/interfaces/orchestrator.py）→ 置かない
└── tests/               # 単体: 9 Protocol をスタブ（実 bd・実 vault・実 LLM に触れない）
```

`build(deps: OrchestratorDeps) -> OrchestratorHandle`: Deps＝board_reader・board_writer・settings_reader・member_resolver・vault_reader・run_monitor・stage_catalog・proposal_gate・crew_executor。Handle＝kickoff_runner・router。ui なし。

**規模の受容**: 設計時の見積り ≈1,200 行に対し、実装の `src` は **≈2,500 行**である。差の内訳は
(a) HTTP の 3 本の口（router・schemas・dependencies ≈240 行。受容は service の行数だけを数えていた）、
(b) 親条文の被覆検査（B-25 で orchestrator へ移った ≈40 行）、(c) 起動時検査 5 段と台帳照合（≈110 行）、
(d) 周回の終了条件（FR-022a/b）と結果の語彙の写像表（≈80 行）、(e) 日本語 docstring（憲章 §7-3）。
先行 6 モジュールもすべて受容を上回っている（実測 1,226〜1,821 行）。憲章 §1 の分割検討水準
（100,000 トークン）には達していない。

## 2. Protocol 実装と利用の対応（要点）

| 相手 | メソッド | fn |
|---|---|---|
| 提供 KickoffRunner.run | IF-49 | F01（＋F02〜F14） |
| BoardReader | show／children_of／open_question_of／list_working／comments | F05／F03・F08／F03・F06・F10／F03／F06 |
| BoardWriter | claim_next／create／add_dependency／move_dependents／release／comment／close／set_labels | F04／F08・F10／F08・F10／F08／**F03**・F04・F10・F12／**F03**・F08・F09・F12／F08・F09／**F03**・F08・F12 |
| SettingsReader／MemberResolver | get_project・get_limits・get_rules・get_hooks・get_tasks（IF-50 D16-h）／resolve・is_human | F05／F05・F09／**F06・F15**（F15＝起動時の台帳照合 `is_human(system_actor)`＝ FR-084a） |
| VaultReader | check_project_folders／outcome_exists | F05／F09。orchestrator は入力を列挙しないので `ContextRefs.input_refs` は**常に空**で渡す（DIFF-18。埋める主体の確定は範囲外） |
| RunMonitor | begin／finish | F07／F07・F11 |
| StageCatalog／ProposalGate | resolve_stage・build_prompt／validate_proposal・extract_outcome_tokens | F05・F07・F08・F09／F08／**F08・F09**（F08＝親条文の被覆検査 B-25） |
| CrewExecutor | run | F07・F08・F09 |

**fn 欄は「要求元」を指す**。`create`／`close` の**物理的な呼び出し**は `service/writes.py` の 1 箇所に
閉じる（SC-027）—— `close` を実行するのは `service/kickoff.py`（F07・F11）だが、要求したのは F08／F09 の
`CloseIntent` である（DIFF-22）。

orchestrator が持たないもの: 盤面の導出そのもの（board）、提案の形の検証・プロンプト組立（stages。テンプレート実体は settings の tasks.yaml D16。orchestrator は get_tasks で読み stages に渡す運び役）、LLM・Tools（executor）、上限・等級・F/W 写像の中身（monitor）、設定の正本（settings）、履歴 csv（trigger）。

## 3. 受け渡し型（common/interfaces/orchestrator.py）

`ExecutionContext`（kind・trigger_snapshot・dropped_path・claim_filter・requested_at）／`LaunchKind`（human|timer|file|manual。**追記**）／`TriggerSnapshot`（**追記**）／`KickoffResult`（kickoff_id・status・issues・notes・started/finished）／`KickoffStatus`（ok|noop|stopped|busy。**追記**）／`IssueRunSummary`（**追記**）／例外 `KickoffBusy(ViolationError)`（**追記**）。p4 notes §2 の 2 件に 5 件追記（フェーズ末改訂。P5D-038）。

## 4. シーケンス図

| 図 | 対象 |
|---|---|
| `p5_orchestrator_sequence.mmd`（主図） | run の骨格: single-flight → 回収 → claim（failing 除外）→ 前処理 → 指示収集 → begin → build_prompt → executor.run → 段別（副図）→ finish → 次 |
| `_propose.mmd` | 二重ガード → validate → 差し戻し（上限）→ create×n → add_dependency → move_dependents → children_of 確認 → single-child 観測印 → close → show |
| `_execute.mmd` | エスカレーション（質問再利用／起票→blocks→release）／完了ゲート（extract → outcome_exists）→ レビュー run（planner）→ close／差し戻し |
| `_recover_human.mmd` | POST/GET /kickoffs（router）と recover_orphans（未回答待ちは触らない／子ありは failing／純粋な残骸は release） |

## 5. PoC からの継承と変更（証跡: renkei `loop.py`）

| 項目 | PoC | V4 |
|---|---|---|
| 外側ループ | `run_loop`（watch あり） | run（watch なし D04-c、single-flight P5D-036） |
| 回収 | `recover_orphans`（in_progress × assignee、`_open_question_of` ガード、`RECOVER_MARKER`） | list_working（state:working）＋open_question_of＋children_of。子ありは failing（P5D-034） |
| 起票順序 | `_flush_proposals`（create→dep→dependents 付け替え→close、UNSAFE） | 同。dependents 付け替えは board.move_dependents。UNSAFE は failing＋済み処理一覧 |
| 差し戻し回数 | `RETURN_MARKER` コメントで数える | 同方式（[orchestrator] 差し戻し）。上限は config |
| エスカレーション | `_open_escalation`（create→dep→release） | 同（F10）。質問の再利用 |
| 人間へ渡す | 滞留（穴①）／UNSAFE 記録 | state:failing＋理由＋release に統一、claim 後の failing 除外（P5D-034） |
| actor | 共通 `crew-exec-01` | system_actor（自分の行為）＋メンバ bd_actor（AI の行為）（P5D-037） |
| human 起動 | `poc run` CLI | POST /kickoffs（router）＋GET（P5D-035） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py orchestrator --render` → ERRORS: none
- participant は Protocol 名 7 群・内部 2（router／service）・外部 1（自分）のみ
- 提供 1 行・利用 **29 行**の if_id が feature_list に現れる（実測。旧記載の 32 行は誤り＝ DIFF-06）

## 7. 未決（orchestrator 固有）

1. **FN-14 取り下げ動線**（UC-06 据え置き）: 所有は orchestrator 暫定のまま（D08 §6-6）。P5D-034 の failing 統一で「撤退状態」は state:failing の人間操作で代替できる可能性あり → 解除条件の再評価は実タスク投入時
2. ~~**max_returns の既定値**~~ **決着（2026-08-26）**: 既定 **2**。判定は**語のとおりの勘定**（盤面にある差し戻しコメントの件数 `N >= max_returns`）で、実行器 3 回・差し戻し 2 件で人間へ渡る。PoC の `N + 1 >= 上限` 式は採らない
3. **review の担当**: config.review_persona（既定 planner。tasks.yaml の review_* に agent: は置かない D16-f）。メンバが無ければ MemberNotFound → F12
4. **ready 巡回 1 回で処理する issue 数の上限**: PoC は ready が空になるまで。上限を置くかは M2（トリガー間隔との関係）
5. ~~**human 起動の claim_filter 既定**~~ **決着（2026-08-26）**: `ClaimFilter(labels={role:executor}, project_key=None)`。`labels` は型の既定のまま、`project_key` は型に既定が無い必須項目なので入口が明示する（FR-078）
