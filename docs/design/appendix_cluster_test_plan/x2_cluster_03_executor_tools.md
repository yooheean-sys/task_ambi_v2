# x2 クラスタ 03 — executor_tools: 実行器の Tools 境界と LLM Hook（分解・遂行／実行監視）

**位置付け**: 付2 統合テストのクラスタ文書。規則は `x2_notes.md`、裁定は D14。機械検査の正は `x2_cluster_index.csv` の行 03。
**並び順の根拠**: 外部依存（p6 csv の機械計上）5 件（EIF-01/02/03/05/10。EIF-10 は settings が居るための計上で実際には通らない）。同数の 04 とはテストダブル数 1（LLM 偽のみ）＜ 2 で先（D14-f）。
**置き場（実装時）**: `apps/api/tests/integration/cluster_03_executor_tools/`

## 1. 対象モジュールと選定理由

| MOD | クラスタ内での役割 | 実物／スタブ |
|---|---|---|
| executor | `CrewExecutor.run(CrewSpec)`。`LlmFactory` で LLM を構築し `LLMHttpHook` を装着（executor 内の唯一の crewai 依存点 P5D-030）、Agent（Member の CrewAI フィールドから D16-h）・Tools（`GuardedTool` 基底＋12 Tool。装備は member.tools／bundle.task_tools D16-a）・hooks を組み kickoff、提案・成果・events（中立型）を `CrewOutcome` に翻訳 | 実物（CrewAI 実物。LLM は偽サーバ） |
| settings | `Authorizer.enforce`（全 Tool の `_run` 前段 P5D-032）・`CommandRegistry.resolve_command`（run_command の argv） | 実物（一時 領域 A の policy.csv／model.conf／commands.yaml） |
| vault | `VaultReader.list_inputs/read_input/read_outcome_file`・`OutcomeWriter.write_outcome`（Tools 経由） | 実物（一時 `VAULT_ROOT`） |
| board | `BoardReader.show`（read_issue）・`BoardWriter.comment`（record_progress） | 実物（一時 dir に `bd init`） |
| monitor | `LLMHttpHook.on_outbound/on_inbound/aon_outbound/aon_inbound`（transport 層で計測・上限停止） | 実物 |

**選定理由**: Tool 呼び出しは「crewai → GuardedTool → `Authorizer.enforce` → 各 Protocol」の鎖で、実 casbin policy・実 vault・実 bd があって初めて拒否・外縁違反・書込・退避が観測できる。Hook は LLM 構築時に executor が装着するため、monitor 単体でも executor 単体でも「装着されて効く」ことは検証できない。p5 `p5_executor_sequence.mmd` のレーン（CrewExecutor／service／tools/／lib:crewai／.env／LLMHttpHook／LLM API／Authorizer・CommandRegistry／VaultReader・OutcomeWriter／BoardReader・BoardWriter）がそのまま本クラスタ。呼び手（orchestrator）は `CrewSpec` を渡すだけなのでテストがドライバになる（憲章 §7-6）。

## 2. 検証する境界（IF-*）

### 2-1. 主検証（12 件）

| IF | 提供→利用 | Protocol.method | 本クラスタで見る点 |
|---|---|---|---|
| IF-29 | settings→executor | `Authorizer.enforce(subject, obj, act) -> bool` | obj は `tool:<name>`／`cmd:<name>`／`vault:<area>`（P5D-029）。拒否はフェイルクローズ。PJ が引けない subject は False |
| IF-30 | settings→executor | `CommandRegistry.resolve_command(name) -> argv` | `shell=False`・引数は AI に渡させない（D07-e）。`UnknownCommand` |
| IF-02 | board→executor | `BoardReader.show` | read_issue Tool |
| IF-17 | board→executor | `BoardWriter.comment` | record_progress Tool（actor＝メンバの bd_actor） |
| IF-33 | vault→executor | `VaultReader.list_inputs(ProjectFolders, role)` | PJ 入力・役割フォルダ・兄弟成果物 B2 |
| IF-34 | vault→executor | `VaultReader.read_input` | `VaultNotFound`／`VaultPathViolation` |
| IF-35 | vault→executor | `VaultReader.read_outcome_file` | 不在は例外で明示（黙って空を返さない FN-13） |
| IF-37 | vault→executor | `OutcomeWriter.write_outcome -> WrittenPath` | 外縁ゲート・禁止文字ゲート・退避（P5D-008/010/011） |
| IF-40 | monitor→executor | `LLMHttpHook.on_outbound(httpx.Request)` | 送信前の回数・コスト・トークン判定。超過は `RunHalted` |
| IF-41 | monitor→executor | `LLMHttpHook.on_inbound(httpx.Response)` | usage・finish_reason の観測 |
| IF-42 | monitor→executor | `LLMHttpHook.aon_outbound` | 非同期経路 |
| IF-43 | monitor→executor | `LLMHttpHook.aon_inbound` | 非同期経路 |

