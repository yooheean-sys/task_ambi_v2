# stages（BC-03 分解・遂行 — プロンプト組立・提案の形のゲート）詳細設計 説明（第5フェーズ。P5-R1 D16 改訂）

**位置付け**: `p5_stages_sequence.mmd`／`p5_stages_data_model.mmd`／`p5_stages_feature_list.csv` の短文の正本。裁定は D11（前提）と `../p5_decision_log.csv` P5D-025〜028。食い違えば D 記録・P5D が勝つ。
**入力**: `../../phase4/p4_module_contract.csv` IF-44〜47（提供 4 行）／p4 notes §1／`../../phase2/p2_domain_model.mmd` DM-08/09（＋DM-14 の書式）／`p2_sequence_UC-I1.mmd`／D06-e,f,l・D08 Q2・D09-f／`docs/initial_memo/付録：受け入れ条件の定義.md`／PoC `crewai_beads_renkei`（`Stage`・`resolve_stage`）・`personas.py`・`backlog.py`・`acceptance.py`・`crew.py`（`TASK_TEMPLATES`・`REVIEW_TASK_TEMPLATES`・`render_persona_guide`）（PoC 基準版、読取のみ）
**レジーム**: 改訂可。

## 0. 一言でいうと

stages は **I/O を持たない純粋 MOD**（D09-f。P5-R1 後も維持）。段表（DM-08 の構造属性）・ペルソナの段所属・{変数}規則をコード定数として持ち、**テンプレート本文（tasks.yaml→TaskTemplates）とペルソナ本文（agents.yaml→Member）は引数で受ける**（D16。P5D-028 を改訂）。`StageCatalog`（段の解決・プロンプト組立）と `ProposalGate`（提案の形の検証・outcome トークン抽出）を純関数で実装する。検証結果は例外でなく指摘リストで返す（P5D-028）。acceptance の書式は本 ver は PoC 書式のまま（P5D-025）、WS 粒度の規則はスロットだけ用意して本文は M1（P5D-027）。テストはスタブなしで完結。

## 1. ファイル構成（憲章 §3）

```
packages/features/stages/
├── pyproject.toml
├── config.json          # StagesConfig: min_items_override（段別。既定 空＝全段 1）・min_acceptance_len・placeholder_patterns・max_acceptance_default
├── SKILL.md             # 「I/O を持たない・他 Protocol を使わない・例外でなく指摘で返す」／テンプレートは引数（tasks.yaml が正本 D16）／{変数} 許可集合を変えたら settings validate と同時に（正本は kernel ではなく stages の Renderer・settings は D16-c の写し）／担当一覧は必ず {persona_guide} で動的生成（二重宣言禁止 P4）
├── src/stages/
│   ├── __init__.py      # build(deps: StagesDeps, *, config: StagesConfig | None = None) -> StagesHandle（stage_catalog／proposal_gate／router=None。両者は同一インスタンス）
│   ├── service.py       # StagesService 1 クラスに 4 メソッド（resolve_stage／build_prompt／validate_proposal／extract_outcome_tokens）。stage_catalog is proposal_gate＝同一インスタンス。純関数の束
│   ├── models.py        # StageTable・PersonaGroup・Renderer・AcceptanceRules（内部 VO。コード定数の器。テンプレート本文は持たない D16）
│   └── exceptions.py    # なし（契約例外 UnknownStage／UnknownPersona は common/interfaces/stages.py）→ 置かない
└── tests/               # 純関数のテスト。スタブ不要
```

置かないもの: `router.py`・`ui.py`・`schemas.py`・`repository.py`・`dependencies.py`・**`prompts/`**（テンプレート本文は settings 所有の tasks.yaml へ。出荷時既定は `settings/defaults/` D16-g）。

`build(deps: StagesDeps, *, config: StagesConfig | None = None) -> StagesHandle`: Deps 空。`config` はキーワード専用の省略可引数で、省略時に `config.json` を読む（テストが差し替える口。環境変数では上書きできない＝ FR-057 と両立）。読んだ設定で段表に min_items と max_acceptance を適用し、閾値検査（段名・1 未満）に落ちれば `StagesConfigError` で起動を中断する。

## 2. Protocol 実装の対応

| Protocol.メソッド | if_id | fn | 要点 |
|---|---|---|---|
| StageCatalog.resolve_stage | IF-44 | F01 | 段表を順に照合。bd type は見ない。矛盾は UnknownStage |
| StageCatalog.build_prompt | IF-45 | F02（＋F03） | Member（本文）＋rules 合成、TaskTemplate 選択（purpose work／review。templates 引数 D16-h）、{変数}充填、担当一覧の動的生成（{persona_guide}）。**段所属の照合は purpose=work のときだけ**（SD-50。review の担当は orchestrator.review_persona＝既定 planner で対象は実行段のため、掛けると全レビューが落ちる） |
| ProposalGate.validate_proposal | IF-46 | F04（＋F06） | 件数・persona・形式・正準形・depends_on・衝突。指摘で返す。**coverage（F07）は含まない** —— 契約に親 acceptance を渡す引数が無く、呼び手へ移した（B-25） |
| ProposalGate.extract_outcome_tokens | IF-47 | F05 | 正規表現＋outcome_filename() 1 箇所 |

