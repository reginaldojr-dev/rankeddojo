from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from rankeddojo.adapters.compiler.system_c_compiler import SystemCCompiler
from rankeddojo.adapters.editor.subprocess_editor import SubprocessEditor, SubprocessEditorFactory
from rankeddojo.adapters.grader.generic_c_grader import GenericCGrader
from rankeddojo.adapters.pack.local_pack_catalog import LocalPackCatalog
from rankeddojo.adapters.pack.local_pack_importer import LocalPackImporter
from rankeddojo.adapters.persistence.sqlite_progress_repository import SQLiteProgressRepository
from rankeddojo.adapters.persistence.sqlite_store import SQLiteStore
from rankeddojo.adapters.runtime.c_runtime import CRuntime
from rankeddojo.adapters.workspace.local_exercise_workspace import LocalExerciseWorkspace
from rankeddojo.adapters.workspace.local_workspace import LocalWorkspace
from rankeddojo.application.engine.runtime_registry import RuntimeRegistry
from rankeddojo.application.use_cases.mvp_coordinator import (
    MVPTrainerCoordinator,
    PreflightResult,
    TrainingOptions,
)
from rankeddojo.domain.grading import GradingOutcome, GradingResult, TraceData
from rankeddojo.ports.grader_port import GradingRequest


