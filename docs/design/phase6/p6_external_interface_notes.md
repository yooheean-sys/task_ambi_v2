# 外部インターフェース一覧・配置図・API Contract 説明（第6フェーズ）

**位置付け**: `p6_external_interface.csv`（★）／`p6_deployment.mmd`（★）／`openapi.yaml` の短文の正本。裁定は **D12**（`設計判断_V4-D12_フェーズ6前提の裁定.md`）。食い違えば D12 が勝つ。
**入力**: `../phase1/01_context.mmd`（外部システム）／`../phase3/p3_bc_ownership.csv` STORE-01〜06（外部ストアの所有）／`../phase3/p3_bounded_context.mmd`／`../phase5/<MOD>/p5_<MOD>_sequence*.mmd`（外部実体レーン）／`../phase5/<MOD>/p5_<MOD>_feature_list.csv`（router 行・build 行・エラー時の振る舞い）／`../phase5/p5_decision_log.csv`／`../phase4/p4_module_contract.csv`（14 Protocol）／憲章 §2・§4・§5・§6・§10／`handoff_p5.md`
**レジーム**: 改訂可（csv・図・yaml と同時に更新）。
**工程文書との食い違い**: INPUT の `p3_context_map.csv` は存在しない（phase3 の対応物は `p3_bc_ownership.csv`）。csv 列「相手(EXT-*)」の EXT-* はどの成果物にも未定義だったため、本フェーズで §1 の表として新設した（D12-a）。

## 0. 一言でいうと

本システムは非コンテナ・単一プロセス（systemd 2 ユニット app／ui）で自前 DB を持たないので、コンテナ図の代わりに **app プロセスの外側との境界** を 13 件（EXT-01〜13）に列挙し、相手×方向で 1 行の外部インターフェース 13 行（EIF-01〜13）に落とした。乗り換え対象（憲章 §2 のベンダーロック対策）は **beads と LLM API の 2 件**（D12-d。LLM は将来 `claude -p` 等の CLI バックエンドへ切り替えたいというユーザ要求による。憲章 §2 の指定ツール列挙と §4 の HTTP 前提に差分が出るので PATCH-7 を提示）。HTTP 入口は apps/ui・自分・Windows 側 FSW の 3 クライアントから叩かれるので API Contract は `openapi.yaml` に書く（D12-c）。`/health` は apps/api 所有の liveness のみ（D12-e）。

## 1. 外部実体 EXT 表（ID の正本。追記のみ・欠番不埋め・再利用禁止）

