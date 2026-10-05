# SKILL: vault — Obsidian vault 領域の唯一のアダプタ

**契約**: `common/interfaces/vault.py`（`VaultReader` 5 メソッド ＋ `OutcomeWriter` 1 メソッド。IF-31〜37）
**設計**: `docs/design/phase5/vault/`（`p5_vault_notes.md`・`p5_vault_feature_list.csv`・`p5_vault_data_model.mmd`・`p5_vault_sequence.mmd`）
**spec / plan**: `specs/004-vault/`（内部境界の正本は `contracts/fs-adapter.md`・`contracts/name-gate.md`）

---

## 責務

Obsidian vault 上のフォルダ・ファイルに触れる**唯一のアダプタ**である（憲章 §2）。`VaultReader` と
`OutcomeWriter` を **1 つの `VaultService`** で実装する（FR-005。`vault_reader is outcome_writer`）。

**判断を持たない**（FR-007）。次はいずれも vault の外にある:

| 判断 | 持ち主 |
|---|---|
| 何を読ませるか（Tool の選択・権限） | executor・settings |
| 成果物の内容が良いか | orchestrator |
| 完了ゲートの合否 | orchestrator（vault は**実ファイルの有無**だけを返す） |
| 上書きしてよいか | 呼び手（vault は `overwritten` を観測印として返すだけ＝ P5D-008） |
| 役割どうしの分離 | 呼び手（→ 規約 3） |

HTTP 経路も UI ページも持たない（FR-001・FR-068）。

---

## 主要ファイル

| ファイル | 置くもの |
|---|---|
| `src/vault/__init__.py` | `VaultEnv`（`.env`）・`VaultConfig`（`config.json`）・`config_path()`／`env_file_path()`・`VaultDeps`（空）・`VaultHandle`・`build()`（起動時 4 検査・文字コードの実効値解決・組み立て） |
| `src/vault/service.py` | `VaultService`（6 メソッド）・名前ゲート・内容ゲート・`FolderCheck` の唯一の生成点・`FileRef` への写像・**契約例外への翻訳** |
| `src/vault/repository.py` | `FsAdapter`（解決・述語・読取・列挙・退避・原子的書込・構造化ログ）。**契約例外を 1 つも送出しない** |
| `src/vault/models.py` | `ResolvedPath`・`NameGateResult`・`NameViolation`・`FileStat`（すべて `frozen=True, slots=True`） |
| `src/vault/exceptions.py` | `FsError` 階層（内部）・`VaultStartupError` 階層（起動時） |

**置かないもの**:

| 置かないもの | 理由 |
|---|---|
| `router.py` | vault は HTTP 経路を持たない。IF-31〜37 の利用者はすべてモジュール（orchestrator・settings・executor）で、外部から HTTP で呼ばれる口が無い |
| `ui.py` | Obsidian vault の閲覧・編集は **Obsidian 自身**が行う（人間の所有物＝ OB-9）。5 つ目の登録点（`apps/ui/src/ui/pages.py`）も行わない |
| `schemas.py` | HTTP 境界の型は経路が無ければ存在理由が無い。モジュール間の受け渡し型は `common/interfaces/vault.py` が正本で、vault 側に再定義しない（FR-006） |
| `dependencies.py` | `Depends` 用 provider は router があってはじめて要る。`VaultDeps` は**空**（FR-003）で受け口も無い |

---

## 規約 1: `outcome/` の外に書く経路を作らない

書込先は **PJ フォルダ/`outcome/` 直下フラットだけ**（D06-a・OB-8）。`outcome/` の外へ書く**契約メソッドを
追加しない** — OB-10 を実行時判定ではなく**契約の形**で満たす（P5D-011）。`grep -c "def write" service.py`
が **1**（`write_outcome` だけ）であることが構造の担保である。

`mkdir` はコード全体で **1 箇所**（`FsAdapter._archive` の 1 階層・親が実在するときのみ）。
**`parents=True` を書かない**（FR-014・SC-004）。人間の所有物（PJ フォルダ・役割フォルダ・`outcome/`）は
システムが作らない。

## 規約 2: `FsAdapter.resolve` を迂回しない

ファイルを開く・stat する・列挙する経路は**必ず `resolve` を通す**。`service.py` に `open`／`Path.read_*`／
`Path.write_*`／`os.*` を書かない。**import-linter では捕まえられない**（ファイル I/O は標準ライブラリで、
禁止すべき「外部パッケージ」が無い）ので、ここと `quickstart.md` S3 の grep とレビューで守る。

