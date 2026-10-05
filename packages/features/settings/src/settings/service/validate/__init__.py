"""整合検査 3 種（F18・F19・F20）。contracts/validation-rules.md が正本。

**規則 1 つ = 関数 1 つ = `kind` 1 つ**（R-15）。`kind` の語彙は `p5r1_notes.md` §5 の
**13 語から増やさない**（FR-046）。受け皿が無い 6 件は意味の近い語に寄せた（§6・SD-25・SD-30）。

**行数が憲章 §1 の目安を超えたので同名サブパッケージに分けた**（憲章 §3・plan (xii) 段 3）。
規則の集約は次のとおりで、依存は `problems` → `vars` → `agents` → `tasks` → `forward` →
`reverse`／`commands` の一方向に閉じる:

| ファイル | 集約 |
|---|---|
| `problems.py` | 違反の生成・秘密語の走査（V-06）・共通の小道具 |
| `vars.py` | `{変数}` の抽出（R-19） |
| `agents.py` | `agents.yaml` 1 ファイル分（V-01〜V-07・V-15・V-18 の一部） |
| `tasks.py` | `tasks.yaml` 1 ファイル分（V-08〜V-13） |
| `forward.py` | 順方向の入口（V-14・V-16・V-17・V-19・V-20・V-27・V-28） |
| `reverse.py` | 逆方向の入口（V-21〜V-23） |
| `commands.py` | コマンド突合の入口（V-24〜V-26） |
"""

from __future__ import annotations

from settings.service.validate.agents import check_agents_mapping
from settings.service.validate.commands import validate_commands
from settings.service.validate.forward import build_hook_refs, validate_forward
from settings.service.validate.problems import check_secret_keys
from settings.service.validate.reverse import validate_reverse
from settings.service.validate.tasks import check_tasks_mapping
from settings.service.validate.vars import extract_variables

__all__ = [
    "build_hook_refs",
    "check_agents_mapping",
    "check_secret_keys",
    "check_tasks_mapping",
    "extract_variables",
    "validate_commands",
    "validate_forward",
    "validate_reverse",
]
