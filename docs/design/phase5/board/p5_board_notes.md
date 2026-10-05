# board（BC-02 盤面）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_board_sequence.mmd`（主図）／`p5_board_sequence_reads.mmd`（副図）／`p5_board_data_model.mmd`／`p5_board_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-003〜007。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-01〜22（提供 22 行）／`../../phase4/p4_module_dependency_notes.md` §1・§2／`../../phase2/p2_domain_model.mmd` DM-13〜16／`../../phase2/p2_sequence_UC-I1.mmd`・`UC-04`・`UC-11`／D01・D04-b・D06-c/i・D08-e・D09-c/d／PoC `crewai_beads_renkei`（PoC 基準版、読取のみ・証跡確認）
**レジーム**: 改訂可（図・csv と同時に更新）。

## 0. 一言でいうと

board は **beads の唯一のアダプタ**（bd CLI のみ。内部 Dolt は触らない）で、`BoardReader`（6 メソッド）と `BoardWriter`（8 メソッド）を 1 つの `BoardService` で実装する。盤面導出（`children_of`／`open_question_of`／`list_working`）は「盤面から都度引く」だけで、**何を残骸・未回答とみなして再投入するかは持たない**（D01・D09-d）。書込は検証しない（`create` の読み戻しのみ例外。P5D-007）。他 Protocol に依存せず（`BoardDeps` は空）、router・ui を持たない。

## 1. ファイル構成（憲章 §3。不要なファイルは置かない＝DEC-009 の読み替え）

```
packages/features/board/
├── pyproject.toml
├── config.json          # BoardConfig: timeout_sec／lock_wait_max_attempts／lock_wait_base_sec／lock_hints／list_limit_none（bd_bin／beads_dir は .env の BD_BIN／BEADS_DIR へ。付3 D15-d）
├── SKILL.md             # bd サブコマンドのホワイトリスト規約（linter (3) で捕まえられない CLI 依存はここで守る。憲章 §1）
├── src/board/
│   ├── __init__.py      # build(deps: BoardDeps, *, config: BoardConfig | None = None) -> BoardHandle（board_reader／board_writer／router=None）
│   ├── service.py       # BoardService(BoardReader, BoardWriter): 14 メソッド。ラベル組立（kernel Labels）・導出・冪等化・例外翻訳
│   ├── repository.py    # BdCli: subprocess 実行基盤（ホワイトリスト・BEADS_DIR／BEADS_ACTOR を env で付与（D19-c）・--json・書込ロック・ロック待ち・構造化ログ）
│   ├── models.py        # bd JSON → Issue／Comment／IssueRef の写像（外部ストアとの受け渡し用内部モデル）
│   └── exceptions.py    # BdCommandError（内部）。契約例外 IssueNotFound／BoardUnavailable／BoardWriteUnverified は common/interfaces/board.py
└── tests/               # 単体: BdCli をスタブにして service を検査／統合: 一時 dir に bd init した実 beads で（憲章 §8）
```

置かないもの: `router.py`・`ui.py`・`schemas.py`・`dependencies.py`（HTTP 入口も UI も注入依存も無い）。

`build(deps: BoardDeps, *, config: BoardConfig | None = None) -> BoardHandle`（D11-f・DIFF-08）: `BoardDeps` は空。設定値の差し替えは**任意キーワード引数 `config: BoardConfig | None`** で受ける（統合テストクラスタが `timeout_sec`・ロック待ちを差し替えるため。解決順序 ⑧）。`registry.py` の呼び出し形 `build_board(BoardDeps())` は変わらない。`BoardHandle(board_reader: BoardReader, board_writer: BoardWriter, router=None)`。build は `.env` の `BEADS_DIR`・`BD_BIN`（BoardEnv。vault／settings／trigger と同じ「ルートは .env」の型。付3 D15-d）と config を読み、`bd` の実在と **`BEADS_DIR` 直下の `config.yaml`** を確認できなければ例外で起動を中断する（憲章 §5）。`BEADS_DIR` は **`.beads` ディレクトリ自身**であって「`.beads` を持つ親」ではない（D19-a／D19-b）。`bd` へは cwd ではなく env で渡すので実行ディレクトリに依存しない（D19-c）。

