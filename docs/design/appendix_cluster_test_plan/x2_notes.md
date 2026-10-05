# 統合テストのクラスタ 説明（付2フェーズ）

**位置付け**: `x2_cluster_01〜05_*.md`（★）／`x2_cluster_index.csv`／`x2_constitution_patch.md`（PATCH-11）の短文の正本。裁定は **D14**（`設計判断_V4-D14_付2前提と統合テストクラスタの裁定.md`）。食い違えば D14 が勝つ。
**入力**: `../phase5/<MOD>/p5_<MOD>_sequence*.mmd`（15 本。共起の抽出元）／`../phase2/p2_sequence_*.mmd`（6 本。ストーリ境界）／`../phase4/p4_module_dependency.mmd`・`p4_module_contract.csv`（IF-01〜50。IF-50 は P5-R1 D16・14 Protocol）／`../phase6/p6_external_interface.csv`（EIF-01〜13）／`../phase1/04_usecase_desc.csv`（UC-*）／憲章 §1・§3・§5・§8／`../phase6/handoff_p6.md` §2-2／`../x1/handoff_x1.md` §3-1（付1 からの申し送り REV-08）
**レジーム**: 改訂可（md・csv と同時に更新）。
**工程文書との読み替え**（D05 §5・D14 冒頭）: 「サービス」→「モジュール」。クラスタ＝「境界をまたぐ結合テストのまとまり」であって E2E シナリオの単位ではない。

## 0. 一言でいうと

契約 50 行（IF-01〜50。IF-50 は P5-R1 D16 で追加）を **5 クラスタ**に割り、各 IF を「主検証クラスタ」ちょうど 1 つに置いた（50/50・重複なし）。外部ツールは憲章 §8 どおり一時ディレクトリの実物（beads は `bd init`）で、模擬するのは **LLM API だけ**（ローカル偽 OpenAI 互換サーバ）。クラスタ外 MOD は Protocol スタブにし、配線は実 composition root の build 連鎖＋override で行う。並びは p6 csv の外部依存件数順（01 監視・写像 → 02 設定 CRUD → 03 Tools・Hook → 04 周回 → 05 トリガー）。

## 1. 規則（D14 の要約）

| 規則 | 内容 | 出典 |
|---|---|---|
| 主検証／通過 | 各 IF-* は主検証クラスタちょうど 1 つ。他クラスタで実装同士が通る IF は「通過」に列挙（スタブ側が片方でも居る IF は通過に数えない） | D14-c |
| テストダブル 3 種 | ①外部模擬＝LLM API（EXT-03）のみ。ループバック HTTP の偽 OpenAI 互換サーバ（台本応答。MockTransport 不可） ②HTTP 入口代替＝TestClient（EXT-10 apps/ui・EXT-11 ブラウザ・EXT-12 PowerShell FSW の代わり） ③Protocol スタブ＝クラスタ外 MOD の Protocol 実装（憲章 §1） | D14-b |
| 実物 | beads（一時 dir に `bd init`・`BEADS_ACTOR`）・Obsidian vault（一時 dir を VAULT_ROOT に）・領域 A（一時 SETTINGS_ROOT）・領域 B＋履歴 csv（一時 TRIGGER_ROOT）・DL フォルダ（一時 dir）・.env（一時ファイル）・config.json（リポジトリの規約パス）・CrewAI・APScheduler・watchdog | 憲章 §8／D14-b |
| 配線 | apps/api の build 連鎖（board,vault→settings→monitor→stages,executor→orchestrator→trigger。`build(deps)->Handle` D11-f）を実行する。クラスタ外 MOD はルート config.json の有効 MOD 一覧（憲章 §5）で構築から外し、残る未充足 Protocol に override でスタブを注入。apps/api への M1 要求（D14 §6-1） | D14-e |
| 置き場 | `apps/api/tests/integration/cluster_NN_<slug>/`（apps→features の一方向。import-linter に抵触しない） | D14-h |
| 並び順 | p6 csv の MOD 列にクラスタ MOD が含まれる行数（EIF-08/09/13 除外）。同数はダブル（スタブ数＋外部模擬数）少ない順。番号＝文書の並び、実行順は各文書 §5 | D14-f |
| 前提（全クラスタ共通） | 付1 完了（並行作業。設計を直す裁定 0）・憲章 fullversion への PATCH-7〜11＋C-5 はユーザ適用待ち／bd バイナリが PATH にある（CI も）／LLM 偽サーバは M1 で実装／憲章 §8 の 3 層のうち本書は統合テスト層のみ（単体・AppTest は各 MOD） | D14-a/h |

## 2. 共起マトリクス（p5 シーケンス 15 本の Protocol レーンを提供 MOD に写像。機械抽出）

抽出規則: `p5_selfcheck.py` の `PART_RE` で participant を取り、ラベル（`／` 区切り）の全要素が 14 Protocol 名なら契約 csv の提供 MOD に写像。自 MOD 内部レーン（`<mod>/…`）と外部実体は除く。

