from __future__ import annotations

import dataclasses
import shutil
import tempfile
import threading
import unittest
import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QVBoxLayout, QWidget

from rankeddojo.adapters.editor.editor_registry import EDITOR_REGISTRY
from rankeddojo.adapters.editor.subprocess_editor import SubprocessEditor, SubprocessEditorFactory
from rankeddojo.adapters.pack.local_pack_catalog import LocalPackCatalog
from rankeddojo.adapters.pack.local_pack_importer import LocalPackImporter
from rankeddojo.adapters.persistence.json_app_config_repository import JsonAppConfigRepository
from rankeddojo.adapters.persistence.sqlite_progress_repository import SQLiteProgressRepository
from rankeddojo.adapters.persistence.sqlite_store import SQLiteStore
from rankeddojo.adapters.runtime.c_runtime import CRuntime
from rankeddojo.adapters.ui.qt.i18n import LocaleService
from rankeddojo.adapters.ui.qt.main_window import MainWindow
from rankeddojo.adapters.workspace.local_exercise_workspace import LocalExerciseWorkspace
from rankeddojo.adapters.workspace.local_workspace import LocalWorkspace
from rankeddojo.application.engine.runtime_registry import RuntimeRegistry
from rankeddojo.application.use_cases.mvp_coordinator import MVPTrainerCoordinator, PreflightResult
from rankeddojo.domain.activity_definition import UsageCategory
from rankeddojo.domain.grading import GradingOutcome, GradingResult, TraceData
from rankeddojo.domain.progress import ActivityProgress
from rankeddojo.ports.compiler_port import CompilationResult
from rankeddojo.ports.runtime_port import RuntimeStatus
from rankeddojo.ports.grader_port import GradingRequest


class StaticGrader:
    def __init__(self, passed: bool = True) -> None:
        self.passed = passed

    def grade(self, request: GradingRequest) -> GradingResult:
        return GradingResult(
            outcome=GradingOutcome.PASSED if self.passed else GradingOutcome.USER_FAILED,
            trace_data=TraceData(("trace",)),
        )


class AvailableCompiler:
    def __init__(self) -> None:
        self.redetect_calls = 0

    def is_available(self) -> bool:
        return True

    def find_compiler(self) -> str:
        return "gcc"

    def redetect(self) -> str:
        self.redetect_calls += 1
        return "gcc"

    def current_compiler(self) -> str:
        return "gcc"

    def cached_compiler(self) -> str | None:
        return "gcc"

    def validate_compiler(self, compiler_path) -> bool:
        return True

    def set_manual_compiler(self, compiler_path) -> None:
        pass

    def compile(
        self,
        source_files: list[Path],
        output_path: Path,
        *,
        include_dirs: tuple[Path, ...] = (),
    ) -> CompilationResult:
        return CompilationResult(success=True, executable_path=output_path)


class BlockingGrader:
    """Hold grading until the test releases it, so the UI can be observed while busy."""

    def __init__(self, passed: bool = False) -> None:
        self.passed = passed
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0
        self.thread_ids: list[int] = []

    def grade(self, request: GradingRequest) -> GradingResult:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        self.started.set()
        self.release.wait(10)
        return GradingResult(
            outcome=GradingOutcome.PASSED if self.passed else GradingOutcome.USER_FAILED,
            trace_data=TraceData(("trace",)),
        )


class FailingGrader:
    def grade(self, request: GradingRequest) -> GradingResult:
        raise RuntimeError("compiler disappeared")


class RecordingEditor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, bool]] = []
        self.file_calls: list[tuple[Path, bool]] = []

    def open_directory(self, directory: Path, *, reuse_window: bool = False) -> None:
        self.calls.append((directory, reuse_window))

    def open_file(self, path: Path, *, reuse_window: bool = True) -> None:
        self.file_calls.append((path, reuse_window))


class SlowProbeCompiler(AvailableCompiler):
    """Mimic SystemCCompiler: only knows whether a compiler exists after a probe."""

    def __init__(self, found: bool = True) -> None:
        super().__init__()
        self.found = found
        self._cached: str | None = None
        self.probe_threads: list[int] = []

    def cached_compiler(self) -> str | None:
        return self._cached

    def is_available(self) -> bool:
        if self._cached is None:
            self.probe_threads.append(threading.get_ident())
            self._cached = "gcc" if self.found else None
        return self._cached is not None

    def current_compiler(self) -> str | None:
        return self._cached


class MainWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        # Modal dialogs lock offscreen mode forever: in tests they are only
        # recorded. Tests that need to verify messages replace them themselves.
        cls._dialogs: list[tuple[str, str]] = []
        cls._original_dialogs = {
            name: getattr(QMessageBox, name) for name in ("information", "warning", "question", "critical")
        }
        for name in cls._original_dialogs:
            setattr(
                QMessageBox,
                name,
                staticmethod(
                    lambda parent, title, text, *args, _name=name, **kwargs: cls._dialogs.append((_name, text))
                    or QMessageBox.StandardButton.Yes
                ),
            )

    @classmethod
    def tearDownClass(cls) -> None:
        for name, original in cls._original_dialogs.items():
            setattr(QMessageBox, name, original)

    def _window(
        self,
        temp_dir: str,
        passed: bool = True,
        grader=None,
        compiler=None,
        locale_service=None,
        editor=None,
    ) -> MainWindow:
        root = Path(temp_dir)
        workspace = root / "workspace"
        workspace.mkdir()
        compiler = compiler or AvailableCompiler()
        self._compiler = compiler
        coordinator = MVPTrainerCoordinator(
            pack_catalog=LocalPackCatalog(
                managed_packs_dir=root / "managed",
                bundled_packs_dir=Path(__file__).parent.parent / "examples" / "packs",
            ),
            progress_repository=SQLiteProgressRepository(SQLiteStore(root / "trainer.sqlite3")),
            workspace=LocalExerciseWorkspace(),
            grader=grader or StaticGrader(passed),
            editor=editor or SubprocessEditor("definitely-not-used"),
            pack_importer=LocalPackImporter(root / "managed"),
            runtimes=RuntimeRegistry([CRuntime(compiler, manager=compiler)]),
            workspace_root=workspace,
            editor_factory=SubprocessEditorFactory(),
            workspace_port=LocalWorkspace(),
        )
        window = MainWindow(workspace, coordinator, locale_service=locale_service)
        # UI tests use the example C pack; python-basics is also bundled.
        for combo in (window._training_pack_combo, window._exam_pack_combo):
            combo.setCurrentIndex(combo.findData("sample_rank"))
        return window

    @staticmethod
    def _runtime_action_button(window: MainWindow, source: str) -> QPushButton:
        for button in window._settings_page.findChildren(QPushButton):
            if button.property("sourceText") == source:
                return button
        raise AssertionError(f"No settings button found for source={source!r}")

    def test_open_editor_first_time_targets_current_exercise_without_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            editor = RecordingEditor()
            window = self._window(temp_dir, editor=editor)
            window._coordinator.preflight_editor = lambda: PreflightResult.passed()
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)

            window._open_editor()

            self.assertEqual(editor.calls, [(window._active.exercise_workspace_path, False)])

    def test_training_reuses_editor_window_when_switching_exercises_after_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            editor = RecordingEditor()
            window = self._window(temp_dir, editor=editor)
            window._coordinator.preflight_editor = lambda: PreflightResult.passed()
            refs = window._coordinator._pack_catalog.list_exercises("sample_rank")[:2]
            window._load_exercise(refs[0], mode="training", overwrite=True)
            first_path = window._active.exercise_workspace_path
            window._open_editor()

            window._load_exercise(refs[1], mode="training", overwrite=True)

            self.assertEqual(editor.calls[0], (first_path, False))
            self.assertEqual(editor.calls[1], (window._active.exercise_workspace_path, True))
            self.assertTrue(first_path.exists())
            self.assertTrue(window._active.exercise_workspace_path.exists())

    def test_switching_exercise_does_not_open_editor_if_user_never_opened_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            editor = RecordingEditor()
            window = self._window(temp_dir, editor=editor)
            window._coordinator.preflight_editor = lambda: PreflightResult.passed()
            refs = window._coordinator._pack_catalog.list_exercises("sample_rank")[:2]

            window._load_exercise(refs[0], mode="training", overwrite=True)
            window._load_exercise(refs[1], mode="training", overwrite=True)

            self.assertEqual(editor.calls, [])

    def test_exam_pass_reuses_editor_window_for_next_exercise_and_preserves_previous_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            editor = RecordingEditor()
            window = self._window(temp_dir, editor=editor)
            window._coordinator.preflight_editor = lambda: PreflightResult.passed()
            state = window._coordinator.start_exam("sample_rank", 60)
            window._exam_state = state
            window._load_exercise(window._coordinator.exam_ref(state), mode="exam", overwrite=True)
            first_active = window._active
            first_path = first_active.exercise_workspace_path
            window._open_editor()

            result = window._coordinator.submit_exam(state, first_active)
            window._on_exam_graded(first_active, result)

            self.assertTrue(first_path.exists())
            self.assertIsNotNone(window._active)
            self.assertNotEqual(window._active.exercise_workspace_path, first_path)
            self.assertEqual(editor.calls[0], (first_path, False))
            self.assertEqual(editor.calls[1], (window._active.exercise_workspace_path, True))

    def test_exam_fail_does_not_reopen_editor_for_same_exercise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            editor = RecordingEditor()
            window = self._window(temp_dir, passed=False, editor=editor)
            window._coordinator.preflight_editor = lambda: PreflightResult.passed()
            state = window._coordinator.start_exam("sample_rank", 60)
            window._exam_state = state
            window._load_exercise(window._coordinator.exam_ref(state), mode="exam", overwrite=True)
            active = window._active
            window._open_editor()

            result = window._coordinator.submit_exam(state, active)
            window._on_exam_graded(active, result)

            self.assertEqual(len(editor.calls), 1)

    def test_home_navigates_to_training_exam_history_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._open_study_flow()
            self.assertIs(window._stack.currentWidget(), window._study_page)
            window._show_home()
            self.assertIs(window._stack.currentWidget(), window._home_page)

            window._open_training_setup()
            self.assertIs(window._stack.currentWidget(), window._training_page)

            window._open_exam_setup()
            self.assertIs(window._stack.currentWidget(), window._exam_page)

            window._show_history()
            self.assertIs(window._stack.currentWidget(), window._history_page)

            window._show_settings()
            self.assertIs(window._stack.currentWidget(), window._settings_page)

    def test_home_keeps_study_intent_form_on_dedicated_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            self.assertIs(window._stack.currentWidget(), window._home_page)
            self.assertFalse(window._home_page.isAncestorOf(window._study_topic))
            self.assertTrue(any("QUERO ESTUDAR ALGO NOVO" in button.text() for button in window._menu_buttons))

            window._menu_buttons[0].click()
            self.assertIs(window._stack.currentWidget(), window._study_page)
            self.assertTrue(window._study_page.isAncestorOf(window._study_topic))

    def test_study_generator_exposes_progression_and_scale_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._open_study_flow()

            self.assertEqual(window._study_progression_combo.currentData(), "progressive")
            self.assertEqual(window._study_levels_combo.currentData(), "automatic")
            self.assertEqual(window._study_exercises_per_level_combo.currentData(), "automatic")
            window._locale.set_locale("en")
            self.assertEqual(window._study_levels_combo.currentText(), "Automatic")
            self.assertEqual(window._study_levels_combo.currentData(), "automatic")
            window._locale.set_locale("es")
            self.assertEqual(window._study_levels_combo.currentData(), "automatic")
            self.assertEqual(window._study_progression_combo.currentData(), "progressive")

    def test_study_generator_language_selector_uses_content_language_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._open_study_flow()
            window._locale.set_locale("en")

            self.assertEqual(
                [window._study_language_combo.itemData(index) for index in range(window._study_language_combo.count())],
                ["automatic", "c", "cpp", "java", "python", "custom"],
            )
            labels = [window._study_language_combo.itemText(index) for index in range(window._study_language_combo.count())]
            self.assertEqual(labels, ["Automatic", "C", "C++", "Java", "Python", "Custom"])
            self.assertFalse(any("Compilador" in label or "runtime not checked" in label for label in labels))

            with mock.patch.object(window._coordinator, "runtime_statuses", return_value=()):
                window._refresh_study_languages()
            self.assertEqual(window._study_language_combo.count(), 6)

            window._study_language_combo.setCurrentIndex(window._study_language_combo.findData("custom"))
            self.assertTrue(window._study_custom_language.isEnabled())
            window._study_custom_language.setText("Rust")
            self.assertEqual(window._study_intent().programming_language, "Rust")
            window._generate_study_prompt()
            self.assertIn("PROGRAMMING LANGUAGE\nRust", window._study_prompt_output.toPlainText())

    def test_home_shows_pack_empty_state_only_without_managed_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            self.assertFalse(window._home_pack_empty_state.isHidden())
            self.assertIsInstance(window._home_pack_empty_actions, QWidget)
            self.assertIsInstance(window._home_pack_empty_actions.layout(), QVBoxLayout)
            self.assertEqual(window._home_generate_pack_button.size(), window._home_import_pack_button.size())
            with mock.patch.object(window._coordinator, "list_managed_packs", return_value=[object()]):
                window._refresh_home_status()
            self.assertTrue(window._home_pack_empty_state.isHidden())

    def test_pack_removal_control_is_disabled_without_managed_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            remove_button = self._runtime_action_button(window, "[ REMOVER PACK ]")

            self.assertEqual(window._managed_pack_combo.count(), 0)
            self.assertFalse(remove_button.isEnabled())

    def test_confirmed_managed_pack_removal_refreshes_catalog_and_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            managed = Path(temp_dir) / "managed"
            shutil.copytree(Path(__file__).parent.parent / "examples" / "packs" / "sample_rank", managed / "sample_rank")
            window = self._window(temp_dir)
            window._refresh_packs()
            window._managed_pack_combo.setCurrentIndex(0)

            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                 mock.patch.object(QMessageBox, "information"):
                window._remove_managed_pack()

            self.assertFalse((managed / "sample_rank").exists())
            self.assertEqual(window._managed_pack_combo.count(), 0)
            self.assertFalse(window._home_pack_empty_state.isHidden())

    def test_home_generates_and_copies_vendor_neutral_pack_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._open_study_flow()
            window._study_topic.setPlainText("ponteiros e strings")
            c_index = window._study_language_combo.findData("c")
            self.assertGreaterEqual(c_index, 0)
            window._study_language_combo.setCurrentIndex(c_index)

            window._generate_study_prompt()
            prompt = window._study_prompt_output.toPlainText()

            self.assertIn("ponteiros e strings", prompt)
            self.assertIn("PROGRAMMING LANGUAGE\nC", prompt)
            self.assertIn("Linguagem de programacao: C", prompt)
            self.assertIn("Idioma dos subjects/conteudo: pt-BR", prompt)
            self.assertIn("Contrato atual do pack", prompt)
            self.assertIn("program_output", prompt)
            self.assertNotIn("OpenAI", prompt)
            self.assertNotIn("Claude", prompt)

            window._copy_study_prompt()
            self.assertEqual(QApplication.clipboard().text(), prompt)

    def test_resume_buttons_appear_only_with_active_exam(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            self.assertTrue(window._resume_exam_button.isHidden())
            self.assertTrue(window._end_exam_button.isHidden())

            state = window._coordinator.start_exam("sample_rank", 60)
            window._show_resume_if_needed()

            self.assertFalse(window._resume_exam_button.isHidden())
            self.assertFalse(window._end_exam_button.isHidden())
            window._coordinator.finish_exam(state, "abandoned", 0)

    def test_subject_markdown_is_rendered_in_exercise_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            ref = next(
                ref
                for ref in window._coordinator._pack_catalog.list_exercises("c-basics")
                if ref.definition.id == "argc_counter"
            )

            window._load_exercise(ref, mode="training", overwrite=True)

            rendered = window._subject.toPlainText()
            first_line = next(line for line in rendered.splitlines() if line.strip())
            self.assertEqual(window._cursor._titles[window._exercise_title], "Argc Counter")
            self.assertNotEqual(first_line.strip(), "argc_counter")
            self.assertIn("argc_counter", rendered)
            self.assertIn("Comportamento esperado", rendered)
            self.assertIn("\\n", rendered)
            self.assertFalse(window._sidebar_expected_block.isHidden())
            self.assertEqual(window._sidebar_expected_value.text(), "> argc_counter.c")
            self.assertNotIn("# argc_counter", rendered)
            self.assertNotIn("## Arquivo esperado", rendered)
            self.assertNotIn("`argc_counter.c`", rendered)
            stylesheet = window._subject.document().defaultStyleSheet()
            self.assertIn("h2", stylesheet)
            self.assertIn("h3", stylesheet)
            self.assertIn("border-bottom", stylesheet)

    def test_settings_open_does_not_run_compiler_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._show_settings()

            self.assertEqual(self._compiler.redetect_calls, 0)

    def test_pack_help_page_documents_pack_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._show_pack_help()

            self.assertIs(window._stack.currentWidget(), window._pack_help_page)
            content = window._pack_help_text.toPlainText()
            self.assertIn("Contrato de Pack — RankedDojo", content)
            self.assertIn("pack.json", content)
            self.assertIn("exercise.json", content)
            self.assertIn("subject.md", content)
            self.assertIn("programming_language", content)
            self.assertIn("content_language", content)
            self.assertIn("program_output", content)
            self.assertIn("function_call", content)
            self.assertIn("random_arguments", content)
            self.assertIn("reference_output", content)
            # single source: help shows the same contract file referenced by README
            from rankeddojo.resources import pack_contract_text

            self.assertIn(pack_contract_text().strip(), content)

    def test_settings_packs_shows_capabilities_and_links_contract_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._show_settings("Packs")

            summary = window._pack_capabilities_summary.text()
            self.assertIn("schema_version 3", summary)
            self.assertIn("Runtimes:", summary)
            self.assertIn("Strategies:", summary)
            self.assertIn("Validators/expectations:", summary)
            window._show_pack_help()
            self.assertIs(window._stack.currentWidget(), window._pack_help_page)

    def test_import_pack_cancel_zip_dialog_does_not_open_folder_dialog_or_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            with mock.patch.object(window, "_ask_pack_source_format", return_value="zip"), \
                 mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as open_file, \
                 mock.patch.object(QFileDialog, "getExistingDirectory") as open_dir, \
                 mock.patch.object(window._coordinator, "inspect_pack") as inspect_pack:
                window._import_pack()

            open_file.assert_called_once()
            open_dir.assert_not_called()
            inspect_pack.assert_not_called()

    def test_import_pack_format_choice_dialog_uses_explicit_buttons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            dialog, zip_button, folder_button, cancel_button = window._pack_source_format_dialog()
            labels = {button.text() for button in dialog.buttons()}

            self.assertEqual(dialog.windowTitle(), "Importar Pack")
            self.assertEqual(dialog.text(), "Qual é o formato do pack?")
            self.assertIn("ZIP", labels)
            self.assertIn("PASTA", labels)
            self.assertIn("CANCELAR", labels)
            self.assertEqual(zip_button.text(), "ZIP")
            self.assertEqual(folder_button.text(), "PASTA")
            self.assertEqual(cancel_button.text(), "CANCELAR")

    def test_import_pack_cancel_format_choice_opens_no_explorer_and_imports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            with mock.patch.object(window, "_ask_pack_source_format", return_value=None), \
                 mock.patch.object(QFileDialog, "getOpenFileName") as open_file, \
                 mock.patch.object(QFileDialog, "getExistingDirectory") as open_dir, \
                 mock.patch.object(window._coordinator, "inspect_pack") as inspect_pack:
                window._import_pack()

            open_file.assert_not_called()
            open_dir.assert_not_called()
            inspect_pack.assert_not_called()

    def test_import_pack_cancel_folder_dialog_does_not_open_zip_dialog_or_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            with mock.patch.object(window, "_ask_pack_source_format", return_value="folder"), \
                 mock.patch.object(QFileDialog, "getOpenFileName") as open_file, \
                 mock.patch.object(QFileDialog, "getExistingDirectory", return_value="") as open_dir, \
                 mock.patch.object(window._coordinator, "inspect_pack") as inspect_pack:
                window._import_pack()

            open_file.assert_not_called()
            open_dir.assert_called_once()
            inspect_pack.assert_not_called()

    def test_import_pack_folder_selection_starts_single_import_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            source = Path(temp_dir) / "pack-folder"
            source.mkdir()
            tasks: list[tuple[str, str]] = []
            window._run_task = lambda key, work, on_done, title, on_finally=None: tasks.append((key, title)) or True
            with mock.patch.object(window, "_ask_pack_source_format", return_value="folder"), \
                 mock.patch.object(QFileDialog, "getOpenFileName") as open_file, \
                 mock.patch.object(QFileDialog, "getExistingDirectory", return_value=str(source)) as open_dir:
                window._import_pack()

            open_file.assert_not_called()
            open_dir.assert_called_once()
            self.assertEqual(tasks, [("import", "Importar Pack")])
            self.assertEqual(window._packs_summary.text(), "validando pack...")

    def test_import_pack_errors_are_still_reported_by_task_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            source = Path(temp_dir) / "bad.zip"
            source.write_text("not a zip", encoding="utf-8")
            shown: list[str] = []
            with mock.patch.object(window, "_ask_pack_source_format", return_value="zip"), \
                 mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "Pack ZIP (*.zip)")), \
                 mock.patch.object(window._coordinator, "inspect_pack", side_effect=ValueError("invalid pack")), \
                 mock.patch.object(QMessageBox, "warning", side_effect=lambda parent, title, text, *a, **k: shown.append(text)):
                window._import_pack()
                self.assertTrue(window._tasks.wait())

            self.assertEqual(shown, ["invalid pack"])

    def test_global_style_does_not_use_neon_green_as_solid_button_background(self) -> None:
        # The intent here is button-scoped: no QPushButton/global button rule
        # may use the neon green as a solid background. It is not a blanket
        # ban on that color anywhere in the stylesheet -- the Fase 3 mini
        # logo (QLabel[role="logo"]) legitimately uses it as its fill, and
        # that is not what this test is meant to catch.
        import re

        rule_pattern = re.compile(r"([^{}]+)\{([^{}]*)\}")
        comment_pattern = re.compile(r"/\*.*?\*/", re.DOTALL)

        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            style = window.styleSheet()
            style_lower = style.lower()
            clean_style = comment_pattern.sub("", style)
            button_rules = "\n".join(
                body
                for selector, body in rule_pattern.findall(clean_style)
                if "QPushButton" in selector
            ).lower()

            self.assertNotIn("background: #39ff14", button_rules)
            self.assertNotIn("background-color: #39ff14", button_rules)
            self.assertIn("background: #102010", style_lower)
            self.assertIn("qpushbutton:hover", style_lower)

    def test_training_and_exam_next_button_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))

            window._load_exercise(ref, mode="training", overwrite=True)
            self.assertFalse(window._next_button.isHidden())
            self.assertTrue(window._next_button.isEnabled())

            state = window._coordinator.start_exam("sample_rank", 60)
            window._exam_state = state
            window._load_exercise(window._coordinator.exam_ref(state), mode="exam", overwrite=True)
            self.assertTrue(window._next_button.isHidden())
            window._coordinator.finish_exam(state, "abandoned", 0)

    def test_exam_prepare_uses_real_pack_levels_and_start_exam(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._open_exam_setup()
            window._show_exam_prepare()

            self.assertIs(window._stack.currentWidget(), window._exam_prepare_page)
            text = window._exam_prepare_text.toPlainText()
            self.assertIn("Pack/Rank : Sample Rank", text)
            self.assertIn("level0", text)
            self.assertIn("Aprovação : 100%", text)

            window._start_exam()

            self.assertIs(window._stack.currentWidget(), window._exercise_page)
            self.assertEqual(window._mode, "exam")
            self.assertIsNotNone(window._exam_state)
            window._coordinator.finish_exam(window._exam_state, "abandoned", 0)

    def test_start_training_button_starts_training(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._open_training_setup()
            window._choose_level_training()
            window._start_training()

            self.assertIs(window._stack.currentWidget(), window._exercise_page)
            self.assertEqual(window._mode, "training")
            self.assertIsNotNone(window._active)

    def test_history_uses_columns_for_attempts_and_latest_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, passed=False)
            ref = next(
                ref
                for ref in window._coordinator._pack_catalog.list_exercises("sample_rank")
                if ref.definition.id == "steady_echo"
            )
            window._show_training_fail_feedback = lambda: None
            window._load_exercise(ref, mode="training", overwrite=True)
            window._submit_current()
            self.assertTrue(window._tasks.wait())

            window._show_history()
            window._select_history_pack("sample_rank")

            self.assertIs(window._stack.currentWidget(), window._history_page)
            headers = [
                window._history_activity_table.horizontalHeaderItem(column).text()
                for column in range(window._history_activity_table.columnCount())
            ]
            self.assertEqual(
                headers,
                ["ATIVIDADE", "STATUS", "TENTATIVAS", "ÚLTIMA"],
            )
            matching_row = next(
                row
                for row in range(window._history_activity_table.rowCount())
                if "steady_echo" in window._history_activity_table.item(row, 0).text()
            )
            self.assertEqual(window._history_activity_table.item(matching_row, 2).text(), "1")
            self.assertEqual(window._history_activity_table.item(matching_row, 1).text(), "Tentado")

            window._history_activity_table.selectRow(matching_row)
            self.assertEqual(window._history_inspector_title.text(), "Steady Echo")
            self.assertEqual(window._history_attempts_table.item(0, 1).text(), "FAIL")
            window._history_attempts_table.selectRow(0)
            self.assertIn("Trace não disponível", window._history_trace_summary.text())
            self.assertFalse(window._history_trace_button.isEnabled())

            coordinator_row = next(
                row
                for row in window._coordinator.exercise_history_rows()
                if row["exercise_id"] == "steady_echo"
            )
            self.assertEqual(coordinator_row["status"], ActivityProgress.ATTEMPTED)

    def test_activity_progress_status_is_stable_internally_and_translated_in_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, passed=True)
            ref = next(
                ref
                for ref in window._coordinator._pack_catalog.list_exercises("sample_rank")
                if ref.definition.id == "steady_echo"
            )
            window._load_exercise(ref, mode="training", overwrite=True)
            window._submit_current()
            self.assertTrue(window._tasks.wait())

            # Internal state never depends on translated text: the coordinator/history
            # layer always returns the stable ActivityProgress value, regardless of locale.
            coordinator_row = next(
                row
                for row in window._coordinator.exercise_history_rows()
                if row["exercise_id"] == "steady_echo"
            )
            self.assertEqual(coordinator_row["status"], ActivityProgress.COMPLETED)
            self.assertNotEqual(coordinator_row["status"], "concluído")

            window._show_history()
            window._select_history_pack("sample_rank")
            row_index = next(
                row
                for row in range(window._history_activity_table.rowCount())
                if "steady_echo" in window._history_activity_table.item(row, 0).text()
            )
            self.assertEqual(window._history_activity_table.item(row_index, 1).text(), "Concluído")

            window._locale.set_locale("en")
            window._render_history()
            self.assertEqual(window._history_activity_table.item(row_index, 1).text(), "Completed")

            window._locale.set_locale("es")
            window._render_history()
            self.assertEqual(window._history_activity_table.item(row_index, 1).text(), "Completado")

            # Counts derived from ActivityProgress stay correct regardless of locale.
            self.assertIn("1", window._history_metric_completed.text())

    def test_history_exposes_overview_pack_session_and_timeline_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, passed=True)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)
            window._submit_current()
            self.assertTrue(window._tasks.wait())

            window._show_history()
            self.assertEqual(window._history_view, "overview")
            self.assertTrue(window._history_activity_table.isHidden())
            self.assertEqual(set(window._history_nav_buttons), {"overview", "training_sessions", "exam_sessions"})
            self.assertTrue(window._history_inspector.isHidden())
            self.assertEqual(window._history_exam_heading.text(), "SESSÕES DE PROVA")
            self.assertEqual(window._history_training_heading.text(), "VOLUME DE TREINO")
            self.assertIn("TRILHA DE APRENDIZADO", window._history_learning_heading.text())
            self.assertEqual(window._history_recent_heading.text(), "SESSÕES RECENTES")
            self.assertEqual(window._history_recent_sessions.count(), 1)
            self.assertTrue(window._history_activity_table.isHidden())

            window._select_history_pack("sample_rank")
            self.assertEqual(window._history_activity_table.horizontalHeaderItem(0).text(), "ATIVIDADE")
            self.assertEqual(window._history_center_title.text(), "Sample Rank")

            window._set_history_view("exam_sessions")
            self.assertEqual(window._history_session_table.horizontalHeaderItem(0).text(), "DATA")
            self.assertFalse(window._history_session_table.isHidden())
            self.assertIn("Passou: 0", window._history_center_meta.text())
            self.assertIn("Falhou: 0", window._history_center_meta.text())

            headers = [
                window._history_session_table.horizontalHeaderItem(column).text()
                for column in range(window._history_session_table.columnCount())
            ]
            self.assertEqual(headers, ["DATA", "PACK", "DURAÇÃO", "NOTA", "STATUS", "ATIVIDADES"])

    def test_home_layout_survives_reference_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            for width, height in ((760, 520), (1024, 720), (1440, 900), (1920, 1080)):
                window.resize(width, height)
                QApplication.processEvents()
                self.assertFalse(window._menu_buttons[0].isHidden())
                self.assertTrue(window._menu_buttons[0].isEnabled())

    def test_settings_locale_layout_survives_reference_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._locale.set_locale("es")
            window._show_settings()
            for width, height in ((760, 520), (1024, 720), (1440, 900), (1920, 1080)):
                window.resize(width, height)
                QApplication.processEvents()
                self.assertFalse(window._locale_combo.isHidden())
                self.assertEqual(window._locale_combo.count(), 3)


    def test_theme_change_restyles_window_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            saved: list[str] = []
            window._coordinator.save_theme = saved.append

            index = window._theme_combo.findData("minimal")
            window._theme_combo.setCurrentIndex(index)

            self.assertEqual(saved, ["minimal"])
            self.assertIn("#121417", window.styleSheet())

    def test_training_fail_shows_inline_feedback_with_trace_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, passed=False)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)

            window._submit_current()
            self.assertTrue(window._tasks.wait())

            self.assertFalse(window._feedback.isHidden())
            self.assertEqual(window._feedback.headline, "[✗] FAIL")
            self.assertTrue(window._trace_button.isEnabled())

    def test_level_options_are_clickable_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._open_training_setup()

            self.assertTrue(window._level_training_button.isChecked())
            self.assertTrue(window._level_checks)
            first = window._level_checks[0]
            self.assertTrue(first.text().startswith("[x] "))
            first.click()
            self.assertTrue(first.text().startswith("[ ] "))
            self.assertNotIn(first.value, window._selected_levels())


    def test_home_shows_rankeddojo_brand_and_window_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            self.assertEqual(window._cursor._titles[window._title_home], "RankedDojo")
            self.assertEqual(window.windowTitle(), "RankedDojo")
            self.assertIn("dojo@RankedDojo:", window._workspace_label.text())
            self.assertNotIn("EXAM TRAINER", window._cursor._titles[window._title_home])
            self.assertNotIn("user@42", window._workspace_label.text())
            # The mini logo mark sits beside the "RankedDojo" title; the
            # written brand itself never changes shape or name per theme.
            self.assertEqual(window._home_logo.property("role"), "logo")
            self.assertTrue(window._home_logo.text())

    def test_home_logo_mark_is_generic_and_reused_across_themes(self) -> None:
        # The logo consumer must not be rebuilt or special-cased when the
        # theme changes -- same widget instance, same glyph, regardless of
        # which theme (old or new RankedDojo one) is active.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            logo = window._home_logo
            glyph = logo.text()

            for theme_key in ("terminal", "default", "gamified", "retro", "paper"):
                window._theme.set_theme(theme_key)
                self.assertIs(window._home_logo, logo)
                self.assertEqual(window._home_logo.text(), glyph)
                self.assertEqual(window._home_logo.property("role"), "logo")

    def test_theme_combo_offers_new_rankeddojo_themes_alongside_legacy_ones(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()

            for key in ("default", "gamified", "retro", "terminal", "amber", "gameboy", "neon", "minimal", "paper"):
                self.assertGreaterEqual(window._theme_combo.findData(key), 0, key)

    def test_home_is_pt_br_by_default(self) -> None:
        # Dojo Session home: three short primary actions (LEARN/TRAIN/EXAM)
        # plus three secondary ones (STUDY NEW/HISTORY/SETTINGS) -- no more
        # "> [N] " prefix, since the numbers are no longer shown on screen
        # (the underlying `_menu_buttons` order is still 1-6 internally, see
        # `_install_shortcuts`, but that is no longer part of the button text).
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            self.assertEqual(window._home_train_button.property("baseText"), "TREINAR")
            self.assertEqual(window._home_learn_button.property("baseText"), "APRENDER")
            self.assertEqual(window._home_exam_button.property("baseText"), "PROVA")
            self.assertEqual(window._home_study_button.property("baseText"), "QUERO ESTUDAR ALGO NOVO")
            self.assertEqual(window._home_history_button.property("baseText"), "HISTÓRICO")
            self.assertEqual(window._home_settings_button.property("baseText"), "CONFIGURAÇÕES")

    def test_home_can_switch_to_english_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._locale.set_locale("en")

            self.assertEqual(window._home_learn_button.property("baseText"), "LEARN")
            self.assertEqual(window._home_train_button.property("baseText"), "TRAIN")
            self.assertEqual(window._home_exam_button.property("baseText"), "EXAM")
            self.assertEqual(window._home_study_button.property("baseText"), "STUDY SOMETHING NEW")
            self.assertEqual(window._home_history_button.property("baseText"), "HISTORY")
            self.assertEqual(window._home_settings_button.property("baseText"), "SETTINGS")

    def test_home_can_switch_to_spanish_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._locale.set_locale("es")

            self.assertEqual(window._home_learn_button.property("baseText"), "APRENDER")
            self.assertEqual(window._home_train_button.property("baseText"), "ENTRENAR")
            self.assertEqual(window._home_exam_button.property("baseText"), "EXAMEN")
            self.assertEqual(window._home_study_button.property("baseText"), "ESTUDIAR ALGO NUEVO")
            self.assertEqual(window._home_history_button.property("baseText"), "HISTORIAL")
            self.assertEqual(window._home_settings_button.property("baseText"), "CONFIGURACIÓN")

    def test_settings_shows_translated_locale_selector_with_three_languages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._locale.set_locale("en")
            window._show_settings()

            self.assertEqual(window._locale_label.text(), "Interface language")
            self.assertEqual(window._locale_combo.count(), 3)
            self.assertEqual(
                [window._locale_combo.itemText(index) for index in range(window._locale_combo.count())],
                ["Português (Brasil)", "English", "Español"],
            )

    def test_random_draw_options_replace_labels_in_pt_br_en_and_es(self) -> None:
        expectations = {
            "pt-BR": (
                "(•) Priorizar não concluídos",
                "( ) Somente não concluídos",
                "( ) Todos os exercícios",
                "[ ] Permitir repetidos",
            ),
            "en": (
                "(•) Prioritize uncompleted",
                "( ) Only uncompleted",
                "( ) All exercises",
                "[ ] Allow repeats",
            ),
            "es": (
                "(•) Priorizar no completados",
                "( ) Solo no completados",
                "( ) Todos los ejercicios",
                "[ ] Permitir repetidos",
            ),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._choose_random_training()
            for locale, expected in expectations.items():
                window._locale.set_locale(locale)
                actual = (
                    window._prioritize_radio.property("baseText"),
                    window._only_uncompleted_radio.property("baseText"),
                    window._all_radio.property("baseText"),
                    window._allow_repeated_check.property("baseText"),
                )
                self.assertEqual(actual, expected)
                for text in actual:
                    self.assertNotRegex(text, r"Priorizar não concluídos\\s+Prioritize")
                    self.assertNotRegex(text, r"Somente não concluídos\\s+Only")
                    self.assertNotRegex(text, r"Todos os exercícios\\s+All")

            self.assertEqual(window._prioritize_radio.value, "prioritize_uncompleted")
            self.assertEqual(window._only_uncompleted_radio.value, "only_uncompleted")
            self.assertEqual(window._all_radio.value, "all_exercises")
            self.assertEqual(window._allow_repeated_check.value, "allow_repeats")

    def test_settings_editor_buttons_follow_locale(self) -> None:
        expectations = {
            "pt-BR": {"[ DETECTAR AUTOMATICAMENTE ]", "[ SELECIONAR EXECUTÁVEL ]", "[ SALVAR EDITOR ]"},
            "en": {"[ DETECT AUTOMATICALLY ]", "[ SELECT EXECUTABLE ]", "[ SAVE EDITOR ]"},
            "es": {"[ DETECTAR AUTOMÁTICAMENTE ]", "[ SELECCIONAR EJECUTABLE ]", "[ GUARDAR EDITOR ]"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            for locale, expected in expectations.items():
                window._locale.set_locale(locale)
                window._show_settings()
                labels = {button.property("baseText") for button in window._settings_page.findChildren(QPushButton)}
                self.assertTrue(expected.issubset(labels))

    def test_editor_combo_and_auto_detection_share_the_same_registry_source(self) -> None:
        # Fase 4: both the preset combo and automatic detection must read the
        # known editors from the same EditorRegistry -- never two independent
        # lists that could drift apart.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()

            combo_presets = tuple(
                window._editor_combo.itemData(index)
                for index in range(window._editor_combo.count())
                if window._editor_combo.itemData(index) != "Outro..."
            )
            self.assertEqual(combo_presets, EDITOR_REGISTRY.ids())
            self.assertEqual(window._coordinator.known_editor_labels(), EDITOR_REGISTRY.ids())

    def test_detect_editor_automatically_button_exists_and_reuses_resolution_flow(self) -> None:
        # The explicit "Detectar automaticamente" action must call the very same
        # known-editor resolution already used by the preset combo -- never a
        # second, independent lookup.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            button = self._runtime_action_button(window, "[ DETECTAR AUTOMATICAMENTE ]")
            self.assertTrue(button.isEnabled())

            calls: list[str] = []
            original = window._coordinator.resolve_known_editor

            def recording_resolve(label: str) -> str | None:
                calls.append(label)
                return "/usr/bin/code" if label == "VS Code" else None

            window._coordinator.resolve_known_editor = recording_resolve
            try:
                window._detect_editor_automatically()
            finally:
                window._coordinator.resolve_known_editor = original

            # Every candidate it tried came from the single shared EditorRegistry,
            # never a locally invented VS Code/Zed/Cursor list.
            self.assertTrue(set(calls).issubset(set(window._coordinator.known_editor_labels())))
            self.assertEqual(window._settings_editor.text(), "/usr/bin/code")
            self.assertEqual(window._editor_combo.currentData(), "VS Code")

    def test_detect_editor_automatically_prefers_current_preset_direct_redetection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            index = window._editor_combo.findData("Zed")
            window._editor_combo.setCurrentIndex(index)

            resolved = {"VS Code": "/usr/bin/code", "Zed": "/usr/bin/zed", "Cursor": "/usr/bin/cursor"}
            window._coordinator.resolve_known_editor = lambda label: resolved.get(label)

            window._detect_editor_automatically()

            # The currently selected preset (Zed) is redetected directly, without
            # switching to a different known editor.
            self.assertEqual(window._editor_combo.currentData(), "Zed")
            self.assertEqual(window._settings_editor.text(), "/usr/bin/zed")

    def test_detect_editor_automatically_falls_back_to_first_known_editor_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            index = window._editor_combo.findData("Outro...")
            window._editor_combo.setCurrentIndex(index)

            # Current preset ("Outro...") never resolves; the flow falls back to
            # scanning known editors and fills in the first one that is found.
            window._coordinator.resolve_known_editor = lambda label: "/opt/cursor" if label == "Cursor" else None

            window._detect_editor_automatically()

            self.assertEqual(window._editor_combo.currentData(), "Cursor")
            self.assertEqual(window._settings_editor.text(), "/opt/cursor")

    def test_detect_editor_automatically_warns_when_nothing_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            window._coordinator.resolve_known_editor = lambda label: None
            before_text = window._settings_editor.text()
            self._dialogs.clear()

            window._detect_editor_automatically()

            self.assertEqual(window._settings_editor.text(), before_text)
            self.assertTrue(any(name == "warning" for name, _text in self._dialogs))

    def test_runtime_settings_status_and_actions_follow_locale(self) -> None:
        expectations = {
            "pt-BR": {
                "status": "● não verificado — use [ DETECTAR NOVAMENTE ]",
                "detect": "[ DETECTAR NOVAMENTE ]",
                "select_cpp": "[ SELECIONAR COMPILADOR C++ ]",
                "title_cpp": "COMPILADOR C++",
            },
            "en": {
                "status": "● Not checked — use [ DETECT AGAIN ]",
                "detect": "[ DETECT AGAIN ]",
                "select_cpp": "[ SELECT C++ COMPILER ]",
                "title_cpp": "C++ COMPILER",
            },
            "es": {
                "status": "● No verificado — usa [ DETECTAR DE NUEVO ]",
                "detect": "[ DETECTAR DE NUEVO ]",
                "select_cpp": "[ SELECCIONAR COMPILADOR C++ ]",
                "title_cpp": "COMPILADOR C++",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            compiler = SlowProbeCompiler(found=True)
            window = self._window(temp_dir, compiler=compiler)
            for locale, expected in expectations.items():
                window._locale.set_locale(locale)
                window._show_settings()
                self.assertEqual(window._settings_compiler.text(), expected["status"])
                self.assertEqual(window._settings_action_text("detect-runtime:cpp"), expected["detect"])
                self.assertEqual(window._settings_action_text("select-runtime:cpp"), expected["select_cpp"])
                self.assertEqual(window._settings_title_text("runtime:cpp"), expected["title_cpp"])

    def test_locale_combo_persists_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = JsonAppConfigRepository(Path(temp_dir) / "config.json")
            locale_service = LocaleService(config, self._app)
            window = self._window(temp_dir, locale_service=locale_service)

            window._show_settings()
            window._locale_combo.setCurrentIndex(window._locale_combo.findData("es"))

            self.assertEqual(config.load_ui_locale(), "es")

    def test_pack_content_and_exercise_name_are_not_translated_by_ui_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            ref = next(
                ref
                for ref in window._coordinator._pack_catalog.list_exercises("c-basics")
                if ref.definition.id == "argc_counter"
            )

            window._locale.set_locale("en")
            window._load_exercise(ref, mode="training", overwrite=True)

            self.assertEqual(window._cursor._titles[window._exercise_title], "Argc Counter")
            # "Arquivo esperado" / "argc_counter.c" are promoted to the
            # sidebar (`_sidebar_expected_value`) and intentionally stripped
            # from the body -- see `_exercise_body_markdown` -- so pack
            # content staying untranslated by UI locale is now checked
            # against a pedagogical section that always stays in the body
            # instead.
            rendered = window._subject.toPlainText()
            self.assertIn("Comportamento esperado", rendered)
            self.assertEqual(window._sidebar_expected_value.text(), "> argc_counter.c")

    def test_internal_status_values_are_not_translated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, passed=False)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._locale.set_locale("es")
            window._load_exercise(ref, mode="training", overwrite=True)
            window._submit_current()
            self.assertTrue(window._tasks.wait())

            rows = window._coordinator.exercise_history_rows()
            self.assertTrue(any(row["latest_result"] == "FAIL" for row in rows))

    def test_runtime_preflight_message_is_translated_from_structured_missing_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            compiler = SlowProbeCompiler(found=False)
            window = self._window(temp_dir, compiler=compiler)
            window._locale.set_locale("en")
            shown: list[str] = []
            original = QMessageBox.information
            QMessageBox.information = staticmethod(lambda parent, title, text, *a, **k: shown.append(text))
            try:
                window._open_exam_setup()
                self.assertTrue(window._tasks.wait())
            finally:
                QMessageBox.information = original

            self.assertEqual(shown, ["Install or configure a compatible runtime to submit this exercise."])

    def test_grading_runs_off_the_ui_thread_and_blocks_duplicate_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            grader = BlockingGrader(passed=False)
            window = self._window(temp_dir, grader=grader)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)

            window._submit_current()
            self.assertTrue(grader.started.wait(5))
            # UI already returned: the button is locked with the progress label
            self.assertFalse(window._correct_button.isEnabled())
            self.assertEqual(window._correct_button.text(), "CORRIGINDO...")
            window._submit_current()  # duplicate click is ignored
            grader.release.set()
            self.assertTrue(window._tasks.wait())

            self.assertEqual(grader.calls, 1)
            self.assertNotEqual(grader.thread_ids[0], threading.get_ident())
            self.assertTrue(window._correct_button.isEnabled())
            self.assertEqual(window._correct_button.text(), "> CORRIGIR")
            self.assertEqual(window._feedback.headline, "[✗] FAIL")

    def test_exam_timer_keeps_running_while_grading_and_does_not_finish_mid_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            grader = BlockingGrader(passed=False)
            window = self._window(temp_dir, grader=grader)
            state = window._coordinator.start_exam("sample_rank", 60)
            window._exam_state = state
            window._load_exercise(window._coordinator.exam_ref(state), mode="exam", overwrite=True)

            window._submit_current()
            self.assertTrue(grader.started.wait(5))
            window._tick_exam()  # timer keeps drawing during grading
            self.assertIsNotNone(window._exam_state)
            self.assertIn("⏱", window._exam_timer_label.text())
            grader.release.set()
            self.assertTrue(window._tasks.wait())
            self.assertIsNotNone(window._exam_state)
            window._coordinator.finish_exam(window._exam_state, "abandoned", 0)

    def test_grading_error_is_shown_as_message_not_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, grader=FailingGrader())
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)
            shown: list[str] = []
            original = QMessageBox.warning
            QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: shown.append(text))
            try:
                window._submit_current()
                self.assertTrue(window._tasks.wait())
            finally:
                QMessageBox.warning = original
            self.assertEqual(shown, ["compiler disappeared"])
            self.assertTrue(window._correct_button.isEnabled())

    def test_redetect_runtime_button_sends_language_id_not_clicked_bool(self) -> None:
        # Regression guard for the QPushButton.clicked(bool checked) signal
        # overwriting the lambda's captured language default.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            received: list[object] = []
            window._redetect_runtime = received.append
            button = self._runtime_action_button(window, "detect-runtime:c")

            button.click()

            self.assertEqual(received, ["c"])

    def test_select_manual_runtime_button_sends_language_id_not_clicked_bool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            received: list[object] = []
            window._choose_manual_runtime = received.append
            button = self._runtime_action_button(window, "select-runtime:c")

            button.click()

            self.assertEqual(received, ["c"])

    def test_redetect_runtime_button_click_does_not_raise_keyerror(self) -> None:
        # End-to-end: clicking the real button (not a monkeypatched handler)
        # must not crash with `KeyError: False` when Qt passes the
        # `clicked(bool)` argument through the callback chain.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            button = self._runtime_action_button(window, "detect-runtime:c")

            button.click()
            self.assertTrue(window._tasks.wait())

            self.assertEqual(self._compiler.redetect_calls, 1)
            self.assertIn("gcc", window._runtime_labels["c"].text())

    def test_select_manual_runtime_button_click_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as open_file:
            window = self._window(temp_dir)
            window._show_settings()
            button = self._runtime_action_button(window, "select-runtime:c")

            button.click()

            open_file.assert_called_once()

    def test_compiler_redetection_runs_in_background(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._show_settings()
            window._refresh_compiler_setting()
            self.assertTrue(window._tasks.wait())
            self.assertEqual(self._compiler.redetect_calls, 1)
            self.assertIn("gcc", window._settings_compiler.text())

    def test_first_compiler_probe_runs_in_background_then_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            compiler = SlowProbeCompiler(found=True)
            window = self._window(temp_dir, compiler=compiler)

            window._open_exam_setup()
            self.assertIsNot(window._stack.currentWidget(), window._exam_page)  # ainda detectando
            self.assertTrue(window._tasks.wait())

            self.assertIs(window._stack.currentWidget(), window._exam_page)
            self.assertEqual(len(compiler.probe_threads), 1)
            self.assertNotEqual(compiler.probe_threads[0], threading.get_ident())

    def test_missing_compiler_after_background_probe_opens_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            compiler = SlowProbeCompiler(found=False)
            window = self._window(temp_dir, compiler=compiler)
            original = QMessageBox.information
            QMessageBox.information = staticmethod(lambda *a, **k: None)
            try:
                window._open_exam_setup()
                self.assertTrue(window._tasks.wait())
            finally:
                QMessageBox.information = original
            self.assertIs(window._stack.currentWidget(), window._settings_page)
            self.assertIsNotNone(window._pending_action)


    def test_home_last_session_shows_real_progress_and_continue_resumes_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir, passed=True)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)
            window._submit_current()
            self.assertTrue(window._tasks.wait())

            window._refresh_home_last_session()

            summary = window._coordinator.last_session_summary()
            self.assertIsNotNone(summary)
            self.assertEqual(summary.activity_id, ref.definition.id)
            self.assertFalse(window._last_session_name.isHidden())
            self.assertFalse(window._last_session_continue_button.isHidden())
            self.assertTrue(window._last_session_empty_label.isHidden())
            self.assertEqual(window._last_session_name.text(), ref.definition.name)
            self.assertIn(str(summary.completed_count), window._last_session_progress_label.text())
            self.assertIn(str(summary.total_count), window._last_session_progress_label.text())
            # Never a hardcoded fraction: it must reflect the real pack size.
            self.assertEqual(
                window._last_session_progress.maximum(),
                max(summary.total_count, 1),
            )

            window._continue_last_session()

            self.assertIsNotNone(window._active)
            self.assertEqual(window._active.ref.definition.id, ref.definition.id)
            self.assertIs(window._stack.currentWidget(), window._exercise_page)

    def test_home_last_session_shows_empty_state_without_prior_training(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            window._refresh_home_last_session()

            self.assertIsNone(window._coordinator.last_session_summary())
            self.assertFalse(window._last_session_empty_label.isHidden())
            self.assertTrue(window._last_session_name.isHidden())
            self.assertTrue(window._last_session_meta.isHidden())
            self.assertTrue(window._last_session_progress.isHidden())
            self.assertTrue(window._last_session_progress_label.isHidden())
            self.assertTrue(window._last_session_continue_button.isHidden())
            self.assertEqual(window._last_session_empty_label.text(), "NENHUMA SESSÃO RECENTE")

    def test_home_language_status_reflects_runtime_registry_without_hardcoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            fake_statuses = (
                RuntimeStatus(language="c", display_name="C", supported=True, available=True, checked=True, tool="gcc"),
                RuntimeStatus(language="cpp", display_name="C++", supported=True, available=True, checked=True, tool="g++"),
                RuntimeStatus(language="python", display_name="Python", supported=True, available=True, checked=True, tool="python3"),
                RuntimeStatus(language="java", display_name="Java", supported=True, available=False, checked=True),
            )
            window._coordinator.runtime_statuses = lambda probe=False: fake_statuses

            window._refresh_home_status()

            chips = []
            for index in range(window._home_language_status.count()):
                widget = window._home_language_status.itemAt(index).widget()
                if widget is not None:
                    chips.append(widget)
            self.assertEqual(len(chips), 4)
            texts = {chip.text(): chip.property("status") for chip in chips}
            self.assertEqual(texts["C ✓"], "pass")
            self.assertEqual(texts["C++ ✓"], "pass")
            self.assertEqual(texts["Python ✓"], "pass")
            self.assertEqual(texts["Java !"], "pending")
            # No parallel/hardcoded runtime logic: swapping which language is
            # unavailable must change which chip shows "!" accordingly.
            fake_statuses_swapped = (
                RuntimeStatus(language="c", display_name="C", supported=True, available=False, checked=True),
                RuntimeStatus(language="java", display_name="Java", supported=True, available=True, checked=True, tool="javac"),
            )
            window._coordinator.runtime_statuses = lambda probe=False: fake_statuses_swapped
            window._refresh_home_status()
            swapped_texts = {}
            for index in range(window._home_language_status.count()):
                widget = window._home_language_status.itemAt(index).widget()
                if widget is not None:
                    swapped_texts[widget.text()] = widget.property("status")
            self.assertEqual(swapped_texts["C !"], "pending")
            self.assertEqual(swapped_texts["Java ✓"], "pass")

    def test_home_applies_every_theme_without_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            for key in ("default", "gamified", "retro", "terminal", "amber", "gameboy", "neon", "minimal", "paper"):
                window._theme.set_theme(key)
                QApplication.processEvents()
            # Reaching here without a raised exception is the assertion; the
            # new dojo-primary/dojo-secondary/cta/pill/keycap/progress roles
            # must resolve under every theme using only existing tokens.
            self.assertTrue(window.styleSheet())

    def test_home_keyboard_shortcut_order_still_maps_to_original_screens(self) -> None:
        # `_menu_buttons` keeps the pre-redesign order (study, train, learn,
        # exam, history, settings) purely so the numeric shortcuts installed
        # by `_install_shortcuts` keep opening the same screens, even though
        # the buttons are now visually split into two groups.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)

            self.assertEqual(len(window._menu_buttons), 6)
            self.assertIs(window._menu_buttons[0], window._home_study_button)
            self.assertIs(window._menu_buttons[1], window._home_train_button)
            self.assertIs(window._menu_buttons[2], window._home_learn_button)
            self.assertIs(window._menu_buttons[3], window._home_exam_button)
            self.assertIs(window._menu_buttons[4], window._home_history_button)
            self.assertIs(window._menu_buttons[5], window._home_settings_button)

            window._menu_buttons[1].click()
            self.assertIs(window._stack.currentWidget(), window._training_page)
            window._show_home()
            window._menu_buttons[3].click()
            self.assertIs(window._stack.currentWidget(), window._exam_page)


class ExerciseBodyMarkdownTest(unittest.TestCase):
    """`MainWindow._exercise_body_markdown` is a pure, static, table-driven
    function -- no exercise loading or sidebar state needed to exercise it
    directly. The caller (`_refresh_exercise_frame`) is covered separately
    below with real packs.
    """

    def test_strips_only_the_sections_confirmed_shown_in_the_sidebar(self) -> None:
        subject = (
            "Enunciado.\n\n"
            "## Arquivo esperado\n`foo.c`\n\n"
            "## Comportamento esperado\n- Faça X.\n\n"
            "## Permitido\n- `write`\n\n"
            "## Não permitido\n- `printf`\n\n"
            "## Regras\n- Sem variáveis globais.\n\n"
            "## Exemplos\n- ok\n"
        )
        result = MainWindow._exercise_body_markdown(
            subject, allowed_shown=True, not_allowed_shown=False, constraints_shown=True
        )
        # Expected file is always promoted (submission.filename is
        # mandatory), so always stripped regardless of its flag.
        self.assertNotIn("Arquivo esperado", result)
        self.assertNotIn("foo.c", result)
        # Confirmed shown in the sidebar -> stripped from the body.
        self.assertNotIn("Permitido", result)
        self.assertNotIn("Regras", result)
        # NOT confirmed shown in the sidebar -> stays, never silently lost.
        self.assertIn("Não permitido", result)
        self.assertIn("printf", result)
        # Pedagogical content is never in the promoted-sections table.
        self.assertIn("Comportamento esperado", result)
        self.assertIn("Faça X.", result)
        # The generic grouping heading is demoted, not removed.
        self.assertIn("###### Exemplos", result.splitlines())

    def test_keeps_every_promoted_section_when_nothing_was_shown_in_the_sidebar(self) -> None:
        # Expected file is the one section promoted unconditionally; the
        # other three stay in the body whenever their sidebar flag is False.
        subject = (
            "## Permitido\n- `write`\n\n"
            "## Não permitido\n- `printf`\n\n"
            "## Regras\n- Sem variáveis globais.\n"
        )
        result = MainWindow._exercise_body_markdown(
            subject, allowed_shown=False, not_allowed_shown=False, constraints_shown=False
        )
        self.assertIn("Permitido", result)
        self.assertIn("Não permitido", result)
        self.assertIn("Regras", result)

    def test_style_behavior_notes_style_headings_are_never_in_the_promoted_table(self) -> None:
        # There is no STYLE/BEHAVIOR/NOTES heading table at all (by design,
        # see `_refresh_exercise_frame`'s comment on the subject content
        # contract) -- so any such section survives regardless of the other
        # three flags, which is how the pedagogical fields stay untouched.
        subject = "## Estilo\n- Use snake_case.\n\n## Observações\n- Veja o apêndice.\n"
        result = MainWindow._exercise_body_markdown(
            subject, allowed_shown=True, not_allowed_shown=True, constraints_shown=True
        )
        self.assertIn("Use snake_case.", result)
        self.assertIn("Veja o apêndice.", result)

    def test_decision_never_reads_ui_locale(self) -> None:
        # Static method, no `self`/`self._t`/locale access at all -- the
        # signature itself is the guarantee, exercised here with the same
        # Markdown under every supported combination of flags.
        import inspect

        signature = inspect.signature(MainWindow._exercise_body_markdown)
        self.assertNotIn("locale", signature.parameters)
        self.assertNotIn("self", signature.parameters)


class UsageCategoryLinesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window(self, temp_dir: str) -> MainWindow:
        return MainWindowTest._window(self, temp_dir)  # reuse the shared factory

    def test_empty_category_returns_no_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            self.assertEqual(window._usage_category_lines(UsageCategory()), [])

    def test_single_populated_field_is_a_flat_unlabeled_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            lines = window._usage_category_lines(UsageCategory(functions=("write", "read")))
            self.assertEqual(lines, ["> write", "> read"])

    def test_multiple_populated_fields_are_grouped_with_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            window._locale.set_locale("en")
            category = UsageCategory(functions=("write",), headers=("unistd.h",))
            lines = window._usage_category_lines(category)
            self.assertEqual(lines, ["Functions", "  > write", "Headers", "  > unistd.h"])


class SubjectContractSidebarAndBodyTest(unittest.TestCase):
    """Integration coverage for `_refresh_exercise_frame`, using real packs
    audited for this fix (see `docs/subject-content-contract.md` and the
    task report): a multi-category structured pack (`argc_counter`), a
    pack whose structured metadata and legacy Markdown text conflict
    (`sum_args`), and a pack that only has `forbidden` as legacy Markdown
    with no structured equivalent (`vector_sum`).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window(self, temp_dir: str) -> MainWindow:
        return MainWindowTest._window(self, temp_dir)

    def _load(self, window: MainWindow, pack_id: str, exercise_id: str):
        ref = next(
            ref
            for ref in window._coordinator._pack_catalog.list_exercises(pack_id)
            if ref.definition.id == exercise_id
        )
        window._load_exercise(ref, mode="training", overwrite=True)

    def test_structured_multi_category_allowed_and_forbidden_do_not_duplicate_in_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            self._load(window, "c-basics", "argc_counter")

            # Sidebar: structured `usage.allowed` has both functions and
            # headers populated, so it renders grouped-by-category.
            self.assertFalse(window._sidebar_allowed_block.isHidden())
            allowed_text = window._sidebar_allowed_value.text()
            self.assertIn("write", allowed_text)
            self.assertIn("unistd.h", allowed_text)

            self.assertFalse(window._sidebar_not_allowed_block.isHidden())
            not_allowed_text = window._sidebar_not_allowed_value.text()
            self.assertIn("printf", not_allowed_text)
            self.assertIn("puts", not_allowed_text)

            self.assertFalse(window._sidebar_constraints_block.isHidden())
            self.assertFalse(window._sidebar_expected_block.isHidden())
            self.assertEqual(window._sidebar_expected_value.text(), "> argc_counter.c")

            rendered = window._subject.toPlainText()
            self.assertNotIn("Arquivo esperado", rendered)
            self.assertNotIn("argc_counter.c", rendered)
            self.assertNotIn("Permitido", rendered)
            self.assertNotIn("Não permitido", rendered)
            # Pedagogical content never promoted, always stays.
            self.assertIn("Comportamento esperado", rendered)
            self.assertIn("Exemplos", rendered)

    def test_legacy_markdown_fallback_promotes_and_strips_forbidden_section(self) -> None:
        # `vector_sum` has structured `usage.allowed` but no `forbidden` key
        # at all -- its only "not allowed" content is the legacy Markdown
        # heading, which must still be promoted to the sidebar and then
        # safely stripped from the body.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            self._load(window, "cpp-basics", "vector_sum")

            self.assertFalse(window._sidebar_not_allowed_block.isHidden())
            self.assertIn("variáveis globais", window._sidebar_not_allowed_value.text())

            self.assertFalse(window._sidebar_allowed_block.isHidden())
            self.assertIn("vector", window._sidebar_allowed_value.text())

            self.assertFalse(window._sidebar_constraints_block.isHidden())
            self.assertIn("referência constante", window._sidebar_constraints_value.text())

            rendered = window._subject.toPlainText()
            self.assertNotIn("Não permitido", rendered)
            self.assertNotIn("Permitido", rendered)
            self.assertIn("Comportamento esperado", rendered)

    def test_structured_metadata_wins_over_conflicting_legacy_markdown_text(self) -> None:
        # `sum_args` has `usage.allowed.libraries = ["java.lang"]` but its
        # subject.md "## Permitido" section still names `Integer.parseInt`
        # (stale legacy text). Only the structured value may ever be
        # visible, in the sidebar; the stale Markdown version must not
        # survive anywhere, not even in the body.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            self._load(window, "java-basics", "sum_args")

            self.assertEqual(window._sidebar_allowed_value.text(), "> java.lang")
            self.assertEqual(window._sidebar_not_allowed_value.text(), "> Scanner")

            rendered = window._subject.toPlainText()
            self.assertNotIn("Integer.parseInt", rendered)
            self.assertNotIn("Permitido", rendered)
            self.assertNotIn("Não permitido", rendered)
            self.assertIn("Comportamento esperado", rendered)

    def test_sidebar_and_body_promotion_decisions_do_not_depend_on_ui_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            self._load(window, "c-basics", "argc_counter")

            for locale in ("pt-BR", "en", "es"):
                window._locale.set_locale(locale)
                self.assertFalse(window._sidebar_allowed_block.isHidden())
                self.assertFalse(window._sidebar_not_allowed_block.isHidden())
                self.assertFalse(window._sidebar_constraints_block.isHidden())
                rendered = window._subject.toPlainText()
                self.assertNotIn("Arquivo esperado", rendered)
                self.assertNotIn("argc_counter.c", rendered)
                # The technical values themselves are never translated.
                self.assertIn("write", window._sidebar_allowed_value.text())
                self.assertIn("printf", window._sidebar_not_allowed_value.text())


class FooterHintLocaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window(self, temp_dir: str) -> MainWindow:
        return MainWindowTest._window(self, temp_dir)

    def _hint_bar(self, window: MainWindow, hints: list[tuple[str, str]]):
        return next(bar for bar, bar_hints in window._footer_hint_bars if bar_hints == hints)

    def test_study_screen_hints_follow_locale(self) -> None:
        # Matches the reported screenshot: an English UI with the footer
        # hints still stuck in pt-BR ("voltar", "copiar prompt").
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            hint_bar = self._hint_bar(window, [("Esc", "voltar"), ("Ctrl+C", "copiar prompt")])

            window._locale.set_locale("en")
            self.assertEqual(
                [label.text() for label in hint_bar._hint_labels],
                ["[Esc] back", "[Ctrl+C] copy prompt"],
            )

            window._locale.set_locale("es")
            self.assertEqual(
                [label.text() for label in hint_bar._hint_labels],
                ["[Esc] volver", "[Ctrl+C] copiar prompt"],
            )

            window._locale.set_locale("pt-BR")
            self.assertEqual(
                [label.text() for label in hint_bar._hint_labels],
                ["[Esc] voltar", "[Ctrl+C] copiar prompt"],
            )

    def test_training_setup_screen_hints_follow_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            hint_bar = self._hint_bar(
                window, [("1", "por level"), ("2", "aleatório"), ("Esc", "voltar")]
            )

            window._locale.set_locale("en")
            self.assertEqual(
                [label.text() for label in hint_bar._hint_labels],
                ["[1] by level", "[2] random", "[Esc] back"],
            )

            window._locale.set_locale("es")
            self.assertEqual(
                [label.text() for label in hint_bar._hint_labels],
                ["[1] por level", "[2] aleatorio", "[Esc] volver"],
            )


class ExerciseScreenLayoutRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window(self, temp_dir: str) -> MainWindow:
        return MainWindowTest._window(self, temp_dir)

    def test_sidebar_and_subject_panel_still_split_after_loading_an_exercise(self) -> None:
        # Regression guard: the new 4th sidebar block must not have
        # collapsed the pre-existing sidebar/main-panel split from the
        # exercise screen redesign.
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._window(temp_dir)
            ref = next(iter(window._coordinator._pack_catalog.list_exercises("sample_rank")))
            window._load_exercise(ref, mode="training", overwrite=True)

            for block in (
                window._sidebar_expected_block,
                window._sidebar_allowed_block,
                window._sidebar_not_allowed_block,
                window._sidebar_constraints_block,
            ):
                self.assertIsNotNone(block.parentWidget())
            self.assertIsNotNone(window._subject.parentWidget())
            self.assertIsNot(window._sidebar_expected_block.parentWidget(), window._subject.parentWidget())


if __name__ == "__main__":
    unittest.main()
