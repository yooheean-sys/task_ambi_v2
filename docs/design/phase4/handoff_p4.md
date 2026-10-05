# handoff_p4 — 第4フェーズ完了、第4.5フェーズへの申し送り

**作成**: 2026-08-18（第4フェーズ設計セッション末）
**レジーム**: 消耗品（§8.2「申し送り」: 新版が旧版を置換。恒久知識は設計メモ・D 記録側が持つ）
**正本の所在**: 裁定は [D09](./設計判断_V4-D09_フェーズ4前提とモジュール分割の裁定.md)、成果物は本フォルダ、未決は D09 §6 と作業メモ A 節。本書はそれらの要約であり、食い違えば正本が勝つ。

## 1. 一言でいうと

第3フェーズの 6 BC を **8 MOD**（`settings`／`board`／`orchestrator`／`stages`／`executor`／`vault`／`monitor`／`trigger`。BC:MOD ≠ 1:1 は BC-03 のみ）に落とし、依存を全て common/interfaces の **14 Protocol**（提供 MOD 単位に 1 ファイル・役割別）に向けた。feature 間の直接辺は 0 本、composition root は apps/api、通信契約は csv 49 行、import-linter は憲章の 2 契約＋外部パッケージ forbidden 案。質疑中に **BC-01 の 2 分割を撤回**（UC-07 validate の相互依存）し、**LLM 構築は executor・common は差し込み点契約のみ**（憲章 §3 と食い違う → 4.5 へ）と裁定した。

## 2. 成果物（`outcome/design/phase4/`）

| ファイル | 内容 | 検証 |
|---|---|---|
| ★`p4_module_dependency.mmd` | flowchart TB。apps（api＝composition root・ui）／features 8 MOD／common（interfaces 7 ファイル・kernel）／外部システム 5。太線＝implements・破線＝uses・実線＝配線/HTTP/外部。BC:MOD 対応表を末尾コメントに | mmdc 描画 ✔・feature→feature 直接辺 0 本 ✔ |
| `p4_module_dependency_notes.md` | MOD 一覧（責務・所有・概算行数・使う/提供 Protocol）、Protocol 一覧、主要 UC が Protocol をどう通るか、composition root の構築順、自己検証、p3 からの差分 | — |
| `p4_module_contract.csv` | 列 `if_id,提供モジュール,利用モジュール,Protocol名,メソッド名,入力,出力,例外,同期/非同期,source_id(rel_id)`。49 行（1 行＝1 メソッド×1 利用者） | 図との相互被覆・REL 10 系統の被覆を機械検査 ✔ |
| `p4_importlinter.toml` | pyproject.toml 断片。(1) independence (2) layers (3) forbidden 案（crewai／casbin／apscheduler・watchdog／httpx／streamlit／ui→service） | MOD 名一致を機械検査 ✔ |
| `p4_selfcheck.py` | 上記の機械検査（`python3 p4_selfcheck.py` → ERRORS: none） | — |
| `設計判断_V4-D09_…md` | D09-a〜j＋根拠＋棄却＋事実＋要求する改訂＋決めていないこと 9 件 | append-only |
| `../../設計判断_索引.md` | D09 行を追加 | — |

## 3. MOD と依存の要約（正本は notes §1・§4、D09-a/f）

| MOD | BC | 提供 Protocol | 使う Protocol |
|---|---|---|---|
| settings | BC-01 | SettingsReader・MemberResolver・Authorizer・CommandRegistry | BoardReader（削除ガード）・VaultReader（フォルダ存在） |
| board | BC-02 | BoardReader・BoardWriter | — |
| orchestrator | BC-03 | KickoffRunner | Board R/W・SettingsReader・MemberResolver・VaultReader・RunMonitor・StageCatalog・ProposalGate・CrewExecutor |
| stages | BC-03 | StageCatalog・ProposalGate | —（純粋） |
| executor | BC-03 | CrewExecutor | BoardReader.show・BoardWriter.comment・VaultReader・OutcomeWriter・Authorizer・CommandRegistry・LLMHttpHook（構築時注入） |
| vault | BC-04 | VaultReader・OutcomeWriter | — |
| monitor | BC-05 | RunMonitor・LLMHttpHook（＋RunHalted） | BoardReader・BoardWriter（写像・確認読取） |
| trigger | BC-06 | — | KickoffRunner |

構築順（apps/api）: **board・vault → settings → monitor → stages・executor → orchestrator → trigger**。循環なし。

## 4. 第4.5フェーズ（憲章とのすり合わせ）への入力と論点

