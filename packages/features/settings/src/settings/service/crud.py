"""CRUD サービス（39 操作の入口・生の構造の保持）。data-model.md §5・plan (ii)。

**`SettingsService`（前半・4 Protocol の実装）にはフィールドを 1 つも足さない**（C-2・FR-005・
R-04）。生の構造（`AreaAData`）を保持するのは本クラスであり、両者は**同じ `SettingsService`
インスタンス**を共有するので、保存の差し替えは読取側へ即時に効く（SC-003）。

**可変なのは `_data` と `_snapshot` の 2 つだけ**である。`_snapshot` は `_service` に入れた
のと**同じオブジェクト**で、世代を読むために保持している —— `SettingsService` は世代の
公開アクセサを持たず、前半は **1 行も変えない**（FR-005）ため、保存経路が「現在 + 1」
（010 SD-01）を決めるにはここに参照が要る。**規則の複製ではない**（data-model §5 の 7 項に
対する実装上の追加 1 件）。

状態遷移:

| 事象 | `_data` | `_snapshot`／`_service` |
|---|---|---|
| `build()` | 読み切った生の構造 | 世代 1 |
| 保存が検査に落ちる | 変化なし | 変化なし |
| 保存が書込に失敗 | 変化なし | 変化なし（**世代だけ進んだ状態を作らない**＝ FR-021） |
| 保存が成功 | 候補へ進む | 世代 N+1 |
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces.settings import ProjectNotFound, UnknownCommand
from common.kernel.enums import PersonaKey
from settings.exceptions import (
    ProjectDeletionBlocked,
    SettingsItemNotFound,
    SettingsValidationError,
)
from settings.models import EffectClass
from settings.schemas import (
    AgentSpec,
    AgentView,
    CommandDefSchema,
    HookRefSchema,
    HooksConfigSchema,
    HumansView,
    LimitSpec,
    LimitsView,
    LlmProfilesView,
    LlmProfileView,
    PolicyRow,
    PolicyView,
    ProjectFoldersSpec,
    ProjectReposSpec,
    ProjectView,
    RolesView,
    TaskTemplatesView,
    TextBody,
)
from settings.service import SettingsService, ai_config, members, projects, roles
from settings.service.resolve import human_actor_names, normalize_mapping, resolve_llm_name
from settings.service.save import save_candidate

if TYPE_CHECKING:
    from common.interfaces.board import BoardReader
    from common.interfaces.settings import LimitPolicy
    from common.interfaces.vault import VaultReader
    from settings import SettingsConfig
    from settings.models import AreaAData, SaveOutcome, SettingsSnapshot
    from settings.repository import AreaAReader, AreaAWriter
    from settings.schemas import LlmProfileSpec, PolicySpec, ProjectSpec


class SettingsCrudService:
    """39 操作の入口。**保存はすべて `service/save.py::save_candidate` を通る**（FR-010）。"""

    def __init__(
        self,
        *,
        service: SettingsService,
        snapshot: SettingsSnapshot,
        data: AreaAData,
        reader: AreaAReader,
        writer: AreaAWriter,
        board_reader: BoardReader,
        vault_reader: VaultReader,
        config: SettingsConfig,
    ) -> None:
        """組み立て済みの読取サービス・生の構造・領域 A の入出力・2 依存・挙動設定を受け取る。"""
        self._service = service
        self._snapshot = snapshot
        self._data = data
        self._reader = reader
        self._writer = writer
        self._board_reader = board_reader
        self._vault_reader = vault_reader
        self._config = config

    # -- 保存経路へ渡す口（`service/save.py` だけが使う） -------------------------------

    @property
    def service(self) -> SettingsService:
        """4 契約の実装（**`build()` が `SettingsHandle` に載せたのと同一インスタンス**＝ C-2）。

        差し替え（`apply`）が読取側へ**即時に**効くのはこの共有によるものである（SC-003）。
        受け手は憲章 §3 に従って該当 Protocol 型を注釈する（`authorizer: Authorizer = …`）。
        """
        return self._service

    @property
    def reader(self) -> AreaAReader:
        """領域 A の読取アダプタ（`build_snapshot` へのパス解決に使う。**読み直さない**）。"""
        return self._reader

    @property
    def writer(self) -> AreaAWriter:
        """領域 A への書込アダプタ（実行時に書く**唯一の入口**＝ FR-026・SC-024）。"""
        return self._writer

    @property
    def config(self) -> SettingsConfig:
        """挙動設定（`key_forbidden_chars`・`context_reserve_tokens`・`limits_defaults` 等）。"""
        return self._config

    @property
    def data(self) -> AreaAData:
        """保持している**生の構造**（候補の素材。保存のたびに読み直さない＝ A-11・FR-025 (b)）。"""
        return self._data

    @property
    def generation(self) -> int:
        """現在の世代。**保存経路が「現在 + 1」を決める**（010 SD-01・D-a）。"""
        return self._snapshot.generation

    def apply(self, snapshot: SettingsSnapshot, data: AreaAData) -> None:
        """段 4-3〜4-4: 差し替えてから生の構造を進める（**書込成功後の 1 箇所だけ**＝ P7）。

        `replace_snapshot` は単調増加を検査して属性 1 つを入れ替えるだけである（前半のまま）。
        例外が飛べば `_data` は旧のまま残り、次の保存は旧を基点にする。

        Args:
            snapshot: 世代を +1 済みの新しいスナップショット。
            data: 対応する候補の生の構造。
        """
        self._service.replace_snapshot(snapshot)
        self._snapshot = snapshot
        self._data = data

    # -- F11 プロジェクト（#1〜#5） ----------------------------------------------------

    def list_projects(self) -> list[ProjectView]:
        """#1 プロジェクト一覧。"""
        return [self._project_view(key) for key in sorted(self._data.projects_raw)]

    def create_project(self, spec: ProjectSpec) -> tuple[ProjectView, SaveOutcome]:
        """#2 プロジェクト登録（キー規則 → 保管庫の実在確認 → 候補 → 保存）。

        Raises:
            SettingsValidationError: キー規則・実在確認・候補全体の検査の違反（→ 422）。
        """
        candidate, changes = projects.build_create(
            self._data, spec, config=self._config, vault_reader=self._vault_reader
        )
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self._project_view(spec.project_key), outcome

    def get_project(self, project_key: str) -> ProjectView:
        """#3 プロジェクト 1 件。

        Raises:
            ProjectNotFound: 未登録のキー（→ 404）。
        """
        if project_key not in self._data.projects_raw:
            raise ProjectNotFound(project_key)
        return self._project_view(project_key)

    def update_project(
        self, project_key: str, spec: ProjectSpec
    ) -> tuple[ProjectView, SaveOutcome]:
        """#4 プロジェクト編集（**フォルダ実体を触らない**＝ FR-039）。

        Raises:
            ProjectNotFound: 未登録のキー（→ 404）。
            SettingsValidationError: 本文のキーの食い違い・実在確認・候補の検査（→ 422）。
        """
        candidate, changes = projects.build_update(
            self._data, project_key, spec, vault_reader=self._vault_reader
        )
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self._project_view(project_key), outcome

    def delete_project(self, project_key: str) -> SaveOutcome:
        """#5 プロジェクト解除（削除ガード 3 分岐 → 退避 ＋ 上限の上書き除去）。

        **候補の検査に落ちたときも 409** に畳む（正本に 422 が無い＝ 010 SD-03・D-2）。
        本文は `Detail` の**単一文字列**になり、機械可読な違反一覧は返らない。

        Raises:
            ProjectNotFound: 未登録のキー（→ 404）。
            ProjectDeletionBlocked: 予約キー／未完了課題あり／盤面が読めない／検査の違反（→ 409）。
        """
        candidate, changes = projects.build_delete(
            self._data, project_key, board_reader=self._board_reader
        )
        try:
            return save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        except SettingsValidationError as exc:
            msg = f"解除できない（設定の検査に違反がある）: {project_key} / {exc}"
            raise ProjectDeletionBlocked(msg) from exc

    def list_project_roles(self, project_key: str) -> RolesView:
        """役割ごとの実効設定（D26。閲覧のみ・領域 A を書かない）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        self._require_project(project_key)
        return roles.build_roles_view(self._service, self._data, project_key)

    def _project_view(self, project_key: str) -> ProjectView:
        """`projects/<key>/project.yaml` の生の構造を応答の形へ写す。

        **`project_key` はディレクトリ名から注入する**（ファイルに書かれていない＝ FR-038）。
        `is_reserved` は宣言または予約キーそのもの（P5D-014）。
        """
        raw = dict(self._data.projects_raw[project_key])
        folders = raw.get("folders")
        spec = ProjectFoldersSpec.model_validate(folders if isinstance(folders, dict) else {})
        raw_repos = raw.get("repos")
        repos = ProjectReposSpec.model_validate(raw_repos) if isinstance(raw_repos, dict) else None
        return ProjectView(
            project_key=project_key,
            name=str(raw.get("name", "")),
            folders=spec,
            is_reserved=bool(raw.get("is_reserved", False)) or project_key == projects.RESERVED_KEY,
            repos=repos,
        )

    # -- F12 メンバ（#6〜#11） ---------------------------------------------------------

    def list_agents(self, project_key: str | None) -> list[AgentView]:
        """#6 メンバ一覧（PJ 無指定なら**全体の既定行**＝ `project_key` が `null`）。

        レベル付き一覧（FR-069・D-4）の判別点はこの `project_key` の `null` である。
        **専用の口を足さない**（R-11）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        if project_key is None:
            return [
                self._global_agent_view(PersonaKey(name))
                for name in sorted(self._data.agents_global)
                if name in PersonaKey.__members__
            ]
        if project_key not in self._data.projects_raw:
            raise ProjectNotFound(project_key)
        return [
            self._agent_view(project_key, PersonaKey(name))
            for name in sorted(self._data.agents_raw.get(project_key, {}))
            if name in PersonaKey.__members__
        ]

    def get_global_agent(self, persona_key: PersonaKey) -> AgentSpec:
        """#7 全体のペルソナ既定 1 件。

        Raises:
            SettingsItemNotFound: そのペルソナの既定が `global/agents.yaml` に無い（→ 404）。
        """
        raw = self._data.agents_global.get(persona_key.value)
        if raw is None:
            msg = f"全体のペルソナ既定が無い: {persona_key.value}"
            raise SettingsItemNotFound(msg)
        return AgentSpec.model_validate(raw)

    def put_global_agent(
        self, persona_key: PersonaKey, spec: AgentSpec
    ) -> tuple[AgentSpec, SaveOutcome]:
        """#8 全体のペルソナ既定の編集（**V-04** が必須 3 を見る）。

        Raises:
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = members.build_put_global(self._data, persona_key, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_global_agent(persona_key), outcome

    def get_agent(self, project_key: str, persona_key: PersonaKey) -> AgentView:
        """#9 メンバ 1 件（merge 済み）。

        Raises:
            MemberNotFound: PJ 側にそのペルソナのキーが無い（PJ 未登録も同じ＝契約どおり）。
        """
        return self._agent_view(project_key, persona_key)

    def put_agent(
        self, project_key: str, persona_key: PersonaKey, spec: AgentSpec
    ) -> tuple[AgentView, SaveOutcome]:
        """#10 メンバの upsert（**キーの存在＝メンバ**。中身が空でもよい）。

        ⚠ **新規作成は V-19 で必ず落ちる**（B-49。到達可能なのは既に居るメンバの編集だけ）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404。D-3・FR-075）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = members.build_put(self._data, project_key, persona_key, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self._agent_view(project_key, persona_key), outcome

    def delete_agent(self, project_key: str, persona_key: PersonaKey) -> SaveOutcome:
        """#11 メンバ削除（**V-21** が孤児の権限行を落とす＝ FR-051）。

        ⚠ **口からは到達不能**である（B-49。権限行を先に消す道は V-19 が塞ぐ）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
            MemberNotFound: そのペルソナのキーが無い（→ 404）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = members.build_delete(self._data, project_key, persona_key)
        return save_candidate(self, candidate, changes, EffectClass.next_kickoff)

    def _agent_view(self, project_key: str, persona_key: PersonaKey) -> AgentView:
        """解決済み（merge 後）のメンバを応答の形へ写す（台帳の導出値を載せる）。

        Raises:
            MemberNotFound: PJ 側にそのペルソナのキーが無い（`resolve` の契約どおり）。
        """
        member = self._service.resolve(project_key, persona_key)
        raw = normalize_mapping(self._data.agents_raw.get(project_key, {}).get(persona_key.value))
        return AgentView(
            **AgentSpec.model_validate(raw).model_dump(exclude_unset=True),
            member_id=member.member_id,
            project_key=project_key,
            persona_key=persona_key,
            bd_actor=member.bd_actor,
            casbin_subject=member.casbin_subject,
            resolved_llm_profile=member.llm_profile.name,
        )

    def _global_agent_view(self, persona_key: PersonaKey) -> AgentView:
        """全体の既定行を応答の形へ写す（**`project_key` は `null`**＝ R-11 の判別点）。

        既定行は台帳の主体ではないので、導出値（`member_id`・`bd_actor`・`casbin_subject`）は
        **空文字**にする。`resolved_llm_profile` は `llm_profiles.yaml` の `default` へ解決した
        名前（届かなければ空文字）である。
        """
        raw = normalize_mapping(self._data.agents_global.get(persona_key.value))
        resolved = resolve_llm_name(raw, self._data.default_profile)
        return AgentView(
            **AgentSpec.model_validate(raw).model_dump(exclude_unset=True),
            member_id="",
            project_key=None,
            persona_key=persona_key,
            bd_actor="",
            casbin_subject="",
            resolved_llm_profile=resolved or "",
        )

    # -- F17 段テンプレート（#12〜#16） -------------------------------------------------

    def get_global_tasks(self) -> TaskTemplatesView:
        """#12 全体の段テンプレート（7 キー）。"""
        return TaskTemplatesView.model_validate(self._data.tasks_global)

    def put_global_tasks(self, view: TaskTemplatesView) -> tuple[TaskTemplatesView, SaveOutcome]:
        """#13 全体の段テンプレートの編集（**V-08** が 7 キー必須を見る）。

        Raises:
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_put_global_tasks(self._data, view)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_global_tasks(), outcome

    def get_project_tasks(self, project_key: str) -> TaskTemplatesView:
        """#14 PJ の**上書きだけ**（merge 結果ではない＝正本の summary）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        self._require_project(project_key)
        return TaskTemplatesView.model_validate(self._data.tasks_raw.get(project_key) or {})

    def put_project_tasks(
        self, project_key: str, view: TaskTemplatesView
    ) -> tuple[TaskTemplatesView, SaveOutcome]:
        """#15 PJ の上書きの編集（キー単位の丸ごと置換）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_put_project_tasks(self._data, project_key, view)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_project_tasks(project_key), outcome

    def delete_project_tasks(self, project_key: str) -> SaveOutcome:
        """#16 PJ の上書きを全解除（全体の 7 キーに戻る＝ FR-070）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        candidate, changes = ai_config.build_delete_project_tasks(self._data, project_key)
        return save_candidate(self, candidate, changes, EffectClass.next_kickoff)

    # -- F13 LLM プロファイル（#17〜#21） ----------------------------------------------

    def list_llm_profiles(self) -> LlmProfilesView:
        """#17 LLM プロファイル一覧。"""
        return LlmProfilesView(
            profiles=[self._profile_view(name) for name in sorted(self._data.profiles_raw)],
            default=self._data.default_profile,
        )

    def create_llm_profile(self, spec: LlmProfileSpec) -> tuple[LlmProfileView, SaveOutcome]:
        """#18 LLM プロファイル作成。

        Raises:
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_create_profile(self._data, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self._profile_view(spec.name), outcome

    def get_llm_profile(self, name: str) -> LlmProfileView:
        """#19 LLM プロファイル 1 件。

        Raises:
            SettingsItemNotFound: 未登録のプロファイル名（→ 404）。
        """
        if name not in self._data.profiles_raw:
            msg = f"LLM プロファイルが無い: {name}"
            raise SettingsItemNotFound(msg)
        return self._profile_view(name)

    def update_llm_profile(
        self, name: str, spec: LlmProfileSpec
    ) -> tuple[LlmProfileView, SaveOutcome]:
        """#20 LLM プロファイル編集。

        Raises:
            SettingsItemNotFound: 未登録のプロファイル名（→ 404）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_update_profile(self._data, name, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self._profile_view(name), outcome

    def delete_llm_profile(self, name: str) -> SaveOutcome:
        """#21 LLM プロファイル削除（**既定の削除は拒否**＝保存時だけの 3 つ目の規則）。

        Raises:
            SettingsItemNotFound: 未登録のプロファイル名（→ 404）。
            SettingsValidationError: 既定の削除・参照中の削除（V-15）（→ 422）。
        """
        candidate, changes = ai_config.build_delete_profile(self._data, name)
        return save_candidate(self, candidate, changes, EffectClass.next_kickoff)

    # -- F14 上限（#22〜#25） -----------------------------------------------------------

    def get_limits(self) -> LimitsView:
        """#22 上限（`global` は**解決値**・`overrides` は PJ 上書き）。

        ⚠ `global` が**解決値**である（`global/limits.yaml` に `global:` が無ければ **CFG-39 の
        ③ 段**＝ `config.json` の `limits_defaults`）ことは data-model §2-4 の注意どおりであり、
        **この応答をそのまま書き戻さない**（書き戻すと ③ 段が永久に効かなくなる）。
        """
        snapshot = self._snapshot
        # `global` は Python の予約語なのでキーワード引数で渡せない（alias 経由で組む）。
        return LimitsView.model_validate(
            {
                "global": _limit_spec(snapshot.limits_global),
                "overrides": {
                    key: _limit_spec(policy) for key, policy in snapshot.limits_overrides.items()
                },
            }
        )

    def put_global_limits(self, spec: LimitSpec) -> tuple[LimitsView, SaveOutcome]:
        """#23 全体既定の保存（**ここで初めて `global:` を書く**＝ ③ 段が役目を終える）。

        Raises:
            SettingsValidationError: 候補全体の検査の違反（V-27 ほか）（→ 422）。
        """
        candidate, changes = ai_config.build_put_global_limits(self._data, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_limits(), outcome

    def put_project_limits(
        self, project_key: str, spec: LimitSpec
    ) -> tuple[LimitsView, SaveOutcome]:
        """#24 PJ 上書きの保存（**`overrides` だけ**を書く＝ plan D-l）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404。C-6）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_put_project_limits(self._data, project_key, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_limits(), outcome

    def delete_project_limits(self, project_key: str) -> SaveOutcome:
        """#25 PJ 上書きの解除（同上）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        candidate, changes = ai_config.build_delete_project_limits(self._data, project_key)
        return save_candidate(self, candidate, changes, EffectClass.next_kickoff)

    # -- F15 コマンド定義（#26〜#30） --------------------------------------------------

    def list_commands(self) -> list[CommandDefSchema]:
        """#26 コマンド定義一覧。"""
        return [self._command_view(name) for name in sorted(self._data.commands_raw)]

    def create_command(self, spec: CommandDefSchema) -> tuple[CommandDefSchema, SaveOutcome]:
        """#27 コマンド定義の追加（**新規名は V-24／V-25 の相互ロックで到達不能**＝ B-40）。

        ⚠ 到達不能なのは**新規名のときだけ**である。**既に定義済みの名前**を送ると同名の
        差し替えになり、V-25 も V-24 も発火せず **201 が返って `argv` が黙って上書きされる**
        （実測 2026-08-27・B-40 の範囲の訂正）。

        Raises:
            SettingsValidationError: 候補全体の検査の違反（新規名なら必ず V-25）（→ 422）。
        """
        candidate, changes = ai_config.build_create_command(self._data, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.immediate)
        return self._command_view(spec.name), outcome

    def get_command(self, name: str) -> CommandDefSchema:
        """#28 コマンド定義 1 件。

        Raises:
            UnknownCommand: 定義に無い名前（→ 404）。
        """
        if name not in self._data.commands_raw:
            raise UnknownCommand(name)
        return self._command_view(name)

    def update_command(
        self, name: str, spec: CommandDefSchema
    ) -> tuple[CommandDefSchema, SaveOutcome]:
        """#29 コマンド定義の編集。

        Raises:
            UnknownCommand: 定義に無い名前（→ 404）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_update_command(self._data, name, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.immediate)
        return self._command_view(name), outcome

    def delete_command(self, name: str) -> SaveOutcome:
        """#30 コマンド定義の削除（**B-51: V-24／V-25 の相互ロックで到達不能**）。

        削除後の候補では必ず V-24 が発火し、policy 行を先に消す候補は V-25 で落ちる
        （`ai_config.build_delete_command` の docstring。**B-40** と同型）。

        Raises:
            UnknownCommand: 定義に無い名前（→ 404）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_delete_command(self._data, name)
        return save_candidate(self, candidate, changes, EffectClass.immediate)

    # -- F16 権限ポリシー（#31〜#32） --------------------------------------------------

    def get_policy(self, project_key: str) -> PolicyView:
        """#31 権限表。

        `model_conf` は**参照用の本文**である（CRUD 対象外＝ FR-064）。領域 A の正本を読む
        **唯一の実行時の読取**であり、候補の素材ではない（候補は保持している生の構造から
        組む＝ A-11・FR-025 (b)）。読めなければ空文字にする（起動時検査が別途担保する）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        self._require_project(project_key)
        try:
            model_conf = self._reader.model_conf_path().read_text(encoding="utf-8")
        except OSError:
            model_conf = ""
        return PolicyView(
            rows=self._policy_rows(project_key), project_key=project_key, model_conf=model_conf
        )

    def put_policy(self, project_key: str, spec: PolicySpec) -> tuple[PolicyView, SaveOutcome]:
        """#32 権限表の編集（候補を判定器に読ませる構文検査つき＝ save.py 段 2-4）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
            SettingsValidationError: 構文・列数・空打ち・V-21〜V-23 の違反（→ 422）。
        """
        candidate, changes = ai_config.build_put_policy(self._data, project_key, spec)
        outcome = save_candidate(self, candidate, changes, EffectClass.immediate)
        return self.get_policy(project_key), outcome

    # -- F17 ルール・Hook・人間（#33〜#38） --------------------------------------------

    def get_project_rules(self, project_key: str) -> TextBody:
        """#33 共通ルール（不在なら空文字＝ FR-027。前半の読取をそのまま使う）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        return TextBody(body=self._service.get_rules(project_key).project_rules)

    def put_project_rules(self, project_key: str, body: TextBody) -> tuple[TextBody, SaveOutcome]:
        """#34 共通ルールの編集（**空文字も可**。正本は 200／404 だけで 422 を持たない）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        candidate, changes = ai_config.build_put_rules(self._data, project_key, body)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_project_rules(project_key), outcome

    def get_hooks(self, project_key: str) -> HooksConfigSchema:
        """#35 Hook 宣言（領域 A の 1 宣言 1 地点を、口の**1 要素の一覧**へ畳む）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
        """
        config = self._service.get_hooks(project_key)
        return HooksConfigSchema(
            hooks=[
                HookRefSchema(name=ref.name, points=[ref.points], agents=list(ref.agents))
                for ref in config.hooks
            ]
        )

    def put_hooks(
        self, project_key: str, config: HooksConfigSchema
    ) -> tuple[HooksConfigSchema, SaveOutcome]:
        """#36 Hook 宣言の編集（**V-18** は形式検査だけ。接続も PATH 解決もしない）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー（→ 404）。
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_put_hooks(self._data, project_key, config)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_hooks(project_key), outcome

    def get_humans(self) -> HumansView:
        """#37 人間の行為者。"""
        return HumansView(human_actors=human_actor_names(self._data.humans_raw))

    def put_humans(self, view: HumansView) -> tuple[HumansView, SaveOutcome]:
        """#38 人間の行為者の編集（**V-20** が 3 対の衝突を見る）。

        Raises:
            SettingsValidationError: 候補全体の検査の違反（→ 422）。
        """
        candidate, changes = ai_config.build_put_humans(self._data, view)
        outcome = save_candidate(self, candidate, changes, EffectClass.next_kickoff)
        return self.get_humans(), outcome

    # -- 応答の写像 --------------------------------------------------------------------

    def _require_project(self, project_key: str) -> None:
        """未登録キーを **404** の契約例外にする（FR-075）。

        Raises:
            ProjectNotFound: 未登録のプロジェクトキー。
        """
        if project_key not in self._data.projects_raw:
            raise ProjectNotFound(project_key)

    def _profile_view(self, name: str) -> LlmProfileView:
        """LLM プロファイルを応答の形へ写す（**`threshold_t` は小文字**＝ A-10・B-43）。"""
        profile = self._snapshot.llm_profiles[name]
        return LlmProfileView(
            name=profile.name,
            provider_kind=profile.provider_kind,
            base_url=profile.base_url,
            model_id=profile.model_id,
            context_window=profile.context_window,
            api_key_ref=profile.api_key_ref,
            extra_headers=dict(profile.extra_headers),
            is_default=self._data.default_profile == name,
            threshold_t=profile.threshold_t,
        )

    def _command_view(self, name: str) -> CommandDefSchema:
        """コマンド定義を応答の形へ写す（**引数列を組み立てない**＝ D07-e）。"""
        return CommandDefSchema(name=name, argv=list(self._snapshot.commands[name].argv))

    def _policy_rows(self, project_key: str) -> list[PolicyRow]:
        """権限表の行を応答の形へ写す（先頭要素が区分＝ `AreaAReader.read_policy` の形）。"""
        return [
            PolicyRow(ptype="p" if row[0] == "p" else "g", fields=list(row[1:]))
            for row in self._data.policy_rows.get(project_key, ())
        ]


def _limit_spec(policy: LimitPolicy) -> LimitSpec:
    """解決済みの上限を応答の形へ写す（`scope` は口に出さない＝内部の導出値）。"""
    return LimitSpec(
        max_requests_per_kickoff=policy.max_requests_per_kickoff,
        max_cost_per_kickoff=policy.max_cost_per_kickoff,
    )
