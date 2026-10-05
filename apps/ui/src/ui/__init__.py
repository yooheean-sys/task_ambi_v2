"""統合した起動点のパッケージ（app に各モジュールの UI ページを載せる薄い器）。

起動骨格は `ui.main`（`create_ui_app`・`main`）・`ui.config`（`.env` と base URL の導出）・
`ui.pages`（UI ページの登録点と構造型 `UiPage`）・`ui.shell`（経路の登録・共通の見出し・入口）の
4 ファイルが持つ。**業務ロジックを持たない**（contracts/ui-shell.md §1・`002` FR-029）。
"""
