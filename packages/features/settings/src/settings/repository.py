"""領域 A の生 I/O と雛形生成（外部ツールアダプタ）。contracts/area-a-loader.md §2・§4・§5。

**本ファイルは生の構造だけを返す**。写像モデルも `ValidationProblem` も作らず、契約例外
（`ProjectNotFound`／`MemberNotFound`／`UnknownCommand`）を 1 つも送出しない（§1 原則 1・2）。
検査は `service/validate.py`、写像は `service/loader.py` が行う。

**読取は build 時だけ**（P5D-016・FR-014）。**書込は雛形生成だけ**（FR-015）。改名・削除・追記の
経路を前半には置かない。文字コードは常に `utf-8`（BOM・CRLF は透過＝ M-17・M-18）。

**casbin の import は本ファイルだけ**に閉じる（CLAUDE.md「repository.py ＝ 外部ツールアダプタ」・
憲章 §2「外部ツールは公約インターフェース経由」）。casbin は型スタブを持たないため、生の
`Enforcer` は `new_enforcer()` の内側で `PolicyEnforcer`（`models.py`）へ写し、以後は
settings のどこにも casbin の型が現れない（FR-010・SC-012）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import casbin  # pyright: ignore[reportMissingTypeStubs]  # casbin 1.43.0 は py.typed を持たない。境界は new_enforcer() の 1 箇所に閉じる
import yaml  # pyright: ignore[reportMissingTypeStubs]  # PyYAML 6.0.3 は py.typed を持たない。境界は _load_mapping() の 1 箇所に閉じる

from common.kernel.log import LoggerFactory
from settings.models import PolicyEnforcer

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterator, Mapping, Sequence

GLOBAL_DIR = "global"
"""共通側 7 ファイルの置き場（contracts/area-a-loader.md §2）。"""

PROJECTS_DIR = "projects"
"""プロジェクトごと 6 ファイルの置き場。**直下のディレクトリだけ**を PJ とみなす。"""

ARCHIVE_DIR = "_archive"
"""解除したプロジェクトの退避先（FR-030。**掃除の仕組みを持たない**＝ FR-032）。"""

MODEL_CONF_REL = f"{GLOBAL_DIR}/model.conf"
GLOBAL_AGENTS_REL = f"{GLOBAL_DIR}/agents.yaml"
GLOBAL_TASKS_REL = f"{GLOBAL_DIR}/tasks.yaml"
LLM_PROFILES_REL = f"{GLOBAL_DIR}/llm_profiles.yaml"
LIMITS_REL = f"{GLOBAL_DIR}/limits.yaml"
COMMANDS_REL = f"{GLOBAL_DIR}/commands.yaml"
HUMANS_REL = f"{GLOBAL_DIR}/humans.yaml"

EMPTY_LLM_PROFILES = "profiles: {}\ndefault: null\n"
"""`llm_profiles.yaml` の空の骨格（R-17。既定プロファイルを最初から立てない）。"""

EMPTY_COMMANDS = "commands: {}\n"
"""`commands.yaml` の空の骨格（FR-016 (d)）。"""

EMPTY_HUMANS = "human_actors: []\n"
"""`humans.yaml` の空の骨格（FR-016 (d)・CFG-81 は「必須」）。"""

YamlError = yaml.YAMLError
"""YAML の構文誤り（重複キー・トップレベルが mapping でないものを含む）。

