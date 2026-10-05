# settings（BC-01 設定）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_settings_sequence.mmd`（主図: 実行時の読取・判定）／`p5_settings_sequence_uc07_save.mmd`（副図: AI 設定の一覧・保存）／`p5_settings_sequence_uc08_project.mmd`（副図: プロジェクト CRUD）／`p5_settings_data_model.mmd`／`p5_settings_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-013〜017（＋P5D-009 パス基準）、**P5-R1 は D16（agents.yaml／tasks.yaml 化。P5D-015/017 を改訂）と `../../p5r1/p5r1_notes.md`（仕様の正本）**。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-23〜30・IF-50（提供 9 行）・IF-09・IF-32（利用 2 行）／p4 notes §1（≈2,500〜3,000 行の受容 D09-a）／`../../phase2/p2_domain_model.mmd` DM-01〜07/11/12/26／`p2_sequence_UC-07.mmd`・`UC-08.mmd`／D06-a〜e,g,j・D07-e,f,g・D08 Q3／PoC `crewai_casbin_control`（`fleet.yaml`・`policy/model.conf`・`policy/policy.csv`・`src/crewai_casbin_demo/fleet/loader.py:1-30`・`authz.py`・`fleet/validate.py`。PoC 基準版、読取のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

settings は **設定ファイル領域 A の唯一の所有者**で、`SettingsReader`・`MemberResolver`・`Authorizer`・`CommandRegistry` の 4 Protocol を 1 つの `SettingsService` で実装し、UC-07（領域 A のファイル群＝agents.yaml・tasks.yaml・policy・rules・hooks・LLM・上限・コマンド定義 D16）・UC-08（プロジェクト）の CRUD と validate（順方向・逆方向・cmd⊆定義）を持つ。casbin（`Enforcer`）はここに閉じる（linter (3)）。**保存は候補全体を検証してから書き、不変スナップショットを丸ごと差替える**（P5D-016）。台帳 DM-07 は保存時に導出しファイルにしない（P4）。UI（Streamlit 2 ページ）と router（HTTP API）を持ち、UI は router を HTTP で叩く。BoardReader（削除ガード）・VaultReader（存在確認）を使う。

## 1. ファイル構成（憲章 §3。集約別にファイルを割る＝D09-a の受容条件）

```
packages/features/settings/
├── pyproject.toml
├── config.json          # SettingsConfig: key_forbidden_chars／context_reserve_tokens／llm_presets／limits_defaults／system_actor
├── defaults/            # 出荷時既定 agents.yaml・tasks.yaml（初回ロードで領域 A にコピー D16-g。git 版管理・テスト対象）
├── SKILL.md             # casbin はここだけ／領域 A のレイアウト（agents.yaml・tasks.yaml D16）／validate 3 種＋agents/tasks 検査の意味／「台帳は導出・保存しない」
├── src/settings/
│   ├── __init__.py      # build(deps: SettingsDeps, *, config: SettingsConfig | None = None) -> SettingsHandle
│   ├── router/          # /settings/* の FastAPI router（F21）。**18 パス・39 操作**（D26 で役割の読取 1 を追加）を 4 集約に割る
│   │                    #   __init__.py（create_settings_router＋保存結果ヘッダの写像）・_translate.py
│   │                    #   （例外→応答を 1 箇所に閉じる）・projects.py 6・agents.py 11・ai_config.py 14・policies.py 8
│   │                    #   6 ファイルとも `from __future__ import annotations` を置かない（局所 provider の解決）
│   ├── ui.py            # Streamlit: AI 設定ページ（UC-07）・プロジェクトページ（UC-08）。HTTP のみ（F22）
│   │                    #   **settings.* を 1 つも import しない**（憲章 §4 注記。ui-pages 契約が機械検査）
│   ├── schemas.py       # HTTP DTO（*Spec／*View／problems）。契約型（common/interfaces/settings.py）とは別
│   ├── service.py       # SettingsService: 4 Protocol の実装＋CRUD＋validate＋スナップショット差替え（F02〜F20）
│   │                    #   行数が目安を超えるので集約別に分割（パッケージ内分割。MOD 境界は不変）。
│   │                    #   割り: service/{__init__,resolve}.py ＋ service/loader/ ＋ service/validate/（読取コア）
│   │                    #        ＋ crud.py（39 操作の入口・生の構造の保持）・save.py（F02 の 4 段）
│   │                    #        ＋ projects.py／members.py／ai_config.py（候補づくりの純関数）
│   │                    #        ＋ roles.py（D26。役割ごとの実効設定＝閲覧のみ。規則を足さない）
│   ├── repository.py    # 領域 A の読書き（YAML／csv／md、一時ファイル→os.replace、_archive 退避）（F01）
│   │                    #   実行時の書込は AreaAWriter の 1 箇所（AreaAScaffolder＝雛形生成とは別クラス）
│   ├── models.py        # 領域 A のファイル写像（ProjectYaml… の内部モデル）・SettingsSnapshot・Registry・PolicySet・CommandDef
│   │                    #   ＋ 保存経路の内部 VO（EffectClass・FileWrite・ChangeSet・SaveOutcome）
│   ├── dependencies.py  # Depends: service の差し替え点 1 箇所（service_provider）
│   └── exceptions.py    # SettingsValidationError（problems）＋ SettingsItemNotFound（404）・ProjectDeletionBlocked（409）
│                        #   契約例外 ProjectNotFound／MemberNotFound／UnknownCommand は common/interfaces/settings.py
└── tests/               # 領域 A は一時 dir。BoardReader／VaultReader は**スタブ**（bd・実 vault に接続しない）
                         #   UI は AppTest.from_function（ブラウザを立てない＝憲章 §4）
```

