# x3_notes — 付3フェーズ（設定の 3 層仕分け）説明

**位置付け**: `x3_config_catalog.csv`（★台帳・機械検査の正）と `.env.example`（★）の短文の正本。裁定は **D15**（`設計判断_V4-D15_付3前提と設定3層の裁定.md`）。食い違えば D15 → 台帳 csv → 本書の順に勝つ。
**入力**: p5 feature_list／data_model の `*Config`・`*Env`（8 MOD）／`../phase6/p6_external_interface.csv`（EIF-08 .env・EIF-09 config.json）／憲章 §2 例外 (a)・§5・§6・§7-4／handoff_p6 §2-3・handoff_x1 §3-2（REV-07／REV-13／X1-077）・handoff_x2 §3-1（テスト差し替え）／工程文書 付3 節（決定例 5 点）。環境情報ファイルは INPUT に挙がっているが**現物が無い**（§6）。
**レジーム**: 改訂可。

## 1. 層の定義（D15-a）

| 層 | 置き場 | 誰が書く | git | 読み手 |
|---|---|---|---|---|
| **(a) 秘密** | リポジトリ直下 `.env`（1 ファイル） | 人間（手で） | gitignore（`.env.example` は commit） | executor（LLM api_key の実値。P5D-030） |
| **(b) 環境依存値** | 同じ `.env`（セクションコメントで (a) と区分） | 人間 | gitignore | 各 MOD の `*Env` Settings（vault・settings・trigger・board）／apps/api（APP_*）／ui（APP_BASE_URL・UI_PORT） |
| **(c1) 挙動の設定（構造的）** | ルート `config.json`＋`packages/features/<mod>/config.json` | 開発者（編集→commit→レビュー） | commit | 各 MOD の `*Config` Settings（build 時に読む。実行中は再読込しない EIF-09） |
| **(c2) 挙動の設定（宣言的）** | 領域 A（`<SETTINGS_ROOT>`）・領域 B（`<TRIGGER_ROOT>`） | **利用者が UI から CRUD**（UC-07／08／09） | **commit しない**（憲章 §2 例外 (a)・DEC-003） | 所有 MOD（settings／trigger）だけが読み書き、他 MOD は Protocol 経由 |

- 工程文書の (c)「YAML/TOML・git 管理下・レビュー対象」は c1 に当たる。決定例の「AI エージェント一覧表」「PJ フォルダの vault 相対パス」は c2。形式は憲章どおり（c1＝JSON、c2＝YAML／casbin 公約形式／markdown／csv。TOML 化しない）。
- **運用規約（REV-07 の付3 送り）**: 領域 A／B は **UI からのみ編集**する。人間がファイルを直接編集しても次回起動または次回保存まで反映されない（不変スナップショット P5D-016・起動時再登録 D07-a）。直接編集が要るのは復旧時のみで、その後は app を再起動する。
- 環境情報ファイル（工程文書の「(b) → .env または環境情報ファイル」）は **app の入力にしない**。(b) は全て `.env`（D15-c）。環境情報ファイルは人間向けの環境事実・手順・実値の控え（§6）。

## 2. 解決順序（D15-b）

低 → 高（後の段が前の段に勝つ）:

| 段 | 層 | 中身 |
|---|---|---|
| ① | コード既定値 | pydantic Settings のフィールド既定 |
| ② | ルート config.json の共通既定 | `root.defaults.*`（憲章 §5「複数モジュール共通のデフォルト値」）と `root.*` |
| ③ | MOD config.json | `<mod>.*` |
| ④ | 領域 A `global/` | limits.yaml の global／llm_profiles.yaml の default／agents.yaml・tasks.yaml のペルソナ・段既定 |
| ⑤ | 領域 A `projects/<key>/`（＋メンバ単位） | limits overrides／agents.yaml の PJ メンバ（llm・skills・mcps…）／tasks.yaml の PJ 上書き |
| ⑥ | `.env` | (a)(b) |
| ⑦ | OS 環境変数 | pydantic-settings 既定で `.env` より優先（憲章 §5「OS 環境変数に依存させない」は「書く場所にしない」の意。機構としては勝つ） |
| ⑧ | `build()` の init 引数 | 統合テストだけ（D14-e の override を config にも広げる。M1 要求） |

**規則**: **各キーの正規の置き場は 1 つ（重複なし）**。`.env`／環境変数で c1 の値は上書きできない（`*Config` の Settings クラスは env ソースを持たない）。順序が意味を持つのは多層を持つキーだけ（台帳の「上書き可能な階層」列。limits ③→④→⑤、llm は ④ の default → ⑤ のメンバ指定、agents/tasks は ④→⑤、ポート類は ①→⑥→⑦）。「同一の役割でも PJ ごとに異なる値」は ⑤ に書き、「意図的に共通化」は ④ だけに書けば済む。c1 を一時的に変えたいときは config.json を編集して再起動（commit 対象）、テストは ⑧。

