# ドメインモデル 説明（第2フェーズ）

**位置付け**: `p2_domain_model.mmd` の各クラスの説明の正本（図内は短文）。DM-nn は採番規約（追記のみ・欠番不埋め・再利用禁止）に従う。裁定は [設計判断_V4-D06](./設計判断_V4-D06_ドメインモデルの裁定.md)（ID 正本・設定粒度・失敗写像 ほか）と [設計判断_V4-D07](./設計判断_V4-D07_フェーズ2レビュー反映.md)（レビュー反映: トリガー実体・禁止文字・起票フォーム廃止・コマンド定義・組織テンプレート廃止・UC-08 エラー。**D07 が D06 と食い違う箇所は D07 が正**）、成果物形式は [設計判断_V4-D05](./設計判断_V4-D05_フェーズ2前提.md)。**P5-R1（2026-08-20）の改訂は 設計判断_V4-D16（非公開）**（AI 設定の agents.yaml／tasks.yaml 化。DM-03/05/07/09 改訂・DM-27 新設。D06-e・D08-b の一部を覆す）。
**確度**: ✔ PoC 実測で成立（PoC 基準版）／▲ 構想済み・未実測／✖ 本フェーズで新設／❓ 未決（D06 §6 または作業メモに登録済み）
**「実体」列**: その概念の状態がどこに実在するか。本システムは固有の永続ストアを持たない（憲章 §2）ため、全 DM に必須で書く（工程文書 §第2フェーズ「特に確認したいこと」）。

## 0. 集約の一覧と境界

| 集約 | ルート | 内包 | 実体 | 主な UC |
|---|---|---|---|---|
| プロジェクト集約 | DM-01 プロジェクト | DM-02 フォルダ対応付け／DM-03 メンバ／DM-04 権限ポリシー／DM-05 ルール／DM-06 hooks | 設定ファイル | UC-07・UC-08 |
| メンバ台帳 | DM-07（単独） | — | 設定ファイル（導出はインメモリ） | UC-04・UC-11・UC-I1 |
| 段と設定 | DM-08（コード定数）、DM-09（キー＝コード定数・本文＝agents.yaml D16）、DM-11／12／26／27（設定）。DM-10 は廃止・欠番 | — | コード定数（構造）／設定ファイル（本文・宣言） | UC-01/02・UC-07・UC-I1 |
| タスク集約 | DM-13 issue | DM-14 受け入れ条件／DM-15 質問 issue／DM-16 コメント | **beads**（bd CLI 経由のみ） | UC-01〜04・UC-13・UC-I1 |
| 成果物 | DM-17（単独） | — | **Obsidian vault** | UC-05・UC-I1 |
| 実行 | DM-18 kickoff | DM-19 実行コンテキスト／DM-20 計測／DM-21 失敗イベント | インメモリ→beads 写像／構造化ログ | UC-11・UC-12・UC-13 |
| トリガー集約 | DM-22 トリガー定義 | DM-23 起動条件／DM-24 実行履歴／DM-25 watcher 状態 | 設定ファイル（定義）／ローカル csv（履歴・ログ扱い）／インメモリ（watcher） | UC-09・UC-10・UC-11 |

境界の根拠: 「一つの事実は一つの出所から」（P4）と憲章 §2「外部ストアごとに所有モジュールを 1 つ」。beads・vault・設定ファイルという**実体の違いが集約の境界と一致する**ようにした。フェーズ3 の Bounded Context はこの境界を出発点にする（＝集約が別モジュールに割れないことを目標にする）。

## 1. プロジェクト集約（実体: 設定ファイル）