工程文書 4.5: INPUT＝constitution（§5 fullversion）・`p1_context.mmd`（本リポジトリでは `phase1/01_context.mmd`）・`p3_bounded_context.mmd`・`p4_module_dependency.mmd`・`p4_module_contract.csv`。OUTPUT＝`p45_constitution_diff.csv`（列 `diff_id,憲章の条項,該当する設計要素(ID),矛盾の内容,影響度,推奨する解決方向,理由`）＋裁定を `decision_log.csv` に DEC-*。対象は「構造に関わる決定」に限定。差分表を全件出してから影響度順に 1 件ずつ。

**冒頭タスク（D09-j）**: 未配置 FN 26 行の M 配置（§6-1 改訂）。材料:
- 依存順（§3）から、**M の入口条件は board・vault → settings → monitor → stages・executor → orchestrator → trigger** の順に満たされる。既存の M1（分解・遂行の再構築）＝orchestrator・stages・executor＋その依存（board・vault・settings 最小・monitor 最小）、M2（トリガー）＝trigger
- 未配置 26 行の MOD 別分布: **settings 13**（FN-15/16/17/19/20/21/23/24/25/27/53/54/55）／**monitor 5**（FN-40/41/42/43/44）／**orchestrator 4**（FN-10/14/22/26）／**executor 1**（FN-18。settings の Authorizer・CommandRegistry と対）／**board 1**（FN-45。確認読取は monitor が使う）／**vault 1**（FN-12）／**据え置き 1**（FN-52）
- orchestrator の kickoff は settings の `MemberResolver.resolve`・`SettingsReader.get_project/get_limits/get_rules/get_hooks` を必ず要する → **settings の最小実装（PJ・メンバ・LLM プロファイル・上限）は M1 の入口条件**。casbin enforce・コマンド定義・hooks・validate 逆方向は後段に回せる（handoff_p3 §4-7 と同じ結論を Protocol 単位で裏付け）

**憲章との突合候補**（D09 §5-5。差分表の種）:
1. 憲章 §3「common ＝ … LLM クライアントのラッパ …」× D09-b（LLM 構築は executor、common は `LLMHttpHook` 契約のみ）→ 憲章側改訂の推奨
2. 憲章 §1 の import-linter 契約 (1)(2) × D09-g の (3) 外部パッケージ forbidden → 取り込むか
3. 憲章 §1 の行数目安 × D09-a（settings ≈2,500〜3,000 行の受容）→ 運用注記にするか
4. 憲章 §2 の永続ストア例外列挙 × 設定ファイル領域 A/B・実行履歴 csv（D07-a/b、D07 §5-2）→ 例外に列挙するか「外部ストア」扱いか
5. 憲章 §4 注記「UI は自モジュールを HTTP 経由で操作」× human 起動 `POST /kickoffs`（orchestrator の router）→ 整合（問題なし）の確認
6. 憲章 §6 correlation ID ミドルウェア × D08-d/D09-e の contextvar（HTTP を通らない起動でも貫通）→ 整合の確認
7. 憲章 §3 補足「インスタンスレベルの相互依存」× 構築順（notes §4）→ 整合の確認

## 5. 未決（正本は D09 §6・D08 §6-5・作業メモ）

D09 §6 の 9 件（M 配置／CrewEventSink の要否／Hook の kickoff 帰属の contextvar 実測／`POST /kickoffs` の状態参照 API／`build(deps)` シグネチャ／受け渡し型の属性／D06・D07 の残り／FN-14 所有／作業メモ D-5）。作業メモ C 節は D09-i で裁定・改訂済み（C 節は空）。D-5 のみ据え置き。

## 6. 本セッションでの副次的な処置

- 質疑 9 問（Q1 9 MOD → Q6 で 8 MOD に見直し）。裁定は D09、計画は plan mode の AskUserQuestion で 1 問ずつ
- 作業メモ C-1〜C-4・D-2 を D09-i で一括裁定し、§0-2・§0-3・設計原則 P4・7-2 H を改訂（`_変更履歴.md` に記帳）。作業メモから該当行を消し、A-0 を 4.5 の頭出しに置換
- PoC 無変更: `pocs/*` は読取のみ。`git -C pocs/<repo> status --porcelain | grep -v '^??'` はセッション開始時と同一（既存の `D` 行は作業メモ D-5 の事前状態）
- 罠の記録（作業メモ E）: flowchart TB でも features 8 ノード＋interfaces 7 ノード＋外部 5 ノードが横一列に並ぶため描画幅 ≈2,400px（3 段は上→下に読める）。subgraph 内 `direction LR` は外向き辺があると無視される（既知）。subgraph を辺の端点にできる（`API --> FEATURES`）ので配線 8 本を 1 本に畳めた