### 2-2. 通過

なし。IF-48（`CrewExecutor.run`）は本クラスタの**入口**だが呼び手はテスト（ドライバ）なので主検証はクラスタ 01。

## 3. テスト対象のユースケース

- **UC-I1 階層分解して遂行する（Tools 部分）**: 1 タスクの実行器が read_issue／record_progress／list_input_files／read_input_file／read_outcome_file／write_outcome／run_command／propose を呼び、提案・成果・events を返す
- **UC-12 暴走を止める（Hook 部分）**: 上限超過 → `RunHalted` → crewai のリトライを素通り → `CrewOutcome.halted=True`

## 4. 必要なテストダブル

| 種別 | 対象 | 形 |
|---|---|---|
| ①外部模擬 | **EXT-03 LLM API**（EIF-03） | ローカル偽 OpenAI 互換サーバ（ループバック HTTP・`usage` 付き）。台本: (T1) 各 Tool を 1 回ずつ呼ぶ `tool_calls` の往復 → 最終応答／(T2) 拒否される Tool（policy で deny）／(T3) 外縁外・禁止文字・不在パスの Tool 引数／(T4) 上限超過（`max_requests` 小）／(T5) 5xx 連発 |
| ②HTTP 入口代替 | なし | 入口は `CrewExecutor.run(CrewSpec)` の直接呼び出し（orchestrator の代わりはテストのドライバ。`CrewSpec`＝固定 `PromptBundle`（task_tools 込み）・`MemberResolver.resolve` で引いた実 Member（CrewAI フィールド込み D16-h）・`HooksConfig`・mode・issue_id・`ProjectFolders`） |
| ③Protocol スタブ | なし | — |

**実物で用意する外部**: EIF-05 領域 A（`projects/<key>/policy.csv`（allow／deny 両方）・`global/model.conf`・`global/commands.yaml`（DM-26。vault 外）・`global/llm_profiles.yaml`（`base_url` を偽サーバに、`provider_kind=openai_compatible`）・agents.yaml（global＋PJ D16）／humans）／EIF-02 vault（PJ 入力・役割フォルダ・`outcome/` に既存ファイル 1 件＝上書き退避用）／EIF-01 beads（issue 1 件）／EIF-08 .env（`api_key_ref` の実値。無い場合の観点 §6-9 は別 fixture）／EIF-09 config.json（`vault/config.json` 禁止文字集合 P5D-010・`monitor/config.json`）／EIF-13 構造化ログ。EIF-10 は機械計上のみ。

## 5. 前提条件と実行順序上の依存クラスタ

- **配線**: 実 composition root の build 連鎖。有効 MOD＝board・vault・settings・monitor・executor（stages・orchestrator・trigger は有効 MOD 一覧から外す。未充足 Protocol は無いので override 不要）。ドライバは handle の `CrewExecutor` を直接呼ぶ（D14-e）
- **前提**: 付1 完了（設計を直す裁定 0）・憲章への PATCH-7〜11＋C-5 はユーザ適用待ち（D14-a/h）／bd バイナリ／LLM 偽サーバ実装済み（D14 §6-2。本クラスタが最初の利用者）／CrewAI の tool calling が偽サーバの `tool_calls` 形式で回ること（M1 で実測）／agents.yaml の `tools:` 名→ToolRegistry（GuardedTool）解決（D16-b。M1 で実測）
- **依存クラスタ**: **02**（領域 A のファイル書式＝UC-07/08 で検証済み。本クラスタはファイルを直接置くか 02 の API で作る）。実行順は 02 → **03** → 01 → 04 → 05
- **既知の未実測**（M1。D14 §6-5）: openrouter 経路の Hook 装着（P5D-013/030）／送信前トークン推定の精度／crewai の async 経路の有無（IF-42/43 の通り方）

