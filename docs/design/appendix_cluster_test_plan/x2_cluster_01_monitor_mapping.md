# x2 クラスタ 01 — monitor_mapping: 上限停止と等級写像（監視・制御）

**位置付け**: 付2 統合テストのクラスタ文書。規則は `x2_notes.md`、裁定は D14。機械検査の正は `x2_cluster_index.csv` の行 01。
**並び順の根拠**: 外部依存（p6 csv の機械計上）3 件 ＝ 5 クラスタ中最小（D14-f）。**番号は文書の並びであって実行順ではない**（実行順は §5）。
**置き場（実装時）**: `apps/api/tests/integration/cluster_01_monitor_mapping/`

## 1. 対象モジュールと選定理由

| MOD | クラスタ内での役割 | 実物／スタブ |
|---|---|---|
| orchestrator | `KickoffRunner.run` の実装。1 周回の中で `RunMonitor.begin`（IF-38）→ `CrewExecutor.run`（IF-48）→ `RunMonitor.finish`（IF-39）を呼び、`MappingUnverified` で停止痕跡を残して周回を打ち切る | 実物 |
| executor | `CrewExecutor.run`。LLM 構築時に `LLMHttpHook` を httpx transport 層に装着（P5D-030）。`RunHalted` を crewai のリトライに食わせず素通りさせ `CrewOutcome.halted=True` に翻訳（Z-6）。crewai 例外を `ExecutorFailure` に翻訳 | 実物（CrewAI 実物。LLM は偽サーバ） |
| monitor | `RunMonitor.begin/finish`・`LLMHttpHook`。計測（DM-20）・上限判定・失敗検出・等級 F/W/I（P5D-020）・封筒・bd 写像・確認読取 | 実物 |
| board | 写像先（`set_labels`/`release`/`close`/`comment`）と確認読取（`show`/`comments`） | 実物（一時 dir に `bd init`） |

**選定理由**: UC-12→UC-13 の連鎖「Hook が `RunHalted` → executor が halted に翻訳 → orchestrator が finish → monitor が等級を付け bd に写像 → 確認読取」は 4 MOD をまたいで初めて成立する。p2 `p2_sequence_UC-12-13.mmd` のレーン（外側ループ／実行器／Interceptor／失敗検出器／LLM／beads）がそのままこの 4 MOD＋LLM 偽に写る。settings・stages・vault は値を供給するだけ（LimitPolicy・Member・StageSpec・フォルダ存在）なのでスタブに置き換えられる（D14-c）。

## 2. 検証する境界（IF-*）

### 2-1. 主検証（本クラスタが実装同士で噛み合わせる。9 件）

| IF | 提供→利用 | Protocol.method | 本クラスタで見る点 |
|---|---|---|---|
| IF-38 | monitor→orchestrator | `RunMonitor.begin(kickoff_id, LimitPolicy, threshold_T)` | 計測開始。以後の Hook 呼び出しがこの kickoff に帰属（RunState。P5D-021） |
| IF-39 | monitor→orchestrator | `RunMonitor.finish(kickoff_id, issue_id, actor, CrewOutcome) -> RunVerdict` | 等級 F/W/I・封筒・**F/W は本メソッド内で board へ写像し確認読取まで**。`MappingUnverified` |
| IF-48 | executor→orchestrator | `CrewExecutor.run(CrewSpec) -> CrewOutcome` | `halted`・`events`（中立型 CrewEvent）・`ExecutorFailure` |
| IF-22 | board→monitor | `BoardWriter.set_labels(add state:failing)` | F 級写像 |
| IF-15 | board→monitor | `BoardWriter.release` | F 級写像（status と assignee の両方を戻す。片方だけだと ready に出ず無症状停滞） |
| IF-18 | board→monitor | `BoardWriter.comment(封筒: 等級・種類・実測値)` | 封筒を**先に**書く（P5D-024） |
| IF-20 | board→monitor | `BoardWriter.close` | W 級写像（close＋封筒） |
| IF-03 | board→monitor | `BoardReader.show` | 写像後の確認読取（rc は成功の証拠にしない D04-b） |
| IF-08 | board→monitor | `BoardReader.comments` | 封筒の反映確認 |

