# x2 クラスタ 05 — trigger_fire: トリガー発火と KickoffRunner 境界（トリガー）

**位置付け**: 付2 統合テストのクラスタ文書。規則は `x2_notes.md`、裁定は D14。機械検査の正は `x2_cluster_index.csv` の行 05。
**並び順の根拠**: 外部依存（p6 csv の機械計上）6 件（EIF-04/06/07/10/11/12。EIF-11 は orchestrator が居るための計上で実際には通らない）＝ 5 クラスタ中最多。
**範囲外（2 MOD ＜ 下限 3）の理由**: trigger の Protocol 境界は IF-49 `KickoffRunner.run` の 1 本のみで、他は全て外部実体（領域 B・履歴 csv・DL フォルダ・タイマ・HTTP 入口）。board を足しても 04 の claim 経路（IF-06/10）の再検証にしかならない（D14-d）。
**置き場（実装時）**: `apps/api/tests/integration/cluster_05_trigger_fire/`

## 1. 対象モジュールと選定理由

| MOD | クラスタ内での役割 | 実物／スタブ |
|---|---|---|
| trigger | `lifespan`（領域 B 読取→全ジョブ再登録→watcher 開始）・`service.py`（timer／file／manual／hooks の全入口を `fire()` 1 経路に合流 P5D-041）・`lib:apscheduler`（BackgroundScheduler＋MemoryJobStore）・`lib:watchdog`（PollingObserver＋完了検知 X7）・`repository.py`（triggers.yaml・履歴 csv・watcher）・`router.py`（`/triggers`・`/history`・`/watcher`・`/hooks/file`） | 実物 |
| orchestrator | `KickoffRunner.run(ExecutionContext) -> KickoffResult` の実装（single-flight・回収・claim の経路。6 依存はスタブ） | 実物（依存は全部スタブ） |

**選定理由**: 「境界をまたぐ結合」として最小完全な組。trigger 側は外部実体が最多（6）で、APScheduler・watchdog・履歴 csv の実物と `KickoffRunner` の同期契約（ブロッキング・スレッド化は呼び側）が噛み合うことを見る。p2 `p2_sequence_UC-11.mmd`（タイマ／ファイル投下／自分／トリガー／実行履歴／外側ループ／回収／beads）のうち beads は orchestrator のスタブ越しに消える（実 bd の claim はクラスタ 04）。

## 2. 検証する境界（IF-*）

### 2-1. 主検証（1 件）

| IF | 提供→利用 | Protocol.method | 本クラスタで見る点 |
|---|---|---|---|
| IF-49 | orchestrator→trigger | `KickoffRunner.run(ExecutionContext) -> KickoffResult` | `ExecutionContext`（起動種別 timer／file／manual／human・trigger_snapshot・dropped_path・ClaimFilter）が渡り、`KickoffResult`（status・kickoff_id・issue_id・I 級付記）が履歴に写る。`KickoffBusy`（single-flight 中）。同期ブロッキング・スレッド化は呼び側（trigger のワーカースレッド） |

### 2-2. 通過

なし（orchestrator の 6 依存は全部スタブなので IF-01〜48 は数えない）。

## 3. テスト対象のユースケース

- **UC-09 トリガーを CRUD・有効化・手動実行する**: `/triggers` の CRUD・validate・enable／disable・`POST /triggers/{id}/run`（202）
- **UC-10 トリガー履歴・watcher 生死を確認する**: `GET /history`（月指定。status=started の行は未終了）・`GET /watcher`（alive・scheduler 状態。DM-25）
- **UC-11 無人でタスク遂行を起動する（発火側）**: timer／file／manual／hooks → `fire()` → `KickoffRunner.run` → 履歴の開始行追記→終了時書換え

## 4. 必要なテストダブル

| 種別 | 対象 | 形 |
|---|---|---|
| ①外部模擬 | なし | — |
| ②HTTP 入口代替 | **EXT-10 apps/ui（EIF-10）**・**EXT-12 Windows 側 PowerShell FSW（EIF-12）** | TestClient で `/triggers`・`/triggers/{trigger_id}`・`/triggers/{trigger_id}/run`・`/history`・`/watcher`（EXT-10 の代わり）、`POST /hooks/file`（EXT-12 の代わり。`openapi.yaml` が正）。Streamlit の `trigger.ui` は UI ロジック層（AppTest）で別 |
| ③Protocol スタブ（9） | orchestrator の 6 依存: `BoardReader`（list_working＝[]・open_question_of＝None・children_of＝[]）・`BoardWriter`（claim_next＝None）・`SettingsReader`（get_tasks 含む D16）・`MemberResolver`・`VaultReader`・`RunMonitor`・`StageCatalog`・`ProposalGate`・`CrewExecutor`（claim が None なので呼ばれない。型だけ埋める） | override で注入（D14-e） |

