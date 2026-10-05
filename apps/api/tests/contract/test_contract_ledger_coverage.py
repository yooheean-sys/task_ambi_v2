"""台帳の健全性・IF 被覆の機械検査・止め木ヘルパの自己テスト（US1・SC-001・SC-002・SC-009）。

module 大域の **`CONTRACT_META = True`** により、本ファイルは AST 走査の対象外になる
（規約 §4-2。`design-sync.md` SD-01 で「被覆検査**および器のメタ検査**を置くファイル専用」に
読み替えた）。ここに置くテストは IF を宣言しない —— 宣言すると FR-002「1 テスト＝ちょうど
1 IF」と衝突するためである。

**期待値（50・41・9・提供 MOD 別件数）を 1 つも直書きしない**（FR-004）。数はすべて台帳と
`common.interfaces` の現物から導出する。台帳が改訂されたら、テストコードの数値を書き換えずに
結果が追随する（SC-002）。
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _pytest.outcomes import Failed
from _support.contract_ledger import (
    ContractRow,
    collect_declarations,
    compare,
    contract_csv_path,
    load_contract_rows,
    unresolved_declarations,
)
from conftest import assert_env_path_under  # contract/ が sys.path の先頭に入る（実測）
from pydantic import ValidationError

from board import BoardEnv
from settings import SettingsEnv
from vault import VaultEnv

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pydantic_settings import BaseSettings

CONTRACT_META = True
"""本ファイルを AST 走査の対象外にする（規約 §4-2・SD-01）。"""

CONTRACT_DIR = Path(__file__).resolve().parent
"""走査対象（`apps/api/tests/contract`）。"""

IF_ID_PATTERN = re.compile(r"^IF-\d{2}$")


def _matches(if_id: str) -> bool:
    """`IF-nn` の形か。"""
    return IF_ID_PATTERN.match(if_id) is not None


INTERFACES_PACKAGE = "common.interfaces"


def _rows() -> tuple[ContractRow, ...]:
    """実台帳を読む（**変更しない**）。"""
    return load_contract_rows(contract_csv_path())


def _contract_exception_names(rows: Iterable[ContractRow]) -> set[str]:
    """台帳の提供 MOD から `common.interfaces.<mod>` を引き、そこで**定義された**例外を集める。

    モジュール名の一覧も台帳から導出する（提供 MOD 名 ＝ interfaces のファイル名）。
    `common.kernel.errors` から import しただけの基底は `__module__` で除ける。
    """
    names: set[str] = set()
    for provider in sorted({row.provider for row in rows}):
        module = importlib.import_module(f"{INTERFACES_PACKAGE}.{provider}")
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and issubclass(value, BaseException)
                and value.__module__ == module.__name__
            ):
                names.add(name)
    return names


def test_contract_ledger_rows_are_well_formed_and_duplicates_differ_only_by_consumer() -> None:
    """台帳の行の形（US1 AC-1）。

    (1) `if_id` は一意で `IF-\\d{2}` に一致する。
    (2) `(provider, protocol, method)` へ畳んだときに減った分が**重複行**であり、
        重複行は**利用モジュールだけ**が違う（FR-002a が書き分けの根拠にしている性質）。
        件数（50・41・9）は 1 つも直書きしない —— 2 通りの数え方が一致することだけを見る。
    """
    rows = _rows()
    assert rows, f"台帳が空に見える: {contract_csv_path()}"

    if_ids = [row.if_id for row in rows]
    duplicated_ids = sorted(name for name, count in Counter(if_ids).items() if count > 1)
    assert not duplicated_ids, f"if_id が重複している: {duplicated_ids}"
    malformed_ids = sorted(name for name in if_ids if not _matches(name))
    assert not malformed_ids, f"if_id が IF-nn の形でない: {malformed_ids}"

    keys = [(row.provider, row.protocol, row.method) for row in rows]
    folded = Counter(keys)
    by_fold = len(rows) - len(folded)
    by_group = sum(count - 1 for count in folded.values() if count > 1)
    assert by_fold == by_group, (
        "重複行の数え方が 2 通りで食い違う"
        f"（畳んだ差={by_fold} / 群ごとの超過={by_group}）: {contract_csv_path()}"
    )

    for key, count in sorted(folded.items()):
        if count == 1:
            continue
        same = [row for row in rows if (row.provider, row.protocol, row.method) == key]
        consumers = [row.consumer for row in same]
        assert len(set(consumers)) == count, (
            f"同一メソッドの重複行が利用モジュールで区別できない: {key} / consumers={consumers}"
        )


def test_contract_ledger_exception_names_match_common_interfaces() -> None:
    """例外欄に現れる名前の集合が `common/interfaces` の契約例外と一致する（US1 AC-1）。

    「14 種」という数を直書きしない。台帳側の集合と現物側の集合を**両方向**で突き合わせる。

    ⚠ **層 1 の突合をやり直しているのではない**（spec §0-1「台帳と宣言の突合そのものを
    やり直さない」）—— ここの目的は、層 4 の loader（`_support/contract_ledger.py` の
    `_split_exceptions`。全角スラッシュ区切り・全角括弧の注記の除去）の**解釈**が現物と
    食い違わないことの**自己検査**である。同じ突合は層 1 が
    `packages/common/tests/interfaces/test_contract_coverage.py::test_exceptions_match` で
    別実装により行っているので、**ここが赤くなったらまず loader の解釈を疑う**
    （`design-sync.md` B-30・SD-02）。
    """
    rows = _rows()
    in_ledger = {name for row in rows for name in row.exceptions}
    in_code = _contract_exception_names(rows)
    assert in_ledger == in_code, (
        "台帳の例外欄と common/interfaces の契約例外が食い違う\n"
        f"  台帳にだけ在る: {sorted(in_ledger - in_code)}\n"
        f"  現物にだけ在る: {sorted(in_code - in_ledger)}\n"
        f"  台帳: {contract_csv_path()}"
    )


def test_contract_ledger_methods_exist_with_matching_sync_kind() -> None:
    """宣言された Protocol・メソッドが現物に在り、`同期/非同期` 欄が実物と一致する。

    T025 の「非同期の行が IF-42・IF-43 である」を**直書きせずに**満たす（`design-sync.md`
    SD-02）—— `inspect.iscoroutinefunction` で現物の定義と突き合わせるほうが強く、台帳の
    改訂に追随する。

    ⚠ **層 1 の突合をやり直しているのではない**（spec §0-1）—— ここの目的は、層 4 の loader
    （`_support/contract_ledger.py` の `ASYNC_PREFIX` の解釈）が現物と食い違わないことの
    **自己検査**である。同じ突合は層 1 が
    `packages/common/tests/interfaces/test_contract_coverage.py` の
    `test_no_ledger_method_is_missing`／`test_sync_async_matches` で別実装により行っているので、
    **ここが赤くなったらまず loader の解釈を疑う**（`design-sync.md` B-30・SD-02）。
    なお本検査が見ているのは `common.interfaces` の **Protocol 宣言**であって提供実装ではない
    （提供実装の同期性は `design-sync.md` B-29 の扱い）。
    """
    rows = _rows()
    mismatched: list[str] = []
    for row in rows:
        module = importlib.import_module(f"{INTERFACES_PACKAGE}.{row.provider}")
        protocol = getattr(module, row.protocol, None)
        assert isinstance(protocol, type), (
            f"{row.if_id}: Protocol が現物に無い: {module.__name__}.{row.protocol}"
        )
        method = getattr(protocol, row.method, None)
        assert method is not None, f"{row.if_id}: メソッドが現物に無い: {row.protocol}.{row.method}"
        if inspect.iscoroutinefunction(method) is not row.is_async:
            mismatched.append(
                f"{row.if_id} {row.protocol}.{row.method}:"
                f" 台帳 is_async={row.is_async} / 現物 async={inspect.iscoroutinefunction(method)}"
            )
    assert not mismatched, "同期/非同期 欄が現物と食い違う:\n  " + "\n  ".join(mismatched)


def test_contract_ledger_copy_drives_coverage_without_hardcoded_numbers(tmp_path: Path) -> None:
    """台帳の**写し**を増減させると被覆の結果が動く（SC-002・US1 AC-2・AC-3）。

    実台帳は**変更しない**。テストコード側の数値の書き換えを要しないことが要件である。

    4 点を確かめる —— (1) 1 行増やすと `missing` に載る (2) 1 行減らすと `unknown` に載る
    (3) 台帳に無い IF を宣言したダミーのテストは `unknown` に載る
    (4) `contract_if` を 0 個／2 個持つダミー関数は `malformed` に載る。
    """
    source = contract_csv_path()
    original = source.read_text(encoding="utf-8")
    base_rows = load_contract_rows(source)
    declared = collect_declarations(CONTRACT_DIR)

    # (1) 1 行増やす → 増えた IF が missing に載る（宣言が無いので）
    added_id = "IF-97"
    grown = tmp_path / "grown.csv"
    last = original.rstrip("\n").split("\n")[-1]
    grown.write_text(
        original.rstrip("\n") + f"\n{added_id},{last.split(',', 1)[1]}\n", encoding="utf-8"
    )
    grown_rows = load_contract_rows(grown)
    assert len(grown_rows) == len(base_rows) + 1
    assert added_id in compare(grown_rows, declared).missing

    # (2) 1 行減らす → 消した IF を宣言しているテストが unknown に載る
    lines = original.rstrip("\n").split("\n")
    removed_id = lines[1].split(",", 1)[0]
    shrunk = tmp_path / "shrunk.csv"
    shrunk.write_text("\n".join([lines[0], *lines[2:]]) + "\n", encoding="utf-8")
    shrunk_rows = load_contract_rows(shrunk)
    assert len(shrunk_rows) == len(base_rows) - 1
    assert removed_id not in {row.if_id for row in shrunk_rows}
    fake_declared = collect_declarations(_dummy_dir(tmp_path, removed_id))
    assert removed_id in compare(shrunk_rows, fake_declared).unknown

    # (3) 台帳に無い IF-99 を宣言したダミー → unknown に載る
    dummy = collect_declarations(_dummy_dir(tmp_path, "IF-99", name="unknown_if"))
    assert "IF-99" in compare(base_rows, dummy).unknown

    # (4) 0 個／2 個の宣言 → malformed に載る
    bad = collect_declarations(_malformed_dir(tmp_path))
    reported = {(item.test_name, item.count) for item in compare(base_rows, bad).malformed}
    assert reported == {("test_zero", 0), ("test_two", 2), ("test_variable", 1)}, reported

    assert source.read_text(encoding="utf-8") == original, "実台帳を書き換えてはならない"


def _dummy_dir(tmp_path: Path, if_id: str, *, name: str = "declared") -> Path:
    """`IF-nn` を 1 件だけ宣言するダミーの `test_*.py` を置いたディレクトリを作る。"""
    directory = tmp_path / f"dummy_{name}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_dummy.py").write_text(
        "import pytest\n\n\n"
        f'@pytest.mark.contract_if("{if_id}")\n'
        "def test_dummy() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    return directory


def _malformed_dir(tmp_path: Path) -> Path:
    """`contract_if` の付け方が受理できないダミーを置いたディレクトリを作る。"""
    directory = tmp_path / "dummy_malformed"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_dummy.py").write_text(
        "import pytest\n\n"
        'WHICH = "IF-01"\n\n\n'
        "def test_zero() -> None:\n"
        "    pass\n\n\n"
        '@pytest.mark.contract_if("IF-01")\n'
        '@pytest.mark.contract_if("IF-02")\n'
        "def test_two() -> None:\n"
        "    pass\n\n\n"
        "@pytest.mark.contract_if(WHICH)\n"
        "def test_variable() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    return directory


def test_contract_coverage_matches_the_ledger() -> None:
    """台帳 IF と `contract/` の宣言が全単射である（FR-003・SC-001）。

    **US1 完了時点では赤い**（宣言が 0 件なので `missing` が台帳の全件になる）。ストーリが
    進むごとに `missing` が減り、US6 完了で初めて緑になる。失敗時のメッセージは
    **追加調査なしで原因に到達できる**水準にする（contracts/ledger-loader.md §3）。
    """
    rows = _rows()
    declared = collect_declarations(CONTRACT_DIR)
    diff = compare(rows, declared)
    if not diff.missing and not diff.unknown and not diff.malformed:
        assert diff.declared_total == len({row.if_id for row in rows})
        return

    provider_of = {row.if_id: row.provider for row in rows}
    per_provider = Counter(provider_of[if_id] for if_id in diff.missing)
    declared_by_id: dict[str, list[str]] = {}
    for item in declared:
        if item.if_id is not None:
            declared_by_id.setdefault(item.if_id, []).append(f"{item.file}::{item.test_name}")

    report = [
        "契約台帳の被覆が満たされていない（FR-003）",
        f"  台帳: {contract_csv_path()}",
        f"  走査したディレクトリ: {CONTRACT_DIR}",
        f"  台帳の行数: {diff.ledger_total} / 宣言された相異なる IF: {diff.declared_total}",
        f"  (a) missing（台帳にあって宣言が無い）: {len(diff.missing)} 件",
        f"      {sorted(diff.missing)}",
        "      提供 MOD 別内訳: "
        + ", ".join(f"{name}={count}" for name, count in sorted(per_provider.items())),
        f"  (b) unknown（宣言にあって台帳に無い）: {len(diff.unknown)} 件",
    ]
    report.extend(
        f"      {if_id}: " + ", ".join(declared_by_id.get(if_id, []))
        for if_id in sorted(diff.unknown)
    )
    report.append(f"  (d) malformed（contract_if がちょうど 1 個でない）: {len(diff.malformed)} 件")
    report.extend(
        f"      {item.file}::{item.test_name} count={item.count} if_id={item.if_id!r}"
        for item in diff.malformed
    )
    pytest.fail("\n".join(report))


def test_contract_stopper_helper_fails_before_touching_real_stores(tmp_path: Path) -> None:
    """止め木ヘルパの自己テスト（SC-009・R-05）。

    `tmp_path` 配下でない解決先を渡すと `pytest.fail` で止まることを確かめる。**実運用パスを
    実際に解決させる形は採らない**（FR-014 と衝突する）—— 一時ディレクトリの**外**を指す値を
    作り、それが弾かれることで「実物に触れる前に止まる」性質を実証する。
    """
    outside = tmp_path.parent / "outside_of_tmp_path"
    outside.mkdir(exist_ok=True)
    env_file = tmp_path / "escaping.env"
    env_file.write_text(f"BEADS_DIR={outside}\n", encoding="utf-8")

    with pytest.raises(Failed) as caught:
        assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path / "inside", env_file=env_file)
    message = str(caught.value)
    assert "BEADS_DIR" in message
    assert str(outside) in message
    assert str(tmp_path / "inside") in message

    # 逆に、配下に収まっていれば素通しする（止め木が正常系まで塞がないこと）。
    inside = tmp_path / "inside" / "ws" / ".beads"
    inside.mkdir(parents=True)
    ok_env = tmp_path / "ok.env"
    ok_env.write_text(f"BEADS_DIR={inside}\n", encoding="utf-8")
    resolved = assert_env_path_under(BoardEnv, "BEADS_DIR", tmp_path / "inside", env_file=ok_env)
    assert resolved == inside.resolve()


def test_contract_stopper_isolates_the_env_file_for_each_provider_module(
    isolated_env_file: Path,
) -> None:
    """**止め木そのもの**が効いていることの実証（FR-013a・SC-009）。

    ⚠ **本検査は止め木の器を見るのであって、契約を見るのではない。** `design-sync.md`
    SD-01 が `CONTRACT_META` の適用範囲を「被覆検査**および器のメタ検査**」へ広げた、その
    後者に当たる。本ファイルは AST 走査の対象外なので IF を宣言せず、被覆の件数も動かない。

    上の `..._helper_fails_before_touching_real_stores` が実証するのは**ヘルパ**
    （`assert_env_path_under`）の判定ロジックだけで、`isolated_env_file` の
    `env_file_path` 差し替え＋`delenv` が効いているかは見ていなかった。提供 MOD 別 fixture は
    `build()` の前に `monkeypatch.setenv` を打っており、pydantic-settings の優先順
    （init > OS 環境変数 > `.env`）で `setenv` が `.env` に勝つため、**止め木を丸ごと外しても
    board・vault・settings の既存テストは緑のまま**である（`design-sync.md` B-28。
    B-25＝`BD_BIN` シムと同型の「器の側を見ていない」穴）。

    そこで `setenv` を**当てずに** `<Mod>Env(_env_file=env_file_path())` を組む —— 止め木が
    効いていれば必須キーが無くて `ValidationError`、外れていれば実 `.env` の値で**成功して
    しまう**。`delenv`（OS 環境変数の側）が外れても成功するので、止め木の 2 つの手当てを
    1 本で覆う。

    **実運用ストアには触れない**（Path の値を組もうとして失敗するだけ・FR-014）。実 `.env` の
    値をテストに載せない（FR-013）ため、主張は「値が**無い**こと」に限り、期待値を 1 つも
    直書きしない（FR-004 の精神）。

    executor（差し替え点は `conftest.ENV_FILE_PATH_TARGETS` の
    `executor.models.env_file_path`）は `<Mod>Env` を持たず `.env` を `dotenv_values` で直に
    読むため、この形の probe には載らない。
    """
    probes: tuple[tuple[str, type[BaseSettings], str], ...] = (
        ("board", BoardEnv, "BEADS_DIR"),
        ("vault", VaultEnv, "VAULT_ROOT"),
        ("settings", SettingsEnv, "SETTINGS_ROOT"),
    )
    for module_name, env_cls, key in probes:
        module = importlib.import_module(module_name)
        # module 属性を import 済みの名前で掴むと差し替え**前**を掴む（conftest.py の注意書き）。
        resolve: Callable[[], Path] = module.env_file_path
        assert resolve() == isolated_env_file, (
            f"止め木が {module_name}.env_file_path を差し替えていない"
            f"（解決先: {resolve()} / 期待: {isolated_env_file}）"
        )
        with pytest.raises(ValidationError) as caught:
            env_cls(_env_file=resolve())
        assert key in str(caught.value), (
            f"{env_cls.__name__} が {key} の不在以外の理由で落ちた: {caught.value}"
        )


def test_contract_declarations_actually_reference_the_declared_method(tmp_path: Path) -> None:
    """**宣言の真正性** —— 宣言した IF のメソッドを、そのテストが実際に呼ぶ（FR-003・SC-001）。

    被覆検査（`compare`）は「宣言 IF の集合 ＝ 台帳 IF の集合」しか突き合わせないので、
    `@pytest.mark.contract_if("IF-13")` を付けて `show` だけを呼ぶテストでも被覆に数えられる。
    SC-001 の「適合検証テストが**対応する**のは 50 件」は、その形では空証明になる
    （`ContractRow` は `protocol`・`method` を持っているのに 1 度も使われていなかった）。
    これは `design-sync.md` **B-12** が指摘した歯止めの穴の一族のうち、**機械検査が可能な唯一の
    1 件**である（残る 4 件＝ B-17・B-18・B-21・B-23 の機械化は B-12 の裁定待ち）。

    ⚠ **必要条件であって十分条件ではない。** メソッド名が現れることは「そのメソッドを主眼に
    検証している」ことを意味しない —— 例えば前処理でたまたま呼んでいるだけでも通る。
    ここが緑でも、契約の中身（例外・出力・入力・副作用）を見ているかは別問題である。

    **期待値を 1 つも直書きしない**（FR-004）—— メソッド名は台帳から取る。

    後半は本検査が**空振りでない**ことの自己テストである（規則が実際に噛む）—— 台帳に在る
    IF を宣言するだけで何も呼ばないダミーは、期待メソッド名とともに報告される。
    """
    rows = _rows()
    unresolved = unresolved_declarations(CONTRACT_DIR, rows)
    if unresolved:
        report = [
            "宣言した IF のメソッドを呼んでいないテストがある（FR-003）",
            f"  台帳: {contract_csv_path()}",
            f"  走査したディレクトリ: {CONTRACT_DIR}",
            f"  解決できない宣言: {len(unresolved)} 件",
            "  （テスト本体にも、そこから呼ぶ module 直下のヘルパ 1 段にも現れない）",
        ]
        report.extend(
            f"      {item.file}::{item.test_name} / {item.if_id} / 期待メソッド={item.method}"
            for item in unresolved
        )
        pytest.fail("\n".join(report))

    sample = rows[0]
    bad = unresolved_declarations(_dummy_dir(tmp_path, sample.if_id), rows)
    assert [(item.if_id, item.method) for item in bad] == [(sample.if_id, sample.method)], bad
