"""2 つの台帳（data-model §3・規約 §8）。**混ぜない**。

| 表 | 意味 |
|---|---|
| `UNREACHED` | 実配線 ＋ 契約準拠スタブ ＋ 実自ストアでは**踏めない** (操作, status) と理由 |
| `KNOWN_DIVERGENCES` | 正本と実装が**食い違う** (操作, status)。検証を書き xfail を付ける |

混ぜると「検証を書いていない」と「書いたが今は落ちる」の区別が消える。被覆検査
（`test_http_coverage_ledger.py` の C-4）が両表の交わりを `overlapping_pairs` で落とす。

**`UNREACHED` の `reason` は空にできない**（C-5 が落とす）。**`KNOWN_DIVERGENCES` の各対には
宣言が在ること**も C-4 が見る（書き忘れて xfail だけ残る事故を防ぐ）。

`strict=True` を外さない —— 正本か実装が直ったときに `XPASS` で赤くなり、この表と
`specs/014-http-contracts/design-sync.md` の B 表の更新を強制する仕掛けである（FR-044・HD-30）。
"""

from __future__ import annotations

from dataclasses import dataclass

from _support.openapi_source import StatusPair


@dataclass(frozen=True, slots=True)
class UnreachedPair:
    """踏まない (操作, status) と理由。"""

    method: str
    path: str
    """正本のパステンプレート。"""

    status: str
    reason: str
    """**空にできない**（被覆検査 C-5 が落とす）。"""

    evidence: str
    """根拠の ID（先行 feature の B 表・SD 表・本 feature の FR）。"""

    @property
    def pair(self) -> StatusPair:
        """対の鍵。"""
        return (self.method, self.path, self.status)


@dataclass(frozen=True, slots=True)
class KnownDivergence:
    """正本と実装が食い違う (操作, status)。"""

    method: str
    path: str
    status: str
    sync_row: str
    """`specs/014-http-contracts/design-sync.md` の B 表の行 ID。"""

    summary: str
    """一文（`xfail` の `reason` に埋める）。"""

    @property
    def pair(self) -> StatusPair:
        """対の鍵。"""
        return (self.method, self.path, self.status)

    @property
    def xfail_reason(self) -> str:
        """`pytest.mark.xfail(reason=...)` に渡す文字列。"""
        return f"{self.sync_row}: {self.summary}"


_M1 = "AgentView は allOf の枝 AgentSpec に additionalProperties:false があり充足不能"
_M2 = "未設定の任意欄を実装が null で返すが正本は null を許さない型で宣言している"
_M3 = "正本の required は threshold_T（大文字 T）だが実装は threshold_t を返す"
_M4 = "実装の ProblemKind 13 語のうち llm_ref・var_required が正本の 7 語 enum の外"

_AGENTS_SUMMARY = f"{_M1}／{_M2}"
_AGENTS_ROW = "B-09 (M-1) ＋ B-10 (M-2)"

UNREACHED: tuple[UnreachedPair, ...] = (
    UnreachedPair(
        method="POST",
        path="/settings/commands",
        status="201",
        reason=(
            "V-24（policy の cmd: ⊆ 定義）と V-25（死に定義の禁止）の相互ロックで、"
            "定義だけを足すと cmd_ref の 422 にしかならない（口から到達できない）"
        ),
        evidence="010 B-40",
    ),
    UnreachedPair(
        method="DELETE",
        path="/settings/agents/{project_key}/{persona_key}",
        status="204",
        reason=(
            "V-19／V-21 の相互ロックで、参照されているメンバを消すと policy が orphan になり "
            "422 にしかならない（口から到達できない）"
        ),
        evidence="010 B-49",
    ),
    UnreachedPair(
        method="DELETE",
        path="/settings/commands/{name}",
        status="204",
        reason=(
            "B-40 の削除側。定義を消すと policy の cmd: 参照が壊れ cmd_ref の 422 にしかならない"
            "（口から到達できない）"
        ),
        evidence="010 B-51",
    ),
    UnreachedPair(
        method="GET",
        path="/history",
        status="404",
        reason=("実装が 404 を返さない（宣言だけの口）。未実行の月も 200 ＋ [] を返す"),
        evidence="011 SD-22",
    ),
    UnreachedPair(
        method="PUT",
        path="/settings/commands/{name}",
        status="422",
        reason=(
            "業務 validate 経路が無い"
            "（argv の空・空白・path と body の name 不一致はいずれも 200）。"
            "422 は DTO 層の内蔵 422 だけで、FR-026 が対象外と定めている"
        ),
        evidence="FR-026",
    ),
    UnreachedPair(
        method="PUT",
        path="/settings/hooks/{project_key}",
        status="422",
        reason=(
            "業務 validate 経路が無い（未知の agent・空の agents・空の name はいずれも 200）。"
            "422 は points の Literal 違反＝内蔵 422 だけで、FR-026 が対象外と定めている"
        ),
        evidence="FR-026",
    ),
)
"""踏まない 6 対（R-04 の U-1〜U-6）。"""

