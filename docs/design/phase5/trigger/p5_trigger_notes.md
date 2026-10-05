# trigger（BC-06 トリガー）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_trigger_sequence.mmd`（主図: lifespan・発火・履歴）／`p5_trigger_sequence_ui.mmd`（副図: CRUD・手動実行・履歴表示・退避経路）／`p5_trigger_data_model.mmd`／`p5_trigger_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-039〜042。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-49（利用 1 行）／p4 notes §1・§4／`../../phase2/p2_domain_model.mmd` DM-22〜25／`p2_sequence_UC-11.mmd`／`docs/initial_memo/付録：トリガー実行機能の設計.md`（DEC-012: app プロセス内の MOD として読み替え）／D04-c・D06-h,j・D07-a,b・D09-e／PoC なし（新規。付録の構想のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

trigger は **常駐機能（APScheduler＋watchdog）と REST・UI** を持つ MOD で、使う Protocol は `KickoffRunner` だけ。timer／file／manual／hooks の全入口を `fire()` 1 経路に合流させ、correlation_id を発番して `ExecutionContext` を組み `KickoffRunner.run` を同期で呼び、`KickoffResult` を履歴 csv に 1 行追記する。定義は領域 B の `triggers.yaml`（宣言が正・起動時再登録・インメモリ jobstore）、履歴は `history_YYYYMM.csv`（開始時に行を追記し終了時に同じ行を全列埋めて書き換える上書き方式 P5D-043。UI 表示にのみ読む）。flock・別プロセス（付録の旧構成）は持たない（DEC-012）。常駐の起動停止は `TriggerHandle.lifespan` を apps/api が束ねる（D11-f）。

## 1. ファイル構成（憲章 §3）

```
packages/features/trigger/
├── pyproject.toml        # apscheduler・watchdog 依存はここだけ（linter (3)）。streamlit も feature 側の main dependencies に版固定で置く（010 の現物に倣う）
├── config.json           # TriggerConfig: ready_interval_default_sec・min_interval_sec・misfire_grace_sec・poll_interval_sec・stable_seconds・watcher_enabled（＋timezone D15-m・P5-R1 転記）
├── SKILL.md              # jobstore は本プロセスだけが触る／追い発火を持たない／履歴は読み戻さない／完了検知の閾値と R12-P4 の実測手順
├── src/trigger/
│   ├── __init__.py       # build(deps: TriggerDeps, *, config=None) -> TriggerHandle（router・lifespan）。TriggerEnv・TriggerConfig もここ
│   ├── router.py         # /triggers CRUD・/triggers/{id}/run・/history・/watcher・/hooks/file
│   ├── ui.py             # Streamlit: トリガーページ（UC-09/10）。HTTP のみ、trigger.* を 1 つも import しない
│   ├── schemas.py        # HTTP DTO（TriggerSpec／TriggerView／HistoryRow／WatcherView／FileHookRequest）
│   ├── correlation.py    # 相関 ID の発番と束縛（api も common も使えないため trigger 内に持つ。011 SD-21）
│   ├── service/          # 憲章 §3 のサブパッケージ分割
│   │   ├── __init__.py   #   TriggerService: start／stop・既定トリガー・fire()・口向けメソッド
│   │   ├── validate.py   #   保存の検査 8 通り（V-1〜V-8）と LaunchCond → ClaimFilter
│   │   ├── jobs.py       #   APScheduler の操作（唯一の apscheduler import 点）
│   │   └── views.py      #   TriggerView／WatcherView の組み立て（定義＋next_fire_at＋last_result）
│   ├── repository/       # 同上（外部実体が 3 つ・寿命も違う）
│   │   ├── __init__.py   #   再エクスポートのみ（振る舞いを置かない）
│   │   ├── definitions.py#   領域 B（triggers.yaml）の読み書き・雛形生成
│   │   ├── history.py    #   履歴 csv（開始行の追記・確定の書換え・直列化）
│   │   ├── watcher.py    #   PollingObserver（唯一の watchdog import 点）
│   │   └── settle.py     #   完了検知（監視から独立した部品。約 1 秒間隔の stat・重複抑止）
│   ├── models.py         # TriggerDef／LaunchCond／RunHistoryRow／WatcherState／FileEvent（内部 VO）
│   ├── dependencies.py   # Depends: service・注入 KickoffRunner の受け口
│   └── exceptions.py     # 内部（TriggerValidationError → 422）
└── tests/                # 単体: KickoffRunner をスタブ、scheduler は実物（短い interval）、watcher は一時 dir／UI は AppTest
```

`build(deps: TriggerDeps) -> TriggerHandle`: `TriggerDeps(kickoff_runner)`。`TriggerHandle(router, lifespan)`。`.env` の `TRIGGER_ROOT`（領域 B）・`DOWNLOADS_DIR`（任意。file トリガーの watch_dir の基準。付3 D15-e）と config.json を読む。

## 2. Protocol 利用と責務の対応

| 相手 | メソッド | fn |
|---|---|---|
| （利用）KickoffRunner.run | IF-49 | F10（fire） |

