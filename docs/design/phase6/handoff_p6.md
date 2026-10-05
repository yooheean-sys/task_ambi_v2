# handoff_p6 — 第6フェーズ完了の申し送り（付1 → 付2 → 付3 へ）

**更新**: 2026-08-19（**第6フェーズ完了**。外部インターフェース 13 行・配置図・openapi 25 paths／48 operations・PATCH-7 提示）
**レジーム**: 消耗品（次フェーズの handoff が置換）。正本は D12（前提・裁定）・`p6_external_interface_notes.md`（短文の正本）。`handoff_p5.md` §2「次にやること」は本書が置換する。

## 1. 現在地

| 成果物 | 状態 |
|---|---|
| ★`p6_external_interface.csv` | EIF-01〜13（相手×方向。Y＝EIF-01 beads・EIF-03 LLM API）。承認済み |
| ★`p6_deployment.mmd` | flowchart LR。WSL2（app／ui／FS／journald／beads-ui）＋Windows 側（ブラウザ・Obsidian・PowerShell FSW・/mnt/c）＋外部サービス（LLM）。承認済み・mmdc ✔ |
| `openapi.yaml` | OpenAPI 3.1。apps/api 1＋settings 16＋orchestrator 3＋trigger 5 paths。承認済み |
| `p6_external_interface_notes.md` | EXT-01〜13 の表（ID の正本）・除外表・列の読み方・LLM Y の読み方・配置図／openapi の読み方・未決 |
| `p6_constitution_patch.md` | **PATCH-7**（§2 指定ツールに LLM バックエンド／§4 LLM と通信プロトコルを HTTP 非依存に）。**未適用** |
| `p6_selfcheck.py` | `python3 p6_selfcheck.py --render` → ERRORS: none |
| D12 | 裁定 a〜g・事実・未決 |

## 2. 次にやること（付1 → 付2 → 付3）

1. **付1（憲章適合の総当たり）**: 憲章 §0〜§11 × P1〜P6 全成果物。裁定済み（DEC-001〜015）は再質問しない。**判定は PATCH-6・PATCH-7 適用後の文言で**（適用はユーザ。未適用なら「適用前提で判定し適用を前提条件に書く」か「適用しない裁定」を先に取る）。第5・第6 からの要判断: P5D-004（bd ロック待ち × §10）・P5D-025（acceptance 書式 × D03-a）・P5D-043/044（履歴書換え。PATCH-6）・**D12-d（LLM Y × §2 指定ツール・§4 HTTP 前提。PATCH-7）**・openapi の 409（削除ガード）と憲章 §10 の整合（問題なしの見込み）。憲章の適用漏れ 2 点（作業メモ C-5）。2-2 改訂（R09-P1・R12-P5 解除、R10-P1・R12-P1）。候補リストは種であって網羅ではない → 条項ごとに全項目スイープし「なし」行も残す（D10 の型）。p45_selfcheck の流用可
2. **付2（統合テストのクラスタ）**: INPUT は p5 主図・p2 シーケンス・p4 契約・`p6_external_interface.csv`。MOD 別の外部依存件数（EIF 行に現れる回数。EIF-09 config.json・EIF-13 journald は全 MOD 共通なので差を見るときは除く）: trigger 8／settings 5／vault 4・executor 4／board 3・monitor 3・orchestrator 3・apps/api 3／stages 2。テストダブル候補＝EXT-01 beads（一時 dir に `bd init` 憲章 §8）・EXT-02 vault（一時 dir）・EXT-03 LLM（ローカル固定応答 or モック）・EXT-04 DL（一時 dir）・EXT-05/06/07（一時 SETTINGS_ROOT／TRIGGER_ROOT）・EXT-10/11/12（TestClient）。p5_selfcheck の participant 抽出（Protocol 名）で共起 MOD を機械で出せる
3. **付3（設定の 3 層仕分け）**: INPUT は p5 feature_list・`p6_external_interface.csv`（EIF-08 .env・EIF-09 config.json）・憲章 §5・環境情報ファイル。(a) 秘密＝LLM api_key 群（`api_key_ref` の名前。プロファイルごと）(b) 環境依存＝`VAULT_ROOT`／`SETTINGS_ROOT`／`TRIGGER_ROOT`／`APP_PORT`／`UI_PORT`／ui 側の app base URL／bd のパス（board/config.json `bd_bin`・`beads_dir` は (c) か (b) か要判断）／watch_dir（領域 B の宣言値＝(c) でも環境依存）(c) 挙動＝ルート config.json（アプリ名・環境ラベル・ログ・有効 MOD 一覧）＋各 MOD config.json（各 data_model の *Config クラス: BoardConfig／VaultConfig／SettingsConfig／MonitorConfig／StagesConfig／ExecutorConfig／OrchestratorConfig／TriggerConfig）。systemd `EnvironmentFile=` の要否（憲章 §5）
4. フェーズ末の改訂は実施済み（D12 §5。`_変更履歴.md` 参照）

## 2-b. 憲章への申し送り（付1）

- **PATCH-7**（`p6_constitution_patch.md`・D12-d）: §2 指定ツールに LLM バックエンド追加／§4 LLM 記述と通信プロトコルを「本 ver は HTTP」に緩める。**未適用。ユーザが両版へ同時に適用**。適用しない裁定なら `p6_external_interface.csv` EIF-03 を N に戻し notes §4 を据え置き論点に書き換える
- PATCH-6（P5D-044）: ユーザ適用済み（`8d098c3` 後。handoff_p5 §2-b）。憲章の適用漏れ 2 点（作業メモ C-5）は未

## 3. 本セッションで決めた横断規約

- 外部実体の ID は `EXT-nn`（notes §1 が正本）、外部インターフェースは `EIF-nn`（相手×方向）。付2・付3 はこの ID で引く
- HTTP 境界の正は `openapi.yaml`（paths・method・応答コード）。M1 は FastAPI 生成の `/openapi.json` と paths 単位で diff する
- `/health` は apps/api 所有・liveness のみ（D12-e）

## 4. 事実・気づき

- 工程文書の食い違い 2 点（`p3_context_map.csv` 不在・`EXT-*` 未定義）は D12 §4 に記録し、EXT 表を新設して解消
- settings-F21 の router 列挙に `/settings/humans` が漏れていた（F17 にはある）→ openapi に追加し p5 csv F21 を改訂
- mermaid flowchart: nested subgraph の `direction` は外向き辺があると無視される → 今回は **LR** の方が読めた（TB は横一列に潰れた）。subgraph を辺の端点にしない
- PyYAML 6.0.1 はシステム python3 にある。selfcheck は無くても最小パーサで paths／responses を検査できる（ImportError 偽装で確認）
- PoC 無変更: `git -C pocs/<repo> status --porcelain | grep -v '^??'` は開始時と同一（renkei `D` 5 行・casbin `D` 3 行）
