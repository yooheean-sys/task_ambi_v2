# handoff_x2 — 付2フェーズ完了の申し送り（付3 へ）

**更新**: 2026-08-19（**付2 完了**。クラスタ 5・IF 被覆 49/49・PATCH-11 提示）
**レジーム**: 消耗品（次フェーズの handoff が置換）。正本は D14（前提・裁定）・`x2_cluster_index.csv`（機械検査の正）・`x2_notes.md`（短文の正本）。`handoff_x1.md` §3 の「付2」項は本書が置換する（付1・付2 は同日に並行作業で進んだ。D14-a）。

## 1. 現在地

| 成果物 | 状態 |
|---|---|
| ★`x2_cluster_01_monitor_mapping.md` | orchestrator・executor・monitor・board（4）。IF-03/08/15/18/20/22/38/39/48。UC-12/13。LLM 偽＋スタブ 8。依存 03 |
| ★`x2_cluster_02_settings_crud.md` | settings・board・vault（3）。IF-09/32。UC-07/08。ダブル 0。依存なし（最初に実行） |
| ★`x2_cluster_03_executor_tools.md` | executor・settings・board・vault・monitor（5）。IF-02/17/29/30/33-35/37/40-43。UC-I1（Tools）/12（Hook）。LLM 偽。依存 02 |
| ★`x2_cluster_04_orchestrator_loop.md` | orchestrator・stages・board・settings・vault（5）。IF 25 件。UC-I1/04/11/03。スタブ CrewExecutor・RunMonitor。依存 02 |
| ★`x2_cluster_05_trigger_fire.md` | trigger・orchestrator（2。範囲外理由あり）。IF-49。UC-09/10/11。スタブ 9。依存 04 |
| `x2_cluster_index.csv` | 17 列・CRLF。主検証 IF 49/49・重複なし |
| `x2_notes.md` | 規則・共起マトリクス・IF→クラスタ割当・除外 UC・憲章の穴・未実測 |
| `x2_constitution_patch.md` | **PATCH-11**（§8 統合テストに「置き場＝apps/api/tests/integration/」「LLM は模擬」「スタブは差し替え点から」）。**未適用**。REV-08 への応答 |
| `x2_selfcheck.py` | `python3 x2_selfcheck.py` → ERRORS: none |
| D14 | 裁定 a〜h（Q1〜Q9）・事実・未決 |

実行順（番号とは別）: **02 → 03 → 01 → 04 → 05**。番号は p6 csv の外部依存件数順（D14-f）。

## 2. ユーザ作業

- **憲章 fullversion の編集（未）**: PATCH-7（第6）→ C-5 の 2 点 → PATCH-8／9／10（付1）→ **PATCH-11（付2）**。適用後に `cd outcome/design/x1 && python3 x1_selfcheck.py --extract && python3 x1_selfcheck.py` → ERRORS: none
- PATCH-11 を適用しない裁定なら x2 の前提は据え置きのまま（設計は不変。D14 §6-6）

## 2-b. 憲章への申し送り（付1 の REV との対応）

- REV-08（統合テストの置き場）: 付2 で `apps/api/tests/integration/cluster_NN_<slug>/` に確定 → PATCH-11 で §8 に写した
- LLM をテストで呼ぶか否か（付2 で見つけた穴。REV には無い）: PATCH-11 に同居
- REV-14（§8 例示の git リポジトリ）: 例示のため触らない（付2 でも git は使わない）
- REV-11（correlation 発番はミドルウェアに一本化）: 各クラスタ §6 の「構造化ログ」観点は「HTTP 入口＝middleware 発番／非 HTTP 入口＝その入口で発番」で書いた（整合）

## 3. 次にやること（付3 → M1）

1. **付3（設定の 3 層仕分け）**: INPUT は p5 feature_list・`p6_external_interface.csv`（EIF-08 .env・EIF-09 config.json）・憲章 §5・環境情報ファイル。`../phase6/handoff_p6.md` §2-3 の (a)(b)(c)＋付1 からの申し送り（REV-13 ui プロセスの設定／REV-07 領域 A/B は UI からのみ編集の運用規約／api_key_ref の命名規約）。**付2 からの申し送り**: テスト用の .env／config（一時 `VAULT_ROOT`／`SETTINGS_ROOT`／`TRIGGER_ROOT`・偽サーバの `base_url`・`bd_bin`・`watcher_enabled`・poll 間隔・`stable_seconds`・`max_requests` 等）を (b) 環境依存／(c) 挙動のどちらの層で差し替えるかを決める（クラスタ 01・03・05 の fixture が値を差し替える）。ルート config.json の「有効化するモジュール一覧」がクラスタの構築範囲を決める（D14-e）
2. **M1 への要求（付2 発）**: (1) apps/api: build 連鎖を関数に切り出し、有効 MOD 一覧と Protocol 実装の override を引数で受ける（D14-e・D14 §6-1）(2) LLM 偽サーバ（ループバック HTTP・OpenAI 互換・usage／finish_reason／tool_calls・台本）（D14 §6-2）(3) `BoardWriteUnverified` の故障注入点（D14 §6-3）(4) CI の bd バイナリ（D14 §6-4）(5) 未実測: openrouter 経路の Hook／送信前トークン推定／PollingObserver R12-P4／BackgroundScheduler ＝ **閉じた**（P5D-041＝`p5_trigger_notes.md` §7 の 2。専用スレッドで回るので FastAPI のループを塞がない。停止時の `shutdown(wait=True)` のみ実測 0.951 秒。`012` の実配線＝クラスタ 05 の 18 本でも発火の合間に HTTP の口が応答することを確認）（D14 §6-5）
3. **付1・付2 の順序**: 工程文書は付1→付2 だが、実際は並行に進み付2 の D 番号を付1 の後（D14）に振り直した。付3 は D15 から

## 4. 事実・気づき

- **付1 が並行作業で同日完了していた**（x1/ は質疑の後に作成）。質疑の時点には無かったため Q1 で「付1 未了」を前提に裁定し、成果物作成中に判明して D13→D14・前提 a・h を改めた（Q9）。**着手時に `outcome/設計判断_索引.md` を読み直して次の D 番号を確かめること**（並行作業があり得る）
- 共起は `p5_selfcheck.py` の `PART_RE` で機械抽出でき、手で数えた契約 csv の提供→利用の対（14 種・49 行）と一致した
- クラスタの「主検証 1 つ」ルールは selfcheck で 49/49 を機械検査できる。通過 IF は「両端がクラスタ内の実装」に限ると 5 クラスタ中 01 の Hook 4 本だけになった
- 並び順の機械計上は settings が居るだけで EIF-10（UI の HTTP）が数えられる過大計上を含む。「実際に通す EIF」列で補った（D14-f）
- 憲章 §8 は「外部ツール実物」を要求し LLM に触れていない。テストダブルの語彙は「スタブ」1 語（§1・§7-6・§8）。付2 の 3 種（外部模擬／HTTP 入口代替／Protocol スタブ）は憲章の語彙を超えるので PATCH-11 で §8 に 1 句置いた
- PoC 無変更: `git -C pocs/<repo> status --porcelain | grep -v '^??'` は開始時と同一（renkei `D` 5 行・casbin `D` 3 行）
