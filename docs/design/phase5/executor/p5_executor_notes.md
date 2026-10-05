# executor（BC-03 分解・遂行 — CrewAI 実行器）詳細設計 説明（第5フェーズ）

**位置付け**: `p5_executor_sequence.mmd`／`p5_executor_data_model.mmd`／`p5_executor_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-029〜033（＋P5D-013/021/022 の帰結）。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-48（提供 1 行）・IF-02/17/29/30/33〜35/37/40〜43（利用 12 行）／p4 notes §1・§3／`p2_sequence_UC-I1.mmd`／D06-e・D09-b,f／PoC `crewai_beads_renkei`（`build_llm`・`build_crew`・`build_review_crew`）・`tools.py`（11 Tool・`IssueSession`）、`crewai_casbin_control`（`RunCommandTool`）・`authz.py`、`crewai_ai_control`（Z-1〜Z-6）（PoC 基準版、読取のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

executor は **crewai を import する唯一の MOD**（linter (3)）。`CrewExecutor.run(CrewSpec)` 1 回＝1 kickoff の Crew 実行で、LLM（プロファイル＋`.env` の api_key＋monitor の Hook を `BaseInterceptor` アダプタで装着）・Agent・Task・Tools・hooks を組み、Tool から board／vault／settings の Protocol を呼び、結果と CrewAI のイベント・例外を**中立型**（`CrewOutcome`・`CrewEvent`・`ExecutorFailure`）に翻訳して返す。判断は持たない: 起票・close・質問起票・差し戻しは呼び手（orchestrator）、上限判定は monitor、権限の規則は settings。権限判定は **全 Tool の基底で必ず**通す（P5D-032）。

## 1. ファイル構成（憲章 §3）

```
packages/features/executor/
├── pyproject.toml        # 実行時依存は common・crewai（**この MOD が独占**）・httpx（**monitor との共同所有**＝ DIFF-12）・python-dotenv（.env の直読み＝ SD-17）
├── config.json           # ExecutorConfig: run_command_timeout_sec・max_iter・max_rpm（agents.yaml 未指定時の既定 D16-h）・event_allowlist・hook_catalog・disable_crewai_telemetry（D15-m）
├── SKILL.md              # crewai 型を契約に出さない／Tool は必ず GuardedTool を継承／LLM 構築は LlmFactory のみ／Hook 装着の実測メモ（Z-1〜Z-6）
├── src/executor/
│   ├── __init__.py       # build(deps: ExecutorDeps) -> ExecutorHandle（crew_executor／router=None）
│   ├── service.py        # CrewExecutorImpl: run（LLM・Agent・Task・Crew 構築、hooks 登録、kickoff、翻訳、RunHalted 捕捉）
│   ├── tools/            # base.py（GuardedTool・ToolContext・語彙 3 系統）／vault_tools.py（5）／board_tools.py（2）／command_tools.py（1）／declare_tools.py（4）／__init__.py（ToolRegistry・TOOL_BUILDERS）
│   ├── models.py         # ExecutorConfig・ExecutorDeps・RunSession・InterceptorAdapter・LlmFactory・env_file_path／read_env_values（内部）
│                         # **ToolRegistry と TOOL_BUILDERS は tools/__init__.py 側**（戻り値の型が tools/ の型なので models に置くと一方向が崩れる＝ C1）
│   └── exceptions.py     # なし（契約例外 ExecutorFailure は common/interfaces/executor.py）→ 置かない
└── tests/                # 単体: 6 Protocol＋LLMHttpHook をスタブ、LLM は crewai のモックまたはローカル固定応答
```

`build(deps: ExecutorDeps, *, config: ExecutorConfig | None = None) -> ExecutorHandle`: `ExecutorDeps(board_reader, board_writer, vault_reader, outcome_writer, authorizer, command_registry, llm_http_hook)`。`ExecutorHandle(crew_executor, router=None)`。

**規模**: 設計時の受容は ≈1,200 行だったが、**実装は src ≈1,300 行・tests ≈2,000 行**（合計 ≈3,300）になった。差の主因は (a) 12 道具を 4 ファイルへ分けたこと、(b) 表駆動の検証を 9 種そろえたこと（246 件）、(c) `.env`・設定・道具の 3 系統それぞれに起動時検査と負の検証を置いたこと。`service.py` が 500 行・`models.py` が 400 行を超えたら分割する（憲章 §3 の目安）。

### 1-a. 採用版と実測メモ（実装セッション 2026-08-25／2026-08-26）

**採用版は crewai 1.15.17**（宣言は `crewai>=1.15.17,<1.16` ＝マイナー上限。翻訳対象が版依存の
実クラス名を握るため、版を上げる作業に宣言の編集を挟ませて見直しを強制する＝ SD-01）。

- **Z-4／Z-6 とも成立**（同期 2 本が抽象・非同期 2 本の既定が未実装例外・引数名は 4 本とも `message`／上限停止は `except Exception` を素通り）。
- **`emit` は `type(event)` の厳密一致**で MRO を辿らない。`__all__` 108 件・`BaseEvent` 派生 103 件で、**fresh import 直後の `dir()` には 1 件も出ない**（遅延読込＝ SD-18）。ハンドラは別スレッドなので**ロック＋ `flush()`** が要る（SD-19）。
- **`@on(point, agents=…)`** は `_registered_hook` を生やし元の関数を返す。**同じ関数を 2 回装飾すると 1 個目が解除不能**（SD-20）。**差し込み点は列挙で渡す**（`InterceptionPoint[ref.points]`＝ SD-23）。
- **`BaseTool` は `arbitrary_types_allowed=True`**・`run` は `_run` を `try` で囲まない（＝戻り値の文言は作法ではなく必須＝ SD-21）。
- **`import crewai` が `.env` を `os.environ` へ流し込む**（`crewai/llm.py` の `load_dotenv()`。逃げ道なし）。秘密がプロセス全体に載り憲章 §5 が崩れ、`BEADS_DIR` が載ると board の `bd init` が失敗する（実測 33 件）。**executor が import 直後に取り除く**（SD-26）。
- **`Agent.skills`／`mcps` は渡さない**（`skills` の `str` は探索パス・`mcps` は crewai 自身の設定型＝ SD-22）。`CrewOutput` の本文は **`raw`**（SD-24）。鍵の空文字は crewai 側で `None` に正規化される（SD-25）。

## 2. Protocol 実装と利用の対応

| Protocol.メソッド | if_id | fn | 要点 |
|---|---|---|---|
| CrewExecutor.run | IF-48 | F01（＋F02〜F11） | 1 kickoff の実行と翻訳 |
| （利用）LLMHttpHook.on_outbound／on_inbound／aon_* | IF-40〜43 | F02 | BaseInterceptor アダプタで委譲（構築時注入） |
| （利用）Authorizer.enforce | IF-29 | F05/F09 | tool:／vault:／cmd: の 3 系統（P5D-029） |
| （利用）CommandRegistry.resolve_command | IF-30 | F09 | 固定 argv |
| （利用）VaultReader.list_inputs／read_input／read_outcome_file | IF-33/34/35 | F06 | vault 系 Tool |
| （利用）OutcomeWriter.write_outcome | IF-37 | F06 | write_outcome Tool |
| （利用）BoardReader.show／BoardWriter.comment | IF-02/17 | F07 | read_issue／record_progress |

executor が持たないもの: 段テンプレート・条文の組立（stages が tasks.yaml を充填）、**装備の宣言**（agents.yaml／tasks.yaml＝settings 所有。executor は ToolName で解決するだけ D16-a/b）、提案の検証（stages。Tool は形だけ）、起票・依存張り・close・質問起票・差し戻し判断（orchestrator）、上限・等級・写像（monitor）、権限規則・コマンド定義（settings）、LLM プロファイルの選択（settings → Member）。

## 3. 受け渡し型（common/interfaces/executor.py）

`CrewSpec`（issue_id・mode・purpose・bundle: PromptBundle・member: Member・hooks: HooksConfig・folders: ProjectFolders・parent_acceptance）／`CrewOutcome`（proposal・task_output・completed・completion_reason・review・escalation・written・events・halted・halted_reason・progress_notes）／`CrewEvent`（kind・at・detail）／`CrewEventKind`（12 種。**追記**）／`Escalation`・`ReviewVerdict`（**追記**）／例外 `ExecutorFailure(ViolationError)`（phase・reason。**追記**）。p4 notes §2 の 3 件に 4 件追記（フェーズ末改訂。P5D-033）。

## 4. シーケンス図

`p5_executor_sequence.mmd`（主図のみ）: run → .env → LLM 構築（Hook 装着）→ Agent/Task/Crew → Tool 集合 → hooks → kickoff → loop（Hook 経由の LLM 往復／RunHalted／Tool 呼び出し: enforce → vault／board／run_command／申告）→ 翻訳 → CrewOutcome／ExecutorFailure。レーン: `CrewExecutor`（呼び手）／`executor/service.py`・`executor/tools/`・`executor/lib:crewai`／`.env`・`LLM API`／`LLMHttpHook`・`Authorizer／CommandRegistry`・`VaultReader／OutcomeWriter`・`BoardReader／BoardWriter`（利用先）。

## 5. PoC からの継承と変更（証跡: renkei・casbin・ai_control）

| 項目 | PoC | V4 |
|---|---|---|
| LLM 構築 | `build_llm(settings, model)`（.env 直書き） | LlmFactory（LLMProfile＋provider_kind＋api_key_ref。P5D-030） |
| Interceptor | probe の `RecordingInterceptor`（BaseInterceptor 直実装） | executor 内のアダプタが monitor の `LLMHttpHook` に委譲（D09-b） |
| Tools | renkei 11＋casbin `RunCommandTool`（ALLOWED 定数） | 12 Tool（kernel ToolName 列挙 D16-b）、GuardedTool 基底、装備は agents.yaml／tasks.yaml（D16-a）、run_command は CommandRegistry（設定対象 D07-e） |
| 権限判定 | casbin `authorize` in Tool＋`casbin_guard` hook | GuardedTool 基底のみ（P5D-032） |
| 申告 Tool | `IssueSession` に積む（complete／escalate／review／propose） | RunSession（同分担。D09-f） |
| outcome 基点 | `outcome/<親ID>/`＋兄弟 `extra_outcome_ids` | PJ フォルダ/outcome/ フラット（D06-a）。B2 は outcome 領域の一覧 |
| イベント | probe `ProbeListener`（全 BaseEvent） | allowlist を中立型 CrewEvent に翻訳（P5D-033） |
| レビュー | `build_review_crew`（submit_review のみ） | purpose=review の run（同じ run、Tool は submit_review のみ） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py executor --render` → ERRORS: none
- participant は Protocol 名 5 群・内部 3（service／tools/／lib:crewai）・外部 2（.env・LLM API）のみ
- 提供 1 行・利用 12 行の if_id が feature_list に現れる

