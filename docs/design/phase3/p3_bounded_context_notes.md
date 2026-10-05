# Bounded Context 説明（第3フェーズ）

**位置付け**: `p3_bounded_context.mmd` の説明の正本（図内は短文）。所有／参照の対応表（機械検査用）は `p3_bc_ownership.csv`。裁定は [設計判断_V4-D08](./設計判断_V4-D08_フェーズ3前提とBC境界の裁定.md)。BC-nn は採番規約（追記のみ・欠番不埋め・再利用禁止）に従う。
**出発点**: `../phase2/p2_domain_model_notes.md` §0 の集約境界（実体の違い＝beads／vault／設定ファイル／インメモリ／csv）。集約は BC をまたいで割れていない（7 集約 → 6 BC。「段と設定」集約だけは DM-08/09（コード定数）を BC-03 に、DM-11/12/26（設定ファイル）を BC-01 に分けた。理由は §2 の Q2/Q3。**P5-R1（D16）で DM-09 の本文と DM-27 段テンプレートが設定ファイル（agents.yaml／tasks.yaml）になり BC-01 所有へ移動**。BC-03 には DM-08 の構造属性と段所属＝コード定数が残り、本文は引数で受ける）。
**BC と MOD の関係**: BC はドメイン言語の境界。憲章 §1 の行数上限（1 モジュール 800〜1,200 行）に対する MOD 分割はフェーズ4 で行い、BC:MOD ≠ 1:1 を許す（工程文書フェーズ4「対応表を併記」）。BC-03 が確実に複数 MOD になる。

## 1. BC 一覧と切った基準（工程文書「分割の判断基準」への回答）

| BC | 名称 | 切った基準（1 行） | 所有 DM | 所有する外部ストア／領域 | UI |
|---|---|---|---|---|---|
| BC-01 | 設定 | **データ所有権**: 人間が UC-07/08 で CRUD し、次回 kickoff から効く設定の集合。外部依存 casbin（validate・enforce）もここに閉じる | DM-01〜07/09（本文）/11/12/26/27 | 設定ファイル領域 A（PJ・agents.yaml＝メンバ・ペルソナ本文・tasks.yaml＝段テンプレート・policy・rules・hooks・LLM プロファイル・上限・コマンド定義 D16）。`.env` の api_key は DM-11 の参照名として本 BC の Settings が読む | 設定 UI（UC-07/08 の Streamlit ページ） |
| BC-02 | 盤面 | **外部依存の単位**: 憲章 §2 の指定ツール beads（乗り換え前提＝アダプタ分離）。盤面導出（`children_of`／`open_question_of`／確認読取）＝D01「判定の出所」もここに 1 箇所 | DM-13/14/15/16 | beads（bd CLI 経由のみ。内部 Dolt は触らない） | なし（beads-ui は外部システム） |
| BC-03 | 分解・遂行 | **ライフサイクル**: 1 kickoff（claim → 段解決 → propose／execute → ゲート → 起票／close → 確認読取）。段テンプレート・ペルソナ本文は D16 で BC-01 の領域 A へ（変更は設定編集になった） | DM-08/09（段所属）/18/19 | なし（インメモリ）。LLM API の呼び出し主体（CrewAI 実行器） | なし |
| BC-04 | 成果物 | **外部依存の単位**: vault 領域（PJ 入力・役割フォルダの読取、PJ フォルダ/outcome/ の書込・存在検証）の所有を 1 つに。P1 ゲート（外縁・禁止文字・不在検出）を構造で持つ | DM-17 | Obsidian vault（/mnt/c 経由） | なし（Obsidian は外部システム） |
| BC-05 | 実行監視 | **外部依存の単位＋変更頻度**: LLM HTTP transport（Interceptor）と CrewAI イベントバスという「戻り値以外の観測点」。等級表（F/W/I）は参考程度で改訂され得る。再利用資産 crewai_ai_control | DM-20/21 | なし（写像先 beads は BC-02 経由、I は BC-06 の履歴へ） | なし |
| BC-06 | トリガー | **ライフサイクル**: daemon 常駐＝kickoff より長い。APScheduler／watchdog／REST という別の外部依存群 | DM-22/23/24/25 | 設定ファイル領域 B（トリガー定義）、実行履歴 csv（追記のみ）、DL フォルダ（監視・読取のみ） | トリガー UI（UC-09/10 の Streamlit ページ） |