KNOWN_DIVERGENCES: tuple[KnownDivergence, ...] = (
    # M-1 ＋ M-2 —— agents 5 対（AgentView の充足不能 ＋ nullable の宣言漏れ）
    KnownDivergence("GET", "/settings/agents", "200", _AGENTS_ROW, _AGENTS_SUMMARY),
    KnownDivergence(
        "GET", "/settings/agents/global/{persona_key}", "200", _AGENTS_ROW, _AGENTS_SUMMARY
    ),
    KnownDivergence(
        "PUT", "/settings/agents/global/{persona_key}", "200", _AGENTS_ROW, _AGENTS_SUMMARY
    ),
    KnownDivergence(
        "GET", "/settings/agents/{project_key}/{persona_key}", "200", _AGENTS_ROW, _AGENTS_SUMMARY
    ),
    KnownDivergence(
        "PUT", "/settings/agents/{project_key}/{persona_key}", "200", _AGENTS_ROW, _AGENTS_SUMMARY
    ),
    # M-2 —— tasks 4 対（TaskTemplateSpec.agent・tools の nullable 宣言漏れ）
    KnownDivergence("GET", "/settings/tasks/global", "200", "B-10 (M-2)", _M2),
    KnownDivergence("PUT", "/settings/tasks/global", "200", "B-10 (M-2)", _M2),
    KnownDivergence("GET", "/settings/tasks/{project_key}", "200", "B-10 (M-2)", _M2),
    KnownDivergence("PUT", "/settings/tasks/{project_key}", "200", "B-10 (M-2)", _M2),
    # M-3 —— llm-profiles 4 対（threshold_T / threshold_t の綴り違い）
    KnownDivergence("GET", "/settings/llm-profiles", "200", "B-11 (M-3)", _M3),
    KnownDivergence("POST", "/settings/llm-profiles", "201", "B-11 (M-3)", _M3),
    KnownDivergence("GET", "/settings/llm-profiles/{name}", "200", "B-11 (M-3)", _M3),
    KnownDivergence("PUT", "/settings/llm-profiles/{name}", "200", "B-11 (M-3)", _M3),
    # M-4 —— 422 の 7 対（ValidationProblem.kind の語彙が正本 7 語・実装 13 語）
    KnownDivergence("PUT", "/settings/agents/global/{persona_key}", "422", "B-12 (M-4)", _M4),
    KnownDivergence(
        "PUT", "/settings/agents/{project_key}/{persona_key}", "422", "B-12 (M-4)", _M4
    ),
    KnownDivergence("POST", "/settings/llm-profiles", "422", "B-12 (M-4)", _M4),
    KnownDivergence("DELETE", "/settings/llm-profiles/{name}", "422", "B-12 (M-4)", _M4),
    KnownDivergence("PUT", "/settings/llm-profiles/{name}", "422", "B-12 (M-4)", _M4),
    KnownDivergence("PUT", "/settings/tasks/global", "422", "B-12 (M-4)", _M4),
    KnownDivergence("PUT", "/settings/tasks/{project_key}", "422", "B-12 (M-4)", _M4),
)
"""正本と実装が食い違う 20 対（R-05 の M-1〜M-4。重複を除いた実数）。"""


def unreached_pairs() -> frozenset[StatusPair]:
    """踏まない対の鍵の集合（被覆検査が「宣言 ∪ 台帳」を組むのに使う）。"""
    return frozenset(row.pair for row in UNREACHED)


def divergence_of(method: str, path: str, status: str) -> KnownDivergence | None:
    """指定の対の既知差分を引く（テストが `xfail` の `reason` を組むのに使う）。

    Args:
        method: HTTP メソッド（大文字）。
        path: 正本のパステンプレート。
        status: status（文字列）。

    Returns:
        該当する行。無ければ `None`。
    """
    wanted = (method, path, status)
    for row in KNOWN_DIVERGENCES:
        if row.pair == wanted:
            return row
    return None


def divergence_pairs() -> frozenset[StatusPair]:
    """既知差分の対の鍵の集合。"""
    return frozenset(row.pair for row in KNOWN_DIVERGENCES)