**モジュールの完成形**: 本モジュールは**読取・検査・保存・HTTP の口・画面**をすべて持つ（垂直スライスが閉じている＝憲章 §2）。`SettingsHandle.router` は **39 操作を持つ `APIRouter`**（D26 で 38 から）（既定値を持たない＝組み立ての戻りは必ず口を持つ）。

`build(deps: SettingsDeps, *, config: SettingsConfig | None = None) -> SettingsHandle`（D11-f）: `SettingsDeps(board_reader, vault_reader)`（**2 依存**。`BoardReader`＝ IF-09 は F11-c の削除ガード、`VaultReader`＝ IF-32 は F11-a・F11-b の実在確認でだけ呼ぶ）。`SettingsHandle(settings_reader, member_resolver, authorizer, command_registry, router)`。build は `.env` の `SETTINGS_ROOT` と config.json を読み、領域 A を初回ロード（不在なら global/ の雛形＝model.conf・defaults/{agents.yaml, tasks.yaml} のコピーを生成 D16-g）して初期スナップショットを組む。validate 違反があれば起動中断（憲章 §5）。任意引数 `config: SettingsConfig` は統合テストが挙動設定を差し替えるための口である（x3_notes §5 の ⑧ 層）。

## 2. Protocol 実装の対応

| Protocol.メソッド | if_id | fn | 要点 |
|---|---|---|---|
| SettingsReader.get_project | IF-23 | F03 | Project（folders は vault ルート相対 P5D-009。_program も登録要 P5D-014） |
| SettingsReader.get_limits | IF-24 | F04 | global＋PJ 上書きの解決（D06-g） |
| SettingsReader.get_rules | IF-25 | F05 | projects/<key>/rules.md の本文のみ（D16-h。役割ルールは backstory に吸収。合成は stages） |
| SettingsReader.get_tasks | IF-50 | F25 | tasks.yaml 2 本 → TaskTemplates（7 キー・世代付き D16-h） |
| SettingsReader.get_hooks | IF-26 | F06 | hooks.yaml → HooksConfig（登録は executor） |
| MemberResolver.resolve | IF-27 | F07 | agents.yaml ④→⑤ merge（D16-d）＋台帳導出＋LLM プロファイル解決（PJ→global→default D16-h）＋threshold_T 導出（P5D-013） |
| MemberResolver.is_human | IF-28 | F08 | 台帳外は印付きで False（D06-c） |
| Authorizer.enforce | IF-29 | F09 | PJ ごとの casbin Enforcer。PJ が引けなければ False |
| CommandRegistry.resolve_command | IF-30 | F10 | commands.yaml → 固定 argv（D07-e） |
| （利用）BoardReader.has_open_issues | IF-09 | F11 | 削除ガード。読めなければ拒否側 |
| （利用）VaultReader.check_project_folders | IF-32 | F11 | 登録・編集時の存在確認 |

人間 actor（単一利用者の bd actor 名）は `global/humans.yaml` に宣言し、台帳の `human_actors` に載せる（P5D-019。AI メンバは導出・人間は宣言）。

