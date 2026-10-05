# SKILL: board — beads（`bd` CLI）の唯一のアダプタ

**1 セッション = 1 モジュール**（憲章 §1）。このファイルと `packages/features/board/` と
`packages/common/src/common/interfaces/board.py` だけで作業できるように書いてある。

---

## 1. 責務

- **beads の唯一のアダプタ**である（憲章 §2・EIF-01）。盤面への読み書きは、システムのどこからでも
  必ず board を通る。他のモジュールは `bd` を呼ばないし、内部ストア（embedded Dolt）も読まない。
- `BoardReader`（読取 6）と `BoardWriter`（書込 8）の **14 メソッドを 1 つの実装クラス**
  `BoardService` で満たす（FR-005）。2 クラスに割ってはならない（`BdCli` のロックが 2 つに割れる）。
- **盤面導出の意味論を持たない**（FR-007）。「何が残骸か」「再投入してよいか」「質問に答えがあるか」は
  呼び手（orchestrator／monitor）の判断である。board は**盤面から都度引いた事実**だけを返す。
- **読めないときに「無い／空／偽」を返さない**（P3・FR-024）。`has_open_issues` が `False` に倒れると
  削除ガードが空振りする。失敗は必ず契約例外で上げる。
- **書込の反映を自分で確かめない**（P5D-007）。唯一の例外が `create` の読み戻し。

## 2. 主要ファイルと「置かないもの」

| ファイル | 置くもの |
|---|---|
| `__init__.py` | `BoardEnv`／`BoardConfig`（設定 2 クラス）・`BoardDeps`・`BoardHandle`・`build()`・起動時の 5 検査 |
| `service.py` | `BoardService`（14 メソッド）・ラベル組立と `state` 次元の排他・`translate_failure`（**唯一の翻訳点**） |
| `repository.py` | `WHITELIST`・`BdCommand`／`BdResult`／`BdRunner`・`BdCli`（`subprocess` 実行・env 注入・ロック待ち・ログ）・`classify()` |
| `models.py` | `to_issue`／`to_issue_ref`／`to_comment`／`first_object`／`as_objects`（bd の JSON → 受け渡し型） |
| `exceptions.py` | `BdErrorKind`・`BdCommandError`／`BdMappingError`・`BoardStartupError` 階層（**内部専用**） |

**置かないもの**（置くと設計違反）:

| 置かない | 理由 |
|---|---|
| `router.py` | board は HTTP 経路を持たない（FR-001・FR-004）。契約 IF-01〜22 の利用者はすべて同一プロセス内の他モジュールで、外から board を叩く要求が 1 件も無い。`BoardHandle.router` は `None` |
| `ui.py` | 盤面の閲覧は beads-ui（外部ツール）が担う。`has_ui_page=False` なので `apps/ui/src/ui/pages.py` への追記も不要 |
| `schemas.py` | HTTP 境界の型が要らない。モジュール**間**の受け渡し型は `common/interfaces/board.py` にあり、board 側で再定義しない（FR-006） |
| `dependencies.py` | FastAPI の `Depends` provider は経路が無いので不要。注入される依存も無い（`BoardDeps` は空） |
| `config.py` | 設定クラスは「公開口の一部」として `__init__.py` に同居させる（R-12。`src/board/` は 5 ファイル） |
| board 専用の記録ファイル | PoC の `bd_audit.jsonl` は**継承しない**（P5D-006・FR-008）。記録は共通ログ基盤 1 経路 |

依存の向きは **`service` → `repository` → `exceptions`** と **`service` → `models` → `exceptions`** の
2 本の一方向で、輪が無い。**`models.py` は `repository.py` を import しない**（R-06）。

## 3. `bd` の呼び出し規約（**import-linter では守れない**）

`bd` は CLI であり import されないので、境界検査は掛からない。守るのは**この節とレビュー**、
そして `tests/unit/test_repository_argv.py` である。

