# SKILL: settings — 設定ファイル領域 A の唯一の所有者

## 責務

- **設定ファイル領域 A（`<SETTINGS_ROOT>` 配下）の唯一の所有者**（憲章 §2 例外 (a)・EIF-05）。
  他モジュールは `common/interfaces/settings.py` の 4 Protocol 経由でのみ領域 A の内容に触れる。
- **4 Protocol・計 9 メソッド**を **1 つの `SettingsService`** で実装する（FR-005）。
  `SettingsReader` 5（`get_project`・`get_limits`・`get_rules`・`get_hooks`・`get_tasks`）／
  `MemberResolver` 2（`resolve`・`is_human`）／`Authorizer` 1（`enforce`）／
  `CommandRegistry` 1（`resolve_command`）。**4 つの契約実装は同一インスタンス**である。
- 本モジュールは**読取・検査・保存・HTTP の口・画面**をすべて持つ（垂直スライスが閉じている
  ＝憲章 §2）。CRUD は **F11〜F17**、口は **F21**（18 パス・39 操作。D26 で役割の読取 1 本を追加）、画面は **F22**（2 ページ）、
  保存経路は **F02**（候補を組む → 検査 → 書く → 差し替える の 4 段）である。
- 利用する契約は **2 つだけ** —— `BoardReader.has_open_issues`（IF-09。F11-c の削除ガード）と
  `VaultReader.check_project_folders`（IF-32。F11-a・F11-b の実在確認）。**`BoardWriter` を
  要求しない**ので、盤面へ書く手段が構造的に無い（D07-g「エラーは UI に返し盤面には書かない」）。
- settings は**判断を持たない**（FR-007）。フォルダの実体を作るのは人間、kickoff 時の存在確認は
  orchestrator（vault の契約を呼ぶ）、段の許可ペルソナは stages、Tool 名から認可の対象語彙への
  写像は executor、秘密の実値の読取は executor の仕事である。
  - ⚠ 例外 1 件（D26）: 役割ごとの実効設定の**表示**（`service/roles.py`）は、装備と許可を並べる
    ために `tool:<名前>`・`execute` の約束を executor と**二重に持つ**。判定は判定器に任せており、
    settings が実行可否を決めるわけではない。約束を変えるときは両方を直す。

## 主要ファイル

| ファイル | 役割 |
|---|---|
| `src/settings/__init__.py` | 公開口。`SettingsEnv`・`SettingsConfig`・`SettingsDeps`・`SettingsHandle`・`build()`・`config_path()`・`env_file_path()`・`defaults_path()` |
| `src/settings/models.py` | 領域 A の写像モデル 9・語彙の定数 13・`SettingsSnapshot`・`Registry`・`ValidationProblem`・`PolicyEnforcer`・`AreaAData` ＋ 保存経路の内部 VO 4（`EffectClass`・`FileWrite`・`ChangeSet`・`SaveOutcome`） |
| `src/settings/repository.py` | **外部ツールアダプタ**。`AreaAReader`（生 I/O）・`AreaAScaffolder`（雛形生成）・**`AreaAWriter`（実行時の書込。唯一の入口）**・直列化 3 種・重複キーを送出する `SafeLoader`・`new_enforcer()`。**casbin と PyYAML の import はここだけ** |
| `src/settings/exceptions.py` | `SettingsValidationError` ＋ `SettingsStartupError` 階層 4 種 ＋ `SettingsItemNotFound`（404）・`ProjectDeletionBlocked`（409）。いずれも `Exception` 直系（**契約例外の系統に混ぜない**＝ HTTP 境界の信号） |
| `src/settings/schemas.py` | **HTTP 境界の DTO 28 種**（D26 で役割ごとの実効設定の 6 種を追加）。正本は `docs/design/phase6/openapi.yaml` の components。入力型には `extra="forbid"` を必ず付ける |
| `src/settings/dependencies.py` | `service_provider`。**差し替え点はこの 1 箇所**（テストは `app.dependency_overrides`） |
| `src/settings/router/` | **18 パス・39 操作**（`__init__.py`＝ `create_settings_router` ＋保存結果ヘッダの写像／`_translate.py`＝例外→応答／`projects.py` 6（D26 の `listProjectRoles` を含む）・`agents.py` 11・`ai_config.py` 14・`policies.py` 8）。**6 ファイルとも `from __future__ import annotations` を置かない** |
| `src/settings/ui.py` | NiceGUI 2 ページ（AI 設定・プロジェクト）。**`settings.*` を 1 つも import しない**（HTTP のみ）。`016` で Streamlit から置き換えた。D26 で AI 設定に**段テンプレートの段ごとの編集欄**（取り直し → 1 段差し替え → `PUT`、null の項目は送らない）、プロジェクトに**役割ごとの設定（閲覧のみ）**を足した |
| `src/settings/service/crud.py` | `SettingsCrudService`。39 操作の入口・**生の構造（`AreaAData`）の保持**・2 依存の保持 |
| `src/settings/service/save.py` | **F02 の 4 段**（検査 → 2 段書込 → 台帳再導出 → 差し替え） |
| `src/settings/service/{projects,members,ai_config}.py` | 候補づくりの**純関数**（`(current, …) -> (candidate, ChangeSet)`）。互いを import しない |
| `src/settings/service/__init__.py` | `SettingsService`（9 メソッド ＋ `replace_snapshot`）。**ファイルシステムに触れない** |
| `src/settings/service/resolve.py` | **解決規則の純粋関数**。build と実行時の唯一の出所 |
| `src/settings/service/roles.py` | 役割ごとの実効設定（D26・閲覧のみ）。`resolve`・`get_tasks`・`enforce` を役割単位に並べるだけで、規則を足さない |
| `src/settings/service/loader/` | 生の読取結果 → `SettingsSnapshot`。`read.py`（(d2) 読取）・`enforcer.py`（判定器と 3 担保）・`assemble.py`（(d3) 写像・台帳導出・世代の採番） |
| `src/settings/service/validate/` | 整合検査 3 種（V-01〜V-28）。`problems.py`（違反と秘密語）・`vars.py`（`{変数}` 抽出）・`agents.py`・`tasks.py`・`forward.py`・`reverse.py`・`commands.py` |
| `defaults/` | 出荷時既定（`agents.yaml`・`tasks.yaml`・`model.conf`）。git 管理下・テスト対象 |
| `config.json` | モジュール設定（CFG-36〜40） |

