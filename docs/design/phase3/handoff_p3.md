# handoff_p3 — 第3フェーズ完了、第4フェーズへの申し送り

**作成**: 2026-08-18（第3フェーズ設計セッション末）
**レジーム**: 消耗品（§8.2「申し送り」: 新版が旧版を置換。恒久知識は設計メモ・D 記録側が持つ）
**正本の所在**: 裁定は [D08](./設計判断_V4-D08_フェーズ3前提とBC境界の裁定.md)、成果物は本フォルダ、未決は D08 §6 と作業メモ A 節。本書はそれらの要約であり、食い違えば正本が勝つ。

## 1. 一言でいうと

第2フェーズの集約境界（実体の違い）を出発点に、憲章 §2「外部ストア領域ごとに所有 1 つ」を検査基準として **6 つの Bounded Context** に切った。DM 25 件（DM-10 欠番除く）・FN 53 行（廃止除く）・外部ストア領域 6 件の所有 BC を一意に決め、持ち越しの X2（実行コンテキストの持ち運び）・D01 §6-2（モジュール境界）・実物照合ゲート・DM-26 の置き場を裁定した。未配置 FN の M 配置はフェーズ4 の依存関係図を見てから §6-1 を改訂する。

## 2. 成果物（`outcome/design/phase3/`）

| ファイル | 内容 | 検証 |
|---|---|---|
| ★`p3_bounded_context.mmd` | flowchart TB + subgraph。BC-01〜06 に所有 DM を内包、太線＝ストア所有・破線＝BC 間参照（ラベル＝何を引くか）・common 共有カーネル・アクターと外部システム | mmdc 描画 ✔ |
| `p3_bounded_context_notes.md` | 図内短文の正本。BC ごとの切った基準 1 行・所有 DM・所有ストア・UI（§1）、Q1〜Q8 の裁定（§2）、参照＝Protocol 候補（§3）、共有カーネル（§4）、ストア所有一覧（§5）、FN 分布（§6） | — |
| `p3_bc_ownership.csv` | 列 `id,種別(STORE|DM|FN),名称,所有BC,参照BC(;区切り),根拠`。84 行 | DM 25 件が唯一の所有 BC／STORE 6 件／FN 53 件／図の subgraph 所属と csv の一致 を機械検査 ✔ |
| `設計判断_V4-D08_フェーズ3前提とBC境界の裁定.md` | D08-a〜h＋根拠＋棄却＋事実＋要求する改訂＋決めていないこと 7 件 | append-only |
| `../../設計判断_索引.md` | D08 行を追加 | — |

## 3. BC の要約（正本は notes §1・D08-a）

| BC | 所有 DM | 所有ストア | 基準 |
|---|---|---|---|
| BC-01 設定 | DM-01〜07/11/12/26 | 設定ファイル領域 A | データ所有権（人間が CRUD・次回 kickoff から効く）。enforce・resolve_command・メンバ解決・台帳照合を Protocol で提供 |
| BC-02 盤面 | DM-13〜16 | beads | 外部依存の単位（指定ツール）。children_of・open_question_of・確認読取＝D01 判定の出所 |
| BC-03 分解・遂行 | DM-08/09/18/19 | なし（LLM 呼び出し主体） | ライフサイクル 1 kickoff。外側ループ・段・ゲート鎖・CrewAI 実行器・回復層 |
| BC-04 成果物 | DM-17 | Obsidian vault | 外部依存の単位（vault 領域）＋P1 ゲート |
| BC-05 実行監視 | DM-20/21 | なし | 外部依存の単位（transport・イベント）＋再利用資産＋等級表の変更頻度 |
| BC-06 トリガー | DM-22〜25 | 設定ファイル領域 B・履歴 csv・DL フォルダ | ライフサイクル（daemon 常駐） |

依存方向: **BC-06 → BC-03 → BC-01／02／04／05、BC-05 → BC-02、BC-01 → BC-02／04。循環なし。** 共有カーネル（common）: persona_key・段名の列挙／DM-19 受け渡し型／correlation_id ログ基盤／Protocol 群。