1. **ホワイトリスト 9 種だけ**を実行する:
   `ready` / `show` / `list` / `comments` / `create` / `update` / `close` / `comment` / `dep`
   - **`init` を含めない**: `bd init` は `.beads/` だけでなく **git リポジトリを作り commit まで行い**、
     `AGENTS.md`・`CLAUDE.md`・`.claude/`・`.codex/`・`.agents/` も生成する（M-10・D19-d）。
     **リポジトリ内で実行してはならない**。統合テストの `bd init` は `tmp_path` 配下だけで、
     `assert find_repo_root() not in tmp_path.parents` の止め木を通る。
   - **`set-state` を含めない**: `state` 次元の排他は保証するが **event bead を新規作成する副作用**が
     ある（M-11）。排他は `set_labels` が board 側で保証する。
   - 検査は `BdCommand` を組み立てる時点で行い、**外れていれば `bd` を起動する前に**失敗させる。
2. **内部ストア（embedded Dolt・SQLite）を直接読み書きしない**（読取を含む。憲章 §2・FR-009）。
3. **`shell=False`**。`subprocess.run` にリストを渡す。表題・本文・受け入れ条件は**リストの 1 要素の
   まま**渡し、文字列へ連結しない（FR-011）。
4. **`BEADS_DIR`／`BEADS_ACTOR` は env**（argv に載せない）、**cwd は `BEADS_DIR.parent`** に固定する。
   - `BEADS_DIR` は **`.beads` ディレクトリ自身**であって、それを持つ親ではない（D19-a）。
   - `BEADS_ACTOR` は**書込メソッドが受け取った actor** を呼び出しごとに設定する（クライアント生成時に
     固定しない）。`.` を含む名前（`demo_pj.programmer`）も無改変で読み戻る（M-01）。
   - cwd を親に固定すると `warning: beads.role not configured` が stderr から消え、文言による
     失敗判定のノイズが無くなる（M-30）。
5. **`--json` を必ず付ける**。機械可読にならない出力（**空文字列を含む**）は成功にしない（FR-014）。

## 4. 前提とする `bd` の版

```
bd version 1.1.2 (20e493e56: HEAD@20e493e569c9)
```

- 応答に載る **`schema_version`（実測 1）** を写像の版ガードに使える。
- **版を上げたら次の 3 つを再確認する**（D21-e・FR-062・R-26）:
  1. spec `specs/003-board/spec.md` の「JSON キー写像表」
  2. 本ファイル（§3 の規約・§5 の注意）
  3. `specs/003-board/contracts/bd-cli.md`（argv・失敗判定・ロック待ち）

## 5. 改修時の注意 7 点

1. **`bd comment` の本文は `--` の後ろ、`--json` は `--` の前**（M-28）。
   `["comment", <id>, "--json", "--", <body>]` の形にする。`--` が無いと本文が `-` で始まったときに
   `Error: unknown shorthand flag` で落ちる。**封筒・停止理由・受け入れ条件は Markdown 箇条書き＝
   `-` 始まりが通常経路**なので、これは例外ではなく常態である。逆に `--json` を `--` の後ろに置くと
   本文に取り込まれる。
2. **`bd create` の表題は `--title`**（位置引数を使わない。M-29）。フラグ形式の値は `-` 始まりでも
   安全に渡る（`--title` / `-d` / `--acceptance` / `-r` で実測）。
3. **ラベルはフラグを繰り返す**（`-l A -l B`）。カンマで連結しない。`frozenset` は `sorted()` して
   から並べる（argv を決定的にするため）。
4. **`config.json` の `list_limit_none` を `false` にすると、`children_of` と `list_working` が
   黙って 50 件で切れる**（M-13）。`bd list` の既定 `--limit` は 50 で、`0` が無制限である。
   `has_open_issues` にはこの設定を効かせない（R-17。存在の有無しか要らず、既定の上限で答えが変わらない）。
5. **`has_open_issues` は bd の既定フィルタに依存する**（M-25）。`--all` も `-n 0` も付けないので、
   bd の既定＝「closed を除く全状態（`in_progress` を含む）」が「未完了」の意図と一致することが前提。
   状態を明示列挙しないのは、列挙すると bd の状態語彙が増えたときに黙って漏れるためである。
6. **`deferred` の課題を読むと例外になる**。`bd` の `--help` は状態に `deferred` を挙げるが、契約
   `IssueStatus` は 4 値（`open`／`in_progress`／`blocked`／`closed`）しか持たない。人間が
   `bd update --defer` した課題を読むと `BdMappingError` → `BoardUnavailable` になる（既定へ黙って
   倒さないため＝ FR-040）。本システムは `--defer` を使わないので通常経路では起きない。
   契約への追加は `packages/common/` の変更にあたるため design-sync B-5。
