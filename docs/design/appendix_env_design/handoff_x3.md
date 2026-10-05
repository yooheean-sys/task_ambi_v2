# handoff_x3 — 付3フェーズ完了の申し送り（P5-R1 → M1 へ）

**更新**: 2026-08-20（**付3 完了**。台帳 85 行・`.env.example`・PATCH-12 提示・AI 設定の CrewAI 規約化 A′ を裁定）
**レジーム**: 消耗品（次フェーズの handoff が置換）。正本は D15（前提・裁定）・`x3_config_catalog.csv`（機械検査の正）・`x3_notes.md`（短文の正本）。`handoff_x2.md` §3 の「付3」項は本書が置換する。

## 1. 現在地

| 成果物 | 状態 |
|---|---|
| ★`x3_config_catalog.csv` | CFG-01〜85（a 3／b 10／c1 55／c2 17）。10 列・CRLF。配置先は a／b／c1／c2 |
| ★`.env.example` | (a) `LLM_API_KEY_<PROFILE>` 2 例／(b) 10 キー（VAULT_ROOT・SETTINGS_ROOT・TRIGGER_ROOT・BEADS_DIR・BD_BIN・DOWNLOADS_DIR・APP_HOST・APP_PORT・APP_BASE_URL・UI_PORT）。ダミー値・コメント付き |
| `x3_notes.md` | 層定義（c1／c2）・8 段の解決順序・台帳の読み方・秘密の担保 7 点・テスト差し替えの層・環境情報ファイルの項目雛形・**CrewAI 規約化 A′ と「最後の確認」3 問の回答**・P5-R1 への要求・未決 |
| `x3_constitution_patch.md` | **PATCH-12**（§5 に ui プロセスの 1 項。REV-13 への応答）。**未適用** |
| `x3_selfcheck.py` | `python3 x3_selfcheck.py` → ERRORS: none |
| D15 | 裁定 a〜m（Q1〜Q11）・根拠・棄却・事実（環境情報ファイル不在・CrewAI 仕様の調査）・改訂・未決 |
| 付3 起因の改訂 | p5 board（BoardEnv）・trigger（DOWNLOADS_DIR／watch_dir 相対）・settings（system_actor 明示）、p6 csv EIF-01/04/08・notes・配置図。`p5_selfcheck.py board／trigger／settings`・`p6_selfcheck.py` → ERRORS: none |

## 2. ユーザ作業

- **憲章 fullversion の編集（未）**: PATCH-7（第6）→ C-5 のツリー 1 点 → PATCH-11（付2）→ **PATCH-12（付3）**。PATCH-8／9／10 は適用済み（2026-08-20 に現物で確認）。適用後に `cd outcome/design/x1 && python3 x1_selfcheck.py --extract && python3 x1_selfcheck.py`（C-5-02 が新文言に、C-5-10 が増える → 判定表に X1-112 を適合で 1 行追記）
- **環境情報ファイルの実体**（vault 側・リポジトリ外）: `x3_notes.md` §6 の項目で作る。(a) の値は書かない
- `.env` の実体はリポジトリ直下に `.env.example` から作る（M1 着手時）

## 3. 次にやること（P5-R1 → M1）

1. **P5-R1（別セッション。D16）: AI 設定の CrewAI 規約化**（D15-j・`x3_notes.md` §8）。対象＝settings（領域 A 実体を `global/agents.yaml`・`projects/<key>/agents.yaml`・`global/tasks.yaml`・`projects/<key>/tasks.yaml` に。roles/<persona>.md は backstory に吸収。UC-07 UI・validate・MemberResolver.resolve の戻り・**新 IF `SettingsReader.get_tasks`**）／stages（`build_prompt(…, templates)` でテンプレートを引数で受ける。純粋性維持）／executor（Member の CrewAI フィールドから `Agent(**)`。F04 の mode／purpose 別 Tool 集合は agents.yaml 既定へ）／orchestrator（get_tasks を読んで stages に渡す）／p4 契約 csv・図（IF 追加）／p2 DM 追記／D06-e・P5D-015/017/028 を覆す記録。質疑で決める点: agents.yaml の許可フィールド集合・`llm` 未指定時の default プロファイル・PJ 上書きの単位（フィールド単位 merge か丸ごと置換か）・tasks.yaml の `{変数}` 名の一覧・roles/*.md 吸収の移行（雛形生成）。完了後に台帳 CFG-69〜72・83 と `型` 列を改訂、`x3_selfcheck.py` の NEW_C1 例外表を空にする（3 新設キーを p5 data_model へ転記）
2. **M1 への要求（付3 発）**: (1) 各 MOD の `*Config` Settings は env ソースを持たない／`*Env` は `.env`＋OS 環境変数（D15-b）(2) build() に config の override 引数（D14-e の拡張。D15-l）(3) common/kernel `LoggerFactory` のマスクフィルタ＋crewai／litellm／httpx ロガの WARNING 固定（D15-i (2)）(4) executor: SecretStr・`LLM_API_KEY_<PROFILE>` の解決・`disable_crewai_telemetry` の setdefault (5) settings validate: api_key_ref の規約・禁止キー名 (6) monitor Hook のヘッダ非記録 (7) pytest 静的走査＋フィルタ単体テスト（D15-i (7)）(8) ui の Settings は (b) 4 キー＋ルート config.json の 2 項のみ、ui ユニットの `EnvironmentFile=`（D15-f）(9) `settings.system_actor`＝`orchestrator.system_actor` の一致検査 (10) trigger: watch_dir の DOWNLOADS_DIR 結合＋外縁検査、board: BEADS_DIR／BD_BIN
3. 第5 notes §7・付2 D14 §6・付1 REV の M1 持ち越しはそのまま（作業メモ A-0）

## 4. 事実・気づき

- 工程文書の INPUT「環境情報ファイル」は現物が無く、(b) を `.env` に一本化する裁定で INPUT 不要になった（人間向け文書として項目雛形だけ残す）
- 憲章 §6 に秘密マスクの規定が無いことが付3 で初めて表に出た（設計側で担保 7 点を置き、憲章には足さない）
- **ユーザは AI 設定を CrewAI 標準ファイル（agents.yaml／tasks.yaml）で定義し UI で編集する方針**。`tools.yaml` は CrewAI 標準に無いと示すと「置かない」を選択。CrewAI v1.15 は JSONC 推奨・classic YAML サポート継続・`process_config` は未知キー無視（将来の機械変換が効く）
- 付3 の裁定で P5 の 4 MOD＋p4 契約に波及する改訂（P5-R1）が生まれた。付 n フェーズの裁定が上流に戻る初のケース。M1 の前に必ず通す
- PATCH-8／9／10 はユーザが既に適用していた（handoff_x2 §2 の「未」は古い）。憲章の現物は着手時に `git diff` で確かめる
- PoC 無変更: `git -C pocs/<repo> status --porcelain | grep -v '^??'` は開始時と同一（renkei `D` 5 行・casbin `D` 3 行）