### 2-2. 通過（実装同士が通るが主検証は別クラスタ）

IF-40〜43（`LLMHttpHook.on_outbound/on_inbound/aon_outbound/aon_inbound`。主検証はクラスタ 03）。本クラスタでは Hook が装着済みであることを前提に、`RunHalted` が finish まで届くことを見る。

## 3. テスト対象のユースケース

- **UC-12 暴走を止める**: 上限（回数・コスト・送信前トークン T）超過 → `RunHalted`（送信しない）→ kickoff 打切り
- **UC-13 失敗を beads で検出する**: 等級 F/W/I → F＝`state:failing`＋release＋封筒／W＝close＋封筒／I＝履歴のみ → 確認読取 → 人間が beads-ui／bd で検出（テストでは bd で読む）

UC-12 → UC-13 の境界は p2 図の `Icpt->>Det: 失敗イベント F` の 1 本（本クラスタでは executor の `CrewOutcome.halted/events` → monitor finish）。

## 4. 必要なテストダブル

| 種別 | 対象 | 形 |
|---|---|---|
| ①外部模擬 | **EXT-03 LLM API**（EIF-03） | ローカル偽 OpenAI 互換サーバ（ループバック HTTP。MockTransport は不可＝Hook が transport 層）。台本: (A) 上限超過＝`max_requests` を小さくしておき N 回目の送信前に Hook が `RunHalted`／(B) `finish_reason=length`／(C) 正常完了／(D) 5xx・429 連発（api_exhausted）。`usage` を返す |
| ②HTTP 入口代替 | なし | 入口は `KickoffRunner.run` の直接呼び出し（trigger の代わりはテストのドライバ。憲章 §7-6）。`POST /kickoffs` はクラスタ 04 |
| ③Protocol スタブ（8） | settings の 4: `SettingsReader`（get_project／get_limits＝小さい `LimitPolicy`／get_rules／get_hooks／get_tasks＝固定 `TaskTemplates` D16）・`MemberResolver`（resolve＝`LLMProfile.base_url` を偽サーバに向けた Member、`context_window` から T を導出／is_human）・`Authorizer`（enforce＝True。台本は Tool を呼ばない）・`CommandRegistry`（resolve_command＝`UnknownCommand`）／stages の 2: `StageCatalog`（resolve_stage＝execute 段の `StageSpec`・build_prompt（member・templates を受ける D16-h）＝固定 `PromptBundle`）・`ProposalGate`（extract_outcome_tokens＝空＝完了ゲート無し）／vault の 2: `VaultReader`（check_project_folders＝OK・outcome_exists＝True・list_inputs＝空）・`OutcomeWriter`（write_outcome＝呼ばれない前提で例外） | override で注入（D14-e）。executor の `<Mod>Deps` が settings 2・vault 2 を要するため、Tools を呼ばない台本でも型は埋める |

**実物で用意する外部**: EIF-01 beads（一時 dir に `bd init`。kind:child のリーフ 1 件を ready で仕込む。`BEADS_ACTOR`）／EIF-08 .env（`api_key_ref` のダミー値）／EIF-09 config.json（`monitor/config.json` の等級割当 P5D-020・`board/config.json` の `bd_bin`）／EIF-13 構造化ログ（capture）。EIF-11 は機械計上のみ（`POST /kickoffs` は通さない）。

## 5. 前提条件と実行順序上の依存クラスタ

