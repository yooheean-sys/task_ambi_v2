# 機能メモ（第一フェーズ）

**位置付け**: 各ユースケース（`04_usecase_desc.csv`）を実現するのに要る**機能の候補**を、出典と確度付きで書き留めるメモ。未確定の問いも書く。フェーズ2の「機能一覧（csv）」は本メモを確定・正規化したもの、と位置付ける（D02 Q6）。
**確度の記号**: ✔ PoC 実測で成立（PoC 基準版）／ ▲ 付録・会話で構想済み・未実装／ ✖ 本メモで初出（新規候補）／ ❓ 問い（未裁定）
**規約**: 本メモは改訂可。裁定が下りた問いは `設計判断_V4-D**` へ移し、本メモからは消すか「→ Dnn」に置き換える。

---

## 0. コンテキスト図（`01_context.mmd`）の要素詳細

図内では短文にした説明の正本。

| 要素 | 種別 | 詳細 |
|---|---|---|
| 自分 | Person | 単一利用者（constitution §0）。複数プロジェクト（案件＋メンテ用）を統括する立場（付録：組織編成 ORG-1）。起票・回答・設定・閲覧を行う |
| task_ambi_v4 | System | CrewAI による階層 AI 組織を beads で駆動する個人作業効率化システム。モジュラーモノリス（FastAPI 単一プロセス＋Streamlit UI の 2 プロセス、7-2 J）。階層分解・遂行、Obsidian 読書、権限制御、AI 制御、トリガー実行を担う |
| beads | System_Ext | bd CLI / Dolt（v1.1.2）。本システムの**オーケストレータ**（CrewAI は従、§8.3）。本システムは bd CLI 経由でのみ読み書きし、内部 SQLite/Dolt を直接触らない（constitution §2）。指定ツール（アダプタ分離、constitution §2） |
| beads-ui | System_Ext | beads の Web UI（0.12.5）。人間のエスカレーション回答（コメント→close）と盤面閲覧に使う。New Issue に acceptance_criteria 欄が無い（K-2） |
| Obsidian vault | System_Ext | `/mnt/c/...` 経由でマウントされる Markdown 群。環境変数でパス指定（付録：windows側 OB-4）。プロジェクトフォルダ・役割フォルダが AI への入力、PJ 直下の `outcome/` **のみ**が出力先（付録 obsidian OB-8〜OB-10） |
| ダウンロードフォルダ | System_Ext | Windows 側フォルダ。ファイル投下をトリガーとして watchdog PollingObserver で監視（WSL2→Windows の inotify は不発、7-2 J） |
| LLM API | System_Ext | OpenAI 互換の推論エンドポイント。実体は 2 つ: OpenRouter（既定 `qwen/qwen3.7-plus`、7-2 G）とローカル llama.cpp（`localhost:11434/v1`、constitution §4）。裁定済 → **D06-d**（LLM プロファイル 2 件・メンバ単位割当＋グローバル既定・既定 OpenRouter） |

描かなかったもの: GitLab（次 ver 候補）、タイマ（システム内部の APScheduler）、systemd（プロセス管理であって連携先ではない）、langfuse / signoz（§6-3 スコープ外）。

---

## 1. UC ごとの機能候補

### UC-01 統括タスクを起票する／UC-02 プロジェクト／WS へ直接起票する

- ✔ 起票口 3 種: 統括 `--file`（kind:program）／`kind:request` 親なし／`kind:subrequest` 親なし。すべて盤面実測で成立（K-1 行4、PoC 報告）
- ✔ 段の解決 `resolve_stage`: ラベル × 親の有無。3 階層化で無改修（7-2 A）
- ✔ ラベル規約: 子にも POC_LABEL（`role:executor`）を必ず載せる。落とすと ready に浮上せず無言停滞（loop.py `_flush_proposals` コメント）
- ▲ 起票時のガイド（役割指名・ラベル・受入条件の書き方）。ガイド穴 12 件は M1 完了まで据え置き（6-2）
- ✖ **起票 UI**: 現状は bd CLI 直叩き。**D07-d で廃止**（D06-k を覆す）: 本 ver は bd CLI 起票のみ。acceptance_criteria は `bd create/update --acceptance`
- ❓ 「何も指示なく投入すると統括に行く」原要件と、現実装「ラベル無しは plain 受け皿で検証なし close」の相違（K-2）。既定の落とし先変更は resolve_stage の不変条件に触れるため本 ver では扱わない、と K-2 に記載済み。**P3 の穴⑤としては残る**

