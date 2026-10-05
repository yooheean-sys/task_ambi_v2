# handoff_p5 — 第5フェーズ 進行中の申し送り（MOD 完了ごとに置換）

**更新**: 2026-08-19（**第5フェーズ完了**。common＋8 MOD 承認済み・P2 被覆 49/49・PATCH-6 ユーザ適用済み）
**レジーム**: 消耗品（新版が旧版を置換）。正本は D11（前提）・`p5_decision_log.csv`（P5D-*）・各 MOD の notes。
**作成順（D11-a）**: common → **board** → vault → settings → monitor → stages → executor → orchestrator → trigger

## 1. 現在地

| MOD | 状態 | 成果物 | selfcheck |
|---|---|---|---|
| common（kernel） | 作成済み・レビュー待ち | `common/p5_common_data_model.mmd`・`p5_common_notes.md`（P5D-001/002） | ERRORS: none |
| board | **承認済み**（ユーザ: 「beads と通信する層」として問題なし） | `board/p5_board_sequence.mmd`（主図）・`p5_board_sequence_reads.mmd`（副図）・`p5_board_data_model.mmd`・`p5_board_feature_list.csv`（17 fn）・`p5_board_notes.md`（P5D-003〜007） | ERRORS: none（--render ✔） |
| vault | **承認済み** | `vault/p5_vault_sequence.mmd`・`p5_vault_data_model.mmd`・`p5_vault_feature_list.csv`（10 fn）・`p5_vault_notes.md`（P5D-008〜012） | ERRORS: none（--render ✔） |
| settings | **承認済み**（P5D-018/019 追加反映） | `settings/p5_settings_sequence.mmd`（主図）・`_uc07_save.mmd`・`_uc08_project.mmd`（副図 2）・`p5_settings_data_model.mmd`・`p5_settings_feature_list.csv`（23 fn）・`p5_settings_notes.md`（P5D-013〜017） | ERRORS: none（--render ✔） |
| monitor | **承認済み** | `monitor/p5_monitor_sequence.mmd`・`p5_monitor_data_model.mmd`・`p5_monitor_feature_list.csv`（12 fn）・`p5_monitor_notes.md`（P5D-020〜024） | ERRORS: none（--render ✔） |
| stages | **承認済み** | `stages/p5_stages_sequence.mmd`・`p5_stages_data_model.mmd`・`p5_stages_feature_list.csv`（8 fn）・`p5_stages_notes.md`（P5D-025〜028） | ERRORS: none（--render ✔） |
| executor | **承認済み** | `executor/p5_executor_sequence.mmd`・`p5_executor_data_model.mmd`・`p5_executor_feature_list.csv`（12 fn）・`p5_executor_notes.md`（P5D-029〜033） | ERRORS: none（--render ✔） |
| orchestrator | **承認済み** | `orchestrator/p5_orchestrator_sequence.mmd`（主図）＋`_propose`・`_execute`・`_recover_human`（副図 3）・`p5_orchestrator_data_model.mmd`・`p5_orchestrator_feature_list.csv`（15 fn）・`p5_orchestrator_notes.md`（P5D-034〜038） | ERRORS: none（--render ✔） |
| trigger | **承認済み**（P5D-043 上書き方式・P5D-044 PATCH-6） | `trigger/p5_trigger_sequence.mmd`（主図）・`_ui.mmd`（副図）・`p5_trigger_data_model.mmd`・`p5_trigger_feature_list.csv`（13 fn）・`p5_trigger_notes.md`（P5D-039〜042） | ERRORS: none（--render ✔） |

## 2. 次にやること（第6 → 付1 → 付2 → 付3）

1. **第6**: コンテナ図（Level 2。非コンテナなので「省略できる」が外部境界図の要否を冒頭で判断）と API Contract（settings `/settings/*`・orchestrator `/kickoffs`・trigger `/triggers`・`/history`・`/watcher`・`/hooks/file`・`/health`）。入力は各 MOD の router 行（feature_list）と notes §1
2. **付1**: 憲章 §0〜§11 × 全成果物の総当たり。裁定済み（DEC-001〜015）は再質問しない。第5 からの要判断: P5D-004（bd ロック待ち × §10）・P5D-025（acceptance 書式 × D03-a）・P5D-043/044（履歴書換え。PATCH-6 適用後の文言で判定）。憲章の適用漏れ 2 点（作業メモ C-5）。2-2 改訂（R09-P1・R12-P5 解除、R10-P1・R12-P1）
3. **付2**: 各 MOD の主図から共起 MOD 群を抽出（orchestrator は全部と共起、board は 5 MOD から）。selfcheck の participant 抽出が流用できる
4. **付3**: `.env` のキー（VAULT_ROOT／SETTINGS_ROOT／TRIGGER_ROOT／LLM api_key 群）と各 MOD config.json の項目（各 data_model の *Config クラス）
5. フェーズ末の改訂は実施済み: p4 notes §2（受け渡し型追記）・p2 csv FN-22 備考・7-2 L・_目次・用語集・作業メモ A-0/A-3/B/E・_変更履歴・D11 §7 追記

## 2-b. 憲章への申し送り（付1）

- **PATCH-6**（`p5_constitution_patch.md`・P5D-044）: §2 例外 (b)「追記のみのローカルログ」→「システム所有のローカルログ（開始行の終了時書換えを同じ所有 MOD・自分の行に限って許す）」。**未適用。ユーザが両版へ同時に適用**。付1 は適用後の文言で判定
- 憲章の適用漏れ 2 点（作業メモ C-5）も同時に

## 3. 本セッションで決めた横断規約（全 MOD に適用）

- 入口レーン＝呼ばれる Protocol 名（P5D-003）。kernel は Note のみ
- 契約例外は提供 MOD の interfaces ファイルで定義し kernel の 4 基底を継承（P5D-001）
- 提供 Protocol の実装 fn は `機能名` に `Protocol.method` を含める（selfcheck (iii) が検出）
- data_model の namespace: `common_interfaces_<MOD>_py`（受け渡し型）／`<MOD>_internal`／外部実体は `<tool>_entities`
- 内部レーンにパッケージ内分割のサブパッケージ（`<mod>/<name>/`）を描いてよい（P5D-029。selfcheck 対応済み）

## 4. 事実・気づき

- 憲章 patch はユーザ適用・commit 済み（`8d098c3`）。適用漏れ 2 点は作業メモ C-5
- Bash の作業ディレクトリはツール呼び出し間で持ち越されるため `cd pocs/…` の後に相対パスで書くと PoC 作業ツリーに書いてしまう（本セッションで 1 度発生 → 即移動・削除し PoC は無変更）。以後は絶対パスで書く
- PoC 無変更: `git -C pocs/<repo> status --porcelain | grep -v '^??'` は開始時と同一（renkei `D` 5 行・casbin `D` 3 行）