読取範囲の規則（ユーザ確認 2026-08-19）: 段・ペルソナ種別で区別せず、全ペルソナ（オーケストレート系を含む）が PJ 入力＋自分の役割フォルダ＋outcome/ を読む。**メンバを作ったペルソナには role_dir が必須**（validate で拒否。P5D-018。先に UC-08 で役割フォルダを登録してから UC-07 でメンバを作る）。

settings が持たないもの: rules の backstory 合成（stages）、hooks の crewai への登録・LLM 構築・api_key の実値読取（executor）、フォルダ実体の作成（人間）、kickoff 時の存在検証の実行（orchestrator が VaultReader を呼ぶ。settings は登録時のみ）、段の構造属性 allowed_personas（stages。tasks.yaml の agent: は「段の既定ペルソナ」で validate は PersonaKey 照合のみ＝stages 非依存 D16-f）。

## 3. 受け渡し型（common/interfaces/settings.py）

`Project`（project_key・name・folders・is_reserved）／`ProjectFolders`（pj_input_dir・role_dirs: dict[PersonaKey, str]・outcome_dir。**vault ルート相対**）／`Member`（member_id・project_key・persona_key・role（旧 display_name 統合）・goal・backstory・bd_actor・casbin_subject・llm_profile: LLMProfile・tools: list[ToolName]・skills・mcps・max_iter・max_rpm・max_execution_time。D16-h）／`LLMProfile`（name・provider_kind・base_url・model_id・context_window・api_key_ref・extra_headers・threshold_T）／`ProviderKind`（openai_compatible｜openrouter。**追記**）／`LimitPolicy`（scope・max_requests_per_kickoff・max_cost_per_kickoff）／`Rules`（project_rules のみ D16-h）／`TaskTemplates`・`TaskTemplate`（IF-50 の戻り。**P5-R1 追記**）／`HooksConfig`（hooks: list[HookRef]）／`HookRef`・`McpSpec`（**追記**）／例外 `ProjectNotFound`・`MemberNotFound`・`UnknownCommand`（NotFoundError）。p4 notes §2 の 7 件に ProviderKind・HookRef・McpSpec を追記（フェーズ末改訂）。

## 4. シーケンス図

| 図 | 対象 |
|---|---|
| `p5_settings_sequence.mmd`（主図） | kickoff 開始時の 5 読取（get_project／resolve／get_limits／get_rules／get_hooks）＋実行中の enforce／resolve_command＋is_human。スナップショット参照のみ |
| `p5_settings_sequence_uc07_save.mmd` | UC-07: 一覧 → 変更案 → 候補スナップショット → validate 3 種＋casbin 構文 → 領域 A 書込 → 台帳再導出 → 差替え → 反映区分の表示 |
| `p5_settings_sequence_uc08_project.mmd` | UC-08: 登録（キー規則→VaultReader 存在確認→書込）／編集／削除（_program 不可→BoardReader 削除ガード→_archive 退避） |

レーン: `SettingsReader／MemberResolver`・`Authorizer／CommandRegistry`（呼び手）／`VaultReader`・`BoardReader`（利用先）／`settings/ui.py`・`router.py`・`service.py`・`repository.py`・`lib:casbin`／`自分`・`設定ファイル領域 A`。

## 5. PoC からの継承と変更（証跡: casbin PoC）