### UC-03 進行中タスクへ追加指示する

- ✔ 次 claim 時に「人間の指示を検出」として原文が渡る（K-1 行6、B3/B4）
- 実行中（kickoff の最中）に付いたコメントの扱い: 現周回では読まれず次周回。**仕様として明文化**（第2フェーズ `p2_feature_list.csv` FN-07 備考）

### UC-04 エスカレーション質問に回答する

- ✔ `kind:question` 起票 → 元 issue に blocks 依存 → **元 issue の claim を解放**（`_open_escalation` は create→dep→release の順。「in_progress のままだと ready にも blocked にも出ない」`loop.py:927`（`_open_escalation`））。待機中の元 issue は open+blocked で、質問が close されると `bd ready` に浮上する（K-1 行5、§8.3）
- ✔ コメントでは再開せず close で再開（2手分離）
- ✔ 回答者 actor が AI と別名（E4、K-1 行7）。beads-ui 既定フォールバックに依存（3-4 X-4）
- ✔ 起動時回収は「未回答の質問待ち」を触らない（`loop.py:1003-1007`＝`recover_orphans` の質問待ちガード）。これは create→dep→release の途中でプロセスが死んだ窓のための安全策であり、通常の待機 issue は in_progress ではないので回収対象にならない
- 裁定済 → **D04-c**: `--watch` モード（ready が空でも質問が残る限りループを終了せず待つ）は**廃止**。再開の起点は ready 巡回（interval 既定トリガー）＋手動実行の 1 系統。E6（自動再開経路の二重化）は解消。回答後の待ち時間 = 巡回間隔
- 裁定済 → **D06-c**: 実行主体はメンバ台帳 1 つに畳む（bd actor＝メンバ ID、casbin subject＝member:<ID>、CrewAI role は表示名。台帳外 actor は印を残す）

### UC-05 成果物を Obsidian で閲覧する

- ✔ 書込先は PJ 直下 `outcome/` のみ。境界は `_outcome_base` の実装（構造、P1）
- ✔ `read_outcome_file` は不在でも `ok:true`（穴⑫。P3 の「黙って進む経路」として記録済み）
- ▲ 禁止文字を含むファイル名の生成禁止。付録は「いったんプロンプトで対応？」→ **P1 に従いゲート（書込前検査）で持つ**。集合は裁定済 → **D07-c**（Windows／mac 非互換＋Obsidian タイトル禁止文字の和集合）
- ❓ 「編集は OK とするか」（outcome/ 外の既存ファイル編集）は付録 obsidian OB-10 で未決のまま

### UC-06 タスクを取り下げる 【据え置き】

- ▲ 解除条件 ★仮 = 実タスク投入判断の前（6-2）。2 段ルーティング機構より先が未踏
- ❓ 取り下げ後の子 issue・成果物・進行中 kickoff の扱い（トリガー導入で「撤退状態」の除外規則とも連動、付録：トリガー 未決）

### UC-07 AI 設定を CRUD する