## 領域 A のレイアウト

```
<SETTINGS_ROOT>/
├── global/
│   ├── agents.yaml        # ペルソナ既定（キー＝PersonaKey）        CFG-69 必須
│   ├── tasks.yaml         # 段テンプレート 7 キー                    CFG-71 必須
│   ├── model.conf         # casbin モデル（全 PJ 共通）              CFG-76 必須
│   ├── llm_profiles.yaml  # LLM プロファイル＋default                CFG-77 必須
│   ├── limits.yaml        # 上限                                      CFG-78 任意
│   ├── commands.yaml      # run_command の固定 argv                   CFG-79 任意
│   └── humans.yaml        # 人間 actor                                CFG-81 必須
└── projects/<key>/
    ├── project.yaml       # PJ 定義                                   CFG-73 PJ ごと必須
    ├── agents.yaml        # PJ メンバ（キーの存在＝メンバ）          CFG-70 PJ ごと必須
    ├── tasks.yaml         # 段テンプレートの PJ 上書き                CFG-72 任意
    ├── policy.csv         # 権限                                      CFG-75 PJ ごと必須
    ├── rules.md           # PJ 共通ルール                             CFG-82 任意
    └── hooks.yaml         # hooks                                     CFG-80 任意
```

- **読まない・作らない**（D16 で廃止。FR-013）: `projects/<key>/members.yaml`・
  `global/roles/<persona>.md`。
- `_archive/` は前半では**作らず読まない**（プロジェクト削除時の退避は F11＝後半）。
- `projects/` 直下の**ディレクトリだけ**を PJ とみなす。ドット始まりは無視・**再帰しない**。
- **文字コードは常に `utf-8`**。BOM・CRLF は透過（M-17・M-18）。生成物の改行は `\n` に揃える。

**不在時の扱い**（R-10）: 共通側 6 件は雛形を生成する／`global/limits.yaml` だけは**生成しない**
（生成すると CFG-39 の ③ 段が永久に効かなくなる）／PJ ごとの `project.yaml`・`agents.yaml`・
`policy.csv` の不在は**起動中断**／`tasks.yaml`・`rules.md`・`hooks.yaml` の不在は任意扱い。

### 各ファイルのトップレベルの形

