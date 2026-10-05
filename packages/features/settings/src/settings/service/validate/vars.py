"""段テンプレート本文の `{変数}` の抽出（R-19・実測 M-20〜M-22）。

**正規表現を使わない**。素朴な波括弧の正規表現は `{issue.id}`（属性）・`{issue[0]}`（添字）・
`{0}`／`{}`（位置引数）・`{題名}`（非 ASCII 名）を**すべて取りこぼす**ため、「validate を通ったのに
`format()` で落ちる」テンプレートを作れてしまい、D16-c の P1／P4 担保が破れる。
"""

from __future__ import annotations

import string


def extract_variables(text: str) -> tuple[frozenset[str], list[str]]:
    """本文の `{変数}` を `string.Formatter().parse()` で抽出する（R-19・M-20〜M-22）。

    正規表現を使わない。`{issue.id}`・`{issue[0]}`・`{0}`・`{}`・`{題名}` を取りこぼすと
    「validate を通ったのに `format()` で落ちる」テンプレートを作れてしまう。

    Returns:
        (抽出した名前の集合, 形式そのものの破れの一覧)。**位置引数 `{}` は空の名前 `''`
        として抽出されるので捨てない**（許可 17 に無いので未知変数として落ちる）。
        `{{` `}}` は変数として拾わない。
    """
    names: set[str] = set()
    issues: list[str] = []
    try:
        for _literal, field, spec, conversion in string.Formatter().parse(text):
            if field is None:
                continue
            names.add(field)
            if spec:
                issues.append(f"書式指定は書けない: {{{field}:{spec}}}")
            if conversion is not None:
                issues.append(f"変換は書けない: {{{field}!{conversion}}}")
    except ValueError as exc:
        issues.append(f"本文の波括弧が壊れている: {exc}")
    return frozenset(names), issues
