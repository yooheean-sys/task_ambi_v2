"""業務ロジック（`VaultService`）。vault-F02〜F08 の判断側。

`VaultReader`（5 メソッド）と `OutcomeWriter`（1 メソッド）を **1 クラス**で実装する（FR-005）。
分けると解決とゲートの規則が 2 つに割れ、「読めるのに書けない」非対称が生まれる。

**引数名は契約が正本**（`001` FR-033a）。1 文字も変えない — 変えると pyright strict の構造的
部分型の適合検査が落ちる。

**このファイルはファイルシステムに直接触れない**（規約 2）。`open`／`Path.read_*`／
`Path.write_*`／`os.*` を 1 つも書かず、到達はすべて `FsAdapter` 経由にする。quickstart S3 が
機械的に検査する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.vault import (
    FileRef,
    FolderCheck,
    VaultArea,
    VaultNotFound,
    VaultPathViolation,
    WrittenPath,
)
from common.kernel.enums import PersonaKey
from common.kernel.log import LoggerFactory
from vault.exceptions import FsNotFoundError
from vault.models import NameGateResult, NameViolation

if TYPE_CHECKING:
    from common.interfaces.settings import ProjectFolders
    from vault import VaultConfig
    from vault.models import FileStat, ResolvedPath
    from vault.repository import FsAdapter

CONTROL_CHAR_MAX = 0x20
"""制御文字とみなす下限（`ord(c) < 0x20`）。コード定数＝ C-2 (3)・name-gate #3。"""

DEL_CHAR = 0x7F
"""制御文字とみなす `DEL`。コード定数＝ C-2 (3)・name-gate #3。"""

TRAILING_FORBIDDEN = ". \t"
"""末尾に置けない文字（name-gate #5・CFG-30）。"""

RESERVED_CODES = frozenset({"reserved_word", "archive_dir_name"})
"""`kind="reserved"` へ畳む規則コード（contracts/name-gate.md §3）。"""

AREA_ORDER = (VaultArea.pj_input, VaultArea.role, VaultArea.outcome)
"""`list_inputs` の並び順の第 1 キー（FR-038）。"""

_SERVICE_LOGGER_NAME = "vault.service"
"""構造化ログの logger 名（FR-059）。"""


def _display(char: str) -> str:
    r"""制御文字を `\x01` の形で見せる（違反文字を明示する＝ FR-029）。"""
    return f"\\x{ord(char):02x}"


def _token(char: str) -> str:
    r"""違反した 1 文字の見せ方（制御文字だけ `\xNN` へ。表示できる文字はそのまま）。"""
    return _display(char) if _is_control(char) else char


def _is_control(char: str) -> bool:
    """制御文字か（name-gate #3。範囲は**コード定数**＝ C-2 (3)）。"""
    return ord(char) < CONTROL_CHAR_MAX or ord(char) == DEL_CHAR


