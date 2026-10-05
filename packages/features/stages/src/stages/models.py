"""stages が所有するコード定数（段表・段所属・`{変数}` 規則・受入条文の規則）。stages-F03。

**本文（プロンプト・ペルソナ）を 1 文字も持たない**（D16-g・FR-013）。ここに置くのは
「構造」だけであり、テンプレート本文は `settings/defaults/tasks.yaml`、ペルソナ本文は
`agents.yaml` が所有する。唯一の例外は `PERSONA_GUIDE_HEADER`（見出し 1 行）である
（Constitution Check 判断 4）。

正本: 段表＝spec.md P-01／段所属＝DM-09・P-06／`{変数}`＝D16-c（**本ファイルが正本**で
`settings/models.py` の `ALLOWED_VARS`／`REQUIRED_VARS` はその写し）／条文＝P5D-025・P-05。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from string import Formatter
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from common.interfaces.stages import StageSpec
from common.kernel.enums import KindLabel, Labels, PersonaKey, StageName

if TYPE_CHECKING:
    from collections.abc import Mapping

    from stages import StagesConfig


@dataclass(frozen=True, slots=True)
class StageRow:
    """段表の 1 行。`spec` は契約の受け渡し型そのもの、`kind_key` は事前検査用。"""

    spec: StageSpec
    kind_key: KindLabel | None


STAGE_ROWS: tuple[StageRow, ...] = (
    StageRow(
        spec=StageSpec(
            name=StageName.program,
            mode="propose",
            require_labels={Labels.kind(KindLabel.program)},
            require_no_parent=True,
            child_labels=[Labels.kind(KindLabel.request)],
            allowed_personas={PersonaKey.project_lead},
            min_items=1,
            max_acceptance=None,
            allow_depends_on=False,
            require_outcome_path=False,
        ),
        kind_key=KindLabel.program,
    ),
    StageRow(
        spec=StageSpec(
            name=StageName.project,
            mode="propose",
            require_labels={Labels.kind(KindLabel.request)},
            require_no_parent=None,
            child_labels=[Labels.kind(KindLabel.subrequest)],
            allowed_personas={PersonaKey.system_engineer, PersonaKey.doc_lead},
            min_items=1,
            max_acceptance=None,
            allow_depends_on=True,
            require_outcome_path=False,
        ),
        kind_key=KindLabel.request,
    ),
    StageRow(
        spec=StageSpec(
            name=StageName.subrequest,
            mode="propose",
            require_labels={Labels.kind(KindLabel.subrequest)},
            require_no_parent=None,
            child_labels=[Labels.kind(KindLabel.child)],
            allowed_personas={
                PersonaKey.architect,
                PersonaKey.programmer,
                PersonaKey.analyst,
                PersonaKey.writer,
                PersonaKey.general,
            },
            min_items=1,
            max_acceptance=None,
            allow_depends_on=True,
            require_outcome_path=True,
        ),
        kind_key=KindLabel.subrequest,
    ),
    StageRow(
        spec=StageSpec(
            name=StageName.child,
            mode="execute",
            require_labels=set(),
            require_no_parent=False,
            child_labels=[],
            allowed_personas=set(),
            min_items=1,
            max_acceptance=None,
            allow_depends_on=True,
            require_outcome_path=False,
        ),
        kind_key=KindLabel.child,
    ),
    StageRow(
        spec=StageSpec(
            name=StageName.plain,
            mode="execute",
            require_labels=set(),
            require_no_parent=None,
            child_labels=[],
            allowed_personas=set(),
            min_items=1,
            max_acceptance=None,
            allow_depends_on=True,
            require_outcome_path=False,
        ),
        kind_key=None,
    ),
)
"""段表 5 段（**宣言順**。`program → project → subrequest → child → plain`）。

実値は spec.md **P-01**（凍結 PoC `config.py:141-174` から現物確定）。`min_items` は**全段 1**で、
段ごとの上書きは `build()` が `build_stage_table()` を通して適用する（元の値を書き換えない）。
`max_acceptance` は**全段 `None`（上限なし）**で、設定の既定上限も `build()` が載せる。

**`kind_key` と `require_labels` を別に持つ理由**: `child` 段は `kind_key = child` だが
`require_labels` は**空**である（P-01）。`require_labels` から `kind_key` を導出すると、
「`kind` ラベルを 1 つも持たず親がある issue」が `child` 段に落ちなくなり FR-016a が壊れる。
逆に `kind_key` から `require_labels` を導出すると `kind:child` を持たない子が弾かれる。
"""

KIND_TO_STAGE: Mapping[KindLabel, StageName] = MappingProxyType(
    {row.kind_key: row.spec.name for row in STAGE_ROWS if row.kind_key is not None}
)
"""`kind` 値 → 段名（`kind_key` を持つ **4 行**から導出）。