## 2. Protocol 実装の対応（契約 csv → fn）

| Protocol.メソッド | if_id（利用側） | fn | 実装の要点 |
|---|---|---|---|
| BoardReader.show | IF-01/02/03 | board-F02 | `bd show <id> --json` → Issue。not found → IssueNotFound |
| BoardReader.children_of | IF-04 | board-F03 | `bd list --parent <id> --all -n 0`（closed 含む・上限なし。bd.py children_of の注記どおり） |
| BoardReader.open_question_of | IF-05 | board-F04 | show の dependencies から kind:question ∧ status≠closed の最初の 1 件を選び、その id で **2 回目の show**（DIFF-05・R-19） |
| BoardReader.list_working | IF-06 | board-F05 | `bd list -l state:working (-l …) --all -n 0` → closed 除外 |
| BoardReader.comments | IF-07/08 | board-F06 | `bd comments <id> --json`（生データ。P5D-005） |
| BoardReader.has_open_issues | IF-09 | board-F07 | `bd list -l project:<key>`（`--all` も `-n 0` も付けない。既定＝完了済みを除く全状態＝`in_progress` を含む。M-25） |
| BoardWriter.claim_next | IF-10 | board-F08 | `bd ready --claim --limit 1 --label …` ＋ `update --add-label state:working` |
| BoardWriter.create | IF-11 | board-F09 | `bd create … --parent --no-inherit-labels` ＋ 読み戻し検証（P5D-007） |
| BoardWriter.add_dependency | IF-12 | board-F10 | `bd dep add <from> --blocked-by <to>` |
| BoardWriter.move_dependents | IF-13 | board-F11 | `bd dep list <old> --direction=up -t blocks` → 各 dependent に `dep add … --blocked-by <new>` |
| BoardWriter.release | IF-14/15 | board-F12 | `update --status open --assignee ""` ＋ `update --remove-label state:working` |
| BoardWriter.comment | IF-16/17/18 | board-F13 | `bd comment <id> --json -- <body>`（`--` 区切りが必須で `--json` は `--` の前。M-28） |
| BoardWriter.close | IF-19/20 | board-F14 | show で closed なら no-op、else `bd close -r` |
| BoardWriter.set_labels | IF-21/22 | board-F15 | `--remove-label`／`--add-label` を **1 回の `bd update`** にまとめる（呼ぶ前に `remove ← remove ∖ add` へ正規化。DIFF-10・D21-h）。state 次元は排他 |

板の外に出る責務（board が持たないもの）: 起票順序と UNSAFE 判定（orchestrator。FN-49）、失敗写像の内容（monitor。F/W/I）、封筒書式（monitor）、人間／AI 判定（orchestrator＋MemberResolver）、削除ガードの拒否（settings）。

## 3. 受け渡し型（common/interfaces/board.py。属性の正本は data_model の namespace）

`Issue`（id・title・description・status・labels・parent・assignee・acceptance 生文字列・dependencies: list[IssueRef]・created_at/updated_at）／`IssueRef`（**本フェーズで追記**: id・title・status・labels・dependency_type）／`IssueStatus`（open/in_progress/blocked/closed）／`Comment`（id・issue_id・actor・body・created_at）／`IssueSpec`（title・description・labels・parent・acceptance・priority=2・issue_type="task"）／`ClaimFilter`（labels frozenset AND・project_key）。例外: `IssueNotFound(NotFoundError)`・`BoardUnavailable(UnavailableError)`・`BoardWriteUnverified(UnverifiedError)`（基底は kernel。P5D-001）。

`IssueRef` の追記は p4 notes §2 の 22 件表に反映する（フェーズ末の改訂でまとめて。`_変更履歴.md` に記帳）。

## 4. シーケンス図

