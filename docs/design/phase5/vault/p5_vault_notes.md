# vault（BC-04 成果物）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_vault_sequence.mmd`／`p5_vault_data_model.mmd`／`p5_vault_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-008〜012。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-31〜37（提供 7 行）／p4 notes §1／`../../phase2/p2_domain_model.mmd` DM-17（＋DM-02 は settings 所有）／`p2_sequence_UC-I1.mmd` execute 段／D04-d・D06-a・D07-c／`docs/initial_memo/付録：windows側のobsidian連携.md` OB-4〜OB-13／PoC `crewai_casbin_control`・`crewai_beads_renkei`（PoC 基準版、読取のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

vault は **Obsidian vault 領域の唯一のアダプタ**で、`VaultReader`（5 メソッド）と `OutcomeWriter`（1 メソッド）を `VaultService` で実装する。P1 のゲート（外縁・領域・禁止文字・不在検出・サイズ・退避）を**構造で**持ち、判断（何を読ませるか・成果物が良いか）は持たない。書込先は PJ フォルダ/`outcome/` 直下フラットのみ、上書き時は `outcome/_archive/` に退避（P5D-008）。パスは vault ルート相対で、ルート `VAULT_ROOT` は .env（P5D-009）。他 Protocol に依存せず router・ui を持たない。

## 1. ファイル構成（憲章 §3）

```
packages/features/vault/
├── pyproject.toml
├── config.json          # VaultConfig: 禁止文字集合（既定 D07-c）・予約語・max_filename_bytes・max_outcome_bytes・archive_dir・list_exclude_prefixes（既定 [.]。退避先名は archive_dir から導出を受けるので二重に持たない）・encoding（None＝ルート defaults.encoding を継承）
├── SKILL.md             # 「outcome/ の外に書く経路を作らない」「resolve を迂回しない」「役割の分離は呼び手」「包含に文字列比較を使わない」の規約と、照合済み Obsidian 版（1.12.7）の固定
├── src/vault/
│   ├── __init__.py      # build(deps: VaultDeps, *, config: VaultConfig | None = None) -> VaultHandle（vault_reader／outcome_writer／router=None）
│   ├── service.py       # VaultService(VaultReader, OutcomeWriter): ゲート鎖（名前・内容・外縁・退避）と例外翻訳
│   ├── repository.py    # FsAdapter: VAULT_ROOT 結合・symlink 解決・領域検査・再帰列挙・原子的書込・退避
│   ├── models.py        # ResolvedPath／NameGateResult／NameViolation／FileStat（内部 VO）
│   └── exceptions.py    # 内部例外（FsError）。契約例外 VaultNotFound／VaultPathViolation は common/interfaces/vault.py
└── tests/               # 単体: 一時 dir をルートにして実 FS で（スタブ不要）／統合: 憲章 §8 の一時 vault
```

置かないもの: `router.py`・`ui.py`・`schemas.py`・`dependencies.py`。
`build(deps: VaultDeps, *, config: VaultConfig | None = None) -> VaultHandle`: Deps 空。`.env` の `VAULT_ROOT`（必須。欠ければ起動中断 憲章 §5）と config.json を読む。任意引数 `config: VaultConfig` で設定を差し替えられる（解決順序 ⑧。統合テストクラスタが上限・禁止集合を差し替えるため）。

## 2. Protocol 実装の対応

| Protocol.メソッド | if_id | fn | 要点 |
|---|---|---|---|
| VaultReader.check_project_folders | IF-31/32 | vault-F02 | 3 種のフォルダを解決し missing を返す。作らない |
| VaultReader.list_inputs | IF-33 | vault-F03 | pj_input＋role_dirs[role]＋outcome（B2）。再帰・ファイルのみ・`_archive/`・ドット始まり・symlink・**宣言された他の領域の根**（枝刈り）を除外。既定構成では outcome が pj_input の内側にあるため、枝刈りが無いと同じファイルが 2 件返る |
| VaultReader.read_input | IF-34 | vault-F04 | FileRef を再解決して UTF-8 で読む。`役割` の領域根は**宣言された役割フォルダの和集合**（契約が role を取らないため）。役割どうしの分離は呼び手が folders を絞って渡すことで行う |
| VaultReader.read_outcome_file | IF-35 | vault-F05 | ゲート→解決→読む。不在は VaultNotFound（FN-13） |
| VaultReader.outcome_exists | IF-36 | vault-F06 | is_file。`_archive/` は数えない |
| OutcomeWriter.write_outcome | IF-37 | vault-F08（＋F07 ゲート・F09 退避・F01 外縁） | 退避→原子的書込→WrittenPath |