stages が持たないもの: 盤面・vault・設定の読取（orchestrator が ContextRefs・Rules に詰めて渡す）、起票（orchestrator。GateResult.order を起票順に使う）、観測印 note:single-child の付与（orchestrator が observations を見て BoardWriter.set_labels）、LLM の呼び出し（executor）、完了ゲートの合否（orchestrator が extract_outcome_tokens → VaultReader.outcome_exists）、**coverage の突合**（orchestrator が親子の acceptance に extract_outcome_tokens を掛けて比べる。B-25）。

## 3. 受け渡し型（common/interfaces/stages.py）

`StageSpec`（name・mode・require_labels・require_no_parent・child_labels・allowed_personas・min_items・max_acceptance・allow_depends_on・require_outcome_path）／`PromptBundle`（persona_key・role・goal・backstory・task_description・expected_output・purpose・proposal_schema）／`Proposal`（items）／`ProposalItem`（title・summary・description・acceptance・depends_on・persona。**追記**）／`GateResult`（ok・problems・observations・order）／`ContextRefs`（issue_id・title・description・acceptance・parent_title・parent_acceptance・human_guidance・input_refs: list[str]・existing_children・purpose・is_final_review。**追記**）／`PromptPurpose`（work／review。**追記**）／例外 `UnknownStage`・`UnknownPersona`（NotFoundError）。p4 notes §2 の 4 件に 3 件を追記（フェーズ末改訂。P5D-026）。

## 4. シーケンス図

`p5_stages_sequence.mmd`（主図のみ）: resolve_stage → build_prompt →（executor 実行）→ validate_proposal → extract_outcome_tokens。レーン: `StageCatalog`・`ProposalGate`（呼び手）／`stages/service.py`／`config.json`（build 時のみ）。

## 5. PoC からの継承と変更（証跡: renkei）

| 項目 | PoC | V4 |
|---|---|---|
| 段の判定 | `config.py resolve_stage`（ラベル×親。HIERARCHIES flat/two/three） | 同方式。段は three 系の 5 段に固定（flat／two は廃止。V4 は 3 階層のみ） |
| MIN_ITEMS | グローバル定数 2（`backlog.py:26`）。不足時は**その 1 件だけ返して早期 return** | 段の属性・既定 1・config 上書き・single_child 観測（D06-f）。**早期 return は継承しない**（SD-49 (b)。1 度に全部返せないと差し戻しが往復する） |
| 担当一覧 | `render_persona_guide(allowed_personas)` 動的生成（`- <キー>: <役割>。<目標>`） | 同（FN-46）。ただし**キーのみを並べる**（SD-51。V4 の stages はペルソナ本文を持たず、引数で受けるのは作業するメンバ 1 人分だけ） |
| テンプレート | `TASK_TEMPLATES` 7 キー＋`REVIEW_TASK_TEMPLATES` | 段 5＋review 2 の**キー構成のみ継承**。本文は global/tasks.yaml（出荷時既定は settings/defaults/。D16-a/g）へ、{変数}規則（Renderer）はコード定数 |
| 提案の検証 | `validate_proposal`（`backlog.py:314`）＋`acceptance.py`。提案の条文は**素の文字列**で `- [ ]` は後段の render が付ける | 同規則（P5D-025）＋兄弟衝突を existing_children まで拡張＋topo 順を返す。V4 は提案の条文にも `- [ ]` の形を課すため、**穴埋め・最小長はチェックボックスを剥がした本文に対して判定する**（剥がさないと正しい形の条文では穴埋め検査が 1 度も発火しない＝ SD-41 と同型の穴） |
| outcome トークン | `_OUTCOME_TOKEN`／`outcome_filename()` | 継承（正準形の切り出しは 1 箇所） |
| rules 合成 | casbin PoC `loader.py`（rules_file＋roles/<役割>.md → backstory） | stages が Member.backstory（agents.yaml。役割ルールを吸収 D16-h）＋Rules.project_rules を合成 |
| 書き込み先の注記 | 「outcome/<親issueID>/ 直下」 | 「PJ フォルダ/outcome/ 直下」に文言変更（D06-a） |

## 6. 自己検証（D11-g）

- `python3 ../p5_selfcheck.py stages --render` → ERRORS: none
- participant は Protocol 名 2・内部 1・外部 1（config.json）のみ
- 提供 4 行の if_id と 4 メソッド全てに実装 fn

## 7. 未決（stages 固有）

1. **acceptance 書式の拡張**（4 要素・等級）: 実タスク投入後に再提起（P5D-025。作業メモ A-3 は「据え置き・解除条件＝実タスク投入」に書き換え）
2. **入力規則スロットの本文**（WS 粒度 D06-l）: M1 テンプレート設計
3. **出荷時既定の本文**（tasks.yaml 7 キー・agents.yaml 9 ペルソナ＝settings/defaults/）: M1 で PoC から移植・改訂（D16-g。stages の所有物ではなくなった）。{変数}名は本設計（D16-c）で固定
4. **人間の直接起票（FN-01/02）のラベル検査**: 起票ガイド（FN-06 プロンプト層）の範疇。resolve_stage は矛盾を UnknownStage で返すのみ
5. **coverage（FN-51）の実施主体**: 呼び手（orchestrator）へ移した（B-25）。契約 IF-46 を変えられる時点で stages へ戻すかは未裁定

**本 feature（`specs/006-stages`）で決着した項目**: `plain` の分解／実行の別＝**実行**（SD-44）／`child`・`plain` の `allowed_personas` ＝**空集合**（SD-45）／段所属の照合は `purpose=work` のみ（SD-50）／担当一覧はキーのみ（SD-51）。