- ✔ casbin ポリシー（policy.csv）＋メンバ宣言（V4 は projects/<key>/agents.yaml。D15-j・D16。PoC は fleet.yaml）＋validate（3-3、7-2 H）
- ✔ `run_command` の二段許可（ALLOWED × Casbin）。ツール追加時は両方見る
- ▲ メンバモデル設計案（未実装）: メンバ = 役割 × プロジェクト、`agent:` 全廃 → `member:`、型付きストレージ、委譲の新規実装、validate 追加 6 規則（7-2 H ✖ 文書化要確認）
- ✖ 一覧の対象: 権限／ルール／スキル／エージェント（役割）／MCP／hooks／AI モデル（§2 R-05）＋上限ポリシー（D06-g）＋コマンド定義（ALLOWED の設定化、**D07-e**）。メンバは手動作成（D07-f）。対応は裁定済 → **D06-e**（casbin PoC `loader.py:6-21` の対応表。fleet.yaml 書式は継承しない）
- 実行中に設定を変えたときの反映タイミング: **次回 kickoff から**（04 csv 事後条件のとおり。`p2_feature_list.csv` FN-22）
- 裁定済 → **D06-b**: ペルソナは各々ただ 1 つの段に属するので段は導出でき、2 セグメント `<project_key>.<persona_key>` のままでよい。統括は予約キー `_program`。casbin ドメイン不使用

### UC-08 プロジェクトを CRUD する（フォルダ対応付け）

- 裁定済 → **D04-d/e**: 本 UC は「XXX のフォルダは XXX プロジェクトのフォルダである」という**対応付け**の登録・編集・削除。フォルダ実体の作成・改名・削除はシステムの外で人間が行う（constitution §2: vault の所有者はシステムではない）
- ▲ 登録項目: プロジェクト名／Obsidian 入力フォルダ（PJ・役割）／outcome/ 出力先／権限領域。組織テンプレート（案件用・メンテ用）は **D07-f で廃止**（メンバは UC-07 で手動作成）。登録・編集・削除時のエラーは Streamlit に返す（**D07-g**）
- ✖ **実行時の存在検証**（D04-d）: kickoff のたびに対応フォルダの存在を確認し、無ければ止まって理由を残す。登録時の検証だけでは手動改名に追随できない（P3: 空コンテキストで黙って走らせない）
- ✖ **削除ガード**（D04-d）: 対応付け解除時に当該プロジェクトの open issue があれば拒否または警告。beads 側の issue は残るため、参照先の無い issue を作らない
- ✖ 「プロジェクト」というエンティティが現行 PoC には明示的に存在しない（casbin 側は `member:<project>.<role>` の文字列、renkei 側は統括 issue の子）。裁定済 → **D06-a**（正本＝UC-08 の project_key、beads へは `project:` ラベル伝播、outcome は PJ フォルダ/outcome/）
- ▲ GitLab パス登録は次 ver 候補（§2 R-13-c）

### UC-09 トリガーを CRUD・有効化・手動実行する／UC-10 履歴・生死を確認する

- 裁定済 → **D04-e**: トリガーは CRUD（付録 §5「トリガー CRUD」を UC 名に明記）。実行中のジョブを持つトリガーの削除は裁定済 → **D06-j(2)**（次回発火停止のみ・kickoff は完走・履歴はスナップショットを持つ）
- 裁定済 → **D04-c**: **既定トリガー「ready 巡回」**（interval・常設）を持つ。人間回答後の再開・通常の ready 拾いはこれが担う。UI からは間隔と有効/無効のみ変更可（削除不可）
- ▲ APScheduler 3.11.x（4.0 は alpha）。`max_instances=1, coalesce=True`。曜日 0=月曜（付録：トリガー §1-2、7-2 J）
- ▲ UI は daemon の REST のみ叩く（jobstore を別プロセスから触ると永久に発火しない）
- ▲ 履歴: 実行ID／開始終了／duration／起動種別 timer・file・manual／status／blame（付録 §5）
- 憲章との食い違い 2 点 → **裁定済 D07-a/b**（jobstore＝設定ファイル宣言＋起動時再登録・インメモリ／履歴＝csv 追記のログ扱い。付録の SQLite 2 箇所は不採用。X3 の残りは一般論のみ）:
  1. jobstore: 付録は `SQLAlchemyJobStore(SQLite)`、憲章 §2 は「インメモリ jobstore、ジョブ定義は設定ファイル宣言＋起動時再登録、取り逃しの追い発火は持たない」
  2. 履歴・メタデータの永続化: 付録は「別テーブル(SQLite)」、憲章 §2 は「システム固有の永続ストアを持たない」→ 履歴を beads（issue コメント）や vault（Markdown ログ）に委ねるか、憲章の例外に列挙するか