7. **`dependencies` の形はサブコマンドで違う**（bd 1.1.2 実測）。
   - `bd show` / `bd dep list`: **課題の完全形 ＋ `dependency_type`** → `IssueRef` に写せる
   - `bd list`（`--flat` を含む）: **辺の形**（`issue_id`／`depends_on_id`／`type`／`created_at`／
     `created_by`／`metadata`）。`status`・`title`・`labels` を持たない
   そのため `to_issue(raw, with_dependencies=False)` を `list`／`ready` 経路に渡している。
   **`children_of`／`list_working`／`claim_next` が返す `Issue` の `dependencies` は常に空**である。
   依存を見たい呼び手は `show` を使うこと。ここを「空だから依存が無い」と読むと誤る。

**`claim_next` が `None` を返す条件**（実測）: `bd ready` は**ブロックされている課題を出さない**。
未回答の質問を `add_dependency(<課題>, <質問>)` で張ると、その課題は質問が close されるまで
`claim_next` に出てこない（＝「人間の回答待ち」が bd 側の機構で成立する）。`None` は失敗ではなく
「今は対象が無い」であり、board は例外にしない。**呼び手が「対象なし」と「ブロック中」を区別したい
なら `show` の `dependencies` を見る**（board はその判断を持たない＝ FR-007）。

**コード定数と設定値の境目**:

| 種類 | 中身 | 置き場 |
|---|---|---|
| コード定数 | 先頭 500 文字（`OUTPUT_HEAD_CHARS`）・argv 要素 200 文字（`ARGV_ELEMENT_CHARS`）・rc センチネル `-1`／`-2`・not-found／unavailable の判定文言 | `repository.py` |
| 設定値（CFG-22〜26） | `timeout_sec`・`lock_wait_max_attempts`・`lock_wait_base_sec`・`lock_hints`・`list_limit_none` | `packages/features/board/config.json` |

**ロック競合の文言（CFG-25）だけが設定値**である。ロック競合は実測できなかった（M-07: 6 並行・
20 並行の書込でも再現せず、embedded Dolt が内部で直列化する模様）ため、PoC 実装の 4 語を既定として
継承している。判定に外れた失敗は `other` に倒れ、**再実行されずに** `BoardUnavailable` になる（安全側）。

## 6. 例外とログの「別物」の関係（崩さないこと）

| 対象 | 例外の `argv`（`BoardUnavailable.argv`） | ログの `argv` |
|---|---|---|
| 自由文字列の値（`--title`・`-d`・`--acceptance`・`-r` の値と `--` の後ろの本文） | `<title:42chars>` の**プレースホルダ**（長さだけ残す） | **実値**（`RedactFilter` が伏字を掛ける） |
| フラグ名・ラベル・課題 ID | そのまま（200 文字で切り詰め） | 同左 |

理由: `RedactFilter` は `logging` のフィルタで**例外の属性には掛からない**一方、
`BoardUnavailable.argv` は呼び手（monitor）が**停止痕跡として盤面のコメントへ書き出す**経路を持つ。
完全な argv の再現は**ログ側で行う**。この 2 系統を混ぜてはならない（R-09）。

## 7. テストコマンド

```bash
uv run pytest packages/features/board/tests                      # 単体 + 統合
uv run pytest packages/features/board/tests -m "not requires_bd" # bd の無い環境（単体だけ）
uv run ruff check packages/features/board
uv run pyright packages/features/board
```

- **単体は `subprocess` を 1 度も起動しない**（FR-060）。`BdRunner` を満たす `StubRunner`
  （`tests/board_testing.py`）を `BoardService(runner)` に注入する。
- `test_repository_argv.py` / `test_repository_retry.py` だけは `BdCli` の内部を検査するため
  `subprocess.run` を `monkeypatch` の偽物に差し替える。実行ファイルを起動しないので `requires_bd` は
  付けない。
- 統合（`requires_bd`）は実 `bd` に対して行う。**ロック競合は実物で再現できない**ため統合側では
  検査しない（機構の検査は `test_repository_retry.py`）。
- 共有部品は `tests/conftest.py` ではなく **`tests/board_testing.py`** に置く。`tests/unit/` と
  `tests/integration/` の双方に `conftest.py` があり、どちらも top-level 名 `conftest` として
  読み込まれるため、`from conftest import …` は先に読まれた方に解決してしまう。
