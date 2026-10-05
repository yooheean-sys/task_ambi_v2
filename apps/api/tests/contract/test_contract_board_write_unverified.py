"""IF-11 の `BoardWriteUnverified`（board 提供）。US2。

実 `bd` を正常に使う限り原理的に踏めない契約例外を、**`BD_BIN` シム (b)**
（実 `bd` へ argv を素通しで委譲しつつ `create` のときだけ `-l <label>` を **1 組**落とす）で
CLI 境界から注入して踏む（contracts/bd-shims.md §3・設計差分候補 (iv)）。

なぜこれで踏めるか: board の `create` は読み戻し検証で「`set(labels)` の完全一致と `parent`
の一致」を見る（`service.py` の `_verify_created`）。ラベルが 1 つ足りない状態で読み戻ると
spec と食い違い `BoardWriteUnverified` になる。

**`IssueSpec.labels` に 2 つ以上のラベルを載せて呼ぶ**（1 組落としても `-l` が残る形にする）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import BOARD_ACTOR, BOARD_PROJECT_KEY

from common.interfaces.board import BoardWriteUnverified, IssueSpec
from common.kernel.enums import KindLabel, Labels

if TYPE_CHECKING:
    from common.interfaces.board import BoardWriter

pytestmark = pytest.mark.requires_bd


@pytest.mark.contract_if("IF-11")
def test_contract_board_create_raises_write_unverified_when_a_label_is_lost(
    board_unverified_writer: BoardWriter,
) -> None:
    """IF-11: 読み戻しが spec と食い違ったら `BoardWriteUnverified`（**成功に倒さない**＝ P3）。

    シムが `-l` を 1 組落とすので、書けたラベルは 2 つ、spec は 3 つになる。board は
    「外部ツールの終了コードを成功の証拠にしない」ため、rc=0 でも不一致を検出する。
    """
    labels = [
        Labels.kind(KindLabel.child),
        Labels.project(BOARD_PROJECT_KEY),
        Labels.role_executor(),
    ]
    spec = IssueSpec(
        title="ラベルが 1 つ落ちる起票",
        description="説明",
        labels=labels,
        parent=None,
        acceptance="- [ ] 受け入れ条件",
    )

    with pytest.raises(BoardWriteUnverified) as caught:
        board_unverified_writer.create(spec, BOARD_ACTOR)

    assert caught.value.mismatch, "不一致の内容を保持する（R-16）"