PyYAML の型名を settings の他ファイルへ出さないための別名である（境界は本ファイルに閉じる）。
"""

_MERGE_TAG = "tag:yaml.org,2002:merge"
"""merge key `<<` のタグ。重複キー検出の対象から外す（Y-9 を壊さないため）。"""


class _DuplicateKeySafeLoader(yaml.SafeLoader):
    """同一キーの重複を**行・列つきで**送出する `SafeLoader`（R-14・M-14）。

    素の `yaml.safe_load` は重複を**黙って後勝ちで落とす**。人間が直接編集する領域 A
    （`x3_notes.md` §1）で綴り重複が片方だけ効く状態は、これが無いと検出経路が 0 になる。

    重複の判定は**キーノードの生の値**で行う。構築済みオブジェクトで比べると merge key
    `<<` が「タグの構築子が無い」で落ち、Y-9（anchor／alias／merge を通す）が壊れる。
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        """重複キーを検出してから既定の構築へ委ねる。"""
        seen: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, yaml.ScalarNode) or key_node.tag == _MERGE_TAG:
                continue
            raw = cast("str", key_node.value)
            if raw in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {raw!r}",
                    key_node.start_mark,
                )
            seen.add(raw)
        return super().construct_mapping(node, deep=deep)


def load_yaml_mapping(text: str, rel: str) -> dict[str, object]:
    """YAML 文字列をトップレベル mapping として読む（Y-1〜Y-5・Y-9）。

    Args:
        text: ファイルの本文。
        rel: 領域 A ルート相対のパス（失敗メッセージ用）。

    Returns:
        トップレベルの mapping。**空ファイル・値なしの `None` は `{}` に正規化する**（M-13）。

    Raises:
        yaml.YAMLError: 構文誤り・重複キー・**トップレベルが mapping でない**（Y-2・M-19）。
    """
    data = cast("object", yaml.load(text, Loader=_DuplicateKeySafeLoader))  # noqa: S506
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{rel}: トップレベルが mapping でない（{type(data).__name__}）"
        raise yaml.YAMLError(msg)
    return cast("dict[str, object]", data)


def new_enforcer(model_conf: Path, policy_csv: Path) -> PolicyEnforcer:
    """判定器を組む（contracts/authorizer.md §2。**パスで渡す**＝自前の構文解析を持たない）。

    **casbin の生の型が外へ出る唯一の関門**である。戻りは `PolicyEnforcer`（settings 内の
    構造的部分型）で、以後 casbin の型は settings のどこにも現れない。

    Args:
        model_conf: `global/model.conf` の絶対パス。
        policy_csv: `projects/<key>/policy.csv` の絶対パス。

    Returns:
        構築済みの判定器。**3 つの担保（`service/loader.py`）はまだ適用していない**。

    Raises:
        RuntimeError: casbin が構築に失敗した（ファイル不在・モデルの読取失敗）。
    """
    raw = cast("object", casbin.Enforcer(str(model_conf), str(policy_csv)))
    return cast(PolicyEnforcer, raw)


