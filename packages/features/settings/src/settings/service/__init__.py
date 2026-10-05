"""`SettingsService`：4 Protocol・9 メソッドを **1 クラス**で実装する（FR-005）。

**引数名は契約が正本**（`001` FR-033a）。1 文字も変えない — 変えると pyright strict の
構造的部分型の適合検査が落ちる。

**このファイルはファイルシステムに一切触れない**（P5D-016・FR-014）。到達点は `repository.py`
だけで、9 メソッドはすべてスナップショット参照で応答する。quickstart S3 (a) が機械的に検査する。

**解決規則を自分で書かない**。merge・LLM 解決・台帳導出はすべて build 時に済んでおり（R-06）、
規則の出所は `resolve.py` の 1 箇所である（R-01）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.settings import (
    HooksConfig,
    MemberNotFound,
    ProjectNotFound,
    Rules,
    TaskTemplate,
    TaskTemplates,
    UnknownCommand,
)
from common.kernel.log import LoggerFactory
from settings.models import TASK_KEYS

if TYPE_CHECKING:
    from common.interfaces.settings import LimitPolicy, Member, Project
    from common.kernel.enums import PersonaKey
    from settings.models import SettingsSnapshot, TaskTemplateRecord

_EMPTY_RULES = Rules(project_rules="")
"""`snap.rules` にキーが無いときの防御的な戻り（**build 経路では到達しない**）。