trigger が持たないもの: 盤面・vault・設定領域 A への一切のアクセス（claim フィルタは ExecutionContext に載せるだけ）、kickoff の同時実行制御（orchestrator の single-flight。busy は履歴に残す）、human 起動（orchestrator の POST /kickoffs。履歴に残らない非対称は D09-e の仕様）、投下ファイルの消費・移動（D06-h）。

## 3. 受け渡し型

trigger は Protocol を提供しないので common/interfaces/trigger.py は**無い**。使う型は orchestrator の `ExecutionContext`・`TriggerSnapshot`・`KickoffResult`・`KickoffBusy`（P5D-038）と board の `ClaimFilter`。

## 4. シーケンス図

| 図 | 対象 |
|---|---|
| `p5_trigger_sequence.mmd`（主図） | lifespan 開始（領域 B → 既定トリガー → add_job → watcher → start）→ 発火 3 入口の合流 → fire（correlation bind → ctx → KickoffRunner.run → 履歴追記。busy も 1 行）→ lifespan 終了 |
| `p5_trigger_sequence_ui.mmd` | UC-09 CRUD・トグル（validate → 領域 B → add/modify/remove_job → watcher）・手動実行 202／UC-10 一覧・watcher・履歴（月）／退避経路 POST /hooks/file |

レーン: `apps/api`（lifespan の呼び手）／`trigger/service.py`・`router.py`・`ui.py`・`repository.py`・`lib:apscheduler`・`lib:watchdog`／`設定ファイル領域 B`・`実行履歴 csv`・`DL フォルダ`・`タイマ`・`自分`／`KickoffRunner`。

## 5. 付録（PoC 段階の構想）からの読み替え

| 項目 | 付録 | V4 |
|---|---|---|
| プロセス | trigger-daemon＋trigger-ui の 2 プロセス・flock | app ユニット内の 1 MOD（DEC-012）。UI は apps/ui が束ねる。flock なし |
| scheduler | AsyncIOScheduler＋SQLAlchemyJobStore → D07-a でインメモリ | BackgroundScheduler＋MemoryJobStore（同期契約を呼ぶ。P5D-041＝**実測で確定**。イベントループを塞がない） |
| 履歴 | SQLite 別テーブル → D07-b で csv | history_YYYYMM.csv 15 列（run_id 追加）。開始行＋終了時書換え（P5D-039/043） |
| 完了検知 | 移植元が存在しない（**011 で確認**） | repository/settle.py に**新規実装**（P5D-042 の「既存部品を移植」は実行不能＝ 011 SD-06）。R12-P4 の実測は合格で、poll 間隔は 5.0 秒に確定 |
| webhook 受け口 | 退避経路として必須 | POST /hooks/file を最初から用意（同じ fire に合流） |
| 表示用メタ | 設定ファイルが正 | triggers.yaml（領域 B）。next_fire_at・running・生死はインメモリ（DM-25）を REST 越しに |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py trigger --render` → ERRORS: none
- participant は Protocol 名 1・内部 6・外部 6 のみ
- 利用 1 行（IF-49）が feature_list に現れる

## 7. 未決だったもの（**5 件は 011 の実測で閉じた**。残る 1 件だけが未決）

1. **PollingObserver の検知遅延と CPU 負荷**（R12-P4）: **閉じた**。`/mnt/c`（9p・aname=drvfs）の実測で取りこぼし 0・検知遅延は poll 間隔で決定的に上限が付き、既存 200 件・poll 5.0 秒で CPU 0.597 %。既定は **`poll_interval_sec` = 5.0**・**`stable_seconds` = 2**・**`watcher_enabled` = true**（縮退しない）
2. **BackgroundScheduler vs AsyncIOScheduler**（P5D-041）: **閉じた**。`BackgroundScheduler` のままでよい（専用スレッドで回るので FastAPI のイベントループを塞がない。停止時の `shutdown(wait=True)` だけは同期で塞ぐ＝実測 0.951 秒で、これは受け入れる）
3. **撤退状態の除外規則**（R12-P2・UC-06 据え置き）: **未決のまま**。P5D-034 の「claim 直後に state:failing なら触らない」で実質的に代替。解除条件の再評価は実タスク投入時
4. **既定トリガーの初期間隔**（config.json の値）: **閉じた**。`ready_interval_default_sec` = **300**（秒）。コードに直書きせず設定から採る
5. **履歴 csv の文字コード・改行**: **閉じた**。UTF-8（**BOM なし**）・改行 **`\n`**（`csv.writer(..., lineterminator="\n")` を明示する。既定は `\r\n`）
6. **上書き方式と憲章 §2 例外 (b) の文言**（P5D-043）: **閉じた**。憲章の現物 §2 は既に「1 実行の開始時に書いた行を終了時に確定値で埋める書換えは許す（同じ所有モジュールが、自分の書いた行に限って行う）。システムのロジック（判定・分岐）はこのログを読み戻さず、表示にのみ読む」を含む（PATCH-6 適用後の文言）。P5D-043 はそのまま適合するので**再裁定は不要**