| EXT | 名称 | 種別 | 実体・所在 | 所有／読み手 | 出典 |
|---|---|---|---|---|---|
| EXT-01 | beads | 外部システム | bd CLI（subprocess、`--json`、`BEADS_ACTOR` env）。内部 Dolt は触らない（憲章 §2） | board（STORE-03） | 01_context `beads`／STORE-03 |
| EXT-02 | Obsidian vault | 外部システム | `VAULT_ROOT`（/mnt/c 経由の Windows 側フォルダ）。PJ 入力・役割フォルダ・`outcome/`（＋`outcome/_archive/`） | vault（STORE-04） | 01_context `vault`／STORE-04／P5D-008/009 |
| EXT-03 | LLM API | 外部システム | OpenAI 互換 HTTP（OpenRouter／ローカル llama.cpp）。実体は LLM プロファイル（DM-11）で切替 | executor（接続）・monitor（Hook で観測） | 01_context `llm`／憲章 §4／P5D-013/030 |
| EXT-04 | DL フォルダ | 外部システム | /mnt/c 配下の監視対象。読取のみ・投下ファイルは消費しない | trigger（STORE-06） | 01_context `dlfolder`／STORE-06／D06-h |
| EXT-05 | 設定ファイル領域 A | 自システム所有 FS | `SETTINGS_ROOT`（.env）配下 `projects/<key>/{project.yaml, agents.yaml, tasks.yaml, policy.csv, rules.md, hooks.yaml}`＋`global/{agents.yaml, tasks.yaml, model.conf, llm_profiles.yaml, limits.yaml, commands.yaml, humans.yaml}`（members.yaml・roles/ は D16 で廃止）。憲章 §2 例外 (a) | settings（STORE-01） | STORE-01／P5D-015/019・D15-j・D16 |
| EXT-06 | 設定ファイル領域 B（triggers.yaml） | 自システム所有 FS | `TRIGGER_ROOT`（.env）/`triggers.yaml`（DM-22/23 の宣言＝正）。憲章 §2 例外 (a) | trigger（STORE-02） | STORE-02／P5D-040 |
| EXT-07 | 実行履歴 csv | 自システム所有 FS | `TRIGGER_ROOT/history/history_YYYYMM.csv`（開始行追記＋終了時書換え）。憲章 §2 例外 (b)（PATCH-6 適用後の文言） | trigger（STORE-05） | STORE-05／P5D-039/043 |
| EXT-08 | .env | 自システム所有 FS | リポジトリ直下 1 つ（gitignore）。`VAULT_ROOT`／`SETTINGS_ROOT`／`TRIGGER_ROOT`／LLM api_key 群（`api_key_ref` で参照）。憲章 §5 | vault・settings・trigger・executor（各 MOD の Settings クラスが pydantic-settings で読む） | 憲章 §5／P5D-009/013/015/040／各 data_model の *Env |
| EXT-09 | config.json 群 | 自システム所有 FS | ルート config.json（アプリ名・環境ラベル・ログ・有効 MOD 一覧）＋各 MOD の config.json（規約パス・commit 対象）。憲章 §5 | apps/api（ルート）・8 MOD（自 MOD 分） | 憲章 §5／各 feature_list の build 行 |
| EXT-10 | app プロセス内の UI ページ（NiceGUI） | HTTP クライアント（app と同一プロセス） | apps/ui が settings.ui／trigger.ui を束ね、app と同じ待受の `/ui` に載る。操作は同一プロセスでも app を HTTP で叩くのみ（憲章 2.0.0 §4 注記・J-5） | settings・trigger の router | 憲章 2.0.0 §4 プロセス構成／D23-g（016 で systemd ユニット ui を廃止）／D11-f／p4 図 |
| EXT-11 | 自分（ブラウザ／curl） | HTTP クライアント | human 起動 `POST /kickoffs`・状態参照・`/health`。UI を持たない orchestrator の入口 | orchestrator の router・apps/api | p4 図「Me → POST /kickoffs」／P5D-035／DIFF-27 |
| EXT-12 | Windows 側 PowerShell FileSystemWatcher | HTTP クライアント | PollingObserver 不合格時の退避経路。`POST /hooks/file` に投下通知 | trigger の router | 付録トリガー §3／P5D-042 |
| EXT-13 | stdout → journald | 観測出口 | kernel `LoggerFactory` の構造化ログ（correlation_id 付き）。`journalctl -u app` で読む。憲章 §6 | apps/api・8 MOD | 憲章 §6／P5D-006／common notes |

## 2. 除外したもの（行にしない理由）と p5 外部レーンの写像

| 候補 | 扱い | 理由 |
|---|---|---|
| beads-ui | 配置図にのみ（Windows 側／WSL の人間向け画面） | システムは beads-ui と通信しない（人間が beads を見る動線。01_context の `beadsui → beads` は内部） |
| Obsidian アプリ | 配置図にのみ | 人間が vault を編集する道具。システムの相手は vault の FS（EXT-02） |
| Dolt（beads 内部ストア） | 行にしない | 憲章 §2「内部ストアを直接読み書きしない」。bd CLI（EXT-01）の内側 |
| systemd | 配置図にのみ（ユニット app／ui の器） | 実行基盤であって通信相手ではない。`Restart=` 等の環境事実は環境情報ファイル（憲章 §4） |
| タイマ（APScheduler）／ファイル投下イベント | 行にしない（p5 trigger 主図のレーン `タイマ` は app プロセス内の BackgroundScheduler） | 01_context「タイマはシステム内部の常駐機能」。ファイル投下の相手は EXT-04／EXT-12 |
| apps/api（p5 trigger 主図の lifespan 呼び手レーン） | 行にしない | app プロセス内の composition root。境界ではない |
| GitLab／GitLab Runner CI／git | 行にしない | 開発時の基盤（憲章 §4）。実行時にシステムは通信しない。GitLab 連携は次 ver 候補（01_context 注記） |
| apps/ui が読む .env／config.json | 行にしない（EIF-08・EIF-09 に含む） | 016 で apps/ui は **app プロセス**の中に入ったので、別行を立てず EIF-08・EIF-09 の「利用するモジュール」に apps/ui を載せる（既掲）。読むのはルート .env の (b)（APP_BASE_URL・APP_HOST・APP_PORT）とルート config.json のみ（D15-f・x3_config_catalog.csv）。UI 専用の待受ポート UI_PORT（CFG-13）は D23-e で廃止 |
| `outcome/_archive/`・`SETTINGS_ROOT/_archive/` | EXT-02／EXT-05 の内側 | 所有 MOD が自領域内に作る退避先（P5D-008） |

