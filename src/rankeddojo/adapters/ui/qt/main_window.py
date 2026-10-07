from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from rankeddojo.adapters.ui.qt.components import widgets as ui
from rankeddojo.adapters.ui.qt.components.cursor import CursorController
from rankeddojo.adapters.ui.qt.i18n import LOCALE_LABELS, LocaleService, SUPPORTED_UI_LOCALES, tr
from rankeddojo.adapters.ui.qt.subject_sections import (
    ALLOWED_HEADINGS,
    CONSTRAINTS_HEADINGS,
    EXPECTED_FILE_HEADINGS,
    NOT_ALLOWED_HEADINGS,
    demote_examples_heading,
    extract_list_section,
    strip_sections,
)
from rankeddojo.adapters.ui.qt.task_runner import TaskRunner
from rankeddojo.adapters.ui.qt.theme import ThemeManager, ThemeTokens
from rankeddojo.application.capabilities import default_exercise_capabilities
from rankeddojo.application.history_service import HistoryQuery
from rankeddojo.application.study_intent import StudyIntent
from rankeddojo.application.engine.activity_preflight import ActivityPreflightStatus
from rankeddojo.application.engine.trace_summary import TraceSummary
from rankeddojo.resources import PACK_CONTRACT, pack_contract_text, resource_path
from rankeddojo.application.mvp_models import (
    ActiveExercise,
    ActivityProgress,
    CorrectionOutcome,
    ExerciseRef,
    GradingOutcome,
    LastSessionSummary,
)
from rankeddojo.application.use_cases.mvp_coordinator import (
    ExamState,
    MVPTrainerCoordinator,
    PreflightResult,
    TrainingOptions,
)
from rankeddojo.application.use_cases.get_learning_track import (
    LearningActivityRef,
    LearningTrackView,
    is_activity_unlocked,
)

MENU_WIDTH = 460
ACTIVITY_PROGRESS_LABELS: dict[ActivityProgress, str] = {
    ActivityProgress.COMPLETED: "Concluído",
    ActivityProgress.ATTEMPTED: "Tentado",
    ActivityProgress.NOT_STARTED: "Não feito",
}