class StaticGrader:
    def __init__(self, passed: bool) -> None:
        self.passed = passed
        # When set, overrides the passed/failed dichotomy entirely -- used by
        # tests that need to simulate a CONTENT_ERROR outcome (a pack/content
        # problem, distinct from both PASSED and USER_FAILED).
        self.outcome_override: GradingOutcome | None = None

    def grade(self, request: GradingRequest) -> GradingResult:
        outcome = self.outcome_override
        if outcome is None:
            outcome = GradingOutcome.PASSED if self.passed else GradingOutcome.USER_FAILED
        return GradingResult(
            outcome=outcome,
            seed=request.seed,
            trace_data=TraceData((f"Result: {outcome.value}",)),
        )


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class InMemoryConfig:
    def __init__(self) -> None:
        self.workspace_path: Path | None = None
        self.editor_command = "code"

    def save_workspace_path(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path

    def load_editor_command(self) -> str:
        return self.editor_command

    def save_editor_command(self, editor_command: str) -> None:
        self.editor_command = editor_command


class MVPTrainerCoordinatorTest(unittest.TestCase):
    def _coordinator(
        self,
        temp_dir: str,
        passed: bool = True,
        config: InMemoryConfig | None = None,
        clock: "FakeClock | None" = None,
    ) -> MVPTrainerCoordinator:
        root = Path(temp_dir)
        compiler = SystemCCompiler(candidates=("definitely-not-a-c-compiler",))
        return MVPTrainerCoordinator(
            pack_catalog=LocalPackCatalog(
                managed_packs_dir=root / "managed",
                bundled_packs_dir=Path(__file__).parent.parent / "examples" / "packs",
            ),
            progress_repository=SQLiteProgressRepository(
                SQLiteStore(root / "trainer.sqlite3")
            ),
            workspace=LocalExerciseWorkspace(),
            grader=StaticGrader(passed),
            editor=SubprocessEditor("definitely-not-used"),
            pack_importer=LocalPackImporter(root / "managed"),
            runtimes=RuntimeRegistry([CRuntime(compiler, manager=compiler)]),
            workspace_root=root / "workspace",
            editor_factory=SubprocessEditorFactory(),
            config_repository=config,
            workspace_port=LocalWorkspace(),
            clock=clock,
        )

    def test_training_selection_prioritizes_uncompleted_exercises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            options = TrainingOptions(
                pack_id="sample_rank",
                level_ids=("level0", "level1"),
                selection_mode="only_uncompleted",
                allow_repeated=False,
            )
            first = coordinator.choose_training_exercise(options)
            active = coordinator.prepare_exercise(first)
            coordinator.submit_training(active)

            second = coordinator.choose_training_exercise(options)

            self.assertNotEqual(first.definition.id, second.definition.id)

    def test_successful_exam_preflight_is_reused_until_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            coordinator._workspace_root.mkdir()
            with mock.patch.object(coordinator, "preflight_training", return_value=PreflightResult.passed()), \
                 mock.patch.object(coordinator, "pack_languages", return_value=("c",)), \
                 mock.patch.object(coordinator, "_preflight_languages", return_value=PreflightResult.passed()), \
                 mock.patch.object(coordinator, "_preflight_exam_content", return_value=PreflightResult.passed()):
                passed = coordinator.preflight_exam("sample_rank")
            self.assertTrue(passed.ok)

            with mock.patch.object(coordinator, "_preflight_exam_content", wraps=coordinator._preflight_exam_content) as content:
                self.assertTrue(coordinator.preflight_exam("sample_rank").ok)
                content.assert_not_called()
                self.assertTrue(coordinator.exam_preflight_ready("sample_rank"))

            coordinator._invalidate_exam_caches()
            self.assertFalse(coordinator.exam_preflight_ready("sample_rank"))

    def test_exam_preflight_cache_is_invalidated_by_runtime_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            coordinator._workspace_root.mkdir()
            coordinator._exam_preflight_cache["sample_rank"] = PreflightResult.passed()
            self.assertTrue(coordinator.exam_preflight_ready("sample_rank"))

            with mock.patch.object(coordinator._runtimes.get("c"), "redetect", return_value=None):
                coordinator.redetect_runtime("c")
            self.assertFalse(coordinator.exam_preflight_ready("sample_rank"))

    def test_exam_fail_keeps_same_exercise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=False)
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            active = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            _outcome, next_state = coordinator.submit_exam(state, active)

            self.assertIsNotNone(next_state)
            self.assertEqual(next_state.exercise_id, state.exercise_id)

    def test_exam_pass_advances_level(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=True)
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            active = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            _outcome, next_state = coordinator.submit_exam(state, active)

            self.assertIsNotNone(next_state)
            self.assertEqual(next_state.level_index, 1)

    # ----------------------------------------------------- CONTENT_ERROR safety
    def test_exam_content_error_does_not_increment_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=True)
            coordinator._grader.outcome_override = GradingOutcome.CONTENT_ERROR
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            active = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            outcome, _next_state = coordinator.submit_exam(state, active)

            self.assertEqual(outcome.attempts_count, 0)

    def test_exam_content_error_does_not_record_level_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=True)
            coordinator._grader.outcome_override = GradingOutcome.CONTENT_ERROR
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            active = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            coordinator.submit_exam(state, active)

            repo = SQLiteProgressRepository(SQLiteStore(Path(temp_dir) / "trainer.sqlite3"))
            self.assertEqual(repo.list_exam_level_results(state.id), [])

    def test_exam_content_error_does_not_fail_or_advance_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=True)
            coordinator._grader.outcome_override = GradingOutcome.CONTENT_ERROR
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            active = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            outcome, next_state = coordinator.submit_exam(state, active)

            self.assertIs(outcome.result.outcome, GradingOutcome.CONTENT_ERROR)
            self.assertFalse(outcome.result.passed)
            self.assertIsNotNone(next_state)
            # Stays on the same exercise/level; score is untouched.
            self.assertEqual(next_state.exercise_id, state.exercise_id)
            self.assertEqual(next_state.level_index, state.level_index)
            self.assertEqual(next_state.score, state.score)

    def test_exam_user_failed_still_increments_attempts_and_records_level_result(self) -> None:
        # Control: USER_FAILED must keep behaving exactly as before this phase.
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=False)
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            active = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            outcome, next_state = coordinator.submit_exam(state, active)

            self.assertIs(outcome.result.outcome, GradingOutcome.USER_FAILED)
            self.assertEqual(outcome.attempts_count, 1)
            self.assertIsNotNone(next_state)
            self.assertEqual(next_state.exercise_id, state.exercise_id)

            repo = SQLiteProgressRepository(SQLiteStore(Path(temp_dir) / "trainer.sqlite3"))
            level_results = repo.list_exam_level_results(state.id)
            self.assertEqual(len(level_results), 1)
            self.assertFalse(level_results[0]["passed"])

    def test_training_content_error_does_not_persist_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=True)
            coordinator._grader.outcome_override = GradingOutcome.CONTENT_ERROR
            ref = coordinator.choose_training_exercise(
                TrainingOptions(pack_id="sample_rank", level_ids=("level0",))
            )
            active = coordinator.prepare_exercise(ref)

            outcome = coordinator.submit_training(active)

            self.assertIs(outcome.result.outcome, GradingOutcome.CONTENT_ERROR)
            self.assertFalse(outcome.result.passed)
            self.assertEqual(outcome.attempts_count, 0)

    def test_training_content_error_does_not_mark_progress_as_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=True)
            coordinator._grader.outcome_override = GradingOutcome.CONTENT_ERROR
            ref = coordinator.choose_training_exercise(
                TrainingOptions(pack_id="sample_rank", level_ids=("level0",))
            )
            active = coordinator.prepare_exercise(ref)

            coordinator.submit_training(active)

            repo = SQLiteProgressRepository(SQLiteStore(Path(temp_dir) / "trainer.sqlite3"))
            progress = repo.progress_by_exercise("sample_rank")
            self.assertNotIn(active.ref.definition.id, progress)

    def test_training_user_failed_still_persists_attempt(self) -> None:
        # Control: USER_FAILED must keep behaving exactly as before this phase.
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir, passed=False)
            ref = coordinator.choose_training_exercise(
                TrainingOptions(pack_id="sample_rank", level_ids=("level0",))
            )
            active = coordinator.prepare_exercise(ref)

            outcome = coordinator.submit_training(active)

            self.assertIs(outcome.result.outcome, GradingOutcome.USER_FAILED)
            self.assertEqual(outcome.attempts_count, 1)

            repo = SQLiteProgressRepository(SQLiteStore(Path(temp_dir) / "trainer.sqlite3"))
            progress = repo.progress_by_exercise("sample_rank")
            self.assertIn(active.ref.definition.id, progress)

    def test_exam_timeout_finishes_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            clock = FakeClock()
            coordinator = self._coordinator(temp_dir, clock=clock)
            state = coordinator.start_exam("sample_rank", duration_seconds=1)
            clock.advance(1)

            updated = coordinator.tick_exam(state)

            self.assertIsNone(updated)
            self.assertIsNone(coordinator.load_active_exam())
            self.assertEqual(coordinator.list_exam_history()[0]["status"], "timeout")

    def test_exam_resume_loads_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            state = coordinator.start_exam("sample_rank", duration_seconds=60)

            loaded = coordinator.load_active_exam()

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.id, state.id)

    def test_training_workspace_uses_training_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            ref = coordinator.choose_training_exercise(
                TrainingOptions(pack_id="sample_rank", level_ids=("level0",))
            )

            active = coordinator.prepare_exercise(ref)

            self.assertIn("training", active.exercise_workspace_path.parts)
            self.assertNotIn("exam", active.exercise_workspace_path.parts)

    def test_exam_workspace_uses_session_directory_and_cleanup_preserves_training(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            training_ref = coordinator.choose_training_exercise(
                TrainingOptions(pack_id="sample_rank", level_ids=("level0",))
            )
            training = coordinator.prepare_exercise(training_ref)
            state = coordinator.start_exam("sample_rank", duration_seconds=60)
            exam = coordinator.prepare_exam_exercise(coordinator.exam_ref(state), state)

            self.assertIn("exams", exam.exercise_workspace_path.parts)
            self.assertIn(state.id, exam.exercise_workspace_path.parts)

            coordinator.finish_exam(state, "abandoned", state.score)

            self.assertTrue(training.exercise_workspace_path.exists())
            self.assertFalse((coordinator.workspace_root / "exams" / state.id).exists())

    def test_import_pack_through_application_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self._coordinator(temp_dir)
            sample_pack = Path(__file__).parent.parent / "examples" / "packs" / "sample_rank"

            pack = coordinator.import_pack(sample_pack)

            self.assertEqual(pack.id, "sample_rank")

    def test_settings_update_workspace_and_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = InMemoryConfig()
            coordinator = self._coordinator(temp_dir, config=config)
            new_workspace = Path(temp_dir) / "new-workspace"
            editor = Path(temp_dir) / "editor.exe"
            editor.write_text("", encoding="utf-8")

            coordinator.change_workspace(new_workspace)
            coordinator.save_editor_command(str(editor))

            self.assertTrue(new_workspace.is_dir())
            self.assertEqual(config.workspace_path, new_workspace)
            self.assertEqual(coordinator.editor_command(), str(editor))