p5 シーケンスの外部実体レーン → EXT の写像（selfcheck (iii) の根拠）: `bd CLI`→EXT-01／`Obsidian vault`→EXT-02／`LLM API`→EXT-03／`DL フォルダ`→EXT-04／`設定ファイル領域 A`→EXT-05／`設定ファイル領域 B`→EXT-06／`実行履歴 csv`→EXT-07／`.env`→EXT-08／`config.json`→EXT-09／`自分`→EXT-10（ui 経由）＋EXT-11（直接）／`タイマ`・`apps/api`→内部（上表）。p3 STORE-01〜06 → EXT-05／06／01／02／07／04。

## 3. csv の列の読み方

- **ext_if_id**: `EIF-nn`（追記のみ・欠番不埋め・再利用禁止）。1 行＝相手（EXT）×方向（D12-b）
- **方向**: app プロセスから見て `out`（app→相手）／`in`（相手→app）／`in/out`。監視は `in（監視）`、ログ読取を伴う書込は `out（＋表示読取）` と限定を括弧で付ける
- **プロトコル**: 通信の形（CLI subprocess／ファイル I/O／HTTP(S)／watchdog／stdout）＋要点。HTTP は叩かれるパス群を併記（個々の操作の正は `openapi.yaml`）
- **利用するモジュール(MOD)**: `;` 区切り。語彙＝8 MOD ∪ `apps/api`（`/health` とルート config.json の読み手）。`8 MOD` と書いた行は全 MOD
- **認証方式**: 憲章 §10 によりセキュリティ機能は対象外。HTTP 入口は「なし（localhost bind）」、CLI／ファイルは OS ユーザ権限、LLM は `.env` の api_key（Bearer。`api_key_ref` 経由、実値は executor だけが読む）
- **失敗時の扱い**: 起動時（build）と実行時を `／` で分ける。根拠の fn_id・P5D を括弧で引く。自動復旧は作らない（憲章 §10。systemd 再起動に委ねる）
- **乗り換え対象か**: D12-d。Y＝beads（憲章 §2 指定ツール）＋LLM API（ユーザ要求。PATCH-7 で憲章 §2 の指定ツールに追加）。Y 行は「対応する Protocol 名」が非空で、名前は p4 契約 csv の 14 Protocol に実在する（selfcheck (iv)）
- **対応する Protocol 名**: 相手との出入りが通る common/interfaces の Protocol。入口（in）の HTTP 行は Protocol を通らず router で受けるので `—（router）`、MOD 内 repository が直接触る自システム所有 FS は `—（<MOD> 内 repository）`

## 4. LLM API を Y にしたことの読み方（D12-d）

- **切替点**: executor の `LlmFactory`（`executor/models.py`。P5D-030）。`LLMProfile.provider_kind` ごとにアダプタクラスを分け（現在 `openai_compatible`／`openrouter`）、crewai の `LLM(interceptor=adapter)` に monitor の `LLMHttpHook` を装着する。crewai 型は契約に出せない（linter (3)・憲章 §7-2）ので、「LLM を返す Protocol」は common に置けず、**切替点は構造上 executor 内部**になる。憲章 §2 の「アダプタクラスを分けて実装し、Protocol 経由でのみ利用する」は、本システムでは「アダプタ分離＝LlmFactory の provider_kind 別クラス、Protocol 経由＝上限・観測の差し込み点 `LLMHttpHook`（IF-40〜43）」で充足と読む。csv の Protocol 列にこの 2 つを併記した
- **憲章との差分**: §2 指定ツール「現時点: beads」に LLM バックエンドが無い／§4「LLM: OpenAI 互換エンドポイント … app → LLM: HTTP … 観測・上限停止は HTTP transport 層の Hook で行う」は HTTP 以外の backend を想定していない → `p6_constitution_patch.md` **PATCH-7**（適用はユーザが両版へ同時、付1 で適用後の文言で判定）
- **`claude -p` 対応そのもの**（provider_kind=`claude_cli`・CLI アダプタ・`LLMHttpHook` の httpx 型を中立型へ＝IF-40〜43 改訂・トークン／コストの観測点を CLI の JSON 出力へ）は**本 ver 対象外**（D12 §6-1）。再浮上条件＝ユーザが claude -p 経路を実際に使い始めるとき。monitor・executor の第5 成果物は今回触らない

## 5. 配置図（`p6_deployment.mmd`）の読み方

