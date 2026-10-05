"""2 契約 4 メソッドの実装（IF-44〜47）。stages-F01・F02・F04〜F07。

`StageCatalog` と `ProposalGate` を **1 クラス**で実装する（FR-005。`build()` が同一
インスタンスを両方の口に載せる）。4 メソッドはいずれも**純関数**であり、ファイル・外部
ストア・外部プロセス・ネットワークに一切触れない（FR-010）。同じ入力に常に同じ結果を
返す（FR-011）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.interfaces.stages import (
    GateResult,
    PromptBundle,
    PromptPurpose,
    UnknownPersona,
    UnknownStage,
)
from common.kernel.enums import KindLabel, LabelDim, Labels, PersonaKey, StageName
from stages.models import (
    CHECKBOX_RE,
    KIND_TO_STAGE,
    OUTCOME_PREFIX,
    OUTCOME_TOKEN_RE,
    PERSONA_GROUP,
    PERSONA_GUIDE_HEADER,
    REPO_PREFIX,
    REPO_TOKEN_RE,
    outcome_filename,
    render,
    repo_path,
)

if TYPE_CHECKING:
    from common.interfaces.settings import Member, Rules, TaskTemplate, TaskTemplates
    from common.interfaces.stages import ContextRefs, Proposal, ProposalItem, StageSpec
    from stages.models import AcceptanceRules, StageRow


@dataclass(frozen=True, slots=True)
class StagesService:
    """段の解決・プロンプト組立・提案の検査・完了トークン抽出（IF-44〜47）。

    組み立て時に確定した段表と閾値を抱えるだけの不変オブジェクトで、状態遷移を持たない。
    """

    rows: tuple[StageRow, ...]
    rules: AcceptanceRules

    def resolve_stage(self, kind_labels: set[str], has_parent: bool) -> StageSpec:
        """`kind` ラベルの組と親の有無から段を決める（T016 で実装）。

        Args:
            kind_labels: issue が持つラベル（接頭辞込みの文字列）。
            has_parent: 親 issue があるか。

        Returns:
            決まった段の構造属性。

        Raises:
            UnknownStage: 段を決められない。
        """
        kinds = _kind_values(kind_labels)
        if len(kinds) >= 2:
            msg = f"kind ラベルが 2 つ以上ある: {sorted(kinds)}"
            raise UnknownStage(msg)
        if len(kinds) == 1:
            self._check_declared_kind(next(iter(kinds)), has_parent)
        for row in self.rows:
            if row.spec.require_labels <= kind_labels and _parent_ok(
                row.spec.require_no_parent, has_parent
            ):
                return row.spec
        msg = (
            f"段表のどの段にも当てはまらない: labels={sorted(kind_labels)} has_parent={has_parent}"
        )
        raise UnknownStage(msg)

    def _check_declared_kind(self, value: str, has_parent: bool) -> None:
        """段表照合の**前**に、名指しされた `kind` 値の妥当性と親の要求を検査する（FR-019）。

        この事前検査が無いと、`plain` が受け皿である以上どんな入力も段表照合を通ってしまい
        `UnknownStage` が 1 度も発火しない（SD-41）。

        Raises:
            UnknownStage: 段表が参照しない値／その段の親の要求を満たさない。
        """
        try:
            kind = KindLabel(value)
        except ValueError:
            msg = f"段表が参照しない kind 値: {value!r}"
            raise UnknownStage(msg) from None
        stage_name = KIND_TO_STAGE.get(kind)
        if stage_name is None:
            msg = f"段表が参照しない kind 値: {value!r}"
            raise UnknownStage(msg)
        row = self._row(stage_name)
        if not _parent_ok(row.spec.require_no_parent, has_parent):
            expected = "親なし" if row.spec.require_no_parent else "親あり"
            msg = f"{stage_name} 段は {expected} を要求する: has_parent={has_parent}"
            raise UnknownStage(msg)

    def _row(self, name: StageName) -> StageRow:
        """段名から段表の行を引く。

        Raises:
            UnknownStage: 段表にその段が無い（設定で段表を壊さない限り起きない）。
        """
        for row in self.rows:
            if row.spec.name == name:
                return row
        msg = f"段表に {name} が無い"
        raise UnknownStage(msg)

    def build_prompt(
        self,
        spec: StageSpec,
        member: Member,
        rules: Rules,
        templates: TaskTemplates,
        ctx: ContextRefs,
        feedback: str | None,
    ) -> PromptBundle:
        """段テンプレートとメンバ本文からプロンプトを組む（T025 で実装）。

        Args:
            spec: 段の構造属性。
            member: 作業するメンバ 1 人分。
            rules: プロジェクト共通ルール。
            templates: 段テンプレート一式。
            ctx: 充填の材料。
            feedback: 差し戻しの理由（無ければ `None`）。

        Returns:
            Agent ＋ Task の材料。

        Raises:
            UnknownStage: テンプレートキーが無い。
            UnknownPersona: 段が許すペルソナに当てはまらない。
        """
        template = self._template_for(spec, templates, ctx)
        self._check_stage_group(spec, member, ctx)
        values = _values(spec, member, ctx, feedback)
        return PromptBundle(
            persona_key=member.persona_key,
            role=member.role,
            goal=member.goal,
            backstory=_compose_backstory(member.backstory, rules.project_rules),
            task_description=render(template.description, values),
            expected_output=render(template.expected_output, values),
            task_tools=template.tools,
            purpose=ctx.purpose,
            proposal_schema="Proposal" if spec.mode == "propose" else None,
        )

    def _template_for(
        self, spec: StageSpec, templates: TaskTemplates, ctx: ContextRefs
    ) -> TaskTemplate:
        """用途と段からテンプレートを引く（FR-023・FR-029）。

        **PoC の「キーが無ければ黙って素のテンプレートへ落ちる」（`crew.py:444`）は
        継承しない**（SD-49 (j)）。落とすと、テンプレートが 1 つ欠けたまま別段の本文で
        AI が走る。

        Raises:
            UnknownStage: そのキーのテンプレートが無い。
        """
        if ctx.purpose == PromptPurpose.work:
            key = str(spec.name)
        else:
            key = "review_final" if ctx.is_final_review else "review_normal"
        template = templates.templates.get(key)
        if template is None:
            msg = f"段テンプレートに {key!r} が無い（世代 {templates.generation}）"
            raise UnknownStage(msg)
        return template

    def _check_stage_group(self, spec: StageSpec, member: Member, ctx: ContextRefs) -> None:
        """段所属を照合する（FR-028）。**`purpose == work` のときだけ掛ける**（SD-50）。

        レビューの担当は `orchestrator.review_persona`（既定 `planner`＝分解側）で、対象は
        実行段（`mode=execute`）である。用途を問わず掛けると**すべてのレビューが落ちる**。

        照合先が `spec.allowed_personas` ではない点にも注意する。分解の 3 段はいずれも、
        作業するメンバ自身がその集合に入らない（F-14）ため、そちらで照合すると分解が全滅する。

        Raises:
            UnknownPersona: 段の別と担当の段所属が食い違う／段所属表に無い担当。
        """
        if ctx.purpose != PromptPurpose.work:
            return
        group = PERSONA_GROUP.get(member.persona_key)
        if group is None:
            msg = f"段所属表に無い担当: {member.persona_key!r}"
            raise UnknownPersona(msg)
        if group != spec.mode:
            msg = (
                f"{spec.name} 段（{spec.mode}）に {member.persona_key}（{group}）は割り当てられない"
            )
            raise UnknownPersona(msg)

    def validate_proposal(
        self, proposal: Proposal, spec: StageSpec, existing_children: list[str]
    ) -> GateResult:
        """提案を段の規則に照らして検査する（T031 で実装）。

        Args:
            proposal: 分解提案一式。
            spec: 段の構造属性。
            existing_children: 既存の子の題名。

        Returns:
            合否・指摘・観測・起票順。**例外を 1 つも送出しない**（FR-032）。
        """
        items = proposal.items
        problems: list[str] = []
        if len(items) < spec.min_items:
            problems.append(
                f"提案が{len(items)}件です。{spec.min_items}件以上に分解してください。"
                "段の最小件数は設定で変えられます。"
            )
            # **早期 return しない**（SD-49 (b)）。PoC は打ち切るが、1 度に全部返せないと
            # 差し戻しが往復する。
        for index, item in enumerate(items, start=1):
            problems.extend(self._item_problems(index, item, spec))
        depends_problems = _depends_problems(items)
        problems.extend(depends_problems)
        problems.extend(_collision_problems(items, existing_children))
        observations = ["single_child"] if len(items) == 1 and spec.min_items == 1 else []
        order = [] if depends_problems else _topo_order(items)
        return GateResult(
            ok=not problems, problems=problems, observations=observations, order=order
        )

    def _item_problems(self, index: int, item: ProposalItem, spec: StageSpec) -> list[str]:
        """1 件分の検査（contracts/proposal-gate.md §1 の 2〜6）。

        **1 つ見つけて打ち切らない**（SD-49 (h)）。PoC の `lint_issue` は種類ごとに最初の
        1 件だけを報告するが（`acceptance.py:63-95`）、その打ち切りは継承しない。

        Args:
            index: 1 始まりの通し番号（指摘文に載せる）。
            item: 提案の 1 件。
            spec: 段の構造属性。

        Returns:
            この件に対する指摘（無ければ空リスト）。
        """
        problems: list[str] = []
        for field, value in (
            ("title", item.title),
            ("summary", item.summary),
            ("description", item.description),
        ):
            if self.rules.is_placeholder(value):
                problems.append(
                    f"{index}件目 の {field}: プレースホルダまたは空です（{value!r}）。"
                    "具体的な内容を書いてください。"
                )
        if item.persona is not None and item.persona not in spec.allowed_personas:
            problems.append(
                f"{index}件目 の persona: {item.persona} は割り当てられません。"
                f"使えるのは {_format_personas(spec.allowed_personas)} です。"
            )
        if not spec.allow_depends_on and item.depends_on:
            problems.append(
                f"{index}件目 の depends_on: この段階では兄弟間の依存を宣言できません。"
            )
        problems.extend(self._acceptance_problems(index, item, spec))
        return problems

    def _acceptance_problems(self, index: int, item: ProposalItem, spec: StageSpec) -> list[str]:
        """受入条文の検査（stages-F06。contracts/proposal-gate.md §2 の a〜e ＋ §1 の 6）。

        条文が**空**のときだけ、その 1 件の条文由来の検査（形式・成果物トークン）を打ち切る
        （PoC `backlog.py:159-164` を継承）。それ以外は最後まで走らせる。

        Args:
            index: 1 始まりの通し番号。
            item: 提案の 1 件。
            spec: 段の構造属性。

        Returns:
            この件の条文に対する指摘。
        """
        if not item.acceptance:
            return [
                f"{index}件目 の acceptance: 受入条件が空です。"
                "少なくとも 1 件、確認できる形で書いてください。"
            ]
        problems: list[str] = []
        if spec.max_acceptance is not None and len(item.acceptance) > spec.max_acceptance:
            problems.append(
                f"{index}件目 の acceptance: 受入条件が{len(item.acceptance)}件あります。"
                f"{spec.max_acceptance}件以内にまとめてください。"
            )
        for line in item.acceptance:
            if self.rules.is_placeholder(line):
                problems.append(
                    f"{index}件目 の acceptance: プレースホルダまたは空の条件があります"
                    f"（{line!r}）。"
                )
            elif self.rules.is_too_short(line):
                # 穴埋めで指摘した条文は最小長を見ない（**排他**。`acceptance.py:172-183`）。
                problems.append(
                    f"{index}件目 の acceptance: 条件が短すぎます"
                    f"（{self.rules.min_len}文字以上。{line!r}）。"
                )
            if not CHECKBOX_RE.match(line):
                problems.append(
                    f"{index}件目 の acceptance: 1 行 1 条件の形（- [ ] …）ではありません"
                    f"（{line!r}）。"
                )
        problems.extend(self._outcome_problems(index, item, spec))
        return problems

    def _outcome_problems(self, index: int, item: ProposalItem, spec: StageSpec) -> list[str]:
        """成果物トークンの検査（contracts/proposal-gate.md §1 の 6・§3）。

        全条文を改行で連ねて抽出し、正準形でないものを**原文つき**で指摘する。段が成果物
        パスを必須にする場合、トークンが 1 つも無ければ指摘する。**正準形でないトークンでも
        「有る」側に数える**（形式違反は別途指摘済みであり、二重に責める意味がない）。

        Args:
            index: 1 始まりの通し番号。
            item: 提案の 1 件。
            spec: 段の構造属性。

        Returns:
            成果物トークンに対する指摘。
        """
        problems: list[str] = []
        joined = "\n".join(item.acceptance)
        tokens = self.extract_outcome_tokens(joined)
        for token in tokens:
            if outcome_filename(token) is None:
                problems.append(
                    f"{index}件目 の acceptance: 成果物のパスが {OUTCOME_PREFIX}<ファイル名> の"
                    f"形ではありません（{token!r}）。"
                )
        repo_tokens = self.extract_repo_tokens(joined)
        for token in repo_tokens:
            if repo_path(token) is None:
                problems.append(
                    f"{index}件目 の acceptance: リポジトリの成果物が {REPO_PREFIX}<リポジトリ"
                    f"相対のパス> の形ではありません（{token!r}。.. と .git は使えません）。"
                )
        # D25-b: リポジトリの成果物（`repo/<パス>`）も成果物の宣言に数える。指摘の文言は
        # リポジトリ未登録の PJ に `repo/` を勧めないよう、従来のまま `outcome/` だけを案内する。
        if spec.require_outcome_path and not tokens and not repo_tokens:
            problems.append(
                f"{index}件目 の acceptance: 成果物のパスが 1 つも書かれていません。"
                f"{OUTCOME_PREFIX}<ファイル名> の形で書いてください。"
            )
        return problems

    def extract_outcome_tokens(self, acceptance: str) -> list[str]:
        """受入条文から完了ゲート鎖の入力となるトークンを抜く（T020 で実装）。

        Args:
            acceptance: 受入条文（複数行でよい）。

        Returns:
            出現順・重複除去済みのトークン列（**原文のまま**）。**例外を送出しない**（FR-050）。
            `repo/` で始まるトークン（`repo/docs/outcome/x.md` のようにリポジトリの成果物の
            途中に `outcome/` を含むもの）は除く —— それはリポジトリの成果物である（D25-b）。
        """
        return [
            token
            for token in dict.fromkeys(OUTCOME_TOKEN_RE.findall(acceptance))
            if not token.startswith(REPO_PREFIX)
        ]

    def extract_repo_tokens(self, acceptance: str) -> list[str]:
        """受入条文からリポジトリの成果物（`repo/<パス>`）のトークンを抜く（D25-b）。

        Args:
            acceptance: 受入条文（複数行でよい）。

        Returns:
            出現順・重複除去済みのトークン列（**原文のまま**）。**例外を送出しない**。
        """
        return list(dict.fromkeys(REPO_TOKEN_RE.findall(acceptance)))


def _kind_values(kind_labels: set[str]) -> set[str]:
    """ラベル集合から `kind` 次元の値だけを取り出す。

    分解できない文字列・他次元（`persona:`・`state:`・`note:`・`role:`・`project:`）は
    **黙って捨てる**。`Labels.parse` は例外を送出しない（`enums.py` の現物）。

    Args:
        kind_labels: issue が持つラベル（接頭辞込みの文字列）。

    Returns:
        `kind` 次元の値の集合（`"kind:"` は空文字として入る）。
    """
    values: set[str] = set()
    for label in kind_labels:
        parsed = Labels.parse(label)
        if parsed is not None and parsed[0] is LabelDim.kind:
            values.add(parsed[1])
    return values


def _parent_ok(require_no_parent: bool | None, has_parent: bool) -> bool:
    """親の要求を満たすか（`True`＝親があってはならない／`False`＝親が無ければならない）。

    Args:
        require_no_parent: 段が課す親の要求（`None` は問わない）。
        has_parent: 親 issue があるか。

    Returns:
        要求を満たすなら真。
    """
    if require_no_parent is None:
        return True
    return has_parent is not require_no_parent


def _compose_backstory(member_backstory: str, project_rules: str) -> str:
    """メンバの背景とプロジェクト共通ルールを**空行 1 つ**で連ねる（FR-021）。

    どちらかが空文字ならもう一方だけを返し、両方空なら空文字を返す。**区切りに見出しを
    入れない**（見出しは本文であり FR-013 の禁止側に寄る）。**共通 backstory は持たない**
    （出荷時既定の 9 ペルソナすべてに畳み込み済み＝ DIFF-06・SD-43）。

    Args:
        member_backstory: メンバの背景。
        project_rules: プロジェクト共通ルール。

    Returns:
        合成した背景。
    """
    return "\n\n".join(part for part in (member_backstory, project_rules) if part)


def _persona_guide(allowed_personas: set[PersonaKey]) -> str:
    """担当一覧を生成する（FN-46・SD-51）。

    見出しに続けて `- <persona_key>` を **`PersonaKey` の列挙定義順**で並べる（集合を受けても
    決定的になる）。**役割・目標は載せない** —— stages はペルソナ本文を持たず、引数で受け取る
    のは作業するメンバ 1 人分だけである。許す担当が無ければ見出しだけを返す。

    Args:
        allowed_personas: その段が起票する子に許す担当。

    Returns:
        担当一覧の本文。
    """
    lines = [PERSONA_GUIDE_HEADER]
    lines.extend(f"- {key}" for key in PersonaKey if key in allowed_personas)
    return "\n".join(lines)


def _values(
    spec: StageSpec, member: Member, ctx: ContextRefs, feedback: str | None
) -> dict[str, str]:
    """`{変数}` の値表を組む（contracts/prompt-rendering.md §4 の 17 種）。

    **「(なし)」等の日本語の既定文言を持たない**（FR-013）。空の材料は**空文字**で埋め、
    文言を添えるのは呼び手の役目である（SD-53）。`repository_*` の 2 変数（D24）も同じ
    規則で、リポジトリ未登録のプロジェクトでは空文字になる。

    Args:
        spec: 段の構造属性。
        member: 作業するメンバ。
        ctx: 充填の材料。
        feedback: 差し戻しの理由。

    Returns:
        変数名 → 充填値。
    """
    return {
        "issue_id": ctx.issue_id,
        "title": ctx.title,
        "description": ctx.description,
        "acceptance": ctx.acceptance,
        "parent_title": ctx.parent_title or "",
        "parent_acceptance": ctx.parent_acceptance or "",
        "persona_guide": _persona_guide(spec.allowed_personas),
        "human_guidance": "\n".join(ctx.human_guidance),
        "feedback": feedback or "",
        # 供給元が未定（P5D-027 が「スロットだけ用意し、本文は M1」と定めたまま）＝ B-24。
        "input_rules": "",
        "input_refs": "\n".join(ctx.input_refs),
        "existing_children": "\n".join(ctx.existing_children),
        "project_key": member.project_key,
        "persona_key": str(member.persona_key),
        "outcome_dir": OUTCOME_PREFIX.rstrip("/"),
        "repository_remote_url": ctx.repository_remote_url or "",
        "repository_local_path": ctx.repository_local_path or "",
    }


def _format_personas(allowed_personas: set[PersonaKey]) -> str:
    """指摘文に載せる担当の列挙（`PersonaKey` の定義順で決定的にする）。

    Args:
        allowed_personas: その段が子に許す担当。

    Returns:
        読点で連ねた担当名（1 つも無ければその旨）。
    """
    names = [str(key) for key in PersonaKey if key in allowed_personas]
    return "、".join(names) if names else "（この段には割り当てられる担当がありません）"


def _depends_problems(items: list[ProposalItem]) -> list[str]:
    """依存の整合を検査する（contracts/proposal-gate.md §4。PoC `backlog.py:207-233` を継承）。

    各依存について 重複 → 自己参照 → 範囲外 → 後方参照 の順に**排他で 1 つだけ**指摘する。
    重複だけは指摘したうえで次の依存へ進む（同じ値の 2 度目以降を数えない）。

    前方参照だけを許すため**循環は構造的に起こらない**が、将来の緩和で黙って壊れないよう
    後方参照の指摘は残す（`backlog.py:210-212` の設計意図）。

    Args:
        items: 提案の全件。

    Returns:
        依存に対する指摘（件の昇順・依存の宣言順）。
    """
    problems: list[str] = []
    for index, item in enumerate(items, start=1):
        seen: set[int] = set()
        for dep in item.depends_on:
            if dep in seen:
                problems.append(f"{index}件目 の depends_on: {dep} が重複しています。")
                continue
            seen.add(dep)
            if dep == index - 1:
                problems.append(f"{index}件目 の depends_on: 自己参照はできません（{dep}）。")
            elif not 0 <= dep < len(items):
                problems.append(
                    f"{index}件目 の depends_on: {dep} は提案の範囲外です"
                    f"（0 以上 {len(items)} 未満）。"
                )
            elif dep >= index - 1:
                problems.append(
                    f"{index}件目 の depends_on: {dep} は後方参照です。"
                    "依存は自分より前の項目にだけ宣言できます。"
                )
    return problems


def _topo_order(items: list[ProposalItem]) -> list[int]:
    """起票順を層状の topo 順で決める（PoC `backlog.py:339-357` を継承）。

    「依存がすべて置き終わった項目」をその周回で**添字の昇順にまとめて追加**する。
    **同順位は元の添字順で安定**する。循環時に PoC は `ValueError` を送出するが、本 feature は
    例外を送出できない（FR-032）ので、**呼び手が依存の指摘の有無で `order = []` に倒す**。

    Args:
        items: 提案の全件（依存に指摘が無いことを前提とする）。

    Returns:
        起票順の添字列。
    """
    placed: set[int] = set()
    order: list[int] = []
    while len(order) < len(items):
        layer = [
            index
            for index, item in enumerate(items)
            if index not in placed and all(dep in placed for dep in item.depends_on)
        ]
        if not layer:
            # 依存に指摘が無ければ到達しない（前方参照だけが残るため）。無限周回を避ける。
            break
        order.extend(layer)
        placed.update(layer)
    return order


def _collision_problems(items: list[ProposalItem], existing_children: list[str]) -> list[str]:
    """兄弟衝突を検査する（contracts/proposal-gate.md §5。FR-043・FR-044）。

    見るのは 3 つ —— 提案内の**題名**の完全一致（**新規**。SD-49 (d)）・提案内の**成果物
    ファイル名**（PoC `backlog.py:236-263` を継承）・**既存の子の題名**との一致（**新規**。
    SD-49 (e)）である。

    成果物のキーは宣言された文字列ではなく `outcome_filename()` の結果にする（別のフォルダに
    見える書き方を別物と見て素通りさせないため）。**正準形でないトークンは飛ばす** ——
    形式違反として既に指摘済みであり、原文のまま突き合わせても意味がない。

    見るのは**提案どうし**の重なりである（FR-043）。1 件が同じ成果物を 2 行の条文に書いても
    衝突ではないので、**件ごとに出現順で重複除去してから**所有者表へ登録する
    （`backlog.py:252` の `dict.fromkeys` を継承）。除去しないと自分自身との重複を指摘して
    `ok=False` になる。

    **既存の子と照合できるのは題名だけ**である。`ContextRefs.existing_children` は兄弟の
    title であり、既存の子の受入条文は渡らない。

    Args:
        items: 提案の全件。
        existing_children: 既存の子の題名。

    Returns:
        衝突に対する指摘（件の昇順）。
    """
    problems: list[str] = []
    existing = set(existing_children)
    seen_titles: dict[str, int] = {}
    seen_outcomes: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        first = seen_titles.get(item.title)
        if first is not None:
            problems.append(
                f"{index}件目 の title: {first}件目 と題名が重複しています（{item.title!r}）。"
            )
        else:
            seen_titles[item.title] = index
        if item.title in existing:
            problems.append(
                f"{index}件目 の title: 既存の子と題名が重複しています（{item.title!r}）。"
            )
        for token in dict.fromkeys(OUTCOME_TOKEN_RE.findall("\n".join(item.acceptance))):
            name = outcome_filename(token)
            if name is None:
                continue
            owner = seen_outcomes.get(name)
            if owner is not None:
                problems.append(
                    f"{index}件目 の acceptance: 成果物 {name} が {owner}件目 と重複しています。"
                )
            else:
                seen_outcomes[name] = index
    return problems