| DM | 種別 | 説明 | 確度 |
|---|---|---|---|
| DM-01 プロジェクト | 集約ルート | `project_key` が**正本**（D06-a）。casbin PoC と同じキー規則（`. / \ :` と空白を禁止。`manifest.py:219`（`_FORBIDDEN_KEY_CHARS`））。**統括（プロジェクト横断）は予約キー `_program` のインスタンス**（D06-b）。案件用／メンテ用の区別属性と組織テンプレート参照は **D07-f で廃止**（メンテ用プロジェクトもただのプロジェクトの 1 つ。ORG-1）。メンバは登録直後 0 件で、UC-07 で人間が手動作成する。renkei PoC にはプロジェクト識別子が存在しない（05 UC-08 ✖）ため本フェーズで新設。登録・編集・削除時のエラーは Streamlit に返す（D07-g） | ✖ |
| DM-02 フォルダ対応付け | VO | 「XXX のフォルダは XXX プロジェクトのフォルダ」という対応付けのみ（D04-d）。PJ 入力フォルダ・役割フォルダ（G-4・OB-4〜OB-8）・`outcome/` 出力先。フォルダ実体は人間所有（憲章 §2）。**kickoff 時に存在検証**し無ければ停止（D04-d）。outcome の置き場は PoC の `outcome/<親issueID>/`（`tools.py:602`（`_outcome_base`））から**PJ フォルダ/outcome/** へ変わる（D06-a） | ▲ |
| DM-03 メンバ | エンティティ | **メンバ ID ＝ `<project_key>.<persona_key>`**（2 セグメント、D06-b）。**UC-07 で人間が手動作成**（プロジェクト × ペルソナを選ぶ。自動生成・テンプレートは持たない。D07-f）。casbin PoC の `AgentSpec.member_id`（`manifest.py:87`）と同形。実体は `projects/<key>/agents.yaml` の 1 エントリ（キーの存在＝メンバ・空 dict 可。D16-d）。role（旧 display_name を統合）／goal／backstory／tools／max_* は CrewAI Agent の同名フィールド、global/agents.yaml のペルソナ既定をフィールド単位 merge（リストは丸ごと置換）。LLM は agents.yaml の llm:（PJ→global→default D16-h）。skills／mcp はメンバ単位（D06-e）。ペルソナはただ 1 つの段に属するので**段は persona_key から導出**（X-6 の第 3 セグメントは不要） | ✖（形は ✔ casbin） |
| DM-04 権限ポリシー | VO | プロジェクト別に 1 セット（casbin PoC: `fleet.yaml` の `permissions`、1 PJ＝1 policy.csv、ドメイン不使用 `model.conf:8`）。主体は `member:<メンバID>`。validate の順方向（manifest⊆policy）・逆方向（policy 主体⊆manifest。`validate.py:215`（`_check_orphan_subjects`））のゲート対象 | ✔ |
| DM-05 ルール | VO | PJ 共通ルール＋役割ルールを `Agent.backstory` に合成（D06-e。`loader.py:11-12`）。**役割ルールは D16 で廃止**（global/agents.yaml の backstory に吸収＝D06 §6-3 の ❓ を解消）。残るのは PJ 共通ルール（projects/<key>/rules.md）のみ | ✔（合成）／✖（role_rules 廃止 D16） |
| DM-06 hooks 設定 | VO | `crewai.hooks` の `register_hook(InterceptionPoint.PRE/POST_TOOL_CALL)` への登録（`hooks.py:17-24, 126-127`）。PJ 単位、`agents:` で絞り込み可。フェイルオープン（3-4 B-3 据え置き）の追実測は解除条件のまま | ✔／▲ |

## 2. メンバ台帳（DM-07、実体: 設定ファイル）

実行主体の名前空間 3 系統（bd actor／CrewAI `Agent.role`／casbin `member:`。3-4 X-4）を**台帳 1 つに畳む**（D06-c）。台帳の項目はメンバ ID と人間 actor（単一利用者）。AI メンバの導出元は projects/*/agents.yaml のキー×global（D16。旧 members.yaml）。bd actor（kickoff ごとにメンバ ID を渡す。現行の共通 `crew-exec-01`（`config.py:328`）は廃止）と casbin subject は台帳から**導出**、CrewAI role は表示名で `AgentRegistry`（casbin PoC `identity.py:40`）に写像。**台帳外の actor は「人間」に黙って倒さず印を残す**（P3）。bd actor に `.` を含む名前が使えるかは ▲（D06 §6）。