## 3. 台帳の読み方

- 列は工程文書の指定 10 列。`配置先` は `a`／`b`／`c1`／`c2`（工程の (c) を 2 分割 D15-a）。`cfg_id` は `CFG-nn` 連番（追記のみ・欠番不埋め）。
- `キー名`: (a)(b) は .env のキー名そのもの／c1 は `<scope>.<key>`（scope＝`root`｜MOD 名。`root.log.level` のように入れ子は `.` 区切り）／c2 は **ファイル粒度**（`<SETTINGS_ROOT>/…`・`<TRIGGER_ROOT>/…`。フィールドは `型` 列の data_model クラス名で p5 を引く）。`#repos` は予約行（D15-h）。
- `上書き可能な階層`: §2 の段番号。`⑧` は統合テストの build() 引数でだけ差し替える（§5）。
- c1 のキー集合は p5 data_model の `*Config` から機械抽出して一致検査する（`x3_selfcheck.py`）。board の bd_bin／beads_dir は D15-d で `.env` へ移した後の姿。新設 3 件（`root.log.redact_keys` D15-i・`executor.disable_crewai_telemetry`・`trigger.timezone` D15-m）は **P5-R1（D16）で p5 data_model へ転記済み・NEW_C1 例外表は空**。
- c2 の agents.yaml／tasks.yaml は **P5-R1（D16。2026-08-20 完了）で新設済み**（§7）。p5 settings は agents.yaml／tasks.yaml に改訂済み、`roles/<persona>.md` 行（CFG-83）は廃止済み印。
- `settings.system_actor` と `orchestrator.system_actor` は同値であること（P5D-037）。起動時の一致検査を M1 要求に含める。
- 載せないもの: `BEADS_ACTOR`（board が bd 呼び出しごとに設定する env。設定ではない）、crewai が読む `CREWAI_DISABLE_TELEMETRY`／`OTEL_SDK_DISABLED`（executor が `disable_crewai_telemetry` から setdefault する。`.env` に書かない）、Streamlit 自身の `.streamlit/config.toml`（置かない。ポートは UI_PORT）。

## 4. 秘密がコード・設定・ログに出ないことの担保（D15-i）

| # | 担保 | 置き場（M1 で実装） |
|---|---|---|
| (1) | api_key の実値を読む点は **executor.LlmFactory の 1 箇所**（P5D-030）。型は `pydantic.SecretStr`。`LLMProfile` 受け渡し型は `api_key_ref`（名前）だけを持ち、実値は Protocol を越境しない | executor/service（LlmFactory）・common/interfaces/settings.py |
| (2) | `common/kernel/log.py` の `LoggerFactory` に**マスクフィルタ**: `root.log.redact_keys`（`*_API_KEY*`・`Authorization`・`api_key`）に合うキーの値を extra と message の両方で伏字。`crewai`／`litellm`／`httpx` のロガは WARNING 以上に固定（DEBUG のヘッダ出力を止める） | common/kernel/log.py・root config.json |
| (3) | monitor の `LLMHttpHook` は usage／finish_reason／tool_calls／トークン数だけを観測し、**リクエストヘッダを記録しない** | monitor/service |
| (4) | 例外・UI は参照名のみ（`ExecutorFailure(build)` のメッセージは `api_key_ref` 名、settings の UI はキー名を表示） | executor／settings/ui.py |
| (5) | `global/llm_profiles.yaml` に api_key の実値フィールドを持たず、settings の validate が `api_key`／`token`／`secret` 名のキーを**拒否**。c1 config.json も同じ規約（selfcheck） | settings/service/validate.py・`x3_selfcheck.py`（設計段階は台帳・雛形を検査） |
| (6) | `.env` は gitignore・権限 600。`.env.example` はダミー値で commit。ui ユニットの `EnvironmentFile=` は同一 OS ユーザ（単一利用者）なので受容 | リポジトリ・systemd ユニット |
| (7) | pytest の**静的走査**: リポジトリ内（`.env` を除く）に `LLM_API_KEY_[A-Z0-9_]*=\S+`・`sk-[A-Za-z0-9-]{20,}` 様式が無い／config.json と領域 A 雛形に禁止キー名が無い／マスクフィルタの単体テスト（キーあり・大文字小文字・入れ子） | `packages/common/tests/`・`apps/api/tests/` |

新ツール（gitleaks 等の pre-commit）は足さない（憲章 §4 CI＝lint／import-linter／pytest）。

