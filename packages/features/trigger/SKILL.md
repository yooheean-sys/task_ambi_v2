# SKILL: trigger — 常駐（スケジューラ・ファイル監視）と発火の 1 経路・実行履歴・トリガー UI

**設計の正本**: `docs/design/phase5/trigger/`（notes・data_model・sequence・sequence_ui・feature_list）／
`docs/design/phase6/openapi.yaml`（HTTP 境界）／`specs/011-trigger/`（spec・plan・contracts）。
食い違ったら **現物コード > 報告書 > 設計メモ**（CLAUDE.md）。

---

## 責務と持たないもの

**持つもの**:

- 常駐（APScheduler の `BackgroundScheduler` ＋ watchdog の `PollingObserver`）の起動停止
- **発火の 1 経路**（`timer`／`file`／`manual`／`hooks` の 4 入口を `TriggerService.fire()` に合流）
- 実行履歴 csv（**1 実行 ＝ 1 行**。開始行の追記 ＋ 終了時の同一行書換え）
- 領域 B の宣言ファイル `triggers.yaml`（**宣言が正**）の読み書き
- HTTP の口 6 パス・10 操作 と トリガー画面 1 枚

**持たないもの**:

| 持たない | どこが持つか |
|---|---|
| 盤面（beads）・vault・設定領域 A への一切のアクセス | それぞれの所有モジュール。trigger は `claim_filter` を `ExecutionContext` に載せるだけ |
| kickoff の同時実行制御 | orchestrator の single-flight（`KickoffBusy` は**履歴に 1 行**として残す） |
| `human` 起動の履歴 | orchestrator の `POST /kickoffs`。**trigger を通らないので履歴に残らない**（D09-e の仕様。埋め合わせない） |
| 投下ファイルの消費・移動・削除 | 誰も持たない。**stat で読むだけ**（D06-h） |
| flock・別プロセス | 持たない（DEC-012。単一 systemd ユニット内の MOD） |
| 認証・リトライ・自動復旧 | 持たない（憲章 §10） |

**提供 Protocol は 0 本**（`ModuleRegistration(provides=())`）。利用するのは `KickoffRunner.run`
（IF-49）**1 本だけ**である。

---

## 主要ファイル（fn → ファイル）

| fn | 概要 | 実装ファイル |
|---|---|---|
| **F01** | 領域 B の宣言ファイル | `repository/definitions.py` |
| **F02** | 実行履歴 csv（2 段書込） | `repository/history.py` |
| **F03** | 常駐の起動停止（lifespan） | `service/__init__.py`（`start`／`stop`）＋ `__init__.py`（`_make_lifespan`） |
| **F04** | 既定トリガーの保証 | `service/__init__.py`（`ensure_default`） |
| **F05** | 保存の検査（8 通り） | `service/validate.py` |
| **F06** | `LaunchCond` → `ClaimFilter` | `models.py`（`to_claim_filter`）＋ `service/validate.py`（既定ラベルの充当） |
| **F07** | 時刻発火（APScheduler 操作） | `service/jobs.py` — **唯一の `apscheduler` import 点** |
| **F08** | 監視 ＋ 完了検知 | `repository/watcher.py`（**唯一の `watchdog` import 点**）＋ `repository/settle.py` |
| **F09** | 手動実行 | `service/__init__.py`（`run_manual`） |
| **F10** | 発火の 1 経路 | `service/__init__.py`（`fire`。**7 段**） |
| **F11** | HTTP の口 | `router.py`・`schemas.py`・`dependencies.py`・`service/views.py` |
| **F12** | トリガー画面 | `ui.py`（**`trigger.*` を 1 つも import しない**）。D26 で「トリガーの仕組み」の静的な説明（定義ファイルの所在・発火後の流れ・段テンプレートへのリンク `/settings-ai`）と一覧の「起動条件」列を足した。リンクは経路の文字列だけで、settings を import しない |
| **F13** | 組み立てと起動中断 | `__init__.py`（`build` の段 (a)〜(f)） |

相関 ID の発番は `correlation.py`（`apps/api` の同型の実装とは**別実装**。共通化の置き場が制度的に
無い＝ `011` SD-21）。

---

## 改修時の注意（6 つ）

### 1. jobstore は本プロセスだけが触る

インメモリの `MemoryJobStore` 1 つで、**外から書き換える経路を作らない**（D07-a・FR-021）。
登録内容は起動のたびに `triggers.yaml`（宣言が正）から作り直す。永続 jobstore に替えると
「宣言が正」が壊れる。

### 2. 追い発火を持たない

