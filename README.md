# task_ambi — AI 駆動タスク処理システム

AI エージェントにタスクを実行させるためのモジュラーモノリス。課題の盤面は **beads（`bd` CLI）**、入力と成果物は **Obsidian vault（ファイルシステム）**、実行は **CrewAI ＋ LLM** が担い、本システムはそれらを公約インターフェース経由で束ねる。

常駐プロセスは **1 つ**。**app**（FastAPI・単一プロセス。全モジュールが同居し、スケジューラとファイル監視もこのプロセス内）に、**NiceGUI の画面が `/ui` で同居する**（業務ロジックを持たず、同じプロセスの中からでも app の HTTP API 経由でのみ操作する）。非コンテナで、WSL2（Ubuntu 24.04）上の systemd **1 ユニット**として常駐させる前提（`016`・設計判断 D23。それ以前は Streamlit の別プロセスで 2 ユニットだった）。

> 事実の正は現物コード。本書と食い違ったらコードが勝つ。各表には正本の在り処を併記してある。

---

## 1. 構成

```
task_ambi_v4/
├── pyproject.toml          # uv workspace ルート（ゲート設定もここに一元化）
├── uv.lock                 # 依存の固定（CI は --frozen でこれに従う）
├── .env                    # 秘密と環境依存値（gitignore・§4-2）
├── .env.example            # .env の雛形（注釈つき・commit）
├── config.json             # アプリ全体の非機密設定（§4-3）
├── .importlinter           # モジュール境界の依存ルール（9 契約）
├── packages/
│   ├── common/             # 共通基盤（モジュール間契約 Protocol・受け渡し型・ログ基盤）
│   └── features/<mod>/     # 機能モジュール 9 本（下表。各々 config.json を持つ）
├── apps/
│   ├── api/                # FastAPI 起動エントリ（composition root）
│   └── ui/                 # 統合した起動点（app を組み立て、各モジュールの UI ページを `/ui` に載せる）
└── docs/                   # 説明書・設計文書（§11）
```

| モジュール | 役割 | HTTP | UI |
|---|---|---|---|
| `board` | beads（`bd` CLI）盤面アダプタ。課題の読み書き | — | — |
| `vault` | Obsidian vault（FS）アダプタ。入力の読取と成果物の書込 | — | — |
| `repo` | プロジェクトのローカルリポジトリ（git 作業ツリー）アダプタ。ファイルの CRUD（git 操作はしない。D25） | — | — |
| `settings` | AI 設定（領域 A）の所有者。ペルソナ・LLM・権限・上限・コマンド | 39 操作 | 2 枚 |
| `stages` | 段の定義と提案の検証（外部 I/O を持たない） | — | — |
| `monitor` | LLM 実行の監視・上限判定・失敗の採点と盤面への写像 | — | — |
| `executor` | CrewAI 実行（1 Agent・1 Task・逐次）とツール 16 種 | — | — |
| `orchestrator` | 実行ループ（kickoff） | 3 操作 | — |
| `trigger` | 常駐トリガー（cron / interval / file）。領域 B の所有者 | 10 操作 | 1 枚 |

モジュールは `config.json` の `enabled_modules`（§4-3）に載っているものだけが組み立てられる。

---

## 2. 前提（先に用意するもの）

これらが揃っていないと **app は待受を開始せずに中断する**。

### 2-1. 実行基盤

- **WSL2（Ubuntu 24.04）**
- **Python 3.13**（`.python-version` で固定。`uv python install` / `uv python pin` で用意する。pyenv は使わない）
- **uv**（依存・仮想環境・実行をすべて uv に一本化している）

### 2-2. `bd`（beads CLI）— 必須