```yaml
# global/agents.yaml ／ projects/<key>/agents.yaml … キー＝PersonaKey、値＝許可 10 フィールド
programmer:
  role: |-
    プログラマ
  goal: |
    …
  backstory: |
    …
  tools: [read_issue, write_outcome]     # skills・mcps・llm・max_iter・max_rpm・max_execution_time も可

# global/tasks.yaml ／ projects/<key>/tasks.yaml … キー＝7 段、値＝許可 4 フィールド
child:
  description: |
    … {acceptance} …
  expected_output: |-
    …
  agent: programmer                       # 任意（段の既定ペルソナ）
  tools: [submit_review]                  # 任意（段側が Agent 側を上書き）

# global/llm_profiles.yaml
profiles:
  default:
    provider_kind: openai_compatible      # または openrouter
    base_url: http://127.0.0.1:8080/v1
    model_id: demo-model
    context_window: 32768
    api_key_ref: DEMO_API_KEY_REF         # **参照名**。実値は .env にあり executor が読む
    extra_headers: {}                     # 任意
default: default                          # null 可（メンバが 0 件なら違反にならない）

# global/limits.yaml（任意。不在ならモジュール設定の出荷時既定）
global:
  max_requests_per_kickoff: 60
  max_cost_per_kickoff: 1.0
overrides:
  <project_key>:
    max_requests_per_kickoff: 3
    max_cost_per_kickoff: 0.5

# global/commands.yaml
commands:
  pytest:
    argv: [uv, run, pytest]               # 空でない文字列の一覧（V-26）

# global/humans.yaml
human_actors: []                          # 空でよい（人間 0 名）

# projects/<key>/project.yaml … project_key は**書かない**（ディレクトリ名が正本）
name: デモ
is_reserved: false
folders:
  pj_input_dir: pj
  role_dirs:
    programmer: pj/roles/programmer       # キーは PersonaKey（V-28）
  outcome_dir: pj/outcome

# projects/<key>/hooks.yaml（任意）
hooks:
  - name: guard
    points: PRE_TOOL_CALL                 # または POST_TOOL_CALL
    agents: [programmer]                  # 任意
```

**`projects/<key>/policy.csv`** は casbin の CSV（`p, <sub>, <obj>, <act>` と `g, <sub>, <role>`）。
**`projects/<key>/rules.md`** は Markdown の本文をそのまま（不在なら空文字）。

## 規約 1: casbin を import してよいのは settings だけ

- `.importlinter` の `casbin-scope` が機械的に守る。**`source_modules` に settings を入れない**
  （この契約は「禁止される側」の列挙であり、入れると settings 自身の実装が違反になる＝ FR-066）。
- 同契約は `allow_indirect_imports = True` を持つ。composition root は全 feature を import する
  のが役目（FR-068）であり、`api.registry -> settings -> casbin` の間接連鎖は実装側で解消
  できないためである。**効きは失われない** — 他パッケージに `import casbin` を 1 行足せば赤くなる。
- **`Enforcer` を `SettingsSnapshot` の外へ出さない**。スナップショットの型面に現れるのは
  `models.PolicyEnforcer`（settings 内の構造的部分型）だけで、casbin の実体に触れるのは
  `repository.new_enforcer()` の 1 箇所である。`Authorizer.enforce` は `bool` しか返さない。
- **PyYAML も同じ扱い**にしてある（`repository.load_yaml_mapping()` の 1 箇所）。

## 規約 2: 台帳は導出して保存しない

- `member_id` = `<project_key>.<persona_key>`（D06-b）
- `bd_actor` = **メンバ ID そのまま**（D21-a が実測で確定。`.` を含む actor 名は盤面が扱える）
- `casbin_subject` = `member:` ＋ `member_id`
- 人間の行為者 = `global/humans.yaml`（P5D-019）／システムの行為者 = `config.json` の
  `system_actor`（CFG-40・P5D-037）

**どの設定ファイルにも書かない**（P4 の二重宣言排除）。台帳（`Registry`）は build 時に導出し、
**ファイルに保存しない**（FR-037）。導出規則の出所は `service/resolve.py` の 1 箇所である。

## 規約 3: 実行時に領域 A のファイルを読み直さない

- **読取は build 時だけ**（P5D-016・FR-014）。9 メソッドはすべてスナップショット参照で応答する。
- **保存の候補も読み直さない**（A-11・FR-025）。素材は `SettingsCrudService` が保持している
  生の構造（`AreaAData`）であり、そこへ変更案を重ねる。読み直す形だと、復旧時の直接編集で
  壊れた領域 A があると**無関係な保存まで全部拒否される**。
- `service/__init__.py` に `open`／`Path(...)`／`read_text` を **1 つも書かない**。読み書きの
  到達点は `repository.py` だけである（quickstart S3 (a) が grep で機械検査する）。
- **スナップショットはキャッシュではない**（C-2）。領域 A は §2 例外 (a) が定める
  **システム所有のローカルファイル**であって、Obsidian vault や beads のような外部ツールの
  ストアではない。人間の直接編集は「**次回起動まで反映されない**」と運用規約（`x3_notes.md` §1）
  が定めており、P5D-016 はそれを構造にしたものである。**vault の「毎回再解決してキャッシュ
  しない」を settings に持ち込まない。**

## 規約 4: `api_key` の実値を持たない

- `LLMProfile` は `api_key_ref`（**参照名**）だけを運ぶ（FR-049）。
- 整合検査（V-06）が領域 A と同梱の雛形から秘密語 4 キー（`api_key`・`token`・`secret`・
  `password`）を拒否する。判定は**キー名の完全一致**である — 部分一致にすると `api_key_ref`
  （FR-049 が定める参照名）と `context_reserve_tokens`（CFG-37）が違反になり、規則そのものが
  成立しない。