class AreaAReader:
    """領域 A の読取（生の構造だけを返す）。contracts/area-a-loader.md §4。"""

    def __init__(self, root: Path) -> None:
        """領域 A のルートを受け取る（`SETTINGS_ROOT`。実在確認は build の (b) が済ませる）。"""
        self._root = root
        self._logger = LoggerFactory.get_logger("settings.repository")

    @property
    def root(self) -> Path:
        """領域 A のルート。"""
        return self._root

    def path_of(self, rel: str) -> Path:
        """領域 A ルート相対のパスを絶対パスにする。"""
        return self._root / rel

    def exists(self, rel: str) -> bool:
        """領域 A ルート相対のファイルが実在するか。"""
        return (self._root / rel).is_file()

    def model_conf_path(self) -> Path:
        """`global/model.conf` の絶対パス（casbin にパスで渡す＝ FR-048）。"""
        return self._root / MODEL_CONF_REL

    def policy_csv_path(self, key: str) -> Path:
        """`projects/<key>/policy.csv` の絶対パス（同上）。"""
        return self._root / PROJECTS_DIR / key / "policy.csv"

    # -- 共通側 7（`limits.yaml` だけが任意） ------------------------------------------

    def read_global_agents(self) -> dict[str, object]:
        """`global/agents.yaml`（CFG-69 必須）。"""
        return self._require_mapping(GLOBAL_AGENTS_REL)

    def read_global_tasks(self) -> dict[str, object]:
        """`global/tasks.yaml`（CFG-71 必須）。"""
        return self._require_mapping(GLOBAL_TASKS_REL)

    def read_llm_profiles(self) -> dict[str, object]:
        """`global/llm_profiles.yaml`（CFG-77 必須。雛形が在るので不在にならない）。"""
        return self._require_mapping(LLM_PROFILES_REL)

    def read_limits(self) -> dict[str, object] | None:
        """`global/limits.yaml`（CFG-78 **任意**）。不在なら `None`（既定はモジュール設定）。"""
        return self._optional_mapping(LIMITS_REL)

    def read_commands(self) -> dict[str, object]:
        """`global/commands.yaml`（CFG-79。雛形が在るので不在にならない）。"""
        return self._require_mapping(COMMANDS_REL)

    def read_humans(self) -> dict[str, object]:
        """`global/humans.yaml`（CFG-81 必須。不在は「人が消した」状態＝ SD-33）。"""
        return self._require_mapping(HUMANS_REL)

    # -- PJ ごと 6 ---------------------------------------------------------------------

    def list_project_keys(self) -> tuple[str, ...]:
        """`projects/` 直下の**ディレクトリだけ**を PJ とみなす。

        ドット始まりの名前は無視し、**再帰しない**（contracts/area-a-loader.md §2）。
        """
        base = self._root / PROJECTS_DIR
        if not base.is_dir():
            return ()
        return tuple(sorted(entry.name for entry in self._entries(base)))

    @staticmethod
    def _entries(base: Path) -> Iterator[Path]:
        for entry in base.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                yield entry

    def project_rel(self, key: str, name: str) -> str:
        """PJ ごとのファイルの領域 A ルート相対パス。"""
        return f"{PROJECTS_DIR}/{key}/{name}"

    def read_project(self, key: str) -> dict[str, object]:
        """`projects/<key>/project.yaml`（CFG-73 PJ ごと必須）。"""
        return self._require_mapping(self.project_rel(key, "project.yaml"))

    def read_project_agents(self, key: str) -> dict[str, object]:
        """`projects/<key>/agents.yaml`（CFG-70 PJ ごと必須。メンバ 0 可）。"""
        return self._require_mapping(self.project_rel(key, "agents.yaml"))

    def read_project_tasks(self, key: str) -> dict[str, object] | None:
        """`projects/<key>/tasks.yaml`（CFG-72 **任意**）。不在なら `None`。"""
        return self._optional_mapping(self.project_rel(key, "tasks.yaml"))

    def read_rules(self, key: str) -> str | None:
        """`projects/<key>/rules.md`（CFG-82 **任意**）。不在なら `None`。読み手が空文字にする。"""
        path = self._root / self.project_rel(key, "rules.md")
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def read_hooks(self, key: str) -> dict[str, object] | None:
        """`projects/<key>/hooks.yaml`（CFG-80 **任意**）。不在なら `None`。"""
        return self._optional_mapping(self.project_rel(key, "hooks.yaml"))

    def read_policy(self, key: str) -> tuple[tuple[str, ...], ...]:
        """`projects/<key>/policy.csv` の行の写し（CFG-75 PJ ごと必須）。

        **自前で CSV を構文解析しない**（FR-048）。行は判定器の `get_policy()`／
        `get_grouping_policy()` から採る（M-09。空白は除去され、`#` 始まりと空行は
        読み飛ばされる。**引用符は除去されない**ので V-21 が孤児として拾う）。

        判定器の構築とは**別経路**である（contracts/area-a-loader.md §4）。スナップショットに
        載る判定器は `service/loader.py` が別に組み、3 つの担保を掛ける。

        Returns:
            先頭要素が `"p"`／`"g"` の区分である行の並び（区分と列を 1 つの型で運ぶ）。

        Raises:
            RuntimeError: 判定器を構築できない（`policy.csv`／`model.conf` の不在等）。
        """
        enforcer = new_enforcer(self.model_conf_path(), self.policy_csv_path(key))
        rows: list[tuple[str, ...]] = [("p", *row) for row in enforcer.get_policy()]
        rows.extend(("g", *row) for row in enforcer.get_grouping_policy())
        return tuple(rows)

    # -- 内部 --------------------------------------------------------------------------

    def _require_mapping(self, rel: str) -> dict[str, object]:
        """必須ファイルを mapping として読む。

        Raises:
            OSError: 実在しない（(d2) の必須ファイル不在＝ R-09・R-10）。
            yaml.YAMLError: 構文誤り・重複キー・トップレベルが mapping でない。
        """
        path = self._root / rel
        return load_yaml_mapping(path.read_text(encoding="utf-8"), rel)

    def _optional_mapping(self, rel: str) -> dict[str, object] | None:
        """任意ファイルを mapping として読む。不在なら `None`（**既定に倒さない**判断は読み手）。

        Raises:
            yaml.YAMLError: 構文誤り・重複キー・トップレベルが mapping でない。
        """
        path = self._root / rel
        if not path.is_file():
            return None
        return load_yaml_mapping(path.read_text(encoding="utf-8"), rel)