- ✖ トリガーの「対象」の表現: 裁定済 → **D06-h**（起動条件＝claim フィルタ。ラベル集合＋任意 project_key。既定トリガーは role:executor 全件。ファイル投下は blame にパス記録のみ）

### UC-11 無人でタスク遂行を起動する

- ▲ 2 プロセス（trigger-daemon + trigger-ui）、flock 単一起動、systemd `Restart=always`（付録 §プロセス構成・§6）
- ▲ 起動種別を実行コンテキストとして貫通（6-1 伝播箇所 2）。持ち運ぶモジュールは 裁定済 → **D08-d**（所有 BC-03・発番は起動元・型は common・correlation_id は contextvar 貫通・履歴の snapshot は値コピー）
- 裁定済 → **D01**: 冪等ガードは盤面導出（`children_of`）で回復層＋分解層の二重。3 点セット（idempotency key＋事前ジャーナル＋reconcile）は不採用。**付録：トリガー §6 の記述は D01 と食い違うため要改訂**（作業メモ）
- 裁定済 → **D04-c**: 再開経路は本 UC の ready 巡回に一本化（watch モード廃止）。回収層は異常死の残骸だけを扱い、質問待ちの issue（open+blocked）は通常の `bd ready` 経路で拾われる。E6 解消。棄却案: タグで区別（静的な印は kill -9 で剥がれず無症状停滞）／PID ロックで共存禁止（人間待ちの間ほかの実行が止まる）
- ▲ 書き込み完了検知は「別途開発済み・テストのみ」（付録 §4）。❓ **どこにあるか未特定**。PollingObserver との統合テストは未実施
- ▲ P0 実測「PollingObserver で /mnt/c を監視した際の検知レイテンシと CPU 負荷」が未実施（付録 作業順序 1）。結果次第で FastAPI の役割（内部 API のみか webhook 受け口も含むか）が変わる
- ❓ 撤退（stoppage）状態の issue をトリガー対象から除外する規則（付録 未決）

### UC-12 暴走を止める

- ✔ HTTP transport 層 Interceptor と 149 の BaseEvent 全購読リスナー（crewai_ai_control P1）。**再利用可能な資産**
- ✔ CrewAI 側の上限は制御にならない: `max_execution_time` はツール実行を割り込まない／`max_rpm` は強制最終回答をレート制限しない／`Agent.guardrail` は黙って無視／コスト上限・ロールバックは存在しない（7-2 D、P2）
- ▲ 要件③: 送信前トークン計測と閾値停止。要約器は作らない（§2 R-10、付録：AI 制御）
- ✔ 三層リトライの積（openai SDK × CrewAI × executor）。上限値は積で見積もる（7-2 E）。裁定済 → **D04-a**（確認）。上限を厳しくするなら外側で回数を絞るのではなく内側の層（例: SDK `max_retries`）を縮める
- 裁定済 → **D06-g**: T は LLM プロファイルの context_window から導出。回数・コストはグローバル既定＋PJ 上書き。段・ペルソナ単位は持たない。「上限ポリシー」は UC-07 の 1 項目

### UC-13 失敗を beads で検出する

- ✔ 戻り値は両端で信用できない（X-3）。観測点はイベント・HTTP 層・出力パース
- ✔ `LLMCallFailedEvent` は 1 つの論理的失敗につき 2 回発火（7-2 E）→ 重複排除が要る
- ▲ 失敗等級 F/W/I・封筒形式・bd 状態遷移（付録：AI 制御 §3.2(b)/§4）。等級表は参考程度（§2 R-09 子行）
- 裁定済 → **D04-b**: bd への写像後に**確認読取**（`bd show`／`bd comments`）で反映を検証する。rc は見ない。不一致・bd 不達なら黙って進まず停止しローカルログに痕跡を残す（P3）
- 裁定済 → **D06-i**: F＝state:failing＋claim 解放＋封筒／W＝close＋封筒／I＝履歴のみ。question 起票は使わない。UC-13 停止時のローカルログは憲章 §6 構造化ログ（D06-j(3)）