`coalesce=True`・`max_instances=1`（`service/jobs.py` の `add_job`）。遅れた分をまとめて 1 回に
する＝**取り逃した発火を取り返さない**（憲章 §2・FR-012）。「取り逃したぶんを後で回す」機能を
足さない。溢れたときは APScheduler の警告ログだけが出て**ジョブは残る**。

### 3. 履歴は読み戻さない

読むのは**表示の 2 経路だけ** —— `GET /triggers` の `last_result`（`service/views.py`）と
`GET /history`（`router.py` → `service/__init__.py`）。**分岐・判定に使った瞬間に憲章 §2 違反**に
なる。`test_trigger_history.py` の AST 走査が呼び手と「条件式で使っていないこと」を固定している。

### 4. `fire()` を増やさない

入口が増えても**合流点は 1 つ**（P5D-041）。`fire()` は 7 段（run_id → 相関 ID → 開始時刻 →
`ExecutionContext` → 開始行 → `KickoffRunner.run` → 確定書換え）で、**安全側に倒さない**:

- 開始行が書けなくても kickoff を止めない
- `KickoffBusy` を握りつぶさない（`status="busy"` の 1 行にする）
- 例外でジョブを消さない（`status="stopped"` にして**再送出しない**）
- `BaseException`（`RunHalted`・`KeyboardInterrupt`・`SystemExit`）は**捕まえない**

### 5. 停止列の順を変えない

**scheduler → 専用ワーカー → 監視（observer → 完了検知）の 3 段**（FR-033・SD-26）。逆にすると
**停止中に新しい発火が入る**。あわせて —— **常駐部品は `start()` で毎回作り直す**（SD-23）。
`threading.Thread` は再 `start()` できず `ThreadPoolExecutor` は `shutdown` 後に `submit` できない
ので、`build()` で作って使い回すと**2 回目の `start()` が必ず落ちる**。`build()` はスレッドを
1 本も起こさない。**順だけでなく「3 段とも待ち切る」も変えない** → 下の「停止列 3 段は**どれも
待ち切る**」。

### 6. 変更の前にモジュール全体のトークン数を測る

`016` design-sync B-38 の裁定。数え方は `011` B-63 と同じで、本フォルダ配下の全ファイル
（`__pycache__`・`.pytest_cache`・`.ruff_cache` を除く）を 1 つずつ tiktoken の `o200k_base`（主）と
`cl100k_base` で符号化して足す（使い捨ての `uv run --no-project --with tiktoken` で数え、`.venv`・
`uv.lock` を変えない）。plan の Constitution Check で、憲章 §1 の上限までの残りを確かめる。
**上限の数値はここに書かない**（憲章を正とする）。2026-09-29 の実測は **125,757**（`o200k_base`）／
**137,639**（`cl100k_base`）。どちらも Claude の符号化器ではない。

---

## 完了検知の閾値と R12-P4 の再実測手順

### 現在の閾値

| 値 | 既定 | 出所 |
|---|---|---|
| `poll_interval_sec`（CFG-65） | **5.0** | `PollingObserver(timeout=…)`。実測 M-02・M-08 |
| `stable_seconds`（CFG-66） | **2** | 安定判定の待ち。実測 M-03・M-08 |
| `SETTLE_POLL_SECONDS`（`repository/settle.py` の定数） | **1.0** | 完了検知の `stat` 間隔。**`poll_interval_sec` とは別**（M-03） |
| `watcher_enabled`（CFG-67） | **true** | R12-P4 が合格したので縮退しない |

⚠ `SETTLE_POLL_SECONDS` を `poll_interval_sec` に合わせると、`stable_seconds`（2）が**実質 5 秒へ
丸められる**。config キーにしていないのは、新キーが `x3_config_catalog.csv`（変更禁止）への行新設を
要するためである（plan C-6）。調整の自由度は `stable_seconds` が持つ。

### 再実測の手順（**環境が変わったら回す**）

DL フォルダの件数が 1000 を超えた・WSL のバージョンが上がった・別のドライブへ移した等で
挙動が変わりうる。実測は **`/mnt/c` の一時ディレクトリ**に対して行う（テストは触らない＝ P-2）。

1. **検知遅延と CPU**: `/mnt/c` に一時ディレクトリを作り、既存ファイル 200 件を置く。
   `poll_interval` を **1.0 / 2.0 / 5.0** で `PollingObserver` を回し、
   (a) 投下から `on_created` までの遅延の最大値、(b) 監視スレッドの CPU 使用率、
   (c) 取りこぼしの件数、を採る。
   基準線（2026-08-27 実測）: 遅延の上限は poll 値でほぼ決まる（1.217 / 2.210 / 5.222 秒）。
   取りこぼし **0**。CPU は poll 5.0・200 件で **0.597 %**。
