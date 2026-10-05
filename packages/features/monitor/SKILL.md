# monitor — 1 kickoff の計測・上限停止・失敗の等級付けと盤面への写像

**1 セッション = 1 モジュール**で読み・直し・検証できるようにするための手引き。
規範の正は `.specify/memory/constitution.md` とルート `CLAUDE.md`、設計の正は
`docs/design/phase5/monitor/` と `specs/007-monitor/`。本ファイルはそれらの要約と、
**改修時に踏みやすい罠**の記録である。

---

## 1. 責務

**「止めて正確に報告する」層**である。1 回の kickoff について

- LLM への HTTP を前後で観測して**計測**を積む（回数・トークン・費用・欠測）
- 上限を超えた要求を**外へ出る前に止める**（`RunHalted`）
- 実行が終わったら失敗を**検出**し、設定の割当で**等級**を付け、封筒に畳んで**盤面へ写像**し、
  **確認読取**で反映を確かめる

**回復の判断を持たない**（付録 AI 制御 §3.3(a)-5）。「止めた／こう失敗した」を正確に伝えるまでが
責務であり、やり直すか・人に投げるかを決めるのは orchestrator である。

### 持たないもの

| 持たない | どこにある |
|---|---|
| 要約器・巻き戻し・履歴の圧縮 | 本システムに無い（`p5_monitor_notes.md` §5） |
| LLM クライアントの構築・装着 | executor（D09-b）。monitor は**フックを返すだけ** |
| 上限値の決定（`threshold_t`・回数・費用） | settings／orchestrator。monitor は**受け取るだけ**（FR-017） |
| 実行履歴への書込・永続ファイル | 持たない（外部ストアを所有しない初のモジュール） |
| HTTP 経路（`router.py`）・UI ページ（`ui.py`） | 持たない。`MonitorHandle.router` は `None` 固定 |
| `bd` の直接呼び出し | board の Protocol 経由のみ |

---

## 2. 主要ファイル

| ファイル | 担当 fn | 中身 |
|---|---|---|
| `src/monitor/__init__.py` | monitor-F12 | `MonitorConfig`・`MonitorDeps`・`MonitorHandle`・`build()`・`config_path()`・`MonitorStartupError` |
| `src/monitor/service.py` | F06〜F11 | `MonitorService`（`begin`／`finish`）と検出・等級・封筒・写像・確認読取 |
| `src/monitor/repository.py` | F01〜F05 | `HttpHook`（4 メソッド）・`TokenEstimator`・帰属・スレッド安全 |
| `src/monitor/models.py` | — | 内部 VO（`RunState`・`RawFailure`・`Envelope`）と検出規則の表（`EVENT_RULES`） |

**`exceptions.py` を置かない**: 契約例外（`RunHalted`・`MappingUnverified`）は
`common/interfaces/monitor.py` が正本で monitor では再定義しない。内部例外は起動時の
`MonitorStartupError` **1 つだけ**なので、公開口である `__init__.py` に同居させる（stages と同型）。
**`router.py`・`ui.py`・`schemas.py`・`dependencies.py` も置かない**（HTTP 境界も UI も無い）。

---

## 3. 規約（4 つ。改修時に必ず守る）

1. **`crewai` を import しない**。イベントバスも購読しない。受け取るのは中立型
   （`CrewOutcome`・`CrewEvent`）だけで、翻訳は executor の責務である。
   `.importlinter` の `crewai-scope` が機械的に守る。
2. **`RunHalted` は `BaseException` 直系を守る**。`AppError` に載せ替えると SDK・crewai の
   `except Exception` に捕まり、**停止装置がリトライの対象になって上限が実質 3 倍以上に膨らむ**
   （Z-6）。monitor 自身のコードも、この性質に**依存した書き方をしない** —— 判定と送出は
   `try` の**外**に置く。
3. **Hook でヘッダ値を記録しない**。残すのは**名前だけ**（小文字化して `extra.header_names`）。
   `common/kernel/log.py` の `RedactFilter` は**第二の防波堤**であって第一ではない。
4. **beads へは board の Protocol 経由のみ**。`bd` を直接呼ばない・`.beads` を読まない。
   **import では捕まえられない**ので規約とレビューで守る（憲章 §1）。

---

## 4. `httpx` の扱い

| 面 | 扱い | 理由 |
|---|---|---|
| 依存宣言 | **実行時依存**（`packages/features/monitor/pyproject.toml`） | 型検査とテストが `httpx.Request`／`httpx.Response` を実際に構築する（FR-073） |
| `src/` の import | **`TYPE_CHECKING` の中だけ** | 実行時に束縛しなければ `httpx.Client()` を書くこと自体が `NameError` になり、通信しないことが機械的に守られる（FR-008） |
| `.importlinter` | `httpx-scope` の `source_modules` に **monitor を足さない** | monitor は**所有側**。入れると monitor 自身の実装が違反になる（FR-071） |
| 同上 | `httpx-scope` に **`allow_indirect_imports = True`** を足す | 土台が全 feature を import するので `api.registry -> monitor -> httpx` の間接連鎖が必ず違反になる。直接 import は**依然として捕まる**（実測: board に `import httpx` を 1 行足すと `6 kept, 1 broken`） |