class MainWindow(QMainWindow):
    def __init__(
        self,
        workspace_path: Path,
        coordinator: MVPTrainerCoordinator,
        theme_manager: ThemeManager | None = None,
        locale_service: LocaleService | None = None,
        task_runner: TaskRunner | None = None,
    ) -> None:
        super().__init__()
        self._workspace_path = workspace_path
        self._coordinator = coordinator
        self._tasks = task_runner or TaskRunner(self)
        self._active: ActiveExercise | None = None
        self._last_outcome: CorrectionOutcome | None = None
        self._last_session_summary: LastSessionSummary | None = None
        self._trace_summary: TraceSummary | None = None
        self._training_options: TrainingOptions | None = None
        self._exam_state: ExamState | None = None
        self._mode = "training"
        self._training_kind = "level"
        self._pending_action: Callable[[], None] | None = None
        self._editor_targets: dict[tuple[str, str], Path] = {}
        self._level_checks: list[ui.OptionButton] = []
        self._footer_buttons: list[tuple[QPushButton, str]] = []
        self._footer_hint_bars: list[tuple[ui.HintBar, list[tuple[str, str]]]] = []

        self._theme = theme_manager or ThemeManager(self._saved_theme_key())
        self._locale = locale_service or LocaleService(coordinator)
        self._locale.locale_changed.connect(self._on_locale_changed)
        self._cursor = CursorController(self)

        self.setWindowTitle("RankedDojo")
        self.setMinimumSize(760, 560)
        self.resize(1100, 760)

        self._stack = QStackedWidget()
        self._home_page = self._build_home_page()
        self._study_page = self._build_study_page()
        self._training_page = self._build_training_page()
        self._exam_page = self._build_exam_page()
        self._exam_prepare_page = self._build_exam_prepare_page()
        self._exercise_page = self._build_exercise_page()
        self._history_page = self._build_history_page()
        self._settings_page = self._build_settings_page()
        self._pack_help_page = self._build_pack_help_page()
        self._learning_language: str | None = None
        self._learning_track_view: LearningTrackView | None = None
        self._learning_track_rows: list[LearningActivityRef] = []
        self._learning_languages_page = self._build_learning_languages_page()
        self._learning_track_page = self._build_learning_track_page()
        for page in (
            self._home_page,
            self._study_page,
            self._training_page,
            self._exam_page,
            self._exam_prepare_page,
            self._exercise_page,
            self._history_page,
            self._settings_page,
            self._pack_help_page,
            self._learning_languages_page,
            self._learning_track_page,
        ):
            self._stack.addWidget(page)
        self.setCentralWidget(self._stack)

        self._theme.apply(self)
        self._theme.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed(self._theme.tokens)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_exam)
        self._install_shortcuts()
        self._refresh_packs()
        self._show_resume_if_needed()
        self._refresh_home_status()
        self._refresh_study_languages()
        self._retranslate_static_ui()
        self._cursor.enter_page(self._home_page)

    # ------------------------------------------------------------------ tema
    def _saved_theme_key(self) -> str | None:
        loader = getattr(self._coordinator, "theme_key", None)
        return loader() if callable(loader) else None

    def _on_theme_changed(self, tokens: ThemeTokens) -> None:
        self._cursor.configure(
            enabled=tokens.blink_cursor,
            animations=tokens.animations,
            cursor_char=tokens.cursor_char,
        )
        self._subject.apply_theme(tokens)
        if self._stack.currentWidget() is self._history_page:
            self._show_history()

    def _change_theme(self, index: int) -> None:
        key = self._theme_combo.itemData(index)
        if not key:
            return
        self._theme.set_theme(str(key))
        saver = getattr(self._coordinator, "save_theme", None)
        if callable(saver):
            saver(str(key))

    # --------------------------------------------------------------- i18n
    def _t(self, text: str, **values: object) -> str:
        translated = self._locale.translate(text)
        return translated.format(**values) if values else translated

    def _action(self, text: str, style: str = "bracket", **values: object) -> str:
        translated = self._t(text, **values).upper()
        if style == "primary":
            return f"> {translated}"
        if style == "indexed":
            return translated
        return f"[ {translated} ]"

    def _set_button(self, button: QPushButton, text: str) -> None:
        self._cursor.set_button_text(button, text)

    def _on_locale_changed(self, _locale: str) -> None:
        self._retranslate_static_ui()
        self._refresh_home_status()
        self._refresh_study_languages()
        self._refresh_packs()
        self._refresh_levels()
        if self._active is not None:
            self._refresh_exercise_frame()
            if self._exam_state is not None:
                self._render_exam_timer(self._exam_state)
        if self._stack.currentWidget() is self._history_page:
            self._show_history()
        elif self._stack.currentWidget() is self._settings_page:
            self._show_settings()
        elif self._stack.currentWidget() is self._exam_prepare_page:
            self._show_exam_prepare()
        elif self._stack.currentWidget() is self._learning_languages_page:
            self._refresh_learning_languages()
        elif self._stack.currentWidget() is self._learning_track_page and self._learning_language is not None:
            self._render_learning_track()

    def _set_locale_from_combo(self, index: int) -> None:
        locale = self._locale_combo.itemData(index)
        if isinstance(locale, str):
            self._locale.set_locale(locale)

    def _retranslate_static_ui(self) -> None:
        self._set_title_label(self._title_home, "RankedDojo")
        self._home_ready_pill.setText(f"\u25cf {self._t('Pronto').upper()}")
        self._home_question_label.setText(self._t("O que você vai fazer hoje?"))
        self._set_button(self._home_learn_button, self._t("Aprender").upper())
        self._set_button(self._home_train_button, self._t("Treinar").upper())
        self._set_button(self._home_exam_button, self._t("Prova").upper())
        self._set_button(self._home_study_button, self._t("Quero estudar algo novo").upper())
        self._set_button(self._home_history_button, self._t("Histórico").upper())
        self._set_button(self._home_settings_button, self._t("Configurações").upper())
        self._home_pack_empty_title.setText(self._t("Ainda não há packs de estudo.").upper())
        self._home_pack_empty_description.setText(self._t("Apenas packs de exemplo estão disponíveis."))
        self._home_pack_empty_description_2.setText(self._t("Gere ou importe um pack para começar."))
        self._set_button(self._home_generate_pack_button, self._action("Gerar pack"))
        self._set_button(self._home_import_pack_button, self._action("Importar Pack"))
        self._last_session_header.setText(self._t("Última sessão").upper())
        self._set_button(self._last_session_continue_button, self._t("Continuar").upper())
        self._refresh_home_last_session()

        self._set_title_label(self._study_title, self._t("Quero estudar algo novo título"))
        self._study_description.setText(self._t("Descreva o que quer estudar, gere um prompt compatível e importe o pack resultante."))
        self._study_topic.setPlaceholderText(self._t("Ex.: ponteiros e strings, OOP em Python, arrays em Java..."))
        self._study_custom_language.setPlaceholderText(self._t("Ex.: Rust, Go, Kotlin..."))
        self._study_section_label.setText(f"> {self._t('O que você quer estudar?').upper()}")
        for label, text in self._study_field_labels:
            label.setText(self._t(text))
        self._reset_combo_items(self._study_progression_combo, ("Progressive", "Uniform"), ("progressive", "uniform"))
        self._reset_combo_items(
            self._study_levels_combo,
            ("Automatic", "1", "2", "3", "4", "5", "6"),
            ("automatic", "1", "2", "3", "4", "5", "6"),
        )
        self._reset_combo_items(
            self._study_exercises_per_level_combo,
            ("Automatic", "2", "3", "4", "5"),
            ("automatic", "2", "3", "4", "5"),
        )
        self._reset_combo_items(self._study_goal_combo, ("Aprender", "Praticar", "Revisar", "Validar conhecimento"))
        self._reset_combo_items(self._study_format_combo, ("Exercícios", "Projeto", "Misto", "Revisão", "Simulado"))
        self._reset_combo_items(self._study_content_language_combo, ("Português (pt-BR)", "Inglês (en)"), ("pt-BR", "en"))
        self._reset_combo_items(self._study_size_combo, ("Curto", "Médio", "Completo"))
        self._set_button(self._generate_prompt_button, self._action("Gerar prompt", "primary"))
        self._set_button(self._copy_prompt_button, self._action("Copiar prompt"))
        self._set_button(self._study_import_button, self._action("Importar Pack"))
        self._study_prompt_output.setPlaceholderText(self._t("O prompt gerado aparecerá aqui."))
        if not self._study_prompt_output.toPlainText().strip():
            self._study_status.setText(self._t("1. gere o prompt · 2. copie · 3. cole na IA que preferir · 4. importe o pack"))

        self._set_title_label(self._training_title, self._t("Treino").upper())
        self._set_button(self._level_training_button, f"[1] {self._t('Treino por Level').upper()}")
        self._set_button(self._random_training_button, f"[2] {self._t('Treino aleatório').upper()}")
        self._training_pack_label.setText(f"> {self._t('Rank / Pack').upper()}")
        self._training_levels_label.setText(f"> {self._t('Levels').upper()}")
        self._random_draw_label.setText(f"> {self._t('Sorteio').upper()}")
        self._prioritize_radio.set_label(self._t("Priorizar não concluídos"))
        self._only_uncompleted_radio.set_label(self._t("Somente não concluídos"))
        self._all_radio.set_label(self._t("Todos os exercícios"))
        self._allow_repeated_check.set_label(self._t("Permitir repetidos"))
        self._set_button(self._start_training_button, self._action("Start training", "primary"))
        self._choose_level_training() if self._training_kind == "level" else self._choose_random_training()

        self._set_title_label(self._exam_title, self._t("Modo prova").upper())
        self._exam_resume_header.setText(f"● {self._t('Prova em andamento').upper()}")
        self._set_button(self._resume_exam_button, self._action("Continuar prova", "primary"))
        self._set_button(self._end_exam_button, self._action("Encerrar prova"))
        self._exam_new_label.setText(f"> {self._t('Nova prova — Rank / Pack').upper()}")
        self._set_button(self._prepare_exam_button, self._action("Preparar prova", "primary"))
        self._set_title_label(self._exam_prepare_title, self._t("Preparar prova").upper())
        self._set_button(self._start_exam_button, self._action("Start exam", "primary"))

        self._set_button(self._open_editor_button, self._action("Abrir IDE"))
        self._set_button(self._correct_button, self._action("Corrigir", "primary"))
        self._set_button(self._trace_button, self._action("Ver trace"))
        self._set_button(self._next_button, self._action("Próximo/trocar"))
        self._set_button(self._exercise_back_button, self._action("Voltar"))
        self._sidebar_expected_header.setText(f"> {self._t('Arquivos esperados').upper()}")
        self._sidebar_allowed_header.setText(f"> {self._t('Permitido').upper()}")
        self._sidebar_not_allowed_header.setText(f"> {self._t('Não permitido').upper()}")
        self._sidebar_constraints_header.setText(f"> {self._t('Regras').upper()}")

        self._set_title_label(self._history_title, self._t("Histórico").upper())
        if hasattr(self, "_history_nav_buttons"):
            nav_labels = {
                "overview": "Visão geral",
                "training_sessions": "Treino",
                "exam_sessions": "Provas",
            }
            for key, button in self._history_nav_buttons.items():
                self._set_button(button, self._t(nav_labels[key]).upper())
            self._history_views_label.setText(self._t("VIEWS"))
            self._history_learning_label.setText(self._t("LEARNING"))
            self._history_packs_label.setText(self._t("PACKS"))
            self._history_sessions_label.setText(self._t("SESSIONS"))
            self._set_button(self._history_trace_button, self._action("Abrir trace completo"))
            self._refresh_history_sidebar()
            self._render_history()

        self._set_title_label(self._settings_title, self._t("Configurações").upper())
        self._locale_label.setText(self._t("Idioma da interface"))
        self._refresh_locale_combo()
        self._refresh_editor_combo()
        self._refresh_settings_cards()
        self._set_title_label(self._pack_help_title, self._t("Como criar um Pack").upper())
        self._set_button(self._open_full_docs_button, self._action("Abrir documentação completa"))

        self._set_title_label(self._learning_languages_title, self._t("Trilha de aprendizado").upper())
        self._learning_languages_subtitle.setText(self._t("Escolha uma linguagem para ver a trilha."))
        self._set_title_label(self._learning_track_title, self._t("Trilha de aprendizado").upper())
        self._set_button(self._learning_continue_button, self._action("Continuar", "primary"))
        self._set_button(self._learning_open_button, self._action("Abrir atividade"))
        self._learning_track_table.setHorizontalHeaderLabels(
            (
                self._t("Nível").upper(),
                self._t("Título").upper(),
                self._t("Tópicos").upper(),
                self._t("Dificuldade").upper(),
                self._t("Estado").upper(),
            )
        )

        for button, source in self._footer_buttons:
            self._set_button(button, self._footer_text(source))
        for hint_bar, hints in self._footer_hint_bars:
            hint_bar.set_hints([(key, self._t(text)) for key, text in hints])

    def _footer_text(self, source: str) -> str:
        if source == "[ VOLTAR ]":
            return self._action("Voltar")
        if source == "[ VOLTAR AO EXERCÍCIO ]":
            return self._action("Voltar ao exercício")
        if source == "[ VOLTAR PARA CONFIGURAÇÕES ]":
            return self._action("Voltar para Configurações")
        return source

    def _reset_combo_items(self, combo: QComboBox, labels: tuple[str, ...], values: tuple[str, ...] | None = None) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for index, label in enumerate(labels):
            value = values[index] if values is not None else label
            combo.addItem(self._t(label), value)
        if current is not None:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _refresh_locale_combo(self) -> None:
        current = self._locale.locale
        self._locale_combo.blockSignals(True)
        self._locale_combo.clear()
        for locale in SUPPORTED_UI_LOCALES:
            self._locale_combo.addItem(LOCALE_LABELS[locale], locale)
        index = self._locale_combo.findData(current)
        self._locale_combo.setCurrentIndex(max(0, index))
        self._locale_combo.blockSignals(False)

    def _refresh_editor_combo(self) -> None:
        current = self._editor_combo.currentData() or self._editor_combo.currentText()
        self._editor_combo.blockSignals(True)
        self._editor_combo.clear()
        for label in (*self._coordinator.known_editor_labels(), "Outro..."):
            self._editor_combo.addItem(self._t(label), label)
        index = self._editor_combo.findData(current)
        if index < 0:
            index = self._editor_combo.findData("Outro...")
        self._editor_combo.setCurrentIndex(max(0, index))
        self._editor_combo.blockSignals(False)

    # --------------------------------------------------------------- helpers
    def _go(self, page: QWidget) -> None:
        ui.fade_to(self._stack, page, animate=self._theme.tokens.animations)
        self._cursor.enter_page(page)

    def _button(self, text: str, handler, variant: str = "default") -> QPushButton:
        button = ui.button(text, handler, variant)
        if variant in ("menu", "start", "primary", "tab", "dojo-primary", "dojo-secondary", "cta"):
            self._cursor.track(button)
        return button

    def _title(self, text: str) -> QLabel:
        label = ui.title_label()
        self._cursor.register_title(label, text)
        return label

    @staticmethod
    def _page(margins: int = 28, spacing: int = 12) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(margins, margins - 4, margins, 18)
        layout.setSpacing(spacing)
        return page, layout

    @staticmethod
    def _centered(widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(widget)
        row.addStretch(1)
        return row

    @staticmethod
    def _terminal_text() -> QTextEdit:
        text = QTextEdit()
        text.setReadOnly(True)
        text.setProperty("role", "terminal")
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        return text

    def _footer(self, back: Callable[[], None] | None, hints: list[tuple[str, str]], back_text: str = "[ VOLTAR ]") -> QVBoxLayout:
        footer = QVBoxLayout()
        footer.setSpacing(10)
        if back is not None:
            row = QHBoxLayout()
            back_button = self._button(back_text, back)
            self._footer_buttons.append((back_button, back_text))
            back_button.setMinimumWidth(180)
            row.addWidget(back_button)
            row.addStretch(1)
            footer.addLayout(row)
        hint_bar = ui.HintBar([(key, self._t(text)) for key, text in hints])
        self._footer_hint_bars.append((hint_bar, hints))
        footer.addWidget(hint_bar)
        return footer

    # ------------------------------------------------------------------ home
    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setProperty("role", "divider")
        divider.setFixedHeight(1)
        return divider

    def _build_home_page(self) -> QWidget:
        """"Dojo Session" home: a minimal, terminal-flavored landing screen
        (header, a centered READY area with the three main actions, LAST
        SESSION, and a bottom bar) -- replaces the old single vertical menu.

        `_menu_buttons` keeps the exact order the old menu used (study,
        train, learn, exam, history, settings) purely so the existing
        numeric keyboard shortcuts in `_install_shortcuts` keep opening the
        same screens as before, even though the buttons are now grouped
        visually into two clusters (3 primary + 3 secondary) instead of one
        vertical list.
        """
        page, layout = self._page(margins=0, spacing=0)

        # Kept alive (not shown) purely so `_change_workspace()` can keep
        # calling `.setText()` on it -- the redesigned Home deliberately
        # drops the workspace-path/packs/runtimes line (see spec).
        self._workspace_label = ui.label(self._workspace_prompt(), role="prompt", wrap=True)

        # ------------------------------------------------------------- header
        header = QWidget()
        header.setProperty("role", "dojo-header")
        header.setFixedHeight(76)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(30, 0, 30, 0)
        header_layout.setSpacing(10)
        self._home_logo = ui.logo_mark()
        self._title_home = self._title("RankedDojo")
        header_layout.addWidget(self._home_logo)
        header_layout.addWidget(self._title_home)
        header_layout.addStretch(1)
        layout.addWidget(header)

        # --------------------------------------------------------- ready area
        ready_area = QWidget()
        ready_layout = QVBoxLayout(ready_area)
        ready_layout.setContentsMargins(30, 0, 30, 0)
        ready_layout.setSpacing(16)
        ready_layout.addStretch(2)

        self._home_ready_pill = ui.pill("", status="ready")
        ready_layout.addLayout(self._centered(self._home_ready_pill))

        self._home_question_label = ui.label("", role="question")
        ready_layout.addLayout(self._centered(self._home_question_label))

        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(16)
        self._home_learn_button = self._button("", self._open_learning_flow, "dojo-primary")
        self._home_train_button = self._button("", self._open_training_setup, "dojo-primary")
        self._home_exam_button = self._button("", self._open_exam_setup, "dojo-primary")
        for button in (self._home_learn_button, self._home_train_button, self._home_exam_button):
            actions_layout.addWidget(button)
        ready_layout.addLayout(self._centered(actions_row))

        # Transient feedback for the background runtime/exercise preflight
        # checks (see `_exercise_preflight_checked`/`_exam_preflight_checked`)
        # -- empty almost always, matching the generous negative space here.
        self._home_status = ui.label("", role="hint")
        self._home_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ready_layout.addLayout(self._centered(self._home_status))

        self._home_pack_empty_state = ui.card(status="pending")
        self._home_pack_empty_state.setMinimumWidth(600)
        self._home_pack_empty_state.setMaximumWidth(720)
        empty_layout = QVBoxLayout(self._home_pack_empty_state)
        empty_layout.setContentsMargins(16, 12, 16, 12)
        empty_layout.setSpacing(0)
        empty_row = QWidget()
        empty_row_layout = QHBoxLayout(empty_row)
        empty_row_layout.setContentsMargins(0, 0, 0, 0)
        empty_row_layout.setSpacing(12)
        self._home_pack_empty_warning_icon = ui.label("⚠", status="pending")
        self._home_pack_empty_warning_icon.setFixedSize(24, 24)
        self._home_pack_empty_warning_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_row_layout.addWidget(
            self._home_pack_empty_warning_icon,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        empty_text = QWidget()
        empty_text_layout = QVBoxLayout(empty_text)
        empty_text_layout.setContentsMargins(0, 0, 0, 0)
        empty_text_layout.setSpacing(1)
        self._home_pack_empty_title = ui.label("", role="question")
        self._home_pack_empty_description = ui.label("", role="muted")
        self._home_pack_empty_description_2 = ui.label("", role="muted")
        for label in (
            self._home_pack_empty_title,
            self._home_pack_empty_description,
            self._home_pack_empty_description_2,
        ):
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            empty_text_layout.addWidget(label)
        empty_row_layout.addWidget(empty_text, 1, Qt.AlignmentFlag.AlignVCenter)

        empty_row_layout.addStretch(1)
        empty_actions_widget = QWidget()
        empty_actions = QVBoxLayout(empty_actions_widget)
        empty_actions.setContentsMargins(0, 0, 0, 0)
        empty_actions.setSpacing(4)
        self._home_pack_empty_actions = empty_actions_widget
        self._home_generate_pack_button = self._button("", self._open_study_flow, "dojo-secondary")
        self._home_import_pack_button = self._button("", self._import_pack, "dojo-secondary")
        for button in (self._home_generate_pack_button, self._home_import_pack_button):
            button.setFixedSize(150, 38)
        empty_actions.addWidget(self._home_generate_pack_button)
        empty_actions.addWidget(self._home_import_pack_button)
        empty_row_layout.addWidget(empty_actions_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        empty_layout.addWidget(empty_row)
        ready_layout.addLayout(self._centered(self._home_pack_empty_state))

        ready_layout.addStretch(3)
        layout.addWidget(ready_area, 1)

        # ----------------------------------------------------------- divider
        divider_row_1 = QHBoxLayout()
        divider_row_1.setContentsMargins(30, 0, 30, 0)
        divider_row_1.addWidget(self._divider())
        layout.addLayout(divider_row_1)

        # ------------------------------------------------------ last session
        last_session_area = QWidget()
        last_session_layout = QHBoxLayout(last_session_area)
        last_session_layout.setContentsMargins(30, 18, 30, 18)
        last_session_layout.setSpacing(16)

        last_session_text = QVBoxLayout()
        last_session_text.setSpacing(4)
        self._last_session_header = ui.label("", role="caption-muted")
        last_session_text.addWidget(self._last_session_header)
        self._last_session_name = ui.label("", role="activity-name")
        last_session_text.addWidget(self._last_session_name)
        self._last_session_meta = ui.label("", role="meta")
        last_session_text.addWidget(self._last_session_meta)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        self._last_session_progress = ui.progress_bar(1, 0)
        self._last_session_progress.setFixedWidth(220)
        progress_row.addWidget(self._last_session_progress)
        self._last_session_progress_label = ui.label("", role="meta")
        progress_row.addWidget(self._last_session_progress_label)
        progress_row.addStretch(1)
        last_session_text.addLayout(progress_row)
        self._last_session_empty_label = ui.label("", role="muted")
        last_session_text.addWidget(self._last_session_empty_label)
        last_session_layout.addLayout(last_session_text, 1)

        self._last_session_continue_button = self._button("", self._continue_last_session, "cta")
        last_session_layout.addWidget(self._last_session_continue_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(last_session_area)

        # ----------------------------------------------------------- divider
        divider_row_2 = QHBoxLayout()
        divider_row_2.setContentsMargins(30, 0, 30, 0)
        divider_row_2.addWidget(self._divider())
        layout.addLayout(divider_row_2)

        # ------------------------------------------------------- bottom bar
        bottom_bar = QWidget()
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(30, 16, 30, 20)
        bottom_layout.setSpacing(10)

        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(10)
        self._menu_buttons: list[QPushButton] = []
        self._home_study_button = self._button("", self._open_study_flow, "dojo-secondary")
        self._home_history_button = self._button("", self._show_history, "dojo-secondary")
        self._home_settings_button = self._button("", lambda: self._show_settings(), "dojo-secondary")
        # Order preserved exactly from the old vertical menu (study, train,
        # learn, exam, history, settings) so the existing numeric shortcuts
        # ("1".."6", see `_install_shortcuts`) keep opening the same screens.
        self._menu_buttons.append(self._home_study_button)
        self._menu_buttons.append(self._home_train_button)
        self._menu_buttons.append(self._home_learn_button)
        self._menu_buttons.append(self._home_exam_button)
        self._menu_buttons.append(self._home_history_button)
        self._menu_buttons.append(self._home_settings_button)

        for key, button in (
            ("N", self._home_study_button),
            ("H", self._home_history_button),
            ("S", self._home_settings_button),
        ):
            item = QWidget()
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(6)
            item_layout.addWidget(ui.keycap(key))
            item_layout.addWidget(button)
            secondary_row.addWidget(item)
        bottom_layout.addLayout(secondary_row)
        bottom_layout.addStretch(1)

        self._home_language_status = QHBoxLayout()
        self._home_language_status.setSpacing(14)
        bottom_layout.addLayout(self._home_language_status)

        layout.addWidget(bottom_bar)
        return page

    def _build_study_page(self) -> QWidget:
        page, layout = self._page(margins=28)
        self._study_title = self._title("QUERO ESTUDAR ALGO NOVO")
        layout.addWidget(self._study_title)
        self._study_description = ui.label(
                "Descreva o que quer estudar, gere um prompt compatível e importe o pack resultante.",
                role="muted",
                wrap=True,
            )
        layout.addWidget(self._study_description)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 10, 0, 10)
        content_layout.setSpacing(14)

        study = ui.card()
        study.setMaximumWidth(900)
        study_layout = QVBoxLayout(study)
        study_layout.setContentsMargins(16, 12, 16, 14)
        study_layout.setSpacing(10)
        self._study_topic = QTextEdit()
        self._study_topic.setPlaceholderText("Ex.: ponteiros e strings, OOP em Python, arrays em Java...")
        self._study_topic.setFixedHeight(72)
        self._study_topic.setAcceptRichText(False)
        self._study_section_label = ui.section_label("> O QUE VOCÊ QUER ESTUDAR?")
        study_layout.addWidget(self._study_section_label)
        study_layout.addWidget(self._study_topic)

        fields = QGridLayout()
        fields.setHorizontalSpacing(10)
        fields.setVerticalSpacing(8)
        self._study_progression_combo = self._combo(("Progressive", "Uniform"), ("progressive", "uniform"))
        self._study_levels_combo = self._combo(
            ("Automatic", "1", "2", "3", "4", "5", "6"),
            ("automatic", "1", "2", "3", "4", "5", "6"),
        )
        self._study_exercises_per_level_combo = self._combo(
            ("Automatic", "2", "3", "4", "5"),
            ("automatic", "2", "3", "4", "5"),
        )
        self._study_goal_combo = self._combo(("Aprender", "Praticar", "Revisar", "Validar conhecimento"))
        self._study_format_combo = self._combo(("Exercícios", "Projeto", "Misto", "Revisão", "Simulado"))
        self._study_language_combo = QComboBox()
        self._study_custom_language = QLineEdit()
        self._study_custom_language.setPlaceholderText("Ex.: Rust, Go, Kotlin...")
        self._study_custom_language.setVisible(False)
        self._study_custom_language.setEnabled(False)
        self._study_language_combo.currentIndexChanged.connect(self._update_custom_language_field)
        language_field = QWidget()
        language_layout = QVBoxLayout(language_field)
        language_layout.setContentsMargins(0, 0, 0, 0)
        language_layout.setSpacing(6)
        language_layout.addWidget(self._study_language_combo)
        language_layout.addWidget(self._study_custom_language)
        self._study_content_language_combo = self._combo(("Português (pt-BR)", "Inglês (en)"))
        self._study_content_language_combo.setItemData(0, "pt-BR")
        self._study_content_language_combo.setItemData(1, "en")
        self._study_size_combo = self._combo(("Curto", "Médio", "Completo"))
        self._study_field_labels: list[tuple[QLabel, str]] = []
        for index, (caption, widget) in enumerate(
            (
                ("Progressão", self._study_progression_combo),
                ("Levels", self._study_levels_combo),
                ("Exercícios por level", self._study_exercises_per_level_combo),
                ("Objetivo", self._study_goal_combo),
                ("Formato", self._study_format_combo),
                ("Linguagem", language_field),
                ("Idioma", self._study_content_language_combo),
                ("Tamanho", self._study_size_combo),
            )
        ):
            row, column = divmod(index, 2)
            label = ui.label(caption, role="muted")
            self._study_field_labels.append((label, caption))
            fields.addWidget(label, row * 2, column)
            fields.addWidget(widget, row * 2 + 1, column)
        study_layout.addLayout(fields)

        study_actions = QHBoxLayout()
        self._generate_prompt_button = self._button("> GERAR PROMPT", self._generate_study_prompt, "primary")
        self._copy_prompt_button = self._button("[ COPIAR PROMPT ]", self._copy_study_prompt)
        self._study_import_button = self._button("[ IMPORTAR PACK ]", self._import_pack)
        study_actions.addWidget(self._generate_prompt_button)
        study_actions.addWidget(self._copy_prompt_button)
        study_actions.addWidget(self._study_import_button)
        study_actions.addStretch(1)
        study_layout.addLayout(study_actions)
        self._study_prompt_output = QTextEdit()
        self._study_prompt_output.setReadOnly(True)
        self._study_prompt_output.setAcceptRichText(False)
        self._study_prompt_output.setPlaceholderText("O prompt gerado aparecerá aqui.")
        self._study_prompt_output.setMinimumHeight(128)
        study_layout.addWidget(self._study_prompt_output)
        self._study_status = ui.label("1. gere o prompt · 2. copie · 3. cole na IA que preferir · 4. importe o pack", role="muted", wrap=True)
        study_layout.addWidget(self._study_status)
        content_layout.addLayout(self._centered(study))
        content_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        layout.addLayout(self._footer(self._show_home, [("Esc", "voltar"), ("Ctrl+C", "copiar prompt")]))
        return page

    @staticmethod
    def _combo(items: tuple[str, ...], values: tuple[str, ...] | None = None) -> QComboBox:
        if values is not None and len(values) != len(items):
            raise ValueError("Combo labels and values must have the same length.")
        combo = QComboBox()
        for index, item in enumerate(items):
            combo.addItem(item, values[index] if values is not None else item)
        return combo

    # --------------------------------------------------------------- training
    def _build_training_page(self) -> QWidget:
        page, layout = self._page()
        self._training_title = self._title("TREINO")
        layout.addWidget(self._training_title)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self._level_training_button = self._button("[1] TREINO POR LEVEL", self._choose_level_training, "tab")
        self._random_training_button = self._button("[2] TREINO ALEATÓRIO", self._choose_random_training, "tab")
        for tab in (self._level_training_button, self._random_training_button):
            tab.setCheckable(True)
            tabs.addWidget(tab)
        tabs.addStretch(1)
        layout.addLayout(tabs)

        self._training_options_panel = QWidget()
        options = QVBoxLayout(self._training_options_panel)
        options.setContentsMargins(6, 14, 6, 0)
        options.setSpacing(8)
        self._training_mode_label = ui.label("", role="headline")
        self._training_pack_combo = QComboBox()
        self._pack_combo = self._training_pack_combo
        self._training_pack_combo.currentIndexChanged.connect(self._refresh_levels)
        self._level_checks_layout = QVBoxLayout()
        self._level_checks_layout.setSpacing(2)

        self._random_options = QWidget()
        random_options = QVBoxLayout(self._random_options)
        random_options.setContentsMargins(0, 8, 0, 0)
        random_options.setSpacing(2)
        self._selection_group = QButtonGroup(self)
        self._selection_group.setExclusive(True)
        self._prioritize_radio = ui.OptionButton("prioritize_uncompleted", kind="radio", checked=True, label="Priorizar não concluídos")
        self._only_uncompleted_radio = ui.OptionButton("only_uncompleted", kind="radio", label="Somente não concluídos")
        self._all_radio = ui.OptionButton("all_exercises", kind="radio", label="Todos os exercícios")
        for radio in (self._prioritize_radio, self._only_uncompleted_radio, self._all_radio):
            self._selection_group.addButton(radio)
        self._allow_repeated_check = ui.OptionButton("allow_repeats", kind="check", label="Permitir repetidos")
        self._random_draw_label = ui.section_label("> SORTEIO")
        random_options.addWidget(self._random_draw_label)
        for widget in (self._prioritize_radio, self._only_uncompleted_radio, self._all_radio):
            random_options.addWidget(widget)
        random_options.addSpacing(6)
        random_options.addWidget(self._allow_repeated_check)

        options.addWidget(self._training_mode_label)
        self._training_pack_label = ui.section_label("> RANK / PACK")
        options.addWidget(self._training_pack_label)
        options.addWidget(self._training_pack_combo)
        options.addSpacing(6)
        self._training_levels_label = ui.section_label("> LEVELS")
        options.addWidget(self._training_levels_label)
        options.addLayout(self._level_checks_layout)
        options.addWidget(self._random_options)
        options.addSpacing(18)
        self._start_training_button = self._button("> START TRAINING", self._start_training, "start")
        options.addLayout(self._centered(self._start_training_button))
        options.addStretch(1)
        self._cursor.set_idle_target(page, self._start_training_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._training_options_panel)
        layout.addWidget(scroll, 1)
        layout.addLayout(
            self._footer(self._show_home, [("1", "por level"), ("2", "aleatório"), ("Esc", "voltar")])
        )
        return page

    # --------------------------------------------------------------- exam mode
    def _build_exam_page(self) -> QWidget:
        page, layout = self._page()
        self._exam_title = self._title("MODO PROVA")
        layout.addWidget(self._exam_title)

        self._exam_resume_card = ui.card(status="pending")
        resume = QVBoxLayout(self._exam_resume_card)
        resume.setContentsMargins(16, 12, 16, 14)
        resume.setSpacing(8)
        self._exam_resume_header = ui.label("● PROVA EM ANDAMENTO", status="pending")
        resume.addWidget(self._exam_resume_header)
        self._exam_resume_label = ui.label("", wrap=True)
        resume.addWidget(self._exam_resume_label)
        resume_buttons = QHBoxLayout()
        self._resume_exam_button = self._button("> CONTINUAR PROVA", self._resume_exam, "primary")
        self._end_exam_button = self._button("[ ENCERRAR PROVA ]", self._end_exam, "danger")
        resume_buttons.addWidget(self._resume_exam_button, 2)
        resume_buttons.addWidget(self._end_exam_button, 1)
        resume.addLayout(resume_buttons)
        layout.addWidget(self._exam_resume_card)

        layout.addSpacing(8)
        self._exam_new_label = ui.section_label("> NOVA PROVA — RANK / PACK")
        layout.addWidget(self._exam_new_label)
        self._exam_pack_combo = QComboBox()
        layout.addWidget(self._exam_pack_combo)
        prepare_row = QHBoxLayout()
        self._prepare_exam_button = self._button("> PREPARAR PROVA", self._show_exam_prepare, "primary")
        self._prepare_exam_button.setMinimumWidth(260)
        prepare_row.addWidget(self._prepare_exam_button)
        prepare_row.addStretch(1)
        layout.addSpacing(4)
        layout.addLayout(prepare_row)
        layout.addStretch(1)
        layout.addLayout(self._footer(self._show_home, [("Enter", "selecionar"), ("Esc", "voltar")]))
        return page

    def _build_exam_prepare_page(self) -> QWidget:
        page, layout = self._page()
        self._exam_prepare_title = self._title("PREPARAR PROVA")
        layout.addWidget(self._exam_prepare_title)
        layout.addWidget(ui.label("exam.conf — less", role="panel-caption"))
        self._exam_prepare_text = self._terminal_text()
        layout.addWidget(self._exam_prepare_text, 1)
        layout.addSpacing(14)
        self._start_exam_button = self._button("> START EXAM", self._start_exam, "start")
        layout.addLayout(self._centered(self._start_exam_button))
        self._cursor.set_idle_target(page, self._start_exam_button)
        layout.addSpacing(10)
        layout.addLayout(self._footer(lambda: self._go(self._exam_page), [("Enter", "iniciar"), ("Esc", "voltar")]))
        return page

    # --------------------------------------------------------------- exercise
    def _build_sidebar_block(self) -> tuple[QWidget, QLabel, QLabel]:
        """A small sidebar section: a section-style header + a mono value
        line. Returned separately so callers can set/translate the header
        and hide the whole container when there's nothing to show (used for
        the best-effort "allowed"/"not allowed" blocks).
        """
        container = QWidget()
        block_layout = QVBoxLayout(container)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(3)
        header = ui.label("", role="section")
        value = ui.label("", role="mono", wrap=True)
        block_layout.addWidget(header)
        block_layout.addWidget(value)
        return container, header, value

    def _build_exercise_page(self) -> QWidget:
        page, layout = self._page(margins=0, spacing=0)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # ------------------------------------------------------------- sidebar
        sidebar = QWidget()
        sidebar.setProperty("role", "exercise-sidebar")
        sidebar.setFixedWidth(272)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 20, 18, 16)
        sidebar_layout.setSpacing(10)

        breadcrumb_row = QHBoxLayout()
        breadcrumb_row.setSpacing(0)
        breadcrumb_column = QVBoxLayout()
        breadcrumb_column.setSpacing(2)
        self._exercise_id_label = ui.label("", role="meta", wrap=True)
        self._exercise_rank_label = ui.label("", role="meta", wrap=True)
        self._exercise_level_label = ui.label("", role="meta", wrap=True)
        for widget in (self._exercise_id_label, self._exercise_rank_label, self._exercise_level_label):
            breadcrumb_column.addWidget(widget)
        breadcrumb_row.addLayout(breadcrumb_column)
        sidebar_layout.addLayout(breadcrumb_row)
        # compat: single label with metadata, used by screen inspection tests
        self._exercise_meta = self._exercise_id_label

        self._exercise_title = self._title("")
        self._exercise_title.setProperty("compact", "true")
        self._exercise_title.setWordWrap(True)
        sidebar_layout.addWidget(self._exercise_title)

        self._exercise_difficulty_label = ui.label("", role="meta")
        self._exercise_difficulty_label.hide()
        sidebar_layout.addWidget(self._exercise_difficulty_label)

        sidebar_layout.addSpacing(6)
        self._sidebar_expected_block, self._sidebar_expected_header, self._sidebar_expected_value = (
            self._build_sidebar_block()
        )
        self._sidebar_allowed_block, self._sidebar_allowed_header, self._sidebar_allowed_value = (
            self._build_sidebar_block()
        )
        self._sidebar_not_allowed_block, self._sidebar_not_allowed_header, self._sidebar_not_allowed_value = (
            self._build_sidebar_block()
        )
        self._sidebar_constraints_block, self._sidebar_constraints_header, self._sidebar_constraints_value = (
            self._build_sidebar_block()
        )
        for block in (
            self._sidebar_expected_block,
            self._sidebar_allowed_block,
            self._sidebar_not_allowed_block,
            self._sidebar_constraints_block,
        ):
            sidebar_layout.addWidget(block)

        sidebar_layout.addStretch(1)

        self._feedback = ui.FeedbackBanner()
        self._feedback.clicked.connect(self._reopen_trace_summary)
        sidebar_layout.addWidget(self._feedback)

        content.addWidget(sidebar)

        # ---------------------------------------------------------- main column
        main_column = QWidget()
        main_layout = QVBoxLayout(main_column)
        main_layout.setContentsMargins(24, 20, 24, 0)
        main_layout.setSpacing(8)

        header_row = QHBoxLayout()
        self._subject_prompt_label = ui.label("", role="panel-caption")
        header_row.addWidget(self._subject_prompt_label, 1)
        self._exam_timer_label = ui.label("", role="timer")
        header_row.addWidget(self._exam_timer_label)
        main_layout.addLayout(header_row)

        self._subject = ui.SubjectMarkdownView()
        self._subject.setMinimumHeight(240)
        main_layout.addWidget(self._subject, 1)

        self._trace_summary_panel = ui.TraceSummaryPanel()
        self._trace_summary_panel.set_open_full_handler(self._open_full_trace)
        main_layout.addWidget(self._trace_summary_panel)
        self._trace_collapse_timer = QTimer(self)
        self._trace_collapse_timer.setSingleShot(True)
        self._trace_collapse_timer.timeout.connect(self._collapse_trace_summary)

        content.addWidget(main_column, 1)
        layout.addLayout(content, 1)

        # ------------------------------------------------------------- footer
        footer_bar = QWidget()
        footer_bar.setProperty("role", "exercise-footer")
        footer_layout = QVBoxLayout(footer_bar)
        footer_layout.setContentsMargins(24, 10, 24, 14)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._open_editor_button = self._button("[ ABRIR IDE ]", self._open_editor)
        self._correct_button = self._button("> CORRIGIR", self._submit_current, "primary")
        self._trace_button = self._button("[ VER TRACE ]", self._toggle_trace_summary)
        self._next_button = self._button("[ PRÓXIMO/TROCAR ]", self._next_exercise)
        self._exercise_back_button = self._button("[ VOLTAR ]", self._back_from_exercise)
        buttons.addWidget(self._open_editor_button, 3)
        buttons.addWidget(self._correct_button, 3)
        buttons.addWidget(self._trace_button, 2)
        buttons.addWidget(self._next_button, 3)
        buttons.addWidget(self._exercise_back_button, 2)
        footer_layout.addLayout(buttons)
        layout.addWidget(footer_bar)

        return page

    # ---------------------------------------------------------------- history
    def _build_history_page(self) -> QWidget:
        page, layout = self._page(spacing=10)
        self._history_title = self._title("HISTÓRICO")
        layout.addWidget(self._history_title)
        self._history_view = "overview"
        self._history_context: tuple[str, str | None] = ("overview", None)
        self._history_activity_rows = []
        self._history_attempt_rows = []
        self._history_selected_attempt = None

        body = QHBoxLayout()
        body.setSpacing(14)

        sidebar = QFrame()
        sidebar.setProperty("role", "card")
        sidebar.setMaximumWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 12, 14, 12)
        sidebar_layout.setSpacing(8)
        sidebar_layout.addWidget(ui.section_label("HISTORY"))
        self._history_views_label = ui.label("VIEWS", role="caption-muted")
        sidebar_layout.addWidget(self._history_views_label)
        self._history_nav_buttons: dict[str, QPushButton] = {}
        for key, text in (("overview", "Visão geral"),):
            button = self._button(self._t(text).upper(), lambda checked=False, view=key: self._set_history_view(view), "option")
            button.setCheckable(True)
            self._history_nav_buttons[key] = button
            sidebar_layout.addWidget(button)
        self._history_learning_label = ui.label("LEARNING", role="caption-muted")
        sidebar_layout.addWidget(self._history_learning_label)
        self._history_learning_buttons_layout = QVBoxLayout()
        self._history_learning_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._history_learning_buttons_layout.setSpacing(6)
        sidebar_layout.addLayout(self._history_learning_buttons_layout)
        self._history_packs_label = ui.label("PACKS", role="caption-muted")
        sidebar_layout.addWidget(self._history_packs_label)
        self._history_pack_buttons_layout = QVBoxLayout()
        self._history_pack_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._history_pack_buttons_layout.setSpacing(6)
        sidebar_layout.addLayout(self._history_pack_buttons_layout)
        self._history_sessions_label = ui.label("SESSIONS", role="caption-muted")
        sidebar_layout.addWidget(self._history_sessions_label)
        self._history_session_buttons_layout = QVBoxLayout()
        self._history_session_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._history_session_buttons_layout.setSpacing(6)
        for key, text in (("training_sessions", "Treino"), ("exam_sessions", "Provas")):
            button = self._button(self._t(text).upper(), lambda checked=False, view=key: self._set_history_view(view), "option")
            button.setCheckable(True)
            self._history_nav_buttons[key] = button
            self._history_session_buttons_layout.addWidget(button)
        sidebar_layout.addLayout(self._history_session_buttons_layout)
        sidebar_layout.addStretch(1)
        body.addWidget(sidebar)

        center = QFrame()
        center.setProperty("role", "card")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 12, 16, 12)
        center_layout.setSpacing(10)
        self._history_center_title = ui.label("", role="activity-name")
        self._history_center_meta = ui.label("", role="muted", wrap=True)
        center_layout.addWidget(self._history_center_title)
        center_layout.addWidget(self._history_center_meta)
        metric_row = QHBoxLayout()
        metric_row.setSpacing(16)
        self._history_metric_completed = ui.label("", status="pass")
        self._history_metric_attempts = ui.label("", role="muted")
        self._history_metric_exams = ui.label("", role="muted")
        self._history_metric_packs = ui.label("", role="muted")
        for widget in (
            self._history_metric_completed,
            self._history_metric_attempts,
            self._history_metric_exams,
            self._history_metric_packs,
        ):
            metric_row.addWidget(widget)
        metric_row.addStretch(1)
        self._history_metric_row = QWidget()
        self._history_metric_row.setLayout(metric_row)
        self._history_metric_row.hide()
        center_layout.addWidget(self._history_metric_row)
        self._history_overview_text = QWidget()
        overview_layout = QVBoxLayout(self._history_overview_text)
        overview_layout.setContentsMargins(0, 8, 0, 0)
        overview_layout.setSpacing(18)
        overview_panels = QHBoxLayout()
        overview_panels.setSpacing(14)

        exam_panel = ui.card()
        exam_layout = QVBoxLayout(exam_panel)
        exam_layout.setContentsMargins(16, 14, 16, 14)
        exam_layout.setSpacing(8)
        self._history_exam_heading = ui.label("", role="caption-muted")
        self._history_exam_total = ui.label("", role="activity-name")
        exam_layout.addWidget(self._history_exam_heading)
        exam_layout.addWidget(self._history_exam_total)
        self._history_exam_meter = QHBoxLayout()
        self._history_exam_meter.setSpacing(3)
        exam_layout.addLayout(self._history_exam_meter)
        self._history_exam_breakdown = ui.label("", role="muted", wrap=True)
        exam_layout.addWidget(self._history_exam_breakdown)
        overview_panels.addWidget(exam_panel, 1)

        training_panel = ui.card()
        training_layout = QVBoxLayout(training_panel)
        training_layout.setContentsMargins(16, 14, 16, 14)
        training_layout.setSpacing(8)
        self._history_training_heading = ui.label("", role="caption-muted")
        self._history_training_total = ui.label("", role="activity-name")
        training_layout.addWidget(self._history_training_heading)
        training_layout.addWidget(self._history_training_total)
        self._history_training_heatmap = QHBoxLayout()
        self._history_training_heatmap.setSpacing(4)
        training_layout.addLayout(self._history_training_heatmap)
        self._history_training_meta = ui.label("", role="muted", wrap=True)
        training_layout.addWidget(self._history_training_meta)
        overview_panels.addWidget(training_panel, 1)

        learning_panel = ui.card()
        learning_layout = QVBoxLayout(learning_panel)
        learning_layout.setContentsMargins(16, 14, 16, 14)
        learning_layout.setSpacing(8)
        self._history_learning_heading = ui.label("", role="caption-muted", wrap=True)
        self._history_learning_percent = ui.label("", role="activity-name")
        self._history_learning_progress = ui.progress_bar(100, 0)
        self._history_learning_meta = ui.label("", role="muted", wrap=True)
        learning_layout.addWidget(self._history_learning_heading)
        learning_layout.addWidget(self._history_learning_percent)
        learning_layout.addWidget(self._history_learning_progress)
        learning_layout.addWidget(self._history_learning_meta)
        overview_panels.addWidget(learning_panel, 1)
        overview_layout.addLayout(overview_panels)

        self._history_recent_heading = ui.label("", role="caption-muted")
        overview_layout.addWidget(self._history_recent_heading)
        self._history_recent_sessions = QVBoxLayout()
        self._history_recent_sessions.setSpacing(8)
        overview_layout.addLayout(self._history_recent_sessions)
        overview_layout.addStretch(1)
        center_layout.addWidget(self._history_overview_text, 1)
        self._history_table = self._table(("ITEM", "VALOR"), stretch=1)
        self._history_activity_table = self._history_table
        self._history_activity_table.itemSelectionChanged.connect(self._history_activity_selected)
        center_layout.addWidget(self._history_activity_table, 1)
        self._history_session_table = self._table(("DATA", "SESSÃO", "PACK", "STATUS"), stretch=2)
        self._history_session_table.itemSelectionChanged.connect(self._history_session_selected)
        center_layout.addWidget(self._history_session_table, 1)
        body.addWidget(center, 1)

        inspector = QFrame()
        self._history_inspector = inspector
        inspector.setProperty("role", "card")
        inspector.setMaximumWidth(340)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(14, 12, 14, 12)
        inspector_layout.setSpacing(10)
        self._history_inspector_title = ui.label("", role="activity-name", wrap=True)
        self._history_inspector_body = ui.label("", role="muted", wrap=True)
        inspector_layout.addWidget(self._history_inspector_title)
        inspector_layout.addWidget(self._history_inspector_body)
        self._history_attempts_table = self._table(("N", "RESULT", "DATA"), stretch=2)
        self._history_attempts_table.itemSelectionChanged.connect(self._history_attempt_selected)
        inspector_layout.addWidget(self._history_attempts_table, 1)
        self._history_trace_summary = ui.label("", role="muted", wrap=True)
        inspector_layout.addWidget(self._history_trace_summary)
        self._history_trace_button = self._button("[ ABRIR TRACE COMPLETO ]", self._open_history_attempt_trace)
        inspector_layout.addWidget(self._history_trace_button)
        body.addWidget(inspector)
        inspector.hide()
        layout.addLayout(body, 1)
        layout.addLayout(self._footer(self._show_home, [("Esc", "voltar")]))
        return page

    @staticmethod
    def _table(headers: tuple[str, ...], stretch: int) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        for column in range(len(headers)):
            mode = QHeaderView.ResizeMode.Stretch if column == stretch else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)
        return table

    # ---------------------------------------------------------------- settings
    def _build_settings_page(self) -> QWidget:
        page, layout = self._page(spacing=10)
        self._settings_title = self._title("CONFIGURAÇÕES")
        layout.addWidget(self._settings_title)

        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(0, 4, 8, 4)
        sections.setSpacing(14)

        # tema
        self._theme_combo = QComboBox()
        for tokens in self._theme.available():
            self._theme_combo.addItem(tokens.name, tokens.key)
        self._theme_combo.setCurrentIndex(max(0, self._theme_combo.findData(self._theme.tokens.key)))
        self._theme_combo.currentIndexChanged.connect(self._change_theme)
        sections.addWidget(self._settings_card("TEMA", [self._theme_combo], []))

        # idioma da interface
        self._locale_label = ui.label("", role="muted")
        self._locale_combo = QComboBox()
        self._locale_combo.currentIndexChanged.connect(self._set_locale_from_combo)
        sections.addWidget(self._settings_card("IDIOMA", [self._locale_label, self._locale_combo], []))

        # workspace
        self._settings_workspace = ui.label("", wrap=True)
        sections.addWidget(
            self._settings_card("WORKSPACE", [self._settings_workspace], [("[ ALTERAR WORKSPACE ]", self._change_workspace)])
        )

        # editor
        self._editor_combo = QComboBox()
        self._editor_combo.currentIndexChanged.connect(self._editor_preset_changed)
        self._settings_editor = QLineEdit()
        sections.addWidget(
            self._settings_card(
                "EDITOR/IDE",
                [self._editor_combo, self._settings_editor],
                [
                    ("[ DETECTAR AUTOMATICAMENTE ]", self._detect_editor_automatically),
                    ("[ SELECIONAR EXECUTÁVEL ]", self._browse_editor),
                    ("[ SALVAR EDITOR ]", self._save_editor_setting),
                ],
            )
        )

        # runtimes
        self._runtime_labels: dict[str, QLabel] = {}
        self._settings_compiler: QLabel | None = None  # compatibilidade dos testes antigos
        for status in self._coordinator.runtime_statuses():
            language = status.language
            label = ui.label("", wrap=True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._runtime_labels[language] = label
            if self._settings_compiler is None:
                self._settings_compiler = label
            sections.addWidget(
                self._settings_card(
                    f"runtime:{language}",
                    [label],
                    [
                        (f"detect-runtime:{language}", lambda checked=False, lang=language: self._redetect_runtime(lang)),
                        (f"select-runtime:{language}", lambda checked=False, lang=language: self._choose_manual_runtime(lang)),
                    ],
                )
            )

        # packs
        self._packs_summary = ui.label("", wrap=True)
        self._pack_capabilities_summary = ui.label("", wrap=True)
        self._managed_pack_combo = QComboBox()
        self._managed_pack_combo.setPlaceholderText(self._t("Nenhum pack gerenciado"))
        sections.addWidget(
            self._settings_card(
                "PACKS",
                [self._packs_summary, self._pack_capabilities_summary, self._managed_pack_combo],
                [
                    ("[ IMPORTAR PACK ]", self._import_pack),
                    ("[ ATUALIZAR PACKS ]", self._refresh_packs),
                    ("[ REMOVER PACK ]", self._remove_managed_pack),
                    ("[ ABRIR DOCUMENTAÇÃO DE PACKS ]", self._show_pack_help),
                ],
            )
        )
        sections.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        layout.addLayout(self._footer(self._show_home, [("Esc", "voltar")]))
        return page

    def _settings_card(self, title: str, body: list[QWidget], actions: list[tuple[str, Callable[[], None]]]) -> QFrame:
        frame = ui.card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
        title_label = ui.section_label(title)
        title_label.setProperty("sourceText", title)
        layout.addWidget(title_label)
        for widget in body:
            layout.addWidget(widget)
        if actions:
            row = QHBoxLayout()
            row.setSpacing(8)
            for text, handler in actions:
                button = self._button(text, handler)
                button.setProperty("sourceText", text)
                row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)
        return frame

    def _refresh_settings_cards(self) -> None:
        for label in self._settings_page.findChildren(QLabel):
            source = label.property("sourceText")
            if isinstance(source, str):
                label.setText(self._settings_title_text(source))
        for button in self._settings_page.findChildren(QPushButton):
            source = button.property("sourceText")
            if isinstance(source, str):
                self._set_button(button, self._settings_action_text(source))

    def _settings_title_text(self, source: str) -> str:
        mapping = {
            "TEMA": "Tema",
            "IDIOMA": "Idioma da interface",
            "WORKSPACE": "Workspace",
            "EDITOR/IDE": "Editor/IDE",
            "PACKS": "Packs",
        }
        if source.startswith("runtime:"):
            return self._runtime_ui_name(source.removeprefix("runtime:")).upper()
        return self._t(mapping.get(source, source)).upper()

    def _settings_action_text(self, source: str) -> str:
        if source.startswith("detect-runtime:"):
            return self._action("Detectar novamente")
        if source.startswith("select-runtime:"):
            language = source.removeprefix("select-runtime:")
            return self._action("Selecionar {runtime}", runtime=self._runtime_ui_name(language))
        normalized = source.strip()
        if normalized.startswith("[ ") and normalized.endswith(" ]"):
            normalized = normalized[2:-2]
        action_map = {
            "ALTERAR WORKSPACE": "Alterar workspace",
            "DETECTAR AUTOMATICAMENTE": "Detectar automaticamente",
            "SELECIONAR EXECUTÁVEL": "Selecionar executável",
            "SALVAR EDITOR": "Salvar editor",
            "DETECTAR NOVAMENTE": "Detectar novamente",
            "IMPORTAR PACK": "Importar Pack",
            "ATUALIZAR PACKS": "Atualizar Packs",
            "REMOVER PACK": "Remover pack",
            "ABRIR DOCUMENTAÇÃO DE PACKS": "Abrir documentação de Packs",
        }
        upper = normalized.upper()
        if upper in action_map:
            return self._action(action_map[upper])
        if upper.startswith("SELECIONAR "):
            runtime = normalized[len("SELECIONAR ") :]
            return self._action("Selecionar {runtime}", runtime=runtime)
        return self._action(normalized)

    def _runtime_ui_name(self, language: str) -> str:
        names = {
            "c": self._t("Compilador C"),
            "cpp": self._t("Compilador C++"),
            "python": "Python",
            "java": "Java",
        }
        if language in names:
            return names[language]
        return self._coordinator.runtime_display_name(language)

    def _build_pack_help_page(self) -> QWidget:
        page, layout = self._page()
        self._pack_help_title = self._title("COMO CRIAR UM PACK")
        layout.addWidget(self._pack_help_title)
        layout.addWidget(ui.label("PACKS.md — less", role="panel-caption"))
        self._pack_help_text = self._terminal_text()
        self._pack_help_text.setPlainText(self._pack_help_content())
        layout.addWidget(self._pack_help_text, 1)
        row = QHBoxLayout()
        self._open_full_docs_button = self._button("[ ABRIR DOCUMENTAÇÃO COMPLETA ]", self._open_full_documentation)
        row.addWidget(self._open_full_docs_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addLayout(
            self._footer(lambda: self._show_settings("Packs"), [("Esc", "voltar")], "[ VOLTAR PARA CONFIGURAÇÕES ]")
        )
        return page

    # ------------------------------------------------------------- teclado
    def _install_shortcuts(self) -> None:
        for index, button in enumerate(self._menu_buttons, start=1):
            QShortcut(QKeySequence(str(index)), self._home_page).activated.connect(button.click)
        QShortcut(QKeySequence("1"), self._training_page).activated.connect(self._choose_level_training)
        QShortcut(QKeySequence("2"), self._training_page).activated.connect(self._choose_random_training)
        back_targets: dict[QWidget, Callable[[], None]] = {
            self._study_page: self._show_home,
            self._training_page: self._show_home,
            self._exam_page: self._show_home,
            self._history_page: self._show_home,
            self._settings_page: self._show_home,
            self._exam_prepare_page: lambda: self._go(self._exam_page),
            self._pack_help_page: lambda: self._show_settings("Packs"),
            self._learning_languages_page: self._show_home,
            self._learning_track_page: lambda: self._go(self._learning_languages_page),
        }
        for page, action in back_targets.items():
            QShortcut(QKeySequence(Qt.Key.Key_Escape), page).activated.connect(action)

    def _workspace_prompt(self) -> str:
        name = self._workspace_path.name or str(self._workspace_path)
        parent = self._workspace_path.parent.name
        compact = f"~/{parent}/{name}" if parent else f"~/{name}"
        if len(compact) > 54:
            compact = f"~/.../{name}"
        return f"dojo@RankedDojo:{compact}$"

    def _set_title_label(self, label: QLabel, text: str) -> None:
        self._cursor.set_title(label, text)

    # -------------------------------------------------------------- navigation
    def _show_home(self) -> None:
        self._show_resume_if_needed()
        self._refresh_home_status()
        self._refresh_home_last_session()
        self._refresh_study_languages()
        self._go(self._home_page)

    def _open_study_flow(self) -> None:
        self._refresh_study_languages()
        self._go(self._study_page)

    def _refresh_home_status(self) -> None:
        """Clears any transient preflight message (see
        `_exercise_preflight_checked`/`_exam_preflight_checked`) and rebuilds
        the language-status row from the same `runtime_statuses()` data the
        old packs/runtimes line used -- no parallel runtime-detection logic.
        """
        self._home_status.setText("")
        while self._home_language_status.count():
            item = self._home_language_status.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for status in self._coordinator.runtime_statuses():
            available = bool(status.tool) or status.available
            mark = "\u2713" if available else "!"
            chip = ui.label(f"{status.display_name} {mark}", status="pass" if available else "pending")
            self._home_language_status.addWidget(chip)
        managed_loader = getattr(self._coordinator, "list_managed_packs", None)
        self._home_pack_empty_state.setVisible(callable(managed_loader) and not managed_loader())

    def _refresh_home_last_session(self) -> None:
        """Populates the LAST SESSION block from `coordinator.last_session_summary()`
        (the most recent *training* attempt) -- or shows the empty state when
        there is none yet. Never fabricates a session.
        """
        summary = self._coordinator.last_session_summary()
        self._last_session_summary = summary
        has_summary = summary is not None
        self._last_session_name.setVisible(has_summary)
        self._last_session_meta.setVisible(has_summary)
        self._last_session_progress.setVisible(has_summary)
        self._last_session_progress_label.setVisible(has_summary)
        self._last_session_continue_button.setVisible(has_summary)
        self._last_session_empty_label.setVisible(not has_summary)
        if summary is None:
            self._last_session_empty_label.setText(self._t("Nenhuma sessão recente").upper())
            return
        self._last_session_name.setText(summary.activity_name)
        self._last_session_meta.setText(f"{summary.language.upper()} · {summary.pack_id} · {summary.mode}")
        total = max(summary.total_count, 1)
        self._last_session_progress.setRange(0, total)
        self._last_session_progress.setValue(min(summary.completed_count, total))
        self._last_session_progress_label.setText(f"{summary.completed_count} / {summary.total_count}")

    def _continue_last_session(self) -> None:
        summary = getattr(self, "_last_session_summary", None)
        if summary is None:
            return
        ref = self._coordinator.exercise_ref_for_activity(summary.pack_id, summary.activity_id)
        if ref is None:
            QMessageBox.warning(self, self._t("Última sessão"), self._t("Atividade não encontrada."))
            return
        self._load_exercise(ref, mode="training")

    def _refresh_study_languages(self) -> None:
        current = self._study_language_combo.currentData()
        self._study_language_combo.blockSignals(True)
        self._study_language_combo.clear()
        options = (
            ("Automático", "automatic"),
            ("C", "c"),
            ("C++", "cpp"),
            ("Java", "java"),
            ("Python", "python"),
            ("Custom", "custom"),
        )
        for label, value in options:
            self._study_language_combo.addItem(self._t(label), value)
        if current is not None:
            index = self._study_language_combo.findData(current)
            if index >= 0:
                self._study_language_combo.setCurrentIndex(index)
        self._study_language_combo.blockSignals(False)
        self._update_custom_language_field()

    def _update_custom_language_field(self, _index: int = -1) -> None:
        custom = self._study_language_combo.currentData() == "custom"
        self._study_custom_language.setEnabled(custom)
        self._study_custom_language.setVisible(custom)

    def _study_intent(self) -> StudyIntent:
        language = str(self._study_language_combo.currentData() or "automatic")
        if language == "custom":
            language = self._study_custom_language.text().strip() or "custom"
        return StudyIntent(
            topic=self._study_topic.toPlainText(),
            goal=str(self._study_goal_combo.currentData() or self._study_goal_combo.currentText()),
            format=str(self._study_format_combo.currentData() or self._study_format_combo.currentText()),
            programming_language=language,
            content_language=str(self._study_content_language_combo.currentData() or "pt-BR"),
            size=str(self._study_size_combo.currentData() or self._study_size_combo.currentText()),
            progression=str(self._study_progression_combo.currentData() or "progressive"),
            levels=str(self._study_levels_combo.currentData() or "automatic"),
            exercises_per_level=str(self._study_exercises_per_level_combo.currentData() or "automatic"),
        )

    def _generate_study_prompt(self) -> None:
        try:
            contract = pack_contract_text()
        except OSError:
            contract = self._t("Documentação do contrato não encontrada nesta instalação.")
        prompt = self._coordinator.build_pack_prompt(self._study_intent(), contract)
        self._study_prompt_output.setPlainText(prompt)
        self._study_status.setText(self._t("Prompt gerado. Copie, use no gerador/IA que preferir e importe o pack."))

    def _copy_study_prompt(self) -> None:
        prompt = self._study_prompt_output.toPlainText()
        if not prompt.strip():
            self._generate_study_prompt()
            prompt = self._study_prompt_output.toPlainText()
        QApplication.clipboard().setText(prompt)
        self._study_status.setText(self._t("Prompt copiado para a área de transferência."))

    def _open_training_setup(self) -> None:
        if not self._handle_preflight(self._coordinator.preflight_training(), self._open_training_setup):
            return
        if not (self._level_training_button.isChecked() or self._random_training_button.isChecked()):
            self._choose_level_training()
        self._go(self._training_page)

    def _open_exam_setup(self) -> None:
        if not self._exam_preflight_checked(self._open_exam_setup):
            return
        self._show_resume_if_needed()
        self._go(self._exam_page)

    # -------------------------------------------------------- aprendizado
    def _build_learning_languages_page(self) -> QWidget:
        page, layout = self._page()
        self._learning_languages_title = self._title("TRILHA DE APRENDIZADO")
        layout.addWidget(self._learning_languages_title)
        self._learning_languages_subtitle = ui.label(
            self._t("Escolha uma linguagem para ver a trilha."), role="prompt", wrap=True
        )
        layout.addWidget(self._learning_languages_subtitle)
        layout.addSpacing(6)

        self._learning_languages_list = QWidget()
        self._learning_languages_layout = QVBoxLayout(self._learning_languages_list)
        self._learning_languages_layout.setContentsMargins(0, 0, 0, 0)
        self._learning_languages_layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._learning_languages_list)
        layout.addWidget(scroll, 1)

        layout.addLayout(self._footer(self._show_home, [("Esc", "voltar")]))
        return page

    def _open_learning_flow(self) -> None:
        self._refresh_learning_languages()
        self._go(self._learning_languages_page)

    def _refresh_learning_languages(self) -> None:
        while self._learning_languages_layout.count():
            item = self._learning_languages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        try:
            languages = self._coordinator.learning_languages()
        except Exception:  # noqa: BLE001 - a content/provider error never breaks the UI
            languages = ()
            self._learning_languages_layout.addWidget(
                ui.label(
                    self._t("Não foi possível carregar as linguagens disponíveis."),
                    status="fail",
                    wrap=True,
                )
            )
        if not languages:
            self._learning_languages_layout.addWidget(
                ui.label(
                    self._t("Nenhuma trilha de aprendizado disponível no momento."),
                    role="muted",
                    wrap=True,
                )
            )
        for language in languages:
            button = self._button(
                f"> {self._learning_language_display_name(language)}",
                lambda checked=False, lang=language: self._show_learning_track(lang),
                "menu",
            )
            self._learning_languages_layout.addWidget(button)
        self._learning_languages_layout.addStretch(1)

    def _learning_language_display_name(self, language: str) -> str:
        names = {"c": "C", "cpp": "C++", "python": "Python", "java": "Java"}
        return names.get(language, self._coordinator.runtime_display_name(language))

    def _build_learning_track_page(self) -> QWidget:
        page, layout = self._page(spacing=8)
        self._learning_track_title = self._title("TRILHA DE APRENDIZADO")
        layout.addWidget(self._learning_track_title)
        self._learning_track_progress = ui.label("")
        layout.addWidget(self._learning_track_progress)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._learning_continue_button = self._button(
            self._action("Continuar", "primary"), self._continue_learning, "primary"
        )
        action_row.addWidget(self._learning_continue_button)
        self._learning_open_button = self._button(
            self._action("Abrir atividade"), self._open_selected_learning_activity
        )
        action_row.addWidget(self._learning_open_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self._learning_track_table = self._table(
            ("NÍVEL", "TÍTULO", "TÓPICOS", "DIFICULDADE", "ESTADO"), stretch=1
        )
        self._learning_track_table.cellDoubleClicked.connect(
            lambda *_: self._open_selected_learning_activity()
        )
        self._learning_track_table.itemSelectionChanged.connect(self._sync_learning_open_button)
        layout.addWidget(self._learning_track_table, 1)

        layout.addLayout(
            self._footer(lambda: self._go(self._learning_languages_page), [("Esc", "voltar")])
        )
        return page

    def _show_learning_track(self, language: str) -> None:
        self._learning_language = language
        self._render_learning_track()
        self._go(self._learning_track_page)

    def _render_learning_track(self) -> None:
        language = self._learning_language
        if language is None:
            return
        self._set_title_label(
            self._learning_track_title,
            f"{self._t('Trilha de aprendizado').upper()} — {self._learning_language_display_name(language)}",
        )
        try:
            view = self._coordinator.learning_track(language)
        except Exception:  # noqa: BLE001 - a content/provider error never breaks the UI
            self._learning_track_view = None
            self._learning_track_rows = []
            self._learning_track_table.setRowCount(0)
            self._learning_track_progress.setText(
                self._t("Não foi possível carregar a trilha de aprendizado.")
            )
            ui.set_status(self._learning_track_progress, "fail")
            self._learning_continue_button.setEnabled(False)
            self._learning_open_button.setEnabled(False)
            return
        self._learning_track_view = view
        self._learning_track_rows = list(view.track.activities)
        ui.set_status(self._learning_track_progress, "")
        completed = len(view.completed_activity_ids)
        total = len(view.track.activities)
        if total == 0:
            self._learning_track_progress.setText(self._t("Ainda não há atividades nesta trilha."))
        else:
            bar_width = 20
            filled = round((completed / total) * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            self._learning_track_progress.setText(
                f"[{bar}] {self._t('{completed}/{total} concluídos', completed=completed, total=total)}"
            )
        self._learning_continue_button.setEnabled(view.next_activity is not None)

        self._learning_track_table.setRowCount(len(self._learning_track_rows))
        for row_index, activity in enumerate(self._learning_track_rows):
            completed_activity = activity.activity_id in view.completed_activity_ids
            unlocked = completed_activity or is_activity_unlocked(activity, view.completed_activity_ids)
            if completed_activity:
                marker, status_color = "[✓]", "success"
                state_text = self._t(ACTIVITY_PROGRESS_LABELS[ActivityProgress.COMPLETED])
            elif unlocked:
                marker, status_color = "[ ]", None
                state_text = self._t("Disponível")
            else:
                marker, status_color = "[🔒]", "text_secondary"
                state_text = self._t("Bloqueado")
            cells = (
                self._item(str(activity.position), None, align_right=True),
                self._item(activity.title),
                self._item(", ".join(activity.topics)),
                self._item(activity.difficulty or "—"),
                self._item(f"{marker} {state_text}", status_color),
            )
            for column, item in enumerate(cells):
                self._learning_track_table.setItem(row_index, column, item)
        self._sync_learning_open_button()

    def _sync_learning_open_button(self) -> None:
        self._learning_open_button.setEnabled(self._selected_learning_activity() is not None)

    def _selected_learning_activity(self) -> LearningActivityRef | None:
        row = self._learning_track_table.currentRow()
        if row < 0 or row >= len(self._learning_track_rows):
            return None
        return self._learning_track_rows[row]

    def _open_selected_learning_activity(self) -> None:
        activity = self._selected_learning_activity()
        if activity is not None:
            self._open_learning_activity(activity)

    def _continue_learning(self) -> None:
        if self._learning_language is None:
            return
        try:
            activity = self._coordinator.next_learning_activity(self._learning_language)
        except Exception:  # noqa: BLE001 - a content/provider error never breaks the UI
            QMessageBox.warning(
                self,
                self._t("Trilha de aprendizado"),
                self._t("Não foi possível carregar a trilha de aprendizado."),
            )
            return
        if activity is None:
            QMessageBox.information(
                self, self._t("Trilha de aprendizado"), self._t("Trilha concluída!")
            )
            return
        self._open_learning_activity(activity)

    def _open_learning_activity(self, activity: LearningActivityRef) -> None:
        view = self._learning_track_view
        if view is not None:
            completed = activity.activity_id in view.completed_activity_ids
            if not completed and not is_activity_unlocked(activity, view.completed_activity_ids):
                QMessageBox.information(
                    self,
                    self._t("Trilha de aprendizado"),
                    self._t("Esta atividade está bloqueada por pré-requisitos."),
                )
                return
        try:
            ref = self._coordinator.exercise_ref_for_activity(activity.pack_id, activity.activity_id)
        except Exception:  # noqa: BLE001 - a content/provider error never breaks the UI
            ref = None
        if ref is None:
            QMessageBox.warning(
                self, self._t("Trilha de aprendizado"), self._t("Atividade não encontrada.")
            )
            return
        self._load_exercise(ref, mode="training")

    # ------------------------------------------------ tarefas em segundo plano
    def _run_task(
        self,
        key: str,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        title: str,
        on_finally: Callable[[], None] | None = None,
    ) -> bool:
        """Roda `work` fora da thread da UI. Erros viram mensagem, nunca traceback."""

        def done(result: object) -> None:
            if on_finally is not None:
                on_finally()
            on_done(result)

        def failed(error: BaseException) -> None:
            if on_finally is not None:
                on_finally()
            QMessageBox.warning(self, title, str(error) or error.__class__.__name__)

        return self._tasks.start(key, work, done, failed)

    def _runtime_checked(self, language: str, then: Callable[[], None]) -> bool:
        """True if the language runtime has already been validated.

        Otherwise validates in the background and calls `then`. Detection runs
        external compiler/interpreter probe processes and may take time. On
        failure, preflight shows the message and opens Settings.
        """
        if self._coordinator.runtime_ready(language):
            return True
        if self._tasks.is_busy("runtime"):
            return False
        self._home_status.setText(self._t("verificando ambiente de execução..."))
        self._run_task(
            "runtime",
            lambda: self._coordinator.preflight_runtime(language),
            lambda preflight: then() if preflight.ok else self._handle_preflight(preflight, then),
            self._t("Ambiente de execução"),
            on_finally=self._refresh_home_status,
        )
        return False

    def _exercise_preflight_checked(self, active: ActiveExercise, then: Callable[[], None]) -> bool:
        if self._coordinator.runtime_ready(active.ref.definition.language):
            preflight = self._coordinator.preflight_exercise(active.ref)
            return self._handle_preflight(preflight, then)
        if self._tasks.is_busy("preflight"):
            return False
        self._home_status.setText(self._t("verificando exercício..."))
        self._run_task(
            "preflight",
            lambda: self._coordinator.preflight_exercise(active.ref),
            lambda preflight: self._finish_exercise_preflight(preflight, active, then),
            self._t("Exercício"),
            on_finally=self._refresh_home_status,
        )
        return False

    def _exam_preflight_checked(self, then: Callable[[], None]) -> bool:
        pack_id = self._selected_exam_pack_id()
        if getattr(self, "_exam_preflight_ready_pack", None) == pack_id:
            self._exam_preflight_ready_pack = None
            return True
        if self._coordinator.pack_runtimes_ready(pack_id):
            preflight = self._coordinator.preflight_exam(pack_id)
            return self._handle_preflight(preflight, then)
        if self._tasks.is_busy("runtime"):
            return False
        self._home_status.setText(self._t("verificando ambientes de execução..."))
        self._run_task(
            "runtime",
            lambda: self._coordinator.preflight_exam(pack_id),
            lambda preflight: self._finish_exam_preflight(preflight, then, pack_id),
            self._t("Ambiente de execução"),
            on_finally=self._refresh_home_status,
        )
        return False

    def _finish_exam_preflight(
        self,
        preflight: PreflightResult,
        then: Callable[[], None],
        pack_id: str | None,
    ) -> None:
        if preflight.ok:
            self._exam_preflight_ready_pack = pack_id
            then()
            return
        self._handle_preflight(preflight, then)

    def _handle_preflight(self, preflight: PreflightResult, resume: Callable[[], None]) -> bool:
        if preflight.ok:
            return True
        if preflight.status is ActivityPreflightStatus.CONTENT_INVALID:
            QMessageBox.warning(self, self._t("Conteúdo do pack inválido"), self._preflight_user_message(preflight))
            return False
        self._pending_action = resume
        QMessageBox.information(self, self._t("Configuração necessária"), self._preflight_user_message(preflight))
        self._show_settings(preflight.missing)
        return False

    def _preflight_user_message(self, preflight: PreflightResult) -> str:
        if preflight.missing == "editor":
            return self._t("Configure um editor/IDE válido antes de abrir a pasta do exercício.")
        if preflight.missing == "Runtimes":
            return self._t("Instale ou configure um runtime compatível para corrigir este exercício.")
        return preflight.message

    def _resume_pending_if_ready(self) -> None:
        if self._pending_action is None:
            return
        action = self._pending_action
        self._pending_action = None
        action()

    def _choose_level_training(self) -> None:
        self._training_kind = "level"
        self._training_mode_label.setText(self._t("Treino por Level — escolha os levels e comece."))
        self._random_options.setVisible(False)
        self._training_options_panel.setVisible(True)
        self._level_training_button.setChecked(True)
        self._random_training_button.setChecked(False)

    def _choose_random_training(self) -> None:
        self._training_kind = "random"
        self._training_mode_label.setText(self._t("Treino Aleatório — sorteia exercícios dos levels marcados."))
        self._random_options.setVisible(True)
        self._training_options_panel.setVisible(True)
        self._level_training_button.setChecked(False)
        self._random_training_button.setChecked(True)

    def _refresh_packs(self) -> None:
        packs = self._coordinator.list_packs()
        for combo in (self._training_pack_combo, self._exam_pack_combo):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for pack in packs:
                combo.addItem(f"{pack.name} ({pack.id})", pack.id)
            if current is not None:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self._packs_summary.setText(
            "\n".join(
                f"● {pack.name}   id={pack.id}   v{pack.version}   levels={len(pack.levels)}"
                for pack in packs
            )
            or f"○ {self._t('nenhum pack instalado')}"
        )
        self._pack_capabilities_summary.setText(self._pack_capabilities_text())
        managed_packs = self._coordinator.list_managed_packs()
        current_managed = self._managed_pack_combo.currentData()
        self._managed_pack_combo.blockSignals(True)
        self._managed_pack_combo.clear()
        for pack in managed_packs:
            self._managed_pack_combo.addItem(f"{pack.name} ({pack.id})", pack.id)
        if current_managed is not None:
            index = self._managed_pack_combo.findData(current_managed)
            if index >= 0:
                self._managed_pack_combo.setCurrentIndex(index)
        self._managed_pack_combo.setPlaceholderText(self._t("Nenhum pack gerenciado"))
        self._managed_pack_combo.blockSignals(False)
        for button in self._settings_page.findChildren(QPushButton):
            if button.property("sourceText") == "[ REMOVER PACK ]":
                button.setEnabled(bool(managed_packs))
        self._refresh_levels()
        self._refresh_study_languages()
        self._refresh_home_status()

    def _pack_capabilities_text(self) -> str:
        capabilities = self._coordinator.exercise_capabilities()
        statuses = self._coordinator.runtime_statuses()
        runtimes = ", ".join(f"{status.display_name} ({status.language})" for status in statuses) or self._t("nenhum")
        executions = ", ".join(sorted(capabilities.executions.supported)) or self._t("nenhuma")
        generators = ", ".join(sorted(capabilities.generators.supported)) or self._t("nenhum")
        expectations = ", ".join(sorted(capabilities.expectations.supported)) or self._t("nenhuma")
        return (
            f"{self._t('Contrato')}: schema_version 3\n"
            f"{self._t('Runtimes')}: {runtimes}\n"
            f"{self._t('Strategies')}: {executions}\n"
            f"{self._t('Generators')}: {generators}\n"
            f"{self._t('Validators/expectations')}: {expectations}"
        )

    def _refresh_levels(self) -> None:
        while self._level_checks_layout.count():
            item = self._level_checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._level_checks = []
        pack_id = self._selected_training_pack_id()
        if pack_id is None:
            return
        for level in self._coordinator.list_levels(pack_id):
            check = ui.OptionButton(level, kind="check", checked=True)
            self._cursor.track(check)
            self._level_checks.append(check)
            self._level_checks_layout.addWidget(check)

    def _selected_training_pack_id(self) -> str | None:
        value = self._training_pack_combo.currentData()
        return None if value is None else str(value)

    def _selected_exam_pack_id(self) -> str | None:
        value = self._exam_pack_combo.currentData()
        return None if value is None else str(value)

    def _selected_levels(self) -> tuple[str, ...]:
        return tuple(check.value for check in self._level_checks if check.isChecked())

    def _selection_mode(self) -> str:
        if self._only_uncompleted_radio.isChecked():
            return "only_uncompleted"
        if self._all_radio.isChecked():
            return "all"
        return "prioritize_uncompleted"

    def _make_training_options(self) -> TrainingOptions:
        pack_id = self._selected_training_pack_id()
        if pack_id is None:
            raise ValueError(self._t("Nenhum pack selecionado."))
        levels = self._selected_levels()
        if not levels:
            raise ValueError(self._t("Selecione pelo menos um level."))
        return TrainingOptions(
            pack_id=pack_id,
            level_ids=levels,
            selection_mode="prioritize_uncompleted" if self._training_kind == "level" else self._selection_mode(),
            allow_repeated=False if self._training_kind == "level" else self._allow_repeated_check.isChecked(),
        )

    def _start_training(self) -> None:
        if not self._handle_preflight(self._coordinator.preflight_training(), self._start_training):
            return
        try:
            self._training_options = self._make_training_options()
            self._load_exercise(self._coordinator.choose_training_exercise(self._training_options), mode="training")
        except Exception as error:
            QMessageBox.warning(self, self._t("Treino"), str(error))

    # --------------------------------------------------------------- exercise
    def _load_exercise(self, ref: ExerciseRef, mode: str, overwrite: bool = False) -> None:
        active = (
            self._coordinator.prepare_exam_exercise(ref, self._exam_state, overwrite=overwrite)
            if mode == "exam"
            else self._coordinator.prepare_exercise(ref, overwrite=overwrite)
        )
        if active.had_existing_submission and not overwrite:
            answer = QMessageBox.question(
                self,
                self._t("Implementação existente"),
                self._t("Já existe implementação na workspace. Continuar implementação?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                active = (
                    self._coordinator.prepare_exam_exercise(ref, self._exam_state, overwrite=True)
                    if mode == "exam"
                    else self._coordinator.prepare_exercise(ref, overwrite=True)
                )
        self._mode = mode
        self._active = active
        self._last_outcome = None
        self._trace_summary = None
        self._trace_collapse_timer.stop()
        self._trace_summary_panel.clear()
        self._refresh_exercise_frame()
        self._exam_timer_label.setVisible(mode == "exam")
        if mode == "exam" and self._exam_state is not None:
            self._render_exam_timer(self._exam_state)
        self._subject_prompt_label.setText(
            f"rankeddojo@dojo:~/{active.exercise_workspace_path.name}$ less subject.md"
        )
        self._feedback.clear()
        self._open_editor_button.setToolTip(
            self._t("Abrir a pasta do exercício no {editor}", editor=self._coordinator.editor_display_name())
        )
        self._trace_button.setEnabled(False)
        self._next_button.setVisible(mode == "training")
        self._next_button.setEnabled(mode == "training")
        self._go(self._exercise_page)
        self._sync_editor_target(active)

    def _open_editor(self) -> None:
        if self._active is None:
            return
        if not self._handle_preflight(self._coordinator.preflight_editor(), self._open_editor):
            return
        try:
            context = self._editor_context(self._active)
            previous = self._editor_targets.get(context)
            reuse_window = previous is not None and previous != self._active.exercise_workspace_path
            self._coordinator.open_in_editor(self._active, reuse_window=reuse_window)
            self._editor_targets[context] = self._active.exercise_workspace_path
        except Exception as error:
            QMessageBox.warning(self, "Editor", str(error))

    def _sync_editor_target(self, active: ActiveExercise) -> None:
        context = self._editor_context(active)
        previous = self._editor_targets.get(context)
        if previous is None or previous == active.exercise_workspace_path:
            return
        try:
            self._coordinator.open_in_editor(active, reuse_window=True)
            self._editor_targets[context] = active.exercise_workspace_path
        except Exception as error:
            QMessageBox.warning(self, "Editor", str(error))

    def _editor_context(self, active: ActiveExercise) -> tuple[str, str]:
        if self._mode == "exam" and self._exam_state is not None:
            return ("exam", self._exam_state.id)
        return ("training", active.ref.pack.id)

    def _refresh_exercise_frame(self) -> None:
        if self._active is None:
            return
        active = self._active
        self._set_title_label(self._exercise_title, active.ref.definition.name)
        self._exercise_id_label.setText(f"ID: {active.ref.definition.id}")
        self._exercise_rank_label.setText(f"{self._t('Pack').upper()}: {active.ref.pack.name}")
        self._exercise_level_label.setText(f"LEVEL: {active.ref.level_id}")
        self._open_editor_button.setToolTip(
            self._t("Abrir a pasta do exercício no {editor}", editor=self._coordinator.editor_display_name())
        )

        difficulty = active.ref.definition.difficulty
        if difficulty:
            self._exercise_difficulty_label.setText(f"{self._t('Dificuldade').upper()}: {difficulty}")
            self._exercise_difficulty_label.show()
        else:
            self._exercise_difficulty_label.setText("")
            self._exercise_difficulty_label.hide()

        self._sidebar_expected_value.setText(f"> {active.ref.definition.submission.filename}")

        usage = active.ref.definition.usage
        allowed_lines = self._usage_category_lines(usage.allowed)
        if not allowed_lines:
            # Legacy compatibility fallback: packs with no structured
            # `usage.allowed` (pre-contract content) may still spell it out
            # as a subject.md heading -- see `subject_sections.py`.
            allowed_lines = [f"> {item}" for item in extract_list_section(active.subject_text, ALLOWED_HEADINGS)]
        self._set_sidebar_block(self._sidebar_allowed_block, self._sidebar_allowed_value, allowed_lines)

        not_allowed_lines = self._usage_category_lines(usage.forbidden)
        if not not_allowed_lines:
            # Same legacy compatibility fallback as above, for the
            # "forbidden" side.
            not_allowed_lines = [
                f"> {item}" for item in extract_list_section(active.subject_text, NOT_ALLOWED_HEADINGS)
            ]
        self._set_sidebar_block(self._sidebar_not_allowed_block, self._sidebar_not_allowed_value, not_allowed_lines)

        # `constraints` has no structured sub-categories (unlike
        # allowed/forbidden) -- it is already a flat list of short technical
        # rules, so it never needs the grouped-by-category rendering.
        # `style`/`behavior`/`notes` are deliberately NOT surfaced here: per
        # the subject content contract, those read as pedagogical
        # explanation (why/how to approach the problem) rather than a short
        # fact to look up, so they stay in the subject body only and are
        # never stripped from it.
        constraint_lines = [f"> {item}" for item in usage.constraints]
        if not constraint_lines:
            constraint_lines = [
                f"> {item}" for item in extract_list_section(active.subject_text, CONSTRAINTS_HEADINGS)
            ]
        self._set_sidebar_block(self._sidebar_constraints_block, self._sidebar_constraints_value, constraint_lines)

        self._subject.set_subject_markdown(
            self._exercise_body_markdown(
                active.subject_text,
                allowed_shown=bool(allowed_lines),
                not_allowed_shown=bool(not_allowed_lines),
                constraints_shown=bool(constraint_lines),
            )
        )

    @staticmethod
    def _set_sidebar_block(block: QWidget, value_label: QLabel, lines: list[str]) -> None:
        if lines:
            value_label.setText("\n".join(lines))
            block.show()
        else:
            value_label.setText("")
            block.hide()

    _USAGE_CATEGORY_FIELDS: tuple[tuple[str, str], ...] = (
        ("functions", "Funções"),
        ("libraries", "Bibliotecas"),
        ("imports", "Imports"),
        ("headers", "Headers"),
        ("apis", "APIs"),
        ("flags", "Flags"),
    )

    def _usage_category_lines(self, category) -> list[str]:
        """Sidebar display lines for one structured usage category (an
        `ExerciseDefinition.usage.allowed`/`.forbidden`, reached via the
        application layer). Grouped by sub-category (functions, libraries,
        imports, headers, apis, flags) only when more than one is populated
        -- the common single-category case (almost always just `functions`
        today) stays a flat, unlabeled list so the sidebar doesn't grow for
        no reason. Category labels go through i18n; the technical values
        inside them (`write`, `printf`, ...) never do.
        """
        groups = [
            (self._t(label), getattr(category, field))
            for field, label in self._USAGE_CATEGORY_FIELDS
            if getattr(category, field)
        ]
        if not groups:
            return []
        if len(groups) == 1:
            return [f"> {item}" for item in groups[0][1]]
        lines: list[str] = []
        for label, items in groups:
            lines.append(label)
            lines.extend(f"  > {item}" for item in items)
        return lines

    @staticmethod
    def _exercise_body_markdown(
        subject_text: str, *, allowed_shown: bool, not_allowed_shown: bool, constraints_shown: bool
    ) -> str:
        """Subject Markdown with sections already promoted to the sidebar
        removed, so the main panel never repeats what the sidebar already
        shows -- the general rule, not a per-heading special case. Each
        `(heading_variants, shown)` pair below is the one place this
        decision is made; a future promoted category is one more entry
        here, not a new branch. A section is stripped only when the caller
        has already confirmed the same information IS being shown in the
        sidebar (structured metadata or the legacy fallback, either way) --
        never speculatively, so content with no sidebar representation is
        never silently dropped. `style`/`behavior`/`notes` and the exercise's
        pedagogical content (objective, expected behavior, explanations,
        examples) are never in this table and always stay in the body.
        """
        promoted_sections: tuple[tuple[tuple[str, ...], bool], ...] = (
            (EXPECTED_FILE_HEADINGS, True),  # submission.filename is mandatory, always shown
            (ALLOWED_HEADINGS, allowed_shown),
            (NOT_ALLOWED_HEADINGS, not_allowed_shown),
            (CONSTRAINTS_HEADINGS, constraints_shown),
        )
        body = subject_text
        for heading_variants, shown in promoted_sections:
            if shown:
                body = strip_sections(body, heading_variants)
        return demote_examples_heading(body)

    def _submit_current(self) -> None:
        if self._active is None or self._tasks.is_busy("submit"):
            return
        active = self._active
        if getattr(self, "_submit_preflight_ready_for", None) == id(active):
            self._submit_preflight_ready_for = None
        elif not self._exercise_preflight_checked(active, self._submit_current):
            return
        if self._mode == "exam":
            if self._exam_state is None:
                return
            self._tick_exam()
            if self._exam_state is None:  # o tempo acabou antes de enviar
                return
            state = self._exam_state
            work = lambda: self._coordinator.submit_exam(state, active)  # noqa: E731
            on_done = lambda result: self._on_exam_graded(active, result)  # noqa: E731
        else:
            work = lambda: self._coordinator.submit_training(active)  # noqa: E731
            on_done = lambda outcome: self._on_training_graded(active, outcome)  # noqa: E731
        self._set_grading(True)
        self._run_task("submit", work, on_done, self._t("Correção"), on_finally=lambda: self._set_grading(False))

    def _finish_exercise_preflight(
        self,
        preflight: PreflightResult,
        active: ActiveExercise,
        then: Callable[[], None],
    ) -> None:
        if preflight.ok:
            self._submit_preflight_ready_for = id(active)
            then()
            return
        self._handle_preflight(preflight, then)

    def _set_grading(self, busy: bool) -> None:
        self._correct_button.setEnabled(not busy)
        self._cursor.set_button_text(
            self._correct_button,
            self._t("Corrigindo...").upper() if busy else self._action("Corrigir", "primary"),
        )
        self._next_button.setEnabled(not busy and self._mode == "training")

    def _on_training_graded(self, active: ActiveExercise, outcome: CorrectionOutcome) -> None:
        if self._active is not active:
            return  # the user left the exercise; the attempt was already saved
        self._last_outcome = outcome
        self._trace_summary = self._coordinator.trace_summary(outcome)
        self._trace_button.setEnabled(self._trace_summary is not None)
        if outcome.result.outcome is GradingOutcome.CONTENT_ERROR:
            self._show_content_error_feedback()
        elif outcome.result.passed:
            self._show_training_pass_feedback()
        else:
            self._show_training_fail_feedback()

    def _on_exam_graded(self, active: ActiveExercise, result: tuple[CorrectionOutcome, ExamState | None]) -> None:
        outcome, next_state = result
        self._last_outcome = outcome
        self._trace_summary = self._coordinator.trace_summary(outcome)
        self._trace_button.setEnabled(self._trace_summary is not None)
        if next_state is None:
            self._timer.stop()
            self._clear_exam_editor_context()
            self._exam_state = None
            self._show_pass_feedback(self._t("Prova concluída — nota 100%."))
            self._show_resume_if_needed()
            return
        self._exam_state = next_state
        # If the deadline expired while grading, finish now with the already updated score.
        self._tick_exam()
        if self._exam_state is None:
            return
        if outcome.result.outcome is GradingOutcome.CONTENT_ERROR:
            # Never shown as FAIL: this isn't the user's submission being
            # wrong, it's the exercise's own content/reference. The exam
            # stays on the same exercise (see `submit_exam`); no attempt or
            # level result was recorded.
            if self._active is active:
                self._show_content_error_feedback()
            return
        if outcome.result.passed:
            try:
                self._load_exercise(self._coordinator.exam_ref(next_state), mode="exam")
            except Exception as error:
                QMessageBox.warning(self, self._t("Modo prova"), str(error))
                return
            self._show_pass_feedback(self._t("Exercício anterior concluído. Próximo exercício carregado."))
            return
        if self._active is active:
            self._show_fail_feedback(self._t("Você continua neste exercício. Corrija e envie de novo."))

    def _show_training_fail_feedback(self) -> None:
        self._show_fail_feedback(self._t("Veja o trace técnico, ajuste no editor e corrija de novo."))

    def _show_training_pass_feedback(self) -> None:
        self._show_pass_feedback(self._t("Exercício concluído.").upper(), with_next=True)

    def _show_fail_feedback(self, message: str) -> None:
        has_summary = self._trace_summary is not None
        if has_summary:
            message = f"{message} ({self._t('Clique para ver detalhes')})"
        self._feedback.show_result("fail", "[✗] FAIL", message, [], animate=self._theme.tokens.animations)
        self._feedback.set_clickable(has_summary)
        self._present_trace_summary()

    def _show_content_error_feedback(self) -> None:
        """Content/pack error, distinct from a normal user FAIL (never uses the 'fail' status)."""
        has_summary = self._trace_summary is not None
        message = self._t("Este exercício tem um erro de conteúdo do pack — não é um erro seu. Não conta como tentativa.")
        if has_summary:
            message = f"{message} ({self._t('Clique para ver detalhes')})"
        self._feedback.show_result(
            "pending",
            "[!] " + self._t("CONTEÚDO INVÁLIDO"),
            message,
            [],
            animate=self._theme.tokens.animations,
        )
        self._feedback.set_clickable(has_summary)
        self._present_trace_summary()

    _TRACE_SUMMARY_AUTO_COLLAPSE_MS = 4500

    def _present_trace_summary(self) -> None:
        """Auto-opens the "trace resumido" right after a FAIL/content-error,
        then schedules its auto-collapse (see `_collapse_trace_summary`) --
        the drawer itself only owns the expand/collapse animation.
        """
        if self._trace_summary is None:
            self._trace_summary_panel.clear()
            self._trace_collapse_timer.stop()
            return
        title, rows = self._trace_summary_view(self._trace_summary)
        self._trace_summary_panel.set_open_full_text(self._action("Abrir trace completo"))
        self._trace_summary_panel.set_open_full_enabled(self._last_outcome is not None)
        self._trace_summary_panel.show_summary(title, rows, animate=self._theme.tokens.animations)
        self._trace_collapse_timer.stop()
        self._trace_collapse_timer.start(self._TRACE_SUMMARY_AUTO_COLLAPSE_MS)

    def _trace_summary_view(self, summary: TraceSummary) -> tuple[str, list[tuple[str, str]]]:
        """Translates a `TraceSummary` (stable, untranslated `stage`/fields)
        into the (title, field rows) `TraceSummaryPanel.show_summary` renders.
        """
        if summary.stage == "content_error":
            return self._t("Erro de conteúdo"), [(self._t("Erro"), summary.message)]
        if summary.stage == "compilation":
            compilation = summary.compilation
            rows = [
                (self._t("Compilador"), compilation.compiler if compilation else ""),
                (self._t("Flags"), " ".join(compilation.flags) if compilation else ""),
                (self._t("Fontes"), ", ".join(compilation.sources) if compilation else ""),
                (self._t("Saída"), compilation.output_path if compilation else ""),
                (self._t("Erro"), summary.message),
            ]
            return self._t("Falha de compilação"), rows
        if summary.stage == "test":
            failure = summary.test_failure
            rows: list[tuple[str, str]] = []
            if failure is not None:
                rows.append((self._t("Esperado"), failure.expected))
                rows.append((self._t("Recebido"), failure.received))
                if failure.stderr:
                    rows.append((self._t("Stderr"), failure.stderr))
                if failure.timed_out:
                    rows.append((self._t("Timeout"), self._t("Execução excedeu o tempo limite")))
                elif failure.exit_code is not None:
                    rows.append((self._t("Código de saída"), str(failure.exit_code)))
            title = self._t("Teste {index}", index=summary.message)
            return title, rows
        return self._t("Trace resumido"), []

    def _toggle_trace_summary(self) -> None:
        if self._trace_summary is None:
            return
        self._trace_collapse_timer.stop()
        if self._trace_summary_panel.isHidden():
            self._present_trace_summary()
        else:
            self._trace_summary_panel.toggle()

    def _reopen_trace_summary(self) -> None:
        if self._trace_summary is None:
            return
        self._trace_collapse_timer.stop()
        self._trace_summary_panel.expand(animate=self._theme.tokens.animations)

    def _collapse_trace_summary(self) -> None:
        self._trace_summary_panel.collapse(animate=self._theme.tokens.animations)

    def _open_full_trace(self) -> None:
        if self._last_outcome is None:
            return
        try:
            self._coordinator.open_trace_in_editor(self._last_outcome)
        except Exception as error:
            QMessageBox.warning(self, "Editor", str(error))

    def _show_pass_feedback(self, message: str, with_next: bool = False) -> None:
        actions: list[QPushButton] = []
        if with_next and self._mode == "training":
            actions.append(ui.button(self._action("Próximo exercício"), self._next_exercise, "small"))
        self._feedback.show_result("pass", "[✓] PASS", message, actions, animate=self._theme.tokens.animations)
        self._feedback.set_clickable(False)
        self._trace_collapse_timer.stop()
        self._trace_summary_panel.clear()

    def _next_exercise(self) -> None:
        if self._training_options is None or self._mode != "training":
            return
        try:
            self._load_exercise(self._coordinator.choose_training_exercise(self._training_options), mode="training")
        except Exception as error:
            QMessageBox.warning(self, self._t("Treino"), str(error))

    # -------------------------------------------------------------------- exam
    def _start_exam(self) -> None:
        if not self._exam_preflight_checked(self._start_exam):
            return
        pack_id = self._selected_exam_pack_id()
        if pack_id is None:
            QMessageBox.warning(self, self._t("Modo prova"), self._t("Nenhum pack selecionado."))
            return
        try:
            self._exam_state = self._coordinator.start_exam(pack_id)
            self._timer.start(1000)
            self._load_exercise(self._coordinator.exam_ref(self._exam_state), mode="exam")
            self._show_resume_if_needed()
        except Exception as error:
            QMessageBox.warning(self, self._t("Modo prova"), str(error))

    def _show_exam_prepare(self) -> None:
        if not self._exam_preflight_checked(self._show_exam_prepare):
            return
        pack_id = self._selected_exam_pack_id()
        if pack_id is None:
            QMessageBox.warning(self, self._t("Modo prova"), self._t("Nenhum pack selecionado."))
            return
        pack = self._selected_pack(pack_id)
        if pack is None:
            QMessageBox.warning(self, self._t("Modo prova"), self._t("Pack selecionado não encontrado."))
            return
        levels = self._coordinator.list_levels(pack_id)
        lines = [
            f"{self._t('Pack/Rank'):<10}: {pack.name}",
            f"ID        : {pack.id}",
            f"Levels    : {len(levels)}",
            "",
            self._t("Estrutura da prova baseada no pack real:"),
            *(f"  {index}. {level}" for index, level in enumerate(levels, start=1)),
            "",
            f"{self._t('Duração'):<10}: {self._format_seconds(self._coordinator.exam_duration_seconds(pack_id))}"
            + ("" if pack.exam_duration_seconds else f"  ({self._t('padrão; o pack não declara exam.duration_minutes')})"),
            f"{self._t('Aprovação'):<10}: 100%",
            "",
            f"{self._t('Regras')}:",
            f"- {self._t('ao errar, permanece no mesmo exercício;')}",
            f"- {self._t('ao passar, avança automaticamente;')}",
            f"- {self._t('não é permitido trocar exercício;')}",
            f"- {self._t('o relógio NÃO pausa: fechar o app não para o tempo;')}",
            f"- {self._t('se o tempo acabar, a prova encerra e salva nota parcial;')}",
            f"- {self._t('se fechar no meio, a sessão pode ser retomada enquanto houver tempo.')}",
        ]
        self._exam_prepare_text.setPlainText("\n".join(lines))
        self._go(self._exam_prepare_page)

    def _selected_pack(self, pack_id: str):
        for pack in self._coordinator.list_packs():
            if pack.id == pack_id:
                return pack
        return None

    def _resume_exam(self) -> None:
        state = self._coordinator.load_active_exam()
        if state is None:
            QMessageBox.information(self, self._t("Modo prova"), self._t("Não há prova em andamento."))
            return
        self._exam_state = state
        self._timer.start(1000)
        self._load_exercise(self._coordinator.exam_ref(state), mode="exam")

    def _end_exam(self) -> None:
        state = self._coordinator.load_active_exam()
        if state is None:
            QMessageBox.information(self, self._t("Modo prova"), self._t("Não há prova em andamento."))
            return
        self._coordinator.finish_exam(state, "abandoned", state.score)
        self._timer.stop()
        self._clear_exam_editor_context(state.id)
        self._exam_state = None
        self._show_resume_if_needed()
        QMessageBox.information(self, self._t("Modo prova"), self._t("Prova encerrada."))

    def _tick_exam(self) -> None:
        if self._exam_state is None:
            return
        if self._tasks.is_busy("submit"):
            # Do not finish the exam in the middle of grading: the timer keeps
            # drawing and the finish happens when grading returns.
            remaining = self._coordinator.remaining_seconds(self._exam_state)
            self._render_exam_timer(replace(self._exam_state, remaining_seconds=remaining))
            return
        state = self._coordinator.tick_exam(self._exam_state)
        if state is None:
            self._timer.stop()
            self._clear_exam_editor_context()
            self._exam_state = None
            self._exam_timer_label.setText(self._t("Tempo esgotado").upper())
            ui.set_status(self._exam_timer_label, "fail")
            QMessageBox.information(self, self._t("Modo prova"), self._t("Tempo esgotado. Prova encerrada."))
            return
        self._exam_state = state
        self._render_exam_timer(state)

    def _clear_exam_editor_context(self, session_id: str | None = None) -> None:
        if session_id is None and self._exam_state is not None:
            session_id = self._exam_state.id
        if session_id is not None:
            self._editor_targets.pop(("exam", session_id), None)

    def _render_exam_timer(self, state: ExamState) -> None:
        self._exam_timer_label.setText(f"⏱ {self._format_seconds(state.remaining_seconds)}   {self._t('Nota').upper()} {state.score:.0f}%")

    @staticmethod
    def _format_seconds(total: int) -> str:
        minutes, seconds = divmod(max(0, int(total)), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _show_resume_if_needed(self) -> None:
        state = self._coordinator.load_active_exam()
        expired = self._coordinator.pop_expired_exam()
        if expired is not None:
            self._timer.stop()
            self._exam_state = None
            QMessageBox.information(
                self,
                self._t("Modo prova"),
                self._t(
                    "O tempo da prova terminou enquanto o app estava fechado.\nA prova foi encerrada com nota parcial de {score}%.",
                    score=f"{expired.score:.0f}",
                ),
            )
        has_state = state is not None
        self._exam_resume_card.setVisible(has_state)
        self._resume_exam_button.setVisible(has_state)
        self._end_exam_button.setVisible(has_state)
        if state is None:
            self._exam_resume_label.setText("")
            return
        self._exam_resume_label.setText(
            f"{self._t('Rank'):<10}: {state.pack_id}\n"
            f"{self._t('Exercício'):<10}: {state.exercise_id}\n"
            f"{self._t('Restante'):<10}: {self._format_seconds(state.remaining_seconds)}"
        )

    # ---------------------------------------------------------------- history
    def _show_history(self, refresh_filters: bool = True) -> None:
        if refresh_filters:
            self._refresh_history_sidebar()
        self._render_history()
        if self._stack.currentWidget() is not self._history_page:
            self._go(self._history_page)

    def _set_history_view(self, view: str) -> None:
        if view == "sessions":
            view = "exam_sessions"
        self._history_view = view
        self._history_context = (view, None)
        self._render_history()

    def _refresh_history_sidebar(self) -> None:
        self._replace_history_buttons(
            self._history_learning_buttons_layout,
            tuple((language, self._learning_language_display_name(language)) for language in self._history_learning_languages()),
            lambda language: self._select_history_learning(language),
        )
        self._replace_history_buttons(
            self._history_pack_buttons_layout,
            tuple((summary.pack_id, summary.name) for summary in self._coordinator.history_pack_summaries()),
            lambda pack_id: self._select_history_pack(pack_id),
        )

    def _replace_history_buttons(self, layout: QVBoxLayout, items: tuple[tuple[str, str], ...], handler: Callable[[str], None]) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        if not items:
            layout.addWidget(ui.label(self._t("nenhum"), role="muted"))
            return
        for value, label in items:
            button = self._button(label, lambda checked=False, item_value=value: handler(item_value), "option")
            button.setCheckable(True)
            button.setProperty("historyValue", value)
            layout.addWidget(button)

    def _select_history_pack(self, pack_id: str) -> None:
        self._history_context = ("pack", pack_id)
        self._history_view = "pack"
        self._render_history()

    def _select_history_learning(self, language: str) -> None:
        self._history_context = ("learning", language)
        self._history_view = "learning"
        self._render_history()

    def _history_query(self) -> HistoryQuery:
        context, value = self._history_context
        pack = value if context == "pack" else None
        return HistoryQuery(pack_id=pack)

    def _render_history(self) -> None:
        self._sync_history_buttons()
        self._history_clear_inspector()
        context, value = self._history_context
        if context == "pack" and value:
            self._render_history_pack(value)
        elif context == "activity" and value:
            pack_id, activity_id = value.split(":", 1)
            self._render_history_activity(pack_id, activity_id)
        elif context == "learning" and value:
            self._render_history_learning(value)
        elif self._history_view == "training_sessions":
            self._render_history_sessions("training")
        elif self._history_view == "exam_sessions":
            self._render_history_sessions("exam")
        else:
            self._render_history_overview()

    def _sync_history_buttons(self) -> None:
        for key, button in self._history_nav_buttons.items():
            button.setChecked(key == self._history_view)

    def _history_learning_languages(self) -> tuple[str, ...]:
        languages = tuple(self._coordinator.learning_languages())
        if languages:
            return languages
        return tuple(status.language for status in self._coordinator.runtime_statuses())

    def _history_clear_inspector(self) -> None:
        self._history_inspector.hide()
        self._history_selected_attempt = None
        self._history_inspector_title.setText(self._t("Selecione uma atividade"))
        self._history_inspector_body.setText(self._t("Attempts e trace aparecem aqui."))
        self._history_trace_summary.setText(self._t("Nenhuma tentativa selecionada."))
        self._history_trace_button.setEnabled(False)
        self._history_attempt_rows = []
        self._history_session_activity_rows = []
        self._set_table_headers(self._history_attempts_table, ("N", "RESULT", self._t("Data").upper()), 2)
        self._history_attempts_table.setRowCount(0)

    def _render_history_overview(self) -> None:
        exam_summary = self._coordinator.history_exam_summary()
        training_volume = self._coordinator.history_training_volume()
        recent_sessions = self._coordinator.history_recent_sessions()
        learning = self._coordinator.history_learning_summaries()
        self._history_activity_rows = []
        self._history_center_title.setText(self._t("Visão geral do sistema").upper())
        self._history_center_meta.setText(self._t("Resumo do progresso real registrado."))
        self._history_metric_row.hide()
        self._history_exam_heading.setText(self._t("Sessões de prova").upper())
        self._history_exam_total.setText(f"{len(exam_summary.sessions)} {self._t('Total').lower()}")
        self._history_clear_layout(self._history_exam_meter)
        for status, count in (
            ("pass", exam_summary.passed_count),
            ("fail", exam_summary.failed_count),
            ("pending", exam_summary.timed_out_count),
            ("muted", exam_summary.abandoned_count),
        ):
            segment = ui.label("", status=status)
            segment.setFixedHeight(8)
            segment.setMinimumWidth(max(8, count * 12) if count else 4)
            self._history_exam_meter.addWidget(segment)
        self._history_exam_breakdown.setText(
            f"{self._t('Passou')}: {exam_summary.passed_count}\n"
            f"{self._t('Falhou')}: {exam_summary.failed_count}\n"
            f"{self._t('Timeout')}: {exam_summary.timed_out_count}\n"
            f"{self._t('Abandonada')}: {exam_summary.abandoned_count}"
        )

        self._history_training_heading.setText(self._t("Volume de treino").upper())
        self._history_training_total.setText(
            f"{training_volume.total_attempts} {self._t('Tentativas').lower()}"
        )
        self._history_clear_layout(self._history_training_heatmap)
        maximum = max(training_volume.daily_counts, default=0)
        for count in training_volume.daily_counts:
            block = ui.label("", status="pass" if count else "muted")
            width = 8 + round(18 * count / maximum) if maximum and count else 8
            block.setFixedSize(width, 14)
            self._history_training_heatmap.addWidget(block)
        self._history_training_meta.setText(self._t("Últimos 14 dias"))

        if learning:
            current = learning[0]
            percent = round(current.completed_count / current.total_count * 100) if current.total_count else 0
            language = self._learning_language_display_name(current.language).upper()
            self._history_learning_heading.setText(
                f"{self._t('Trilha de aprendizado').upper()} {language} · LVL {current.current_level}"
            )
            self._history_learning_percent.setText(f"{percent}%")
            self._history_learning_progress.setValue(percent)
            self._history_learning_meta.setText(
                f"{current.completed_count} / {current.total_count} {self._t('Atividades').lower()} {self._t('Concluídas').lower()}"
            )
        else:
            self._history_learning_heading.setText(self._t("Trilha de aprendizado").upper())
            self._history_learning_percent.setText("-")
            self._history_learning_progress.setValue(0)
            self._history_learning_meta.setText(self._t("Nenhum progresso de aprendizado ativo."))

        self._history_recent_heading.setText(self._t("Sessões recentes").upper())
        self._history_clear_layout(self._history_recent_sessions)
        for session in recent_sessions:
            row = ui.card()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setSpacing(14)
            kind = ui.label(session.kind, status="pass" if session.kind == "TRAIN" else "pending")
            kind.setMinimumWidth(54)
            pack = ui.label(session.pack_id, role="activity-name")
            row_layout.addWidget(kind)
            row_layout.addWidget(pack)
            row_layout.addStretch(1)
            if session.kind == "TRAIN":
                detail = f"{session.attempts_count} {self._t('Tentativas').lower()} · {session.completed_count} {self._t('Concluídas').lower()}"
            else:
                detail = f"{session.activities_count} {self._t('Atividades').lower()} · {self._t('Resultado')}: {self._display_overview_result(session.result)}"
            row_layout.addWidget(ui.label(f"{self._format_date(session.timestamp)} · {detail}", role="muted"))
            self._history_recent_sessions.addWidget(row)
        if not recent_sessions:
            self._history_recent_sessions.addWidget(ui.label(self._t("Nenhum histórico registrado."), role="muted"))
        self._history_session_table.hide()
        self._history_activity_table.hide()
        self._history_overview_text.show()

    @staticmethod
    def _history_clear_layout(layout: QHBoxLayout | QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _display_overview_result(self, status: str) -> str:
        labels = {
            "passed": self._t("Passou"),
            "completed": self._t("Passou"),
            "failed": self._t("Falhou"),
            "timed_out": self._t("Timeout"),
            "timeout": self._t("Timeout"),
            "abandoned": self._t("Abandonada"),
        }
        return labels.get(status.lower(), status or "-")

    def _history_learning_overview_text(self) -> str:
        summaries = self._coordinator.history_learning_summaries()
        if not summaries:
            return ""
        lines = [f"{self._t('Aprendizado atual')}"]
        for summary in summaries:
            current = summary.current_activity_id or "-"
            lines.append(
                f"{self._learning_language_display_name(summary.language)} · "
                f"{self._t('Nível')} {summary.current_level} · "
                f"{summary.completed_count}/{summary.total_count} · "
                f"{self._t('Atual')}: {current}"
            )
        return "\n".join(lines) + "\n\n"

    def _render_history_pack(self, pack_id: str) -> None:
        summary = self._coordinator.history_pack_summary(pack_id)
        self._history_metric_row.hide()
        self._history_overview_text.hide()
        self._history_session_table.hide()
        self._history_activity_table.show()
        if summary is None:
            self._history_activity_rows = []
            self._history_center_title.setText(pack_id)
            self._history_center_meta.setText(self._t("Pack selecionado não encontrado."))
            self._set_history_metrics(0, 0, 0, 0)
            self._history_activity_table.setRowCount(0)
            return
        self._history_center_title.setText(summary.name)
        self._history_center_meta.setText(
            f"{summary.activities_count} {self._t('Atividades').lower()} · "
            f"{summary.completed_count} {self._t('Concluídas').lower()} · "
            f"{summary.attempts_count} {self._t('Tentativas').lower()}"
        )
        self._set_history_metrics(summary.completed_count, summary.attempts_count, 0, 1)
        self._history_activity_rows = list(summary.activities)
        self._set_table_headers(
            self._history_activity_table,
            (self._t("Atividade").upper(), self._t("Status").upper(), self._t("Tentativas").upper(), self._t("Última").upper()),
            0,
        )
        self._history_activity_table.setRowCount(len(summary.activities))
        for row_index, activity in enumerate(summary.activities):
            cells = (
                self._item(activity.activity_id),
                self._item(self._display_history_status(activity.status)),
                self._item(str(activity.attempts_count), align_right=True),
                self._item(self._format_date(activity.latest_at), "text_secondary"),
            )
            for column, item in enumerate(cells):
                self._history_activity_table.setItem(row_index, column, item)
        if not summary.activities:
            self._history_center_meta.setText(self._t("Pack sem atividades registradas."))

    def _render_history_activity(self, pack_id: str, activity_id: str) -> None:
        self._render_history_pack(pack_id)
        summary = self._coordinator.history_activity_summary(pack_id, activity_id)
        if summary is not None:
            self._show_history_activity(summary)

    def _item(self, text: str, color: str | None = None, align_right: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if color:
            item.setForeground(self._theme.color(color))
        alignment = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft)
        item.setTextAlignment(alignment)
        return item

    def _set_table_headers(self, table: QTableWidget, headers: tuple[str, ...], stretch: int) -> None:
        was_blocked = table.blockSignals(True)
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.blockSignals(was_blocked)
        header = table.horizontalHeader()
        for column in range(len(headers)):
            mode = QHeaderView.ResizeMode.Stretch if column == stretch else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)

    def _set_history_metrics(self, completed: int, attempts: int, exams: int, packs: int) -> None:
        self._history_metric_completed.setText(f"{self._t('Atividades concluídas')}: {completed}")
        self._history_metric_attempts.setText(f"{self._t('Total de tentativas')}: {attempts}")
        self._history_metric_exams.setText(f"{self._t('Sessões de prova')}: {exams}")
        self._history_metric_packs.setText(f"{self._t('Packs usados')}: {packs}")

    def _history_activity_selected(self) -> None:
        row = self._history_activity_table.currentRow()
        if row < 0 or row >= len(getattr(self, "_history_activity_rows", [])):
            return
        activity = self._history_activity_rows[row]
        self._history_context = ("activity", f"{activity.pack_id}:{activity.activity_id}")
        if self._history_view == "learning":
            self._show_history_learning_activity(activity)
        else:
            self._show_history_activity(activity)

    def _show_history_learning_activity(self, activity: LearningActivityRef) -> None:
        self._history_inspector.show()
        self._history_inspector_title.setText(activity.title or activity.activity_id)
        view = self._coordinator.learning_track(activity.language)
        if activity.activity_id in view.completed_activity_ids:
            state = self._t("Concluído")
        elif view.next_activity is not None and activity.activity_id == view.next_activity.activity_id:
            state = self._t("Atual")
        elif is_activity_unlocked(activity, view.completed_activity_ids):
            state = self._t("Disponível")
        else:
            state = self._t("Bloqueado")
        self._history_inspector_body.setText(
            f"{self._t('Estado')}: {state}\n"
            f"{self._t('Nível')}: {activity.position}\n"
            f"{self._t('Pack')}: {activity.pack_id}"
        )
        self._history_attempt_rows = []
        self._set_table_headers(self._history_attempts_table, ("N", "RESULT", self._t("Data").upper()), 2)
        self._history_attempts_table.setRowCount(0)
        self._history_trace_summary.setText(self._t("Nenhuma tentativa registrada."))
        self._history_trace_button.setEnabled(False)

    def _show_history_activity(self, activity) -> None:
        self._history_inspector.show()
        self._history_overview_text.hide()
        detailed = self._coordinator.history_activity_summary(activity.pack_id, activity.activity_id) or activity
        self._history_inspector_title.setText(detailed.title or detailed.activity_id)
        self._history_inspector_body.setText(
            f"{detailed.pack_id}/{detailed.level}\n"
            f"{self._t('Status')}: {self._display_history_status(detailed.status)}\n"
            f"{self._t('Tentativas')}: {detailed.attempts_count}\n"
            f"{self._t('Último resultado')}: {detailed.latest_result}"
        )
        self._history_attempt_rows = list(detailed.attempts)
        self._set_table_headers(self._history_attempts_table, ("N", "RESULT", self._t("Data").upper()), 2)
        self._history_attempts_table.setRowCount(len(self._history_attempt_rows))
        for row_index, attempt in enumerate(self._history_attempt_rows):
            cells = (
                self._item(str(attempt.number), align_right=True),
                self._item(attempt.result, "success" if attempt.result == "PASS" else "fail"),
                self._item(self._format_date(attempt.submitted_at), "text_secondary"),
            )
            for column, item in enumerate(cells):
                self._history_attempts_table.setItem(row_index, column, item)
        if not self._history_attempt_rows:
            self._history_trace_summary.setText(self._t("Nenhuma tentativa registrada."))

    def _history_attempt_selected(self) -> None:
        row = self._history_attempts_table.currentRow()
        if row < 0:
            return
        session_context = self._history_context[0] in {"exam_session", "training_session"}
        rows = getattr(self, "_history_session_activity_rows", []) if session_context else getattr(self, "_history_attempt_rows", [])
        if row >= len(rows):
            return
        if session_context:
            activity = rows[row]
            self._history_inspector_title.setText(activity.activity_id)
            self._history_inspector_body.setText(
                f"{self._t('Resultado')}: {activity.result}\n"
                f"{self._t('Tentativas')}: {activity.attempts_count}\n"
                f"{self._t('Última')}: {self._format_date(activity.updated_at)}"
            )
            self._history_trace_summary.setText(self._t("Trace não disponível para tentativas históricas."))
            self._history_trace_button.setEnabled(False)
            return
        attempt = rows[row]
        self._history_selected_attempt = attempt
        summary = attempt.failure_summary or attempt.status or attempt.result
        trace_state = self._t("Trace não disponível para tentativas históricas.")
        self._history_trace_summary.setText(f"{summary}\n{trace_state}")
        self._history_trace_button.setEnabled(bool(attempt.trace_available))

    def _open_history_attempt_trace(self) -> None:
        QMessageBox.information(
            self,
            self._t("Trace resumido"),
            self._t("Trace não disponível para tentativas históricas."),
        )

    def _render_history_learning(self, language: str) -> None:
        self._history_metric_row.hide()
        self._history_overview_text.hide()
        self._history_session_table.hide()
        self._history_activity_table.show()
        self._history_center_title.setText(f"{self._t('Learning')} · {self._learning_language_display_name(language)}")
        try:
            view = self._coordinator.learning_track(language)
        except Exception:
            self._history_center_meta.setText(self._t("Não foi possível carregar a trilha de aprendizado."))
            self._set_history_metrics(0, 0, 0, 0)
            self._history_activity_table.setRowCount(0)
            return
        completed = len(view.completed_activity_ids)
        total = len(view.track.activities)
        self._history_center_meta.setText(f"{completed}/{total} · {self._t('Trilha de aprendizado')}")
        self._set_history_metrics(completed, total, 0, 0)
        self._history_activity_rows = []
        self._set_table_headers(
            self._history_activity_table,
            (self._t("Atividade").upper(), self._t("Estado").upper(), self._t("Pack").upper()),
            0,
        )
        self._history_activity_table.setRowCount(len(view.track.activities))
        for row_index, activity in enumerate(view.track.activities):
            if activity.activity_id in view.completed_activity_ids:
                status = self._t("Concluído")
            elif view.next_activity is not None and activity.activity_id == view.next_activity.activity_id:
                status = self._t("Atual")
            elif is_activity_unlocked(activity, view.completed_activity_ids):
                status = self._t("Disponível")
            else:
                status = self._t("Bloqueado")
            cells = (
                self._item(activity.activity_id),
                self._item(status),
                self._item(activity.pack_id, "text_secondary"),
            )
            for column, item in enumerate(cells):
                self._history_activity_table.setItem(row_index, column, item)
        if total == 0:
            self._history_center_meta.setText(self._t("Ainda não há atividades nesta trilha."))

    def _render_history_sessions(self, policy: str) -> None:
        exam_summary = None if policy == "training" else self._coordinator.history_exam_summary()
        sessions = (
            self._coordinator.history_training_summaries()
            if policy == "training"
            else exam_summary.sessions
        )
        self._history_activity_rows = []
        self._history_overview_text.hide()
        self._history_metric_row.hide()
        self._history_center_title.setText(self._t("Sessões de treino" if policy == "training" else "Sessões de prova"))
        if policy == "training":
            self._history_center_meta.setText(self._t("Sessões de treino registradas."))
        else:
            self._history_center_meta.setText(
                f"{self._t('Resumo das provas registradas.')} "
                f"{self._t('Passou')}: {exam_summary.passed_count} · "
                f"{self._t('Falhou')}: {exam_summary.failed_count} · "
                f"{self._t('Tempo esgotado')}: {exam_summary.timed_out_count} · "
                f"{self._t('Abandonada')}: {exam_summary.abandoned_count}"
            )
        self._set_history_metrics(0, 0, len(sessions) if policy == "exam" else 0, 0)
        self._history_activity_table.hide()
        self._history_session_table.show()
        self._history_session_rows = list(sessions)
        headers = (
            (self._t("Data").upper(), "PACK", self._t("Duração").upper(), self._t("Atividades").upper(), self._t("Status").upper())
            if policy == "training"
            else (self._t("Data").upper(), "PACK", self._t("Duração").upper(), self._t("Nota").upper(), self._t("Status").upper(), self._t("Atividades").upper())
        )
        self._set_table_headers(self._history_session_table, headers, 1)
        self._history_session_table.setRowCount(len(sessions))
        for row_index, session in enumerate(sessions):
            duration = "-" if session.duration_seconds is None else self._format_seconds(session.duration_seconds)
            status = self._display_session_status(session.status)
            if policy == "training":
                cells = (
                    self._item(self._format_date(session.finished_at), "text_secondary"),
                    self._item(session.pack_id),
                    self._item(duration),
                    self._item(str(session.activities_count), align_right=True),
                    self._item(status),
                )
            else:
                score = "-" if session.score is None else f"{session.score:.0f}%"
                cells = (
                    self._item(self._format_date(session.finished_at), "text_secondary"),
                    self._item(session.pack_id),
                    self._item(duration),
                    self._item(score),
                    self._item(status),
                    self._item(str(session.activities_count), align_right=True),
                )
            for column, item in enumerate(cells):
                self._history_session_table.setItem(row_index, column, item)
        if not sessions:
            if policy == "training":
                self._history_center_meta.setText(self._t("Nenhuma sessão de treino registrada."))

    def _history_session_selected(self) -> None:
        row = self._history_session_table.currentRow()
        if row < 0 or row >= len(getattr(self, "_history_session_rows", [])):
            return
        session = self._history_session_rows[row]
        self._history_inspector.show()
        self._history_context = (f"{session.policy}_session", session.session_id)
        self._history_inspector_title.setText(f"{session.policy} · {session.pack_id}")
        score = "-" if session.score is None else f"{session.score:.0f}%"
        duration = "-" if session.duration_seconds is None else self._format_seconds(session.duration_seconds)
        lines = [
            f"{self._t('Status')}: {self._display_session_status(session.status)}",
            f"{self._t('Nota')}: {score}",
            f"{self._t('Duração')}: {duration}",
        ]
        if session.activities:
            lines.append("")
            lines.extend(f"{activity.activity_id} · {activity.result}" for activity in session.activities)
        else:
            lines.append(self._t("Sessão sem atividades registradas."))
        self._history_inspector_body.setText("\n".join(lines))
        self._history_session_activity_rows = list(session.activities)
        self._set_table_headers(self._history_attempts_table, (self._t("Atividade").upper(), self._t("Resultado").upper(), self._t("Tentativas").upper()), 0)
        self._history_attempts_table.setRowCount(len(self._history_session_activity_rows))
        for row_index, activity in enumerate(self._history_session_activity_rows):
            cells = (
                self._item(activity.activity_id),
                self._item(activity.result, "success" if activity.result == "PASS" else "fail"),
                self._item(str(activity.attempts_count), align_right=True),
            )
            for column, item in enumerate(cells):
                self._history_attempts_table.setItem(row_index, column, item)
        self._history_trace_summary.setText(self._t("Selecione uma activity para ver os detalhes."))
        self._history_trace_button.setEnabled(False)

    @staticmethod
    def _history_result_text(passed: bool | None) -> str:
        if passed is True:
            return "PASS"
        if passed is False:
            return "FAIL"
        return "-"

    @staticmethod
    def _format_date(value: object) -> str:
        if not value:
            return "-"
        try:
            return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return str(value)[:16]

    def _display_history_status(self, status: str) -> str:
        if isinstance(status, ActivityProgress):
            return self._t(ACTIVITY_PROGRESS_LABELS[status])
        return status

    def _display_session_status(self, status: str) -> str:
        labels = {
            "passed": self._t("Passou"),
            "completed": self._t("Passou"),
            "failed": self._t("Falhou"),
            "timed_out": self._t("Tempo esgotado"),
            "timeout": self._t("Tempo esgotado"),
            "abandoned": self._t("Abandonada"),
        }
        return labels.get(status.lower(), status or "-")

    # ---------------------------------------------------------------- settings
    def _remove_managed_pack(self) -> None:
        pack_id = self._managed_pack_combo.currentData()
        if not isinstance(pack_id, str) or not pack_id:
            return
        pack = next((item for item in self._coordinator.list_managed_packs() if item.id == pack_id), None)
        if pack is None:
            self._refresh_packs()
            return

        answer = QMessageBox.question(
            self,
            self._t("Remover pack"),
            self._t(
                'Remover o pack "{pack}"?\n\nEste pack importado será removido do RankedDojo.\nSeu histórico e progresso serão preservados.',
                pack=pack.name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._coordinator.remove_managed_pack(pack_id)
        except Exception as error:  # noqa: BLE001 - UI converts service failures to feedback
            QMessageBox.warning(
                self,
                self._t("Remover pack"),
                f"{self._t('Não foi possível remover o pack.')}\n\n{error}",
            )
            return
        self._refresh_packs()
        QMessageBox.information(
            self,
            self._t("Remover pack"),
            self._t("Pack removido. Histórico e progresso foram preservados."),
        )

    def _import_pack(self) -> None:
        if self._tasks.is_busy("import"):
            return
        path = self._choose_pack_source()
        if path is None:
            return
        self._packs_summary.setText(self._t("validando pack..."))
        self._run_task(
            "import",
            lambda: self._coordinator.inspect_pack(path),
            lambda report: self._confirm_pack_import(path, report),
            self._t("Importar Pack"),
            on_finally=self._refresh_packs,
        )

    def _choose_pack_source(self) -> Path | None:
        choice = self._ask_pack_source_format()
        if choice is None:
            return None
        if choice == "zip":
            selected, _ = QFileDialog.getOpenFileName(
                self,
                self._t("Selecionar pack ZIP"),
                "",
                self._t("Pack ZIP (*.zip);;Todos os arquivos (*)"),
            )
        else:
            selected = QFileDialog.getExistingDirectory(self, self._t("Selecionar pasta do pack"))
        return Path(selected) if selected else None

    def _pack_source_format_dialog(self) -> tuple[QMessageBox, QPushButton, QPushButton, QPushButton]:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self._t("Importar Pack"))
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(self._t("Qual é o formato do pack?"))
        dialog.setInformativeText(
            self._t("Escolha ZIP para selecionar um arquivo compactado ou Pasta para selecionar uma pasta de pack.")
        )
        zip_button = dialog.addButton("ZIP", QMessageBox.ButtonRole.ActionRole)
        folder_button = dialog.addButton(self._t("Pasta").upper(), QMessageBox.ButtonRole.ActionRole)
        cancel_button = dialog.addButton(self._t("Cancelar").upper(), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(zip_button)
        dialog.setEscapeButton(cancel_button)
        return dialog, zip_button, folder_button, cancel_button

    def _ask_pack_source_format(self) -> str | None:
        dialog, zip_button, folder_button, _cancel_button = self._pack_source_format_dialog()
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is zip_button:
            return "zip"
        if clicked is folder_button:
            return "folder"
        return None

    def _confirm_pack_import(self, path: Path, report) -> None:
        if report.has_executable_code:
            listed = "\n".join(f"  - {name}" for name in report.executable_files[:8])
            more = "" if len(report.executable_files) <= 8 else self._t("\n  ... e mais {count}", count=len(report.executable_files) - 8)
            answer = QMessageBox.warning(
                self,
                self._t("Importar Pack"),
                self._t(
                    "O pack \"{pack}\" contém código que será compilado e EXECUTADO no seu computador durante a correção (fixtures/references):\n\n{listed}{more}\n\nNão há sandbox: esse código roda com as permissões do seu usuário. Importe apenas packs de fontes em que você confia.\n\nImportar mesmo assim?",
                    pack=report.pack.name,
                    listed=listed,
                    more=more,
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._packs_summary.setText(self._t("importando pack..."))
        self._run_task(
            "import",
            lambda: self._coordinator.import_pack(path),
            self._on_pack_imported,
            self._t("Importar Pack"),
            on_finally=self._refresh_packs,
        )

    def _on_pack_imported(self, pack) -> None:
        QMessageBox.information(self, self._t("Importar Pack"), self._t("Pack importado: {pack}", pack=pack.name))
        self._resume_pending_if_ready()

    def _show_settings(self, section: str | None = None) -> None:
        title = self._t("Configurações").upper() if section is None else f"{self._t('Configurações').upper()} > {self._t(section).upper()}"
        self._set_title_label(self._settings_title, title)
        self._settings_workspace.setText(str(self._coordinator.workspace_root))
        self._settings_editor.setText(self._coordinator.editor_command())
        for language in self._runtime_labels:
            self._show_runtime_cached(language)
        self._go(self._settings_page)

    def _show_pack_help(self) -> None:
        self._pack_help_text.setPlainText(self._pack_help_content())
        self._go(self._pack_help_page)

    def _open_full_documentation(self) -> None:
        document = self._documentation_path()
        if document is None:
            QMessageBox.warning(self, self._t("Documentação"), self._t("Documentação não encontrada nesta instalação."))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(document)))

    @staticmethod
    def _documentation_path() -> Path | None:
        """README from checkout or packaged executable; otherwise the pack contract."""
        candidates = []
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(Path(bundle) / "README.md")
        candidates.append(Path(__file__).resolve().parents[5] / "README.md")
        try:
            candidates.append(resource_path(PACK_CONTRACT))
        except (OSError, TypeError, ValueError):
            pass
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _pack_help_content() -> str:
        """Pack contract (single source: resources/pack-contract.md) + active capabilities."""
        capabilities = default_exercise_capabilities()
        executions = ", ".join(sorted(capabilities.executions.supported))
        generators = ", ".join(sorted(capabilities.generators.supported))
        expectations = ", ".join(sorted(capabilities.expectations.supported))
        languages = ", ".join(sorted(capabilities.languages))
        active = (
            "Capabilities deste app\n"
            f"  linguagens   : {languages}\n"
            f"  execution    : {executions}\n"
            f"  generators   : {generators}\n"
            f"  expectations : {expectations}\n\n"
        )
        try:
            contract = pack_contract_text()
        except OSError:
            contract = tr("Documentação do contrato não encontrada nesta instalação.")
        return active + contract

    def _change_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, self._t("Selecionar nova workspace"))
        if not selected:
            return
        try:
            self._coordinator.change_workspace(Path(selected))
            self._workspace_path = Path(selected)
            self._workspace_label.setText(self._workspace_prompt())
            self._settings_workspace.setText(str(self._workspace_path))
            QMessageBox.information(self, "Workspace", self._t("Workspace atualizada."))
            self._resume_pending_if_ready()
        except Exception as error:
            QMessageBox.warning(self, "Workspace", str(error))

    def _save_editor_setting(self) -> None:
        try:
            self._coordinator.save_editor_command(self._settings_editor.text())
            QMessageBox.information(self, "Editor", self._t("Editor atualizado."))
            self._resume_pending_if_ready()
        except Exception as error:
            QMessageBox.warning(self, "Editor", str(error))

    def _refresh_compiler_setting(self) -> None:
        language = self._first_runtime_language()
        if language is not None:
            self._redetect_runtime(language)

    def _show_compiler_result(self, compiler: object) -> None:
        language = self._first_runtime_language()
        if language is not None:
            self._show_runtime_result(language, compiler)

    def _show_compiler_cached(self) -> None:
        language = self._first_runtime_language()
        if language is not None:
            self._show_runtime_cached(language)

    def _show_runtime_result(self, language: str, tool: object, cached: bool = False) -> None:
        label = self._runtime_labels[language]
        if tool:
            label.setText(f"● {'' if cached else 'OK   '}{tool}")
            ui.set_status(label, "pass")
        elif cached:
            label.setText(f"● {self._t('não verificado — use [ DETECTAR NOVAMENTE ]')}")
            ui.set_status(label, "pending")
        else:
            label.setText(f"● {self._t('não encontrado — selecione o executável manualmente')}")
            ui.set_status(label, "fail")

    def _show_runtime_cached(self, language: str) -> None:
        self._show_runtime_result(language, self._coordinator.runtime_current_tool(language), cached=True)

    def _redetect_runtime(self, language: str) -> None:
        label = self._runtime_labels[language]
        label.setText(f"● {self._t('detectando...')}")
        ui.set_status(label, "pending")
        self._run_task(
            "runtime",
            lambda: self._coordinator.redetect_runtime(language),
            lambda tool: self._show_runtime_result(language, tool),
            self._coordinator.runtime_display_name(language),
        )

    def _choose_manual_runtime(self, language: str) -> None:
        display_name = self._coordinator.runtime_display_name(language)
        filter_text = self._t("Executáveis (*.exe);;Todos os arquivos (*)") if sys.platform == "win32" else self._t("Todos os arquivos (*)")
        selected, _ = QFileDialog.getOpenFileName(self, self._t("Selecionar {display_name}", display_name=display_name), "", filter_text)
        if not selected:
            return
        path = Path(selected)
        self._runtime_labels[language].setText(f"● {self._t('validando...')}")

        def done(tool: object) -> None:
            self._show_runtime_result(language, self._coordinator.runtime_current_tool(language))
            QMessageBox.information(self, display_name, self._t("{display_name} atualizado.", display_name=display_name))
            self._resume_pending_if_ready()

        self._run_task(
            "runtime",
            lambda: self._coordinator.save_manual_runtime(language, path),
            done,
            display_name,
            on_finally=lambda: self._show_runtime_cached(language),
        )

    def _editor_preset_changed(self, _index: int) -> None:
        label = str(self._editor_combo.currentData() or self._editor_combo.currentText())
        if label == "Outro...":
            self._browse_editor()
            return
        resolved = self._coordinator.resolve_known_editor(label)
        if resolved is not None:
            self._settings_editor.setText(resolved)

    def _browse_editor(self) -> None:
        filter_text = self._t("Executáveis (*.exe);;Todos os arquivos (*)") if sys.platform == "win32" else self._t("Todos os arquivos (*)")
        selected, _ = QFileDialog.getOpenFileName(self, self._t("Selecionar executável do editor"), "", filter_text)
        if selected:
            self._settings_editor.setText(str(Path(selected)))

    def _detect_editor_automatically(self) -> None:
        # Explicit "Detectar automaticamente" action: re-run the same known-editor
        # resolution already used by the preset combo (`resolve_known_editor`),
        # without requiring the person to touch the preset selector.
        #
        # If the currently selected preset is a known one, try it first -- this is
        # the direct redetection case. Otherwise (or if that preset is no longer
        # found), fall back to trying every known editor and filling in the first
        # one that resolves, keeping the preset combo in sync with the result.
        current = str(self._editor_combo.currentData() or self._editor_combo.currentText())
        ordered_labels = list(self._coordinator.known_editor_labels())
        if current in ordered_labels:
            ordered_labels.remove(current)
            ordered_labels.insert(0, current)

        for label in ordered_labels:
            resolved = self._coordinator.resolve_known_editor(label)
            if resolved is None:
                continue
            index = self._editor_combo.findData(label)
            if index >= 0:
                self._editor_combo.blockSignals(True)
                self._editor_combo.setCurrentIndex(index)
                self._editor_combo.blockSignals(False)
            self._settings_editor.setText(resolved)
            QMessageBox.information(
                self, "Editor", self._t("{editor} detectado automaticamente.", editor=self._t(label))
            )
            return

        QMessageBox.warning(self, "Editor", self._t("Nenhum editor conhecido foi encontrado automaticamente."))

    def _choose_manual_compiler(self) -> None:
        language = self._first_runtime_language()
        if language is not None:
            self._choose_manual_runtime(language)

    def _first_runtime_language(self) -> str | None:
        return next(iter(self._runtime_labels), None)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Let in-progress grading/import finish writing before closing.
        self._tasks.wait(15_000)
        super().closeEvent(event)

    def _back_from_exercise(self) -> None:
        self._go(self._exam_page if self._mode == "exam" else self._training_page)
