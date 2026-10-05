"""横断: 実 9 件の登録から導かれる組み立て順が設計と整合すること（US6・FR-025〜FR-027）。

GAP-6 を埋める。REG-8f は `set(order) == {登録名}` としか見ない（**集合比較**）ので、順序そのものの
検査は本 feature が初である。

⚠ **突合は半順序で行う。** `p4_module_dependency_notes.md` §4 の 6 段を**全順序として読むと現物と
食い違う**——実出力は `('board','monitor','stages','vault','settings','executor','orchestrator',
'trigger')` で、monitor（§4 第 3 段）が vault（第 1 段）・settings（第 2 段）より**前**に来る。
原因は `_topological_order` が Kahn 法＋**同時に組める者は名前の辞書順**で並べるためであり、
**配線の誤りではない**（§4 が保証するのは「提供 MOD の `build` を利用 MOD より先に呼べる」という
依存の一方向性であって段番号の全順序ではない）。裁定待ちの起票は **B-04** である。

⚠ 本ファイルは**組を宣言しない**（横断ファイル）。**`requires_bd` を付けない**（`build`
を呼ばない）。
"""

from __future__ import annotations

import re

from _support.fake_modules import DESIGN_DEPENDENCY_LEVELS

from api.registry import MODULE_REGISTRATIONS
from api.wiring import check_registration_graph, contract_label
from common.kernel.config import find_repo_root

# `DESIGN_DEPENDENCY_LEVELS` は `p4_module_dependency_notes.md` §4 の 6 段の**逐語データ**である。
# ⚠ 同じ module の `make_topology`（偽の登録）は **1 度も使わない**——偽の登録では monitor ←
# settings・stages ← monitor という**現物に無い辺**が入っており、実 registry の依存関係と食い違う
# （**B-08**）。本ファイルが import するのは**データ定数だけ**である（research R-07）。
# ⚠ ただし**写しは正本ではない**。O-2・O-3 だけでは §4 の現物が改訂されても緑のままなので、
# O-6 が §4 を**直に読んで**写しが追随していることを確かめる（`013` Phase 17 (b)・`014` HD-41 で
# 2 度指摘された「検査が別の源を見ている」型を塞ぐ）。`fake_modules.py` の側は**変更しない**
# （FR-034）——写しを正へ直すのではなく、写しが正本に追随していることを機械で見る。

DEPENDENCY_NOTES_RELATIVE = "docs/design/phase4/p4_module_dependency_notes.md"
"""§4 の所在（**行番号で引かない**＝ CLAUDE.md の参照書式。見出しをアンカーにする）。"""

BUILD_ORDER_HEADING = "## 4."
"""構築順の節の見出し（`## 4. composition root（apps/api）の構築順`）。"""

_SECTION_HEADING = re.compile(r"^##\s")
"""節の見出し（`## 4.` の次に現れた時点で走査を打ち切る）。"""

_NUMBERED_ITEM = re.compile(r"^\d+\.\s+(.*)$")
"""§4 の段を表す番号付きリストの 1 行。"""

_PARENTHETICAL = re.compile(r"（[^（）]*）")
"""段の括弧書き（**契約名は突き合わせない**＝ **B-11**。実 `requires` と食い違うため落とす）。"""

_BACKTICKED = re.compile(r"`([^`]+)`")
"""段のモジュール名（§4 はモジュール名だけをバッククォートで囲っている）。"""


def parse_design_levels(document: str) -> tuple[tuple[str, ...], ...]:
    """§4 の番号付きリストから各段のモジュール名の並びを導く**純関数**。

    `## 4.` の見出しから**次の `## ` 見出しの手前まで**を対象にし、番号付きリストの各行から
    括弧書き（契約名）を落としてからバッククォートで囲われた名前を拾う。

    Args:
        document: `p4_module_dependency_notes.md` の全文。

    Returns:
        段ごとのモジュール名の並び（文書に現れる順）。§4 が見つからなければ空。
    """
    levels: list[tuple[str, ...]] = []
    inside = False
    for line in document.splitlines():
        if line.startswith(BUILD_ORDER_HEADING):
            inside = True
            continue
        if not inside:
            continue
        if _SECTION_HEADING.match(line):
            break
        item = _NUMBERED_ITEM.match(line.strip())
        if item is None:
            continue
        names = tuple(_BACKTICKED.findall(_PARENTHETICAL.sub("", item.group(1))))
        if names:
            levels.append(names)
    return tuple(levels)


