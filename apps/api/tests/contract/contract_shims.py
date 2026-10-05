"""`BD_BIN` に向ける実行可能スクリプト 2 形（FR-021・FR-021a・contracts/bd-shims.md）。

実 `bd` を正常に使う限り原理的に踏めない契約例外 2 種を、**CLI 境界から**故障注入して踏む。
プロダクションコードは 1 行も変えない。

なぜ CLI 境界で注入できるか（現物の根拠）: board の `build()` は `BD_BIN` を `shutil.which`
＋ `os.access(X_OK)` だけで確かめ、**`bd version` を打たない**
（`packages/features/board/src/board/__init__.py` の検査 (d)）。したがって `BD_BIN` に任意の
実行可能スクリプトを向けても `build()` は通る。

効力は当該テストに閉じる（`monkeypatch.setenv("BD_BIN", …)` は fixture の終了で戻る）。
**正常系のテストは実 `bd` をそのまま使う**。
"""

from __future__ import annotations

import shutil
import stat
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SHIM_FILENAME = "bd"
"""board が `BD_BIN` をコマンド名としても扱えるように、名前は `bd` にする。"""

UNAVAILABLE_MESSAGE = "no beads database found"
"""シム (a) が標準エラーへ出す `bd` の不達文言。"""

_ALWAYS_FAILS_TEMPLATE = '''\
#!{python}
"""BD_BIN シム (a): どのサブコマンドでも不達として失敗する（BoardUnavailable を踏む）。"""

import sys

print({message!r}, file=sys.stderr)
sys.exit(1)
'''

_DROPS_ONE_LABEL_TEMPLATE = '''\
#!{python}
"""BD_BIN シム (b): 実 bd へ委譲しつつ `create` の `-l <label>` を 1 組だけ落とす。

実 bd の所在は**生成時に解決した絶対パス**を埋め込む（このスクリプト自身が BD_BIN なので、
環境変数から引くと自分を再帰的に呼ぶ）。`-l` が 1 つも無い呼び出しでは何も落とさない。
"""

import subprocess
import sys

REAL_BD = {real_bd!r}


def drop_one_label(argv):
    """第 1 引数が `create` のときだけ `-l <label>` を 1 組取り除く。"""
    if not argv or argv[0] != "create":
        return argv
    kept = []
    dropped = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if not dropped and token == "-l" and index + 1 < len(argv):
            dropped = True
            index += 2
            continue
        kept.append(token)
        index += 1
    return kept


completed = subprocess.run(  # noqa: S603
    [REAL_BD, *drop_one_label(sys.argv[1:])],
    capture_output=True,
    text=True,
    check=False,
)
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
sys.exit(completed.returncode)
'''


def _install(tmp_path: Path, body: str, *, name: str) -> Path:
    """`tmp_path/<name>/bd` として実行可能スクリプトを置く（リポジトリ内に残さない）。"""
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / SHIM_FILENAME
    shim.write_text(body, encoding="utf-8", newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def make_always_fails_shim(tmp_path: Path) -> Path:
    """シム (a)「常に失敗」を生成する（`BoardUnavailable`＝ IF-01〜22 の 22 件）。

    Returns:
        `BD_BIN` に渡す絶対パス。
    """
    body = _ALWAYS_FAILS_TEMPLATE.format(python=sys.executable, message=UNAVAILABLE_MESSAGE)
    return _install(tmp_path, body, name="shim_unavailable")


def make_drops_one_label_shim(tmp_path: Path, real_bd: str | None = None) -> Path:
    """シム (b)「委譲＋`-l` 1 組落とし」を生成する（`BoardWriteUnverified`＝ IF-11）。

    board の `create` は読み戻し検証で「`set(labels)` の完全一致と `parent` の一致」を見る
    （`service.py` の `_verify_created`）。ラベルが 1 つ足りない状態で読み戻ると spec と
    食い違い `BoardWriteUnverified` になる。

    Args:
        tmp_path: 一時ディレクトリ。
        real_bd: 委譲先の実 `bd`（省略時は `shutil.which("bd")` で解決する）。

    Returns:
        `BD_BIN` に渡す絶対パス。
    """
    resolved = real_bd if real_bd is not None else shutil.which(SHIM_FILENAME)
    if resolved is None:
        msg = "委譲先の実 bd を解決できない（BD_BIN シム (b) は実 bd を必要とする）"
        raise RuntimeError(msg)
    body = _DROPS_ONE_LABEL_TEMPLATE.format(python=sys.executable, real_bd=resolved)
    return _install(tmp_path, body, name="shim_unverified")