## 規約 3: 役割の分離は呼び手が行う

`read_input` は契約上 `role` を受け取らず、`FileRef` も役割を持たない。したがって `役割` の領域根は
**宣言された役割フォルダの和集合**になり、**vault 側で 1 つに絞る手段は構造的に存在しない**（FR-026a・C-4）。
分離が要る呼び手（executor）は `ProjectFolders.model_copy(update={"role_dirs": {...}})` で当該実行の役割だけを
宣言に含めて渡す。

**一覧経路は既に閉じている**: `list_inputs(folders, role)` は `role_dirs[role]` だけを歩き、宣言された他の
領域の根を枝刈りするため（FR-037a）、他役割の手引きを返さない。開いているのは `read_input` の側だけである。

## 規約 4: 包含の判定に文字列比較を使わない

`casefold` / `lower` / `normcase` / `startswith` / `is_relative_to` を判定の根拠にしない（FR-024）。
包含は **実体の同一性**（`os.stat` の `(st_dev, st_ino)` を親方向へ 1 段ずつ照合）で判定する。

実測の根拠:

| 実測 | 内容 |
|---|---|
| M-02 | `os.path.realpath` / `Path.resolve` は**入力のケーシングをそのまま返し**、実体へ正規化しない |
| M-03 | `os.path.normcase` は POSIX で恒等関数（drvfs の大文字小文字非区別を反映しない） |
| M-07 | `str.casefold()` は `'ß'` と `'ss'` を同一視する（**異なる実体を「ルート内」と誤判定しうる**） |
| M-05 | **inode 同一性は drvfs でも信頼できる**（異ケーシングの同一実体で真・別実体で偽） |

この形にしたことで判定が大文字小文字の区別から独立し、検証環境（ext4）と実運用環境（drvfs）で
挙動が一致する。**drvfs 専用のテスト印が要らなくなった根拠でもある**（FR-071）。

---

## 禁止文字集合の出典と、照合した Obsidian の版

出典は **D07-c**（Windows/mac 非互換文字 ＋ 制御文字 ＋ 予約語 ＋ 末尾ドット/空白 ＋ Obsidian の
リンク破壊集合の**和集合**）。**ファイルシステムのエラーを検査の代わりにしない**: 実測 M-11・M-12 により、
これらの名前は**実運用の領域（drvfs）でも検証環境（ext4）でも 19/19 すべて作れてしまう**。拒否できるのは
本ゲートだけである。

### 照合した版を固定する（FR-055a・FR-062・contracts/name-gate.md §5）

- **Obsidian 1.12.7**（`C:\Program Files\Obsidian\Obsidian.exe` の `ProductVersion`＝ M-20）。
- `forbidden_chars` の既定（`\ / : * ? " < > |`）はアプリの**ハードブロック集合**（Windows 分岐 `*"\/<>:|?`）と
  **完全一致**（M-22・M-23）。
- `forbidden_obsidian` の既定のうち 5 種（`# ^ [ ] |`）はアプリの**リンク破壊警告集合** `HT = "#^[]|"` と
  **完全一致**（M-22・M-23）。
- 設計に無い 2 件を**採用済み**（Clarifications 2026-08-23）: **`%%`**（コメント記法。公式ヘルプがリンクを
  壊す例に挙げるが、アプリの警告集合には含まれない）と **ドット始まり**（Obsidian 自身が `msgBadDotfile` で
  拒否するが、vault-F07 の禁止列挙には無かった）。
- **既定値は変更しない**（照合の結果が「書き換え不要」であった＝ P5D-010 はこれで閉じる）。
- **版が上がったら次の 3 箇所を再確認する**: `packages/features/vault/config.json` の既定値／**本ファイル**／
  spec の実測記録 M-20〜M-24。

---

## 契約に無い型が呼び手へ届く 3 件（C-3）

契約 csv の例外列と docstring の `Raises` は**要約であって網羅ではない**（DIFF-05・DIFF-07 が確立した読み）。

