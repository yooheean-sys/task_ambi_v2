# `apps/api/tests/contract/` の規約

**転記であることの明示**: 本ファイルは
`specs/013-protocol-contracts/contracts/contract-test-conventions.md`（**正本**）の §1〜§10 を
転記したものである。規約を変えるときは**正本を先に直し、こちらへ写す**。
正本と本ファイルの最終一致は `specs/013-protocol-contracts/tasks.md` の T106 が判定する。

**Feature**: `013-protocol-contracts` | 設計差分候補 **(ii)** と **(vi)** の裁定

---

## 1. この層は何を検査するか

**提供実装ちょうど 1 つ**が、契約台帳 `docs/design/phase4/p4_module_contract.csv` の宣言
（入力・出力・例外・同期／非同期）どおりに**振る舞う**かを IF 単位で確かめる。

既存 3 層とやることを重ねない（spec §0-1）:

| 層 | 置き場 | やること | 本層が**やらない**こと |
|---|---|---|---|
| 1 静的照合 | `packages/common/tests/interfaces/`・`typing/` | 宣言の突合（台帳↔Protocol・引数個数・戻り型注釈・例外階層・Raises docstring・pyright 構造適合） | 型注釈・docstring・`frozen` の照合をやり直さない |
| 2 各 feature の unit | `packages/features/<mod>/tests/` | 内部ロジック（スタブ注入） | 分岐網羅・設定読取・起動時検査をやり直さない |
| 3 統合クラスタ | `apps/api/tests/integration/`（**012 の領分**） | 実物×実物・実 composition root・HTTP 入口 | 消費側 MOD の実装を登場させない。HTTP を通さない |
| **4 本層** | **`apps/api/tests/contract/`** | **提供実装 1 つの振る舞いが契約どおりか** | — |

`integration/` の規約（実 composition root 必須・HTTP 入口）は**継承しない**（FR-006）。

---

## 2. 登場してよいダブル 4 種（これ以外を登場させない）

1. **提供実装ちょうど 1 つ** — 当該 feature の `build()` が返すインスタンス。
2. **その提供 MOD が所有する外部ストアの実物** — 一時ディレクトリ（`bd init` した beads・
   vault のフォルダ構成・設定ファイル領域 A）。
3. **偽 LLM サーバ** — `_support/fake_llm_server.py`（ループバック HTTP・台本駆動）。
4. **依存 Protocol の契約準拠スタブ** — `_support/contract_stubs.py`（呼び出し記録つき）。

**禁止**:

- 消費側 MOD の実装を同じテストに登場させること（FR-007）。
- `httpx` の `MockTransport`（FR-009。Hook が transport 層の差し込み点であるため）。
- 外部の実 LLM API への通信（FR-010）。
- `api.wiring.build_modules`・`api.app.create_app`・`ProtocolOverrides`・`TestClient`（FR-006a）。

---

## 3. 提供実装の取り出し（FR-006a）

```python
from board import BoardConfig, BoardDeps, build as build_board
from common.interfaces.board import BoardReader

handle = build_board(BoardDeps(), config=BoardConfig(timeout_sec=120))
board_reader: BoardReader = handle.board_reader  # 憲章 §3: Protocol 型を必ず注釈する
```

- 依存スタブは `<Mod>Deps` へ**直接**渡す。
- モジュール設定を変えるときだけ `config=` を使う（省略時は commit 済み `config.json`）。
- **戻りを受ける変数には Protocol 型を注釈する**（FR-012）。注釈が無いと pyright strict の
  構造適合検査が発火せず、スタブが契約から外れていても気づけない。

---

## 4. IF 宣言の書式（設計差分候補 (ii) の裁定）

```python
@pytest.mark.contract_if("IF-01")
def test_contract_board_show_returns_issue_for_orchestrator(...) -> None:
    ...
```

1. **1 テスト＝ちょうど 1 IF**。0 個も 2 個以上も禁止。**1 つの IF に複数テストを置くのは可**。
2. 引数は**文字列リテラル 1 個**だけ。変数・f-string・連結は使わない（AST で静的に読むため）。
3. `pytestmark`（モジュール大域）に `contract_if` を置かない。置いてよいのは
   `pytest.mark.requires_bd` だけである。