- ⚠ **走査は「ファイルの生トップレベル」から始める**（FR-050）。`llm_profiles.yaml` と
  `commands.yaml` だけは規則が使う値が部分木（`profiles:` / `commands:` の中身）なので、
  `AreaAData` はその部分木と**生トップレベル**（`profiles_file_raw`・`commands_file_raw`）の
  対を運ぶ。部分木だけを走査すると、`api_key` の実値が最も置かれやすい `llm_profiles.yaml` の
  **ファイル直下**が素通りする。
- 例外・ログに実値を載せない（FR-051）。`config.json` と `defaults/` の担保は
  `tests/unit/test_settings_defaults.py` が**生テキストを走査**して行う（SD-32。`SettingsConfig`
  は `extra="forbid"` なので入れ子の秘密キーは構築時に捨てられ、validate では到達できない）。

## `agents.yaml`／`tasks.yaml` の書式規約（Y-1〜Y-9。実測 M-13〜M-19）

| # | 規約 |
|---|---|
| Y-1 | 読み込みは `safe_load` 系のみ（`!!python/object:` は拒否される） |
| Y-2 | **トップレベルは mapping**（リスト・文字列は構文誤り） |
| Y-3 | **空ファイルは `None`** → `{}` に正規化する |
| Y-4 | **キーの値なし（`programmer:`）は `None`** → `{}` に正規化する |
| Y-5 | **同一キーの重複は構文誤り**（行・列つきで報告し、起動を止める） |
| Y-6 | **本文（`description`・`expected_output`・`backstory`・`goal`）は必ず `\|` で始める** |
| Y-7 | 波括弧そのものを書きたいときは `{{` `}}` |
| Y-8 | 日本語・CRLF・BOM は透過。特別な対処をしない |
| Y-9 | anchor／alias／merge key `<<` は通る |

**Y-6 が最重要**である。素の `description: {title}` は**失敗せずに**
`{'description': {'title': None}}` へ化ける（M-15 (b)）。値の型検査（**V-10b**）を通さないと
validate も素通りする。必須フィールドの**欠落**は別規則（**V-10a** ＝ `var_required`）である。

**`{変数}` の抽出は `string.Formatter().parse()` に一本化する**（R-19）。正規表現は
`{issue.id}`・`{issue[0]}`・`{0}`・`{}`・`{題名}` を**すべて取りこぼす**。書式指定（`{title:>10}`）
と変換（`{acceptance!r}`）も拒否する。位置引数 `{}` は空の名前として抽出されるので**捨てない**。

## `policy.csv` の規約

- **書出しも引用符を付けない**（FR-029）。`csv` モジュールを使わず `", ".join(fields)` で
  1 行ずつ組む —— 引用符を付けると casbin が剥がさないため **V-21 が孤児として拒否する**。
  値にカンマを混ぜた行は**候補の判定器で列数違反として捕まる**（担保 2）。

- **引用符を書かない**。casbin は引用符を剥がさないため（M-09）、`p, "member:x", …` の主体は
  `'"member:x"'` という文字どおりの値になり、台帳のどれにも一致せず**孤児として落ちる**。
- `p` 行＝3 列・`g` 行＝2 列。列数が合わない行は構築時には通り、**判定の時点で**
  `RuntimeError: invalid policy size` になる（M-09）。build 時に列数を検査して塞ぐ。
- `#` 始まりの行と空行は casbin が読み飛ばす。空白は除去される。

### `keyMatch` の落とし穴 3 件（M-02・M-03）

1. `vault:role:*` は **`vault:role` 自身に一致しない**
2. `vault:role*`（コロンなし）は **`vault:roles_other` にも一致する**
3. obj が `*` だけの行は**全対象を許可する**

**ワイルドカードを含まない obj は素通りしない**（完全一致でのみ許可される）。

### `model.conf` の 5 節（PoC から変更なしで継承。FR-048a）

**`g = _, _` を落としてはならない**。g 行を含む policy を g 無しの `model.conf` で読むと、
**g 行は黙って捨てられ `p, role_*, …` 行が到達不能になる**（`get_grouping_policy()` は
`TypeError` を送出する＝ M-06・M-07）。落とす利得はゼロで、失う側は**後半の F16 が CRUD する
行の沈黙の無効化**である。

**構築直後に必ず 3 つの担保を掛ける**（PoC に無かった。順序は 1 → 2 → 3）:

1. `enable_auto_save(False)`（M-12・FR-048d。`Enforcer` は可変で `auto_save` の既定が真）
2. **列数の検査**（M-09・FR-048c）
3. **空打ち `enforce()` を 1 回**（M-08・FR-048b）。**戻り値は判定に使わない** —
   obj が `*` だけの行があると健全な設定でも真が返りうるため（M-03 (3)）