2. **安定判定**: 24 MB を 9 秒かけて分割で書きながら、`stable_seconds` を **1 / 2 / 3** で試し、
   誤発火の件数と検知の遅れを採る。基準線: `stable_seconds=2` で誤発火 **0**・遅れ **+約 0.83 秒**。
3. 結果が悪化したら `config.json` の `poll_interval_sec`・`stable_seconds` を調整する。
   **不合格（取りこぼしが出る）なら `watcher_enabled=false` に落とし、退避経路
   `POST /hooks/file` だけにする**（P5D-042 の縮退。完了検知は `watcher_enabled` と独立に動くので
   そのまま使える）。
4. 実測後は `docs/design/phase5/trigger/p5_trigger_notes.md` §7-1 の値を更新する。

---

## 停止列 3 段は**どれも待ち切る**

停止列は **3 段**あり（順を変えない＝ FR-033・SD-26）、**3 段とも進行中の kickoff を待ち切る**。
発火の入口は 4 つ（`timer`・`file`・`manual`・`hooks`）で、走るスレッドが 3 系統に分かれている
ので、待ちも 3 段に分かれる。

| 段 | 呼ぶもの | 待つ対象の入口 |
|---|---|---|
| **1** | `scheduler.shutdown(wait=True)` | `timer`（APScheduler の executor スレッド） |
| **2** | `worker.shutdown(wait=True)` | `manual`・`hooks`（`max_workers=1` の専用ワーカー） |
| **3** | `settle.stop()` ＝ `_stop.set()` → **timeout なしの `join`** | `file`（完了検知スレッド） |

段 1 は実測 **0.951 秒**（interval 1 秒・所要 3 秒のジョブ）。**同期でイベントループを塞ぐ**が、
`asyncio.to_thread` に逃がしていない（plan C-4）—— 停止中に他の要求を捌く必要が無く、逃がしても
待ち時間は変わらないため。段 2・段 3 も同じ理由で同期のままにしている。

⚠ **段 3 の `join` に timeout を戻さない**（T092 の決定。根拠は `repository/settle.py` の
`stop()` の docstring にもある）。完了検知スレッドは callback の中で `fire()` →
`KickoffRunner.run` を**同期で**回すので、timeout を置くと `file` 入口の**進行中 kickoff を
打ち切って返る**。実運用の kickoff は LLM を回すので分単位であり、既定の
`SETTLE_POLL_SECONDS = 1.0` から導く 15 秒では**常に**踏む。打ち切ると lifespan が閉じて
プロセスが終わった時点で daemon スレッドは実行中のまま消え、履歴の行は `started` のまま残る。
**`timer`・`manual`・`hooks` は待つのに `file` だけ待たない非対称を残さない**のがこの段の要件
である（FR-033 段 3・SC-017・US1 AC6・N-6・contracts/fire-pipeline.md §6）。「停止が遅い」は
timeout を戻す理由にならない —— 遅いのは**段 1・段 2 も同じ**で、下の ⚠ が扱う話に帰着する。

`settle.stop()` は `self._thread` を **`join` が終わってから**落とす。先に落とすと `is_alive()`
がスレッドの走行中に `False` を返し、**打ち切ったことが口（`GET /watcher`）からもログからも
読めなくなる**。

- ⚠ **進行中の kickoff が分単位なら停止もその分待つ**。**uvicorn の graceful shutdown の待ち時間を
  超えると強制終了されうる**（F-17）。その場合は `status=started` の行が履歴に残り、「終わって
  いない run」として画面に出る（US1 AC5）。

---

## 新依存の版とライセンス（憲章 §11 の**暫定記録**）

恒久的な記録先が未整備なので、ここに暫定で置く（`specs/005-settings-read/design-sync.md` の
**B-12** に積み増し済み）。整備されたらここから移す。

| パッケージ | 版 | ライセンス | `py.typed` | 備考 |
|---|---|---|---|---|
| `apscheduler` | **3.11.3** | **MIT** | **無し** | 型スタブ不在の抑制は `service/jobs.py` の import 5 行だけに閉じ、境界の型を `Scheduler`（= `Any`）で止める |
| `watchdog` | **6.0.0** | **Apache-2.0** | あり | **明示宣言する**（`016` で streamlit を外したので、推移依存としての在庫も消えた＝ R-09 の判断が結果的に効いた） |
| `pyyaml` | 6.0.3 | MIT | 無し | settings が既に使っている |
| `fastapi` | 0.141.1 | MIT | あり | HTTP の口 |
| `nicegui` | **3.17.1** | **MIT** | あり | `ui.py` だけが import する（`016` で streamlit から置き換えた） |