## 6. 検証観点（p5 の alt 分岐から）

| # | 観点 | 期待 | 出典 |
|---|---|---|---|
| 1 | 台本 (T2): policy で deny の Tool | `enforce` False → **例外にせず戻り値で指摘**・events に `tool_denied`（フェイルクローズ P5D-032）。PJ が引けない subject も False・構造化ログに印 | executor 主図 `alt 拒否`／settings 主図 |
| 2 | run_command: 未定義コマンド | `UnknownCommand` → Tool が指摘で返す | IF-30 |
| 3 | run_command: 定義あり | `argv` のまま `subprocess.run(shell=False)`。AI の引数は渡らない（D07-e）。短命 subprocess | executor 主図 |
| 4 | list_input_files | PJ 入力・役割フォルダ・兄弟成果物（B2）が返る。role が `role_dirs` に無い → `VaultNotFound(role)` | vault 主図 |
| 5 | read_input_file: 不在／外縁外／非 UTF-8／サイズ超過 | `VaultNotFound`／`VaultPathViolation(kind)`（P5D-011）。Tool は指摘で返す | vault 主図 `alt` |
| 6 | read_outcome_file: 不在 | `VaultNotFound`（黙って空を返さない FN-13） | vault 主図 |
| 7 | write_outcome: 禁止文字 | `VaultPathViolation`（違反文字を明示。黙って置換しない） | vault 主図 `alt ゲート不合格` |
| 8 | write_outcome: 既存ファイルあり | `outcome/_archive/<filename>.<UTC stamp>` へ `os.replace` 退避（P5D-008）→ 書込 → events に `outcome_overwritten`。`WrittenPath` は実パス | vault 主図／executor 主図 |
| 9 | build: `api_key_ref` が .env に無い | `ExecutorFailure(build)`（黙って not_set にしない executor-F02） | EIF-03／EIF-08 |
| 10 | 台本 (T4): 上限超過 | `on_outbound` で `RunHalted`（送信しない）→ litellm／crewai のリトライを素通り（Z-6）→ `CrewOutcome.halted=True`・events | executor 主図 `alt RunHalted` |
| 11 | 台本 (T1): 正常 | `on_outbound` の回数・コスト計測、`on_inbound` の usage・finish_reason 観測が RunState に帰属（begin はテストが呼ぶ） | monitor 主図 |
| 12 | 台本 (T5): 5xx 連発 | crewai 内リトライ後 `ExecutorFailure(phase, reason)`。crewai の型・イベント名が `CrewOutcome`／`CrewEvent` に出ない（P5D-033） | executor 主図 `alt crewai 例外` |
| 13 | record_progress／read_issue | bd に comment が actor＝メンバの bd_actor で入る／`IssueNotFound` | board 主図 |
| 14 | 非同期経路（IF-42/43） | crewai が async を使う構成でも同じ判定が効く（装着を両経路で確認） | 契約 csv |
| 15 | 構造化ログ | 直接呼び出し入口で発番した correlation_id が Hook ログ・Tool ログ・bd 監査ログに貫通（憲章 §6） | EIF-13 |
| 16 | repo 系道具（D25）: list／read／write／delete_repo_file | 実物の作業ツリー（一時）を読み書きできる。作業ツリーの外・`.git`・拒否パターン（`.env` 等）は `RepoPathViolation`、不在は `RepoNotFound` → **例外にせず戻り値で指摘**。二段許可は `tool:<name>` → `repo:files` の read／write | D25-a／IF-51・52・54・55 |