UI について: 憲章 §2 の垂直スライスに従い、独立した UI BC は置かない。UI を持つのは BC-01・BC-06 のみ。BC-02/04 の人間向け画面は外部ツール（beads-ui／Obsidian）が担う。

## 2. 所有を決めた論点（Q1〜Q8。詳細・棄却案は D08）

| Q | 論点 | 裁定 |
|---|---|---|
| Q1 | BC の全体構成 | 6 BC。分解・遂行は 1 BC（外側ループ・段・ゲート・CrewAI 実行器・回復層）。実行監視は分離 |
| Q2 | DM-08/09 段・ペルソナ | **当初裁定（D08-b）**: 所有 BC-03。`persona_key`・段名の**列挙型のみ common**に置き、プロンプト（role/goal/backstory・段テンプレート）・allowed_personas・min_items は BC-03。**P5-R1（D16）で一部改訂**: role/goal/backstory（DM-09 本文）と段テンプレート（DM-27）は **BC-01 所有の領域 A（agents.yaml／tasks.yaml）**。BC-03 は本文を**引数で受ける**（Member・TaskTemplates）ので D08-b の 2 棄却案（「BC-01 所有→テンプレートの置き場が割れる」「Protocol で引く→双方向依存」）はどちらも起きない: 置き場は領域 A の 1 箇所、依存は従来どおり BC-03→BC-01 の一方向のみ。allowed_personas・min_items（config.json 上書き。UC-07 対象外）・段所属は BC-03 のまま。**BC-01 → BC-03 の依存を作らない**は維持（validate は kernel の PersonaKey・ToolName 列挙で足りる D16-b/f） |
| Q3 | 設定 BC の一体性 | **一体**。enforce（`authorize`）と `resolve_command(name)→argv` は BC-01 が Protocol で提供。DM-26 の置き場＝BC-01 所有の設定ファイル領域 A（BC-03 の Tool は Protocol 経由でしか届かない＝D07-e (1) の「AI 到達不能」を構造で満たす）。DM-07 台帳は BC-01 が保存時に導出して所有、FN-10 の actor 判定は BC-03 が BC-01 Protocol で照合 |
| Q4 | X2（DM-19 の持ち運び） | **所有 BC-03**（DM-18 の一部）。発番＝起動元（BC-06、human 起動は BC-03 の入口）。型＝common/interfaces の受け渡し型。correlation_id は common のログ基盤（contextvar）で貫通し、BC-02/04/05 の Protocol 引数に持たせない。DM-24 の trigger_snapshot は DM-22 の**値コピー**（BC-06 所有。削除後も残る D06-j(2) を構造で満たす） |
| Q5 | 回復層と判定の出所 | recover_orphans＝**BC-03**（外側ループ起動時。`state:working` の意味と再投入方針を知るのは BC-03）。`children_of`／`open_question_of`／確認読取＝**BC-02 Protocol**（回復層・分解層の両方が同じ 1 メソッドを呼ぶ＝D01 の P4 整合） |
| Q6 | 実物照合ゲート（FN-52） | **否・据え置き**（解除条件: 実タスク投入で V3 同型の実害が再現）。置くなら BC-03 の完了ゲート鎖（outcome トークン→実ファイル存在の次段）＋BC-04 の識別子抽出 Protocol。「成果物が使ったソースを主張させ、その実在を検査する」二重宣言型は M1 の段テンプレート設計へ（D06-l と同じ置き場） |
| Q7 | 未配置 FN | 本フェーズは BC 配置のみ（csv）。M3 以降の配置はフェーズ4 の依存関係図を見てから §6-1 を改訂 |
| Q8 | 成果物・完了条件 | 本ファイル群。完了条件は D08-h |

## 3. 参照（非所有）の主なもの