| # | 型 | いつ | 根拠 |
|---|---|---|---|
| 1 | `FsArchiveError` | 退避に失敗した（新版を書かず古い版を残す） | FR-048。契約の `kind` は 7 種の閉じた `Literal` で `archive_failed` を作れない（DIFF-06） |
| 2 | `FsIoError` | 権限・同期中の一時的な失敗（「空・偽に倒さない」） | Edge Cases。翻訳せず素通しする |
| 3 | `VaultNotFound` | `write_outcome` で `outcome_dir` が実在しない | R-14・VD-19。外縁も名前も内容も正しく、7 種のどれにも当たらない。呼び手には `check_project_folders` の `missing` と同じ意味で届き、`NotFoundError` 系統で捕捉できる |

---

## 改修時の注意

1. **`extra={"filename": …}` を書かない。** `logging.LogRecord` の**予約属性**であり、渡すと
   `KeyError: "Attempt to overwrite 'filename' in LogRecord"` で**実行時に落ちる**。契約のメソッド引数名が
   `filename` であるため踏みやすい。他の予約キー（`module`・`name`・`args`・`msg`・`pathname`・`lineno`・
   `levelname`・`process`・`thread`）も同様。`extra` に載せてよいキーは `contracts/fs-adapter.md` §7 の一覧
   （`repository.py` は 9 種／`service.py` はそれに `kind`・`violations`・`overwritten` を足した 12 種）。
   ⚠ **契約例外を送出する行には、ログを対で置く**（FR-059「違反のすべてを構造化ログに残す」）。`service.py` の
   `_log_violation`（`VaultPathViolation` 用）／`_log_not_found`（`VaultNotFound` 用。`kind` を持たないので
   `result="not_found"` で見分ける）を通す。**新しい `raise` を足したらログも足す** ―― 落とすと、その操作は
   journal に痕跡を 1 つも残さずに失敗する（`/speckit-converge` 第 5 回が 11 送出点でこれを実測した＝ VD-37）。
   固定しているのは `test_vault_roundtrip.py::test_every_rejection_leaves_a_log_line` の 8 経路だけなので、
   **経路を増やしたらこの表も足す**。`detail` に**ファイルの本文を混ぜない**（FR-060）。
   ⚠ **記録するのは「呼び手に届く送出」だけである**（VD-41）。`_require_inside(…, log_not_found=False)` を
   渡すのは `outcome_exists` **だけ** ―― 成果物フォルダ不在の `VaultNotFound` はその場で握り潰されて呼び手に
   届かず、かつ人間がフォルダを作るまでは**正常状態**（FR-014・SC-004）で、完了ゲートは 1 kickoff の中で
   本メソッドを繰り返し呼ぶ。ここに WARNING を残すと正常運用のたびに journal が埋まり、US7「事後に追える
   唯一の手段がログ」の可読性が落ちる（VD-37 が塞いだ「痕跡が無い」の**逆側**の失敗）。情報は
   `outcome_exists` 自身の INFO 行（`op="exists"`・`rel_path=<outcome_dir>`・`result=False`）が持つ。
   **level を一律に下げて解決しない** ―― 同じ分岐は `read_input`・`read_outcome_file` でも通り、そちらでは
   `VaultNotFound` が呼び手に届くので WARNING が正しい。
   ⚠ **`rel_path` に載せられない識別子は `result` に畳む**（VD-42）。`_log_not_found` の `result` は既定
   `"not_found"` で、役割未宣言の `list_inputs` だけが `"not_found:<役割>"` を渡す（`rel_path` は plan (v)
   の「値を捏造しない」により空文字列のまま。`list_inputs` の**成功**行も `result` に役割を載せており対称）。
   **§7 の 12 種を増やして解決しない**。見分けの印は前方一致で残るので、journal を拾う側は
   `result == "not_found"` ではなく `startswith("not_found")` で見る。
2. **一時ファイルは書込先と同一ディレクトリに置く。** FS をまたぐ `os.replace` は `OSError errno=18`
   （`EXDEV`）で失敗する（M-17）。接頭辞 `.vaulttmp-` が**ドット始まり**であることも本質で、既定の除外接頭辞に
   必ず一致するので一覧にも `outcome_exists` にも現れない（FR-050）。
3. **退避の連番を外さない。** 同一スタンプでの再退避は **旧版を黙って上書きする**（M-19 が実測）。
   差し戻し後の再書込は同一秒内に連続しうる正常経路である（FR-047a）。
4. **`archive_dir` は唯一の出所。** 変えると**除外（列挙の枝刈り）・予約名（名前ゲート #8）・退避先**が同時に
   変わる（FR-030・R-10）。`list_exclude_prefixes` の既定から `_archive` を落としてあるのはこのためである
   （DIFF-09）。⚠ 逆に `list_exclude_prefixes` を空にすると、**一時ファイル `.vaulttmp-*` が一覧に現れる**のと
   **名前ゲート #7（ドット始まり）が無効になる**のが同時に起きる。