## 3. 段と設定（DM-08〜DM-12、DM-26）

| DM | 種別 | 説明 | 実体 | 確度 |
|---|---|---|---|---|
| DM-08 段 | VO | `Stage`（`config.py:46-97`）の属性そのまま＋ **`min_items` を段の属性に格上げ、既定は全段 1**（D06-f。現行はグローバル定数 `backlog.py:26` `MIN_ITEMS = 2`）。子 1 件の分解は通すが盤面に観測印（例 `note:single-child`）を残す。段の解決はラベル×親の有無（`config.py:177`（`resolve_stage`））。**テンプレート本文は持たない**（DM-27＝tasks.yaml D16） | コード定数（構造属性。min_items のみ設定で上書き可） | ✔／✖（min_items） |
| DM-09 ペルソナ | VO | 9 種（`personas.py:105-199`）。各ペルソナはただ 1 つの段集合に属す（PROPOSE／EXECUTE、`personas.py:201-215`＝コード定数）。**本文（role/goal/backstory）は global/agents.yaml**（設定ファイル。出荷時既定は settings/defaults/ D16-g。旧 コード定数を D16 で改訂） | キー・段所属＝コード定数／本文＝設定ファイル（D16） | ✔／✖（agents.yaml 化） |
| DM-10 組織テンプレート | — | **廃止（D07-f）。欠番として残す**（採番規約: 番号を再利用しない）。メンバは UC-07 で手動作成し、段ごとの許可ペルソナは DM-08 の allowed_personas が正。WS 分割粒度は出荷時既定 tasks.yaml の本文設計（M1）へ送りのまま（D06-l。送り先の実体は DM-27 D16） | — | — |
| DM-11 LLM プロファイル | エンティティ | openrouter（`qwen/qwen3.7-plus`）／local（`localhost:11434/v1`）の 2 件（D06-d）。base_url／model_id／context_window／api_key の参照名（実値は `.env`）。**送信前閾値 T は context_window から導出**（D06-g。別に宣言しない＝P4）。既定は OpenRouter、ローカルはメンバ単位 opt-in。レビュー用別モデル（renkei `POC_REVIEW_MODEL`、`config.py:347`）は planner ペルソナのメンバ割当で表現 | 設定ファイル＋.env | ✔（2 実体）／✖（プロファイル化） |
| DM-12 上限ポリシー | VO | kickoff あたりの `max_requests`・`max_cost`。**グローバル既定＋プロジェクト単位の上書き（任意）**。段・ペルソナ単位は持たない（D06-g、D04-a）。UC-07 の AI 設定一覧の 1 項目。T はここに持たない | 設定ファイル | ▲ |
| DM-27 段テンプレート | VO | 7 キー（段 5＋review_normal／review_final）の description／expected_output／agent（段の既定ペルソナ・任意）／tools（Task.tools・任意）。実体は global/tasks.yaml＋projects/<key>/tasks.yaml（キー単位上書き）。｛変数｝はシステムが kickoff 時に充填（許可 15＋段別必須の validate D16-c。担当一覧は ｛persona_guide｝ でのみ入る＝P1/P4）。ゲート論理は DM-08（コード）のまま | 設定ファイル（領域 A。出荷時既定は settings/defaults/） | ✖（D16 新設） |
| DM-26 コマンド定義 | VO | `run_command` の許可リスト（casbin PoC `tools.py:140`（`RunCommandTool.ALLOWED`）: 名前 → 固定 argv）を**設定対象に格上げ**し UC-07 で CRUD する（D07-e）。条件: AI が Tool で到達できない設定領域に置く（設定モジュールのみ所有）／argv リスト形式のみ・shell=False・引数は AI に渡させない／validate で権限ポリシーの `cmd:<名前>` オブジェクトと突合（policy の cmd ⊆ 定義、定義の名前は 1 行以上から参照）／単一利用者前提。Casbin 側「誰が」×コマンド定義「何が実行可能か」の二段判定は維持 | 設定ファイル（AI 到達不能領域） | ✖（形は ✔ casbin） |

