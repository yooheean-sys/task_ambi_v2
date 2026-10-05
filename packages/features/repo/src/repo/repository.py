"""ローカルリポジトリの作業ツリーへの唯一の入出力（外部ツールアダプタ）。

**契約例外を送出しない**（`service.py` が翻訳する）。パスの閉じ込めは `resolve` の 1 か所で行い、
読み書きはすべて `resolve` を通った実パスに対して行う。git そのもの（commit・push 等）には触れない。
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from repo.exceptions import FsEscapeError, FsGitDirError, FsKindError, FsNotFoundError

GIT_DIR = ".git"
"""git の管理領域。**読取・書込とも禁止**（作業ツリーの一部ではない）。"""

TMP_PREFIX = ".repotmp-"
"""原子的書込の一時ファイルの接頭辞（一覧から除く）。"""

NEW_FILE_MODE = 0o644
"""新規ファイルの権限（既存ファイルの上書きは元の権限を引き継ぐ＝実行ビットを落とさない）。"""


class FsAdapter:
    """作業ツリーの解決・述語・読取・列挙・原子的書込・削除。"""

    def __init__(self, *, excluded_dirs: frozenset[str]) -> None:
        """一覧で刈り込むディレクトリ名を受け取る。"""
        self._excluded_dirs = excluded_dirs | {GIT_DIR}

    def work_tree(self, local_path: str) -> Path | None:
        """`local_path` が git の作業ツリー（直下に `.git` を持つ実在ディレクトリ）なら返す。

        `.git` は通常ディレクトリだが、worktree・submodule ではファイルなので種類を問わない。
        **`realpath` した結果を返さない**（解決は操作のたびに `resolve` が行う）。
        """
        root = Path(local_path)
        if not root.is_absolute() or not root.is_dir():
            return None
        if not (root / GIT_DIR).exists():
            return None
        return root

    def resolve(self, root: Path, rel: str) -> Path:
        """`root/rel` を実体まで解決し、作業ツリーの内側で `.git` を通らないことを確かめる。

        Raises:
            FsEscapeError: 解決先が作業ツリーの外。
            FsGitDirError: 解決先が `.git` の配下。
        """
        real_root = Path(os.path.realpath(root))
        target = Path(os.path.realpath(root / rel))
        try:
            inner = target.relative_to(real_root)
        except ValueError as exc:
            msg = f"作業ツリーの外を指している: {rel}"
            raise FsEscapeError(msg, rel) from exc
        if GIT_DIR in inner.parts:
            msg = f".git の配下を指している: {rel}"
            raise FsGitDirError(msg, rel)
        return target

    def is_file(self, path: Path) -> bool:
        """実在する通常ファイルか。"""
        return path.is_file()

    def is_dir(self, path: Path) -> bool:
        """実在するディレクトリか。"""
        return path.is_dir()

    def read_bytes(self, path: Path, rel: str, limit: int) -> bytes:
        """先頭から `limit + 1` バイトまで読む（超過の判定は呼び手が行う）。

        Raises:
            FsNotFoundError: 実在しない。
            FsKindError: ディレクトリである。
        """
        try:
            with path.open("rb") as handle:
                return handle.read(limit + 1)
        except FileNotFoundError as exc:
            msg = f"ファイルが無い: {rel}"
            raise FsNotFoundError(msg, rel) from exc
        except IsADirectoryError as exc:
            msg = f"ディレクトリである: {rel}"
            raise FsKindError(msg, rel) from exc

    def walk(self, root: Path, base: Path) -> list[str]:
        """`base` 配下のファイルをリポジトリ相対の POSIX 形で列挙する（昇順）。

        除外ディレクトリは降りずに刈り込む。シンボリックリンクのディレクトリは辿らない。
        """
        real_root = Path(os.path.realpath(root))
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in self._excluded_dirs)
            for name in filenames:
                if name.startswith(TMP_PREFIX):
                    continue
                found.append(Path(dirpath, name).relative_to(real_root).as_posix())
        return sorted(found)

    def write_atomic(self, path: Path, rel: str, data: bytes) -> bool:
        """同じディレクトリの一時ファイルに書いて `os.replace` で置き換え、上書きだったかを返す。

        親ディレクトリが無ければ作る（`resolve` を通った実パスの親＝作業ツリーの内側）。

        Raises:
            FsKindError: 書込先がディレクトリ／親の途中がファイル。
        """
        if path.is_dir():
            msg = f"ディレクトリの位置には書けない: {rel}"
            raise FsKindError(msg, rel)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError) as exc:
            msg = f"親の途中がファイルである: {rel}"
            raise FsKindError(msg, rel) from exc
        overwritten = path.is_file()
        mode = stat.S_IMODE(path.stat().st_mode) if overwritten else NEW_FILE_MODE
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=TMP_PREFIX)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            tmp.chmod(mode)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return overwritten

    def delete(self, path: Path, rel: str) -> None:
        """ファイルを 1 つ削除する（ディレクトリは消さない）。

        Raises:
            FsNotFoundError: 実在しない。
            FsKindError: ディレクトリである。
        """
        if path.is_dir():
            msg = f"ディレクトリは削除できない: {rel}"
            raise FsKindError(msg, rel)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            msg = f"ファイルが無い: {rel}"
            raise FsNotFoundError(msg, rel) from exc
