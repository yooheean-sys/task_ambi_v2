"""外部ツール bd が PATH 上に存在することの確認（FR-025・SC-007・US5 AC3）。

導入そのものは本 feature の範囲外（リポジトリ外の環境整備）。ここで担うのは**確認と記録**。
bd を持たない環境で土台の検証だけを回す場合は `uv run pytest -m "not requires_bd"`。
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.mark.requires_bd
def test_bd_version_is_readable(bd_bin: str) -> None:
    result = subprocess.run(  # noqa: S603  fixture が解決した実行ファイルのみを起動する
        [bd_bin, "version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"`{bd_bin} version` が異常終了した（rc={result.returncode}）。\n"
        f"  stdout: {result.stdout.strip()}\n"
        f"  stderr: {result.stderr.strip()}"
    )
    output = (result.stdout + result.stderr).strip()
    assert output, "bd version が版文字列を返さない（版の記録ができない）"
    assert "version" in output.lower(), f"版文字列に見えない出力: {output!r}"
