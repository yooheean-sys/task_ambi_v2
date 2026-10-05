# orchestrator — 外側ループ（1 kickoff の周回・起票 1 箇所・人間へ渡す 1 経路）

BC-03「決める層」。10 契約の**利用側**として 1 kickoff を回し、`KickoffResult` を返す。

## 1. 責務

- `KickoffRunner.run(ctx) -> KickoffResult`（IF-49）を提供する。**提供契約はこの 1 本だけ**。
- 決めるのは 4 つ: **起票するか／close するか／もう一度やらせるか人間に渡すか／触るか触らないか**。
- HTTP の口（`POST /kickoffs`・`GET /kickoffs/{id}`・`GET /kickoffs/current`）で人が今すぐ動かせる。

### 持たないもの

| 持たないもの | 持っている場所 |
|---|---|
| 盤面の導出そのもの（`bd` の呼び出し） | board |
| 提案の形の検査・プロンプト組立・トークン抽出 | stages |
| LLM・Tools・CrewAI | executor |
| 上限・等級・F/W の盤面写像の中身 | monitor |
| 設定の正本（PJ・メンバ・段テンプレート） | settings |
| 履歴 csv | trigger（human 起動は履歴に残らない＝ D09-e の非対称） |
| **永続ファイル・キャッシュ・履歴** | 無し。**進行状態の正は盤面** |

## 2. 主要ファイル

| ファイル | fn | 中身 |
|---|---|---|
| `__init__.py` | F15 | `build`・`OrchestratorDeps`／`Handle`／`Config`・`OrchestratorStartupError`・`config_path` |
| `router.py` | F13 | `/kickoffs` の 3 本（**このファイルだけ `from __future__ import annotations` を置かない**。理由は冒頭 docstring） |
| `schemas.py` | — | HTTP 境界の DTO 5 種（共有側の型をそのまま入れ子にする） |
| `dependencies.py` | — | `Depends` 用 provider（差し替え点） |
| `models.py` | — | 内部 VO・入れ物・段の戻り・**写像表**・**印の定数と 3 関数** |
| `service/loop.py` | F01・F02・F04・F14 | 周回の骨格・single-flight・確保・記録・`start_background` |
| `service/recover.py` | F03 | 起動時の拾い直し（3 分岐） |
| `service/prepare.py` | F05・F06 | 前処理と人間の指示・回答の収集（**自分では書かない**。失敗は F12 へ委ねる） |
| `service/kickoff.py` | F07・F11 | `begin` → 組立 → 実行器 → 段別 → `finish` → 判定の解釈 |
| `service/propose.py` | F08 | 分解（ガード・検査・被覆・起票順序の不変条件） |
| `service/execute.py` | F09・F10 | 完了ゲート（`outcome/` は vault・`repo/` は repo で実在確認＝ D25-b）とレビュー／エスカレーション |
| `service/handoff.py` | F12 | 人間へ渡す 1 経路 |
| `service/writes.py` | — | **`create`／`close` を呼ぶ唯一の場所** |

内部 import の向き（**崩さない**）: `__init__ → router → {schemas, dependencies, service}` ／
`service/loop → {recover, prepare, kickoff, handoff}` ／ `{propose, execute} → {writes, handoff}` ／
すべて `models.py`（葉。`common` 以外を import しない）。`OrchestratorConfig`・`OrchestratorDeps`
は `__init__.py` にあるので `models.py`／`service/*` からは **`if TYPE_CHECKING:` でのみ**参照する。

## 3. 規約（4 つ。改修時に必ず守る）

1. **起票（`create`）と close は `service/writes.py` の 1 箇所ずつ**（D01「起票 1 箇所」・SC-027）。
   `grep -rn 'board_writer\.create' src | wc -l` が **1**、`board_writer\.close` も **1**。
2. **判定は盤面から都度引く**（キャッシュ禁止＝ FR-088）—— 子の有無・未回答の質問・差し戻し回数。
3. **人間へ渡す形は `HandOff` の 1 経路**（`comment` → `set_labels` → `release` の 3 手・この順）。
4. **差し戻し回数は盤面のコメントで数える**（前方一致。プロセスメモリに持たない）。

## 4. actor の使い分け（P5D-037・SC-028）

| 行為 | actor |
|---|---|
| 確保・解放・回収コメント・差し戻しコメント・人間へ渡すコメント | `config.system_actor`（**自分の行為**） |
| 子の起票・質問の起票・依存張り・親／リーフの close | `member.bd_actor`（**AI の行為**） |
| `RunMonitor.finish` に渡す 1 つ | `config.system_actor`（2026-08-26 裁定・SD-32。契約が actor を 1 つしか取らない穴は B-38） |

- `system_actor` は settings の台帳が「システム」として知り、`is_human` は **False** を返す。
- `build()` が起動時に `is_human(system_actor)` を **1 回だけ**叩き、True なら**起動を中断**する。

## 5. コメントの印（前方一致の鍵。**コード上 1 箇所**＝ `models.py`）

