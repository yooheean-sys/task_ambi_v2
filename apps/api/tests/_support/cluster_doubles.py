"""クラスタ層が足すテストダブル 2 形（順次応答ラッパ・待機ラッパ）。data-model §3-2・規約 §4。

`_support/contract_stubs.py` の `Stub*` は 14 種とも**固定応答**で、呼び出し回数で応答を変える
口も、進行中を保持する口も無い（CD-04）。本モジュールはその 2 つを足す。いずれも
**`Stub*` を内側に持つ**（`setattr` で振る舞いを差し替えない＝ FR-033b）。
`_support/contract_stubs.py` は 1 行も変えない。

- **順次応答ラッパ**は並びを使い切ったら**失敗する**（偽 LLM サーバのように最終要素を
  繰り返さない）。往復数・呼び出し回数の読み違いが「無限に同じ応答」で緑になるのを防ぐ。
- **待機ラッパの待ちには上限を置く**（既定 10 秒）。超えたら待ち直さずに失敗させる
  （憲章 §10 —— 自動復旧しない）。

受け側には必ず Protocol 型を注釈する（憲章 §3）。Protocol は構造的部分型であり、型注釈付きの
代入がなければ適合検査が発火しない。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from common.interfaces.board import BoardWriter, ClaimFilter, Issue, IssueSpec
    from common.interfaces.executor import CrewExecutor, CrewOutcome, CrewSpec

DEFAULT_GATE_TIMEOUT_SEC = 10.0
"""待機ラッパの既定の上限（規約 §4）。超えたら待ち直さずに落とす。"""


class SequencedCrewExecutor:
    """呼ばれた順に内側の `StubCrewExecutor` へ委譲する（`CrewExecutor` に構造適合）。

    1 周回で `CrewExecutor.run` は `purpose=work`（差し戻しで反復）と `purpose=review` に
    **複数回**呼ばれる。そのたびに違う `CrewOutcome`（あるいは契約例外）を返すために、
    **1 呼び出しにつき 1 つの `Stub*`** を用意する —— 応答を差し替える口は `Stub*` の
    コンストラクタしか無い（規約 §4）ので、この形なら記録（`CallLog`）も契約例外（`raises=`）
    も既存のまま使え、`setattr` を持ち込まずに済む。

    **並びを使い切ったら失敗する**（偽サーバのように最終要素を繰り返さない）。空の並びは
    「この経路では `run` が 1 度も呼ばれてはならない」の表明として使える。
    """

    def __init__(self, stubs: Sequence[CrewExecutor]) -> None:
        """委譲先を呼ばれる順に受け取る。

        Args:
            stubs: 1 呼び出しにつき 1 つの `CrewExecutor` 実装（`StubCrewExecutor` を想定）。
                **空を渡せる** —— 「この経路では `run` が 1 度も呼ばれてはならない」の表明に
                なる（呼ばれたら「並びを使い切った」で落ちる）。
        """
        self._stubs = list(stubs)
        self._calls = 0

    @property
    def calls(self) -> int:
        """これまでに `run` が呼ばれた回数。"""
        return self._calls

    def run(self, spec: CrewSpec) -> CrewOutcome:
        """`calls` 番目の委譲先へ渡す。並びを超えたら `AssertionError` で落とす。"""
        if self._calls >= len(self._stubs):
            msg = (
                "SequencedCrewExecutor の並びを使い切った"
                f"（用意 {len(self._stubs)} 件・{self._calls + 1} 回目の呼び出し）"
                " / 用意 0 件なら「この経路では run が呼ばれてはならない」の表明が破れている"
                " / 用意 1 件以上なら往復数を読み違えている（最終要素は繰り返さない）"
            )
            raise AssertionError(msg)
        stub = self._stubs[self._calls]
        self._calls += 1
        return stub.run(spec)


class Gate:
    """`threading.Event` の薄い包み。`wait` は**上限超過で失敗する**（規約 §4）。

    「1 本目を走らせ続けたまま 2 本目を投げる」形を作るためだけの道具で、待ち直しはしない。
    """

    def __init__(self, *, timeout_sec: float = DEFAULT_GATE_TIMEOUT_SEC) -> None:
        """上限を受け取る。"""
        self._event = threading.Event()
        self._entered = threading.Event()
        self._timeout_sec = timeout_sec

    def wait_entered(self, *, what: str = "待機ラッパの入口") -> None:
        """包んだ面に**入った**ことを待つ（上限超過は失敗）。"""
        if not self._entered.wait(self._timeout_sec):
            msg = f"{what} に {self._timeout_sec} 秒以内に到達しなかった"
            raise AssertionError(msg)

    def release(self) -> None:
        """待っている側を解放する。"""
        self._event.set()

    def hold(self, what: str) -> None:
        """包んだ面の中で解放を待つ（上限超過は失敗）。"""
        self._entered.set()
        if not self._event.wait(self._timeout_sec):
            msg = f"{what} が {self._timeout_sec} 秒以内に解放されなかった"
            raise AssertionError(msg)


class GatedCrewExecutor:
    """`CrewExecutor.run` の中で解放を待つ（`CrewExecutor` に構造適合）。

    `POST /kickoffs` は `start_background` で即 202 を返すので、1 本目を保持しないと 2 本目が
    409（`KickoffBusy`）を踏めない（R-07）。
    """

    def __init__(self, inner: CrewExecutor, gate: Gate) -> None:
        """内側の実装と門を受け取る。"""
        self._inner = inner
        self._gate = gate

    def run(self, spec: CrewSpec) -> CrewOutcome:
        """解放されるまで待ってから内側へ委譲する。"""
        self._gate.hold("GatedCrewExecutor.run")
        return self._inner.run(spec)


class GatedBoardWriter:
    """`BoardWriter.claim_next` の中で解放を待つ（`BoardWriter` に構造適合）。

    クラスタ 05 の 9 スタブは `claim_next=None` で周回が即 `no_work` に終わるので、1 本目を
    走らせ続けないと `KickoffBusy`（#6）を観測できない。待つ面以外は内側へ素通しする。
    """

    def __init__(self, inner: BoardWriter, gate: Gate) -> None:
        """内側の `BoardWriter` 実装と門を受け取る。

        Args:
            inner: 内側の `BoardWriter`（`StubBoardWriter` を想定）。**Protocol 型を注釈する**
                （憲章 §3。注釈が無いと pyright strict の構造適合検査が発火しない）。
            gate: 待ち合わせの門。
        """
        self._inner = inner
        self._gate = gate

    def claim_next(self, claim_filter: ClaimFilter, actor: str) -> Issue | None:
        """解放されるまで待ってから内側へ委譲する。"""
        self._gate.hold("GatedBoardWriter.claim_next")
        return self._inner.claim_next(claim_filter, actor)

    def create(self, spec: IssueSpec, actor: str) -> str:
        """内側へ素通しする。"""
        return self._inner.create(spec, actor)

    def add_dependency(self, from_id: str, to_id: str, actor: str) -> None:
        """内側へ素通しする。"""
        self._inner.add_dependency(from_id, to_id, actor)

    def move_dependents(self, old_id: str, new_id: str, actor: str) -> None:
        """内側へ素通しする。"""
        self._inner.move_dependents(old_id, new_id, actor)

    def release(self, issue_id: str, actor: str) -> None:
        """内側へ素通しする。"""
        self._inner.release(issue_id, actor)

    def comment(self, issue_id: str, body: str, actor: str) -> None:
        """内側へ素通しする。"""
        self._inner.comment(issue_id, body, actor)

    def close(self, issue_id: str, reason: str, actor: str) -> None:
        """内側へ素通しする。"""
        self._inner.close(issue_id, reason, actor)

    def set_labels(self, issue_id: str, add: list[str], remove: list[str], actor: str) -> None:
        """内側へ素通しする。"""
        self._inner.set_labels(issue_id, add, remove, actor)