### UC-I1 階層分解して遂行する（include）

- ✔ 3 階層の段定義・ペルソナ 9 種・段別テンプレート（V4 は tasks.yaml D16）・B1/B2・完了ゲート・機械検証（7-2 A・B・F、PoC 設計仕様）
- ✔ 「子を全て起票し終えてから親を close」の順序不変条件（`loop.py:1547-1551`＝`_handle_issue`）
- ✔ 起票・依存張りの途中失敗は UNSAFE で人間へ（自動再試行しない）
- M1 議題（§6-1）: MIN_ITEMS 裁定済 → **D06-f**（段属性・既定 1・観測印）／WS 分割粒度は M1 テンプレート設計へ送り（D06-l）／実物照合ゲートは 裁定済 → **D08-f**（否・据え置き。帰属は BC-03 完了ゲート鎖）
- ✔ 「機構合格であって内容合格ではない」🔒（§1）。内容評価は §6-3 スコープ外

---

## 2. 横断的な問い（フェーズ2 以降へ運ぶ）

| # | 問い | 出どころ | 当たるフェーズ |
|---|---|---|---|
| X1 | 「プロジェクト」「メンバ（役割×プロジェクト）」「実行主体 ID」の正本と写像規則（X-4・X-6） | UC-04/07/08 | 2（ドメインモデル）→ **裁定済 D06-a/b/c** |
| X2 | 実行コンテキスト（起動種別・blame）をどのモジュールが持ち運ぶか | UC-11、6-1 伝播箇所 2 | 裁定済 → **D08-d**（所有 BC-03）／実装形 → **D09-e**（同期 `KickoffRunner.run(ctx)`、human 入口は orchestrator の `POST /kickoffs`、correlation_id は contextvar） |
| X3 | 憲章 §2「システム固有ストアを持たない」と、トリガー履歴・jobstore・設定 CRUD の永続化先 | UC-07/09/10 | ログ＝D06-j(3)、jobstore＝D07-a、履歴＝D07-b で解消。残りは「設定ファイル・csv ログが §2 の例外か」の一般論のみ（4.5） |
| X4 | LLM 2 実体（OpenRouter／ローカル）の使い分け規則 | コンテキスト図 | 2 → **裁定済 D06-d** |
| X5 | 「ルール・スキル・MCP・hooks」の設定対象が CrewAI のどの機構に対応するか | UC-07 | 2 → **裁定済 D06-e** |
| X6 | 上限値（回数・コスト・トークン）の粒度と設定表現 | UC-12/07 | 2 → **裁定済 D06-g** |
| X7 | 書き込み完了検知の実装の所在 | UC-11 | 実装フェーズ前に特定 |
| X8 | UC-01/02 の起票 UI を Streamlit に持つか（bd CLI 直叩きのままか） | UC-01/02 | 2 → D06-k（持つ）→ **D07-d で覆し「持たない・CLI」** |

---

## 3. フェーズ2 への引き継ぎ（**2026-08-18 実施済み**。成果物は `../phase2/`、裁定は D05・D06。本節は記録として残す）

- 機能一覧（csv）は本メモ §1 の各行を `func_id, uc_id, name, kind(構造/ゲート/プロンプト), status(✔▲✖), source` に正規化して作る。P1 の三段（構造 > ゲート > プロンプト）を列に持たせ、プロンプト依存の機能を可視化する
- ドメインモデルの候補エンティティ（本メモから抽出）: プロジェクト／メンバ（役割×プロジェクト）／段（Stage）／issue（統括・プロジェクト・WS・リーフ・質問）／成果物（outcome ファイル）／トリガー／実行履歴（run, blame）／上限ポリシー／権限ポリシー
- シーケンス図の候補: UC-I1（1 周回）、UC-04（往復）、UC-11（発火→claim）、UC-12/13（失敗の bd 写像）