5. **`read_outcome_file` にサイズ上限を掛けない。** 掛けると `outcome_exists` が `True` なのに読めない状態が
   生まれ、完了ゲートが壊れる（R-15・VD-20）。**`read_input` 経由では上限が掛かる**非対称は設計どおりである
   （同じ成果物ファイルを兄弟成果物として読むと `too_large` になりうる）。
6. **ゲートの拒否は違反文字を明示する。** 置換・除去・切り詰めをしない（P5D-010・FR-029）。`NameViolation.token`
   を必ず埋める（「禁止文字が含まれます」だけでは AI が直せない）。**違反は全件集める**（早期打ち切りしない＝
   FR-031）。**`too_large` を名前長に流用しない**（内容のバイト数の語であるため。名前長は `forbidden_name`）。
7. **列挙の枝刈りは 4 条件**: `archive_dir` と完全一致／除外接頭辞で始まる／symlink である／
   **`prune_rels`（宣言された他の領域の根）と同一実体**。4 つ目を落とすと、既定構成（`outcome_dir` が
   `pj_input_dir` の内側＝ D06-a）で**同じファイルが 2 件返り**、役割フォルダが PJ 入力の内側にある構成では
   **指定されていない役割の手引きが渡る**（FR-037a・R-24）。**列挙後の重複除去では後者が塞がらない。**
   照合は実体の同一性で行う（名前や綴りで比べない）。
8. **コード定数 3 つと設定値 9 つの境目**（憲章 §7-4・C-2）。

   | 区分 | もの |
   |---|---|
   | コード定数 | 退避スタンプ形式 `%Y%m%dT%H%M%SZ` と連番上限 `999`／一時ファイル接頭辞 `.vaulttmp-`／制御文字の範囲 `< 0x20` と `0x7F` |
   | 設定値（`config.json`・CFG-27〜35） | `forbidden_chars`・`forbidden_obsidian`・`reserved_names`・`forbid_trailing_dot_space`・`max_filename_bytes`・`max_outcome_bytes`・`archive_dir`・`list_exclude_prefixes`・`encoding` |

   コード定数にしたのは、いずれも「環境で変わる値」でも「運用で調整する値」でもなく、かつ設定キーを増やすと
   設定台帳 `docs/design/appendix_env_design/x3_config_catalog.csv` に CFG 行の新設が要るためである。
   変更が要ると判明したら CFG 行の新設をユーザに諮る。
9. **退避の名前確保には TOCTOU の窓が残る。** `exists()` と `os.replace` の間である。単一プロセス・単一利用者の
   前提で受け入れる（R-07・憲章 §10。ロック・排他を作らない）。
10. **スタンプの `:` を除くのは FS の制約ではない。** drvfs でも `:` を含む名前は作れてしまう（M-18）。
    Windows 側のアプリ（Obsidian・同期ソフト）が読めるようにするためである（OB-13）。
11. **予約語の語幹は「最初のドットより前」で採る。** `name.split(".", 1)[0]` であって
    `name.rsplit(".", 1)[0]`（＝ `Path(name).stem`）**ではない**。Windows はデバイス名を最初の区切りより前の語で
    解決するため、`rsplit` にすると `CON.tar.gz`・`NUL.d.ts`・`LPT1.a.b` が**ゲートを黙って素通りする**
    （converge 2026-08-24 実測）。`Path.stem` に「整理」しないこと（contracts/name-gate.md §1 #4・§2）。
