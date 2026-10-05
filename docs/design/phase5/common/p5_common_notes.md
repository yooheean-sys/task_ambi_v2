# common（共有カーネル）説明（第5フェーズ）

**位置付け**: `p5_common_data_model.mmd` の短文の正本。common は MOD ではなく、全 MOD が参照する **kernel**（列挙・共通例外の基底・log 基盤・ルート設定の読み口）と **interfaces**（14 Protocol・41 メソッド＋受け渡し型 37 件・提供 MOD 固有の列挙 9 件・契約例外 14 件）からなる。本書と図は kernel のみを扱う。interfaces の Protocol は `../../phase4/p4_module_contract.csv`（50 行。P5-R1 で IF-50 を新設）が正本、受け渡し型の属性は各提供 MOD の `p5_<MOD>_data_model.mmd`（namespace `common_interfaces_<MOD>_py`）が正本（D11-d）。
**レジーム**: 改訂可（図と同時に更新）。裁定は D11・`../p5_decision_log.csv`（P5D-001／002）。

## 1. ファイル構成（憲章 §3）

```
packages/common/
├── pyproject.toml            # uv workspace パッケージ（憲章 §7-5）。feature を import しない（linter (2)）
├── src/common/
│   ├── kernel/
│   │   ├── enums.py          # PersonaKey／StageName／ToolName／KindLabel／StateLabel／LabelDim／Labels（組立・分解）
│   │   ├── errors.py         # AppError と 4 基底（NotFound／Unavailable／Unverified／Violation）
│   │   ├── log.py            # CorrelationContext（contextvar）／LogFields／LoggerFactory（root.log.redact_keys のマスクフィルタ D15-i (2)・P5D-050）
│   │   └── config.py         # RootConfig（＋LogConfig・DefaultsConfig）。ルート config.json の読み口（CFG-14〜21。cwd 非依存・環境変数で上書きしない。DIFF-05・`000` plan (iv)）
│   └── interfaces/
│       ├── board.py          # BoardReader・BoardWriter＋Issue・Comment・IssueSpec・ClaimFilter＋IssueNotFound・BoardUnavailable・BoardWriteUnverified
│       ├── settings.py       # SettingsReader・MemberResolver・Authorizer・CommandRegistry＋Project・ProjectFolders・Member（CrewAI フィールド D16-h）・LLMProfile・LimitPolicy・Rules（project_rules のみ D16-h）・HooksConfig・TaskTemplates（IF-50 D16-h）＋ProjectNotFound・MemberNotFound・UnknownCommand
│       ├── vault.py          # VaultReader・OutcomeWriter＋FolderCheck・FileRef・WrittenPath＋VaultNotFound・VaultPathViolation
│       ├── monitor.py        # RunMonitor・LLMHttpHook＋RunVerdict・Measurement＋RunHalted(BaseException)・MappingUnverified（httpx は型注釈のみ）
│       ├── stages.py         # StageCatalog・ProposalGate＋StageSpec・PromptBundle・Proposal・GateResult＋UnknownStage・UnknownPersona
│       ├── executor.py       # CrewExecutor＋CrewSpec・CrewOutcome・CrewEvent＋ExecutorFailure
│       └── orchestrator.py   # KickoffRunner＋ExecutionContext・KickoffResult＋KickoffBusy
│                             # ※ 循環回避（DIFF-08・R-06）: Pydantic フィールド型に要る型は実行時 import、
│                             #    Protocol の注釈にしか要らない型は TYPE_CHECKING import。executor→monitor は実行時、
│                             #    monitor→executor（CrewOutcome）と httpx は型検査時のみ。
└── tests/
```