## 4. タスク集約（実体: beads）

| DM | 種別 | 説明 | 確度 |
|---|---|---|---|
| DM-13 issue | 集約ルート | 盤面の単位。ラベル体系: `kind:*`（段。`config.py`）／`project:<key>`（**新設**、D06-a。子に伝播。伝播経路は `loop.py:1360-1364` の child_labels＋POC_LABEL と同じ）／`persona:*`（`personas.py:28`）／`role:executor`（claim 対象）／`state:working|failing`（`loop.py:68-69`）／`note:*`（観測印。新設）。進行状態の正は盤面（P2）。「子を全て起票し終えてから親を close」の順序不変条件（`loop.py:1547`（`_handle_issue`）） | ✔／✖（project・note ラベル） |
| DM-14 受け入れ条件 | VO | 4 要素（対象・前提・手順・判定）＋判定者等級 M/A/H（付録：受け入れ条件の定義、D03-a）。issue の acceptance_criteria に載る。書式と lint 拡張は作業メモ A-3（未決）。outcome トークンは正準形 `outcome/<ファイル名>`（`backlog.py:185-196`） | ✔（形）／❓（等級の載せ方） |
| DM-15 質問 issue | エンティティ（issue の一種） | `kind:question` のみ付く。タイトル `[要判断] …`、本文 3 キー（`loop.py:927`（`_open_escalation`））。**元 issue が質問に blocks 依存**し claim は解放（create→dep→release）。close で元 issue が ready に浮上（D04-c）。失敗写像には使わない（D06-i） | ✔ |
| DM-16 コメント | エンティティ | actor 付き。種別: 人間指示（次 claim で注入、K-1 行6）／回答（UC-04）／**封筒**（失敗の等級＋種類＋実測値、D06-i）。actor が台帳内かで人間／AI を判定（DM-07） | ✔／✖（封筒） |

## 5. 成果物（DM-17、実体: Obsidian vault）

PJ フォルダ/`outcome/` 配下のみ書込可。境界は `outcome/` の外縁で**構造**として引く（`tools.py:602-614`、P1）。ファイル名の**禁止文字は書込前ゲート**で弾く（P1 ゲート層、OB-12・OB-13、D07-c）。集合は **Windows／mac 互換性のない文字**（`\ / : * ? " < > |`、制御文字、予約語 CON／PRN／AUX／NUL／COM1-9／LPT1-9、末尾のドット・空白）**＋ Obsidian でタイトルに使えない文字**（`# ^ [ ] |`。wikilink を壊す集合。Obsidian 現行版の正確な集合は実装前に照合 ▲）の和集合。`outcome/` 外の既存ファイル編集可否（OB-10）は ❓ のまま。

## 6. 実行（DM-18〜DM-21、実体: インメモリ→beads／構造化ログ）

| DM | 種別 | 説明 | 確度 |
|---|---|---|---|
| DM-18 実行（kickoff） | エンティティ | 外側ループ 1 回分の CrewAI 起動（§8.3。課金・回数の単位）。issue と実行主体メンバ、コンテキスト・計測・失敗イベントを束ねる。結果は成功／UNSAFE（起票・依存張りの途中失敗、自動再試行しない）／停止（上限）。履歴化は DM-24 | ✔ |
| DM-19 実行コンテキスト | VO | 起動種別 human／timer／file／manual（§6-1 伝播箇所 2）、トリガーのスナップショット、ファイル投下時のパス（**投下ファイルは消費しない**、D06-h）、correlation ID（憲章 §6）。**どのモジュールが持ち運ぶかは X2** → D08-d（所有 BC-03）／D09-e（`ExecutionContext` は common の受け渡し型、`orchestrator` が保持、`trigger`／`orchestrator` の入口が組む） | ▲ |
| DM-20 計測 | VO | HTTP transport 層 Interceptor でリクエスト数・コスト・送信前トークンを kickoff ごとに計測（crewai_ai_control P1、7-2 D）。上限は DM-12、閾値 T は DM-11 | ✔ |
| DM-21 失敗イベント | VO | 等級 F／W／I（付録 AI 制御 §3.2(b)、割当は参考程度 R-09）。**bd への写像**: F＝`state:failing`＋claim 解放（close しない）＋コメント封筒／W＝close＋封筒／I＝実行履歴のみ（D06-i）。写像後は確認読取（D04-b）、不一致・bd 不達は停止し構造化ログに痕跡（D06-k）。`LLMCallFailedEvent` の 2 回発火は重複排除（7-2 E） | ✔（検出）／▲（写像） |