## 7. 未決（executor 固有）

**6 件はいずれも `008-executor` の実装セッション（2026-08-25／26）で決着した。** 経緯は
`specs/008-executor/design-sync.md` の SD-01〜SD-26 が正本。

1. **openrouter 経路の Hook 装着**（P5D-013/030 ▲）→ **解消**（実体・接続先の既定・保持を実測＝ SD-02。代替は不要）。
2. **MCP（`Agent.mcps`）の接続** → **M4 のまま**。ただし「渡せるか」は決着 —— 型が `Member.mcps` と合わず **M1 では渡さない**（SD-22）。
3. **review に vault 読取 Tool を渡すか**（P5D-031）→ **設定へ移管済み**（D16-a・P5D-048）。executor は対応表だけを持つ。
4. **event_allowlist の確定** → **実クラス名 10 件で確定**（`contracts/event-translation.md` §2）。`agent retry`・`summarization` は発生源が無いので載せない。版は `SKILL.md` ⑤ に固定。
5. **hook_catalog の中身** → **空で確定**（M1）。権限判定は hook に依らない（P5D-032）。
6. **役割の絞り**（D22・vault の FR-026a・VD-23）→ **裁定済みなので本文へ移した**（下記 7-a）。

### 7-a. 役割の絞り（裁定済み。未決ではない）

**vault は役割どうしを分離しない。** 契約 `VaultReader.read_input(folders, ref)` は `role` を取らず
`FileRef` も役割を持たないため、`area=role` の領域根は**宣言された役割フォルダの和集合**になる。
executor が `folders.model_copy(update={"role_dirs": {当該 persona: …}})` で絞って渡さないと、
**AI が他役割の手引きを読める**。絞りは executor の Tool 層の責務で、vault 側の実装変更では塞げない
（`packages/features/vault/SKILL.md` 規約 3）。**絞りは保管庫に触れる 5 道具すべてで、呼び出しの
たびに通す**（書込も含む＝絞りの経路を 1 本にするため）。役割が宣言に無ければ `role_dirs={}` を
渡し、保管庫が返す理由を文言にする（**空の一覧に倒さない**）。