4. マーカは `contract/conftest.py` の `pytest_configure` で登録する。ルート `pyproject.toml` の
   `markers` は触らない（FR-030）。
5. **宣言した IF のメソッドを実際に呼ぶ**（宣言の真正性）。テスト本体、または**そこから名前で
   呼ぶ module 直下のヘルパ 1 段**に、台帳が定めるメソッド名が現れなければならない。破ると
   `test_contract_declarations_actually_reference_the_declared_method` が赤くなる。
   ⚠ **必要条件であって十分条件ではない** —— 名前が現れることは、そのメソッドを主眼に検証して
   いることを意味しない（前処理でたまたま呼んでいるだけでも通る）。

### 4-1 台帳の重複 9 行の書き分け（FR-002a）

同一メソッドに複数 IF がぶら下がる 9 件は、**消費者ごとに別テストを立てる**。各テストは、
その行の入力欄・出力欄の注記が示す**消費者固有の観点**を検証する。

| メソッド | IF | 消費者 | 書き分ける観点（台帳の出力欄） |
|---|---|---|---|
| `show` | IF-01 | orchestrator | DM-13 の全項目（labels・parent・acceptance・status・claim 状態） |
| | IF-02 | executor | `read_issue` Tool 用の読取 |
| | IF-03 | monitor | **写像後の**確認読取 |
| `comments` | IF-07 | orchestrator | DM-16（actor・種別 人間指示／回答／封筒） |
| | IF-08 | monitor | 封筒の反映確認 |
| `release` | IF-14 | orchestrator | claim 解放（`state=working` を外す） |
| | IF-15 | monitor | F 級写像の一部 |
| `comment` | IF-16 | orchestrator | 停止理由・UNSAFE 記録 |
| | IF-17 | executor | `record_progress` Tool |
| | IF-18 | monitor | **封筒形**の body（等級・種類・実測値） |
| `close` | IF-19 | orchestrator | 親 close／リーフ承認 close |
| | IF-20 | monitor | W 級写像（close＋封筒） |
| `set_labels` | IF-21 | orchestrator | `note=single-child` 観測印・state 更新 |
| | IF-22 | monitor | `add` が **`state=failing`** |
| `check_project_folders` | IF-31 | orchestrator | 不在一覧（空＝OK） |
| | IF-32 | settings | 登録・編集時の存在確認 |

**観点が実質同一になったら**、その IF の入力欄・出力欄の注記を読み直して書き分ける
（spec の Edge Cases）。

### 4-2 被覆検査から外れるファイル

module 大域に `CONTRACT_META = True` を置いたファイルは AST 走査の対象外になる。
**被覆検査および器のメタ検査（止め木ヘルパの自己テスト等）を置くファイル専用**の逃げ道であり、
それ以外に使わない。

理由（`design-sync.md` **SD-01**）: FR-002 が「1 テスト＝ちょうど 1 IF」を課すため、
SC-009 が要求する**止め木ヘルパの自己テスト**は IF を宣言できない。
「被覆検査そのもの専用」の字句のままでは `contract/` 内に合法な置き場が無く、
`apps/api/tests/unit/` へ逃がす案は FR-029 が許す 3 範囲の外である。

---

## 5. 検証の深度（台帳の 4 欄に対応）

| 台帳の欄 | 何を確かめるか | FR |
|---|---|---|
| 出力 | 宣言された**型のインスタンス**が返る | FR-015 |
| 出力の注記 | 注記が振る舞いを規定していればその振る舞い（例: IF-35「不在は例外で明示。黙って空を返さない」・IF-33「空の一覧に倒さない」・IF-37「返すのは vault ルート相対」・IF-31/32「不在は例外にせず missing に載せる」） | FR-016 |
| 例外 | 宣言された例外が、宣言された条件で送出される | FR-017 |
| 例外が `—` | 正常入力で例外が送出されない | FR-018 |
| 同期/非同期 | 非同期 2 件（IF-42・IF-43）は非同期として呼ぶ。**出力欄が `None` の IF は戻りを変数に受けて `assert … is None` する**（呼び捨てにしない）—— 提供実装の同期性を主張する唯一の手であり、呼び捨てだとコルーチンを返す実装でも緑になる（`design-sync.md` **B-29**） | FR-019 |