class AreaAScaffolder:
    """共通側の雛形生成（contracts/area-a-loader.md §5・FR-016・FR-018）。

    **対象は `global/` だけ**である。PJ ごとのファイルは前半では生成しない（登録は F11＝後半）。
    `global/limits.yaml` も生成しない（生成すると CFG-39 の ③ 段が永久に効かなくなる）。
    """

    def __init__(self, root: Path, defaults_dir: Path) -> None:
        """領域 A のルートと同梱既定の置き場を受け取る（`defaults_path()` は呼び手が解決する）。"""
        self._root = root
        self._defaults = defaults_dir
        self._logger = LoggerFactory.get_logger("settings.repository")

    def ensure(self) -> tuple[str, ...]:
        """不在の共通側ファイルだけを生成する。

        Returns:
            生成した領域 A ルート相対のパス（生成が 0 件なら空タプル）。

        Raises:
            OSError: 生成に失敗した（呼び手が (d1) の `SettingsAreaError` に翻訳する）。
        """
        (self._root / GLOBAL_DIR).mkdir(exist_ok=True)
        (self._root / PROJECTS_DIR).mkdir(exist_ok=True)

        created: list[str] = []
        for rel, content in self._planned():
            if self._write_if_absent(rel, content):
                created.append(rel)
        return tuple(created)

    def _planned(self) -> tuple[tuple[str, str], ...]:
        """生成物 6 件（`defaults/` の写し 3 ＋ 空の骨格 3）。"""
        return (
            (MODEL_CONF_REL, self._default_text("model.conf")),
            (GLOBAL_AGENTS_REL, self._default_text("agents.yaml")),
            (GLOBAL_TASKS_REL, self._default_text("tasks.yaml")),
            (LLM_PROFILES_REL, EMPTY_LLM_PROFILES),
            (COMMANDS_REL, EMPTY_COMMANDS),
            (HUMANS_REL, EMPTY_HUMANS),
        )

    def _default_text(self, name: str) -> str:
        """同梱既定をテキストとして読む（生成物の改行が LF に揃う＝ M-25・M-26）。"""
        return (self._defaults / name).read_text(encoding="utf-8")

    def _write_if_absent(self, rel: str, content: str) -> bool:
        """不在のときだけ原子的に書く（M-23〜M-26）。

        `os.replace` は既存を**黙って上書きする**ため、この `exists()` の分岐が
        FR-016（既存を上書きしない）の**唯一の防壁**である（M-24）。単一プロセスの起動時
        1 回の生成なので競合は考えなくてよい。

        Returns:
            生成したなら `True`、既存だったなら `False`。
        """
        target = self._root / rel
        if target.exists():
            return False
        handle, name = tempfile.mkstemp(dir=str(target.parent))
        temporary = Path(name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        self._logger.info("settings.scaffold.created", extra={"rel": rel})
        return True


# --------------------------------------------------------------------------------------
# 書込側（後半。data-model.md §6・contracts/save-pipeline.md §3）
# --------------------------------------------------------------------------------------


class _BlockScalarDumper(yaml.SafeDumper):
    """改行を含む文字列を `|` で書く `SafeDumper`（D26）。

    tasks.yaml の `description`・`expected_output` は `|` で書く規約（Y-6・M-15 (b)）なので、
    API で保存しても書式を崩さない。`|` で表せない文字列（行末の空白・タブ・CR を含む等）は
    PyYAML の emitter が自動で引用符付きに落とすので、値は変わらない。
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """文字列を YAML のスカラーにする（改行を含むときだけ `style="|"` を指定する）。

    Args:
        dumper: 書き出し中の dumper。
        data: 文字列。

    Returns:
        スカラーのノード。
    """
    style = "|" if "\n" in data else None
    # typeshed の PyYAML スタブは represent_scalar の value を注釈していない（渡すのは str だけ）
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)  # pyright: ignore[reportUnknownMemberType]


_BlockScalarDumper.add_representer(str, _represent_str)


def dump_yaml_mapping(data: Mapping[str, object]) -> str:
    """トップレベル mapping を YAML テキストにする（FR-028・D-g・D26）。

    3 つの指定は意味を持つ —— `allow_unicode=True`（日本語がエスケープされない）・
    `sort_keys=False`（宣言の並びを保ち差分を読みやすくする）・`default_flow_style=False`
    （入れ子が `{...}` の 1 行にならない）。改行を含む文字列は `|` で書く（D26。
    `_BlockScalarDumper`）。⚠ 元のファイルのコメントは保たれない（mapping から書き直すため）。

    Args:
        data: 書き出す mapping。

    Returns:
        `load_yaml_mapping` で読み直して**同値になる**テキスト（S-11 が表駆動で確かめる）。
    """
    return yaml.dump(
        dict(data),
        Dumper=_BlockScalarDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def dump_policy_rows(rows: Sequence[Sequence[str]]) -> str:
    """権限表の行を CSV テキストにする（FR-029・D-h）。**引用符を付けない**。

    **`csv` モジュールを使わない** —— `csv.writer` は必要に応じて引用符を付け、`QUOTE_NONE` は
    区切り文字を含む値で例外を投げる。引用符を付けると casbin がそれを剥がさないため
    **V-21 が孤児として拒否する**（M-09）。値にカンマが混じった行は**候補の判定器で列数違反
    として捕まる**（`build_enforcer` の担保 2）ので、ここで先回りしない。

    Args:
        rows: 1 行分のフィールド列（先頭は `p`／`g` の区分）。

    Returns:
        `", ".join(fields)` を 1 行ずつ並べたテキスト。
    """
    return "".join(f"{', '.join(row)}\n" for row in rows)


def dump_text(body: str) -> str:
    """Markdown 本文を書き出す形にする（末尾に改行が無ければ 1 つ足す）。

    Args:
        body: 本文（**空文字も可**＝ FR-065）。

    Returns:
        末尾が改行のテキスト。**空文字はそのまま空文字**（無い本文に改行を作らない）。
    """
    if not body or body.endswith("\n"):
        return body
    return f"{body}\n"


class AreaAWriter:
    """領域 A への**実行時**の書込（data-model §6-1）。**唯一の入口**である（FR-026・SC-024）。

    **`AreaAScaffolder` とは別クラス**にする（R-10・N-02・F-15）。`AreaAScaffolder` の
    `exists()` の分岐は「既存を上書きしない」ための唯一の防壁であり、上書きする書込を同じ
    入口に混ぜると防壁が緩む。
    """

    def __init__(self, root: Path) -> None:
        """領域 A のルートを受け取る（`AreaAReader` と同じ値）。"""
        self._root = root
        self._logger = LoggerFactory.get_logger("settings.repository")

    def stage(self, rel: str, text: str) -> Path:
        """置換先と**同じディレクトリ**に一時ファイルを作って本文を書く（2 段書込の第 1 段）。

        形は `AreaAScaffolder._write_if_absent` の原型を写す（R-09）が、**`exists()` の分岐は
        持たない** —— こちらは既存を上書きする書込であり、防壁を同じ入口に混ぜない（D-f）。

        Args:
            rel: 領域 A ルート相対のパス。
            text: 直列化済みの本文。

        Returns:
            書き終えた一時ファイルの絶対パス（**置換はしていない**）。

        Raises:
            OSError: 置換先の親ディレクトリが無い／書けない（新規 PJ は段 3-0 が先に作る）。
        """
        target = self._root / rel
        handle, name = tempfile.mkstemp(dir=str(target.parent))
        temporary = Path(name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        return temporary

    def commit(self, rel: str, temporary: Path) -> None:
        """一時ファイルを正本へ置換する（第 2 段）。"""
        os.replace(temporary, self._root / rel)
        self._logger.info("settings.write.committed", extra={"rel": rel})

    def discard(self, temporary: Path) -> None:
        """一時ファイルを消す（不在でも失敗しない＝ `finally` から二重に呼ばれても壊れない）。"""
        temporary.unlink(missing_ok=True)

    def remove(self, rel: str) -> None:
        """正本を消す（PJ の `tasks.yaml` の解除。不在なら何もしない）。"""
        (self._root / rel).unlink(missing_ok=True)
        self._logger.info("settings.write.removed", extra={"rel": rel})

    def archive_project(self, key: str, stamp: str) -> str:
        """`projects/<key>/` を `_archive/projects/<key>.<stamp>/` へ **rename** する。

        **フォルダ実体（保管庫側）は 1 つも触らない**（FR-007・SC-010）。掃除の仕組みも
        持たない（FR-032）。

        Args:
            key: プロジェクトキー。
            stamp: UTC の時刻印。

        Returns:
            退避先の領域 A ルート相対パス。

        Raises:
            FileExistsError: 退避先が既に在る（**上書きしない**＝ FR-031）。
        """
        rel = f"{ARCHIVE_DIR}/{PROJECTS_DIR}/{key}.{stamp}"
        destination = self._root / rel
        # `os.rename` は「空でない既存ディレクトリ」なら落ちるが、**空なら黙って置き換わる**
        # 環境がある。上書きしないことを明示的な検査で担保する（FR-031）。
        if destination.exists():
            msg = f"退避先が既に存在する（上書きしない）: {destination}"
            raise FileExistsError(msg)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(self._root / PROJECTS_DIR / key, destination)
        self._logger.info("settings.write.archived", extra={"rel": rel})
        return rel

    def ensure_project_dir(self, key: str) -> None:
        """`projects/<key>/` を作る（段 3-0。**`exist_ok=False`**＝ plan D-j）。

        重複キーは `key_rule` が段 1 で落とすのでここには到達しない。

        Raises:
            FileExistsError: 既に在る（内部の不変条件の破れ）。
        """
        (self._root / PROJECTS_DIR / key).mkdir(parents=False, exist_ok=False)

    def discard_project_dir(self, key: str) -> None:
        """段 3-0 で作ったディレクトリを**空のときだけ**消す（不在なら何もしない）。

        空のまま残すと `AreaAReader.list_project_keys` が**無条件に**プロジェクトとみなすため、
        定義ファイルの無いディレクトリが**次回起動を中断させる**（SC-004 (d)・plan D-j）。
        中身が在るとき（＝置換が始まっていた）は**消さない**。
        """
        target = self._root / PROJECTS_DIR / key
        if not target.is_dir():
            return
        if any(target.iterdir()):
            return
        target.rmdir()