vault が持たないもの: 何を読ませるか（Tools の選択は executor、権限は settings の Authorizer）、成果物の内容評価（🔒 機構合格であって内容合格ではない）、完了ゲートの合否判断（orchestrator が outcome_exists の結果で決める）、上書きの是非（呼び手が WrittenPath.overwritten を観測印にする）。

## 3. 受け渡し型（common/interfaces/vault.py。P5D-012 で追記）

`VaultArea`（pj_input／role／outcome）／`FolderCheck`（missing・ok）／`FileRef`（area・rel_path・name・size_bytes・mtime）／`WrittenPath`（rel_path・bytes・overwritten・archived_to）／例外 `VaultNotFound(NotFoundError)`・`VaultPathViolation(ViolationError, kind)`。`ProjectFolders` は settings の型（vault ルート相対パス。P5D-009）。

## 4. シーケンス図

`p5_vault_sequence.mmd`（主図のみ。1 kickoff で受ける全 6 メソッド: check → list_inputs → read_input → write_outcome（ゲート鎖・退避・原子的書込）→ read_outcome_file → outcome_exists）。レーン: `VaultReader`／`OutcomeWriter`（呼び手）／`vault/service.py`／`vault/repository.py`／`Obsidian vault`。

## 5. PoC からの継承と変更（証跡: `safefs.py`・`tools.py`）

| 項目 | PoC | V4 |
|---|---|---|
| 外縁 | renkei `resolve_under`（tools.py）／casbin `SafeFS.resolve`（実パス一元化） | `FsAdapter.resolve`（ルート内＋領域内。毎回再解決） |
| outcome の置き場 | `outcome/<親issueID>/`（`_outcome_base`） | PJ フォルダ/`outcome/` 直下フラット（D06-a） |
| 兄弟成果物 B2 | `extra_outcome_ids` で兄弟の outcome/ を追加基点に | outcome/ 全体が見える（フラット化の帰結。何を読むかは executor／プロンプト） |
| 上書き | 上書き（renkei）／`_archive/` 退避（casbin） | `_archive/` 退避（P5D-008） |
| 原子的書込 | casbin: 一時ファイル＋`os.replace` | 同 |
| checksums／archive.jsonl／監査フック（`sys.addaudithook`） | casbin にあり | 持たない（構造化ログ。迂回遮断は linter＋SKILL.md＋レビュー） |
| 禁止文字 | なし（プロンプト対応の構想 OB-12） | ゲート（FN-12・P5D-010） |
| サイズ上限 | `MAX_OUTCOME_BYTES` 定数 | config.json |
| 不在検出 | `read_outcome_file` が ok:true（穴⑫） | VaultNotFound（FN-13） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py vault --render` → ERRORS: none
- participant は `VaultReader`／`OutcomeWriter`／`vault/service.py`／`vault/repository.py`／`Obsidian vault` のみ
- 提供 7 行の if_id と 6 メソッド全てに実装 fn

## 7. 未決（vault 固有）

1. **Obsidian がタイトルに許さない文字の正確な集合**（D07 §5-1）: **Obsidian 1.12.7 と照合済み・既定値の変更は不要**（P5D-010 はこの結果で閉じる）。`forbidden_chars` はアプリのハードブロック集合（Windows 分岐 `*"\/<>:|?`）と、`forbidden_obsidian` の 5 種はリンク破壊警告集合 `HT="#^[]|"` と完全一致。設計に無い 2 件を**採用**した: `%%`（コメント記法）と**ドット始まり**（Obsidian 自身が `msgBadDotfile` で拒否）。ドット始まりは新しい設定キーを作らず `list_exclude_prefixes` から導出する
2. **`_program`（統括）の DM-02 要否**（D06 §6-1）: vault の設計は不変（ProjectFolders が渡れば検証する）。settings の回で ProjectFolders を任意にするか決める
3. **`/mnt/c` の大文字小文字非区別**（実測済み・決着）: **包含は実体の同一性（inode。`(st_dev, st_ino)` を親方向へ 1 段ずつ照合）で判定する**。実パス同士の文字列比較は使えない — 解決関数は入力のケーシングをそのまま返して実体へ正規化せず、`os.path.normcase` は POSIX で恒等、`str.casefold()` は `'ß'` と `'ss'` を同一視する。inode 同一性は drvfs でも信頼できる。**symlink は drvfs でも ext4 でも挙動が同一**（作成でき、`realpath` はリンク先まで解決する）ため、drvfs 専用の検証は要らない
4. **read_input の対象拡張子**: 現設計は UTF-8 テキストのみ（.md ほか）。PDF・画像は VaultPathViolation(not_text)。バイナリ入力が要る要求は本 ver に無い（R-11 は Obsidian 連携の拡張として次 ver）