| 種類 | 本文の頭 | 数えるか |
|---|---|---|
| 差し戻し | `<marker> 差し戻し（n/上限 回目）[分類]: 理由` | **これだけ**数える |
| 回収 | `<marker> 回収: …` | 数えない |
| 人間へ渡す | `<marker> 停止[区分]: …` | 数えない |

- 分類は**印の直後**に置く（前方一致の鍵より後ろ）。
- 印を変えるときは `config.json` の `comment_marker` を変えるだけでよい。
  本文も鍵も `models.return_comment()`／`return_prefix()` が組む。
- 判定式は **`件数 >= max_returns`**（語のとおりの勘定）。既定 2 で**実行器 3 回・差し戻し 2 件**。

## 6. `close` を計測の締めの後に置く理由（plan の C-1）

段別処理（`propose`／`execute`）は **`CloseIntent`（理由・actor）を返すだけ**で、実際の `close` は
`service/kickoff.py` が `finish` → 判定の解釈の**直後に 1 回**行う。

- monitor の現物は等級 W で `comment` ＋ `close` を行うため、**先に閉じると同じ課題へ close が
  2 回飛ぶ**。FR-068「計測側が既に閉じていれば自分の close を抑止」が成立しなくなる。
- **入れ替えない**。FR-045 が定めるのは相対順序（起票 → 依存 → 付け替え → 確認読取 → 観測印 →
  親を閉じる）であり、間に `finish` を挟んでも相対順序は保たれる。

## 7. 改修時の注意（踏みやすい罠）

- **`config_path()` を module ロード時に評価する形へ「整理」しない**。`settings_customise_sources`
  の中で呼ぶこと。クラス定義時に評価するとテストの差し替えが**黙って無効**になり実ファイルを読む。
- **差し戻し回数を「前処理で読んだコメント＋自分の加算」に最適化しない**。SC-019（プロセスの
  状態を捨てても同じ値）が実質検査できなくなる。
- **回収コメントを回数に数えない**（FR-037）。
- **`monitor.begin.superseded` の警告は 2 件目以降の課題で必ず出る**。異常ではない（`begin` は
  課題ごと＝ FR-034a）。
- **上限（`max_requests_per_kickoff`／`max_cost_per_kickoff`）は名前に反して「1 課題あたり」に
  効く**。「1 起動あたりに直す」と読んで `begin` を 1 回に減らさない（`threshold_t` がメンバごと、
  `limits` が PJ ごとに違う）。
- **`claim_next` の `None` を「失敗」に倒さない**。ブロック中かどうかを盤面に問い直さない。
- **質問を `--parent` で張らない**（`open_question_of` が拾えなくなる＝ P5D-059 (2)）。
- **`src` に `httpx`・`crewai`・`casbin`・`nicegui`・`apscheduler`／`watchdog` を取り込まない**
  （境界契約 5 本の**対象側**。`fastapi` は取り込む）。
- **設定 5 種は課題ごとに 1 回**。`persona:` ラベルが無いときだけ `get_tasks` を主体の解決の前に
  呼ぶが、**回数は 1 課題につき 1 回のまま**にすること（D16-f と FR-025 の両立）。

### 意図して未達にしてある箇所（**黙って埋めない**）

- `IssueRunSummary.result` の **`released` に到達する分岐は無い**（FR-071a・DIFF-07・B-34）。
  段・主体の解決前に終わった課題は一覧に載せず `KickoffResult.notes` に 1 行残す。
- 設定 5 種は課題ごとに読む。`P5D-016`／`P5D-049`「次回 kickoff から効く」の粒度は
  **1 課題単位**に弱まる（DIFF-21・B-37）。
- `KickoffRecord` の **`rejected` は本 feature では現れない**（FR-012a。断りを積まない）。
- **「異常終了 → 回収 → また異常終了」の歯止めが無い**（SD-18・plan の C-7）。対応する SC が
  無いため実装しない。回収コメントが積み上がるだけで盤面は壊れない。
- SC-036・SC-037 は**自動テストで測らない**（plan の C-6）。`quickstart.md` S8 の手動手順で測る。
- `service/kickoff.py`・`service/loop.py`・`models.py` は quickstart S10 の目安（250 行）を
  超えている。分割しない理由は下の「規模」を読むこと。

### 規模

`src` は約 2,600 行。`models.py`（≈480）は **1 ファイルに保つ** —— SC-027 の確認（`grep` が
`models.py` を名指しする）と data-model §1 が置き場を固定しているため。`service/` は
plan (i)・設計 notes §1 が **8 ファイル**に固定しているので、`kickoff.py`／`loop.py` を
これ以上割らない（`begin`／`finish` の `try/finally` は 1 ファイルに閉じる＝ C-3）。
憲章 §1 の分割検討水準（100,000 トークン）には達していない。

## 8. 検証コマンド（**1 コマンドずつ**実行する）

```bash
uv run pytest packages/features/orchestrator/tests
uv run ruff check packages/features/orchestrator
uv run pyright packages/features/orchestrator
```

土台側の登録まで含めて確かめるとき（順序を守る）:

```bash
uv sync
uv run lint-imports                      # → Contracts: 7 kept, 0 broken.
uv run pytest apps/api/tests/foundation
```