- **配線**: 実 composition root の build 連鎖。有効 MOD＝board・monitor・executor・orchestrator。settings・stages・vault・trigger は有効 MOD 一覧から外し、orchestrator・executor が要する 8 Protocol（§4 ③）を override で注入（D14-e）
- **前提**: 付1 完了（設計を直す裁定 0）・憲章への PATCH-7〜11＋C-5 はユーザ適用待ち（D14-a/h）／bd バイナリが PATH にある（CI も）／LLM 偽サーバが実装済み（D14 §6-2）／Hook 装着と `RunHalted` 伝播はクラスタ 03 で先に確認済み
- **依存クラスタ**: **03**（Hook 経路・LLM 偽サーバ）。実行順は 02 → 03 → **01** → 04 → 05 の 3 番目（番号 01 は外部依存数の並び。D14-f）
- **既知の未実測**（M1。D14 §6-5）: openrouter 経路の Hook 装着（P5D-013/030。偽サーバでは openai_compatible のみ）／送信前トークン推定の精度（観点は回数・コストを必須、T は実測後）／contextvar の貫通

## 6. 検証観点（p5 の alt 分岐から）

| # | 観点 | 期待 | 出典 |
|---|---|---|---|
| 1 | 台本 (A): 送信前に `tokens_before_send > T`／`requests > max_requests`／`cost > max_cost` | Hook が `RunHalted`（送信しない）→ litellm／crewai のリトライを素通り（BaseException 直系 Z-6）→ `CrewOutcome.halted=True` → finish → **F**。board: 封筒 comment → `state:failing` → release の順 → 確認読取一致 → `RunVerdict(grade=F, halted=True)` | monitor 主図／executor 主図／P5D-024 |
| 2 | 台本 (B): `finish_reason=length` | **W** → close＋封筒 → 確認読取一致 → `RunVerdict(grade=W, closed=True)` | P5D-020 |
| 3 | 台本 (C): 正常 | **I** → board 不変（封筒は書かない）・履歴のみ（履歴はクラスタ 05 の trigger）。`RunVerdict(grade=I)` | UC-12-13 p2 図 `alt I` |
| 4 | 台本 (D): 5xx／429 連発 | crewai 内リトライ後 `ExecutorFailure` → **F（api_exhausted）**。自前の再試行・迂回が無いこと（憲章 §10） | EIF-03 失敗時 |
| 5 | 写像後の確認読取が不一致／`BoardUnavailable`（finish 時点で `bd_bin` を失敗するラッパに差し替え） | `MappingUnverified` → orchestrator が停止痕跡を残し `KickoffResult.status=stopped`・周回打ち切り（P3「黙って進まない」） | IF-39 例外／orchestrator 主図 `alt MappingUnverified` |
| 6 | 封筒先行 | F/W とも comment（封筒）が状態変更（set_labels／close）より先に bd に入っている（comments の順序で assert） | P5D-024 |
| 7 | release の完全性 | release 後に `bd ready` 相当の読取に出る（status と assignee の両方が戻る） | board reads 副図 Note |
| 8 | `begin` を進行中 RunState がある状態で呼ぶ（single-flight 違反の検出） | 旧 RunState を「帰属不明で打切り」とログし差し替え、**止めない**（single-flight は orchestrator の不変条件、monitor は検出のみ） | P5D-024 |
| 9 | 構造化ログ | Hook の計測ログ・bd 呼び出しの監査ログ（P5D-006）に同じ correlation_id（憲章 §6）。直接呼び出し入口でも発番される | EIF-13 |
| 10 | Hook 非同期経路（IF-42/43） | Hook が `aon_*` を実装していれば（`CX-01`＝`hasattr`）`aon_outbound` で同じ上限判定が効く（実 CrewAI kickoff が `aon_*` を通るかは未実測のまま許容＝ design-sync B-22）（装着の両経路） | 契約 csv IF-42 |
| 11 | openrouter 経路（`provider_kind=openrouter`） | 偽サーバでは模擬しない。M1 実測（P5D-013/030）に送る | D14 §6-5 |
