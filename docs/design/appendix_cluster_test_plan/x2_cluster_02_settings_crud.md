# x2 クラスタ 02 — settings_crud: 設定・プロジェクトの CRUD と削除ガード（設定管理）

**位置付け**: 付2 統合テストのクラスタ文書。規則は `x2_notes.md`、裁定は D14。機械検査の正は `x2_cluster_index.csv` の行 02。
**並び順の根拠**: 外部依存（p6 csv の機械計上）4 件（EIF-01/02/05/10）。テストダブル 0（スタブも外部模擬も無し）＝最も実物に近いクラスタ。**実行順では最初**（§5）。
**置き場（実装時）**: `apps/api/tests/integration/cluster_02_settings_crud/`

## 1. 対象モジュールと選定理由

| MOD | クラスタ内での役割 | 実物／スタブ |
|---|---|---|
| settings | `router.py`（`/settings/*`）→ `service.py`（validate 順方向・逆方向・cmd⊆定義・解決可能性）→ `repository.py`（領域 A の YAML／csv／md。一時ファイル→`os.replace`）→ スナップショット差替え（世代付き P5D-016）→ 台帳再導出・casbin Enforcer 再構築 | 実物 |
| vault | `VaultReader.check_project_folders`（UC-08 登録・編集時のフォルダ存在確認 IF-32） | 実物（一時 dir を `VAULT_ROOT` に） |
| board | `BoardReader.has_open_issues`（UC-08 削除ガード IF-09） | 実物（一時 dir に `bd init`） |

**選定理由**: UC-08（プロジェクト CRUD）は settings が vault（存在確認）と board（削除ガード）を同期で呼ぶ唯一の設定系境界で、p5 `p5_settings_sequence_uc08_project.mmd` のレーン（自分／ui／router／service／VaultReader／BoardReader／repository／領域 A）がそのまま本クラスタになる。UC-07（AI 設定 CRUD）は settings 単独だが、同じ router＋TestClient＋領域 A 実物の経路で「validate→保存→台帳再導出→スナップショット差替え」を通すため同居させる（憲章 §8「API 層＋外部ツール実物」の最小形）。settings が**提供**する 9 IF（IF-23〜30・IF-50）の主検証は利用側のクラスタ（03・04）。

## 2. 検証する境界（IF-*）

### 2-1. 主検証（2 件）

| IF | 提供→利用 | Protocol.method | 本クラスタで見る点 |
|---|---|---|---|
| IF-32 | vault→settings | `VaultReader.check_project_folders(ProjectFolders) -> FolderCheck` | 登録・編集時。不在一覧が 422 の problems に載る（beads には書かない D07-g） |
| IF-09 | board→settings | `BoardReader.has_open_issues(project_key) -> bool` | 削除ガード。`project:<key>` の open issue があれば拒否。**読めない（`BoardUnavailable`）ときも拒否側**（D06-j(4)） |

### 2-2. 通過

なし（settings の提供 IF-23〜30・IF-50 は本クラスタでは router 経由の CRUD だけで、Protocol としては呼ばれない）。

## 3. テスト対象のユースケース

- **UC-07 AI 設定を CRUD する**: agents（メンバ・ペルソナ既定）／tasks（段テンプレート）／llm-profiles／limits／commands／policies／rules／hooks／humans の一覧・保存・削除（D16）（validate → 領域 A → 台帳再導出 → Enforcer 再構築 → 世代＋1）。反映は次回 kickoff から
- **UC-08 プロジェクトを CRUD する**: 登録（キー規則→フォルダ存在確認→保存）／編集（新パス不在→422）／削除（削除ガード→`_archive` 退避）。kickoff 時の実行時存在検証（D04-d）は orchestrator 側＝クラスタ 04

## 4. 必要なテストダブル

| 種別 | 対象 | 形 |
|---|---|---|
| ①外部模擬 | なし | — |
| ②HTTP 入口代替 | **EXT-10 apps/ui（EIF-10）** | FastAPI TestClient で `/settings/projects`・`/settings/projects/{project_key}`・`/settings/agents`・`/settings/agents/global/{persona_key}`・`/settings/agents/{project_key}/{persona_key}`・`/settings/tasks/global`・`/settings/tasks/{project_key}`・`/settings/llm-profiles`・`/settings/llm-profiles/{name}`・`/settings/limits`・`/settings/limits/{project_key}`・`/settings/commands`・`/settings/commands/{name}`・`/settings/policies/{project_key}`・`/settings/rules/projects/{project_key}`・`/settings/hooks/{project_key}`・`/settings/humans`（`openapi.yaml` が正）。Streamlit の `settings.ui` は憲章 §8 の UI ロジック層（AppTest）で別 |
| ③Protocol スタブ | なし | — |