- subgraph 3 つ（Windows 側／WSL2／外部サービス）は `01_context.mmd` の Boundary と同じ切り方。WSL2 の中に systemd ユニット **app**（apps/api＋common＋8 MOD。APScheduler・watchdog は trigger 内のスレッド、kickoff はワーカースレッド）と **ui**（apps/ui＋settings.ui／trigger.ui）、ローカル FS（リポジトリ直下 .env・config.json 群／SETTINGS_ROOT／TRIGGER_ROOT／beads `.beads`）、/mnt/c（VAULT_ROOT・DL フォルダ）、journald
- 辺は **MOD→外部のみ**（MOD→MOD は p4 図の正なので描かない）。ラベル＝プロトコル（EIF 番号付き）。ポート・パスの実値は書かず記号（`APP_PORT`／`VAULT_ROOT` 等）で、実値は付3
- beads-ui は WSL2 側（01_context どおり）、ブラウザ・Obsidian・PowerShell FSW は Windows 側

## 6. openapi.yaml の読み方

- OpenAPI 3.1 手書き。`info.version` は設計版 `v4-design`。servers は `http://localhost:{APP_PORT}`（変数。実値は付3）
- paths の所有を `x-mod`（settings／orchestrator／trigger／apps/api）、対応 fn を `x-fn`（`settings-F21` 等）で付ける。M1 で FastAPI が生成する `/openapi.json` と paths・operation 単位で diff する（スキーマ細部は実装が正になり得るが、paths と応答コードは本書が正）
- settings の 10 リソース: `projects`／`agents`／`tasks`／`llm-profiles`／`limits`／`commands`／`policies`／`rules`／`hooks`／`humans`（members・rules/roles は D16 で廃止）（humans は settings-F17 が CRUD すると書いており F21 の列挙から漏れていた → 追加。p5 csv F21 概要はフェーズ末に改訂）
- エラー: 422＝`Problem`（settings の validate 違反は `problems[]`、trigger の validate 違反も同形）／404＝未登録・不明 id／409＝single-flight busy（`POST /kickoffs`）。FastAPI 既定の `{"detail": ...}` は 404/409 に使う
- 認証なし（憲章 §10）。CORS 不要（ui は同一ホスト）

## 7. 自己検証（D12-g）

`python3 p6_selfcheck.py --render` → **ERRORS: none**（2026-08-19。P5-R1（2026-08-20 D16）後: csv 13 行・EXT 13・Protocol 14・openapi 27 paths／51 operations／45 schemas（/settings/agents・/settings/tasks 新設、/settings/members・/settings/rules/roles 廃止）、parser=pyyaml。PyYAML 無しの最小パーサ経路も ImportError 偽装で ERRORS: none）。(viii) PoC 無変更・(ix)(x) 改訂と未決登録は D12 §4・§5・handoff_p6。

- (iii) の写像表 `LANE_TO_EXT`／`STORE_TO_EXT` は §2 と同内容を selfcheck に持つ（二重持ちだが、片方を直したら selfcheck が落ちるので乖離は検出される）
- (vi) の router 行からのパス抽出は `/settings/…`・`/kickoffs…`・`/triggers…`・`/history`・`/watcher`・`/hooks/file` に限定した正規表現。新しい接頭辞の router が増えたら `router_paths_from_p5` を足す

## 8. 未決（第6 固有）

1. **`claude -p` バックエンドの設計**（§4。本 ver 対象外・再浮上条件付き）
2. **ポート・bind アドレス・UI ページの base URL**: 付3 で裁定（APP_HOST／APP_PORT／APP_BASE_URL＝.env の (b)。既定 127.0.0.1・8000・導出。D15-f）。UI 専用の UI_PORT（既定 8501）は 016 で廃止し、UI は app と同じ待受の /ui に載る（D23-e）。本フェーズの図・openapi は記号のまま
3. **API の prefix／バージョン**（`/api/v1` 等）: 置かない前提で書いた（憲章 §2「FastAPI アプリ 1 個が入口」、p5 のパス表記どおり）。必要になれば openapi の servers で吸収
4. **`POST /hooks/file` の発信元検証**: 憲章 §10 により置かない。localhost 以外から叩かれる構成になったら再提起
5. **apps/ui の設定（.env／config の読み方）**: 付3 で裁定（ルート .env の (b) とルート config.json のみ。D15-f・PATCH-12）。016 で apps/ui は app プロセスの中に入り、ui ユニットと、それに限る EnvironmentFile= の例外は廃止した（D23-e）
