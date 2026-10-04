from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from rankeddojo.adapters.theme.user_theme_loader import UserThemeLoader
from rankeddojo.adapters.ui.qt.components.combo_wheel_guard import install_combo_box_wheel_guard
from rankeddojo.adapters.ui.qt.main_window import MainWindow
from rankeddojo.adapters.ui.qt.startup_window import StartupWindow
from rankeddojo.adapters.ui.qt.i18n import LocaleService
from rankeddojo.adapters.ui.qt.theme import ThemeManager
from rankeddojo.adapters.ui.qt.theme.themes import TERMINAL, THEME_REGISTRY
from rankeddojo.infrastructure.app_factory import AppFactory
from rankeddojo.infrastructure.data_dir_migration import migrate_legacy_data_dir
from rankeddojo.infrastructure.paths import user_themes_dir


class DesktopApp:
    def __init__(self) -> None:
        self._data_dir_migration = migrate_legacy_data_dir()
        self._qt_app = QApplication(sys.argv)
        self._combo_wheel_guard = install_combo_box_wheel_guard(self._qt_app)
        factory = AppFactory()
        self._config_repository = factory.create_config_repository()
        self._workspace_service = factory.create_workspace_service()
        self._locale_service = LocaleService(self._config_repository, self._qt_app)
        UserThemeLoader(base=TERMINAL).load_into(THEME_REGISTRY, user_themes_dir())
        self._theme_manager = ThemeManager(self._config_repository.load_theme())
        self._window: MainWindow | StartupWindow | None = None

    def run(self) -> int:
        startup_state = self._workspace_service.get_startup_state()
        if startup_state.has_workspace and startup_state.workspace_path is not None:
            self._show_main_window(startup_state.workspace_path)
        else:
            self._show_startup_window()
        return self._qt_app.exec()

    def _show_startup_window(self) -> None:
        startup_window = StartupWindow(self._workspace_service, self._theme_manager, self._locale_service)
        startup_window.workspace_configured.connect(self._show_main_window)
        self._window = startup_window
        startup_window.show()

    def _show_main_window(self, workspace_path: Path) -> None:
        main_window = MainWindow(
            workspace_path,
            AppFactory().create_mvp_coordinator(workspace_path),
            self._theme_manager,
            self._locale_service,
        )
        self._window = main_window
        main_window.show()