def _notes_text() -> str:
    """`p4_module_dependency_notes.md` を**読み取り専用**で開いて全文を返す。

    Returns:
        文書の全文。
    """
    return (find_repo_root() / DEPENDENCY_NOTES_RELATIVE).read_text(encoding="utf-8")


def _provider_of() -> dict[type[object], str]:
    """契約 → それを提供する MOD 名（実登録から導く）。

    Returns:
        契約の型 → 提供モジュール名。
    """
    return {
        contract: registration.name
        for registration in MODULE_REGISTRATIONS
        for contract in registration.provides
    }


def _dependency_edges() -> tuple[tuple[str, str], ...]:
    """実 `requires`／`provides` から `(提供元, 利用元)` の辺を導く。

    Returns:
        依存辺（提供元が先、利用元が後）。
    """
    provider = _provider_of()
    return tuple(
        (provider[contract], registration.name)
        for registration in MODULE_REGISTRATIONS
        for contract in registration.requires
        if contract in provider
    )


def _design_level_of() -> dict[str, int]:
    """`p4_module_dependency_notes.md` §4 の段番号（0 起算）。

    Returns:
        モジュール名 → 段番号。
    """
    return {name: index for index, level in enumerate(DESIGN_DEPENDENCY_LEVELS) for name in level}


def test_o1_every_provider_is_built_before_its_consumer() -> None:
    """横断 O-1: 各登録の `requires` を提供する MOD が実出力で自分より**前**に在る（FR-025）。

    見る欄: `requires`・`provides`（依存の一方向性）。**期待値は実 registry の 2 欄から導き、§4 を
    使わない**——設計文書に頼らずに「配線として成立しているか」だけを見る層である。
    """
    order = check_registration_graph(MODULE_REGISTRATIONS)
    position = {name: index for index, name in enumerate(order)}

    violations = [
        f"{consumer} が {provider} より先に組まれる"
        f"（{provider} の位置 {position[provider]} > {consumer} の位置 {position[consumer]}）"
        for provider, consumer in _dependency_edges()
        if position[provider] > position[consumer]
    ]

    assert not violations, "依存の一方向性が壊れている:\n  " + "\n  ".join(violations)


def test_o1_every_required_contract_has_a_provider_among_the_registrations() -> None:
    """横断 O-1（前提）: 実 9 件の中で `requires` の供給元がすべて見つかる（FR-025）。

    見る欄: `requires`・`provides`。供給元の無い契約が在ると O-1 の辺が黙って減るため、辺を数える
    前にこの前提を独立に確かめる（`_dependency_edges` が `if contract in provider` で落とす分）。
    """
    provider = _provider_of()
    orphans = {
        registration.name: sorted(
            contract_label(contract)
            for contract in registration.requires
            if contract not in provider
        )
        for registration in MODULE_REGISTRATIONS
        if any(contract not in provider for contract in registration.requires)
    }

    assert not orphans, f"実 9 件の中に供給元の無い `requires` がある: {orphans}"


def test_o2_design_levels_name_exactly_the_real_registrations() -> None:
    """横断 O-2: §4 の 6 段の名前集合が実登録名の集合と一致する（FR-026）。

    見る欄: `MODULE_REGISTRATIONS[*].name` × §4 の段データ。片方だけに名前が増えると、O-3 の段番号
    引きが黙って `KeyError` になる（または対象から漏れる）ので、先に集合で確かめる。
    """
    design_names = {name for level in DESIGN_DEPENDENCY_LEVELS for name in level}
    registered = {registration.name for registration in MODULE_REGISTRATIONS}

    assert design_names == registered, (
        "§4 の段データと実登録の名前集合が食い違う\n"
        f"  §4 にだけ在る: {sorted(design_names - registered)}\n"
        f"  実登録にだけ在る: {sorted(registered - design_names)}"
    )