## 整合検査 3 種の意味

| 入口 | fn | 見るもの |
|---|---|---|
| `validate_forward` | F18 | 許可フィールド・禁止キー・秘密語・参照（ペルソナ・Tool・LLM）・変数・段別必須変数・メンバ ⊆ role_dirs・メンバ ⊆ policy 主体・行為者名の衝突・上限と閾値の値域 |
| `validate_reverse` | F19 | policy の主体が台帳に居る（孤児ポリシー）・`g` 行の役割に `p` 行が在る・`vault:` 対象の形式 |
| `validate_commands` | F20 | `cmd:` 対象 ⊆ 定義・**死に定義の検出**・`argv` の形 |

**build 時に 3 種すべてを走らせ、違反があれば起動を中断する**（FR-047・clarify 裁定）。
**死に定義も同じ重さ**で中断させる — 違反に 2 種の重さを持ち込むと `ValidationProblem` に
`p5r1_notes.md` §5 の語彙に無い属性が要り、「起動は通ったが保存できない設定」という第 3 の状態も
残るためである。違反は**一覧**で返し、最初の 1 件で打ち切らない。

**`kind` の語彙を 13 語から増やさない**（FR-046）。受け皿が無い 6 件は意味の近い語に寄せてある
（V-08・V-10b・V-16・V-20・V-23・V-27。→ design-sync SD-25・SD-30）。

### 前半で `folder_missing` と `key_rule` が発火しない理由

- `folder_missing`: PJ フォルダの実体確認は `VaultReader.check_project_folders`（IF-32）を呼ぶ
  検査であり、**呼び出し経路が F11＝後半にしか無い**。前半の `SettingsDeps` は空である。
- `key_rule`: キー規則は **`project_key` を新しく作るとき**の規則で、F11／F12＝後半の保存経路に
  しか適用点が無い。既に領域 A に在るキーへ遡って適用すると、後半の CRUD を通っていない領域 A
  （復旧時の直接編集）で**起動できない状態**を作る。CFG-36 の値は `SettingsConfig` に**持つだけ**。

**前半に無いのは検査そのものではなく、検査を掛ける契機である。**

## 起動 6 段が守ること（(a)〜(d3)。FR-062・R-08）

**最初に失敗した段だけ**を報告し、原因が 1 つに定まる形にする。段ごとの不変条件:

| 段 | 例外 | 落としてはならない形 |
|---|---|---|
| (a) `.env` | `SettingsEnvError` | `.env` が UTF-8 で読めない・開けない場合も**同じ段に畳む**（生の `UnicodeDecodeError`・`OSError` を build の外へ出さない） |
| (b) `SETTINGS_ROOT` | `SettingsRootError` | **絶対パスの検査を最初に置く**。空値を pydantic は `Path(".")` として受理し、cwd に解決されて `exists()`・`is_dir()` が真になる → (d1) が**カレントディレクトリに雛形 6 件を書く**（FR-019・SC-015） |
| (c) `config.json` | `SettingsConfigError` | `JsonConfigSettingsSource` は `json.loads` を呼ぶので、構文誤り・空ファイルは `JSONDecodeError`、トップレベルが配列なら `TypeError`。**どちらも `ValidationError` ではない**ので `except ValidationError` だけでは漏れる |
| (d1) 雛形 | `SettingsAreaError` | 書込中の失敗で一時ファイルを残さない（FR-018。`os.replace` に失敗を注入するテストが担保） |
| (d2) 読取 | `SettingsAreaError` | 壊れた `model.conf` を **PJ ごとの `policy.csv` のせいにしない**（`read_policy` は自前で判定器を組むため PJ の数だけ `TypeError` が上がる。原因ファイルの絶対パスで 1 件に畳む） |
| (d3) 整合検査 | `SettingsValidationError` | **一覧**で返す。最初の 1 件で打ち切らない |

(b) が絶対パスを強制するので、以後の `root` は必ず絶対＝ (d1)(d2) の失敗メッセージも絶対に
なる（`realpath` の結果は保持しない＝ R-05）。

## 整合検査で「黙って迂回しない」ための 2 つの形

- **V-17 は V-28 が通っている PJ でだけ当てる**（契約 §7 行 17・裁定 2026-08-24・A1）。
  `role_dirs` のキーが綴り違いのとき、V-17 は「`agents.yaml` のメンバが役割フォルダに無い」と
  原因（`project.yaml`）と違う場所を指す**誤診**になる。「先に当てる」を**出力順だけ**で
  済ませると誤診が併記されたまま残るので、抑止の分岐が要る。
- **検査と写像は `humans.yaml` を同じ経路で読む**（R-01。`resolve.human_actor_names`）。
  検査側だけが「1 件でも非文字列ならリスト全体を捨てる」読み方をすると、写像側は残りを拾うため
  **V-20 の衝突が違反 0 件のままスナップショットに残る**。読めない要素そのものも違反にする。