class VaultService:
    """`VaultReader` ＋ `OutcomeWriter` の実装（6 メソッド）。"""

    def __init__(self, adapter: FsAdapter, *, config: VaultConfig, encoding: str) -> None:
        """アダプタ・設定・実効文字コードを保持する。

        Args:
            adapter: ファイルシステムへの唯一の到達点。
            config: 挙動設定（禁止集合・上限・退避先名など）。
            encoding: 実効値の解決済み文字コード（R-11）。
        """
        self._adapter = adapter
        self._config = config
        self._encoding = encoding
        self._logger = LoggerFactory.get_logger(_SERVICE_LOGGER_NAME)

    # ------------------------------------------------------- VaultReader（IF-31〜36）

    def check_project_folders(self, folders: ProjectFolders) -> FolderCheck:
        """フォルダの存在を確認する。不在は例外にせず `FolderCheck.missing` に載せる。

        **フォルダを 1 つも作らない**（FR-014）。外縁違反は不在に倒さない（FR-035・DIFF-05）。
        領域根は自分自身なので `resolve` は**外縁のみ**を見る。

        Args:
            folders: プロジェクトのフォルダ宣言（vault ルート相対）。

        Returns:
            不在の一覧と `ok`（生成点は `_folder_check` の 1 つだけ）。

        Raises:
            VaultPathViolation: 宣言が vault ルートの外を指す（`kind="outside_root"`）。
        """
        missing: list[str] = []
        for area, rel in self._declared_roots(folders):
            resolved = self._adapter.resolve(rel=rel, area=area, area_rels=(rel,))
            self._require_root(resolved)
            if not self._adapter.is_dir(resolved):
                missing.append(resolved.rel)
        result = self._folder_check(missing)
        self._logger.info(
            "vault.check_project_folders",
            extra={"op": "check", "count": len(missing), "result": result.ok},
        )
        return result

    def list_inputs(self, folders: ProjectFolders, role: PersonaKey) -> list[FileRef]:
        """PJ 入力・役割フォルダ・兄弟成果物を列挙する。

        Args:
            folders: プロジェクトのフォルダ宣言。
            role: この実行の役割（宣言に無ければ失敗する）。

        Returns:
            領域（`pj_input`→`role`→`outcome`）→ `rel_path` のコードポイント順（FR-038）。

        Raises:
            VaultNotFound: 役割が宣言に無い／領域フォルダが実在しない（空に倒さない＝ FR-040）。
            VaultPathViolation: パス規則に違反した。
        """
        if role not in folders.role_dirs:
            msg = f"役割がプロジェクトの宣言に無い: {role}"
            # `rel_path` は空文字列が正しい（解決すべきパスが無いので値を捏造しない＝ plan
            # (v)）ため、**どの役割が宣言に無かったか**は `result` に畳んで残す（VD-42）。
            not_found = f"not_found:{role}"
            self._log_not_found("list", rel_path="", area=VaultArea.role, result=not_found)
            raise VaultNotFound(msg, rel_path="", role=role)

        all_roots = tuple(rel for _, rel in self._declared_roots(folders))
        walked: list[tuple[VaultArea, str]] = [
            (VaultArea.pj_input, folders.pj_input_dir),
            (VaultArea.role, folders.role_dirs[role]),
            (VaultArea.outcome, folders.outcome_dir),
        ]
        refs: list[FileRef] = []
        for area, rel in walked:
            resolved = self._adapter.resolve(rel=rel, area=area, area_rels=(rel,))
            self._require_root(resolved)
            if not self._adapter.is_dir(resolved):
                msg = f"領域のフォルダが実在しない: {resolved.rel}"
                self._log_not_found("list", rel_path=resolved.rel, area=area)
                raise VaultNotFound(msg, rel_path=resolved.rel, role=None)
            prune_rels = self._prune_rels(all_roots, resolved.rel)
            for child, stat in self._adapter.walk(resolved, prune_rels=prune_rels):
                refs.append(self._to_file_ref(child, stat))
        refs.sort(key=lambda ref: (AREA_ORDER.index(ref.area), ref.rel_path))
        self._logger.info(
            "vault.list_inputs",
            extra={"op": "list", "count": len(refs), "result": str(role)},
        )
        return refs

    def read_input(self, folders: ProjectFolders, ref: FileRef) -> str:
        """入力ファイルを読む。**`FileRef` を信用せず毎回再解決する**（FR-026）。

        `役割` の領域根は宣言された役割フォルダの**和集合**である（契約が `role` を受け取らない
        ため。FR-026a・C-4）。分離が要る呼び手は宣言を当該実行の役割だけに絞って渡す。

        Args:
            folders: プロジェクトのフォルダ宣言。
            ref: 一覧で得た参照（改竄されていても領域の外へは出られない）。

        Returns:
            復号済みの本文。

        Raises:
            VaultNotFound: 指定のファイルが無い／**領域のフォルダが実在しない**（VD-34。
                `list_inputs` が同じ状況で上げるものと揃える）。
            VaultPathViolation: パス規則に違反した／上限超過／復号できない。
        """
        resolved = self._adapter.resolve(
            rel=ref.rel_path, area=ref.area, area_rels=self._area_rels(folders, ref.area)
        )
        self._require_inside(resolved, folders)
        if not self._adapter.is_file(resolved):
            msg = f"入力ファイルが実在しない: {resolved.rel}"
            self._log_not_found("read", rel_path=ref.rel_path, area=ref.area)
            raise VaultNotFound(msg, rel_path=ref.rel_path, role=None)
        stat = self._adapter.stat_of(resolved)
        limit = self._config.max_outcome_bytes
        if stat.size_bytes > limit:
            msg = f"入力ファイルが上限を超えている: {resolved.rel}"
            detail = f"{resolved.rel}: {stat.size_bytes} bytes > {limit}"
            self._log_violation(
                "read", rel_path=resolved.rel, kind="too_large", detail=detail, area=ref.area
            )
            raise VaultPathViolation(msg, kind="too_large", detail=detail)
        return self._decode(resolved)

    def read_outcome_file(self, folders: ProjectFolders, filename: str) -> str:
        """成果物ファイルを読む。不在は例外で明示する（**空文字列を返さない**＝ FN-13）。

        **サイズ上限を掛けない**（`read_input` とは非対称＝ R-15・VD-20）。掛けると
        `outcome_exists` が `True` なのに読めない状態が生まれ、完了ゲートが壊れる。

        Args:
            folders: プロジェクトのフォルダ宣言。
            filename: 成果物フォルダ直下のファイル名。

        Returns:
            復号済みの本文。

        Raises:
            VaultNotFound: 指定のファイルが無い／**成果物フォルダが実在しない**（VD-34）。
            VaultPathViolation: 名前規則・パス規則に違反した／復号できない。
        """
        resolved = self._resolved_outcome(folders, filename)
        if not self._adapter.is_file(resolved):
            msg = f"成果物ファイルが実在しない: {resolved.rel}"
            self._log_not_found("read", rel_path=resolved.rel, area=VaultArea.outcome)
            raise VaultNotFound(msg, rel_path=resolved.rel, role=None)
        return self._decode(resolved)

    def outcome_exists(self, folders: ProjectFolders, filename: str) -> bool:
        """成果物ファイルの存在を確認する（完了ゲート）。

        名前の違反を **`False` に倒さない**（FR-044）。中身が空のファイルも `True`（FR-043）。

        Args:
            folders: プロジェクトのフォルダ宣言。
            filename: 成果物フォルダ直下のファイル名。

        Returns:
            成果物フォルダ直下に実在するか（`_archive/` 配下は構造上数えられない）。
            **成果物フォルダ自体が無ければ `False`**（VD-34。人間が作るまでは正常状態＝ FR-014）。

        Raises:
            VaultPathViolation: 名前規則・パス規則に違反した。
        """
        try:
            # 不在は**ログを残さずに**受ける（VD-41）。この `VaultNotFound` はここで握り潰され
            # 呼び手に届かないうえ、人間が `outcome/` を作るまでは正常状態（FR-014・SC-004）で
            # あり、完了ゲートは 1 kickoff の中で本メソッドを繰り返し呼ぶ。WARNING を残すと
            # 正常運用のたびに journal が埋まり、US7「事後に追える唯一の手段がログ」の可読性が
            # 落ちる。情報は下の INFO 行（`op="exists"`・`rel_path=<outcome_dir>`・
            # `result=False`）が持つので失われない。
            resolved = self._resolved_outcome(folders, filename, log_not_found=False)
        except VaultNotFound:
            # `_resolved_outcome` で `VaultNotFound` が出る経路は「成果物フォルダが実在しない」
            # の 1 つだけである（名前ゲートと外縁・領域ゲートは `VaultPathViolation` を上げる）。
            # フォルダが無ければ成果物も無いので、完了ゲートには `False` が真である（FR-043・
            # US5）。SC-007 が「不在を偽に倒さない」と定める 3 経路（成果物の読取・入力の一覧・
            # 入力の読取）に存在確認は入っておらず、存在確認に課されているのは**名前の違反**を
            # 偽に倒さないことだけである（その検査は上の `try` の中で先に済んでいる）。
            self._logger.info(
                "vault.outcome_exists",
                extra={
                    "op": "exists",
                    "area": str(VaultArea.outcome),
                    "rel_path": folders.outcome_dir,
                    "result": False,
                },
            )
            return False
        exists = self._adapter.is_file(resolved)
        self._logger.info(
            "vault.outcome_exists",
            extra={
                "op": "exists",
                "area": str(VaultArea.outcome),
                "rel_path": resolved.rel,
                "result": exists,
            },
        )
        return exists

    # ------------------------------------------------------- OutcomeWriter（IF-37）

    def write_outcome(self, folders: ProjectFolders, filename: str, content: str) -> WrittenPath:
        """成果物を書き、通過した実パス（vault ルート相対）を返す。

        ゲート鎖は 名前 → 内容 → resolve → `outcome_dir` の実在 → 退避 → 原子的書込 の 6 段。
        **いずれかで止まったら後の段を行わない**（FR-045）。**上書きしてよいかを判断しない**
        （`overwritten` は観測印として返すだけ＝ P5D-008）。

        Args:
            folders: プロジェクトのフォルダ宣言。
            filename: 成果物フォルダ直下のファイル名。
            content: 本文（空は違反）。

        Returns:
            書込結果（退避が起きていれば `archived_to` に退避先の rel が載る）。

        Raises:
            VaultNotFound: `outcome_dir` が実在しない（R-14・C-3 (3)。フォルダを作らない）。
            VaultPathViolation: 名前・内容・パス規則に違反した。
            FsArchiveError: 退避に失敗した（**契約例外へ翻訳しない**＝ FR-048）。
        """
        self._require_name(filename)  # (1)
        # 内容ゲートは resolve より前（FR-045 の段の順は動かさない）ので、ログに載せられるのは
        # **これから書く綴り**だけである（解決前なので `ResolvedPath.rel` はまだ無い）。
        data = self._check_content(content, rel_path=f"{folders.outcome_dir}/{filename}")  # (2)
        resolved = self._adapter.resolve(  # (3)
            rel=f"{folders.outcome_dir}/{filename}",
            area=VaultArea.outcome,
            area_rels=(folders.outcome_dir,),
        )
        self._require_root(resolved)
        outcome_root = self._adapter.resolve(  # (3')
            rel=folders.outcome_dir, area=VaultArea.outcome, area_rels=(folders.outcome_dir,)
        )
        self._require_root(outcome_root)
        if not self._adapter.is_dir(outcome_root):
            # `outcome_dir` の不在は「領域の外」ではない。既存の契約例外を**その意味どおりに**
            # 使う（R-14・VD-19・C-3 (3)）。領域の判定より先に置くのは、領域根が実在しないと
            # `area_ok` が必ず偽になり `outside_area` が不在を覆い隠すためである。
            # 同じ規則は `_require_inside` → `_missing_area_root` にも置いてある（VD-34 で読取 3
            # 経路へ広げ、VD-35 で 3 分岐にした）。ここはより具体的な文言で**先に**答えるための
            # もので、消しても振る舞いは保たれる（`rel_path` は同じ `outcome_dir` になる）。
            msg = f"成果物フォルダが実在しない: {outcome_root.rel}"
            self._log_not_found("write", rel_path=outcome_root.rel, area=VaultArea.outcome)
            raise VaultNotFound(msg, rel_path=outcome_root.rel, role=None)
        self._require_inside(resolved, folders)  # (3'') 領域

        overwritten = self._adapter.is_file(resolved)
        archived_to = self._adapter.archive(resolved) if overwritten else None  # (4)
        written = self._adapter.write_atomic(resolved, data)  # (5)
        self._logger.info(
            "vault.write_outcome",
            extra={
                "op": "write",
                "area": str(VaultArea.outcome),
                "rel_path": resolved.rel,
                "bytes": written,
                "overwritten": overwritten,
                "archived_to": archived_to,
            },
        )
        return WrittenPath(  # (6)
            rel_path=resolved.rel,
            bytes=written,
            overwritten=overwritten,
            archived_to=archived_to,
        )

    # --------------------------------------------------------------- ゲート・写像（内部）

    def _check_name(self, name: str) -> NameGateResult:
        """ファイル名ゲート（contracts/name-gate.md §1 の 10 規則）。

        **すべて評価して違反を集める**（早期打ち切りしない＝ FR-031）。規則 #7・#8 は
        **新しい設定キーを作らず** `list_exclude_prefixes` と `archive_dir` から導出する（R-10）。

        Args:
            name: 検査する成果物のファイル名。

        Returns:
            触れた規則すべてを持つ結果。
        """
        config = self._config
        violations: list[NameViolation] = []
        if name == "":  # 0
            violations.append(NameViolation(code="empty_name", token=""))
        violations += [
            NameViolation(code="path_separator", token=sep) for sep in ("/", "\\") if sep in name
        ]  # 1
        violations += [
            NameViolation(code="forbidden_char", token=char)
            for char in config.forbidden_chars
            if char in name
        ]  # 2
        violations += [
            NameViolation(code="control_char", token=_display(char))
            for char in name
            if _is_control(char)
        ]  # 3
        # 語幹は**最初のドットより前**。Windows はデバイス名を最初の区切りより前の語で解決する
        # ため、`CON.tar.gz`・`NUL.d.ts` も予約語である（`rsplit` だと素通りする＝ T071）
        stem = name.split(".", 1)[0]
        if stem and any(stem.upper() == word.upper() for word in config.reserved_names):  # 4
            violations.append(NameViolation(code="reserved_word", token=stem))
        if config.forbid_trailing_dot_space and name and name[-1] in TRAILING_FORBIDDEN:  # 5
            violations.append(NameViolation(code="trailing_dot_space", token=_token(name[-1])))
        violations += [
            NameViolation(code="obsidian_char", token=token)
            for token in config.forbidden_obsidian
            if token in name
        ]  # 6
        violations += [
            NameViolation(code="excluded_prefix", token=prefix)
            for prefix in config.list_exclude_prefixes
            if name.startswith(prefix)
        ]  # 7
        if name == config.archive_dir:  # 8
            violations.append(NameViolation(code="archive_dir_name", token=name))
        size = len(name.encode(self._encoding))
        if size > config.max_filename_bytes:  # 9
            token = f"{size} bytes > {config.max_filename_bytes}"
            violations.append(NameViolation(code="too_long", token=token))
        return NameGateResult(ok=not violations, violations=tuple(violations))

    def _check_content(self, content: str, *, rel_path: str) -> bytes:
        """内容ゲート（FR-046。空でない・符号化長 ≤ 上限）。

        Args:
            content: 書き込む本文。
            rel_path: これから書く綴り（**ログ用**。解決前なので綴りであって実パスではない）。

        Returns:
            書き込むバイト列（符号化を 1 度だけ行う）。

        Raises:
            VaultPathViolation: `kind="empty"` または `kind="too_large"`。
        """
        if content == "":
            msg = "成果物の内容が空である"
            detail = "content is empty"
            self._log_violation(
                "write", rel_path=rel_path, kind="empty", detail=detail, area=VaultArea.outcome
            )
            raise VaultPathViolation(msg, kind="empty", detail=detail)
        data = content.encode(self._encoding)
        limit = self._config.max_outcome_bytes
        if len(data) > limit:
            msg = "成果物の内容が上限を超えている"
            detail = f"{len(data)} bytes > {limit}"
            self._log_violation(
                "write", rel_path=rel_path, kind="too_large", detail=detail, area=VaultArea.outcome
            )
            raise VaultPathViolation(msg, kind="too_large", detail=detail)
        return data

    def _to_file_ref(self, resolved: ResolvedPath, stat: FileStat) -> FileRef:
        """`ResolvedPath` ＋ `FileStat` → `FileRef` の写像（**1 関数に閉じる**）。"""
        return FileRef(
            area=resolved.area,
            rel_path=resolved.rel,
            name=resolved.rel.rsplit("/", 1)[-1],
            size_bytes=stat.size_bytes,
            mtime=stat.mtime,
        )

    def _folder_check(self, missing: list[str]) -> FolderCheck:
        """`FolderCheck` の**唯一の生成点**。`ok = not missing` をここでのみ計算する。

        矛盾した組み合わせ（`missing` が空でないのに `ok=True`）を作れる場所を残さない
        （`001` FR-039a・data-model.md §1）。
        """
        return FolderCheck(missing=missing, ok=not missing)

    # ------------------------------------------------------------------------ 補助（内部）

    def _declared_roots(self, folders: ProjectFolders) -> list[tuple[VaultArea, str]]:
        """宣言された全領域の根を、`missing` の並び順（SC-008）で返す。

        `role_dirs` は **`PersonaKey` の列挙宣言順**で並べる（`dict` の挿入順に依存させない）。
        """
        roles = sorted(folders.role_dirs.items(), key=lambda item: list(PersonaKey).index(item[0]))
        return [
            (VaultArea.pj_input, folders.pj_input_dir),
            *[(VaultArea.role, rel) for _, rel in roles],
            (VaultArea.outcome, folders.outcome_dir),
        ]

    def _prune_rels(self, all_roots: tuple[str, ...], current: str) -> tuple[str, ...]:
        """枝刈り集合＝**宣言された全領域の根 ∖ いま歩く根**（FR-037a・R-24）。

        照合そのものは実体の同一性で `FsAdapter` が行う。ここが渡すのは綴りだけである。
        """
        return tuple(rel for rel in all_roots if rel != current)

    def _area_rels(self, folders: ProjectFolders, area: VaultArea) -> tuple[str, ...]:
        """`area` に対応する領域根（`role` は宣言された役割フォルダの**和集合**＝ R-04）。"""
        if area is VaultArea.pj_input:
            return (folders.pj_input_dir,)
        if area is VaultArea.outcome:
            return (folders.outcome_dir,)
        return tuple(folders.role_dirs.values())

    def _missing_area_root(self, folders: ProjectFolders, area: VaultArea) -> str | None:
        """宣言された当該領域の根のうち**実在しない**ものを 1 つ返す（無ければ `None`）。

        3 分岐で見る（VD-35。VD-34 の「1 つでも在れば領域は在る」を差し替えた）:

        1. `root_ok` が偽の根は**読み飛ばす**。実在するが境界の外なだけで「不在」ではない。
           判定は領域ゲート（`outside_root`／`outside_area`）へ委ねる。
        2. ルート内にあって実在しない根が 1 つでもあれば、それを返す。`役割` の領域根は和集合
           （R-04）だが、**対象がどの根の下に居たかは構造的に判別できない** ―― 根が実在しない
           以上、照合する実体が無く、綴りでも比べられない（FR-024）。したがって「1 つでも在れ
           ば領域は在る」ではなく「**実在しない根があれば不在**」に倒す。`role_dirs` が複数な
           のは既定の構成であり、和集合で見ると対象の役割だけが不在の場合を取りこぼす。
        3. どれも実在するなら `None` ＝ 対象が本当に領域の外である。

        宣言が空のときも `None` を返す。宣言の無い領域は「実在しない」のではなく本当に
        「領域の外」だからである。**綴りではなく実体で見る**（FR-024）。
        """
        for rel in self._area_rels(folders, area):
            root = self._adapter.resolve(rel=rel, area=area, area_rels=(rel,))
            if not root.root_ok:
                continue  # 1. 境界の問題であって不在ではない
            if not self._adapter.is_dir(root):
                return root.rel  # 2. 実在しない根
        return None  # 3. どれも実在する

    def _resolved_outcome(
        self, folders: ProjectFolders, filename: str, *, log_not_found: bool = True
    ) -> ResolvedPath:
        """成果物 3 メソッド共通の前段（名前ゲート → resolve → 外縁・領域）。

        Args:
            folders: プロジェクトのフォルダ宣言。
            filename: 成果物フォルダ直下のファイル名。
            log_not_found: 成果物フォルダ不在の `VaultNotFound` を 1 行残すか（VD-41）。

        Raises:
            VaultNotFound: 成果物フォルダが実在しない（VD-34。当該チェーンで `VaultNotFound`
                が出る経路はこの 1 つだけ）。
            VaultPathViolation: 名前規則・パス規則に違反した。
        """
        self._require_name(filename)
        resolved = self._adapter.resolve(
            rel=f"{folders.outcome_dir}/{filename}",
            area=VaultArea.outcome,
            area_rels=(folders.outcome_dir,),
        )
        self._require_inside(resolved, folders, log_not_found=log_not_found)
        return resolved

    def _log_violation(
        self, op: str, *, rel_path: str, kind: str, detail: str, area: VaultArea | None = None
    ) -> None:
        """契約違反の送出を 1 行残す（FR-059「違反のすべてを構造化ログに残す」）。

        **`extra` のキーは contracts/fs-adapter.md §7 の 12 種に収める**（`filename` を含む
        `LogRecord` の予約属性を渡すと実行時に `KeyError` で落ちる＝ R-16）。`detail` は
        「どの規則にどう触れたか」なので `violations` に載せる。**ファイルの本文は載せない**
        （FR-060。`detail` に本文が混ざる経路は無い ―― 載るのは規則名・バイト数・位置だけ）。

        Args:
            op: 操作名（`list`・`read`・`write`・`resolve` のいずれか）。
            rel_path: 対象の vault ルート相対パス。
            kind: 契約 `VaultPathViolation.kind`。
            detail: 違反の詳細（例外の `detail` と同じもの）。
            area: 分かっていれば領域（呼び出し元で決まらない送出点が将来出たら省ける）。
        """
        extra: dict[str, object] = {
            "op": op,
            "rel_path": rel_path,
            "kind": kind,
            "violations": detail,
        }
        if area is not None:
            extra["area"] = str(area)
        self._logger.warning("vault.violation", extra=extra)

    def _log_not_found(
        self, op: str, *, rel_path: str, area: VaultArea | None = None, result: str = "not_found"
    ) -> None:
        """不在の送出を 1 行残す（FR-059）。

        `VaultNotFound` は `kind` を持たない（種別の語彙は `VaultPathViolation` 側にしかない）
        ので、**`result` に `"not_found"` を置いて**違反の行と見分けられるようにする。キーは
        `_log_violation` と同じく §7 の 12 種に収める。

        Args:
            op: 操作名。
            rel_path: 不在だった対象の vault ルート相対パス（役割未宣言のときは空文字列）。
            area: 分かっていれば領域。
            result: `result` に置く値。**`"not_found"` で始めること**（違反の行と見分ける印を
                前方一致で残す）。`rel_path` に載せられない識別子を持つ経路だけが
                `"not_found:<値>"` を渡す ―― §7 の 12 種を増やさずに「どれが無かったか」を
                journal へ残すための口である（VD-42）。
        """
        extra: dict[str, object] = {"op": op, "rel_path": rel_path, "result": result}
        if area is not None:
            extra["area"] = str(area)
        self._logger.warning("vault.not_found", extra=extra)

    def _require_name(self, filename: str) -> None:
        """名前ゲートの結果を契約 `kind` へ畳んで送出する（contracts/name-gate.md §3）。

        Raises:
            VaultPathViolation: `kind="reserved"`（`reserved` 系を含む）または
                `kind="forbidden_name"`。
        """
        gate = self._check_name(filename)
        if gate.ok:
            return
        kind = (
            "reserved"
            if any(violation.code in RESERVED_CODES for violation in gate.violations)
            else "forbidden_name"
        )
        detail = "; ".join(f"{v.code}: {v.token}" for v in gate.violations)
        self._logger.warning(
            "vault.name_gate.rejected",
            extra={"op": "name_gate", "kind": kind, "violations": detail},
        )
        msg = f"成果物のファイル名が規則に違反している: {detail}"
        raise VaultPathViolation(msg, kind=kind, detail=detail)

    def _require_root(self, resolved: ResolvedPath) -> None:
        """外縁ゲート（6 メソッド全部の第 1 段）。

        Raises:
            VaultPathViolation: `kind="outside_root"`。
        """
        if resolved.root_ok:
            return
        detail = f"{resolved.rel}: vault ルートの外を指した"
        self._logger.warning(
            "vault.boundary.outside_root",
            extra={
                "op": "resolve",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "kind": "outside_root",
            },
        )
        msg = f"vault ルートの外を指している: {resolved.rel}"
        raise VaultPathViolation(msg, kind="outside_root", detail=detail)

    def _require_inside(
        self, resolved: ResolvedPath, folders: ProjectFolders, *, log_not_found: bool = True
    ) -> None:
        """外縁 → 領域の実在 → 領域の順に見る（`outside_root` が `outside_area` に先行する）。

        Args:
            resolved: 解決済みのパス。
            folders: プロジェクトのフォルダ宣言。
            log_not_found: 領域根不在の `VaultNotFound` を 1 行残すか（既定は残す）。**呼び手に
                届く送出だけを残す**ため、その場で握り潰す `outcome_exists` だけが偽を渡す
                （VD-41）。外縁・領域の**違反**のログはこの口では止まらない。

        Raises:
            VaultNotFound: 宣言された領域根に実在しないものがある（VD-34・VD-35）。
            VaultPathViolation: `kind="outside_root"` または `kind="outside_area"`。
        """
        self._require_root(resolved)
        if resolved.area_ok:
            return
        missing = self._missing_area_root(folders, resolved.area)
        if missing is not None:
            # 領域根が実在しないと識別子が採れず `area_ok` は**必ず**偽になる。ここで分けないと
            # `outside_area` が不在を覆い隠し、呼び手には「自分がパス規則を破った」という偽の
            # 理由が届く（VD-19 が退けた「意味の捏造」／VD-25 が `write_outcome` で塞いだのと
            # 同じ穴。VD-34 で 3 メソッドへ広げ、VD-35 で判定を 3 分岐にした）。人間がフォルダ
            # を作るまでは正常状態である（FR-014）ため、この経路は通常運用で踏む。**通常運用で
            # 踏むからこそ、その場で握り潰す呼び手では 1 行も残さない**（VD-41）。
            msg = f"領域のフォルダが実在しない: {missing}"
            if log_not_found:
                self._log_not_found("resolve", rel_path=missing, area=resolved.area)
            raise VaultNotFound(msg, rel_path=missing, role=None)
        roots = ", ".join(self._area_rels(folders, resolved.area))
        detail = f"{resolved.rel}: 領域 {resolved.area} の外を指した（宣言された領域根: {roots}）"
        self._logger.warning(
            "vault.boundary.outside_area",
            extra={
                "op": "resolve",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "kind": "outside_area",
            },
        )
        msg = f"宣言された領域の外を指している: {resolved.rel}"
        raise VaultPathViolation(msg, kind="outside_area", detail=detail)

    def _decode(self, resolved: ResolvedPath) -> str:
        """バイト列を復号する。**復号できたところまでの文字列を返さない**（FR-041）。

        Raises:
            VaultNotFound: 読む直前に消えた。
            VaultPathViolation: `kind="not_text"`。
        """
        try:
            data = self._adapter.read_bytes(resolved)
        except FsNotFoundError as exc:
            msg = f"ファイルが実在しない: {resolved.rel}"
            self._log_not_found("read", rel_path=resolved.rel, area=resolved.area)
            raise VaultNotFound(msg, rel_path=resolved.rel, role=None, cause=exc) from exc
        try:
            text = data.decode(self._encoding)
        except UnicodeDecodeError as exc:
            detail = f"{resolved.rel}: {self._encoding} で復号できない（位置 {exc.start}）"
            msg = f"テキストとして読めない: {resolved.rel}"
            # `detail` に載るのは文字コード名と位置だけで、**復号できなかったバイト列そのものは
            # 載せない**（FR-060: ファイルの本文をログへ流さない）。
            self._log_violation(
                "read", rel_path=resolved.rel, kind="not_text", detail=detail, area=resolved.area
            )
            raise VaultPathViolation(msg, kind="not_text", detail=detail, cause=exc) from exc
        self._logger.info(
            "vault.read",
            extra={
                "op": "read",
                "area": str(resolved.area),
                "rel_path": resolved.rel,
                "bytes": len(data),
            },
        )
        return text