12. **領域根の実在確認（`_missing_area_root`）を `_require_inside` から外さない・後ろへ動かさない。**
    領域根が実在しないと識別子が採れず `area_ok` は**必ず**偽になるため、この確認が `outside_area` を
    上げる前に無いと、**不在が「領域の外」に化ける**。呼び手には「自分がパス規則を破った」という偽の
    理由が届く（VD-19・VD-25・VD-34・VD-35）。**人間が `outcome/` や役割フォルダを作るまでは正常状態**
    であり（FR-014・SC-004。vault はフォルダを作らない）、この経路は例外ではなく通常運用で踏む。
    付随して 2 つ:

    - **3 分岐の順序を動かさない。** (1) `root_ok` が偽の根は読み飛ばす →(2) 実在しない根があれば
      それを名指して `VaultNotFound` →(3) どれも実在すれば `outside_area`。(1) を省くと、**ルート外に
      実在するフォルダを「実在しない」と報告する**（converge 2026-08-24 実測の退行）。(2) を「1 つでも
      在れば領域は在る」（和集合）にすると、`role_dirs` が複数で対象の役割だけ不在のときに取りこぼす。
    - **領域まわりのテストを `role_dirs` 1 件で書かない。** 和集合の経路を踏まず、**緑のまま見逃す**
      （実際に見逃した）。既定の構成は複数役割である（`tests/conftest.py` の `folders` は 2 件・
      `PersonaKey` は 9 種）。
    - **`log_not_found=False` を `outcome_exists` 以外へ広げない。** 呼び手に届く送出まで無記録になり
      FR-059 が破れる（注意 1 の ⚠ を参照＝ VD-41）。この口が止めるのは**不在**の 1 行だけで、外縁・領域の
      **違反**のログは止まらない。
13. **quickstart S3 の構造ガードを地の文で汚さない。** S3 の受け入れ検査は「フォルダ生成はコード全体で
    1 箇所だけ」（FR-014・SC-004）などを **grep の行数**で機械的に守る。`src/vault/` 配下のコメントや
    docstring に検査対象の語を書くと行数が増え、**本物の 2 つ目の呼び出しと地の文が区別できなくなる**
    （次に検査を回す人が毎回「増えたのはコメントだから良い」と判断する必要が出る＝ VD-43）。退避先を
    作れない旨は「退避先フォルダを作れない」と書く。同型の罠が在る検査は S3 の 7 本すべてである
    （フォルダ生成の呼び出し／親ごと作る指定／FS への直接到達／`outcome` 外へ書く定義／包含の文字列比較／
    子プロセス起動／`LogRecord` の予約属性）。**本 `SKILL.md` は grep の対象外**（`src/vault/` の外）
    なので、語を説明したいときはここに書く。
14. **段 5（原子的書込）が失敗したとき、古い版は `outcome/` に戻らない。** FR-045 が段の順を
    (4) 退避 →(5) 原子的書込 と固定しているため、段 5 に達した時点で古い版は既に `_archive/` へ
    **移動済み**である（FR-047「移動であって複製ではない」・SC-005「元の場所に古い版が残った事象が 0 件」）。
    **裁定 2026-08-24（ユーザ決定）＝「古い版を残さない形でよい」** ―― **退避先から戻すロールバックを
    作らない**（憲章 §10「自前の復旧機構を作らない」）。FR-049・US2/AC8 の「古い版があればそれを残す」は
    **「失われない（退避先に在る）」の意味**である（VD-49）。⚠ **観測される帰結**: 完了ゲート
    `outcome_exists` が **`True` → `False` に転じる**。人間は `_archive/` から中身を取り戻せ、呼び手は
    `FsIoError` を受けるので「書けた」とは誤解しない。**退避に失敗した場合（段 4）とは別物**で、そちらは
    古い版が元の場所に残る（FR-048）。両側を `test_write_outcome.py` の
    `test_the_old_version_survives_in_the_archive_when_stage_5_fails` と
    `test_archive_failure_keeps_the_old_version_and_writes_nothing` が対で固定している。

---

## 実運用の vault に触れない規約（FR-019・SC-015）

テストは `tmp_path` だけを使う。`tests/conftest.py` の autouse fixture `_isolate_real_vault` が
`vault.env_file_path` を空の `.env` に差し替え、`VAULT_ROOT` を環境から落とす。**この fixture を消さない・
迂回しない。**

⚠ **止め木は 2 点セットで初めて効く**:

1. `VaultEnv` の**明示 `__init__(self, **overrides: Any)`**（pydantic が合成する `__init__` はフィールドしか
   受け付けず、`BaseSettings` の `_env_file` を渡せない）
2. `build()` の **`VaultEnv(_env_file=env_file_path())`**（`model_config` の `env_file=env_file_path()` は
   **クラス定義時に 1 回しか評価されない**）

どちらかを消すと `monkeypatch.setattr("vault.env_file_path", …)` は**黙って無効**になり、テストは**緑のまま
実運用の Obsidian vault を掴む**。**`build()` を `VaultEnv()` の形に「整理」しないこと。**