## 規約 5: 保存は候補**全体**を検査してから書く（部分保存を作らない）

- 4 段である —— **組む**（純関数。1 バイトも書かない）→ **検査する**（候補に前半の
  `validate_forward`／`validate_reverse`／`validate_commands` を当てる ＋ 候補 policy の構文検査）
  → **書く**（2 段書込）→ **差し替える**（台帳再導出・判定器再構築・世代 +1・`replace_snapshot`）。
- **検査に落ちたら正本を 1 バイトも書かない**（FR-012）。一時ファイルも `finally` で必ず片付ける。
- 候補 policy の構文検査だけは**一時ファイルを要する**（casbin の `FileAdapter` はパスしか
  受けない＝ 010 SD-02）。置換先と同じディレクトリに作り、**通ればそのまま置換に再利用する**。
- **書込が全部成功してから**世代を +1 して差し替える（FR-021。**世代だけ進んだ状態を作らない**）。
- 新規 PJ のディレクトリは**段 3 の先頭**で作り、置換に到達せず落ちたら**空のまま片付ける**
  （plan D-j。空のまま残すと `list_project_keys` が拾って**次回起動が中断する**）。

## 規約 6: 領域 A への書込は `repository.py` の 1 箇所

- 実行時に書くのは **`AreaAWriter` だけ**（FR-026・SC-024）。`tests/unit/test_settings_boundaries.py`
  が `src/settings/` 全体の AST を走査して機械的に確かめる。
- **`AreaAScaffolder` を流用しない**。`exists()` の分岐は「既存を上書きしない」ための唯一の
  防壁であり、上書きする書込を同じ入口に混ぜると防壁が緩む。
- `os`・`shutil`・`tempfile` を `repository.py` 以外で import しない（同テストが見る）。

## 規約 7: `ui.py` は HTTP のみ。`settings.*` を import しない

- 憲章 §4 注記の実装形。import してよいのは `nicegui` と標準ライブラリだけで（`016` で
  `streamlit` から置き換えた。許可の最上位名は `__future__`・`asyncio`・`collections`・
  `dataclasses`・`json`・`typing`・`urllib`）、
  `.importlinter` の **`ui-pages` 契約**と AST 走査が二重に担保する。
- HTTP クライアントは**標準ライブラリ `urllib.request`** の `call_api` **1 関数**に閉じる
  （010 SD-06。`httpx` は `httpx-scope` の対象側、`requests` は「避けられる第三者依存」で退けた）。
  `HTTPError` は**それ自体がファイルオブジェクト**なので本文を必ず読む（422 の違反一覧・
  409 の理由を取りこぼさない）。
- **`.env` を読まない**。接続先 app の base URL は `apps/ui` の登録が**閉包で渡す**（010 SD-05）。
- **新規 LLM プロファイルの雛形の正本は画面側**である（FR-094・010 SD-19）。`settings/config.json`
  の `llm_presets`（CFG-38）との二重管理は design-sync **B-48** に記録してある。

## 規約 8: 世代は保存経路が決める

- `build_snapshot` は**常に世代 1**、`replace_snapshot` は**単調増加の検査と差替えだけ**を行う。
  **この 2 つを変えない**（010 SD-01）。保存経路が `dataclasses.replace(snapshot, generation=現在+1)`
  を作る。
- 応答ヘッダは `X-Settings-Generation`（10 進整数）と `X-Settings-Effect`（`immediate` ｜
  **`next-kickoff`**）で、**書込 22 操作すべて**に載る（204 でも）。読取 16 操作には載せない。
  ⚠ 内部表現は `EffectClass.next_kickoff`（**アンダースコア**）で、**写像は
  `router/__init__.py` の 1 箇所にだけ**置く（010 SD-18）。

## コード定数に残す 13 種（憲章 §7-4 の判断＝ C-4）

`AGENT_FIELDS`（10）・`AGENT_GLOBAL_REQUIRED`（3）・`FORBIDDEN_FIELDS`（5）・
`SECRET_KEY_WORDS`（4）・`TASK_FIELDS`（4）・`TASK_REQUIRED_FIELDS`（2）・`TASK_KEYS`（7）・
`ALLOWED_VARS`（15）・`REQUIRED_VARS`・`SUBJECT_PREFIX`・`MEMBER_ID_SEPARATOR`・
`PROBE_TRIPLE`・`SCOPE_GLOBAL`。正本は `models.py`。

**設定にしない理由は共通**である。いずれも「環境で変わる値」でも「運用で調整する値」でもなく、
**設計判断（D16-a/b/c/d/f・D06-b・P5D-029）が固定した閉じた語彙**である。加えて `config.json` の
キーにすると設定台帳 `x3_config_catalog.csv` に CFG 行の新設が要るが、同ファイルは**変更しては
ならない**。変更が要ると判明したら CFG 行の新設をユーザに諮る。

