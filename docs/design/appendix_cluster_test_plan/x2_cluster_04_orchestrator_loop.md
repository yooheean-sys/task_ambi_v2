# x2 クラスタ 04 — orchestrator_loop: 外側ループの周回・回収・起票（分解・遂行）

**位置付け**: 付2 統合テストのクラスタ文書。規則は `x2_notes.md`、裁定は D14。機械検査の正は `x2_cluster_index.csv` の行 04。
**並び順の根拠**: 外部依存（p6 csv の機械計上）5 件（EIF-01/02/05/10/11。EIF-10 は settings が居るための計上で実際には通らない）。同数の 03 とはテストダブル数 2（スタブ 2）＞ 1 で後（D14-f）。主検証 IF は 26 件で最多（P5-R1 で IF-50 追加 D16）。
**置き場（実装時）**: `apps/api/tests/integration/cluster_04_orchestrator_loop/`

## 1. 対象モジュールと選定理由

| MOD | クラスタ内での役割 | 実物／スタブ |
|---|---|---|
| orchestrator | `KickoffRunner.run` の実装（single-flight → 起動時回収 `recover_orphans` → claim → 前処理 5 読取 → 段解決 → kickoff → propose／execute → close・差し戻し・エスカレーション）と `router.py`（`POST /kickoffs`・`GET /kickoffs/{id}`・`GET /kickoffs/current`） | 実物 |
| stages | `StageCatalog.resolve_stage/build_prompt`・`ProposalGate.validate_proposal/extract_outcome_tokens`（純関数。I/O なし） | 実物 |
| board | 起票（`create` は orchestrator の 1 箇所のみ）・依存張り・claim・release・close・盤面導出（`children_of`／`open_question_of`／`list_working`／`comments`） | 実物（一時 dir に `bd init`） |
| settings | `SettingsReader.get_project/get_limits/get_rules/get_hooks/get_tasks`・`MemberResolver.resolve/is_human` | 実物（一時 領域 A） |
| vault | `VaultReader.check_project_folders`（実行時存在検証 D04-d）・`outcome_exists`（完了ゲート） | 実物（一時 `VAULT_ROOT`） |

**選定理由**: 周回の骨格は orchestrator と 4 provider（board・settings・vault・stages）の同期呼び出しで成立し、executor と monitor は「結果を返す」だけなので台本スタブで置き換えられる（LLM 偽サーバ不要）。起票順序不変条件（子起票→兄弟依存 B1→dependents 付け替え→release）と盤面導出の二重ガード（D01）は**実 bd でしか検証できない**。p2 `p2_sequence_UC-I1.mmd`（外側ループ／段解決／メンバ台帳／分解ゲート／実行器／beads／vault／LLM）のうち実行器＋LLM をスタブ化した形。p2 UC-12/13 の部分はクラスタ 01（D14-c）。

## 2. 検証する境界（IF-*）

### 2-1. 主検証（26 件）

| 提供→利用 | IF | Protocol.method | 本クラスタで見る点 |
|---|---|---|---|
| board→orchestrator | IF-01 | `BoardReader.show` | claim 後の読取（labels・parent・acceptance・status・claim 状態） |
| | IF-04 | `BoardReader.children_of` | 盤面から導出。**D01 二重ガード・確認読取の唯一の出所** |
| | IF-05 | `BoardReader.open_question_of` | 未回答の kind=question 子（UC-04・回収ガード D04-c） |
| | IF-06 | `BoardReader.list_working(ClaimFilter)` | state=working の残骸（回収） |
| | IF-07 | `BoardReader.comments` | 人間指示／回答／封筒の生データ（種別判定は orchestrator。P5D-005） |
| | IF-10 | `BoardWriter.claim_next(ClaimFilter, actor)` | `bd ready --claim` 相当。state=working 付与 |
| | IF-11 | `BoardWriter.create(IssueSpec, actor) -> issue_id` | **起票は orchestrator の 1 箇所のみ**（D01-a）。`BoardWriteUnverified` |
| | IF-12 | `BoardWriter.add_dependency` | 兄弟依存 B1・blocks（UC-I1／UC-04） |
| | IF-13 | `BoardWriter.move_dependents` | dependents 付け替え |
| | IF-14 | `BoardWriter.release` | claim 解放 |
| | IF-16 | `BoardWriter.comment` | 停止理由・UNSAFE 記録・`[orchestrator]` コメント |
| | IF-19 | `BoardWriter.close` | 親 close／リーフ承認 close |
| | IF-21 | `BoardWriter.set_labels` | note=single-child 観測印・state 更新 |
| settings→orchestrator | IF-23 | `SettingsReader.get_project` | `ProjectNotFound` |
| | IF-24 | `SettingsReader.get_limits` | LimitPolicy（RunMonitor スタブへ渡す） |
| | IF-25 | `SettingsReader.get_rules` | Rules（build_prompt へ） |
| | IF-26 | `SettingsReader.get_hooks` | HooksConfig（CrewSpec へ） |
| | IF-50 | `SettingsReader.get_tasks` | TaskTemplates（7 キー。build_prompt へ引数で渡す D16-h） |
| | IF-27 | `MemberResolver.resolve` | Member（`MemberNotFound`） |
| | IF-28 | `MemberResolver.is_human` | 台帳照合（台帳内＝AI）。UC-03／04 の回答者判定 |
| vault→orchestrator | IF-31 | `VaultReader.check_project_folders` | 実行時存在検証（D04-d） |
| | IF-36 | `VaultReader.outcome_exists` | 完了ゲート（outcome トークン→実ファイル） |
| stages→orchestrator | IF-44 | `StageCatalog.resolve_stage(kind ラベル, has_parent)` | `UnknownStage` |
| | IF-45 | `StageCatalog.build_prompt` | PromptBundle（member・templates を受ける D16-h。`UnknownPersona`） |
| | IF-46 | `ProposalGate.validate_proposal` | 合格／指摘／single-child 観測印（例外にしない） |
| | IF-47 | `ProposalGate.extract_outcome_tokens(acceptance)` | 完了ゲート鎖の入力 |