| 参照する BC → 参照先 | 引くもの（Protocol の候補。定義はフェーズ4） |
|---|---|
| BC-06 → BC-03 | 外側ループの起動（DM-19 を渡す）→ 結果（DM-18 の status／result／I 級の付記）を受ける |
| BC-03 → BC-01 | 設定読取（PJ 対応付け・メンバ・LLM プロファイル・上限・ルール・hooks）、メンバ解決（project × persona → メンバ ID／bd actor／表示名）、台帳照合（actor が人間か）、enforce、resolve_command |
| BC-03 → BC-02 | claim（起動条件のラベルフィルタ）・show・create・dep add・release・comment・close・children_of・open_question_of・確認読取 |
| BC-03 → BC-04 | コンテキスト読取（PJ・役割フォルダ・兄弟成果物）、outcome 書込（ゲート付き）、read_outcome_file、存在検証 |
| BC-03 → BC-05 | kickoff 開始（上限 T／回数／コストを渡す）、終了（失敗イベントの集約） |
| BC-05 → BC-02 | 失敗写像（F＝state:failing＋claim 解放＋封筒／W＝close＋封筒）と確認読取 |
| BC-01 → BC-02 | 削除ガード（`project:<key>` の open issue の有無を読むだけ） |
| BC-01 → BC-04 | 登録・編集時のフォルダ存在確認 |

依存方向は BC-06 → BC-03 → BC-01／02／04／05、BC-05 → BC-02、BC-01 → BC-02／04 で**循環なし**（憲章 §3 補足「インスタンスレベルの相互依存は設計見直しのシグナル」に当たらない）。BC-05 の Interceptor を BC-03 の LLM 呼び出しに差し込む配線は composition root（フェーズ4）。

## 4. 共有カーネル（common に置くもの。BC ではない）

| 置くもの | 理由 | 憲章上の根拠 |
|---|---|---|
| `persona_key`・段名の列挙型 | BC-01 がメンバ作成で要り、BC-03 が所有する。双方向依存を避ける | §7-5「全モジュール共用の基盤・型定義」 |
| DM-19 実行コンテキストの受け渡し型 | BC-06 が組み立て BC-03 が保持する | §1「受け渡し用 Pydantic 型は common/interfaces」 |
| correlation_id のログ基盤（contextvar） | 憲章 §6 のミドルウェアと同じ発想。HTTP を通らない起動（timer／file）でも貫通させる | §6 |
| モジュール間 Protocol | フェーズ4 で定義 | §2 |

置かないもの: プロンプト本文（段テンプレート・ペルソナ本文＝BC-01 の領域 A が持ち BC-03 が引数で受ける D16）、ラベルの意味論、ゲートのロジック（common を物置にしない §7-5）。列挙は PersonaKey・StageName に加えて ToolName（D16-b）。

## 5. 外部ストア・外部システムの所有一覧（憲章 §2 の検査）

| ストア／領域 | 所有 BC | 他 BC からのアクセス |
|---|---|---|
| 設定ファイル領域 A | BC-01 | Protocol 経由のみ（BC-03） |
| 設定ファイル領域 B（トリガー定義） | BC-06 | なし |
| beads | BC-02 | Protocol 経由のみ（BC-03／05／01） |
| Obsidian vault | BC-04 | Protocol 経由のみ（BC-03／01） |
| 実行履歴 csv | BC-06 | なし（UI 表示は BC-06 自身） |
| DL フォルダ | BC-06（監視・読取のみ） | なし |
| LLM API（ストアではない） | 呼び出し主体は BC-03、transport 計測は BC-05。憲章 §3 は「LLM クライアントのラッパ」を common に置くとしており、Interceptor との関係はフェーズ4.5 で突合（D08 §6） | — |

## 6. FN の BC 分布（`p3_bc_ownership.csv` から。53 行）

BC-03: 19／BC-01: 16（P5-R1 で FN-56/57/58 追加 D16）／BC-06: 8／BC-02: 5／BC-05: 5／BC-04: 3。優先度「未配置」26 行の分布は BC-01: 13／BC-03: 6／BC-05: 5／BC-04: 1／BC-02: 1。→ 未配置の半分は設定 BC に集まり、BC-03 の kickoff は BC-01 の Protocol（メンバ解決・設定読取）を必ず要するので、**BC-01 は M1 の入口条件に近い**（M 配置の裁定材料。フェーズ4 後に §6-1 改訂）。