`KindLabel.question` はどの行にも現れないため、`resolve_stage` の事前検査で `UnknownStage` に
倒れる（SD-41）。段表の走査に任せると `plain` が受け皿になって黙って通る。
"""

DEFAULT_PLACEHOLDERS: frozenset[str] = frozenset(
    {"", "-", "n/a", "na", "tbd", "todo", "なし", "特になし", "未定", "検討中", "後で"}
)
"""穴埋め文言の既定 **11 値**（PoC `acceptance.py:20-22` を継承）。

**出荷時既定の正本は `config.json`（CFG-49）**であり、この定数は設定を渡さずに
`AcceptanceRules` を組むときのフォールバックである（`build()` は必ず設定を通す）。
"""

PERSONA_GROUP: Mapping[PersonaKey, Literal["propose", "execute"]] = MappingProxyType(
    {
        PersonaKey.planner: "propose",
        PersonaKey.project_lead: "propose",
        PersonaKey.system_engineer: "propose",
        PersonaKey.doc_lead: "propose",
        PersonaKey.architect: "execute",
        PersonaKey.programmer: "execute",
        PersonaKey.analyst: "execute",
        PersonaKey.writer: "execute",
        PersonaKey.general: "execute",
    }
)
"""ペルソナの段所属（DM-09。分解側 **4**・実行側 **5** で 9 種すべてを網羅する）。

値の型を `StageSpec.mode` と**同じ `Literal`** にしてあるので、照合は
`PERSONA_GROUP.get(key) != spec.mode` の 1 行で済む。

**`planner` を分解側に置く**のは DM-09 が「ただ 1 つの段に属す」と定めており、統括分解の
既定担当が `planner` だからである。PoC は `planner` をどちらの集合にも入れず分解時の
フォールバック専用にしていた（`personas.py:236-237`）が、**そこは継承しない**（SD-49 (m)）。
「どちらにも属さない」を許すと段所属の照合が「未所属なら素通し」に崩れる。
"""

ALLOWED_VARS: frozenset[str] = frozenset(
    {
        "issue_id",
        "title",
        "description",
        "acceptance",
        "parent_title",
        "parent_acceptance",
        "persona_guide",
        "human_guidance",
        "feedback",
        "input_rules",
        "input_refs",
        "existing_children",
        "project_key",
        "persona_key",
        "outcome_dir",
    }
)
"""段テンプレート本文の `{変数}` の許可 **15 種**（D16-c）。

**本ファイルが正本**であり、`settings/src/settings/models.py` の同名定数はその写しである。
機械的に乖離を捕まえる手段が無い（二重宣言＝ SD-42・B-22）ため、**改訂は必ず 2 モジュール
同時**に行う（SKILL.md の規約）。
"""

REQUIRED_VARS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "program": frozenset({"persona_guide", "acceptance"}),
        "project": frozenset({"persona_guide", "acceptance"}),
        "subrequest": frozenset({"persona_guide", "acceptance"}),
        "child": frozenset({"acceptance"}),
        "plain": frozenset({"acceptance"}),
        "review_normal": frozenset({"acceptance"}),
        "review_final": frozenset({"acceptance"}),
    }
)
"""テンプレートキー別の必須変数（D16-c。**キーは段名ではなくテンプレートキー**）。

**stages はこれを検査に使わない**（本文の検査は settings の validate の役目で、stages は
例外を送出できない＝ FR-031）。正本として持つことに意味がある。
"""

PERSONA_GUIDE_HEADER = "割り当てられる担当（persona）:"
"""担当一覧の見出し（PoC `personas.py:254` を 1 バイトも変えず継承）。

**stages が持つ唯一の「プロンプトに載る定型文」**である（Constitution Check 判断 4）。
担当の並びそのものは `spec.allowed_personas` から毎回生成し、**定数にも本文にも書き写さない**
（FN-46・SC-005）。
"""


def render(text: str, values: Mapping[str, str]) -> str:
    """`{変数}` を充填する（contracts/prompt-rendering.md §6）。

    **`string.Formatter().parse()` を使い、正規表現を使わない** —— settings の
    `extract_variables` と同じ手段に揃えないと「settings の検査を通ったのに stages が
    埋められない」が沈黙で起きる。許可集合に無い名前・`{0}`・`{}` は**空文字**で埋め、
    書式指定（`:>10`）と変換（`!r`）は**無視**する。波括弧が壊れていれば**破れの手前まで**を
    返し、以降を捨てる（例外を送出しない＝ FR-031）。

    Args:
        text: テンプレート本文。
        values: 変数名 → 充填値。

    Returns:
        充填後の文字列。
    """
    out: list[str] = []
    try:
        for literal, field, _format_spec, _conversion in Formatter().parse(text):
            out.append(literal)
            if field is not None:
                out.append(values.get(field, ""))
    except ValueError:
        # 波括弧が閉じていない等。ここまでに積んだ分を返す（FR-027 と FR-031 の両立）。
        pass
    return "".join(out)


OUTCOME_TOKEN_RE = re.compile(r"[0-9A-Za-z_./\\~]*outcome/[^\s、。,）)\]】」]+")
"""成果物の指し名を拾う正規表現（PoC `backlog.py:29` を**1 バイトも変えずに継承**）。