def test_o6_design_levels_follow_the_notes_verbatim() -> None:
    """横断 O-6: `DESIGN_DEPENDENCY_LEVELS` が §4 の**現物**に追随している（FR-026）。

    見る欄: §4 の段データ × 現物。O-2 は**名前集合**しか見ないため、段の割り当てが変わっても
    （例: monitor を第 2 段へ移す）緑のままである——`_support/fake_modules.py` の定数は §4 の
    **手写し**であり、写しだけを突合先にすると正本の改訂に気づけない。ここで §4 を直に読み、
    段の区切りと並びまで含めて逐語一致することを確かめる。

    ⚠ **§4 の括弧書きの契約名とは突き合わせない**（**B-11**）——orchestrator の実 `requires` 9 件に
    `OutcomeWriter`・`Authorizer`・`CommandRegistry`・`LLMHttpHook` は含まれない（research R-12）。
    ⚠ `_support/fake_modules.py` は**変更しない**（FR-034）。食い違いが出たら写しを直すのではなく、
    どちらが正かを人間に確かめる。
    """
    parsed = parse_design_levels(_notes_text())

    assert parsed, (
        f"§4（見出し {BUILD_ORDER_HEADING!r}）から段を 1 つも読めなかった: "
        f"{DEPENDENCY_NOTES_RELATIVE}——見出しの綴りが変わった可能性がある"
        "（空同士の比較で偽の緑にしないための前提確認）"
    )
    assert parsed == DESIGN_DEPENDENCY_LEVELS, (
        "`_support/fake_modules.py` の `DESIGN_DEPENDENCY_LEVELS` が §4 の現物と食い違う\n"
        f"  §4 の現物: {parsed}\n"
        f"  写し     : {DESIGN_DEPENDENCY_LEVELS}\n"
        f"  正本: {DEPENDENCY_NOTES_RELATIVE} の {BUILD_ORDER_HEADING} 節"
    )


def test_o6_parser_is_sensitive_to_the_level_assignment() -> None:
    """横断 O-6（自己実証）: 解析器が**段の割り当て**まで映すこと（名前集合だけではない）。

    見る欄: —（判定器そのものの検査）。合成の文書を与えて、(a) 括弧書きの契約名を拾わない
    (b) 次の `## ` 見出しの先を読まない (c) 段の区切りが出力に残る、の 3 つを示す。
    ⚠ 合成データは**関数の中**に置く（`test_wiring_contract_coverage.py` の C-4 と同じ規律）。

    ⚠ (a) は **2 機構の連言**で守られている——`_PARENTHETICAL`（括弧ごと落とす）と `_BACKTICKED`
    （バッククォートで囲われた語だけ拾う）。§4 の**現物**は契約名をバッククォートで囲っていないため
    `_BACKTICKED` だけで (a) が達たされ、`_PARENTHETICAL` を外しても現物に対しては**恒等変換**に
    なる（実測）。そこで**契約名がバッククォート化された §4**（B-11 の裁定が §4 側を改訂する形で
    転んだ場合に現れうる体裁）を 2 本目の合成文書として与え、`_PARENTHETICAL` **単独の変異でも
    赤くなる**ようにしてある——この 1 本が無いと (a) の前方ガードは無検査のまま置かれる。
    """
    document = (
        "## 3. 別の節\n"
        "1. `never_parsed`（§4 より前は読まない）\n"
        "\n"
        "## 4. composition root（apps/api）の構築順\n"
        "\n"
        "1. `board`・`vault`（依存なし）\n"
        "2. `monitor`（BoardReader・BoardWriter）\n"
        "\n"
        "## 5. 自己検証\n"
        "1. `never_parsed_either`（次の見出しの先も読まない）\n"
    )
    backticked_contracts = (
        "## 4. composition root（apps/api）の構築順\n"
        "\n"
        "1. `board`・`vault`（依存なし）\n"
        "2. `monitor`（`BoardReader`・`BoardWriter`）\n"
        "\n"
        "## 5. 自己検証\n"
    )

    assert parse_design_levels(document) == (("board", "vault"), ("monitor",))
    assert parse_design_levels("## 9. 構築順が無い文書\n") == ()
    # ⚠ `_PARENTHETICAL` を外すと `('monitor', 'BoardReader', 'BoardWriter')` になる（実測）。
    assert parse_design_levels(backticked_contracts) == (("board", "vault"), ("monitor",))


