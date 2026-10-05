# 設計判断 V4-D19: `BEADS_DIR` の意味を bd の公約に合わせる（`.beads` ディレクトリ自身）の裁定

**裁定日**: 2026-08-20
**論点の出典**: `.env` の実値を用意する作業（環境変数準備セッション）で、`bd` 現物（v1.1.2）の `BEADS_DIR` の意味が設計メモの定義と食い違うことを実測で発見した。
**番号**: 索引を着手時に再読し D19 を確定（D18 が最大。D13／D14 の重複の再発防止。並行作業がある場合は要確認）。
**レジーム**: append-only（§0-4）
**影響**: **D15-d の一部を改訂**（キーの置き場＝`.env` は不変。値の意味だけを確定）。board-F17・EIF-01・CFG-07・`.env.example` を改訂（§5）。

## 1. 裁定

| # | 論点 | 裁定 |
|---|---|---|
| D19-a | `BEADS_DIR` の値の意味 | **`.beads` ディレクトリ自身の絶対パス**（例 `<data>/beads/.beads`）。「`.beads` を持つ親ディレクトリ」ではない。`bd` 現物の公約（`BEADS_DIR` は直下に `config.yaml` を持つ `.beads` を指す。§4）に合わせる。必須・絶対パス（D15-d）は不変 |
| D19-b | board-F17 の起動時検査 | 「`BEADS_DIR/.beads` の実在」→ **「`BEADS_DIR` が実在するディレクトリで、直下に `config.yaml` がある」**。欠ければ従来どおり例外で起動中断（憲章 §5） |
| D19-c | `bd` の呼び出し方 | `BdCli` は **`BEADS_DIR` を子プロセスの env に載せて `bd` を実行する**（cwd からの上方探索に頼らない）。`BEADS_ACTOR` と同じく呼び出しごとに設定する env（P5D-004・EIF-01）。cwd は問わない |
| D19-d | beads ワークスペースの置き場 | **リポジトリ内に `bd init` しない**。運用データ置き場（`SETTINGS_ROOT`／`TRIGGER_ROOT` と同じ親）の下に作る。理由は embedded Dolt の実体が git 管理下に入るため（`.gitignore` にも `.beads` は無い） |
| D19-e | 実装作業用の beads との分離 | 実装作業そのものの課題管理に beads を使う場合は**別ワークスペース**とし、`.env` の `BEADS_DIR` を共有しない（製品の運用データと開発課題が 1 つの DB に混ざる）。本 ver では `.env` に載るのは board が読む 1 つだけ |

## 2. 根拠

- **D19-a**: 事実の優先規則（CLAUDE.md「事実については 現物コード > 報告書 > 設計メモ」）。`bd` 現物が `BEADS_DIR` を `.beads` 自身として解決している以上、設計メモ側の定義が誤りである。加えて **同名・異義は機構で守れない**: 解決順序は ⑦OS 環境変数 > ⑥`.env`（D15-b）なので、人間がシェルで `export BEADS_DIR=<親>` した値がそのまま app に効く。意味が食い違っていると、bd 直叩き（`no config.yaml found in BEADS_DIR`）か board のどちらかが必ず壊れる。D15-b が「運用規約でなく機構で守る」を採った方針とも整合する
- **D19-b**: D19-a の機械的な帰結。`.beads/config.yaml` は `bd init` が必ず作る（現物で確認）ので、`.beads` らしさの検査として `config.yaml` の実在が最も安価かつ確実
- **D19-c**: `BEADS_DIR` を env で渡せば cwd に依存しない（PoC 実測。§4）。cwd 依存にすると、systemd 常駐で WorkingDirectory を取り違えたときに別ワークスペースへ黙って書き込む事故が起こりうる
- **D19-d**: 領域 A・B（`SETTINGS_ROOT`／`TRIGGER_ROOT`）を `.env` の絶対パスでリポジトリ外に置いた D15-c／P5D-040 と同型。beads だけ扱いを変える理由がない
- **D19-e**: board が読む `BEADS_DIR` は「製品の盤面」の所在であり（憲章 §2 外部ストアは所有モジュールの Protocol 経由でのみ）、開発作業の課題管理は本システムの外

## 3. 棄却した選択肢

| 選択肢 | 棄却理由 |
|---|---|
| 設計メモどおり「親ディレクトリ」を維持し、「board は env で `BEADS_DIR` を渡さない」「人間はシェルで export しない」を運用規約で縛る | 人間の規律に依存し機構で守れない。環境情報ファイル（D15-c）に実値を控える運用と噛み合わず、コピーして export した瞬間に壊れる |
| 変数名を `BEADS_WORKDIR` 等に改名して親を指す | 改訂量は同等以上で、`bd` の公約名をわざわざ捨てる利点がない。将来 bd の文書を読んだ実装者がかえって混乱する |
| `.env` には親を書き、board が内部で `/.beads` を連結する | `.env` の値と bd に渡る値が一致せず、D19-a と同じ「同名・異義」を MOD 内部に押し込むだけ |
| リポジトリルートに `bd init` して cwd 解決に任せる | embedded Dolt の実体が git 管理下に入る。`SETTINGS_ROOT`／`TRIGGER_ROOT` と非対称 |

## 4. 事実・確認したこと