**やらないこと**（層 1 が担保済み）: 受け渡し型の `frozen` 検査・型注釈の照合・
Raises docstring の照合（FR-020）。

---

## 6. 実運用ストアに触れない（FR-013・FR-013a・FR-014）

- `contract/conftest.py` の autouse fixture が、`.env` を読む 4 MOD
  （board・vault・settings・executor）の `env_file_path` を空の一時 `.env` に差し替え、
  該当キーを `delenv` する。**この fixture を消さない・迂回しない。**
- 各提供 MOD の fixture は、`build()` を呼ぶ**前**に「解決先が `tmp_path` 配下であること」を
  検査し、外れていれば実物に触れる前に失敗する。settings の `build()` は不在時に雛形を
  **書き込む**ため、事後確認では手遅れになる。
- **`/mnt/c` 配下に触れるテストを作らない**。実 `.env` の `VAULT_ROOT`・`DOWNLOADS_DIR` は
  `/mnt/c` 配下を指す。「実行前後で実 vault を比較する」形の検査は**禁止**である。
- `bd init` は**リポジトリ外**でのみ実行する（`.git`・`AGENTS.md`・`CLAUDE.md`・`.claude/` を
  生成するため）。

---

## 7. `requires_bd` の付与単位（FR-026）

実 `bd` を起動するファイル（board 提供分）にのみ、モジュール大域で付ける。

```python
pytestmark = pytest.mark.requires_bd
```

他の 6 提供 MOD には付けない。`-m "not requires_bd"` の実行で board 分（22 IF 相当）だけが
外れ、残り 28 IF 分は緑になる（SC-006）。**被覆検査は AST 走査なので除外の影響を受けない。**

---

## 8. ファイルの割り方と基名（FR-027）

```
contract/
├── README.md                              本書の転記
├── conftest.py                            マーカ登録・一括止め木・提供 MOD 別 fixture
├── contract_stores.py                     一時 beads / vault / 領域 A の組み立て
├── contract_shims.py                      BD_BIN シム 2 形
├── test_contract_ledger_coverage.py       被覆検査（CONTRACT_META = True）
├── test_contract_board_reads.py           IF-01〜09
├── test_contract_board_writes.py          IF-10〜22
├── test_contract_board_unavailable.py     IF-01〜22 の BoardUnavailable（シム (a)）
├── test_contract_board_write_unverified.py IF-11 の BoardWriteUnverified（シム (b)）
├── test_contract_vault_reads.py           IF-31〜36
├── test_contract_vault_outcome_write.py   IF-37
├── test_contract_settings_reader.py       IF-23〜26・50
├── test_contract_settings_resolvers.py    IF-27〜30
├── test_contract_stages_pure.py           IF-44〜47
├── test_contract_monitor_hook.py          IF-40〜43
├── test_contract_monitor_run.py           IF-38・39
├── test_contract_executor_run.py          IF-48
└── test_contract_orchestrator_run.py      IF-49
```

- テストファイルの基名は `test_contract_<mod>_<観点>.py`。**リポジトリ全体で一意**にする。
- 補助モジュール（`contract_stores.py` 等）も top-level 名になるため一意にする。
- `tests/` に `__init__.py` を置かない方針のため、基名の重複は `uv run pytest`（全体）だけを
  collection error で落とす。**モジュール単位の検証では再現しない。**

---

## 9. 実行順序に依存しない（FR-028・SC-008）

- 一時ディレクトリ・偽 LLM サーバのポートはテストごとに独立させる。
- 環境変数の差し替えは `monkeypatch` に限る（fixture 終了で必ず戻る）。
- スレッドを起こすテスト（IF-49）は必ず `join` してから終わる。
- 台本・スタブの状態をモジュール大域に持たない。

---

## 10. 変更してよい範囲（FR-029・FR-030）

**変更してよい**: `apps/api/tests/contract/`（新設）／`apps/api/tests/_support/` への最小追記／
`specs/013-protocol-contracts/`。

**1 行も変更しない**: `apps/api/src/`・`apps/ui/`・`packages/`（common・features とも）・
`docs/design/`・`.importlinter`・ルート `pyproject.toml`・ルート `config.json`・`registry.py`・
`apps/api/tests/integration/`。必要が生じたら**変更せず** `design-sync.md` に記録して報告する。