## 5. 統合テストでの差し替え（付2 申し送り handoff_x2 §3-1 → D15-l）

| 対象 | 層 | 差し替え方 |
|---|---|---|
| `VAULT_ROOT`／`SETTINGS_ROOT`／`TRIGGER_ROOT`／`BEADS_DIR`／`BD_BIN`／`DOWNLOADS_DIR` | (b) | fixture が一時 dir を作り **環境変数を設定**（pytest `monkeypatch.setenv`。⑦が⑥に勝つ）または一時 `.env` を `_env_file` で指定 |
| poll 間隔・`stable_seconds`・`watcher_enabled`・timeout・`max_returns` 等 | c1 | **`build()` の init 引数**で `*Config` を差し替え（⑧。D14-e の override 引数を config にも広げる。M1 要求） |
| 偽 LLM サーバの `base_url`・`model_id` | c2 | 一時 `SETTINGS_ROOT/global/llm_profiles.yaml` を fixture が書く（`api_key_ref` は空か `LLM_API_KEY_FAKE` を setenv） |
| `max_requests_per_kickoff`・`max_cost` | c2 | 一時 `global/limits.yaml`（PJ 上書きは `overrides`） |
| トリガー定義 | c2 | 一時 `TRIGGER_ROOT/triggers.yaml` |
| 有効 MOD 一覧 | c1 | `build()` 引数（D14-e） |

## 6. 環境情報ファイル（人間向け。リポジトリ外＝vault 側にユーザが作る）の項目雛形

憲章 §4「WSL 固有の環境事実は憲章に書かず環境情報ファイルへ」・工程文書 E1 成果物「プロキシ／PAT 等の環境変数／API_KEY」。**app はこれを読まない**（D15-c）。

1. 実行基盤: Windows 11＋WSL2 Ubuntu 24.04／systemd 有効化の手順／Windows 起動時の WSL 常駐化／`uv` の所在
2. systemd ユニット 2 本（app・ui）: ユニット名・`ExecStart`（`uv run …`／`streamlit run … --server.port ${UI_PORT}`）・`WorkingDirectory=<repo>`・ui のみ `EnvironmentFile=<repo>/.env`・`Restart=`（憲章 §10 注記）・journalctl の読み方
3. `.env` の実値の控え（(a) は**値を書かず**「どこで発行したか」だけ。(b) は実パス・ポート）
4. 外部ツールの所在: `bd`（`BD_BIN`）・`BEADS_DIR`・Dolt／beads-ui・`glab`（次 ver）・LLM: OpenRouter／ローカル llama-server の起動方法（LLM サーバ運用は本システムの外 憲章 §4）
5. Windows 側: vault の実パス（Dropbox）・DL フォルダ・Obsidian・ブラウザ・（退避経路 EIF-12 を使うときだけ）PowerShell FileSystemWatcher
6. テスト用の一時領域と偽 LLM サーバの起動方法（付2）

## 7. AI 設定の CrewAI 規約化（D15-j＝A′。工程文書末尾「最後の確認」への回答）

**裁定**: AI 設定（領域 A）は CrewAI 標準の **`agents.yaml`／`tasks.yaml` と同一フィールド名の YAML を正本**にし、**ローダは自前**（validate・casbin・不変スナップショット・issue／段ごとの動的 Crew 構築のため。@CrewBase は静的クラス前提で、PoC も同じ理由で `Agent(...)` 直組み）。**`tools.yaml` は置かない**（CrewAI 標準に無い。tools は agents.yaml からコード登録ツール名を参照＝classic の `@tool` 名参照と同じ意味論。`commands.yaml` は維持）。UI（UC-07）はこれらの YAML を編集する。

| 問い（工程文書） | 回答 |
|---|---|
| AI エージェントは agents.yaml で定義される形か？ | **なる**（P5-R1＝D16 で改訂済み）。`global/agents.yaml`（ペルソナ既定）＋`projects/<key>/agents.yaml`（PJ メンバ。キー＝PersonaKey）。フィールド＝CrewAI `Agent` の公開フィールド（role／goal／backstory／llm／tools／skills／mcps／max_iter／max_rpm／allow_delegation…）。`llm:`＝llm_profiles.yaml の name（classic の `@llm` 名参照と同義）、`tools:`＝コード登録名。未知キーは CrewAI `process_config` が無視するので、独自キーを足しても @CrewBase 互換は壊れない（ただし本 ver は validate で許可集合を絞る） |
| tasks.yaml は使っているか？ | **使う**（P5-R1＝D16 で改訂済み）。`global/tasks.yaml`（5 段 program／project／subrequest／child／plain の description／expected_output／agent／context）＋`projects/<key>/tasks.yaml`（任意上書き）。`{変数}` は kickoff 時にシステムが埋める（CrewAI の inputs と同義）。ゲート論理（MIN_ITEMS・受け入れ条件検査）は stages のコードのまま。P5D-028「テンプレートはコード定数」を覆す |
| tools の中に何があるか？ | executor-F04〜F09 の **12 種**（全て GuardedTool 基底・casbin enforce 前段）: 共通 7＝`read_issue`・`record_progress`・`list_input_files`・`read_input_file`・`list_outcome_files`・`read_outcome_file`・`escalate_to_human`／propose＝`propose_breakdown`／execute＝`write_outcome`・`complete_issue`・`run_command`／review＝`submit_review`。mode／purpose 別の集合（F04）は P5-R1（D16-a）で `global/agents.yaml` の `tools:`＋tasks.yaml の Task.tools に移した。**許可**は `policy.csv`（装備とは別層。フェイルクローズ P5D-032） |