| 図 | 対象 | 出典 |
|---|---|---|
| `p5_board_sequence.mmd`（主図） | 起票 1 周: claim_next → show → create×n（読み戻し）→ add_dependency → move_dependents → children_of（確認読取）→ close | UC-I1 propose 段・FN-49・D01 |
| `p5_board_sequence_reads.mmd`（副図） | 盤面導出の読取（list_working／open_question_of／has_open_issues／comments）と写像系の書込（set_labels／release） | UC-11 recover・UC-04・UC-13・UC-08 |

レーン: `BoardWriter／BoardReader`（呼び手＝Protocol 名。P5D-003）／`board/service.py`／`board/repository.py`／`bd CLI`。kernel（Labels・LoggerFactory・CorrelationContext）は Note のみ。

## 5. PoC からの継承と変更（証跡: `bd.py`、PoC 基準版）

| 項目 | PoC | V4 |
|---|---|---|
| actor | クライアント生成時に固定（`BeadsClient.actor`、共通 `crew-exec-01`） | **呼び出しごとに引数**（メンバ ID 由来の bd actor。D06-c）→ env `BEADS_ACTOR` を実行ごとに設定 |
| 監査ログ | `bd_audit.jsonl`（`_audit`） | 構造化ログへ統合（P5D-006） |
| ロック待ち | 0.5s×n・最大 3 回・文言はコード定数 | 同機構、パラメータは config.json（P5D-004） |
| claim | `ready --claim --limit 1 --label <1 枚>` | ClaimFilter（複数ラベル＋project）。**複数 `-l` は AND で成立**（M-02 で実測。`--claim --limit 1` と併用しても原子的に 1 件） |
| 残骸列挙 | `list --status in_progress -a <assignee>` | `list -l state:working …`（actor が kickoff ごとに変わるため assignee では引けない） |
| dependents 付け替え | loop 側（`loop.py:1399`） | board の `move_dependents` に降ろす（PoC の `dependents_of` を内包） |
| release | `update --status open --assignee ""` | 同＋`--remove-label state:working` |
| create の読み戻し | なし | あり（P5D-007） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py board --render` → ERRORS: none（(i)〜(vi)）
- 主図・副図の participant は `BoardReader`／`BoardWriter`／`board/service.py`／`board/repository.py`／`bd CLI` のみ
- 提供 22 行の if_id が feature_list に現れ、14 メソッド全てに実装 fn がある

## 7. 未決だった論点（M1 board 実装の実測で解消。仮 ID は design-sync「追記候補」の D21-x）

1. **bd actor に `.` を含む名前（`<project_key>.<persona_key>`）が使えるか**（D06 §6-2）→ **使える**。`BEADS_ACTOR="demo_pj.programmer"` でコメント追記・更新が成功し `author` に無改変で読み戻る（M-01）。board は渡された actor をそのまま env に載せるだけなので**設計は不変**（D21-a）
2. **`bd ready --claim` に `--label` を複数付けて AND にできるか** → **できる**。`-l A -l B`（繰返し）と `-l "A,B"`（カンマ）は同義で AND。`--claim --limit 1` と併用しても原子的に 1 件だけ確保される（M-02・D21-b）
3. **`bd update --add-label` に複数指定が効くか** → **効く**（カンマ区切り・繰返しとも）。`--remove-label` も同形で、**両者を 1 回の `bd update` に同時指定できる**。ただし同一ラベルが双方に現れると **remove が勝つ**（M-03・M-12・D21-c）
4. `Labels.parse()` の未知次元（人間が付ける自由ラベル）の扱い: board は labels を文字列のまま保持し、次元の検査をしない（common notes §5 の未決はこれで閉じる方向。P5D 行の追記は design-sync B-2＝ユーザ作業）
5. bd の JSON スキーマ（`bd show --json` のキー名）はバージョン依存。**前提とする版は `bd version 1.1.2 (20e493e56)`**（M-14）で、これを SKILL.md に固定する。応答に載る `schema_version`（実測 1）を写像の版ガードに使える。版を上げたら spec「JSON キー写像表」・SKILL.md・`specs/003-board/contracts/bd-cli.md` を再確認する（D21-d・D21-e）