- `bd version 1.1.2 (20e493e56)`（`/home/user/.local/bin/bd`。`BD_BIN` の既定値候補として実在を確認）
- **`bd start` というサブコマンドは無い**（`Error: unknown command "start" for "bd"`）。`.beads/` を作るのは `bd init`（`-p, --prefix` の既定は「カレントディレクトリ名」）。`start` があるのは beads-ui の `bdui start`
- `bd` 実行ファイル内のメッセージ（`strings` で確認。2026-08-20）:
  - `no config.yaml found in BEADS_DIR (%s) (run 'bd init' first)` — `config.yaml` は `.beads/` の直下にある
  - `or set BEADS_DIR to point to your .beads directory`
  - `BEADS_DIR points to a different project's .beads/`
  - `check BEADS_DIR/worktree setup, or run 'bd init' to create a new database`
  - `BEADS_DIR points to unsafe location: %s`
- PoC の実測記録も `.beads` 自身を指している: `crewai_beads_renkei`（`BEADS_DIR=/.../crewai_beads_poc/.beads bdui start`。同 76 行「cwd が /tmp でもボードに PoC の実データが返ることを WebSocket 経由で確認した」＝env だけで cwd 非依存に解決できる）。同 PoC の現物 `.beads/config.yaml` も存在する
- `bd init` は **git リポジトリ外でも動く**（バイナリ内の変更履歴 `FIX: git.IsWorktree() hang on Windows - bd init no longer hangs outside git repos`、フォールバック `not a git repo, falling back to .beads/backup`）
- 作業前のリポジトリルートは未初期化（`bd where` → `No active beads workspace found`）
- PoC 無変更: `bd` を `pocs/*/.beads` に向けた実行は行っていない（読み取りは `strings`／`sed` のみ）
- **本裁定に伴う実測（2026-08-20。`bd init -p ambi` で `/home/user/task_ambi_data/beads` に新規作成した後、cwd＝リポジトリルートで実行）**:
  - `BEADS_DIR=…/task_ambi_data/beads/.beads bd where` → `/home/user/task_ambi_data/beads/.beads`（prefix `ambi`・database `…/.beads/embeddeddolt`）を返す。**cwd に依存せず解決する**（D19-c の裏付け）
  - `BEADS_DIR=…/task_ambi_data/beads bd where`（旧定義＝親）→ `Error: No active beads workspace found.`。**旧定義の値では解決できない**（D19-a の裏付け）
  - `BEADS_DIR` なし・cwd＝リポジトリルート → 同じく `No active beads workspace found.`（リポジトリ内に `.beads` を作っていないことの確認。D19-d）
  - `bd -C <dir> init` は使えない（`cannot use -C directory …: no beads project found`）。`-C` は既存プロジェクト向けで、新規作成はそのディレクトリで `bd init` を実行する
  - `bd init` は対象ディレクトリに `AGENTS.md`・`CLAUDE.md`・`.claude/`・`.agents/`・`.codex/` も生成する（`--skip-hooks` 等で抑制可）。運用データ置き場なのでリポジトリには影響しない

## 5. 改訂したもの（本記録の裁定に伴う）

| 対象 | 改訂 |
|---|---|
| `appendix_env_design/x3_config_catalog.csv` CFG-07 | 説明「`.beads` を持つディレクトリ（bd の実行 cwd）」→「`.beads` ディレクトリ自身（直下に config.yaml。D19-a）」。起動中断条件を D19-b に |
| `appendix_env_design/.env.example`（およびリポジトリ直下の同名コピー） | `BEADS_DIR` の既定値を `…/task_ambi_data/beads/.beads` に。コメントを D19-a／D19-d に合わせる |
| `phase5/board/p5_board_feature_list.csv` board-F17 | 検査を「`BEADS_DIR` 直下の `config.yaml` の実在」に。失敗条件を同様に |
| `phase5/board/p5_board_notes.md` §1 | build の記述を D19-a／D19-b に。`repository.py` の説明に `BEADS_DIR` を env で渡す旨（D19-c） |
| `phase5/board/p5_board_data_model.mmd` `BoardEnv` | `+BEADS_DIR` のフィールド説明を `.beads` 自身に |
| `phase6/p6_external_interface.csv` EIF-01 | 呼び出し方（`BEADS_DIR` を env で渡す。D19-c）と build 失敗条件を改訂 |
| 横断 | `docs/design/設計判断_索引.md`（D19）・`docs/initial_memo/_変更履歴.md`・`docs/initial_memo/7-2.トピック索引.md` |

## 6. 決めていないこと（未決）

1. `BdCli` が `BEADS_DIR` を env で渡す実装の最終形（cwd も併せて固定するか）は M1 の board 実装で確定。D19-c は方針
2. `bd` のサブコマンド・ホワイトリスト（board/SKILL.md）に `init` を含めない点の明文化は M1
3. **`outcome/` → `docs/design/` の移設が未完**: `outcome/design/{p5r1,phase45,x1,x4}`・`outcome/init/`・`outcome/設計判断_V4-D18_…md` がディスク上に存在せず git HEAD にのみ残る。索引のリンク（`./design/x3/…` 等）も旧パス・旧ディレクトリ名（x2→`appendix_cluster_test_plan`／x3→`appendix_env_design`）のままで解決しない。本記録の裁定とは独立の課題として登録する

## 公開時の注記

公開にあたり、5・38・46・50・51 行目を置き換えた（個人の環境を示すパス・名前・時刻、非公開の文書や作業の呼び名、非公開文書へのリンク）。置き換えの方針は [docs/design/README.md](../README.md) を参照。それ以外は記録時点のまま。