**`model.conf` の本文は 13 種に含めない**。正本は `defaults/model.conf`（同梱データ）＋
`test_settings_defaults.py` 内の照合リテラルであり、`models.py` にも持つと**ファイル・テスト内
リテラル・コード定数の 3 重管理**になる（SD-26）。

**`config.json` に出すのは CFG-36〜40 の 5 つだけ**である（FR-060）。

## 不変性の範囲が一様でないこと（C-3）

`SettingsSnapshot` は `@dataclass(frozen=True, slots=True)` で、`Mapping` は構築時に
`MappingProxyType` で包む。しかし **`Enforcer` は可変**である（M-12）。`frozen` は「参照の
差し替え」を止めるだけで、参照先の可変性には及ばない。担保は `enable_auto_save(False)` と
「**settings が変更系メソッドを呼ばない**」ことに依存する（FR-048d）。

## 外部パッケージの版（FR-064）

| パッケージ | 版 | ライセンス |
|---|---|---|
| `casbin` | **1.43.0** | Apache 2.0 |
| `simpleeval`（casbin の唯一の推移依存） | **1.0.7** | MIT |
| `pyyaml` | **6.0.3** | MIT |
| `fastapi` | **0.141.1** | MIT |
| `nicegui` | **3.17.1** | MIT |

**推移依存まで含めて憲章 §11 に適合**（M-01。強コピーレフトに該当しない）。
`fastapi`・`nicegui` は**いずれも `py.typed` を持つ**ので `# pyright: ignore` の手当ては
ほぼ要らない（010 SD-11）。ただし nicegui 3.17.1 の `ui.select.set_options` は
`options: list | dict` の素のジェネリクスで、要素の型が解決しきれない（`trigger/ui.py` の
2 箇所に理由つきの `# pyright: ignore[reportUnknownMemberType]` を置いた）。**`httpx` も `requests` も実行時依存に入れない**（テストの `TestClient` が
使う `httpx` はルートの dev 依存にあり、`src` レイアウトなので grimp のグラフに載らない）。
**版を上げたら実測 M-01〜M-27 を再確認する**（`003` が `bd 1.1.2` を D21-e で固定したのと同型）。
ルート `pyproject.toml` の `[dependency-groups] dev` の `pyyaml` には**相乗りしない**
（「openapi.yaml 照合テスト専用。実行時依存にしない」と用途が明記されている）。

> 恒久的なライセンス互換の記録先（憲章 §11 が指定する「開発プロセス B の成果物」）が
> リポジトリに存在しないため、本節の記載は**暫定**である（→ design-sync B-12）。

## 改修時の注意

- **止め木の両輪を壊さない**。`SettingsEnv` の明示 `__init__(self, **overrides)` と `build()` の
  `SettingsEnv(_env_file=env_file_path())` が**対で**在るときだけ、テストの autouse 止め木が効く。
  どちらかを消すと止め木は**黙って無効**になり、**settings の build は書込を行う**ため、テストが
  実運用の領域 A に雛形を書き込む（読むだけの vault と違い **1 回で観測可能な被害が出る**）。
- **`kind` の語彙を増やさない**（FR-046）。正本は `_archive/` 配下＝変更してはならない。
- **保存時だけの規則は 3 つだけ**（`key_rule`・`folder_missing`・既定 LLM プロファイルの削除の
  拒否＝ `llm_ref`）。**起動時の 28 規則に 1 つも足さない**。`V-16`（閾値）と `V-27`（上限の
  値域）は既に起動時へ前倒し済みなので**保存経路で再実装しない**（同じ規則を 2 箇所に書かない
  ＝ SC-013。`test_settings_boundaries.py` が AST 走査で代理観測する）。
- **口から到達できない経路が 2 つある**（契約どおり実装した結果。塞ぐには契約の改訂が要る）——
  `POST /settings/commands` は V-24／V-25 の相互ロック（**B-40**）、
  `PUT`／`DELETE /settings/agents/{pj}/{persona}` の**新規作成と削除**は V-19／V-21 の
  相互ロック（**B-49**。到達可能なのは既に居るメンバの編集だけ）。テストが行き止まりを固定している。
- **`packages/common/` を 1 行も変更しない**（FR-006）。**引数名は契約の一部**であり、変えると
  pyright strict の構造的部分型の適合検査が壊れる。
- **テストファイルの基名は `test_settings_*` で統一する**（FR-073）。`tests/` に `__init__.py` を
  置かないため基名がそのまま top-level モジュール名になり、feature をまたいで重複すると
  **全体の `uv run pytest` だけ**が collection error で止まる（モジュール単位では再現しない）。