**調査した事実**（出典: docs.crewai.com Agents／Crews／Tasks／Using Annotations／Agent Capabilities、GitHub `crew_base.py`・`utilities/config.py`・`agent/core.py`。PoC は crewai 1.15.10〜1.15.12）:
- classic（@CrewBase）: `config/agents.yaml`＋`config/tasks.yaml` を `yaml.safe_load` → `Agent(config=…)`／`Task(config=…)`。`llm:`・`tools:`・`step_callback:` は**クラス内 `@llm`／`@tool`／`@callback` メソッド名**で解決。`process_config` は宣言フィールドだけコピー、未知キーは黙って無視。`skills`／`mcps`／`apps` は Agent の宣言フィールド。
- JSONC（v1.15 の新規推奨）: `crew.jsonc`（tasks 配列）＋`agents/<name>.jsonc`、tools は `"SerperDevTool"`（組込）か `"custom:<name>"`→`tools/<name>.py`。classic は「引き続きサポート」。フィールド名が同じなので YAML→JSONC は機械変換できる。
- **`tools.yaml` は標準に無い**（classic・JSONC とも。Tool は Python で、YAML からは名前参照のみ）。

## 8. P5-R1（別セッション。M1 の前）への要求 — **実施済み（2026-08-20 D16。仕様の正本は `../p5r1/p5r1_notes.md`）**

対象: settings／stages／executor／orchestrator の p5（sequence・data_model・feature_list・notes）、p4 契約 csv・図、p2 DM の追記、D06-e・P5D-015／017／028 を覆す D 記録。要点:
1. settings: 領域 A の実体を `global/agents.yaml`・`projects/<key>/agents.yaml`・`global/tasks.yaml`・`projects/<key>/tasks.yaml`（＋維持: project.yaml・policy.csv・model.conf・llm_profiles.yaml・limits.yaml・commands.yaml・hooks.yaml・humans.yaml・rules.md）に改める。`roles/<persona>.md` は agents.yaml の backstory に吸収。UC-07 の UI・validate（許可フィールド集合・`llm` がプロファイルに在る・`tools` がコード登録名に在る・`agent` が persona に在る・禁止キー D15-i (5)）・MemberResolver.resolve の戻り（Member に CrewAI フィールドを載せる）。**新 IF**: `SettingsReader.get_tasks(project_key) -> TaskTemplates`（orchestrator が読む）
2. stages: 純粋性（D09-f・P5D-028 の I/O なし）は維持し、`build_prompt(…, templates)` でテンプレートを**引数で受ける**（settings 依存を作らない）。段テンプレートのコード定数は出荷時既定＝`global/tasks.yaml` の雛形に移す
3. executor: `Agent(**)` を Member の CrewAI フィールドから組む（`llm` はプロファイル→LlmFactory、`tools` は名前→GuardedTool 登録、`skills`／`mcps` はそのまま）。F04 の mode／purpose 別集合は agents.yaml 既定に移し、executor 側は「許可された登録名だけ」を組む
4. orchestrator: kickoff 開始時の読取に `get_tasks` を加え stages に渡す（p5 orchestrator 主図・契約 csv）
5. 台帳（本フェーズ）CFG-69〜72・83 の行と `型` 列のクラス名を P5-R1 の data_model に合わせて改訂する

## 9. 未決（D15 §6 と同じ）

- ~~c1 の 3 新設キーの p5 data_model への転記~~ → P5-R1（D16）で転記済み
- `settings.system_actor`／`orchestrator.system_actor` の一致検査の置き場（起動時か selfcheck か）は M1
- 環境情報ファイルの実体作成はユーザ（§6）
- リポジトリ登録（CFG-74）の再浮上条件＝git 操作を入れるとき（D02-b・R-13-c）
