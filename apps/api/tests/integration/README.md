# `apps/api/tests/integration/` の規約

統合テスト（pytest + FastAPI TestClient）の置き場。**配線は実際の composition root
（`api.create_app`）のものを使う**。手配線をしたテストは、モジュール間接続の噛み合いを
検証していないので統合テストとして数えない。

根拠は D14-h（クラスタ単位の統合テストの置き場）と `docs/design/appendix_cluster_test_plan/`
の付2（クラスタ文書）。憲章 §8 にはまだクラスタの置き場の規定が無い（PATCH-11 が未適用。
→ design-sync.md（非公開） B-1）ので、
**憲章 §8 を根拠として引かない**。

## ディレクトリの割り方

```
integration/
├── README.md                 本書
├── conftest.py               _support/composition.py の fixture 化
├── test_smoke_*.py           土台のスモーク（モジュールに依存しない。本 feature が置く 5 本）
└── cluster_NN_<slug>/        クラスタ単位の統合テスト（各クラスタの feature が足す）
    ├── conftest.py           そのクラスタ用の fixture（必要なら）
    └── test_*.py
```

- `cluster_NN_<slug>/` の `NN` はクラスタ番号（2 桁・ゼロ埋め）、`<slug>` は英小文字と
  ハイフンだけの短い識別子。採番は追記のみで、欠番を埋めず、削除しても再利用しない
  （CLAUDE.md の採番規約）。
- **本 feature ではディレクトリを作らない**（対象クラスタのモジュールが 1 つも無いため）。
  規約と器だけを置く。

## 共通の準備手段（FR-036）

`_support/composition.py` の `build_test_app` / `build_test_client` / `make_root_config` を
使う。`integration/conftest.py` ではなく `_support/` に置いてあるのは、`cluster_NN_<slug>/`
からも同じ import 文（`from _support.composition import ...`）で使えるようにするため。

```python
from _support.composition import build_test_client, make_root_config

with build_test_client(enabled_modules=["board"], config=make_root_config()) as client:
    assert client.get("/health").status_code == 200
```

`integration/conftest.py` は同じものを `client_factory` fixture として配っている。

## 守ること

- **有効 MOD 一覧で絞り、残った未充足の契約だけを `overrides` で埋める**。モジュールを
  外す手段は有効 MOD 一覧に一本化されている（override でモジュールの組み立ては抑止されない。
  二重供給は `DuplicateProviderError` で落ちる）。
- 外部ツール（beads・vault・git）は**一時ディレクトリに実物を作る**（憲章 §8）。
  模擬してよいのは LLM だけで、偽 LLM サーバは `_support/fake_llm_server.py`（`000`）を使う。
- **外部 LLM API へ通信しない**（FR-039・SC-010）。
- HTTP を通らない検証（配線の失敗系・相関 ID の正規化・設定の読み口）は `integration/` では
  なく `apps/api/tests/unit/` に置く。