### 2-2. 通過

なし（IF-09／IF-32 の settings→board／vault はクラスタ 02。IF-38/39/48 はスタブ側なので数えない）。

## 3. テスト対象のユースケース

- **UC-I1 階層分解して遂行する（1 周回）**: propose 段（program／project／subrequest）の提案検証→差し戻し→起票順序不変条件、execute 段（child リーフ）の完了ゲート→レビュー→close／差し戻し
- **UC-04 エスカレーション質問に回答する**: 質問起票（既存の未回答があれば再利用 FN-08）→ `add_dependency` → release → 人間が回答（bd comment）→ 次の claim で再開
- **UC-11 無人でタスク遂行を起動する（orchestrator 側）**: 起動時回収 `recover_orphans`・claim・human 起動 `POST /kickoffs`。発火側（trigger）はクラスタ 05
- **UC-03 進行中タスクへ追加指示する（通過）**: 人間コメントを `comments`＋`is_human` で読み、次の kickoff の入力に載せる（FN-07/09/10）。人間側の操作は bd 直接

## 4. 必要なテストダブル

| 種別 | 対象 | 形 |
|---|---|---|
| ①外部模擬 | なし | — |
| ②HTTP 入口代替 | **EXT-11 ブラウザ／curl（EIF-11）** | TestClient で `POST /kickoffs`（202／409）・`GET /kickoffs/{kickoff_id}`（404）・`GET /kickoffs/current`。他の周回は `KickoffRunner.run` の直接呼び出し（trigger の代わりはドライバ） |
| ③Protocol スタブ（2） | `CrewExecutor`（台本 `CrewOutcome`: propose 段＝proposals（合格／指摘あり／single-child）、execute 段＝task_output＋完了申告、escalation 要求、review verdict approved／rejected。成果物ファイルは台本に合わせてテスト fixture が vault に置く）・`RunMonitor`（begin＝no-op、finish＝`RunVerdict(grade=I, closed=False, halted=False)`） | override で注入（D14-e） |

**実物で用意する外部**: EIF-01 beads（一時 dir に `bd init`。kind:program の親・kind:child のリーフ・state=working の残骸（子あり／子なし／未回答 question 付き）を仕込む）／EIF-02 vault（PJ フォルダ・役割フォルダ・`outcome/` の成果物ファイル）／EIF-05 領域 A（project／agents.yaml（メンバ・ペルソナ既定）／tasks.yaml（段テンプレート）／rules／hooks／limits／llm_profiles／humans D16。02 の API で作るか直接置く）／EIF-08 .env／EIF-09 config.json（`stages/config.json` 段表・`orchestrator/config.json` max_returns 等）／EIF-13 構造化ログ。EIF-10 は機械計上のみ。

## 5. 前提条件と実行順序上の依存クラスタ