def test_o3_each_provider_sits_in_an_earlier_design_level() -> None:
    """横断 O-3: 実依存辺のすべてで、提供元の**段番号**が利用元より小さい（FR-026・US6 AC-2）。

    見る欄: `requires`・`provides` × §4 の段番号。⚠ **§4 の括弧書きの契約名とは突き合わせない**
    （**B-11**）——第 5 段は orchestrator を「上記全部＋…」と書くが、実 `requires` 9 件に
    `OutcomeWriter`・`Authorizer`・`CommandRegistry`・`LLMHttpHook` は**含まれない**（research
    R-12）。
    段番号だけを使う突合は 8 MOD すべてで成立する。
    """
    level_of = _design_level_of()

    violations = [
        f"{consumer}（第 {level_of[consumer] + 1} 段）が"
        f" {provider}（第 {level_of[provider] + 1} 段）を要求している"
        for provider, consumer in _dependency_edges()
        if level_of[provider] >= level_of[consumer]
    ]

    assert not violations, "§4 の段番号が実依存辺と矛盾する:\n  " + "\n  ".join(violations)


def test_o4_derived_order_diverges_from_design_levels_only_off_the_dependency_edges() -> None:
    """横断 O-4: 実出力と §4 の段番号が食い違う対は在るが、
    **そのどれも実依存辺ではない**（FR-026）。

    見る欄: 導出順 × §4 の段番号。§4 の段を**全順序として読むと現物と食い違う**という事実
    （**B-04**）を、埋めずに機械で囲う形にしたものである。

    ⚠ **明示的に採らない主張が 3 つある**（採ると赤くなる、または現物と食い違う）。

    - (a) 「実出力 == §4 の逐語の列」——実出力は monitor（第 3 段）が vault（第 1 段）・settings
      （第 2 段）より前に来る。Kahn 法＋同層の辞書順（`m < s < v`）による並びであり、
      **配線の誤りでは
      ない**（**B-04**）。
    - (b) 「層 i の全部が層 i+1 の全部より前」——`unit/test_wiring_order.py` の CR-11
    が**偽の登録**で
      置く主張。偽では monitor ← settings・stages ← monitor という現物に無い辺があるおかげで通るが、
      実 registry では成り立たない（**B-08**）。
    - (c) §4 の**括弧書きの契約名**との突合——orchestrator の実 `requires` 9
    件と食い違う（**B-11**）。
    """
    order = check_registration_graph(MODULE_REGISTRATIONS)
    position = {name: index for index, name in enumerate(order)}
    level_of = _design_level_of()
    edges = set(_dependency_edges())

    inverted = {
        (earlier, later)
        for earlier in order
        for later in order
        if level_of[earlier] < level_of[later] and position[earlier] > position[later]
    }

    assert inverted, (
        "§4 の段と実出力の食い違いが 1 件も無い。B-04（実測: monitor が vault・settings より前）が"
        "解消したか、導出規則が変わった可能性がある——この事実を design-sync で確かめ直すこと"
    )
    assert not (inverted & edges), (
        "段と実出力の食い違いが**実依存辺の上**で起きている（半順序が壊れた）: "
        f"{sorted(inverted & edges)}"
    )


def test_o5_derivation_is_deterministic() -> None:
    """横断 O-5: 同じ入力で同じ順序が返る（FR-027・US6 AC-3）。

    見る欄: 導出順の決定性。`_topological_order` は同層を**名前の辞書順**（`heapq`）で並べるため
    決定的であり、登録の並び順に依存しない。
    """
    first = check_registration_graph(MODULE_REGISTRATIONS)
    second = check_registration_graph(MODULE_REGISTRATIONS)
    reversed_input = check_registration_graph(tuple(reversed(MODULE_REGISTRATIONS)))

    assert first == second
    assert first == reversed_input
    assert len(first) == len(MODULE_REGISTRATIONS)
