# x2_constitution_patch — 憲章の修正差分案（付2フェーズ）

**作成**: 2026-08-19（付2 統合テストのクラスタ）
**位置付け**: PATCH-1〜5（`../phase45/p45_constitution_patch.md`）・PATCH-6（`../phase5/p5_constitution_patch.md`）・PATCH-7（`../phase6/p6_constitution_patch.md`）・PATCH-8〜10（`../x1/x1_constitution_patch.md`）の続き。付1 の REV-08（「統合テストの置き場は付2 のクラスタ文書で定めてから憲章 §8 への 1 行を再提起」）に応えるもの＋付2 で前提にした「LLM はテストで模擬」の 1 行（D14-h）。**未適用**。対象は `docs/initial_memo/5. constitution_fullversion_モジュラーモノリス_開発原則（task_ambi 固有版・非コンテナ）.md`（fullversion。簡潔版は存在しない＝付1 D13-a）。**適用はユーザが行う**（Claude は差分案まで。D10-d）。
**書式**: 憲章の該当文を「前」に引用し、「後」に改訂案。

---

## PATCH-11 §8 統合テスト — 置き場と LLM の扱い（D14-h / REV-08）

**前**（§8「統合テスト」の項）:

> - 統合テスト（pytest + FastAPI TestClient）: API エンドポイント経由で「モジュール + 外部ツール実物 + API 層」の噛み合いを検証。主要な守りの層。一時ディレクトリに vault / git リポジトリ / beads を実際に作成して検証する。サーバ型 DB を持たないため、テスト環境は一時ディレクトリで完結する（CI に DB 用コンテナは不要）。配線は実際の composition root（apps/api）のものを用い、モジュール間接続の噛み合いもここで検証する。

**後**（末尾に 2 文追加）:

> - 統合テスト（pytest + FastAPI TestClient）: API エンドポイント経由で「モジュール + 外部ツール実物 + API 層」の噛み合いを検証。主要な守りの層。一時ディレクトリに vault / git リポジトリ / beads を実際に作成して検証する。サーバ型 DB を持たないため、テスト環境は一時ディレクトリで完結する（CI に DB 用コンテナは不要）。配線は実際の composition root（apps/api）のものを用い、モジュール間接続の噛み合いもここで検証する。**置き場は apps/api 側（`apps/api/tests/integration/`）とし、`packages/features/<module名>/tests/` は単体テストのみを置く（テストコードも feature 間 import 禁止のため）。外部ツールのうち LLM API だけはローカルの模擬サーバ（OpenAI 互換・台本応答）で代替し、実物の LLM を呼ぶ検証は CI 外の手動工程とする。クラスタ外のモジュールは Protocol のスタブ実装を composition root の差し替え点から注入する。**

**理由**: (1) 置き場＝付1 REV-08 の指摘（§1「テストコードも feature 間 import 禁止」と §8「実 composition root」から、統合テストは features/<mod>/tests に置けない）。付2 の 5 クラスタ文書は `apps/api/tests/integration/cluster_NN_<slug>/` を前提に書いた（D14-h）。REV-08 の括弧書き「repo 直下 tests/integration」は uv workspace のルートに tests を持つ形で composition root から離れるため採らない。(2) LLM＝§8 は beads・vault・git を「実物」と明記する一方 LLM に触れておらず、AI が §8 を読んで LLM も実物で呼ぶ統合テストを書き得る。付2 は「LLM API のみ模擬、他は一時 dir の実物」で設計した（D14-b。monitor の Hook が transport 層にあるためループバック HTTP の偽サーバ。MockTransport 不可）。(3) スタブ注入＝§1「テストでは依存 Protocol にスタブ実装を注入」と §8「実 composition root」を両立させる差し替え点（D14-e）を §8 側にも 1 句置き、apps/api の M1 要求と憲章がつながる。

**影響**: 設計側の変更なし。apps/api への M1 要求（build 連鎖の関数化＋有効 MOD 一覧・override の引数化）は D14-e／`handoff_x2.md` §3 に記載。REV-14（例示の「git リポジトリ」は V4 対象外）は例示のため触らない。

---

**適用の順序**: PATCH-7 → C-5 の 2 点 → PATCH-8／9／10 → **PATCH-11**（いずれも未適用。`../x1/handoff_x1.md` §2 の順に本書を足す）。適用後に `python3 ../x1/x1_selfcheck.py --extract && python3 ../x1/x1_selfcheck.py`（条項表 C-8-02 の本文が変わる）。
