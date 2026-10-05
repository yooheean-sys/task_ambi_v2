# SKILL — stages（段の解決・プロンプト組立・提案ゲート）

## 1. 責務

stages が持つのは **4 つだけ**である（IF-44〜47）。

| メソッド | すること |
|---|---|
| `resolve_stage(kind_labels, has_parent)` | ラベルの組と親の有無から段を 1 つ決める |
| `build_prompt(spec, member, rules, templates, ctx, feedback)` | 段テンプレートとメンバ本文からプロンプトの材料を組む |
| `validate_proposal(proposal, spec, existing_children)` | 分解提案を段の規則に照らして検査し、**指摘の一覧**を返す |
| `extract_outcome_tokens(acceptance)` | 受入条文から成果物の指し名を**原文のまま**抜く（`repo/` 始まりは除く） |
| `extract_repo_tokens(acceptance)` | 受入条文からリポジトリの成果物（`repo/<パス>`）を**原文のまま**抜く（D25-b）。`require_outcome_path` の段では outcome と同列に「成果物の宣言」と数える。兄弟間の重複検査は当てない |

**所有するのは構造であって本文ではない**（D16-g）。段表・段所属・`{変数}` の許可集合・受入条文の
規則はこのモジュールが持つが、**プロンプト本文もペルソナ本文も 1 文字も持たない** —— 前者は
`settings/defaults/tasks.yaml`、後者は `agents.yaml` が所有し、stages は引数で受け取るだけである。
唯一の例外は担当一覧の見出し 1 行（→ §6）。

stages は**外部ストアを持たず、他モジュールの契約も使わない**（`StagesDeps` はフィールド 0）。

## 2. 主要ファイル

| ファイル | 置くもの |
|---|---|
| `src/stages/__init__.py` | `build()`・`StagesDeps`・`StagesHandle`・`StagesConfig`・`StagesConfigError`・`config_path()` |
| `src/stages/service.py` | `StagesService`（4 メソッド ＋ 検査の private ヘルパ） |
| `src/stages/models.py` | コード定数（段表・段所属・`{変数}` 規則・受入条文の規則）と純関数 |

**置かないファイルとその理由**:

| 置かないもの | 理由 |
|---|---|
| `exceptions.py` | 契約例外 `UnknownStage`・`UnknownPersona` は `common/interfaces/stages.py` が正本。内部例外は `StagesConfigError` 1 つだけなので公開口に同居させる |
| `repository.py`・`models.py` の外部ストア用途 | 外部ストア・外部ツールを 1 つも持たない |
| `router.py`・`ui.py`・`schemas.py`・`dependencies.py` | HTTP 経路も UI ページも持たない（`StagesHandle.router` は `None`。`apps/ui` への追記も不要） |
| `prompts/` | 本文を所有しない。置くと本文の置き場が 2 つに割れる |

## 3. 規約 3 つ（最重要）

1. **I/O を持たない。** ファイルを読むのは `build()` の `config.json` **1 回だけ**である。4 メソッドは
   ファイル・外部ストア・外部プロセス・ネットワークに一切触れず、現在時刻も乱数も見ない（同じ入力に
   常に同じ結果）。`build()` 以降に設定を読み直さない。
2. **他モジュールの Protocol を使わない。** `StagesDeps` は空で、`requires` も空である。
   **他 feature を import しない**（`src` もテストも）。テストで出荷時既定の担当が要るときは
   `packages/features/settings/defaults/` を読まず、テスト内のリテラルとして持つ。
3. **例外でなく指摘で返す。** `validate_proposal` と `extract_outcome_tokens` は**例外を 1 つも
   送出しない**（壊れた提案でも `GateResult` を返す）。送出しうるのは `resolve_stage` の
   `UnknownStage` と `build_prompt` の `UnknownStage`／`UnknownPersona` だけで、いずれも
   **共有側にある型を import して送出する**（stages では定義しない）。

## 4. 段表（`models.STAGE_ROWS`）

**宣言順に走査し、最初に一致した 1 行を採る**。

| 段 | `kind_key` | `require_labels` | 親の要求 | 分解／実行 | 子に許す担当 | 依存 | 成果物パス |
|---|---|---|---|---|---|---|---|
| `program` | `program` | `kind:program` | 親なし | 分解 | `project_lead` | **不可** | 否 |
| `project` | `request` | `kind:request` | 問わない | 分解 | `system_engineer`・`doc_lead` | 可 | 否 |
| `subrequest` | `subrequest` | `kind:subrequest` | 問わない | 分解 | 実行側 5 種 | 可 | **必須** |
| `child` | `child` | **なし** | 親あり | 実行 | **空集合** | 可 | 否 |
| `plain` | — | なし | 問わない | 実行 | **空集合** | 可 | 否 |

- **段表照合の前に `kind` 集合を検査する**（2 つ以上／段表が参照しない値／親の要求違反 → `UnknownStage`）。
  `plain` が受け皿である以上、宣言順の照合だけでは `UnknownStage` が **1 度も発火しない**。