Windows 側のドライブ（drvfs）配下に一時物を作るテストも置かない（quickstart S8 が grep で検査する）。
実運用の領域に固有の事実は spec の実測記録 M-01〜M-24 として残してあり、自動テストの対象にしない。
**新しい pytest の印（marker）を作らない**（FR-071）— vault は外部ツールを起動しないので、
`uv run pytest packages/features/vault/tests` は**どの環境でも 100% 走る**（skip 0 件）。

---

## 検証コマンド

```bash
uv run pytest packages/features/vault/tests
uv run ruff check packages/features/vault
uv run pyright packages/features/vault
```

構造の機械的検査は `specs/004-vault/quickstart.md` S3（`service.py` に FS 呼び出しが無い／`mkdir` が 1 箇所／
`parents=True` が 0 行／`def write` が 1／`repository.py` に文字列比較が無い／`subprocess` 系が 0 行／
`extra=` に `LogRecord` 予約属性が無い）。

## 肥大したときの分割方針（憲章 §3・R-23）

**閾値は実コード行数で見る**（docstring・空行・コメント専用行を除く）。`service.py`・`repository.py` の
いずれかが**実コード 400 行**を超えたら同名サブパッケージへの分割を再検討する（VD-36）。総行数で
見ると docstring の厚さが要否を歪めるため採らない。plan (x)・R-23 は「500 行」と書くが、それは
総行数の目安であって本ファイルの規約は上の実コード基準が正である（VD-28 → VD-36 で現行化）。

| ファイル | 総行数 | 実コード | 閾値 400 に対して |
|---|---|---|---|
| `service.py` | 701 | **371** | **93%**（残り **29 行**） |
| `repository.py` | 446 | 248 | 62% |

**2026-08-24 時点の判断: どちらも分けない**（VD-47 で再判断した結果）。根拠は VD-36 の 3 つがそのまま
成立するためである ―― (1) 憲章 §3 の文言は「分けて**よい**」であって義務ではない。(2) 実コードは
閾値の内側にある。(3) quickstart S3 の受け入れ検査が `…/src/vault/service.py` を**パスで名指し**して
おり（7 本中 3 本）、分けると字面が成立しなくなる。

⚠ **`service.py` は残り 29 行しかない。次に実コードを増やす改修では、着手前にこの節の判断を
やり直し、上の表を実測で更新すること。** 書いただけでは踏まれないので、実コードを触ったら必ず
ここへ戻る（T085 と T089・T092 の 2 度にわたって実コードが増えたが、後者では手続きが踏まれず
上の表が 1 回分 stale になった＝ VD-47。**この地の文に旧い実測値を書き残さない** ―― T093 の
受け入れ検査は旧値の grep で行うので、経緯として書くと構造ガードが鈍る＝ VD-43 と同型）。
実測の採り方は「総行数 − docstring 行 − コメント専用行 −
空行」で、**行末コメントは実コードとして数える**（引き算を二重にしない）。

**モジュール全体の規模（憲章 §1）**: 19 ファイル・**3,930 行**（`src/` 1,611・`tests/` 2,019・その他 300）で、
トークン概算は **約 54,000〜63,000**。憲章 §1 の実用目標「20,000〜40,000 トークン程度」は**超えている**が、
同条が分割か common への抽出を**検討する**と定める引き金は「**大きく超えた(100,000 トークン程度)場合**」で、
現在はその **5〜6 割**である（VD-48）。伸びた内訳は主に `tests/`（plan (x) の見込み 900 → 2,019）と docstring で、
converge 7 回分の退行検査と裁定の記録が積み上がった結果である。**抽出すべき共通部品は現時点で見当たらない**
（`common` へ出せるのは他モジュールも使う部品だけで、vault のテストは vault の規則そのものを固定している）。
**分けるときは quickstart S3 の grep 対象の書き換えが対で要る**（受け入れ検査が
`…/src/vault/service.py` をパスで名指ししているため。例: `grep -c "def write" …/service.py` が 1）。
`service/` は `__init__.py`（6 メソッド）・`gates.py`（名前・内容ゲート）・`errors.py`（翻訳）、
`repository/` は `__init__.py`（`FsAdapter`）・`resolve.py`・`walk.py`・`write.py`。
モジュール境界と `__init__.py` のエクスポート（`build`・`VaultDeps`・`VaultHandle`・`VaultConfig`・`VaultEnv`）は
**不変**。**`VaultService` を 2 クラスに割らない**（FR-005。分けると解決とゲートの規則が 2 つに割れ、
「読めるのに書けない」非対称が生まれる）。