| p5 図 | 所有 MOD | 共起 MOD（Protocol 経由） | 外部実体レーン | UC |
|---|---|---|---|---|
| board 主図／reads 副図 | board | なし（提供のみ） | bd CLI | UC-I1／UC-11・04・13・08 |
| vault 主図 | vault | なし（提供のみ） | Obsidian vault | UC-I1（UC-08 の登録時検証も） |
| settings 主図 | settings | なし（提供のみ） | なし（スナップショット参照 P5D-016） | UC-I1・UC-07 末尾 |
| settings uc07_save 副図 | settings | なし | 自分・設定ファイル領域 A | UC-07 |
| settings uc08_project 副図 | settings | **board・vault**（IF-09・IF-32） | 自分・設定ファイル領域 A | UC-08 |
| monitor 主図 | monitor | **board**（IF-03/08/15/18/20/22） | なし | UC-12・UC-13 |
| stages 主図 | stages | なし（提供のみ） | config.json（build 時） | UC-I1 |
| executor 主図 | executor | **board・settings・vault・monitor**（IF-02/17・29/30・33〜35/37・40〜43） | .env・LLM API | UC-I1（Tools）・UC-12（RunHalted 伝播） |
| orchestrator 主図 | orchestrator | **board・settings・vault・monitor・stages・executor**（7 MOD 全部） | なし | UC-11・UC-I1 |
| orchestrator propose 副図 | orchestrator | board・stages・executor | なし | UC-I1 |
| orchestrator execute 副図 | orchestrator | board・settings・vault・stages・executor | なし | UC-04・UC-I1 |
| orchestrator recover_human 副図 | orchestrator | board | 自分（POST /kickoffs） | UC-11 |
| trigger 主図 | trigger | **orchestrator**（IF-49） | 設定ファイル領域 B・実行履歴 csv・DL フォルダ・タイマ・apps/api | UC-11 |
| trigger ui 副図 | trigger | なし | 自分・設定ファイル領域 B・実行履歴 csv | UC-09・UC-10 |

UC 軸で読むと: UC-I1 は 7 MOD（trigger 以外）／UC-11 は 8 MOD／UC-04 は 6／UC-12・13 は board・monitor・executor・orchestrator／UC-07 は settings のみ／UC-08 は settings・board・vault／UC-09・10 は trigger（09 は fire→orchestrator）。

## 3. IF → クラスタ割当（主検証。csv `x2_cluster_index.csv` が正）

| クラスタ | 主検証 IF | 件数 |
|---|---|---|
| 01 monitor_mapping | IF-03・08・15・18・20・22（board←monitor）／IF-38・39（monitor←orchestrator）／IF-48（executor←orchestrator） | 9 |
| 02 settings_crud | IF-09（board←settings）／IF-32（vault←settings） | 2 |
| 03 executor_tools | IF-02・17（board←executor）／IF-29・30（settings←executor）／IF-33・34・35・37（vault←executor）／IF-40・41・42・43（monitor←executor） | 12 |
| 04 orchestrator_loop | IF-01・04・05・06・07・10・11・12・13・14・16・19・21（board←orchestrator）／IF-23〜28・IF-50（settings←orchestrator）／IF-31・36（vault←orchestrator）／IF-44〜47（stages←orchestrator） | 25 |
| 05 trigger_fire | IF-49（orchestrator←trigger） | 1 |
| 合計 | | **50** |

通過（実装同士が通るが主検証は別クラスタ）: 01 では IF-02/17（executor→board の Tools。LLM 台本が Tool を呼べば）・IF-40〜43（Hook 装着は 01 の前提）／03 では無し（呼び手はテスト）／04 では IF-09/32 は通らない（settings の UC-08 は 02）／05 では無し（orchestrator の依存は全部スタブ）。

## 4. クラスタに入らない UC

| UC | 理由 |
|---|---|
| UC-01／UC-02（統括タスク・直接起票） | 人間が beads-ui／bd で起票する動線。システムの境界を通らない（p5 に図なし）。起票済み issue から先は UC-I1＝04 |
| UC-03（進行中タスクへ追加指示） | 人間が bd comment する動線。システム側の読取（`BoardReader.comments` IF-07・`MemberResolver.is_human` IF-28）は 04 の通過 UC として扱う |
| UC-05（Obsidian で閲覧） | 人間が Obsidian で vault を読む。システムの境界を通らない |
| UC-06（取り下げ） | 据え置き（§6-2）。解除時に 04 へ追加（D14 §6-7） |

## 5. 憲章の穴 2 点（PATCH-11 として提示。`x2_constitution_patch.md`）

1. §3 ツリー・§8 に統合テストの置き場が無い（`packages/*/tests/` は MOD 単位。付1 REV-08 と同じ指摘）→ 本書は `apps/api/tests/integration/` を前提に書き、REV-08 が待つ「付2 で定めてから §8 へ 1 行」に PATCH-11 で応えた
2. §8 に LLM をテストで呼ぶか否かの規定が無い（beads・vault・git は「実物」と明記）→ 本書は「LLM は模擬」を前提に書き、同じ PATCH-11 に 1 行
適用はユーザ（PATCH-7〜10・C-5 と一緒に。`../x1/handoff_x1.md` §2 の順）。適用しない裁定でも x2 の前提は据え置き（D14 §6-6）。

## 6. 未実測・未決の所在

- apps/api の override 点の具体形／LLM 偽サーバの実装／`BoardWriteUnverified` の故障注入点／CI の bd 導入: D14 §6-1〜4（M1）
- openrouter 経路の Hook 装着（P5D-013/030）／PollingObserver の検知遅延 R12-P4（P5D-042）／BackgroundScheduler と FastAPI ループ（P5D-041）: D14 §6-5（M1／M2 の実測）。該当クラスタ §6 に「不合格なら」の分岐を書いた
- 付1 は並行作業で完了し「設計を直す 0 件」（D14-a）。IF／EIF は不変。PATCH-11 の適用可否はユーザ（D14 §6-6）

## 7. 工程文書との食い違い

- INPUT は全て実在（第6 と違い新設 ID は無い）。OUTPUT の置き場は工程文書に無く `outcome/design/x2/` とした
- 「1 クラスタ 3〜6 モジュール」に対し 05 は 2 MOD（理由は D14-d・csv の範囲外理由列）
- 「各クラスタ文書に含める内容」5 項目に §6「検証観点」を追加（D14-g）