## 4. 第4フェーズ（モジュール依存関係図・通信契約）への入力と論点

工程文書: INPUT＝`p3_bounded_context.mmd`・`p2_domain_model.mmd`・`p2_sequence_*.mmd`・constitution（§5 fullversion が設計入力、D02-i）・本書。OUTPUT＝★`p4_module_dependency.mmd`（C4 L3・Protocol + DI・composition root 明示・features 間直接矢印なしを自己検証）＋`p4_module_contract.csv`（`if_id,提供モジュール,利用モジュール,Protocol名,メソッド名,入力,出力,例外,同期/非同期,source_id(rel_id)`）＋import-linter contract 案（pyproject.toml 断片）。完了条件は D05-c／D08-h と同型で着手時に D 記録で定める。

論点:
1. **BC → MOD 分割**（BC:MOD ≠ 1:1 の対応表）。BC-03 は確実に複数 MOD（候補: 外側ループ＋回復層／段テンプレート・ペルソナ／CrewAI 実行器・Tools／ゲート群）。BC-01 も casbin（validate・enforce）と PJ・メンバ・LLM・上限で割れる可能性。憲章 §1 の 800〜1,200 行目安で判断
2. **Protocol の粒度と名前**（notes §3 の候補から）。特に BC-02 の盤面 Protocol（claim／create／dep／release／comment／close／show／children_of／open_question_of／確認読取）と BC-01 の設定 Protocol（読取・メンバ解決・台帳照合・enforce・resolve_command）
3. **ラベル語彙の定数の置き場**（D08 §6-1: BC-02 か common か）
4. **LLM 呼び出し経路**（D08 §6-2: 憲章 §3「LLM クライアントのラッパは common」と BC-05 Interceptor・CrewAI クライアントの関係。composition root での差し込み）
5. **DM-19 の貫通の実装形**（contextvar の置き場＝common ログ基盤、BC-06 → BC-03 の起動 Protocol の形、human 起動の入口）
6. **UI の配線**: BC-01・BC-06 の Streamlit ページは自モジュールの HTTP API 経由（憲章 §4 注記）。ui プロセスは器のみ
7. **未配置 FN の M 配置**（§6-1 改訂。フェーズ4 の依存が出てから）: 未配置 26 行の BC 分布は BC-01: 13／BC-03: 6／BC-05: 5／BC-04: 1／BC-02: 1。BC-03 の kickoff は BC-01 の Protocol（メンバ解決・設定読取・上限・LLM プロファイル）を必ず要するので、**BC-01 の最小実装（プロジェクト・メンバ・LLM プロファイル・上限）は M1 の入口条件に近い**。casbin enforce・コマンド定義・hooks は後段に回せる

## 5. 未決（正本は D08 §6・D06 §6・D07 §5・作業メモ）

D08 §6 の 7 件（ラベル語彙の定数／LLM 呼び出し経路／BC→MOD／Protocol 粒度／D06・D07 の残り／FN-14 の所有暫定／未配置の M 配置）。作業メモ C 節（事実の食い違い: §0-3「再検証不能」は誤り 等）は**本セッションでも未裁定**（D05-g・D08 でも触っていない）。次回冒頭で裁定を求める。

## 6. 本セッションでの副次的な処置

- 工程文書の改名を検出（2026-08-18）。§7.2 L 節を新名に改訂、D05 に追記（append-only）、`_変更履歴.md` に記帳
- §6-1 M1 議題表の実物照合ゲート行に「裁定済み → D08-f」を追記。M1 初回議題 4 件は全て裁定済み
- 作業メモ: A-0（フェーズ3 入力）・A-1（M1 議題の残り）・A-2-2（モジュール境界）を消し、A-0 をフェーズ4 の頭出しに置換
- PoC 無変更: `pocs/*` は読取のみ。`git -C pocs/<repo> status --porcelain | grep -v '^??'` はセッション開始時と同一（既存の `D` 行は作業メモ D-5 の事前状態）
- 罠の記録（作業メモ E）: flowchart は LR だと横に伸びすぎ TB が読みやすい／subgraph 内 `direction TB` は外向き辺があると無視される（既知の mermaid 挙動）