`rules.md` **不在**のときの戻り（FR-027。失敗にしない）を決めているのは
`service/loader/assemble.py` の 1 箇所であり、そこが `projects_raw` の全キーを必ず埋めるため、
実在検査を通った `project_key` は必ず `snap.rules` にも在る。ここに同じ意味を書くと二重宣言に
なるので**書かない**。`replace_snapshot` で欠けたスナップショットを渡された場合の保険である。
"""

_EMPTY_HOOKS = HooksConfig(hooks=[])
"""`snap.hooks` にキーが無いときの防御的な戻り（**build 経路では到達しない**。上と同型）。"""


class SettingsService:
    """設定の読取・メンバ解決・認可・コマンド台帳（IF-23〜30・IF-50）。

    4 契約の実装は**同一インスタンス**である（FR-005）。参照するスナップショットが 4 つに
    割れないためであり、`build()` と `registry.py` の 4 代入がそれを読める形で示す。
    """

    def __init__(self, snapshot: SettingsSnapshot) -> None:
        """畳んだスナップショットを 1 つ保持する（世代はサービスごとに 1 から始まる＝ SD-28）。"""
        self._snapshot = snapshot
        self._logger = LoggerFactory.get_logger("settings.service")

    # -- SettingsReader（IF-23〜26・IF-50） --------------------------------------------

    def get_project(self, project_key: str) -> Project:
        """プロジェクトを読む（IF-23・settings-F03）。

        Args:
            project_key: プロジェクトのキー。

        Returns:
            キー・名前・3 種のフォルダ対応付け・予約かどうか。

        Raises:
            ProjectNotFound: 未登録のキー（**既定に倒さない**）。予約プロジェクト
                （`_program`）も未登録なら他と同じ扱いである（P5D-014）。
        """
        snap = self._snapshot
        project = snap.projects.get(project_key)
        if project is None:
            raise ProjectNotFound(project_key)
        return project

    def get_limits(self, project_key: str) -> LimitPolicy:
        """上限を読む（IF-24・settings-F04）。

        **9 メソッドで唯一プロジェクトの実在を見ない**（IF-24 の例外欄が `—`）。未登録キーには
        全体の既定が返る。`limits.yaml` 不在時にモジュール設定の出荷時既定を使うのは、
        settings-F04 が**明示している唯一の既定**である（他ファイルの不在を既定で埋めない）。

        Args:
            project_key: プロジェクトのキー。

        Returns:
            解決済みの上限（`scope` は全体を表す固定文字列か当該キー）。
        """
        snap = self._snapshot
        override = snap.limits_overrides.get(project_key)
        return override if override is not None else snap.limits_global

    def get_rules(self, project_key: str) -> Rules:
        """プロジェクト共通ルールを読む（IF-25・settings-F05）。

        **先に実在を見てからファイルの内容を見る**。`rules.md` **不在**の空文字と
        **PJ 未登録**の失敗を混ぜない（US1 AC-12・AC-15）。

        Args:
            project_key: プロジェクトのキー。

        Returns:
            本文（不在なら空文字）。

        Raises:
            ProjectNotFound: 未登録のキー。
        """
        snap = self._snapshot
        if project_key not in snap.projects:
            raise ProjectNotFound(project_key)
        return snap.rules.get(project_key, _EMPTY_RULES)

    def get_hooks(self, project_key: str) -> HooksConfig:
        """Hook 設定を読む（IF-26・settings-F06）。

        Args:
            project_key: プロジェクトのキー。

        Returns:
            宣言された Hook 参照の一覧（不在なら空の一覧）。

        Raises:
            ProjectNotFound: 未登録のキー（空の設定を返さない）。
        """
        snap = self._snapshot
        if project_key not in snap.projects:
            raise ProjectNotFound(project_key)
        return snap.hooks.get(project_key, _EMPTY_HOOKS)

    def get_tasks(self, project_key: str) -> TaskTemplates:
        """段テンプレートを読む（IF-50・settings-F25）。

        共通定義に PJ 上書きを**キー単位で丸ごと**重ねた 7 キーを、世代番号つきで返す
        （D16-a・D16-h・FR-029）。**フィールド単位の merge をしない**。

        Args:
            project_key: プロジェクトのキー。

        Returns:
            7 キーの段テンプレート一式（`generation` つき）。

        Raises:
            ProjectNotFound: 未登録のキー（global のテンプレートを返さない）。
        """
        snap = self._snapshot
        if project_key not in snap.projects:
            raise ProjectNotFound(project_key)
        overrides = snap.tasks.get(project_key, {})
        templates: dict[str, TaskTemplate] = {}
        for key in TASK_KEYS:
            record = overrides.get(key) or snap.tasks_global.get(key)
            if record is not None:
                templates[key] = _to_template(record)
        return TaskTemplates(generation=snap.generation, templates=templates)

    # -- MemberResolver（IF-27・IF-28） -------------------------------------------------

    def resolve(self, project_key: str, persona_key: PersonaKey) -> Member:
        """メンバを解決する（IF-27・settings-F07）。

        merge は build 時に済んでいる（R-06）ので、ここは**辞書引き 1〜2 回**である。

        Args:
            project_key: プロジェクトのキー。
            persona_key: ペルソナのキー。

        Returns:
            merge 済み・LLM 解決済み・台帳導出済みのメンバ。

        Raises:
            MemberNotFound: PJ 側にそのペルソナのキーが無い（**ペルソナ既定だけでメンバを
                作らない**）。**PJ が未登録のときも同じ**（`resolve` の契約例外は 1 種だけ）。
        """
        snap = self._snapshot
        members = snap.members.get(project_key)
        if members is None:
            raise MemberNotFound(f"{project_key}.{persona_key.value}")
        member = members.get(persona_key)
        if member is None:
            raise MemberNotFound(f"{project_key}.{persona_key.value}")
        return member

    def is_human(self, actor: str) -> bool:
        """回答者が人間かを判定する（IF-28・settings-F08）。

        4 通りに分かれる: 台帳の AI メンバ → `False`／宣言された人間 → `True`／システム自身の
        行為者 → `False`（**印なし**＝ P5D-037）／どれにも当たらない → `False` ＋
        `settings.is_human.unknown_actor` の構造化ログ（**黙って人間に倒さない**＝ D06-c）。

        3 集合の衝突そのものは V-20 が起動時に拒否するので、判定順序に依存した勝敗は生じない。

        Args:
            actor: 盤面上の行為者名。

        Returns:
            人間なら `True`。**例外を送出しない**。
        """
        snap = self._snapshot
        registry = snap.registry
        if actor in registry.bd_actors:
            return False
        if actor in registry.human_actors:
            return True
        if actor in registry.system_actors:
            return False
        self._logger.warning("settings.is_human.unknown_actor", extra={"actor": actor})
        return False

    # -- Authorizer（IF-29） ------------------------------------------------------------

    def enforce(self, subject: str, obj: str, act: str) -> bool:
        """認可を判定する（IF-29・settings-F09）。

        主体から PJ を引くのに**文字列解析をしない**。台帳（`Registry.subjects`）を引く。
        分割規則をここに書くと台帳の導出規則（`resolve.py`）と同じ知識が 2 箇所に生まれる
        （P4 の二重宣言）。台帳を引けば、形式違い・空文字・未登録 PJ の 3 通りが**同じ 1 経路**
        で拒否になる。

        Args:
            subject: 認可の主体名（`member:<pj>.<persona>`）。
            obj: 対象（`tool:<name>`／`cmd:<name>`／`vault:<area>`）。
            act: 操作（`execute`／`read`／`write`）。

        Returns:
            許可なら `True`。**例外を送出しない**・**黙って許可に倒れない**（P3）。
        """
        snap = self._snapshot
        project_key = snap.registry.subjects.get(subject)
        if project_key is None:
            self._logger.warning(
                "settings.enforce.unknown_subject",
                extra={"subject": subject, "obj": obj, "act": act},
            )
            return False
        enforcer = snap.enforcers.get(project_key)
        if enforcer is None:
            self._logger.warning(
                "settings.enforce.enforcer_missing", extra={"project_key": project_key}
            )
            return False
        return enforcer.enforce(subject, obj, act)

    # -- CommandRegistry（IF-30） -------------------------------------------------------

    def resolve_command(self, name: str) -> list[str]:
        """登録名から引数列を解決する（IF-30・settings-F10）。

        **引数列を組み立てない**。定義に書かれたものをそのまま返す（D07-e）。内部の不変な
        並びから**その都度新しい一覧を作る**ので、呼び手が書き換えてもスナップショットは
        壊れない。

        Args:
            name: コマンドの登録名。

        Returns:
            引数列（新しい `list`）。

        Raises:
            UnknownCommand: 定義に無い名前（**空の一覧を返さない**＝ US2 AC-8）。
        """
        snap = self._snapshot
        command = snap.commands.get(name)
        if command is None:
            raise UnknownCommand(name)
        return list(command.argv)

    # -- スナップショットの保持と付け替え（FR-023・settings-F02） -----------------------

    def replace_snapshot(self, snapshot: SettingsSnapshot) -> None:
        """スナップショットを丸ごと差し替える（**後半の保存経路が呼ぶ**）。

        属性 1 つの代入は GIL 下で不可分であり、9 メソッドは**入口で 1 度だけ**ローカル変数へ
        読むため、読み側が新旧の混ざった値を見ることは無い（US7 AC-1）。**ロック・世代の
        ピン留め・再読込ポーリングを作らない**（憲章 §2・§10）。

        前半では `build()` とテストだけが呼ぶ。後半の保存経路は「候補を組む → validate →
        領域 A へ書く → 本メソッド」の 4 段になり、**前半が作った 3 段をそのまま使う**。

        Args:
            snapshot: 新しいスナップショット（世代は現在より大きいこと）。

        Raises:
            ValueError: 世代が増えていない（内部の不変条件の破れ＝ US7 AC-3）。
        """
        current = self._snapshot
        if snapshot.generation <= current.generation:
            msg = (
                "世代が単調増加していない: "
                f"{current.generation} → {snapshot.generation}（内部の不変条件の破れ）"
            )
            raise ValueError(msg)
        self._snapshot = snapshot
        self._logger.info(
            "settings.snapshot.replaced",
            extra={"generation": snapshot.generation, "previous": current.generation},
        )


def _to_template(record: TaskTemplateRecord) -> TaskTemplate:
    """段テンプレートの写像を契約の型へ写す。

    **`tools` は受け渡し型としては既定値を持たない**ため、YAML に `tools:` が無くても
    `tools=None` を**明示的に渡して**構築する（DIFF-07）。
    """
    return TaskTemplate(
        description=record.description,
        expected_output=record.expected_output,
        agent=record.agent,
        tools=list(record.tools) if record.tools is not None else None,
    )
