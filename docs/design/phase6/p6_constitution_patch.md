# p6_constitution_patch — 憲章の修正差分案（第6フェーズ）

**作成**: 2026-08-19（第6フェーズ 外部インターフェース一覧）
**位置付け**: `../phase45/p45_constitution_patch.md`（PATCH-1〜5）・`../phase5/p5_constitution_patch.md`（PATCH-6）の続き。第6フェーズで生じた「憲章を直す」裁定の修正差分案。**未適用**。対象は `docs/initial_memo/5. constitution_fullversion_モジュラーモノリス_開発原則（task_ambi 固有版・非コンテナ）.md`（fullversion）と AI に渡す簡潔版 constitution.md。**適用はユーザが両版へ同時に行う**（D10-d・P4）。
**裁定の正本**: D12-d（`設計判断_V4-D12_フェーズ6前提の裁定.md`）。
**書式**: 憲章の該当文を「前」に引用し、「後」に改訂案。

---

## PATCH-7 §2 乗り換え前提の指定ツールに LLM バックエンドを追加し、§4 の LLM 記述を HTTP 非依存に緩める（D12-d）

### 7-a §2 アーキテクチャ原則 — 指定ツール

**前**（§2 の 7 点目）:

> - 乗り換え前提として指定した外部ツールは、アダプタクラスを分けて実装し、Protocol 経由でのみ利用する（指定制。非指定ツールは直接組み込み可）。現時点の指定ツール: beads（タスク管理）。

**後**:

> - 乗り換え前提として指定した外部ツールは、アダプタクラスを分けて実装し、Protocol 経由でのみ利用する（指定制。非指定ツールは直接組み込み可）。現時点の指定ツール: beads（タスク管理）、**LLM バックエンド（推論の相手。OpenAI 互換 HTTP のほか、将来は CLI 型のバックエンド（例: `claude -p`）への切替を想定）**。LLM バックエンドの切替点は、LLM 実行ライブラリの型を契約に出せないため利用モジュール内のアダプタ（プロバイダ種別ごとのクラス分離）に置き、上限・観測の差し込み点のみ common の Protocol とする（設計文書側で定める）。

### 7-b §4 技術スタック — LLM

**前**:

> - LLM: OpenAI 互換エンドポイント（ローカルサーバまたはリモート API）。実体は設計文書の **LLM プロファイル**（name・base_url・model_id・context_window・api_key の参照名）で宣言し、プロファイル単位に切り替える。LLM サーバ自体の起動・モデル配置は本システムの外。api_key は .env（§5）。

**後**:

> - LLM: **本 ver は** OpenAI 互換エンドポイント（ローカルサーバまたはリモート API）。実体は設計文書の **LLM プロファイル**（name・provider_kind・base_url・model_id・context_window・api_key の参照名）で宣言し、プロファイル単位に切り替える。**将来の CLI 型バックエンド（例: `claude -p`）もプロファイルの provider_kind として追加する（§2 指定ツール）。** LLM サーバ自体の起動・モデル配置は本システムの外。api_key は .env（§5）。

### 7-c §4 通信プロトコル — app → LLM

**前**:

> - app → LLM: HTTP（OpenAI 互換。ローカル／リモートを問わず同じ経路。観測・上限停止は HTTP transport 層の Hook で行う）。

**後**:

> - app → LLM: **本 ver は** HTTP（OpenAI 互換。ローカル／リモートを問わず同じ経路。観測・上限停止は HTTP transport 層の Hook で行う）。**バックエンドを CLI 型へ切り替える場合も、観測・上限停止の差し込み点は common の Protocol（transport 非依存の中立型）として 1 箇所に保つ（設計文書側で定める）。**

**理由**: 第6フェーズの外部インターフェース一覧で「乗り換え対象か（憲章のベンダーロック対策条項）」を付ける際、ユーザが LLM API を Y と裁定した（将来 `claude -p` でも動作させたい。D12-d）。憲章 §2 の指定ツール列挙は beads のみで、§4 は OpenAI 互換 HTTP と transport 層 Hook を前提に書かれているため、CLI 型バックエンドは字面に収まらない。一方で「アダプタ分離＋Protocol 経由」の原則自体は、本システムでは executor 内 `LlmFactory`（provider_kind ごとのクラス）＋monitor の `LLMHttpHook`（IF-40〜43）で既に満たしており、設計側の構造変更は要らない。CLI 型の実装（provider_kind=`claude_cli`・Hook の httpx 型を中立型へ改める＝IF-40〜43 改訂）は本 ver 対象外とし、再浮上条件付きで D12 §6・作業メモに登録した。

**影響**: 設計側の変更なし（第5 の executor・monitor 成果物は HTTP 前提のまま。`p6_external_interface.csv` EIF-03 は Y・Protocol 列に LLMHttpHook＋切替点 executor.LlmFactory）。付1 の適合チェックでは本 PATCH 適用後の文言で判定する。適用しない（憲章を触らない）と裁定した場合は、csv の EIF-03 を N に戻し notes §4 を「据え置き論点」に書き換える（ユーザ判断）。

---

（PATCH-8 以降は付1 で追加があれば本ファイルに追記）