**いずれも GPL 系でない**。`httpx`・`requests` は**足さない**（画面は標準ライブラリ
`urllib.request` の `call_api` 1 関数に閉じる＝ `010` SD-06）。

---

## テストコマンド

```bash
uv run pytest packages/features/trigger/tests
uv run ruff check packages/features/trigger
uv run pyright packages/features/trigger
```

全体の 5 ゲートは**1 コマンドずつ**回す（`&&` で連ねると最初の失敗で後続が隠れる）:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports          # → Contracts: 8 kept, 0 broken.
uv run pytest
```

`bd` を要さないので `requires_bd` の印は使わない。scheduler と watcher は**実物**（短い間隔・
一時ディレクトリ）で回し、`KickoffRunner` だけがスタブである。

---

## 触ってはいけないもの

- **実運用の `.env`**（`TRIGGER_ROOT`・`DOWNLOADS_DIR`）。テストは `tests/conftest.py` の autouse
  fixture `_isolate_real_area_b` が `trigger.env_file_path` を一時ファイルへ差し替え、2 つのキーを
  環境変数から外す。**この止め木を消さない・迂回しない。**
  ⚠ 止め木が効くのは `env_file_path()` の module 関数・`TriggerEnv` の明示 `__init__`・`build()` 側の
  `TriggerEnv(_env_file=env_file_path())` の **3 つが対で**在るときだけである（R-13）。どれかを落とすと
  **黙って無効**になり、テストが実運用の領域 B に雛形と履歴を書き、**実運用の Windows Downloads に
  `PollingObserver` が張り付いて投下ファイルで実際に kickoff が起きうる**。
- **`/mnt/c` にテストを書かない**（spec A-05・P-2）。監視のテストは `tmp_path`（ext4）だけを使う。
  観測点は `grep -rn '/mnt/c' packages/features/trigger/src/ packages/features/trigger/tests/` が
  **0 行**であること（本ファイルは走査対象外 —— 上の再実測手順が地名を明記するため）。
- **投下ファイル**（DL フォルダ配下）。stat で読むだけで、書かない・動かさない・消さない（D06-h）。
- **`packages/common/`**（trigger は Protocol を提供しないので足すものが無い）。
- **`apps/api` の `registry.py` 以外**・**`apps/ui` の `pages.py` 以外**。

**`016` で足した NiceGUI とその推移依存（暫定の記録先。research R-16）**: 憲章 §11 のライセンス
互換の成果物が不在（`005` B-12）なので、`010` SD-11 と同じ扱いでここに記す。追加は **12 件**で、
**いずれも GPL 系ではない**。

| パッケージ | 版 | 直接／推移 | ライセンス | 判断 |
|---|---|---|---|---|
| `nicegui` | **3.17.1** | 直接（`ui.py` だけが import する） | **MIT**（`py.typed` あり） | 可 |
| `pytest-asyncio` | 1.4.0 | 直接（dev。`user` フィクスチャが async ジェネレータ） | Apache-2.0 | 可 |
| `docutils` | 0.23 | 推移（nicegui） | Public Domain ＋ BSD-2/3-Clause（分類子に GPL も並ぶ） | **可**。同梱 `COPYING.rst` によれば本体は Public Domain で、GPL-3.0-or-later が掛かるのは `tools/editors/emacs/rst.el` の 1 ファイルだけ。wheel の `RECORD`（225 行）に `.el` は 0 件で、**配布物に GPL のファイルは含まれない**（実測） |
| `bidict` | 0.24.1 | 推移（python-socketio） | **MPL-2.0** | **可**。弱いコピーレフト（ファイル単位）で、未改変のまま依存として使う限り本リポジトリのコードに義務は及ばない。憲章 §11 が避けるのは GPL 系（AGPL・SSPL 等の強いコピーレフト）である |
| `python-socketio` | 5.17.0 | 推移 | MIT | 可 |
| `python-engineio` | 4.14.0 | 推移 | MIT | 可 |
| `simple-websocket` | 1.1.0 | 推移 | MIT | 可 |
| `wsproto` | 1.3.2 | 推移 | MIT | 可 |
| `ifaddr` | 0.2.0 | 推移 | MIT | 可 |
| `markdown2` | 2.5.5 | 推移 | MIT | 可 |
| `tinycss2` | 1.5.1 | 推移 | BSD | 可 |
| `webencodings` | 0.6.1 | 推移 | BSD | 可 |