`outcome/` の**直前に続く ASCII のパス構成文字も含めて 1 トークン**にする。`outcome/` から
切り出すと `../outcome/x.md` や絶対パスが `outcome/x.md` に化けて正準形として素通りする。
文字クラスを ASCII に限るのは、日本語まで飲むと `成果物はoutcome/x.md` の `成果物は` が
前置と見なされて化けるためである。全角括弧・読点・空白は終端に含む。
"""

OUTCOME_PREFIX = "outcome/"
"""正準形の唯一の接頭辞（`{outcome_dir}` の値もここから導出する）。"""


def outcome_filename(token: str) -> str | None:
    """正準形 `outcome/<ファイル名>` からファイル名を切り出す（PoC `backlog.py:47-73` を継承）。

    **切り出しの実装はここ 1 箇所だけ**である（FR-051・SC-009）。`rsplit` や
    `split("outcome/")` の複製を他所に作らない —— 複製すると宣言と書き込み先の食い違いが
    場所ごとに変わる。**基準名に丸めない**理由も同じで、丸めると丸めたことが誰にも見えなくなる
    （`backlog.py:56-58`）。

    Args:
        token: 抽出された成果物の指し名。

    Returns:
        正準形ならファイル名、そうでなければ `None`。
    """
    text = token.strip()
    if not text.startswith(OUTCOME_PREFIX):
        return None
    rest = text[len(OUTCOME_PREFIX) :]
    if rest in {"", ".", ".."}:
        return None
    if "/" in rest or "\\" in rest:
        return None
    if rest.startswith("~"):
        return None
    # ドライブ付き（`C:x.md`）。絶対パスは `/`・`\` の検査で既に落ちているので残るのはこれだけ。
    # `pathlib` は使わない —— POSIX では `PosixPath("C:x.md").drive` が常に空で検査が効かず
    # （PoC `backlog.py:71` はこの環境では無効）、`models.py` を I/O 由来の型から遠ざけたい。
    if len(rest) >= 2 and rest[1] == ":" and rest[0].isascii() and rest[0].isalpha():
        return None
    return rest


REPO_PREFIX = "repo/"
"""リポジトリの成果物の接頭辞（D25-b）。`repo/` の後ろはリポジトリ相対のパス。"""

REPO_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z_.\-/\\~])repo/[^\s、。,）)\]】」`'\"]+")
"""リポジトリの成果物の指し名を拾う正規表現（D25-b）。

`OUTCOME_TOKEN_RE` と違い、**直前にパス構成文字が続く `repo/` は拾わない**（`myrepo/x`・
`../repo/x` を成果物と見なさない）。拾い漏れても害が無い —— 成果物の宣言が 1 つも無ければ
段の規則（`require_outcome_path`）が指摘する。バッククォート・引用符は終端に含む。
"""


def repo_path(token: str) -> str | None:
    """`repo/<パス>` からリポジトリ相対のパスを切り出す（D25-b）。正準形でなければ `None`。

    見るのは形だけ（空・絶対・`..`・バックスラッシュ・`~` 始まり・`.git`）。実在と閉じ込めは
    完了ゲートで repo が判定する。

    Args:
        token: 抽出されたリポジトリの成果物の指し名。

    Returns:
        正準形ならリポジトリ相対のパス、そうでなければ `None`。
    """
    text = token.strip()
    if not text.startswith(REPO_PREFIX):
        return None
    rest = text[len(REPO_PREFIX) :]
    if not rest or rest.startswith(("/", "~")) or "\\" in rest:
        return None
    parts = [part for part in rest.split("/") if part not in {"", "."}]
    if not parts or ".." in parts or ".git" in parts:
        return None
    return "/".join(parts)


CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*\S")
"""1 行 1 条件の形（`- [ ]`／`- [x]`／`- [X]` ＋ 本文）。PoC `acceptance.py:31` を継承。