- 契約例外（`IssueNotFound` 等）は **提供 MOD の interfaces ファイル**で定義し kernel の基底を継承する（利用 MOD は interfaces だけを読めば例外まで分かる）。`RunHalted` のみ `BaseException` 直系で階層外（Z-6）
- vault.py の `FolderCheck`・`FileRef`・`WrittenPath`（＋`VaultArea`）は契約 csv 上の戻り型（IF-31〜37）で、受け渡し型として確定済み（P5D-012）。`p4 notes §2` の表への**追記済み**（`vault.py` 行）

## 2. kernel の中身（図の対応）

| 区分 | 型 | 内容 | 根拠 |
|---|---|---|---|
| 列挙 | `PersonaKey` | 9 種（planner／project_lead／system_engineer／doc_lead／architect／programmer／analyst／writer／general） | D08 Q2（キーのみ common）。PoC `personas.py:105-215` |
| 列挙 | `StageName` | program／project／subrequest／child／plain | p2 DM-08、PoC `config.py`（`resolve_stage`） |
| 列挙 | `ToolName` | 12 種（read_issue／record_progress／list_input_files／read_input_file／list_outcome_files／read_outcome_file／escalate_to_human／propose_breakdown／write_outcome／complete_issue／run_command／submit_review） | P5D-045（D16-b）。executor が Tool 登録キーに、settings が agents.yaml／tasks.yaml の tools 検査に使う |
| 列挙 | `KindLabel` | program／request／subrequest／child／question | DM-13 ラベル体系、DM-15 |
| 列挙 | `StateLabel` | working／failing（同一次元で排他） | `loop.py:68-69` |
| 列挙 | `LabelDim` | kind／project／persona／role／state／note | D09-d のラベル語彙 |
| 関数群 | `Labels` | `kind()`／`project()`／`persona()`／`role_executor()`／`state()`／`note()`／`parse()`。文字列の組立・分解のみ | D09-d（意味論は board／orchestrator） |
| 例外 | `AppError` ＞ `NotFoundError`／`UnavailableError`／`UnverifiedError`／`ViolationError` | 契約例外の 4 系統の基底 | P5D-001 |
| log | `CorrelationContext` | `correlation_id` contextvar の bind／reset／current | D08-d／D09-e／憲章 §6 |
| log | `LogFields`／`LoggerFactory` | 構造化ログの共通項目と取得口 | 憲章 §6 |
| 設定 | `RootConfig`（＋`LogConfig`・`DefaultsConfig`） | ルート `config.json` の読み口（CFG-14〜21）。`find_repo_root()` で cwd 非依存に解決し、ソースは `(init_settings, JsonConfigSettingsSource)` だけ（環境変数で上書きしない）。`extra="forbid"`・`frozen=True` | 憲章 §5・DIFF-05・`000` plan (iv) |

**note の値語彙**（`note:single-child` 等）は kernel に列挙しない（stages／orchestrator が持つ）。`project:<key>` の key 規則（casbin キー規則）は settings が検査し kernel は形式を知らない。

## 3. 各 MOD が kernel をどう使うか

| MOD | 使うもの |
|---|---|
| board | `Labels`（ラベル文字列の組立・分解）、`StateLabel`、例外基底 |
| settings | `PersonaKey`（メンバ作成時の選択肢）、`Labels.project()`（削除ガードのラベル）、例外基底 |
| stages | `PersonaKey`・`StageName`・`KindLabel`（段の判定と allowed_personas）、例外基底 |
| orchestrator | 全列挙・`Labels`・`CorrelationContext`（router 入口で bind）・例外基底 |
| executor／monitor／vault | 例外基底・`LoggerFactory`（monitor は `CorrelationContext.current()` で Hook 呼び出しを kickoff に帰属。D09 §6-3） |
| trigger | `Labels`（起動条件の既定 `role:executor`）・`CorrelationContext`（発火時に bind） |

## 4. 自己検証

- `python3 ../p5_selfcheck.py common` → ERRORS: none（全クラスに永続化先注記）
- `mmdc` 描画 ✔（2026-08-19）

## 5. 確定した扱い