PATH 上にあるか、`BD_BIN` に絶対パスを指定する。**既定構成では `bd` が無いと app 全体が起動しない** — `settings` が `BoardReader`・`VaultReader` を、`monitor` が `BoardReader`・`BoardWriter` を要求し（[registry.py:172](apps/api/src/api/registry.py#L172)）、その供給元は `board` だけだから。`board` を `enabled_modules` から外すと今度は `UnsatisfiedContractError` になる。

beads ワークスペース（`.beads` ディレクトリ）は**アプリの外であらかじめ用意する**。`bd init` は `.beads/` だけでなく git リポジトリを作って commit まで行うため、本システムのホワイトリスト 9 サブコマンドに含めていない（[board/repository.py:28](packages/features/board/src/board/repository.py#L28)）。

### 2-3. 4 つのルートディレクトリ — **アプリは作らない**

いずれも起動時に実在を検査し、不在なら中断する。

| 環境変数 | 何を指すか | 追加の制約 |
|---|---|---|
| `VAULT_ROOT` | Obsidian vault のルート | 実在するディレクトリ |
| `SETTINGS_ROOT` | 領域 A（AI 設定）のルート | **絶対パス必須**・実在するディレクトリ |
| `TRIGGER_ROOT` | 領域 B（トリガー定義・履歴）のルート | **絶対パス必須**・実在するディレクトリ |
| `BEADS_DIR` | **`.beads` ディレクトリ自身**（それを持つ親ではない） | 直下に `config.yaml` があること |

`SETTINGS_ROOT` の直下には `global/`・`projects/` と雛形 6 ファイルが起動時に生成されるが、**ルート自体は生成されない**（§4-5）。

### 2-4. LLM の口

OpenRouter（`LLM_API_KEY_OPENROUTER` に鍵）か、OpenAI 互換のローカルサーバ（既定プリセットは `http://127.0.0.1:8080/v1`）。**LLM サーバ自体の運用は本システムの外**。

---

## 3. セットアップ

```bash
uv sync --frozen
```

`--frozen` は `uv.lock` を更新しない（CI の `before_script` と同じ）。依存を足すときだけ素の `uv sync` を使う。

```bash
cp .env.example .env
chmod 600 .env
$EDITOR .env
```

`.env.example` は注釈つきの雛形で、各キーの意味・誤配置の注意が書かれている。埋めるべき値は §4-2 の表を参照。

```bash
# アプリはこれらを作らない。不在なら起動時に中断する。
mkdir -p <SETTINGS_ROOT> <TRIGGER_ROOT>
# VAULT_ROOT は既存の Obsidian vault を指す（本システムは作らない）
# BEADS_DIR は `.beads` 自身。用意は beads 側の作業（§2-2）
```

---

## 4. 設定

### 4-1. 設定の置き場（5 つ）

正本: [config.py:103](packages/common/src/common/kernel/config.py#L103)

| 置き場 | commit | 書き手 | 中身 |
|---|---|---|---|
| `.env`（ルート） | しない（gitignore） | 人間 | 秘密（LLM の鍵）と環境依存値（各ルートパス・ポート） |
| `config.json`（ルート） | する | 人間 | アプリ全体の非機密設定（ログ・有効モジュール） |
| `packages/features/<mod>/config.json` | する | 人間 | モジュール固有の非機密の挙動設定 |
| 領域 A（`SETTINGS_ROOT` 配下） | しない | UI（settings 経由）＋ 人間の直接編集 | AI 設定の宣言ファイル群 |
| 領域 B（`TRIGGER_ROOT` 配下） | しない | UI（trigger 経由） | トリガー定義と実行履歴 |

原則:

- **`config.json` の値は環境変数でも `.env` でも上書きできない**（設定ソースを JSON ファイルだけに絞ってあるため構造的に不可能）。各キーの正規の置き場は 1 つだけ。
- コードのパスは規約（固定の相対パス）で知る。`.env` にパスを持たせない。
- `.env` と `config.json` の位置は cwd に依存しない。`[tool.uv.workspace]` を持つ `pyproject.toml` を上方向に探してルートを決める（[config.py:32](packages/common/src/common/kernel/config.py#L32)）。

### 4-2. `.env`（秘密・環境依存値）

正本: [.env.example](.env.example)。ルート直下に 1 つ・gitignore・権限 600。各読み口は `extra="ignore"` なのでキーの同居は自由。現在 11 キー（`016` で Streamlit の待受ポート `UI_PORT` を廃止した。手元の `.env` に残っていても読まれないので消してよい）。

| キー | 秘密 | 必須 | 読む場所 | 意味 |
|---|---|---|---|---|
| `LLM_API_KEY_OPENROUTER` | ○ | プロファイルが参照する場合 | executor（実行ごと） | LLM の鍵の実値。**キー名は自由**で、領域 A の `llm_profiles.yaml` の `api_key_ref` がこの名前を参照する |
| `LLM_API_KEY_LOCAL` | ○ | 同上 | 同上 | 同上（ローカル LLM 用。空でよい） |
| `VAULT_ROOT` | — | vault 有効時 | vault の build | Obsidian vault のルート絶対パス |
| `SETTINGS_ROOT` | — | settings 有効時 | settings の build | 領域 A のルート絶対パス |
| `TRIGGER_ROOT` | — | trigger 有効時 | trigger の build | 領域 B のルート絶対パス |
| `BEADS_DIR` | — | board 有効時 | board | `.beads` 自身。bd 実行時に環境変数として常に注入する |
| `BD_BIN` | — | 任意（既定 `bd`） | board | bd バイナリのパス。systemd 下は PATH が最小なので**絶対パス推奨** |
| `DOWNLOADS_DIR` | — | **任意** | trigger | `file` 種別トリガーの監視基点。未設定なら `file` トリガーの登録だけが飛ばされる |
| `APP_HOST` | — | 任意（既定 `127.0.0.1`） | api・ui | FastAPI の待受ホスト。ui は接続先の導出にも使う |
| `APP_PORT` | — | 任意（既定 `8000`） | api・ui | FastAPI の待受ポート |
| `APP_BASE_URL` | — | 任意 | ui のみ | app への接続先の明示。あれば `APP_HOST`/`APP_PORT` より優先（[ui/config.py:36](apps/ui/src/ui/config.py#L36)） |

**LLM の鍵の解決**（executor）— 鍵は build 時に読まず、**実行のたびに読んでその場で捨てる**（[models.py:332](packages/features/executor/src/executor/models.py#L332)）。executor だけは OS 環境変数を見ず `.env` を直読みする。

| `api_key_ref` | `.env` の状態 | 振る舞い |
|---|---|---|
| `""` | — | `.env` を引かず空文字 |
| 参照名あり | キーが**無い** | 実行失敗（`ExecutorFailure(phase="build")`） |
| 参照名あり | キーがあり値が空 | 空文字のまま続行 |
| 参照名あり | キーがあり値がある | その実値 |

ログでは `log.redact_keys`（§4-3）に一致するキーが伏字になる。

### 4-3. ルート `config.json`

正本: [config.py:91](packages/common/src/common/kernel/config.py#L91)。`extra="forbid"` なので**未知キーがあると起動に失敗する**。不備は `ConfigurationError` に翻訳され、**欠けたキー名と読んだファイルの絶対パス**が本文に載る。

| キー | 型・許容値 | 既定 | 意味 |
|---|---|---|---|
| `app_name` | str | **必須** | アプリ名（`/health` と UI タイトルに出る） |
| `env_label` | str | **必須** | 環境ラベル（`dev` 等。`/health` に出る） |
| `log.level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | ログレベル |
| `log.format` | `json`/`text` | `json` | ログ書式 |
| `log.correlation_header` | str | `X-Correlation-ID` | 相関 ID の HTTP ヘッダ名 |
| `log.redact_keys` | list[str] | `["*_API_KEY*", "Authorization", "api_key"]` | ログ伏字対象のキーパターン |
| `enabled_modules` | list[str] | **必須** | 組み立てるモジュール名の一覧。ここから外すと配線されず、router も UI ページも出ない |
| `defaults.encoding` | str | `utf-8` | ファイル I/O の既定文字コード |

### 4-4. モジュール `config.json`（9 本）

置き場は `packages/features/<mod>/config.json`（commit 対象）。いずれも `extra="forbid"`。**既定値は現物の値**。

**board** — [board/config.json](packages/features/board/config.json)

| キー | 現値 | 意味 |
|---|---|---|
| `timeout_sec` | 120 | bd 1 回の実行タイムアウト（秒）。超過は rc=124 で打ち切り |
| `lock_wait_max_attempts` | 3 | ロック競合と分類されたときだけ再試行する上限回数 |
| `lock_wait_base_sec` | 0.5 | 再試行の待ち時間の基数（秒） |
| `lock_hints` | lock / locked / … | この語が stderr・stdout に含まれたらロック競合と分類（bd はあらゆる失敗を rc=1 で返すため） |
| `list_limit_none` | true | 一覧系の bd 呼び出しに `-n 0`（上限なし）を付ける |

**vault** — [vault/config.json](packages/features/vault/config.json)

| キー | 現値 | 意味 |
|---|---|---|
| `forbidden_chars` | `\ / : * ? " < > \|` | ファイル名の禁止文字（Windows 互換） |
| `forbidden_obsidian` | `# ^ [ ] \| %%` | Obsidian のリンク・タグ構文と衝突する禁止文字 |
| `reserved_names` | CON, PRN, … | Windows の予約名を拒否 |
| `forbid_trailing_dot_space` | true | 末尾のドット・空白を拒否 |
| `max_filename_bytes` | 200 | ファイル名の UTF-8 バイト長上限 |
| `max_outcome_bytes` | 1048576 | **読取**サイズ上限。入力側にのみ掛かり、成果物の読み返しには掛けない |
| `archive_dir` | `_archive` | 成果物上書き時の退避先ディレクトリ名 |
| `list_exclude_prefixes` | `["."]` | 一覧から除外する名前の接頭辞 |
| `encoding` | （キー無し＝null） | null ならルート `defaults.encoding` を継承 |

**repo** — [repo/config.json](packages/features/repo/config.json)（D25。`.env` は読まない）

| キー | 現値 | 意味 |
|---|---|---|
| `max_read_bytes` | 262144 | 読取の上限バイト |
| `max_write_bytes` | 1048576 | 書込の上限バイト |
| `max_list_entries` | 2000 | 一覧の上限件数（超過は `subdir` で絞らせる） |
| `excluded_dirs` | `.git` `.venv` `node_modules` ほか | 一覧で降りないディレクトリ（`.git` は常に除外） |
| `deny_patterns` | `.env` `.env.*` `*.pem` `*.key` ほか | 読み書き・一覧を拒む名前（秘密を LLM に送らない） |
| `encoding` | （キー無し＝null） | null ならルート `defaults.encoding` を継承 |

**settings** — [settings/config.json](packages/features/settings/config.json)

| キー | 現値 | 意味 |
|---|---|---|
| `key_forbidden_chars` | `. / \ :` と空白 | プロジェクトキーの禁止文字 |
| `context_reserve_tokens` | 4096 | 送信前閾値の予備。`threshold_t = context_window − この値`（0 以下になるプロファイルは起動時検証で落ちる） |
| `llm_presets` | openrouter / local | UI のプロファイル作成フォームに出すプリセット（鍵の実値は持たない） |
| `limits_defaults` | requests 60 / cost 1.0 | `global/limits.yaml` が**無いとき**の上限既定 |
| `system_actor` | `system` | システムの行為者名の予約。人間・メンバとの衝突を起動時検証で弾く |

**stages** — [stages/config.json](packages/features/stages/config.json)

| キー | 現値 | 意味 |
|---|---|---|
| `min_items_override` | `{}` | 段名 → 最小項目数の上書き。未知の段名キーは組み立て失敗 |
| `min_acceptance_len` | 10 | 受け入れ条件の最小文字数 |
| `placeholder_patterns` | `"-"` `"tbd"` `"なし"` 等 | 一致するものを「実質未記入」として弾く |
| `max_acceptance_default` | null | 受け入れ条件の件数上限（null＝上限なし） |

**monitor** — [monitor/config.json](packages/features/monitor/config.json)。**6 キーすべて必須**（既定なし。欠落は起動中断）

| キー | 現値 | 意味 |
|---|---|---|
| `grade_map` | 12 種 → F/W/I | 失敗種別 → 等級の対応表。**未知の種別は F に倒す** |
| `envelope_marker` | `[monitor]` | 盤面へ書く封筒コメントの印 |
| `token_estimator` | `chars` | トークン推定方式。`chars`＝`⌈連結content長/4⌉`。`tiktoken` は**既定では未導入**なので導入してから切り替える |
| `token_encoding` | `cl100k_base` | `tiktoken` 方式のときの符号化名 |
| `cost_field_path` | `usage.cost` | LLM 応答 JSON からコストを読むパス |
| `truncation_phrases` | CrewAI の強制打ち切り文言 | 一致したら「打ち切り回答」と検知 |

**executor** — [executor/config.json](packages/features/executor/config.json)。**6 キーすべて必須・`strict=True`**（`"30"` のような文字列は型不一致で起動中断）

| キー | 現値 | 意味 |
|---|---|---|
| `run_command_timeout_sec` | 30 | `run_command` ツールのコマンド実行タイムアウト |
| `max_iter` | 20 | Agent の反復上限の既定（メンバ側の指定が優先） |
| `max_rpm` | null | 毎分要求数上限の既定（null＝制限なし） |
| `event_allowlist` | 10 イベント | CrewAI イベント名の**完全一致**リスト（ワイルドカード不可）。空にすると翻訳が 0 件になる |
| `hook_catalog` | `[]` | 使用を許す差し込み（hook）名の台帳。`hooks.yaml` が参照する名前がここに無いと実行失敗 |
| `disable_crewai_telemetry` | true | `CREWAI_DISABLE_TELEMETRY`・`OTEL_SDK_DISABLED` を立てて外部送信を止める |

`event_allowlist` は CrewAI の**実クラス名**なので、`crewai` の版を上げたら見直す（依存は `>=1.15.17,<1.16` に固定してある）。

**orchestrator** — [orchestrator/config.json](packages/features/orchestrator/config.json)。**5 キーすべて必須・`strict=True`**

| キー | 現値 | 意味 |
|---|---|---|
| `max_returns` | 2 | 差し戻しの上限。達したら人間へエスカレーション |
| `recent_results_n` | 20 | `GET /kickoffs/{id}` が遡れる実行結果の保持件数 |
| `system_actor` | `system` | 盤面の確保・解放・コメントに使う行為者名 |
| `comment_marker` | `[orchestrator]` | 書き込むコメントの印。**差し戻し勘定の前方一致の鍵**なので運用途中で変えると勘定が狂う |
| `review_persona` | `planner` | レビューを担当するペルソナ（実在することを build が検査） |

**trigger** — [trigger/config.json](packages/features/trigger/config.json)

| キー | 現値 | 意味 |
|---|---|---|
| `ready_interval_default_sec` | 300 | 既定トリガー「ready 巡回」の初期間隔（雛形生成時のみ使用） |
| `min_interval_sec` | 60 | `interval` 種別の下限（下回る値は 422 で拒否） |
| `misfire_grace_sec` | 30 | APScheduler の発火遅延の許容秒 |
| `poll_interval_sec` | 5.0 | watchdog `PollingObserver` の巡回間隔 |
| `stable_seconds` | 2 | 投下ファイルの完了検知（サイズ安定）の待ち秒 |
| `watcher_enabled` | true | false なら observer を作らない（`POST /hooks/file` の退避経路は動き続ける） |
| `timezone` | null | cron の時刻帯（null＝システムローカル） |

### 4-5. 領域 A・領域 B のレイアウト

**領域 A（`SETTINGS_ROOT` 配下）** — 正本: [settings/repository.py:32](packages/features/settings/src/settings/repository.py#L32)

```
<SETTINGS_ROOT>/
├── global/                 # 起動時に不在分だけ雛形生成（limits.yaml は生成しない）
│   ├── model.conf          # casbin モデル
│   ├── agents.yaml         # 共通ペルソナ定義
│   ├── tasks.yaml          # 段ごとのタスク雛形
│   ├── llm_profiles.yaml   # LLM プロファイル
│   ├── limits.yaml         # 上限（任意。無ければ settings/config.json の limits_defaults）
│   ├── commands.yaml       # run_command 用コマンド台帳（雛形は空）
│   └── humans.yaml         # 人間の行為者一覧
├── projects/<project_key>/ # project.yaml・agents.yaml・policy.csv（必須）／tasks.yaml・rules.md・hooks.yaml（任意）
└── _archive/               # 解除した PJ の退避先（自動掃除はしない）
```

**領域 B（`TRIGGER_ROOT` 配下）** — 正本: [trigger/repository/definitions.py:30](packages/features/trigger/src/trigger/repository/definitions.py#L30)

```
<TRIGGER_ROOT>/
├── triggers.yaml                   # トリガー定義の正本（初回起動時に無ければ既定トリガー入りで生成）
└── history/history_YYYYMM.csv      # 実行履歴（月ごと・表示専用）
```

領域 A・B とも UI（HTTP 経由）から CRUD する。**各ファイルの形式は docs/manual/設定値と外部仕様.md（非公開） §4.2・§5.5** が正本。

---

## 5. 起動（手動・開発時）

```bash
# app ＋ 画面（1 プロセス。待受は .env の APP_HOST / APP_PORT。既定 http://127.0.0.1:8000）
uv run python -m ui.main
```

画面は `http://127.0.0.1:8000/ui/` に出る。HTTP API（`/health`・各モジュールの口）は**同じ待受**が返す。


注意:

- **`uvicorn api.main:app` は動かない。** [main.py](apps/api/src/api/main.py) にモジュールレベルの `app` 変数は無く、定義されているのは `main()` だけ。
- **組み立てに失敗すると待受を開始せずに中断する。** `create_app()` が `uvicorn.run` より先に呼ばれるため。切り分けは §9。
- **`APP_HOST` の導出値で自プロセスに届かない待受のときは `APP_BASE_URL` を与える。** 画面は `APP_BASE_URL`（無ければ `http://{APP_HOST}:{APP_PORT}`）へ HTTP で接続するので、`APP_HOST=0.0.0.0` のように「待ち受けるが接続先にはできない」値のときは、`APP_BASE_URL=http://127.0.0.1:8000` を明示する。

---

## 6. 常駐（systemd 1 ユニット）

常駐させるのは **app の 1 ユニットだけ**である（`016`・D23。画面は同じプロセスに `/ui` で同居する）。

```ini
# app ユニット（画面を含む）
[Unit]
Description=task_ambi app (FastAPI + NiceGUI)
After=network.target

[Service]
Type=simple
WorkingDirectory=<repo>
ExecStart=<uv の絶対パス> run python -m ui.main
Restart=on-failure
# EnvironmentFile= は要らない（app 自身が <repo>/.env を読む）

[Install]
WantedBy=default.target
```

要点:

- **ui ユニットは要らない**（`016` で廃止した）。`EnvironmentFile=` はどのユニットでも不要である
  —— 待受ポートを起動引数で受ける仕組みが無くなり、`.env` は app 自身が読む。
- `ExecStart` は絶対パス必須（systemd 下の PATH は最小）。`uv` の所在は `command -v uv` で確認する。`BD_BIN` も同じ理由で絶対パス推奨。
- 再起動は systemd（`Restart=on-failure`）に委ねる。アプリ内に自前の復旧機構は作らない。
- ログは stdout への構造化ログ。`journalctl -u <ユニット名>` で読む。
- システムユニットなら `WantedBy=multi-user.target`、ユーザユニット（`~/.config/systemd/user/`）なら `default.target`。
- 待受を localhost に限るのは `.env` の `APP_HOST=127.0.0.1`（既定）で行う。本システムは認証・認可を対象外としており localhost bind が前提である。
- **ユニット名・`User=`・WSL の systemd 有効化・Windows 起動時の常駐化・各パスの実値は、リポジトリ外の「環境情報ファイル」に置く**（環境依存の事実を commit されるファイルに書かないため）。

---

## 7. 動作確認

```bash
curl -s http://127.0.0.1:8000/health
```

`status` と、**実際に組み立てられたモジュールの一覧**が返れば配線は成功している。`enabled_modules` に載せたモジュールが一覧に出ているかを見る。この経路は外部ツールにも設定にも触れない。

画面は `http://127.0.0.1:8000/ui/` を開き、`/ui/settings-ai`（AI 設定）・`/ui/settings-projects`（プロジェクト）・`/ui/triggers`（トリガー）の 3 ページが出ることを確認する（`enabled_modules` から外したモジュールのページは出ない）。

kickoff（`POST /kickoffs`）は実際に LLM を呼ぶので、動作確認の目的では流さない。

---

## 8. 既知の制限

- **認証・認可は対象外。** localhost bind が前提（プロトタイプ用途）。
- **`createCommand`・`deleteAgent`・`deleteCommand` は HTTP 経由で常に 422 になる**（検証規則の相互ロック。B-40／B-49／B-51・未裁定）。コマンドの新規登録・削除とメンバ削除は、当面 **領域 A の直接編集**で行う。
- **画面（HTTP）から領域 A の YAML を保存すると、ファイル内のコメントは消える。** 値は保たれ、複数行の文字列は `|` のブロック書式で書かれる（D26）。コメントを残したいファイルは直接編集する（直接編集は再起動まで反映されない）。
- **repo 系ツールは人の作業ツリーへ直接書く。** commit・push は人が行う（D25）。kickoff の前に人の編集を commit しておき、終わったら `git diff` で確かめてから commit する。
- **LLM サーバ（ローカル llama-server 等）の運用は本システムの外。** 起動・停止・モデル配置は別途行う。

---

## 9. 起動に失敗したら

`create_app()` は `uvicorn.run` より**前**に実行される。したがって「待受が始まらないまま例外で終了する」＝設定または配線の不備であり、アプリの不具合ではないことが多い。例外の本文には**欠けたキー名と、読みに行った絶対パス**が載る。

| 例外・症状 | 原因 |
|---|---|
| `BoardBinaryError` | `bd` が PATH / `BD_BIN` に無い、または実行権が無い |
| `BoardWorkspaceError` | `BEADS_DIR` が `.beads` 自身でない（直下に `config.yaml` が無い） |
| `VaultRootError` | `VAULT_ROOT` が不在、またはディレクトリでない |
| `SettingsRootError` / `TriggerRootError` | ルートが不在・ディレクトリでない・**相対パス**（この 2 つは絶対パス必須） |
| `*EnvError` | `.env` に当該キーが無い／読めない |
| `ConfigurationError`（未知キー） | `config.json` は `extra="forbid"`。**環境変数での上書きは構造的に不可能**なので、値は `config.json` 自体を直す |
| `UnsatisfiedContractError` | `enabled_modules` から依存先を外した（例: `board` を外すと `settings`・`monitor` が充足できない） |
| 画面は出るが操作がすべて「app に接続できません」になる | `APP_BASE_URL`（無ければ `APP_HOST`／`APP_PORT` からの導出値）で自プロセスに届いていない。`APP_HOST=0.0.0.0` のときは `APP_BASE_URL=http://127.0.0.1:8000` を明示する（§5） |

---

## 10. 開発（ゲート）

正本は [.gitlab-ci.yml](.gitlab-ci.yml)（GitLab CI 用・1 ゲート＝1 ジョブ。GitHub では実行されない）。手元では **1 コマンドずつ**実行する — `&&` で連ねると最初の失敗で後続が隠れる。

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run pytest
```

`bd` を持たない環境で土台の検証だけを回す場合:

```bash
uv run pytest -m "not requires_bd"
```

Ruff・pyright の設定はルート [pyproject.toml](pyproject.toml) に一元化してある（モジュール側には置かない）。pre-commit は採用していない（`uv.lock` が固定した版との二重管理を避けるため）。

---

## 11. ドキュメント

| 文書 | いつ見るか |
|---|---|
| docs/manual/設定値と外部仕様.md（非公開） | 設定値の詳細、領域 A・B の**各ファイルの形式**、HTTP API 53 操作の一覧、外部ツールとの公約 |
| [.env.example](.env.example) | `.env` を書くとき（注釈つきの雛形。誤配置の注意も） |
| .specify/memory/constitution.md（非公開） | 設計原則・モジュール境界・対象外要件の裁定 |
| CLAUDE.md（非公開） | AI エージェント（Claude Code）向けの作業規約 |
| [docs/design/](docs/design/README.md) | 設計の経緯（設計工程の記録。機能ごとの仕様 `specs/` は非公開・§12） |

---

## 12. 公開版について

このリポジトリは、開発用リポジトリから実行に要るものと設計の記録を切り出した公開版である。

- **名前**: 正式名称は `task_ambi_v4`。パッケージ名は `task-ambi-v4`、画面と `/health` に出るアプリ名（`config.json` の `app_name`）は `task_ambi`。
- **含めていないもの**: 作者の運用データ（領域 A・B の実ファイル）、起票用のスキル、手元用の `.envrc`。領域 A・B のルートと beads ワークスペースは §2-3 のとおりリポジトリの外に用意する（ルートの中のファイルは起動時に雛形が作られる）。
- **CI**: `.gitlab-ci.yml` は GitLab CI 用で、GitHub では実行されない（§10）。
- **設計文書の値**: `docs/design/` は設計当時の記録で、既定モデル（`qwen/qwen3.7-plus`）やローカル LLM のポート（`11434`）などは現在の値と違うことがある。現在の値はコードと各 `config.json` が正（ローカル LLM の既定プリセットは §2-4 の `http://127.0.0.1:8080/v1`）。設計記録の扱いは [docs/design/README.md](docs/design/README.md) を参照。
- **コミットハッシュ**: 文書やコメントに出てくるコミットハッシュは開発用リポジトリのもので、この公開リポジトリには無い。
- **ID と節番号**: 文中の `FR-xxx`・`SC-xxx`・`T-xxx`・`R-nn` などは非公開の仕様書や設計メモの ID。`§n` は本書の節のほか、憲章や設計メモの節番号のこともある。
- **公開していない参照先**: コード・テスト・文書には、次の文書への参照が残っている。

| 参照の書き方 | 中身 |
|---|---|
| `docs/initial_memo/` | 設計メモ本体（要求 `R-nn`・受け入れ条件・用語集・付録）。`docs/design/phase1/06_acceptance_criteria.md` が指す受け入れ条件の本文もここにある |
| `specs/` | 機能ごとの仕様・計画・タスク・検証手順（`FR-xxx`・`SC-xxx`・`T-xxx` はここの ID） |
| `docs/impl/` | 実装フェーズの設計判断記録（D21 以降） |
| `docs/manual/` | 運用手順書と設定値の詳細 |
| `pocs/` | 設計の前に作った PoC |
| `_archive/`・`outcome/` | 過去の判断記録と作業メモ（`outcome/design/` は現在の `docs/design/` の旧称） |
| `CLAUDE.md`・`.specify/memory/constitution.md` | AI エージェント向けの作業規約と、憲章（設計原則） |

---

## 13. ライセンス

MIT License。全文は [LICENSE](LICENSE)。

---

最終確認: 2026-09-20（NiceGUI 化＝ `016` の追随確認: 2026-10-03）