**実物で用意する外部**: EIF-06 領域 B（一時 `TRIGGER_ROOT/triggers.yaml`。不在なら既定トリガーだけの雛形 trigger-F13）／EIF-07 実行履歴 csv（`TRIGGER_ROOT/history/history_YYYYMM.csv`）／EIF-04 DL フォルダ（一時 dir を watch_dir に。PollingObserver 実物・短い poll 間隔と `stable_seconds`）／APScheduler 実物（短い interval）／EIF-08 .env（`TRIGGER_ROOT`）／EIF-09 config.json（`trigger/config.json`: poll 間隔・stable_seconds・watcher_enabled）／EIF-13 構造化ログ。EIF-11 は機械計上のみ。

## 5. 前提条件と実行順序上の依存クラスタ

- **配線**: 実 composition root の build 連鎖。有効 MOD＝orchestrator・trigger（他 6 MOD は有効 MOD 一覧から外す）。orchestrator が要する 9 Protocol を override でスタブ注入。trigger の `lifespan` を TestClient の `with` で起動する（D11-f・D14-e）
- **前提**: 付1 完了（設計を直す裁定 0）・憲章への PATCH-7〜11＋C-5 はユーザ適用待ち（D14-a/h）／`KickoffRunner.run` の実装がクラスタ 04 で検証済み／PollingObserver の検知遅延・CPU（R12-P4）は M2 実測（不合格なら `watcher_enabled=false` で §6-4 は §6-5（hooks）のみに縮退 P5D-042）／BackgroundScheduler が FastAPI のイベントループを塞がない（P5D-041 実測）
- **依存クラスタ**: **04**。実行順は 02 → 03 → 01 → 04 → **05**（最後）
- **既知の統合テスト未実施項目**（本クラスタが拾う）: PollingObserver と書き込み完了検知の組合せ（`05_function_memo.md:100`・`03_activity_trigger.mmd:38`・trigger notes §7-1）

## 6. 検証観点（p5 の alt 分岐から）

| # | 観点 | 期待 | 出典 |
|---|---|---|---|
| 1 | lifespan 起動 | triggers.yaml を読み**全ジョブを再登録**（宣言が正・jobstore はインメモリ D07-a）→ watcher 開始。不在なら既定トリガーだけの雛形を生成。YAML 不正／`TRIGGER_ROOT` 不正 → 起動中断（trigger-F13・F01） | trigger 主図 |
| 2 | timer 発火（短 interval） | `fire(kind=timer)` → `ExecutionContext(trigger_snapshot, ClaimFilter)` → `KickoffRunner.run` → `KickoffResult(no_work)` → 履歴: 開始時に run_id 付きの行を追記 → 終了時に同じ行を全列埋めて一時ファイル→`os.replace`（P5D-039/043。trigger 内ロックで直列化） | trigger 主図／EIF-07 |
| 3 | manual: `POST /triggers/{id}/run` | 202（run_id）→ ワーカースレッドで `fire(kind=manual)` → 履歴行。不明 id は 404 | openapi |
| 4 | file: DL フォルダに投下 | `on_created/on_modified` → サイズ・mtime が `stable_seconds` 安定（X7）→ `fire(kind=file, dropped_path)`。投下ファイルは**消費しない**（D06-h）。安定しない（書き続ける）→ 発火しない（構造化ログ） | trigger 主図／EIF-04 |
| 5 | hooks: `POST /hooks/file {path}` | 同じ完了検知 → `fire(kind=file)` に合流（P5D-042）。本文不正 → 422／watch 対象外のパス → 422 | trigger ui 副図／EIF-12 |
| 6 | 二重発火（timer と manual が重なる） | 2 本目は `KickoffBusy` → `finalize_history(status=busy)`（kickoff は止めない） | trigger 主図 `alt KickoffBusy` |
| 7 | UC-09 validate | cron 5 フィールド／interval ≥ min／watch_dir 絶対パス／既定トリガー削除不可 → 422。保存 → triggers.yaml を一時ファイル→`os.replace` → 再登録。enable／disable が scheduler に反映 | trigger ui 副図 `alt 違反` |
| 8 | UC-10 表示 | `GET /history` は csv を**表示用にだけ**読む（ロジックは読み戻さない 憲章 §2）。`GET /watcher` が alive・scheduler 状態を返す | EIF-07／DM-25 |
| 9 | watcher スレッド死亡 | `WatcherState.alive=False` を `GET /watcher` で見せるのみ（自動復旧なし 憲章 §10） | EIF-04 失敗時 |
| 10 | 履歴書込失敗（history ディレクトリを読み取り専用に） | 開始行の追記失敗 → 構造化ログのみ（kickoff は止めない）／終了時の書換え失敗 → 旧ファイルが残りログに確定値 | trigger-F02／EIF-07 |
| 11 | shutdown | `shutdown(wait=True)` で進行中 kickoff は完走。（プロセス死の場合は status=started の行が残り「終わっていない」と分かる＝観察のみ） | trigger 主図 Note |
| 12 | 構造化ログ | 非 HTTP 入口（timer／file）で correlation_id を発番し contextvar で `KickoffRunner.run` 内まで貫通（憲章 §6「HTTP を通らない入口でも同じ ID をその入口で発番」） | EIF-13 |
| 13 | R12-P4 不合格時 | `watcher_enabled=false` → §6-4 は見ず §6-5（hooks）のみ | P5D-042 |
