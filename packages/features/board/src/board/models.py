"""`bd` の機械可読出力 → 受け渡し型の写像（board-F16）。

キー名の正本は `specs/003-board/contracts/json-mapping.md`（spec「JSON キー写像表」）。**版依存**
（`bd 1.1.2` / `schema_version` = 1）。**`repository.py` を import しない**（依存は一方向＝ R-06）。

規則: 空値の任意キーは省略される（M-17）ので既定へ倒す／**必須キーの欠落は例外**（M5-4）／
**未知のキーは無視**（M5-3）／**未知の `status` 値は例外**（M5-5。既定へ倒さない）／
**`labels` は次元を検査せず文字列のまま保持**（M5-1・FR-037）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  pydantic が実行時に解決する必要がある
from typing import cast

from board.exceptions import BdMappingError
from common.interfaces.board import Comment, Issue, IssueRef, IssueStatus

DEPENDENCY_TYPES: tuple[str, ...] = ("blocks", "parent-child")
"""契約 `IssueRef.dependency_type` の `Literal` と同じ 2 値（実測 M-18）。"""


def _invalid(key: str, reason: str) -> BdMappingError:
    """写像の失敗を、**どのキーで何が起きたか**が読める形で作る。"""
    return BdMappingError(f"bd の応答を写像できない（{key}）: {reason}", key=key)


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if value is None:
        raise _invalid(key, "必須キーが無い")
    if not isinstance(value, str):
        raise _invalid(key, f"文字列ではない: {type(value).__name__}")
    return value


def _optional_str(raw: dict[str, object], key: str, default: str = "") -> str:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise _invalid(key, f"文字列ではない: {type(value).__name__}")
    return value


def _optional_str_or_none(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(key, f"文字列ではない: {type(value).__name__}")
    return value


def _optional_labels(raw: dict[str, object], key: str = "labels") -> list[str]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid(key, f"配列ではない: {type(value).__name__}")
    labels = cast("list[object]", value)
    if any(not isinstance(label, str) for label in labels):
        raise _invalid(key, "要素に文字列でないものがある")
    return [label for label in labels if isinstance(label, str)]


def _required_datetime(raw: dict[str, object], key: str) -> datetime:
    text = _required_str(raw, key)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise _invalid(key, f"RFC3339 として読めない: {text!r}") from exc


def _status(raw: dict[str, object], key: str = "status") -> IssueStatus:
    text = _required_str(raw, key)
    try:
        return IssueStatus(text)
    except ValueError as exc:
        known = "／".join(member.value for member in IssueStatus)
        raise _invalid(key, f"契約に無い状態: {text!r}（既知: {known}）") from exc


def _objects(value: object, key: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise _invalid(key, f"配列ではない: {type(value).__name__}")
    entries = cast("list[object]", value)
    result: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _invalid(key, f"要素がオブジェクトではない: {type(entry).__name__}")
        result.append(_named(cast("dict[object, object]", entry)))
    return result


def _named(entry: dict[object, object]) -> dict[str, object]:
    """`json.loads` の戻り（キーの型が静的に不明な dict）をキー文字列の写像へ整える。"""
    return {str(name): value for name, value in entry.items()}


def first_object(payload: object) -> dict[str, object]:
    """単一オブジェクトと配列の 2 形を吸収して**先頭 1 件**を返す（M-16）。

    Args:
        payload: `json.loads` の結果。

    Returns:
        オブジェクト 1 件。

    Raises:
        BdMappingError: 空配列・オブジェクトでも配列でもない。
    """
    objects = as_objects(payload)
    if not objects:
        raise _invalid("__root__", "応答が空配列で 1 件も無い")
    return objects[0]


def as_objects(payload: object) -> list[dict[str, object]]:
    """単一オブジェクトと配列の 2 形を吸収して**全件**を返す（M-16）。

    Args:
        payload: `json.loads` の結果。

    Returns:
        オブジェクトのリスト（0 件もありうる）。

    Raises:
        BdMappingError: オブジェクトでも配列でもない／要素がオブジェクトでない。
    """
    if isinstance(payload, dict):
        return [_named(cast("dict[object, object]", payload))]
    return _objects(payload, "__root__")


def to_issue(raw: dict[str, object], *, with_dependencies: bool = True) -> Issue:
    """`bd show`／`list`／`ready`／`update`／`close` の 1 要素を `Issue` へ写す。

    ⚠ **`dependencies` の形はサブコマンドによって違う**（bd 1.1.2 実測。契約
    `contracts/json-mapping.md` §3 が書くのは前者だけである）:

    - `bd show`／`bd dep list`: **課題の完全形 ＋ `dependency_type`**（`IssueRef` に写せる）
    - `bd list`（`--flat` を含む）: **辺の形**（`issue_id`／`depends_on_id`／`type`／`created_at`／
      `created_by`／`metadata`）。`status`・`title`・`labels` を**持たない**ので `IssueRef` を
      作れない（作れば捏造になり FR-040・P3 に反する）

    そのため呼び手が `with_dependencies` で**明示的に**選ぶ。`False` を渡す経路（`list`／`ready`）
    では `Issue.dependencies` は常に空になる。依存を見たい呼び手は `show` を使う。

    Args:
        raw: bd の 1 オブジェクト。
        with_dependencies: `dependencies` を写すか。`bd list` 由来の応答には `False` を渡す。

    Returns:
        写像した `Issue`。

    Raises:
        BdMappingError: 必須キーの欠落・型違い・未知の `status`・時刻の書式違い。
            `with_dependencies=True` で辺の形が来た場合も含む（bd の版が変わった合図）。
    """
    dependencies_raw = raw.get("dependencies")
    dependencies = (
        _objects(dependencies_raw, "dependencies")
        if with_dependencies and dependencies_raw is not None
        else []
    )
    return Issue(
        id=_required_str(raw, "id"),
        title=_optional_str(raw, "title"),
        description=_optional_str(raw, "description"),
        status=_status(raw),
        labels=_optional_labels(raw),
        parent=_optional_str_or_none(raw, "parent"),
        assignee=_optional_str_or_none(raw, "assignee"),
        acceptance=_optional_str(raw, "acceptance_criteria"),
        dependencies=[to_issue_ref(entry) for entry in dependencies],
        created_at=_required_datetime(raw, "created_at"),
        updated_at=_required_datetime(raw, "updated_at"),
    )


def to_issue_ref(raw: dict[str, object]) -> IssueRef:
    """`Issue.dependencies` の 1 要素・`bd dep list` の 1 要素を `IssueRef` へ写す。

    `parent-child` の辺も closed の依存も落とさない（M-18・FR-039。絞り込みは読取メソッド側）。

    Args:
        raw: bd の 1 オブジェクト。

    Returns:
        写像した `IssueRef`。

    Raises:
        BdMappingError: 必須キーの欠落・型違い・`dependency_type` が契約の 2 値に無い。
    """
    dependency_type = _required_str(raw, "dependency_type")
    if dependency_type not in DEPENDENCY_TYPES:
        raise _invalid(
            "dependency_type",
            f"契約に無い種別: {dependency_type!r}（既知: {'／'.join(DEPENDENCY_TYPES)}）",
        )
    return IssueRef(
        id=_required_str(raw, "id"),
        title=_optional_str(raw, "title"),
        status=_status(raw),
        labels=_optional_labels(raw),
        dependency_type="blocks" if dependency_type == "blocks" else "parent-child",
    )


def to_comment(raw: dict[str, object]) -> Comment:
    """`bd comments` の 1 要素・`bd comment` の応答を `Comment` へ写す。

    行為者は **`author`**、本文は **`text`**（M-04）。種別（人間指示／回答／封筒）は
    付けない（P5D-005）。

    Args:
        raw: bd の 1 オブジェクト。

    Returns:
        写像した `Comment`。

    Raises:
        BdMappingError: 必須キーの欠落・型違い・時刻の書式違い。
    """
    return Comment(
        id=_required_str(raw, "id"),
        issue_id=_required_str(raw, "issue_id"),
        actor=_required_str(raw, "author"),
        body=_required_str(raw, "text"),
        created_at=_required_datetime(raw, "created_at"),
    )