## 7. トリガー集約（DM-22〜DM-25）

| DM | 種別 | 説明 | 実体 | 確度 |
|---|---|---|---|---|
| DM-22 トリガー定義 | 集約ルート | 種別 cron／interval／file、spec、起動条件、有効／無効。**既定トリガー「ready 巡回」**（interval・常設・削除不可・間隔と有効/無効のみ変更可、D04-c）。**間隔は本エンティティの属性**（UC-07 には置かない、D06-j）。実行中ジョブを持つトリガーの削除は次回以降の発火停止のみで kickoff は完走（D06-j） | **設定ファイル**（宣言＋起動時再登録・インメモリ jobstore。憲章 §2 を採用、付録 §1 の SQLAlchemyJobStore は不採用。D07-a） | ▲ |
| DM-23 起動条件 | VO | 外側ループの claim フィルタ＝ラベル集合（現行 POC_LABEL の一般化）＋任意の `project_key`（D06-h）。既定トリガーは `role:executor` 全件 | トリガー定義に内包 | ✖ |
| DM-24 実行履歴 | エンティティ | 実行ID・トリガーのスナップショット（名・種別。削除後も追える）・起動種別・開始終了・duration・status・blame（付録 §5） | **ローカル csv に追記するだけ**（憲章 §4「ローカルの作業ファイル・ログは FS 直置き」のログ扱い。ロジックは読み戻さず UI 表示にのみ読む。憲章 §2 の例外列挙が要るかは 4.5 で確認。D07-b。2-2 R12-P1 の解除条件が成立） | ▲ |
| DM-25 watcher 状態 | VO | 生死・次回実行時刻・実行中インスタンス数。daemon の REST 経由でのみ読む（付録 §5、憲章 §4 注記） | インメモリ | ▲ |

## 8. 状態を持つ概念の一覧（工程文書「特に確認したいこと」への回答）

| 実体の所在 | DM |
|---|---|
| beads（外部・オーケストレータ） | DM-13／14／15／16 |
| Obsidian vault（外部・人間所有） | DM-17、DM-02 のフォルダ実体 |
| 設定ファイル（config.json 系・agents.yaml／tasks.yaml・policy・コマンド定義・トリガー定義。UC-07/08/09 の CRUD 対象） | DM-01〜07、DM-09（本文）、DM-11／12／26／27、DM-22／23 |
| コード定数 | DM-08（構造属性）、DM-09（キー・段所属のみ D16） |
| インメモリ | DM-18〜21、DM-25、DM-07 の導出結果 |
| 構造化ログ（憲章 §6。停止痕跡） | DM-18 の停止・DM-21 の不達 |
| ローカル csv（ログ扱い・追記のみ） | DM-24 実行履歴 |
| 廃止・欠番 | DM-10 |

X3 のうち jobstore（D07-a）と履歴（D07-b）は解消。残るのは「UI から CRUD する設定ファイル・追記のみの csv ログ」が憲章 §2「システム固有の永続ストアを持たない」の例外に当たるか、という一般論のみで、フェーズ4.5 のすり合わせで扱う（作業メモ A-0 に登録）。