`grimp` は `TYPE_CHECKING` の import も依存グラフに載せる（research R-13 で実測）。
したがって `monitor -> httpx` の辺は**どちらの書き方でも成立する**。

---

## 5. `truncation_phrases` の対象 CrewAI 版

**`crewai 1.15.10`**。実体は `crewai/translations/en.json:47`（`errors.force_final_answer`）で、
全文は `crewai_ai_control`。
出荷時既定は現物と**1 バイト違わない**ことを確認して置いてある。

**版が変われば文言が変わる**。上げるときは実物と照合してから `config.json` を差し替える
（`003` が `bd 1.1.2` を手引きに固定したのと同型）。照合は `messages[-1].content` と
**両辺 `strip()` して完全一致**であり、部分一致では積まない。

---

## 6. `grade_map` を変えるときの手順

1. **`config.json` の値だけを変える**。**コードに写しは無い**（`_grade_of()` が唯一の読み手）。
2. **F に上げると写像経路が変わる** —— `comment` → `close`（完了）から
   `comment` → `set_labels(+state:failing, -state:working)` → `release` へ。
   **課題が open のまま残り、人間の対処が要る**ことを意味する。
3. 値は `F`／`W`／`I` のみ。それ以外は**起動が止まる**（見に行った絶対パスが出る）。
4. **キーを消しても起動は止まらない**が、実行時に **F に倒され警告**が出る
   （`monitor.grade.unknown_kind`）。無症状にしないための倒し方である。
5. 変更後は `uv run pytest packages/features/monitor/tests` を回す。

---

## 7. 改修時の注意（踏みやすい罠）

- **応答本文を読んだら `elapsed` を埋める**。transport 層で `read()` すると `_content` が
  キャッシュされ、httpx 後段の `read()` が短絡して `elapsed` が未設定のままになる。
  消すと呼び手の `.elapsed` が `RuntimeError` で壊れる。**ストリームの内部状態
  （`_content`・`is_closed`・`_decoder`）には触らない**。
- **SSE（`text/event-stream`）は本文を読まない**。読むと下流のストリームを食い潰す。
- **判定順（長さ → 回数 → 費用）と境界（厳密な `>`）を変えない**。3 つ同時に当たっても
  種類が一意に決まることが要件である（SC-003）。PoC の「費用 → 回数」「費用 `>=`」は継承しない。
- **ロック区間から本文読取・ログ出力・`RunHalted` の送出を出す**。ロックは 1 本
  （`HttpHook._lock`）で、`RunState` には持たせない。再入しない。
- **ラベルは `Labels.state(...)` で作る**。`"state:failing"` を直書きしない。
  質問ラベル（`Labels.kind(KindLabel.question)`）は失敗の写像に使わない。
- **`MonitorConfig(**overrides)` は `config.json` を読む**。`init_settings` と
  `JsonConfigSettingsSource` の両方を通るため、**`dict` のフィールド（`grade_map`）は深くマージ**
  される。「キーが欠けた設定」をテストで作るには `conftest.without_config_file()` で
  JSON ソースを空にする（`make_config()` は既にそうしている）。
- **`JsonConfigSettingsSource(..., json_file=config_path())` を呼び出し時に解決する**形を保つ。
  クラス定義時に評価する形へ「整理」すると、テストの差し替えが**黙って無効**になり実ファイルを読む。
- **`import tiktoken` をモジュール先頭に書かない**。`tiktoken` は依存に加えていない（SD-14）ので、
  静的依存にすると `grimp` の依存グラフと `pyproject.toml` の実態が食い違う。
  使うのは `token_estimator == "tiktoken"` を明示指定したときだけで、未導入なら**起動が止まる**。

### 意図して未達にしてある箇所（**黙って埋めない**）

- **`no_output` の「定型のみ」を実装していない**（spec FR-040 の一部）。判定語彙が設計・設定台帳・
  共有側のどこにも無く、`config.json` が 6 項目に固定されているため置き場も無い。実装は
  **`None`／空文字／空白のみ**の 3 つに限る。裁定待ちは `specs/007-monitor/design-sync.md` の
  **SD-15・B-25**。

---

## 8. 検証コマンド（**1 コマンドずつ**実行する）

```bash
uv run pytest packages/features/monitor/tests
uv run ruff check packages/features/monitor
uv run pyright packages/features/monitor
```

`&&` で連ねない（最初の失敗で後続が隠れる）。テストファイルの基名は必ず `test_monitor_*` にする
（`tests/` に `__init__.py` を置かないため、基名が top-level モジュール名になる。feature を
またいで重複すると `uv run pytest`（全体）が collection error で止まり、**モジュール単位の
検証では再現しない**）。
