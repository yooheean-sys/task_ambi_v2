"""外部ストアアダプタ（`FsAdapter`）。vault-F01・F03 の列挙・F08 の原子的書込・F09。

正本は `specs/004-vault/contracts/fs-adapter.md`。

原則（同 §1）:

1. **契約例外（`VaultNotFound`／`VaultPathViolation`）を 1 つも送出しない。** 外縁・領域の合否は
   `ResolvedPath` の真偽値で返す。送出するのは `FsError` 系（vault 内部専用）だけである。
2. **判断をしない。**「不在を失敗とするか」「ゲート不合格をどう畳むか」は `service.py` が決める。
3. **状態を持たない。** 保持するのは `vault_root`（構成値）・`VaultConfig`・logger の **3 つだけ**。
   解決結果・stat・ディレクトリ一覧を**インスタンスに溜めない**（憲章 §2・FR-016）。
   `__init__` は §2 の署名どおり `encoding` も受けるが、**保持も参照もしない**（符号化・復号は
   `service.py` の仕事＝ R-13）。
4. **呼び手へ絶対パスを出さない。** `ResolvedPath.real` は `service.py` の同一呼び出しの中でのみ
   使われ、契約の戻り値・ログ・例外本文には現れない（FR-025・FR-060）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import uuid4

from common.kernel.log import LoggerFactory
from vault.exceptions import FsArchiveError, FsIoError, FsNotFoundError
from vault.models import FileStat, ResolvedPath

if TYPE_CHECKING:
    from common.interfaces.vault import VaultArea
    from vault import VaultConfig

TMP_PREFIX = ".vaulttmp-"
"""原子的書込の一時ファイルの接頭辞（コード定数＝ C-2 (2)・R-08）。

**ドット始まり**であることが本質である（既定の除外接頭辞に必ず一致する＝ FR-050）。成果物名を
含めないのは、成果物名が最大 200 バイトあり接尾辞を足すと FS の 255 バイト上限に近づくため。
対応付けは**ログ**で取る（`tmp_name` と `rel_path` を同じ行に載せる）。
"""

ARCHIVE_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
"""退避スタンプの形式（コード定数＝ C-2 (1)・R-07）。UTC・秒精度・`:` を含まない。

`:` を除くのは **FS の制約ではなく**（M-18: drvfs でも `:` は作れてしまう）、Windows 側の
アプリ（Obsidian・同期ソフト）が読めるようにするため（OB-13）。
"""

ARCHIVE_SEQ_LIMIT = 999
"""同一スタンプでの退避の連番の上限（コード定数＝ C-2 (1)・FR-047a）。

