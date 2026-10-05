"""モジュール間契約（Protocol と受け渡し型）を提供モジュール単位で置く。

再エクスポートを一切書かない。まとめて import できるようにすると
`executor` ↔ `monitor` の相互参照が実行時に復活する（data-model.md §12）。
"""