- 共有の準備手段は `tests/settings_testing.py` に置く（`conftest.py` と別名。`unit/` と
  `integration/` の双方に `conftest` があると `from conftest import …` が曖昧になる）。
- `service/` に**ファイルを足す**形で後半を載せる（サブパッケージの器は前半で作ってある）。
- **1 ファイル 500 行を超えたら同名サブパッケージに分ける**（憲章 §3・plan (xii)）。前半では
  `service/validate/`（plan (xii) 段 3 の契機を満たした）と `service/loader/`（同じ閾値を同じ
  方針で当てた）を分けてある。分けても**公開する名前は変えない**（`__init__.py` が再エクスポート
  するので、呼び手の `from settings.service.validate import …` はそのまま通る）。
  - **例外: `ui.py` は分割しない**（`010` B-52・`016` ND-17。2026-09-29 の実測で 1,843 行）。
    ファイルを分けても、モジュール全体のトークン数は減らない（憲章 §1 の規模は解けない）。
- **変更の前に、モジュール全体のトークン数を測る**（`016` design-sync B-38 の裁定）。数え方は
  `011` B-63 と同じで、本フォルダ配下の全ファイル（`__pycache__`・`.pytest_cache`・`.ruff_cache`
  を除く）を 1 つずつ tiktoken の `o200k_base`（主）と `cl100k_base` で符号化して足す（使い捨ての
  `uv run --no-project --with tiktoken` で数え、`.venv`・`uv.lock` を変えない）。plan の
  Constitution Check で、憲章 §1 の上限までの残りを確かめる。**上限の数値はここに書かない**
  （憲章を正とする）。2026-09-29 の実測は **249,616**（`o200k_base`）／**276,640**
  （`cl100k_base`）。どちらも Claude の符号化器ではない。

## 検証コマンド

```bash
# モジュール単位（憲章 §1・SC-013・US8 AC-1）
uv run pytest packages/features/settings/tests
uv run ruff check packages/features/settings
uv run pyright packages/features/settings

# 外部ツールを持たない環境でも 100% 走る（SC-002・US8 AC-3）。印を新設しないので skip 0 件
uv run pytest packages/features/settings/tests -m "not requires_bd"

# 4 ゲート（1 コマンドずつ実行する。&& で連ねない）
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports            # → Contracts: 7 kept, 0 broken.
uv run pytest

# 土台（登録点の整合）
uv sync
uv run pytest apps/api/tests/foundation
```

⚠ **`build()` を呼ぶコマンドには必ず `SETTINGS_ROOT=$(mktemp -d)` を前置する**。前置を忘れると
実 `.env` の `SETTINGS_ROOT` が指す**実運用の領域 A に雛形が書かれる**（FR-019・SC-015）。

**`016` で足した NiceGUI とその推移依存（暫定の記録先。research R-16）**: 憲章 §11 のライセンス
互換の成果物が不在（`005` B-12）なので、`010` SD-11 と同じ扱いでここに記す。追加は **12 件**で、
**いずれも GPL 系ではない**。

| パッケージ | 版 | 直接／推移 | ライセンス | 判断 |
|---|---|---|---|---|
| `nicegui` | **3.17.1** | 直接（`ui.py` だけが import する） | **MIT**（`py.typed` あり） | 可 |
| `pytest-asyncio` | 1.4.0 | 直接（dev。`user` フィクスチャが async ジェネレータ） | Apache-2.0 | 可 |
| `docutils` | 0.23 | 推移（nicegui） | Public Domain ＋ BSD-2/3-Clause（分類子に GPL も並ぶ） | **可**。同梱 `COPYING.rst` によれば本体は Public Domain で、GPL-3.0-or-later が掛かるのは `tools/editors/emacs/rst.el` の 1 ファイルだけ。wheel の `RECORD`（225 行）に `.el` は 0 件で、**配布物に GPL のファイルは含まれない**（実測） |
| `bidict` | 0.24.1 | 推移（python-socketio） | **MPL-2.0** | **可**。弱いコピーレフト（ファイル単位）で、未改変のまま依存として使う限り本リポジトリのコードに義務は及ばない。憲章 §11 が避けるのは GPL 系（AGPL・SSPL 等の強いコピーレフト）である |
| `python-socketio` | 5.17.0 | 推移 | MIT | 可 |
| `python-engineio` | 4.14.0 | 推移 | MIT | 可 |
| `simple-websocket` | 1.1.0 | 推移 | MIT | 可 |
| `wsproto` | 1.3.2 | 推移 | MIT | 可 |
| `ifaddr` | 0.2.0 | 推移 | MIT | 可 |
| `markdown2` | 2.5.5 | 推移 | MIT | 可 |
| `tinycss2` | 1.5.1 | 推移 | BSD | 可 |
| `webencodings` | 0.6.1 | 推移 | BSD | 可 |