- `Labels.parse()` は**未知**の次元（既知 6 次元に無い接頭辞）・`:` を含まない文字列・空文字を受けたとき、例外を送出せず `None` を返す（`001` で確定＝DIFF-01・FR-014）。戻り型は `tuple~LabelDim,str~ | None`。
  board は課題のラベル一覧を丸ごと `parse` に通す（`../board/p5_board_sequence_reads.mmd:22`・`../board/p5_board_notes.md` §91・board-F16）ため、人間が付けた自由ラベルが 1 つあっても通常経路が落ちない。**未知**と判別したあとの扱い（読み飛ばすか扱うか）は呼び側の責務（P5D-002・D09-d）。
- 最初の `:` だけで分割し、値の妥当性は判断しない（`kind:` → `(kind, "")`、`note:a:b` → `(note, "a:b")`）。

## 6. 図から Python へ写す規約（`001` で確定。以後の全 MOD で共有する）

`p5_<MOD>_data_model.mmd` は型を省いた属性や日本語の属性名を含む。写し方が MOD ごとに割れないよう、次を規約とする。

| # | 対象 | 規約 |
|---|---|---|
| (1) | `<<module>>` のクラス | `@staticmethod` だけを持つ**名前空間クラス**として写す（`__init__` を書かずインスタンス化しない）。`Labels`・`CorrelationContext`・`LoggerFactory` が該当 |
| (2) | 型注記の無いスカラ属性 | 識別子・題名・本文・理由・名前は `str`（R-15 規則 1） |
| (3) | 時刻を表す属性 | `datetime`（aware）。`Issue.created_at`／`updated_at`・`Comment.created_at`・`FileRef.mtime`・`FailureEvent.at`・`Measurement.started_at`／`finished_at`・`CrewEvent.at`・`ExecutionContext.requested_at`・`KickoffResult.started_at`／`finished_at` の 11 件（R-15 規則 2） |
| (4) | `a \| b` の値域が図に直接書かれている属性 | `Literal[...]`（新しい列挙クラスを作らない）。`IssueRef.dependency_type`・`StageSpec.mode`・`CrewSpec.mode`・`HookRef.points`・`IssueRunSummary.result`・`VaultPathViolation.kind`・`ExecutorFailure.phase` の 7 件（R-15 規則 3） |
| (5) | 日本語で書かれた例外の属性 | `BoardWriteUnverified` の「不一致の内容」→ `mismatch: str`、`MappingUnverified` の「期待した状態／読めた状態」→ `expected: str`／`actual: str`（R-16）。属性を挙げていない 5 件（`ProjectNotFound`・`MemberNotFound`・`UnknownCommand`・`UnknownStage`・`UnknownPersona`）は**属性を足さない** |
| (6) | 綴りの正規化 | `LLMProfile.threshold_T` → `threshold_t`（Ruff `N815` 回避。R-03）。突合は大文字小文字を無視して行う |
| (7) | `既定 X` | `attr: T = X`（省略可）。R-17 規則 1 |
| (8) | `任意` | 省略可。既定値は型で決める（`T \| None` なら `= None`、コレクション型なら空コレクション）。R-17 規則 2 |
| (9) | 印なし | **必須**（`T \| None` であっても `= None` を付けない。呼び手が `None` を明示的に渡す）。R-17 規則 3 |

**突合の入力**（`packages/common/tests/_support/design_docs.py`）は `../../phase4/p4_module_contract.csv` と 7 つの `p5_<MOD>_data_model.mmd` **だけ**。本書のような散文は入力にしない（表記ゆれでテストが壊れるため）。上表 (2) は「実装がどう書くか」の指針であって図から機械的には導けないため（`TaskTemplates.generation` は無型だが `int`、`Member.max_iter` は無型だが `int | None`）、**突合は図が型を書いている属性だけを対象**とし、(3)(4) は名指しの表で補う。名前・必須／省略可・既定値は全属性で突合する。