| 項目 | PoC | V4 |
|---|---|---|
| 設定表 | `fleet.yaml` 1 枚のポインタ表 | PJ ごとのフォルダ＋global/（P5D-015→D16 で agents.yaml／tasks.yaml に改訂）。fleet.yaml の書式でなく CrewAI 標準の agents.yaml／tasks.yaml のファイル名・フィールド名に寄せる（D15-j） |
| メンバ ID | `<project>.<key>`（`manifest.py:87`） | 同形（D06-b）。キーは PersonaKey 列挙 |
| 主体 | `role_*`／`member:` 混在 | `member:<member_id>`（agent: 全廃 FN-23） |
| model.conf | `policy/model.conf`（g 2 項・keyMatch） | **PoC から変更なしで継承**し global/model.conf 共通（雛形から生成）。`casbin 1.43.0` で実測済み（§7-4） |
| validate | `collect_problems`／`_check_orphan_subjects` | 順方向・逆方向＋cmd⊆定義（FN-54 新設）を候補全体に対して保存前に |
| enforce | `authz.py authorize`（Tool 名・リソース・委譲） | `enforce(subject, obj, act)` の純粋判定。Tool 名→obj/act の写像は executor 側 |
| 委譲 delegate | あり（manager） | 持たない（本 ver の最低限組織。FN-23 残り据え置き） |
| model 割当 | `defaults.model`＋agent 単位 | LLM プロファイル（provider_kind 付き P5D-013）＋メンバ単位割当＋グローバル既定 |
| UI | fleet_ui（Streamlit） | ui.py（HTTP 経由・AppTest） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py settings --render` → ERRORS: none
- participant は Protocol 名 4 群・内部ファイル 5・外部 2（自分・領域 A）のみ
- 提供 9 行・利用 2 行の if_id が feature_list に現れ、9 メソッド全てに実装 fn（D16 で IF-50 が新設された後の姿。`p5_selfcheck.py` は散文を見ないので本行の stale は検出されない）

## 7. 未決（settings 固有）

1. ~~bd actor に `.` を含む名前（member_id）が使えるか~~ → **決着（D21-a）**: `設計判断_V4-D21` D21-a が実測で「使える」と確定させた（`BEADS_ACTOR="demo_pj.programmer"` でコメント追記・更新が成功し `author` に無改変で読み戻る）。台帳の `bd_actor` は**メンバ ID をそのまま**である。settings は bd に触れないため分岐は生じない。**再実測しない**
2. **openrouter provider 経路での Interceptor 装着**（P5D-013 の帰結）→ **executor の回へ送る**。settings が持つのはプロファイルの `provider_kind` までで、装着の可否は settings の設計に影響しない（REC-01）
3. **MCP（`Agent.mcps`）の解決可能性検査の中身**（PoC は宣言・検証のみ ▲）→ **M4（実接続の回）へ送る**。前半の整合検査は**宣言の形式検査まで**（`name`・`transport`・`command`・`args` が揃っているか＝ V-18）で、接続も PATH 解決もしない（REC-02）
4. ~~雛形の権限モデル（`global/model.conf`）の中身~~ → **決着（`005-settings-read` の実測。`casbin 1.43.0`／推移依存 `simpleeval 1.0.7`）**: PoC `crewai_casbin_control` を**変更なしで継承**する（5 節すべて）。P5D-029 の V4 obj 語彙 3 系統 16 ケースが期待どおり判定される。**`g = _, _` を落としてはならない** — g 行を含む policy を g 無しの model.conf で読むと g 行は黙って捨てられ `p, role_*, …` 行が到達不能になり、`get_grouping_policy()` は `TypeError` を送出する。PoC に無かった**運用側の担保 3 件**を足す: (a) `enable_auto_save(False)`（Enforcer は可変で auto_save の既定が真）、(b) policy 行の列数検査（p=3・g=2。不正 arity は構築時に通り判定時に落ちる）、(c) 起動時の空打ち `enforce()` 1 回（壊れた matcher は構築時に検出されず最初の判定で落ちる）。**戻り値は判定に使わない**（obj が `*` だけの行があると健全な設定でも真が返りうる）
5. **`SETTINGS_ROOT/_archive/`** の保持期間・掃除 → **決着**: **持たない**（人間が消す）。退避（F11 のプロジェクト解除）の実装形は `_archive/projects/<key>.<UTC の時刻印>/` への **rename** で、**既存があれば上書きせず失敗**する（`FileExistsError`）。**掃除**の仕組みも消す口も持たない（`AreaAWriter` に退避を消すメソッドが 1 つも無いことをテストが固定している）。保管庫のフォルダ実体は 1 つも触らない（REC-03）
6. **casbin のポリシー粒度を `area` 単位にするか役割単位にするか**（`004` vault 実装で浮上＝ D22・vault の FR-026a・VD-23）: vault は役割どうしを分離せず、`area=role` の領域根は宣言された役割フォルダの**和集合**になる。分離を `Authorizer.enforce` の `obj` 語彙で担保するなら `vault:role:<persona>` の粒度が要り、呼び手（executor）の絞り込みだけに委ねるなら `vault:role` で足りる。
   → **settings 側の裁定は要らない**（`005-settings-read` の実測）。settings は権限行の対象を**文字列として扱うだけ**（P5D-029）で、`keyMatch` は語彙に非依存であり、**同じ model.conf が両方の語彙を許す**。ただし手引きに載せる落とし穴が 3 件ある: (1) `vault:role:*` は `vault:role` 自身に一致しない、(2) `vault:role*`（コロンなし）は `vault:roles_other` にも一致する、(3) obj が `*` だけの行は全対象を許可する。どちらを使うかは executor と vault の**呼び手の語彙選択**である
