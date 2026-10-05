# SKILL: repo — プロジェクトのローカルリポジトリ（作業ツリー）の唯一のアダプタ

**契約**: `common/interfaces/repo.py`（`RepoReader` 3 メソッド ＋ `RepoWriter` 2 メソッド。IF-51〜55）
**設計**: `docs/design/phase5/repo/p5_repo_data_model.mmd`・設計判断記録 D25
**利用者**: executor（repo 系の道具 4 種）・orchestrator（完了ゲートの実在確認）

---

## 責務

`project.yaml#repos.local_path` が指す git の作業ツリーに触れる**唯一のアダプタ**である（憲章 §2・D25-a）。
`RepoReader` と `RepoWriter` を **1 つの `RepoService`** で実装する（`repo_reader is repo_writer`）。

**判断を持たない**。次はいずれも repo の外にある:

| 判断 | 持ち主 |
|---|---|
| 誰が読み書きしてよいか | executor の前段（casbin の `tool:<name>` と `repo:files` の read／write） |
| 成果物の内容が良いか | orchestrator（レビュー） |
| 完了ゲートの合否 | orchestrator（repo は**実ファイルの有無**だけを返す） |
| commit・push | 人間（**git 操作の手段を持たない**＝ D25-c） |

HTTP 経路も UI ページも持たない。`.env` も読まない（ルートはプロジェクトごとに違い、呼び出しごとに
`ProjectRepos` で渡される）。

---

## 主要ファイル

| ファイル | 置くもの |
|---|---|
| `src/repo/__init__.py` | `RepoConfig`（`config.json`）・`config_path()`・`RepoDeps`（空）・`RepoHandle`・`build()` |
| `src/repo/service.py` | `RepoService`（5 メソッド）・パスの形の規則・上限・**契約例外への翻訳** |
| `src/repo/repository.py` | `FsAdapter`（作業ツリーの判定・閉じ込め・読取・列挙・原子的書込・削除）。**契約例外を送出しない** |
| `src/repo/exceptions.py` | `FsError` 階層（内部）・`RepoStartupError` 階層（起動時） |

## 規約 1: 閉じ込めは `FsAdapter.resolve` の 1 か所

読み書きはすべて `resolve` を通った実パスに対して行う。`resolve` は `realpath` で実体まで解決し、
作業ツリーの外（シンボリックリンク経由を含む）と `.git` の配下を拒む。**`resolve` を迂回する経路を作らない**。

## 規約 2: 形の規則は `RepoService._check_rel` の 1 か所

空・絶対パス・`..`・バックスラッシュ・NUL は `invalid`、`.git` は `git_dir`、拒否パターン（`config.json` の
`deny_patterns`。既定は `.env`・鍵ファイル）は `denied`。拒否パターンは**一覧にも出さない**（秘密を LLM に送らない）。

## 規約 3: 未 clone は「作業ツリーが無い」

`local_path` 直下に `.git`（ディレクトリかファイル）が無ければ `RepoNotFound`。**上位ディレクトリの
git を探しに行かない**（`git -C` の上方探索とは違う。ホームが git 管理下でも誤爆しない）。

## 規約 4: 書込は原子的・権限を引き継ぐ

同じディレクトリの `.repotmp-*` に書いて `os.replace`。既存ファイルは元の権限（実行ビット）を、新規は
`0o644` を付ける（`mkstemp` の `0600` を持ち込まない）。親ディレクトリは閉じ込めの内側でだけ作る。

---

## 検証

```bash
uv run pytest packages/features/repo/tests
uv run ruff check packages/features/repo
uv run pyright packages/features/repo
```