- **`kind_key` と `require_labels` を別に持つ**。`child` 段は `kind_key = child` だが `require_labels` は
  空である。導出すると「`kind` ラベルを持たず親がある issue」が `child` に落ちなくなる（逆向きに
  導出すると `kind:child` を持たない子が弾かれる）。
- 判定に使うのは**ラベルと親の有無だけ**。作業管理ツール側の種別は引数に無い。
- `allowed_personas` は「**その段が起票する子に**許す担当」であり、その段で作業するメンバを照合する
  集合ではない（分解 3 段はいずれも、担当自身がその集合に入らない）。

## 5. `{変数}` の扱い

- 許可は **15 種**（`ALLOWED_VARS`）、段別必須は **7 キー**（`REQUIRED_VARS`。キーは段名ではなく
  **テンプレートキー**で、`review_normal`／`review_final` を含む）。
- **正本は stages の `models.py` であり、`settings` の同名定数はその写しである**（D16-c）。
  乖離を機械的に捕まえる手段が無いので、**改訂は必ず 2 モジュール同時**に行う。
- 抽出（settings 側）と充填（stages 側）は **`string.Formatter` に揃える**。正規表現で書き直さない
  —— 別手段だと「settings の検査を通ったのに stages が埋められない」が沈黙で起きる。
- 許可集合に無い名前・`{0}`・`{}` は**空文字**で埋め、書式指定と変換は無視し、波括弧が壊れていれば
  **例外を投げずに**破れの手前までを返す。
- **「(なし)」等の日本語の既定文言を stages は持たない**。空の材料は空文字で埋め、文言を添えるのは
  呼び手（orchestrator）の役目である。

## 6. 担当一覧は動的生成する

`{persona_guide}` の値は `spec.allowed_personas` から **`PersonaKey` の列挙定義順**で毎回生成する
（`- <persona_key>` を並べるだけ。**役割・目標は載せない**）。**ペルソナ名の並びを、テンプレート本文にも
コード定数にも書き写さない**。見出し `割り当てられる担当（persona）:` の 1 行だけが、stages の持つ
**唯一のプロンプトに載る定型文**である。

## 7. 改修時の注意

1. **契約例外を stages に再定義しない**（`common/interfaces/stages.py` から import する）。
2. **`outcome_filename()` の複製を作らない**。`rsplit` や `split("outcome/")` で書き直すと、
   宣言と書き込み先の食い違いが場所ごとに変わる。**正準形に丸めない**（丸めると丸めたことが
   誰にも見えなくなる）。
3. **指摘文をテンプレート本文の代用にしない**。`GateResult.problems` は呼び手が `{feedback}` に載せて
   AI へ返すため本文に見えるが、あくまで検査の診断文である。ここに手順や様式を書き足さない。
4. **受入条文の判定は本文に対して行う**（`acceptance_body()` でチェックボックスを剥がしてから）。
   剥がさないと、`- [ ]` の形を課している以上、穴埋め検査が 1 度も発火しない。
5. **凍結 PoC（`pocs/`）は読取のみ**。テストから読まない（作業ツリーに中身が無い環境が在りうる）。
6. **テストファイルの基名は `test_stages_*`**。feature をまたいで重複すると `uv run pytest`（全体）が
   collection error で止まる（**モジュール単位の検証では再現しない**）。
7. **段所属の照合は `purpose == work` のときだけ**掛ける。レビューの担当は段ではなく
   `orchestrator.review_persona`（既定 `planner`＝分解側）が決めるため、用途を問わず掛けると
   **すべてのレビューが落ちる**。
8. `service.py` が 600 行を超えたら `src/stages/service/`（`__init__.py`・`catalog.py`・`gate.py`）へ
   分ける。分けても**指摘文と段表の実値は 1 箇所のまま**にする。

## 8. テストコマンド（**1 コマンドずつ**実行する）

```bash
uv run pytest packages/features/stages/tests
uv run ruff check packages/features/stages
uv run pyright packages/features/stages
```

`&&` で連ねると最初の失敗で後続が隠れる。stages は外部ツールを起動しないので、
`-m "not requires_bd"` を付けても付けなくても**読み飛ばしは 0 件**である。

## 9. 未実装（呼び手へ移した検査）

**coverage（親の受入条文が指定した成果物を子が尽くしているか）は stages に無い**。凍結契約
`validate_proposal(proposal, spec, existing_children)` に親の受入条文を渡す引数が無く、`StageSpec` にも
`Proposal` にも含まれないため、構造的に実装できない（`existing_children` は兄弟の**題名**である）。
検査の主体は呼び手（orchestrator）へ移した —— 呼び手は親の条文を持っているので、
`extract_outcome_tokens` を親と子の双方に掛けて突き合わせられる。
経緯は `specs/006-stages/design-sync.md` の **B-25**。