**PoC はこの形を起票済み issue の条文にだけ掛けており、提案には掛けていない**。V4 は
`stages-F06`・P5D-025 が提案側にも課すので、**ここだけ PoC より厳しい**（FR-037）。
"""

BULLET_CHARS = "・-*＊　 \t"
"""穴埋め判定の前に剥がす箇条書き記号（PoC `acceptance.py:25` を継承。全角空白を含む）。"""

CHECKBOX_PREFIX_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*")
"""条文の頭に付くチェックボックス（`- [ ] `）。**本文を取り出すためだけに使う**。

PoC の提案の受入条文は**素の文字列**であり、`- [ ]` は後段の `render()` が付けていた
（`acceptance.py:54-60`）。V4 は `stages-F06`・P5D-025 が提案側にも 1 行 1 条件の形を課す
ため、条文は必ず `- [ ] 本文` の形になる。穴埋め・最小長を条文**全体**に掛けると
`- [ ] tbd` は `[ ] tbd` に化けて穴埋め集合に一致せず、**正しい形の条文では穴埋め検査が
1 度も発火しない**（`resolve_stage` の SD-41 とまったく同型の穴）。よって本文を取り出して
から判定する —— これで PoC の「条文＝本文」に対する判定と同じ意味になる。
"""


def acceptance_body(line: str) -> str:
    """条文からチェックボックス接頭辞を剥がした本文を返す。

    Args:
        line: 受入条文 1 行。

    Returns:
        本文（接頭辞が無ければ元のまま）。
    """
    return CHECKBOX_PREFIX_RE.sub("", line, count=1)


@dataclass(frozen=True, slots=True)
class AcceptanceRules:
    """受入条文の判定に使う閾値（設定由来）。振る舞いは値の判定だけ（憲章 §7-2）。"""

    placeholders: frozenset[str] = DEFAULT_PLACEHOLDERS
    """穴埋め文言の集合（CFG-49）。空集合なら穴埋め検査を無効にする運用になる。"""

    min_len: int = 10
    """受入条文 1 件の最小長（CFG-48）。"""

    max_acceptance_default: int | None = None
    """受入条文の既定上限（CFG-50）。**検査で直に読まない** —— 実効値は `build_stage_table()`
    が `StageSpec.max_acceptance` へ載せ、検査はそちらだけを見る（二重適用を避ける）。"""

    def is_placeholder(self, text: str) -> bool:
        """穴埋め文言か（PoC `acceptance.py:34-36` の手順を継承）。

        チェックボックス接頭辞を剥がす（→ `CHECKBOX_PREFIX_RE`）→ 前後空白除去 →
        箇条書き記号を剥がす → 再度空白除去 → 小文字化 → 集合照合。題名・要約・説明には
        接頭辞が付かないので、剥がす段は何もしない。

        Args:
            text: 判定する文字列。

        Returns:
            穴埋め文言なら真。
        """
        return (
            acceptance_body(text).strip().strip(BULLET_CHARS).strip().lower() in self.placeholders
        )

    def is_too_short(self, text: str) -> bool:
        """最小長に満たないか（穴埋め判定と**排他**で使う）。

        長さもチェックボックス接頭辞を除いた**本文**で数える（PoC の条文＝本文と同じ意味）。

        Args:
            text: 判定する文字列。

        Returns:
            最小長未満なら真。
        """
        return len(acceptance_body(text).strip()) < self.min_len

    @classmethod
    def from_config(cls, config: StagesConfig) -> AcceptanceRules:
        """設定から閾値を写す。

        Args:
            config: 読み込み済みの `config.json`。

        Returns:
            閾値を抱えた不変オブジェクト。
        """
        return cls(
            placeholders=frozenset(config.placeholder_patterns),
            min_len=config.min_acceptance_len,
            max_acceptance_default=config.max_acceptance_default,
        )


def build_stage_table(config: StagesConfig) -> tuple[StageRow, ...]:
    """設定の上書きを適用した段表を作る。

    `min_items` の上書きと `max_acceptance` の既定を **`StageSpec.model_copy(update=...)`**
    で載せる（憲章 §7-2。元の `STAGE_ROWS` を書き換えない）。段名の妥当性と値域は `build()`
    が先に検査済みである（検査の主語が段表なので `StagesConfig` の validator には置かない）。

    Args:
        config: 読み込み済みの `config.json`。

    Returns:
        上書き適用後の段表（**宣言順**は変わらない）。
    """
    rows: list[StageRow] = []
    for row in STAGE_ROWS:
        update: dict[str, object] = {}
        override = config.min_items_override.get(str(row.spec.name))
        if override is not None:
            update["min_items"] = override
        if config.max_acceptance_default is not None:
            update["max_acceptance"] = config.max_acceptance_default
        spec = row.spec.model_copy(update=update) if update else row.spec
        rows.append(StageRow(spec=spec, kind_key=row.kind_key))
    return tuple(rows)
