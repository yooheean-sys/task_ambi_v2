"""設計文書（契約台帳 csv・データモデル mmd）を読むパーサ。

突合テストが読む正本は 2 種だけ（plan.md (viii)）:

- `docs/design/phase4/p4_module_contract.csv` — Protocol・メソッド・例外・同期／非同期
- `docs/design/phase5/<MOD>/p5_<MOD>_data_model.mmd` — 列挙・受け渡し型・契約例外

散文の notes 類（`p5_common_notes.md`・`p4_module_dependency_notes.md`）は入力にしない。

実装している正規化規則（plan.md (viii) の 5 規則＋research R-03）:

1. `/` 連記（`+created_at / updated_at`）は複数属性へ展開する。
   分割は**前後に空白のある ` / ` のみ**を対象にする（`outcome/_archive/<f>` の
   ようなパス表記を誤って分割しないため。R-14 の規則 1 の実装形）。
2. 列挙値は大文字小文字を問わず識別子として扱う（`Grade` の `F`／`W`／`I`）。
3. 名前が `[A-Za-z_][A-Za-z0-9_]*` に一致しない行は除外する
   （`+種別（人間指示／回答／封筒）は持たない` のような注記行）。
   照合は行頭の識別子接頭辞に対して行う（`+task_description（…）` は `task_description`）。
4. `を継承` を含む行は属性ではなく継承宣言として扱い、例外の基底の突合に使う。
5. `既定 X` → 省略可＋既定値／`任意` → 省略可／印なし → 必須（research R-17 の 3 規則）。
   印は**行全体**に掛かる（`+max_iter / max_rpm / max_execution_time 任意` は 3 件とも省略可）。

読む namespace は `common_interfaces_<MOD>_py` と `common_kernel_enums_py` のみ。
各 MOD の config namespace（`BdConfig` 等）は突合の対象外。

型の扱い: mmd が型を書いている属性はその型を正規化して返す。書いていない属性は
`type_text=None` を返す（research R-15 の規則 1 は「実装がどう書くか」の指針であって
mmd から機械的に導けない — `TaskTemplates.generation` は無型だが `int`、
`Member.max_iter` は無型だが `int | None`）。時刻 11 件（R-15 規則 2）と
`Literal` 7 件（R-15 規則 3）は名指しの表で補う。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

INTERFACE_MODULES: tuple[str, ...] = (
    "board",
    "settings",
    "vault",
    "monitor",
    "stages",
    "executor",
    "orchestrator",
    "repo",
)
"""契約ファイルを持つ提供モジュール 8 件（trigger は契約を提供しない＝FR-006。repo は D25）。"""

INHERIT_MARK = "を継承"
OPTIONAL_MARK = "任意"
DEFAULT_MARK = "既定"

# --- research R-15 規則 2: mmd に型が無い時刻属性 11 件（名指し） ---------------
DATETIME_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("Issue", "created_at"),
        ("Issue", "updated_at"),
        ("Comment", "created_at"),
        ("FileRef", "mtime"),
        ("FailureEvent", "at"),
        ("Measurement", "started_at"),
        ("Measurement", "finished_at"),
        ("CrewEvent", "at"),
        ("ExecutionContext", "requested_at"),
        ("KickoffResult", "started_at"),
        ("KickoffResult", "finished_at"),
    }
)

# --- research R-15 規則 3: 値域が図に直接書かれ Literal にする属性 8 件（名指し。8 件目は D25） ---
LITERAL_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("IssueRef", "dependency_type"),
        ("StageSpec", "mode"),
        ("CrewSpec", "mode"),
        ("HookRef", "points"),
        ("IssueRunSummary", "result"),
        ("VaultPathViolation", "kind"),
        ("ExecutorFailure", "phase"),
        ("RepoPathViolation", "kind"),
    }
)

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_BUILTIN_TYPES = frozenset(
    {"str", "int", "bool", "float", "bytes", "dict", "list", "set", "frozenset", "None", "datetime"}
)

_NAMESPACE_RE = re.compile(rf"^namespace\s+({_IDENT})\s*\{{")
_CLASS_RE = re.compile(rf'^class\s+({_IDENT})(?:\["[^"]*"\])?\s*\{{')
_STEREOTYPE_RE = re.compile(r"^<<\s*([^>]+?)\s*>>$")
_ATTR_NAME_RE = re.compile(rf"^\+\s*({_IDENT})")
_ENUM_VALUE_RE = re.compile(rf"^({_IDENT})$")
_SLASH_SPLIT_RE = re.compile(r"\s+/\s+")
_DEFAULT_RE = re.compile(rf"{DEFAULT_MARK}\s*([A-Za-z0-9_:.\-]+)")
# 型トークン: `str` / `Grade` / `list~str~` / `dict~PersonaKey,str~`
_TYPE_TOKEN_RE = re.compile(rf"({_IDENT})(?:~([^~]*)~)?")


@dataclass(frozen=True)
class Attribute:
    """mmd の 1 属性。`type_text` は mmd が型を書いていなければ `None`。"""

    name: str
    type_text: str | None
    required: bool
    default_text: str | None
    raw: str


@dataclass(frozen=True)
class DesignClass:
    """mmd の 1 クラス（受け渡し型・列挙・例外・名前空間クラス）。"""

    name: str
    stereotype: str
    module: str
    attributes: tuple[Attribute, ...]
    values: tuple[str, ...]
    base: str | None


# --------------------------------------------------------------------------
# mmd
# --------------------------------------------------------------------------


def is_type_expression(text: str, known_types: frozenset[str] | None = None) -> bool:
    """先頭の `a | b` 列が「型の並び」に見えるか。

    2 つの誤読を防ぐ門番:

    - `global | project_key`（`LimitPolicy.scope`）や `propose | execute` のような
      **値の列挙**を型と読まない（組み込み型か PascalCase のみ許す）。
    - `+role CrewAI Agent.role（…）` の `CrewAI` のような**注記中の固有名**を型と読まない
      （`known_types` を渡した場合、設計文書に実在するクラス名だけを許す）。
    """
    for token in text.split("|"):
        name = token.strip().split("~", 1)[0].strip()
        if name in _BUILTIN_TYPES:
            continue
        if known_types is not None:
            if name in known_types:
                continue
            return False
        if re.fullmatch(r"[A-Z][A-Za-z0-9]*", name):
            continue
        return False
    return True


def normalize_type(text: str) -> str:
    """mermaid の `~` 記法を Python の `[]` 記法へ直す。"""
    parts: list[str] = []
    for token in text.split("|"):
        match = _TYPE_TOKEN_RE.match(token.strip())
        if match is None:
            parts.append(token.strip())
            continue
        base, params = match.group(1), match.group(2)
        if params is None:
            parts.append(base)
        else:
            inner = ", ".join(p.strip() for p in params.split(","))
            parts.append(f"{base}[{inner}]")
    return " | ".join(parts)


def extract_type(remainder: str, known_types: frozenset[str] | None = None) -> str | None:
    """属性名の後ろから型表記を取り出す。無ければ `None`。"""
    text = remainder.strip()
    if not text:
        return None
    pattern = re.compile(rf"^({_IDENT}(?:~[^~]*~)?(?:\s*\|\s*{_IDENT}(?:~[^~]*~)?)*)")
    match = pattern.match(text)
    if match is None:
        return None
    candidate = match.group(1)
    if not is_type_expression(candidate, known_types):
        return None
    rest = text[match.end() :].lstrip()
    # `PersonaKey | 空`（`ProposalItem.persona`）は `| None` の日本語表記。
    if rest.startswith("| 空"):
        return f"{normalize_type(candidate)} | None"
    return normalize_type(candidate)


def literal_values(remainder: str) -> tuple[str, ...]:
    """`blocks | parent-child` のような値域表記から値を取り出す。"""
    head = remainder.strip().split("（", 1)[0]
    values: list[str] = []
    for token in head.split("|"):
        candidate = token.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", candidate):
            values.append(candidate)
        else:
            break
    return tuple(values)


def parse_attribute_line(
    line: str, class_name: str, known_types: frozenset[str] | None = None
) -> list[Attribute]:
    """1 行の属性宣言を（`/` 連記を展開して）`Attribute` の並びにする。"""
    body = line[1:].strip()
    # 規則 5: 印は行全体に掛かる。
    default_match = _DEFAULT_RE.search(body)
    if default_match is not None:
        required, default_text = False, default_match.group(1)
    elif OPTIONAL_MARK in body:
        required, default_text = False, None
    else:
        required, default_text = True, None

    attributes: list[Attribute] = []
    for segment in _SLASH_SPLIT_RE.split(body):  # 規則 1
        name_match = _ATTR_NAME_RE.match(f"+{segment.strip()}")
        if name_match is None:  # 規則 3
            continue
        name = name_match.group(1)
        remainder = segment.strip()[len(name) :]
        key = (class_name, name)
        if key in DATETIME_ATTRS:
            type_text: str | None = "datetime"
        elif key in LITERAL_ATTRS:
            values = literal_values(remainder)
            joined = ", ".join(f'"{v}"' for v in values)
            type_text = f"Literal[{joined}]"
        else:
            type_text = extract_type(remainder, known_types)
        attributes.append(
            Attribute(
                name=name,
                type_text=type_text,
                required=required,
                default_text=default_text,
                raw=segment.strip(),
            )
        )
    return attributes


def parse_mmd(
    path: Path, namespace: str, module: str, known_types: frozenset[str] | None = None
) -> dict[str, DesignClass]:
    """mmd の 1 namespace を読み、クラス名 → `DesignClass` の辞書にする。"""
    classes: dict[str, DesignClass] = {}
    in_namespace = False
    class_name: str | None = None
    stereotype = ""
    attributes: list[Attribute] = []
    values: list[str] = []
    base: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("%%") or not line:
            continue

        if class_name is not None:
            if line == "}":
                classes[class_name] = DesignClass(
                    name=class_name,
                    stereotype=stereotype,
                    module=module,
                    attributes=tuple(attributes),
                    values=tuple(values),
                    base=base,
                )
                class_name, stereotype, base = None, "", None
                attributes, values = [], []
                continue
            stereotype_match = _STEREOTYPE_RE.match(line)
            if stereotype_match is not None:
                stereotype = stereotype_match.group(1)
                continue
            if line.startswith("+"):
                if INHERIT_MARK in line:  # 規則 4
                    inherit_match = _ATTR_NAME_RE.match(line)
                    if inherit_match is not None:
                        base = inherit_match.group(1)
                    continue
                attributes.extend(parse_attribute_line(line, class_name, known_types))
                continue
            enum_match = _ENUM_VALUE_RE.match(line)  # 規則 2
            if stereotype == "enumeration" and enum_match is not None:
                values.append(enum_match.group(1))
            continue

        if not in_namespace:
            namespace_match = _NAMESPACE_RE.match(line)
            if namespace_match is not None and namespace_match.group(1) == namespace:
                in_namespace = True
            continue

        if line == "}":
            in_namespace = False
            continue
        class_match = _CLASS_RE.match(line)
        if class_match is not None:
            class_name = class_match.group(1)

    return classes


def _interface_paths(design_root: Path) -> list[tuple[str, Path]]:
    return [
        (module, design_root / "phase5" / module / f"p5_{module}_data_model.mmd")
        for module in INTERFACE_MODULES
    ]


def load_interface_classes(design_root: Path) -> dict[str, DesignClass]:
    """7 つの `p5_<MOD>_data_model.mmd` の `common_interfaces_<MOD>_py` を全部読む。

    2 周する。1 周目でクラス名を集め、2 周目でその集合を型の門番に渡す
    （注記中の固有名を型と誤読しないため）。
    """
    first_pass: dict[str, DesignClass] = {}
    for module, path in _interface_paths(design_root):
        first_pass.update(parse_mmd(path, f"common_interfaces_{module}_py", module))
    known = frozenset(first_pass) | frozenset(load_kernel_enums(design_root))

    classes: dict[str, DesignClass] = {}
    for module, path in _interface_paths(design_root):
        classes.update(parse_mmd(path, f"common_interfaces_{module}_py", module, known))
    return classes


def load_kernel_enums(design_root: Path) -> dict[str, DesignClass]:
    """`p5_common_data_model.mmd` の `common_kernel_enums_py` の列挙を読む。"""
    path = design_root / "phase5" / "common" / "p5_common_data_model.mmd"
    classes = parse_mmd(path, "common_kernel_enums_py", "common")
    return {name: c for name, c in classes.items() if c.stereotype == "enumeration"}


def passing_types(classes: dict[str, DesignClass]) -> dict[str, DesignClass]:
    """受け渡し型（`<<受け渡し型>>`）だけを抜く。"""
    return {n: c for n, c in classes.items() if c.stereotype == "受け渡し型"}


def enumerations(classes: dict[str, DesignClass]) -> dict[str, DesignClass]:
    """列挙（`<<enumeration>>`）だけを抜く。"""
    return {n: c for n, c in classes.items() if c.stereotype == "enumeration"}


def exceptions(classes: dict[str, DesignClass]) -> dict[str, DesignClass]:
    """契約例外（`<<exception>>`）だけを抜く。"""
    return {n: c for n, c in classes.items() if c.stereotype == "exception"}


# --------------------------------------------------------------------------
# p4_module_contract.csv
# --------------------------------------------------------------------------

_PAREN_RE = re.compile(r"（[^（）]*）")
_RETURN_RE = re.compile(
    rf"^\s*(?:{_IDENT}\s*:\s*)?"
    rf"([A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]]*\])?"
    rf"(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]]*\])?)*)"
)
_RETURN_ALIASES = {"issue_id": "str"}


@dataclass(frozen=True)
class ContractRow:
    """契約台帳の 1 行（IF-01〜50）。"""

    if_id: str
    provider: str
    consumer: str
    protocol: str
    method: str
    arg_count: int
    return_type: str
    exceptions: tuple[str, ...]
    is_async: bool


@dataclass(frozen=True)
class ContractMethod:
    """`(提供 MOD, Protocol, メソッド)` へ畳み込んだ 1 メソッド。"""

    provider: str
    protocol: str
    method: str
    arg_count: int
    return_type: str
    exceptions: tuple[str, ...]
    is_async: bool
    if_ids: tuple[str, ...]


def strip_parens(text: str) -> str:
    """入れ子を考慮して全角括弧の注記を落とす。"""
    previous = None
    current = text
    while previous != current:
        previous = current
        current = _PAREN_RE.sub("", current)
    return current


def split_exceptions(cell: str) -> tuple[str, ...]:
    """例外列を例外名の並びにする。`—` は空。"""
    stripped = strip_parens(cell).strip()
    if not stripped or stripped == "—":
        return ()
    return tuple(part.strip() for part in stripped.split("／") if part.strip())


def count_args(cell: str) -> int:
    """入力列から引数の個数を数える（`・` 区切り。注記括弧は落とす）。"""
    stripped = strip_parens(cell).strip()
    if not stripped or stripped == "—":
        return 0
    return len([part for part in stripped.split("・") if part.strip()])


def return_type(cell: str) -> str:
    """出力列の先頭から戻り型を取り出す。"""
    match = _RETURN_RE.match(cell)
    if match is None:
        return cell.strip()
    value = match.group(1).strip()
    return _RETURN_ALIASES.get(value, value)


def load_contract_rows(design_root: Path) -> tuple[ContractRow, ...]:
    """契約台帳 csv の全行（50 行）を読む。"""
    path = design_root / "phase4" / "p4_module_contract.csv"
    rows: list[ContractRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            rows.append(
                ContractRow(
                    if_id=record["if_id"],
                    provider=record["提供モジュール"],
                    consumer=record["利用モジュール"],
                    protocol=record["Protocol名"],
                    method=record["メソッド名"],
                    arg_count=count_args(record["入力"]),
                    return_type=return_type(record["出力"]),
                    exceptions=split_exceptions(record["例外"]),
                    is_async=record["同期/非同期"].startswith("非同期"),
                )
            )
    return tuple(rows)


def fold_contract_rows(rows: tuple[ContractRow, ...]) -> tuple[ContractMethod, ...]:
    """50 行を `(提供 MOD, Protocol, メソッド)` で 41 メソッドへ畳み込む。

    同一メソッドが複数行を持つのは利用モジュールが違うだけで、シグネチャは 1 つ
    （contracts/protocols.md 末尾の畳み込み表）。例外は行をまたいで和を取る。
    """
    folded: dict[tuple[str, str, str], ContractMethod] = {}
    for row in rows:
        key = (row.provider, row.protocol, row.method)
        existing = folded.get(key)
        if existing is None:
            folded[key] = ContractMethod(
                provider=row.provider,
                protocol=row.protocol,
                method=row.method,
                arg_count=row.arg_count,
                return_type=row.return_type,
                exceptions=row.exceptions,
                is_async=row.is_async,
                if_ids=(row.if_id,),
            )
            continue
        merged = list(existing.exceptions)
        for name in row.exceptions:
            if name not in merged:
                merged.append(name)
        folded[key] = ContractMethod(
            provider=existing.provider,
            protocol=existing.protocol,
            method=existing.method,
            arg_count=existing.arg_count,
            return_type=existing.return_type,
            exceptions=tuple(merged),
            is_async=existing.is_async,
            if_ids=(*existing.if_ids, row.if_id),
        )
    return tuple(folded.values())