M-19 が「同一スタンプの再退避で旧版が黙って上書きされる」ことを実測している。差し戻し後の
再書込は同一秒内に連続しうる正常経路である。
"""

_LOGGER_NAME = "vault.repository"
"""構造化ログの logger 名（FR-059・contracts/fs-adapter.md §7）。"""


def _ident(path: str) -> tuple[int, int]:
    """実体の識別子 `(st_dev, st_ino)`。**文字列比較を使わない**判定の土台（FR-024・R-01）。"""
    stat_result = os.stat(path)
    return (stat_result.st_dev, stat_result.st_ino)


def _utc_stamp() -> str:
    """退避スタンプ（UTC・秒精度）。"""
    return datetime.now(tz=UTC).strftime(ARCHIVE_STAMP_FORMAT)


def _posix_join(*parts: str) -> str:
    """Vault ルート相対パスを `/` で連結する（`real` から逆算しない＝ contracts §4）。"""
    return "/".join(part for part in parts if part not in {"", "."})


class FsAdapter:
    """ファイルシステムへの唯一の到達点（vault-F01・F03・F08・F09）。"""

    def __init__(self, *, vault_root: Path, config: VaultConfig, encoding: str) -> None:
        """構成値だけを保持する（解決結果を溜めない＝ FR-016）。

        Args:
            vault_root: `.env` の `VAULT_ROOT`（**`realpath` して保持しない**＝ R-05）。
            config: 挙動設定（除外接頭辞・退避先名など）。
            encoding: 実効値の解決済み文字コード（決着は `build()` の仕事＝ R-11）。
                **本アダプタは読まない**（下記）。
        """
        self._vault_root = vault_root
        self._config = config
        self._logger = LoggerFactory.get_logger(_LOGGER_NAME)
        # `encoding` は contracts/fs-adapter.md §2 が署名に固定するので受けるが、**保持しない**。
        # FS 側の操作はすべてバイト列で完結し（`read_bytes` は復号せず `write_atomic` は bytes を
        # 受ける）、符号化・復号は例外なく `service.py` が行う（R-13）。保持すると §1 原則 3
        # 「保持するのは 3 つだけ」が崩れ、読まれない状態が 1 つ増える。`build()` の `del deps`
        # と同じ扱いにする。
        del encoding

    # ------------------------------------------------------------------ 解決（vault-F01）

    def resolve(self, *, rel: str, area: VaultArea, area_rels: tuple[str, ...]) -> ResolvedPath:
        """`rel` を vault ルートに結合して解決し、外縁と領域の内側かを**真偽値で**返す。

        `..`・絶対パスの構文事前検査は置かない（R-02）。`join` ＋ `realpath` が正しい種別
        （`outside_root` / `outside_area`）へ落とす。

        Args:
            rel: vault ルート相対の綴り（絶対パス・`..` を含みうる。信用しない）。
            area: どの領域として解決するか。
            area_rels: 宣言された領域根の vault ルート相対パス（複数可）。

        Returns:
            解決結果（`root_ok`／`area_ok` は**真偽値であって例外ではない**）。

        Raises:
            FsIoError: ルートが読めない／途中の段が権限などで読めない。
        """
        root_real = os.path.realpath(self._vault_root)
        rel_norm = PurePosixPath(rel).as_posix()
        try:
            root_id = _ident(root_real)
        except OSError as exc:
            msg = "vault ルートを読めない"
            raise FsIoError(msg, rel="", op="stat") from exc

        target_real = os.path.realpath(os.path.join(root_real, rel))
        root_ok = self._contains(root_id, target_real, rel_norm)
        area_ok = False
        if root_ok:
            for area_rel in area_rels:
                area_id = self._ident_or_none(os.path.realpath(os.path.join(root_real, area_rel)))
                if area_id is not None and self._contains(area_id, target_real, rel_norm):
                    area_ok = True
                    break
        return ResolvedPath(
            rel=rel_norm,
            real=Path(target_real),
            area=area,
            root_ok=root_ok,
            area_ok=area_ok,
        )

    def _contains(self, anchor_id: tuple[int, int], real: str, rel: str) -> bool:
        """`real` が `anchor_id` の実体の内側か（親方向へ 1 段ずつ実体を照合する）。

        判定に文字列比較を使わない（FR-024。理由は実測 M-02・M-03・M-07）。

        Raises:
            FsIoError: 途中の段が権限などで読めない。
        """
        current = Path(real)
        while True:
            try:
                if _ident(str(current)) == anchor_id:
                    return True
            except (FileNotFoundError, NotADirectoryError):
                pass  # 未作成の末尾は「登って」判定する（FR-022）
            except OSError as exc:
                # 権限・I/O は登らない（読めない段を跨いで「内側」と誤判定しないため）
                msg = "パスの包含を判定できない（途中の段を読めない）"
                raise FsIoError(msg, rel=rel, op="stat") from exc
            if current.parent == current:
                return False
            current = current.parent

    def _ident_or_none(self, path: str) -> tuple[int, int] | None:
        """実在しない領域根の識別子は採れない。その候補は**不一致として扱う**（contracts §3）。

        Raises:
            FsIoError: 権限などで読めない（不在と区別する）。
        """
        try:
            return _ident(path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            msg = "宣言された領域根を読めない"
            raise FsIoError(msg, rel="", op="stat") from exc

    # ----------------------------------------------------------- 述語（値で返す・例外にしない）

    def is_dir(self, resolved: ResolvedPath) -> bool:
        """実在するディレクトリか。"""
        return resolved.real.is_dir()

    def is_file(self, resolved: ResolvedPath) -> bool:
        """実在する通常ファイルか。"""
        return resolved.real.is_file()

    # ------------------------------------------------------------------------------- 読取

    def stat_of(self, resolved: ResolvedPath) -> FileStat:
        """`os.stat` を `FileStat` へ写す（`os.stat_result` を service へ漏らさない）。

        Returns:
            サイズと更新時刻の器。

        Raises:
            FsNotFoundError: 実在しない。
            FsIoError: その他の入出力失敗。
        """
        try:
            stat_result = resolved.real.stat()
        except FileNotFoundError as exc:
            msg = "対象が実在しない"
            raise FsNotFoundError(msg, rel=resolved.rel) from exc
        except OSError as exc:
            msg = "対象を stat できない"
            raise FsIoError(msg, rel=resolved.rel, op="stat") from exc
        return FileStat(
            size_bytes=stat_result.st_size,
            mtime=datetime.fromtimestamp(stat_result.st_mtime, tz=UTC),
        )

    def read_bytes(self, resolved: ResolvedPath) -> bytes:
        """バイト列を読む。**復号しない**（`UnicodeDecodeError` の翻訳は service＝ R-13）。

        Returns:
            読んだバイト列。

        Raises:
            FsNotFoundError: 実在しない。
            FsIoError: その他の入出力失敗。
        """
        try:
            data = resolved.real.read_bytes()
        except FileNotFoundError as exc:
            msg = "対象が実在しない"
            raise FsNotFoundError(msg, rel=resolved.rel) from exc
        except OSError as exc:
            msg = "対象を読めない"
            raise FsIoError(msg, rel=resolved.rel, op="read") from exc
        self._logger.debug(
            "vault.read_bytes",
            extra={
                "op": "read",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "bytes": len(data),
            },
        )
        return data

    # ---------------------------------------------------------------- 列挙（vault-F03）

    def walk(
        self, resolved: ResolvedPath, *, prune_rels: tuple[str, ...]
    ) -> list[tuple[ResolvedPath, FileStat]]:
        """領域根の下を再帰列挙する（contracts/fs-adapter.md §4）。

        `prune_rels` は「宣言された他の領域の根」の vault ルート相対パス。**どれを刈るかは
        宣言の解釈**であり、組み立てるのは `service.py` である（§1 原則 2）。照合は
        `ident()` の比較で行い、**名前や綴りで比べない**（R-24）。

        Args:
            resolved: 歩き始める領域根。
            prune_rels: 刈る領域根（実在しない要素は集合に入らないだけで失敗にしない）。

        Returns:
            `(ResolvedPath, FileStat)` の列（**並べ替えはしない**＝順序は service が決める）。

        Raises:
            FsIoError: 列挙の途中で読めなくなった（短い一覧を静かに返さない）。
        """
        prune_ids = self._prune_ids(prune_rels)
        results: list[tuple[ResolvedPath, FileStat]] = []

        def _raise(exc: OSError) -> None:
            msg = "列挙の途中で読めなくなった"
            raise FsIoError(msg, rel=resolved.rel, op="walk") from exc

        for dirpath, dirnames, filenames in os.walk(
            resolved.real, followlinks=False, onerror=_raise
        ):
            dirnames[:] = self._kept_dirs(dirpath, dirnames, prune_ids)
            sub = os.path.relpath(dirpath, str(resolved.real))
            for name in filenames:
                if self._excluded(name):
                    continue
                child_rel = _posix_join(resolved.rel, PurePosixPath(sub).as_posix(), name)
                child = self.resolve(rel=child_rel, area=resolved.area, area_rels=(resolved.rel,))
                if not (child.root_ok and child.area_ok):
                    self._logger.warning(
                        "vault.walk.dropped_outside_area",
                        extra={"op": "walk", "area": str(resolved.area), "rel_path": child_rel},
                    )
                    continue
                results.append((child, self.stat_of(child)))
        self._logger.info(
            "vault.walk",
            extra={
                "op": "walk",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "count": len(results),
            },
        )
        return results

    def _prune_ids(self, prune_rels: tuple[str, ...]) -> set[tuple[int, int]]:
        """枝刈りの照合集合を**冒頭で 1 度だけ**組む（contracts/fs-adapter.md §4）。"""
        root_real = os.path.realpath(self._vault_root)
        ids: set[tuple[int, int]] = set()
        for prune_rel in prune_rels:
            candidate = self._ident_or_none(os.path.realpath(os.path.join(root_real, prune_rel)))
            if candidate is not None:
                ids.add(candidate)
        return ids

    def _kept_dirs(
        self, dirpath: str, dirnames: list[str], prune_ids: set[tuple[int, int]]
    ) -> list[str]:
        """ディレクトリの枝刈り 4 条件（`archive_dir`／除外接頭辞／symlink／`prune_rels`）。"""
        kept: list[str] = []
        for name in dirnames:
            if name == self._config.archive_dir or self._excluded(name):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            identity = self._ident_or_none(full)
            if identity is not None and identity in prune_ids:
                continue
            kept.append(name)
        return kept

    def _excluded(self, name: str) -> bool:
        """`list_exclude_prefixes` のいずれかで始まるか（一時ファイルはここで必ず落ちる）。"""
        return any(name.startswith(prefix) for prefix in self._config.list_exclude_prefixes)

    # ------------------------------------------------------------ 書込（vault-F08・F09）

    def archive(self, resolved: ResolvedPath) -> str:
        """既存の成果物を `<親>/<archive_dir>/<name>.<stamp>` へ**移動**する。

        Args:
            resolved: 退避する成果物（解決済み）。

        Returns:
            退避先の **vault ルート相対**パス。

        Raises:
            FsArchiveError: 親が実在しない／退避先を作れない／連番が尽きた／移動に失敗した。
        """
        parent = resolved.real.parent
        if not parent.is_dir():
            msg = "退避先の親フォルダが実在しない（親を含めて作らない＝ FR-014）"
            raise FsArchiveError(msg, rel=resolved.rel)
        archive_dir = parent / self._config.archive_dir
        try:
            # コード全体で唯一のフォルダ生成。**親を含めて作る指定を付けない**（FR-014・SC-004）
            archive_dir.mkdir(exist_ok=True)
        except OSError as exc:
            msg = "退避先フォルダを作れない"
            raise FsArchiveError(msg, rel=resolved.rel) from exc

        candidate = self._archive_candidate(archive_dir, resolved)
        # **移動の前に**退避先の rel を組む。ここまで来ると退避先が確定しているので、失敗しても
        # `FsArchiveError.archive_rel` に「どこへ移そうとしたか」を載せられる（data-model.md §4）。
        # 手前の 3 つの送出点（親不在・退避先フォルダを作れない・連番枯渇）は退避先が決まって
        # いないので `archive_rel` は `None` のままが正しい。
        archive_rel = _posix_join(
            PurePosixPath(resolved.rel).parent.as_posix(),
            self._config.archive_dir,
            candidate.name,
        )
        try:
            os.replace(resolved.real, candidate)
        except OSError as exc:
            msg = "退避（移動）に失敗した"
            raise FsArchiveError(msg, rel=resolved.rel, archive_rel=archive_rel) from exc
        self._logger.info(
            "vault.archive",
            extra={
                "op": "archive",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "archived_to": archive_rel,
            },
        )
        return archive_rel

    def _archive_candidate(self, archive_dir: Path, resolved: ResolvedPath) -> Path:
        """未使用の退避先名を選ぶ（同一スタンプの衝突は連番で避ける＝ FR-047a）。

        Raises:
            FsArchiveError: 連番が上限（`ARCHIVE_SEQ_LIMIT`）を超えた。
        """
        stamp = _utc_stamp()
        name = resolved.real.name
        candidate = archive_dir / f"{name}.{stamp}"
        sequence = 2
        while candidate.exists():
            if sequence > ARCHIVE_SEQ_LIMIT:
                msg = f"同一スタンプの退避先が尽きた（上限 {ARCHIVE_SEQ_LIMIT}）"
                raise FsArchiveError(msg, rel=resolved.rel)
            candidate = archive_dir / f"{name}.{stamp}-{sequence}"
            sequence += 1
        return candidate

    def write_atomic(self, resolved: ResolvedPath, data: bytes) -> int:
        """同一ディレクトリの一時ファイル経由で原子的に書く（contracts/fs-adapter.md §6）。

        Args:
            resolved: 書込先（解決済み）。
            data: 書き込むバイト列。

        Returns:
            書いたバイト数。

        Raises:
            FsIoError: 一時ファイルの書込・差し替えに失敗した。
        """
        tmp = resolved.real.parent / f"{TMP_PREFIX}{uuid4().hex}"
        try:
            tmp.write_bytes(data)
        except OSError as exc:
            self._cleanup(tmp, resolved.rel)
            msg = "一時ファイルを書けない"
            raise FsIoError(msg, rel=resolved.rel, op="write") from exc
        try:
            os.replace(tmp, resolved.real)
        except OSError as exc:
            self._cleanup(tmp, resolved.rel)
            msg = "一時ファイルを差し替えられない"
            raise FsIoError(msg, rel=resolved.rel, op="replace") from exc
        self._logger.info(
            "vault.write_atomic",
            extra={
                "op": "write",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "bytes": len(data),
                "tmp_name": tmp.name,
            },
        )
        return len(data)

    def _cleanup(self, tmp: Path, rel: str) -> None:
        """一時ファイルを片付ける。**消せなくても元の例外を優先**し、失敗は `warning` に残す。"""
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            self._logger.warning(
                "vault.write_atomic.cleanup_failed",
                extra={"op": "write", "rel_path": rel, "tmp_name": tmp.name},
            )