**実物で用意する外部**: EIF-05 設定ファイル領域 A（一時 `SETTINGS_ROOT`。不在なら `global/` の雛形＝model.conf・defaults/{agents.yaml, tasks.yaml} のコピーが生成される settings-F23・D16-g）／EIF-02 Obsidian vault（一時 `VAULT_ROOT` に PJ 入力・役割フォルダ・`outcome/` を作る。役割フォルダはメンバのペルソナ全員分 P5D-018）／EIF-01 beads（一時 dir に `bd init`。削除ガード用に `project:<key>` ラベルの open issue を bd で仕込む）／EIF-08 .env（`VAULT_ROOT`・`SETTINGS_ROOT`）／EIF-09 config.json／EIF-13 構造化ログ。

## 5. 前提条件と実行順序上の依存クラスタ

- **配線**: 実 composition root の build 連鎖。有効 MOD＝board・vault・settings（monitor 以降は有効 MOD 一覧から外す。未充足 Protocol は無いので override 不要）（D14-e）
- **前提**: 付1 完了（設計を直す裁定 0）・憲章への PATCH-7〜11＋C-5 はユーザ適用待ち（D14-a/h）／bd バイナリが PATH にある（CI も）／`openapi.yaml` の paths・応答コード（422 validate／404／409 削除ガード）が正
- **依存クラスタ**: なし。**実行順は最初**（02 → 03 → 01 → 04 → 05）。本クラスタで作る領域 A のフィクスチャ（projects/<key>/・global/）は 03・04 が再利用する
- **build 失敗の観点**（§6-8）は app の起動そのものを失敗させるため、他の観点とは別プロセス（別 fixture）で行う

## 6. 検証観点（p5 の alt 分岐から）

| # | 観点 | 期待 | 出典 |
|---|---|---|---|
| 1 | UC-08 登録: キー規則違反／重複 | 422・**何も書かない**（P1 ゲート） | uc08 副図 `alt キー規則違反／重複` |
| 2 | UC-08 登録: フォルダ不在（IF-32） | 422・不在パス一覧が problems に・**beads には書かない**（D07-g） | `alt missing あり` |
| 3 | UC-08 登録: 正常 | `projects/<key>/project.yaml` が一時ファイル→`os.replace` で書かれ、スナップショット世代＋1、GET で読める | uc08 副図 |
| 4 | UC-08 編集: 新パス不在 | 422（既存は不変） | `alt 新パス不在` |
| 5 | UC-08 削除: open issue あり（IF-09） | **409**（openapi。現在状態との衝突。D12 §4 (2)） | `alt open issue あり` |
| 6 | UC-08 削除: `BoardUnavailable`（`bd_bin` を失敗するラッパに差し替え） | 拒否側（409）。読めないときに消さない（D06-j(4)） | 同上 `または BoardUnavailable` |
| 7 | UC-08 削除: `_program` 予約キー | 422 削除不可 | `alt key が予約` |
| 8 | UC-08 削除: 正常 | `_archive` へ退避（消さない）・スナップショット世代＋1 | EIF-05 |
| 9 | UC-07 保存: validate 違反（メンバ⊆policy／policy⊆台帳／cmd⊆定義／解決可能性／agents.yaml・tasks.yaml の許可フィールド・禁止キー・PersonaKey／llm／tools 参照・{変数}検査 D16） | `SettingsValidationError(problems[])` → 422・**何も書かない** | uc07 副図 `alt 違反あり` |
| 10 | UC-07 保存: 通過 | 領域 A 書込 → 台帳（DM-07）再導出 → Enforcer 再構築 → 世代＋1 のスナップショット差替え | uc07 副図 `else 通過` |
| 11 | UC-07: メンバのペルソナ全員分の role_dir が無い | 422（P5D-018。vault-F03 の `VaultNotFound(role)` は通常起きない整合違反として残る） | P5D-018 |
| 12 | `/settings/humans`（人間の bd actor 宣言 P5D-019） | CRUD できる（F21 の列挙漏れを openapi で補った D12 §4） | EIF-10 |
| 13 | build: `SETTINGS_ROOT` 不在 | `global/` の雛形を生成して起動 | settings-F23 |
| 14 | build: 構文誤り／validate 違反のファイルがある | 起動中断（例外。黙って既定にしない） | settings-F23／憲章 §5 |
| 15 | 実行時の読取はスナップショット | 保存後に領域 A のファイルを壊しても GET（一覧）は壊れない（P5D-016。次の保存で validate に落ちる） | P5D-016 |
| 16 | 構造化ログ | HTTP 入口の middleware で発番した correlation_id が settings の保存ログ・bd 呼び出し監査ログに貫通（憲章 §6） | EIF-13 |
| 17 | UC-08 登録・編集: リポジトリ対応付け（任意） | `repos` 付き登録 → GET で読める → 編集で更新 → 解除で `repos` キー消滅。未登録ならキー自体を書かない | CFG-74・D24 |
| 18 | UC-08 登録: `repos` の形式違反 | 422（URL・絶対パスの形式のみの緩い検査）・**何も書かない**（P1 ゲート） | CFG-74・D24 |
