# x3_constitution_patch — 憲章の修正差分案（付3フェーズ）

**作成**: 2026-08-20（付3 設定の 3 層仕分け）
**位置付け**: PATCH-1〜5（`../phase45/p45_constitution_patch.md`）・PATCH-6（`../phase5/`）・PATCH-7（`../phase6/`）・PATCH-8〜10（`../x1/`）・PATCH-11（`../x2/`）の続き。付1 の **REV-13**（「§5 は app プロセスの .env／config.json を前提。ui プロセスが読む設定の置き場が書かれていない → 付3 で決めてから §5 に 1 句」）への応答（D15-f）。**適用済み**（憲章 v1.0.0 §5 に反映済み）。対象は `docs/initial_memo/5. constitution_fullversion_モジュラーモノリス_開発原則（task_ambi 固有版・非コンテナ）.md`（fullversion のみ）。**適用はユーザが行う**（Claude は差分案まで。D10-d）。
**書式**: 憲章の該当文を「前」に引用し、「後」に改訂案。
**付3 で憲章を直さなかったもの**: 層の 2 分割（c1／c2）は §2 例外 (a)・§5 に既に書かれている（DEC-003／007 の「§5 に別系統の追記はしない」を維持）／解決順序・秘密のマスク（D15-b／i）は設計側（x3_notes §2・§4）に置き憲章には足さない（4.5 以来の「注記を増やす提案は通りにくい」傾向と、憲章内の自己矛盾・AI の誤読という理由が立たないため）／`agents.yaml`／`tasks.yaml` 化（D15-j）は §2 例外 (a) の列挙「…LLM プロファイル・上限・トリガー定義**等**」で読める（P5-R1 で要れば再提起）。

---

## PATCH-12 §5 設定管理 — ui プロセスが読む設定（D15-f / REV-13）

**前**（§5 の末尾の項）:

> - 将来モジュールに秘密値が必要になった場合は、その 1 キーだけ root .env に prefix 付きで置く（例: USERMGMT_API_KEY）。

**後**（末尾に 1 項追加）:

> - 将来モジュールに秘密値が必要になった場合は、その 1 キーだけ root .env に prefix 付きで置く（例: USERMGMT_API_KEY）。
> - **ui プロセス（apps/ui）も同じルート .env の環境依存値（app の base URL・ポート）とルート config.json（アプリ名・有効化するモジュール一覧）を読む。秘密・各モジュールの config.json・宣言的設定ファイルは読まない（全て app の HTTP API 経由）。Streamlit の待受ポートは起動引数でしか渡せないため、ui の systemd ユニットに限り EnvironmentFile= で .env を併用する。**

**理由**: (1) REV-13 の指摘どおり §5 は app の読み方しか書いておらず、ui が何を読んでよいかが決まっていない。付3 で「(b) のうち APP_BASE_URL／APP_PORT／APP_HOST／UI_PORT とルート config.json だけ」と裁定した（D15-f）ので、§5 に写さないと AI が ui に領域 A や MOD config.json を直接読ませる実装を書き得る（§2「UI は自モジュールを HTTP API 経由で操作」の帰結を §5 側で明文化）。(2) 既存の「systemd ユニットへ環境変数として渡す必要がある場合のみ EnvironmentFile= を併用する」の**その場合**が ui ユニットだと特定する。

**影響**: 設計側の変更なし（p6 配置図の UIU ノード・EIF-10 は記号のまま）。M1: ui の Settings クラスは (b) 4 キー＋ルート config.json の 2 項だけを持つ。

---

**適用の順序**: PATCH-7 → C-5 の 2 点 → PATCH-8／9／10（**ユーザ適用済み**: 2026-08-20 に現物で確認。§3 補足・§5 例示・§10 注記の 3 箇所）→ PATCH-11。適用後に `python3 ../x1/x1_selfcheck.py --extract && python3 ../x1/x1_selfcheck.py`（条項表 C-5-* が 1 行増える。判定表に X1 行を 1 行追記＝適合）。