- **配線**: 実 composition root の build 連鎖。有効 MOD＝board・vault・settings・stages・orchestrator（monitor・executor・trigger は有効 MOD 一覧から外す）。orchestrator が要する `CrewExecutor`・`RunMonitor` を override でスタブ注入（D14-e）
- **前提**: 付1 完了（設計を直す裁定 0）・憲章への PATCH-7〜11＋C-5 はユーザ適用待ち（D14-a/h）／bd バイナリ／登録済み PJ（UC-08）が前提状態／`BoardWriteUnverified` の故障注入点は未定（D14 §6-3。§6-9 は条件付き）
- **依存クラスタ**: **02**（PJ 登録・メンバ・policy の書式）。実行順は 02 → 03 → 01 → **04** → 05。01・03 とは独立（LLM 不要）
- **M1 実測へ持ち越し**（各 p5 notes §7）: bd actor の `.` 可否／`bd ready --claim` の複数 `--label`／`move_dependents` の bd 側コマンド

## 6. 検証観点（p5 の alt 分岐から）

| # | 観点 | 期待 | 出典 |
|---|---|---|---|
| 1 | single-flight: 進行中に `POST /kickoffs` | 409 `{status: rejected, reason: busy}`（`KickoffBusy`）。`GET /kickoffs/current` は running。終了後 `GET /kickoffs/{id}` で結果（直近 N 件インメモリ P5D-035・不明は 404） | recover_human 副図 |
| 2 | `recover_orphans`（D01 二重ガード）: state=working 残骸 | 未回答 question あり → 触らない（crash window D04-c）／子が既にいる → failing＋人間へ／純粋な残骸 → release して ready へ | recover_human 副図 `alt` |
| 3 | claim: ready なし | `claim_next` None → 周回終了 `KickoffResult(no_work)` | 主図 `alt None` |
| 4 | claim: labels に `state:failing` | 即 release（人間が直すまで触らない P5D-034） | 主図 |
| 5 | 前処理: `ProjectNotFound`／`MemberNotFound`／`UnknownStage`／フォルダ不在（IF-31） | 人間へ渡す統一経路＝`[orchestrator]` 理由コメント → `state:failing` → release（P5D-034） | 主図 `alt 不在／…` |
| 6 | propose 段: 提案に指摘（`validate_proposal`） | 差し戻し loop。回数は `[orchestrator]` コメントを `comments` で数える（再起動で消えない）。`max_returns` 到達 → 人間へ | propose 副図 `loop 差し戻し` |
| 7 | propose 段: 合格 | 起票順序不変条件: `create`×n → `add_dependency`（B1 blocks）→ `move_dependents` → 親 close／release。`children_of` で確認読取。single-child 観測 → `set_labels(note:single-child)` | propose 副図 |
| 8 | propose 段: 既に子がいる（クラッシュ窓の再入） | 人間へ（自動再投入しない D01-b） | propose 副図 `alt 既に子がいる` |
| 9 | 途中失敗（`BoardUnavailable`＝`bd_bin` を N 回目で失敗するラッパに差し替え。`BoardWriteUnverified` は注入点が出来たら） | UNSAFE: 済み処理の一覧を添えて人間へ。**自動再試行しない**（FN-49・D01-b） | propose 副図 `alt 途中失敗` |
| 10 | execute 段: 完了ゲート（`extract_outcome_tokens` → `outcome_exists`） | 欠け／不正トークン → 差し戻し。揃っていればレビュー run（スタブ）へ | execute 副図 `alt 欠け` |
| 11 | execute 段: レビュー approved／却下 | approved → close／却下 → 差し戻し（`max_returns` 超過は人間へ） | execute 副図 |
| 12 | UC-04: スタブが escalation 要求 | 未回答の質問が既にある → 再利用（起票しない FN-08）／無ければ question 起票 → `add_dependency` → release | execute 副図 `alt outcome.escalation` |
| 13 | UC-04: 人間が bd comment で回答（台帳外 actor＝`is_human` True） | 次の claim で `open_question_of` None → 回答を `comments` から読み入力に注入して続きから kickoff | p2 UC-04 |
| 14 | UC-03 通過: 人間コメント | `comments`＋`is_human` で拾い ContextRefs／feedback に載る（FN-07/09/10） | execute 副図 Note |
| 15 | 起票の actor | `create` の actor が system_actor（AI メンバの bd_actor とは分離 P5D-037） | P5D-037 |
| 16 | 構造化ログ | HTTP 入口（middleware）と直接呼び出し（発番）の両方で correlation_id が board・settings・vault の呼び出しログに貫通（憲章 §6） | EIF-13 |
| 17 | `MappingUnverified` → stopped | 本クラスタでは RunMonitor がスタブなので**見ない**（クラスタ 01） | — |
| 18 | 完了ゲート: リポジトリの成果物（D25） | 受入条文の `repo/<パス>` を実 stages が抽出し（IF-56）、実 repo が作業ツリーでの実在を確かめる（IF-53）。在ればレビューへ、無ければ差し戻し | D25-b／IF-53・56 |
