from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from rankeddojo.application.history_service import HistoryQuery, HistoryService, LearningHistorySummary
from rankeddojo.application.capabilities import ExerciseCapabilities, capabilities_from_runtime_descriptors
from rankeddojo.application.mvp_models import (
    ActiveExercise,
    CorrectionOutcome,
    ExerciseRef,
    LastSessionSummary,
    ProgressEntry,
)
from rankeddojo.application.study_intent import PackPromptBuilder, StudyIntent
from rankeddojo.application.prompt_context import PromptContextRegistry
from rankeddojo.application.engine.content_registry import ContentRegistry
from rankeddojo.application.engine.trace_summary import TraceSummary, build_trace_summary
from rankeddojo.application.use_cases.get_learning_track import GetLearningTrack, LearningTrackView
from rankeddojo.application.use_cases.get_next_learning_activity import GetNextLearningActivity
from rankeddojo.application.engine.activity_preflight import (
    ActivityContentPreflight,
    ActivityPreflightStatus,
)
from rankeddojo.domain.attempt_modes import EXAM_MODE, TRAINING_MODE
from rankeddojo.domain.learning import LearningActivityRef
from rankeddojo.domain.grading import GradingOutcome, GradingPolicy, GradingResult, TraceData
from rankeddojo.application.engine.runtime_registry import RuntimeRegistry
from rankeddojo.domain.pack_definition import PackDefinition
from rankeddojo.domain.session_policy import EXAM_POLICY_ID, TRAINING_POLICY_ID, SessionPolicy, SessionPolicyRegistry
from rankeddojo.ports.config_repository import AppSettingsRepository
from rankeddojo.ports.progress_repository import TrainerProgressRepository
from rankeddojo.ports.runtime_port import LanguageRuntime, RuntimeStatus
from rankeddojo.domain.workspace import Workspace
from rankeddojo.ports.editor_port import EditorFactory, EditorLaunchError, EditorPort
from rankeddojo.ports.exercise_workspace_port import ExerciseWorkspacePort, WorkspaceScope
from rankeddojo.ports.grader_port import GraderPort, GradingRequest


@dataclass
class TrainingOptions:
    pack_id: str
    level_ids: tuple[str, ...]
    selection_mode: str = "prioritize_uncompleted"
    allow_repeated: bool = False


@dataclass
class ExamState:
    id: str
    pack_id: str
    level_index: int
    exercise_id: str
    score: float
    remaining_seconds: int
    seed: int
    workspace_path: Path
    deadline_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: int = 0


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    missing: str | None = None
    message: str = ""
    status: ActivityPreflightStatus = ActivityPreflightStatus.READY
    issue_code: str = ""
    technical_detail: str = ""

    @classmethod
    def passed(cls) -> "PreflightResult":
        return cls(ok=True)

    @classmethod
    def failed(
        cls,
        missing: str,
        message: str,
        status: ActivityPreflightStatus = ActivityPreflightStatus.CONFIG_REQUIRED,
        technical_detail: str = "",
        issue_code: str = "",
    ) -> "PreflightResult":
        return cls(
            ok=False,
            missing=missing,
            message=message,
            status=status,
            issue_code=issue_code,
            technical_detail=technical_detail,
        )


class MVPTrainerCoordinator:
    def __init__(
        self,
        pack_catalog,
        progress_repository: TrainerProgressRepository,
        workspace: ExerciseWorkspacePort,
        grader: GraderPort,
        editor: EditorPort,
        pack_importer,
        runtimes: RuntimeRegistry,
        workspace_root: Path,
        editor_factory: EditorFactory,
        config_repository: AppSettingsRepository | None = None,
        workspace_port=None,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
        session_policies: SessionPolicyRegistry | None = None,
        content_registry: ContentRegistry | None = None,
    ) -> None:
        """`runtimes`: one runtime per language, assembled by the composition root
        or the test itself. The application never instantiates a concrete runtime
        adapter. The same applies to `editor_factory`: the adapter knows how to
        create/validate a concrete editor, not the coordinator.
        """
        self._pack_catalog = pack_catalog
        self._progress_repository = progress_repository
        self._workspace = workspace
        self._grader = grader
        self._editor = editor
        self._pack_importer = pack_importer
        self._runtimes = runtimes
        self._editor_factory = editor_factory
        self._config_repository = config_repository
        self._workspace_port = workspace_port
        self._workspace_root = workspace_root
        self._seen_training_exercises: set[tuple[str, str]] = set()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._random = rng or random.Random()
        self._session_policies = session_policies or SessionPolicyRegistry()
        self._history = HistoryService(progress_repository)
        self._activity_preflight = ActivityContentPreflight(runtimes)
        self._expired_exam: ExamState | None = None
        # Fase 9/10: learning tracks. `content_registry` is optional -- an
        # empty `ContentRegistry()` (no providers registered) behaves like
        # "no learning content available" everywhere (empty languages()/
        # track()), so every existing caller/test that builds a coordinator
        # without passing one keeps working unchanged.
        self._content_registry = content_registry or ContentRegistry()
        self._prompt_context_registry = PromptContextRegistry.default()
        self._get_learning_track = GetLearningTrack(self._content_registry, progress_repository)
        self._get_next_learning_activity = GetNextLearningActivity(self._get_learning_track)
        self.adopt_legacy_progress()

    def adopt_legacy_progress(self) -> int:
        """Associate old packless attempts with the installed pack that declares the exercise.

        Only when the exercise_id exists in exactly one pack. Never deletes
        anything. Failures here must not prevent the app from opening.
        """
        try:
            owners: dict[str, set[str]] = {}
            for pack in self.list_packs():
                for ref in self._pack_catalog.list_exercises(pack.id):
                    owners.setdefault(ref.definition.id, set()).add(pack.id)
            unique = {exercise_id: next(iter(packs)) for exercise_id, packs in owners.items() if len(packs) == 1}
            return self._progress_repository.adopt_legacy_attempts(unique)
        except Exception:  # noqa: BLE001 - opportunistic migration, never blocking
            return 0

    def list_packs(self) -> list[PackDefinition]:
        return self._pack_catalog.list_packs()

    def list_managed_packs(self) -> list[PackDefinition]:
        return self._pack_catalog.list_managed_packs()

    def pack_origin(self, pack_id: str) -> str | None:
        return self._pack_catalog.pack_origin(pack_id)

    def remove_managed_pack(self, pack_id: str) -> PackDefinition:
        return self._pack_catalog.remove_managed_pack(pack_id)

    def list_levels(self, pack_id: str) -> tuple[str, ...]:
        packs = {pack.id: pack for pack in self.list_packs()}
        pack = packs.get(pack_id)
        return () if pack is None else tuple(level.id for level in pack.levels)

    # ------------------------------------------------------------ learning
    # Thin delegation to the Fase 9 learning-track use cases: the UI talks
    # only to the coordinator (never to `ContentRegistry`/`LocalPackCatalog`
    # directly, per the architecture boundary test), and no prerequisite or
    # progress-derivation logic is reimplemented here.
    def learning_languages(self) -> tuple[str, ...]:
        return self._content_registry.languages()

    def learning_track(self, language: str) -> LearningTrackView:
        return self._get_learning_track.execute(language)

    def next_learning_activity(self, language: str) -> LearningActivityRef | None:
        return self._get_next_learning_activity.execute(language)

    def exercise_ref_for_activity(self, pack_id: str, activity_id: str) -> ExerciseRef | None:
        """Resolves a learning activity back to the same `ExerciseRef` the
        existing training/exam flow already uses -- reuses `_pack_catalog`
        (the one source of truth for pack/exercise parsing) instead of any
        new lookup path."""
        for ref in self._pack_catalog.list_exercises(pack_id):
            if ref.definition.id == activity_id:
                return ref
        return None

    def last_session_summary(self) -> LastSessionSummary | None:
        """The Home screen's "LAST SESSION" block: the most recent *training*
        attempt, with the pack/exercise metadata and progress fraction needed
        to display it. Built entirely from existing read paths -- the same
        `list_activity_attempts` the history screen uses (already sorted most
        recent first) and the same `_pack_catalog`/`progress_by_exercise`
        combination `exercise_ref_for_activity`/`exercise_history_rows`
        already rely on. Exam attempts are excluded: resuming an exam needs
        the dedicated exam-resume flow (`load_active_exam`/`_exam_resume_card`),
        which this block never duplicates.

        Returns None when there is no training attempt yet -- the UI shows an
        empty state rather than inventing a session.
        """
        attempts = self._progress_repository.list_activity_attempts(policy=TRAINING_MODE)
        if not attempts:
            return None
        latest = attempts[0]
        pack_id = str(latest["pack_id"])
        activity_id = str(latest.get("activity_id") or latest.get("exercise_id"))
        pack_name = pack_id
        activity_name = activity_id
        language = ""
        total_count = 0
        completed_count = 0
        for pack in self._pack_catalog.list_packs():
            if pack.id != pack_id:
                continue
            pack_name = pack.name
            refs = self._pack_catalog.list_exercises(pack.id)
            total_count = len(refs)
            progress = self._progress_repository.progress_by_exercise(pack.id)
            completed_count = sum(1 for entry in progress.values() if entry.best_passed)
            for ref in refs:
                if ref.definition.id == activity_id:
                    activity_name = ref.definition.name
                    language = ref.definition.programming_language
            break
        return LastSessionSummary(
            pack_id=pack_id,
            pack_name=pack_name,
            activity_id=activity_id,
            activity_name=activity_name,
            language=language,
            mode=TRAINING_MODE,
            completed_count=completed_count,
            total_count=total_count,
        )

    def list_progress(self) -> list[ProgressEntry]:
        return self._progress_repository.list_progress()

    def list_exam_history(self) -> list[dict[str, object]]:
        return self._progress_repository.list_exam_history()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def change_workspace(self, workspace_path: Path) -> None:
        workspace = Workspace.from_path(workspace_path)
        if self._workspace_port is not None:
            self._workspace_port.ensure_exists(workspace)
        if self._config_repository is not None:
            self._config_repository.save_workspace_path(workspace.path)
        self._workspace_root = workspace.path

    def theme_key(self) -> str | None:
        return None if self._config_repository is None else self._config_repository.load_theme()

    def save_theme(self, theme_key: str) -> None:
        if self._config_repository is not None:
            self._config_repository.save_theme(theme_key)

    def editor_command(self) -> str:
        if self._config_repository is None:
            return "code"
        return self._config_repository.load_editor_command()

    def editor_display_name(self) -> str:
        return self._editor_factory.display_name(self.editor_command())

    def resolve_known_editor(self, label: str) -> str | None:
        return self._editor_factory.resolve_known(label)

    def known_editor_labels(self) -> tuple[str, ...]:
        return self._editor_factory.known_labels()

    def save_editor_command(self, command: str) -> None:
        executable = self._editor_factory.validate(command)
        if self._config_repository is not None:
            self._config_repository.save_editor_command(str(executable))
        self._editor = self._editor_factory.create(str(executable))

    # ---------------------------------------------------------------- runtimes
    def runtime(self, language: str) -> LanguageRuntime:
        return self._runtimes.get(language)

    def supported_languages(self) -> tuple[str, ...]:
        return self._runtimes.languages()

    def runtime_statuses(self, probe: bool = False) -> tuple[RuntimeStatus, ...]:
        return self._runtimes.statuses(probe=probe)

    def exercise_capabilities(self) -> ExerciseCapabilities:
        return capabilities_from_runtime_descriptors(self._runtimes.descriptors())

    def build_pack_prompt(self, intent: StudyIntent, pack_contract: str) -> str:
        builder = PackPromptBuilder(
            capabilities=self.exercise_capabilities(),
            runtime_statuses=self.runtime_statuses(probe=False),
            pack_contract=pack_contract,
            context_registry=self._prompt_context_registry,
        )
        return builder.build(intent)

    def runtime_status(self, language: str, probe: bool = False) -> RuntimeStatus:
        return self._runtimes.status(language, probe=probe)

    def runtime_display_name(self, language: str) -> str:
        return self.runtime_status(language).display_name

    def runtime_current_tool(self, language: str) -> str | None:
        return self.runtime_status(language).tool

    def redetect_runtime(self, language: str) -> str | None:
        return self.runtime(language).redetect()

    def pack_language(self, pack_id: str | None) -> str:
        languages = self.pack_languages(pack_id)
        if languages:
            return languages[0]
        primary = self._runtimes.primary_language()
        if primary is not None:
            return primary
        for pack in self.list_packs():
            if pack.id == pack_id:
                return pack.language
        return ""

    def pack_languages(self, pack_id: str | None) -> tuple[str, ...]:
        if pack_id is None:
            return ()
        refs = self._pack_catalog.list_exercises(pack_id)
        if refs:
            return tuple(sorted({ref.definition.language for ref in refs}))
        for pack in self.list_packs():
            if pack.id == pack_id:
                return (pack.language,)
        return ()

    def runtime_ready(self, language: str) -> bool:
        """No external process: safe on the UI thread. False means not checked yet."""
        return self._runtimes.has(language) and self._runtimes.get(language).is_ready()

    def runtime_available(self, language: str) -> bool:
        """May spawn probe processes. Call outside the UI thread."""
        return self._runtimes.has(language) and self._runtimes.get(language).check_available()

    # C: shortcuts used by Settings > Compiler
    def current_compiler(self) -> str | None:
        language = self._runtimes.primary_language()
        return None if language is None else self.runtime_current_tool(language)

    def redetect_compiler(self) -> str | None:
        language = self._runtimes.primary_language()
        return None if language is None else self.redetect_runtime(language)

    def save_manual_compiler(self, compiler_path: Path) -> None:
        language = self._runtimes.primary_language()
        if language is None:
            raise ValueError("Nenhum runtime registrado.")
        self.save_manual_runtime(language, compiler_path)

    def save_manual_runtime(self, language: str, path: Path) -> str:
        """Validate with a probe and save the selected tool for the language."""
        configured = self.runtime(language).configure_manual(path)
        if self._config_repository is not None:
            self._config_repository.save_runtime_path(language, str(path))
        return configured

    def exercise_history_rows(self) -> list[dict[str, object]]:
        """One row per installed activity plus attempts from missing/legacy packs."""
        return self._history.exercise_rows(self.list_packs(), self._pack_catalog.list_exercises)

    def exam_history_rows(self) -> list[dict[str, object]]:
        return self._history.exam_rows()

    def history_timeline(self, query: HistoryQuery = HistoryQuery()):
        return self._history.timeline(query)

    def history_overview(self, query: HistoryQuery = HistoryQuery()):
        return self._history.overview(query)

    def history_pack_summaries(self):
        return self._history.pack_summaries(self.list_packs(), self._pack_catalog.list_exercises)

    def history_pack_summary(self, pack_id: str):
        return self._history.pack_summary(pack_id, self.list_packs(), self._pack_catalog.list_exercises)

    def history_activity_summary(self, pack_id: str, activity_id: str):
        return self._history.activity_summary(pack_id, activity_id, self.list_packs(), self._pack_catalog.list_exercises)

    def history_session_summaries(self):
        return self._history.session_summaries()

    def history_exam_summaries(self):
        return self._history.exam_summaries()

    def history_exam_summary(self):
        return self._history.exam_summary()

    def history_training_volume(self):
        return self._history.training_volume()

    def history_recent_sessions(self):
        return self._history.recent_sessions()

    def history_training_summaries(self):
        return self._history.training_summaries()

    def history_learning_summaries(self) -> tuple[LearningHistorySummary, ...]:
        summaries: list[LearningHistorySummary] = []
        for language in self.learning_languages():
            view = self.learning_track(language)
            completed_count = len(view.completed_activity_ids)
            if completed_count == 0:
                continue
            summaries.append(
                LearningHistorySummary(
                    language=language,
                    current_level=view.current_level,
                    completed_count=completed_count,
                    total_count=len(view.track.activities),
                    current_activity_id=None if view.next_activity is None else view.next_activity.activity_id,
                )
            )
        return tuple(summaries)

    def inspect_pack(self, source_path: Path):
        """Validate a pack without copying it; report whether it contains executable code."""
        return self._pack_importer.inspect_pack(source_path)

    def import_pack(self, source_path: Path) -> PackDefinition:
        return self._pack_importer.import_pack(source_path)

    def compiler_ready(self) -> bool:
        language = self._runtimes.primary_language()
        return False if language is None else self.runtime_ready(language)

    def compiler_available(self) -> bool:
        language = self._runtimes.primary_language()
        return False if language is None else self.runtime_available(language)

    def preflight_training(self) -> PreflightResult:
        if not self._workspace_root.exists():
            return PreflightResult.failed("workspace", "Configure a workspace antes de treinar.")
        if not self.list_packs():
            return PreflightResult.failed("packs", "Importe ou recarregue um pack antes de treinar.")
        return PreflightResult.passed()

    def preflight_exam(self, pack_id: str | None = None) -> PreflightResult:
        training = self.preflight_training()
        if not training.ok:
            return training
        languages = self.pack_languages(pack_id)
        if not languages:
            return PreflightResult.failed("packs", "Pack selecionado sem exercícios disponíveis.")
        runtimes = self._preflight_languages(languages)
        if not runtimes.ok:
            return runtimes
        if pack_id is None:
            return PreflightResult.passed()
        return self._preflight_exam_content(pack_id)

    def preflight_runtime(self, language: str) -> PreflightResult:
        """Is the pack language runtime ready? May run the probe."""
        return self._preflight_languages((language,))

    def preflight_exercise(self, ref: ExerciseRef) -> PreflightResult:
        return self._coordinator_preflight(ref, probe_runtime=True)

    def pack_runtimes_ready(self, pack_id: str | None) -> bool:
        languages = self.pack_languages(pack_id)
        return bool(languages) and all(self.runtime_ready(language) for language in languages)

    def _preflight_languages(self, languages: tuple[str, ...]) -> PreflightResult:
        failures: list[RuntimeStatus] = []
        for language in languages:
            status = self._runtimes.status(language, probe=True)
            if not status.supported or not status.available:
                failures.append(status)
        if not failures:
            return PreflightResult.passed()
        if len(failures) == 1:
            failure = failures[0]
            if not failure.supported:
                return PreflightResult.failed(
                    "Runtimes",
                    f"Este app não executa exercícios em '{failure.language}'. Atualize o app ou use outro pack.",
                    status=ActivityPreflightStatus.RUNTIME_UNAVAILABLE,
                )
            return PreflightResult.failed(
                "Runtimes",
                f"{failure.display_name} não encontrado.\n"
                "Instale ou configure um runtime compatível para corrigir este exercício.",
                status=ActivityPreflightStatus.RUNTIME_UNAVAILABLE,
            )
        missing = ", ".join(f"{failure.display_name} ({failure.language})" for failure in failures)
        return PreflightResult.failed(
            "Runtimes",
            f"Runtimes/toolchains indisponíveis para este pack: {missing}.",
            status=ActivityPreflightStatus.RUNTIME_UNAVAILABLE,
        )

    def preflight_editor(self) -> PreflightResult:
        try:
            self._editor_factory.validate(self.editor_command())
        except EditorLaunchError:
            return PreflightResult.failed(
                "editor",
                "Configure um editor/IDE válido antes de abrir a pasta do exercício.",
            )
        return PreflightResult.passed()

    def choose_training_exercise(self, options: TrainingOptions) -> ExerciseRef:
        refs = [
            ref
            for ref in self._pack_catalog.list_exercises(options.pack_id)
            if ref.level_id in options.level_ids
        ]
        if not refs:
            raise ValueError("No exercises available for selected levels.")

        progress = self._progress_repository.progress_by_exercise(options.pack_id)
        if not options.allow_repeated:
            unseen = [
                ref
                for ref in refs
                if (options.pack_id, ref.definition.id) not in self._seen_training_exercises
            ]
            if unseen:
                refs = unseen

        if options.selection_mode == "only_uncompleted":
            refs = [
                ref
                for ref in refs
                if not progress.get(ref.definition.id, None)
                or not progress[ref.definition.id].best_passed
            ]
            if not refs:
                raise ValueError("No uncompleted exercises available.")
        elif options.selection_mode == "prioritize_uncompleted":
            uncompleted = [
                ref
                for ref in refs
                if not progress.get(ref.definition.id, None)
                or not progress[ref.definition.id].best_passed
            ]
            if uncompleted:
                refs = uncompleted

        selected = self._random.choice(refs)
        self._seen_training_exercises.add((options.pack_id, selected.definition.id))
        return selected

    def training_workspace_root(self, pack_id: str) -> Path:
        return self._workspace.root_for(self._workspace_root, self._training_scope(pack_id))

    def prepare_exercise(self, ref: ExerciseRef, overwrite: bool = False) -> ActiveExercise:
        policy = self._policy(TRAINING_POLICY_ID)
        training_root = self.training_workspace_root(ref.pack.id)
        self._migrate_legacy_training_workspace(training_root, ref.definition.id)
        prepared = self._workspace.prepare_scoped(
            definition=ref.definition,
            exercise_content_path=ref.content_path,
            workspace_root=self._workspace_root,
            scope=self._training_scope(ref.pack.id, policy),
            overwrite=overwrite,
        )
        return ActiveExercise(
            ref=ref,
            exercise_workspace_path=prepared.exercise_workspace_path,
            subject_text=prepared.subject_path.read_text(encoding="utf-8"),
            submission_path=prepared.submission_path,
            had_existing_submission=prepared.had_existing_submission,
        )

    def _migrate_legacy_training_workspace(self, training_root: Path, exercise_id: str) -> None:
        """Move `training/<exercise_id>/` (old layout) to `training/<pack_id>/<exercise_id>/`.

        Moves only when the target does not exist yet and the old folder is
        actually an exercise workspace with `subject.txt`. Never deletes
        anything; if it cannot move, it leaves the folder in place.
        """
        legacy = self._workspace_root / "training" / exercise_id
        target = training_root / exercise_id
        if legacy == training_root or target.exists() or not (legacy / "subject.txt").is_file():
            return
        self._workspace.move_directory(legacy, target)

    def prepare_exam_exercise(
        self,
        ref: ExerciseRef,
        state: ExamState | None = None,
        overwrite: bool = False,
    ) -> ActiveExercise:
        session_id = state.id if state is not None else None
        prepared = self._workspace.prepare_scoped(
            definition=ref.definition,
            exercise_content_path=ref.content_path,
            workspace_root=self._workspace_root,
            scope=self._exam_scope(session_id, ref.pack),
            overwrite=overwrite,
        )
        return ActiveExercise(
            ref=ref,
            exercise_workspace_path=prepared.exercise_workspace_path,
            subject_text=prepared.subject_path.read_text(encoding="utf-8"),
            submission_path=prepared.submission_path,
            had_existing_submission=prepared.had_existing_submission,
        )

    def open_in_editor(self, active: ActiveExercise, *, reuse_window: bool = False) -> None:
        try:
            self._editor.open_directory(active.exercise_workspace_path, reuse_window=reuse_window)
        except EditorLaunchError:
            raise

    def trace_summary(self, outcome: CorrectionOutcome) -> TraceSummary | None:
        """The "trace resumido" for `outcome` -- `None` when it passed.

        UI-facing gateway: keeps `MainWindow` from importing
        `rankeddojo.application.engine.trace_summary`'s builder or
        `rankeddojo.domain.grading` directly (same pattern as the other
        small delegation methods on this coordinator).
        """
        return build_trace_summary(outcome.result)

    def open_trace_in_editor(self, outcome: CorrectionOutcome) -> None:
        """Opens the already-written `outcome.trace_path` (see
        `_persist_outcome`) in the user's configured editor, as a new tab --
        never inside RankedDojo's own window.
        """
        try:
            self._editor.open_file(outcome.trace_path, reuse_window=True)
        except EditorLaunchError:
            raise

    def submit_training(self, active: ActiveExercise) -> CorrectionOutcome:
        preflight = self._coordinator_preflight(active.ref, probe_runtime=True)
        if preflight.status is ActivityPreflightStatus.CONTENT_INVALID:
            return self._blocked_by_content(active, preflight, TRAINING_MODE)
        policy = self._policy(TRAINING_POLICY_ID)
        result = self._grade(active, policy.grading_policy())
        return self._persist_outcome(active, result, TRAINING_MODE)

    def start_exam(self, pack_id: str, duration_seconds: int | None = None) -> ExamState:
        pack = self._pack(pack_id)
        levels = self._exam_levels(pack)
        if not levels:
            raise ValueError("No exercises available for exam.")
        duration = duration_seconds or pack.exam_duration_seconds_or_default
        first = self._select_exam_ref(levels[0][1])
        session_id = str(uuid4())
        now = self._clock()
        state = ExamState(
            id=session_id,
            pack_id=pack_id,
            level_index=0,
            exercise_id=first.definition.id,
            score=0,
            remaining_seconds=duration,
            seed=random.SystemRandom().randint(1, 2**31),
            workspace_path=self._workspace.root_for(self._workspace_root, self._exam_scope(session_id, pack)) / ("project" if pack.workspace_scope == "pack" else first.definition.id),
            deadline_at=now + timedelta(seconds=duration),
            duration_seconds=duration,
        )
        self._save_exam_state(state, "active")
        return state

    def load_active_exam(self) -> ExamState | None:
        """Load the active exam.

        If the absolute deadline has already passed, including while the app was
        closed, finish it as timed out with the partial score and return None.
        """
        row = self._progress_repository.load_active_exam()
        if row is None:
            return None
        now = self._clock()
        deadline = _parse_datetime(row.get("deadline_at"))
        if deadline is None:
            deadline = now + timedelta(seconds=int(row.get("remaining_seconds") or 0))
        state = ExamState(
            id=str(row["id"]),
            pack_id=str(row["rank"]),
            level_index=int(row["current_level"]),
            exercise_id=str(row["current_exercise_id"]),
            score=float(row["score"]),
            remaining_seconds=max(0, int((deadline - now).total_seconds())),
            seed=int(row["seed"]),
            workspace_path=Path(str(row["workspace_path"])),
            deadline_at=deadline,
            duration_seconds=int(row.get("duration_seconds") or row.get("remaining_seconds") or 0),
        )
        if state.remaining_seconds <= 0:
            self.finish_exam(state, "timeout", state.score)
            self._expired_exam = state
            return None
        return state

    def pop_expired_exam(self) -> ExamState | None:
        """Exam that expired while the app was closed, to notify the user once."""
        expired, self._expired_exam = self._expired_exam, None
        return expired

    def exam_ref(self, state: ExamState) -> ExerciseRef:
        for ref in self._pack_catalog.list_exercises(state.pack_id):
            if ref.definition.id == state.exercise_id:
                return ref
        raise ValueError("Active exam exercise is no longer available.")

    def exam_duration_seconds(self, pack_id: str) -> int:
        return self._pack(pack_id).exam_duration_seconds_or_default

    def submit_exam(self, state: ExamState, active: ActiveExercise) -> tuple[CorrectionOutcome, ExamState | None]:
        policy = self._policy(EXAM_POLICY_ID)
        preflight = self._coordinator_preflight(active.ref, probe_runtime=True)
        if preflight.status is ActivityPreflightStatus.CONTENT_INVALID:
            outcome = self._blocked_by_content(active, preflight, EXAM_MODE, session_id=state.id)
            self._save_exam_state(state, "active")
            return outcome, state
        result = self._grade(active, policy.grading_policy(), seed=state.seed)
        outcome = self._persist_outcome(active, result, EXAM_MODE, session_id=state.id)
        if result.outcome is GradingOutcome.CONTENT_ERROR:
            # Pack/content failure: never the user's fault. No attempt was
            # persisted (see `_persist_outcome`), no level result is recorded
            # here, the exam isn't failed or advanced, and the deadline/score
            # are left untouched. The user stays on the same exercise and
            # sees the content-error feedback; resubmitting simply re-grades.
            #
            # Known limitation (deferred, see report): the exam clock keeps
            # running while this happens -- there is no timer pause/refund
            # mechanism yet. Not penalizing attempts/score/progression is the
            # guarantee this phase makes; not penalizing wall-clock time is
            # future work.
            self._save_exam_state(state, "active")
            return outcome, state
        self._progress_repository.record_exam_level_result(
            state.id,
            state.level_index,
            active.ref.definition.id,
            result.passed,
            outcome.attempts_count,
        )
        if policy.stays_on_fail and not result.passed:
            self._save_exam_state(state, "active")
            return outcome, state

        levels = self._exam_levels(self._pack(state.pack_id))
        next_index = state.level_index + 1
        if next_index >= len(levels):
            self.finish_exam(state, "completed", 100)
            return outcome, None

        score = (next_index / len(levels)) * 100
        next_ref = self._select_exam_ref(levels[next_index][1])
        next_state = replace(
            state,
            level_index=next_index,
            exercise_id=next_ref.definition.id,
            score=score,
            remaining_seconds=self._remaining(state),
            seed=random.SystemRandom().randint(1, 2**31),
            workspace_path=self._workspace.root_for(self._workspace_root, self._exam_scope(state.id, self._pack(state.pack_id))) / ("project" if self._pack(state.pack_id).workspace_scope == "pack" else next_ref.definition.id),
        )
        self._save_exam_state(next_state, "active")
        return outcome, next_state

    def tick_exam(self, state: ExamState) -> ExamState | None:
        """Recalculate remaining time from the absolute deadline.

        Does not write to the database: the deadline is already persisted. The
        exam is only finished when the deadline expires (timeout, partial score).
        """
        remaining = self._remaining(state)
        if remaining <= 0:
            expired = replace(state, remaining_seconds=0)
            self.finish_exam(expired, "timeout", expired.score)
            return None
        return replace(state, remaining_seconds=remaining)

    def remaining_seconds(self, state: ExamState) -> int:
        """Remaining time from the absolute deadline, with no side effects."""
        return self._remaining(state)

    def _remaining(self, state: ExamState) -> int:
        return max(0, int((state.deadline_at - self._clock()).total_seconds()))

    def _pack(self, pack_id: str) -> PackDefinition:
        for pack in self.list_packs():
            if pack.id == pack_id:
                return pack
        raise ValueError(f"Pack not found: {pack_id}")

    def _exam_levels(self, pack: PackDefinition) -> list[tuple[str, list[ExerciseRef]]]:
        """Levels in pack.json declaration order, never alphabetical, without empty levels."""
        refs = self._pack_catalog.list_exercises(pack.id)
        grouped: list[tuple[str, list[ExerciseRef]]] = []
        for level_id in pack.level_ids:
            level_refs = [ref for ref in refs if ref.level_id == level_id]
            if level_refs:
                grouped.append((level_id, level_refs))
        return grouped

    def finish_exam(self, state: ExamState, status: str, final_score: float | None = None) -> None:
        self._progress_repository.finish_exam(state.id, status, state.score if final_score is None else final_score)
        session_root = state.workspace_path.parent
        if session_root.parent in (self._workspace_root / "exam", self._workspace_root / "exams"):
            self._workspace.remove_directory(session_root)

    def _grade(
        self,
        active: ActiveExercise,
        policy: GradingPolicy,
        seed: int | None = None,
    ) -> GradingResult:
        return self._grader.grade(
            GradingRequest(
                definition=active.ref.definition,
                exercise_path=active.ref.content_path,
                workspace_path=active.exercise_workspace_path,
                policy=policy,
                seed=seed,
            )
        )

    def _persist_outcome(
        self,
        active: ActiveExercise,
        result: GradingResult,
        mode: str,
        session_id: str | None = None,
    ) -> CorrectionOutcome:
        pack_id = active.ref.pack.id
        exercise_id = active.ref.definition.id
        # A content error is a pack/system failure, not a user attempt: it
        # must never be persisted as one (no attempts-table row), so it can
        # never count toward attempts_count, best_passed, or ActivityProgress.
        # The trace is still written below for diagnosis -- that's technical
        # output, not progress/history.
        if result.outcome is not GradingOutcome.CONTENT_ERROR:
            self._progress_repository.save_grading_result(
                pack_id,
                exercise_id,
                result,
                mode,
                datetime.now(),
                session_id=session_id,
            )
        trace_path = active.exercise_workspace_path / "trace.txt"
        trace_path.write_text(result.trace_data.as_text(), encoding="utf-8")
        return CorrectionOutcome(
            result=result,
            trace_path=trace_path,
            # training: attempts for this exercise; exam: attempts in this session
            attempts_count=self._progress_repository.attempts_count(
                pack_id, exercise_id, mode, session_id=session_id
            ),
        )

    def _coordinator_preflight(self, ref: ExerciseRef, *, probe_runtime: bool) -> PreflightResult:
        result = self._activity_preflight.check(
            ref.definition,
            ref.content_path,
            probe_runtime=probe_runtime,
        )
        return PreflightResult(
            ok=result.ok,
            missing=result.missing,
            message=result.message,
            status=result.status,
            issue_code=result.issue_code,
            technical_detail=result.technical_detail,
        )

    def _preflight_exam_content(self, pack_id: str) -> PreflightResult:
        pack = self._pack(pack_id)
        for level_id, refs in self._exam_levels(pack):
            usable = [ref for ref in refs if self._coordinator_preflight(ref, probe_runtime=True).ok]
            if not usable:
                details = [
                    self._coordinator_preflight(ref, probe_runtime=True).technical_detail
                    for ref in refs
                ]
                return PreflightResult.failed(
                    "content",
                    f"O pack não possui exercício válido para o nível '{level_id}'.",
                    status=ActivityPreflightStatus.CONTENT_INVALID,
                    technical_detail="\n".join(detail for detail in details if detail),
                )
        return PreflightResult.passed()

    def _select_exam_ref(self, refs: list[ExerciseRef]) -> ExerciseRef:
        candidates = list(refs)
        while candidates:
            selected = self._random.choice(candidates)
            preflight = self._coordinator_preflight(selected, probe_runtime=True)
            if preflight.ok or preflight.status is ActivityPreflightStatus.RUNTIME_UNAVAILABLE:
                return selected
            candidates.remove(selected)
        raise ValueError("No valid exercises available for exam.")

    def _blocked_by_content(
        self,
        active: ActiveExercise,
        preflight: PreflightResult,
        mode: str,
        session_id: str | None = None,
    ) -> CorrectionOutcome:
        message = preflight.message
        if preflight.technical_detail:
            message = f"{message}\n{preflight.technical_detail}"
        result = GradingResult(
            outcome=GradingOutcome.CONTENT_ERROR,
            compile_output=message,
            trace_data=TraceData(("Content preflight failed.", message)),
        )
        return self._persist_outcome(active, result, mode, session_id=session_id)

    def _save_exam_state(self, state: ExamState, status: str) -> None:
        self._progress_repository.save_active_exam(
            {
                "id": state.id,
                "rank": state.pack_id,
                "current_level": state.level_index,
                "current_exercise_id": state.exercise_id,
                "score": state.score,
                "remaining_seconds": state.remaining_seconds,
                "seed": state.seed,
                "status": status,
                "policy": EXAM_POLICY_ID,
                "workspace_path": str(state.workspace_path),
                "started_at": datetime.now().isoformat(),
                "finished_at": None,
                "deadline_at": state.deadline_at.isoformat(),
                "duration_seconds": state.duration_seconds,
            }
        )

    def _policy(self, policy_id: str) -> SessionPolicy:
        return self._session_policies.get(policy_id)

    def register_session_policy(self, policy: SessionPolicy) -> None:
        self._session_policies.register(policy)

    def session_policy_ids(self) -> tuple[str, ...]:
        return self._session_policies.ids()

    def _training_scope(self, pack_id: str, policy: SessionPolicy | None = None) -> WorkspaceScope:
        policy = policy or self._policy(TRAINING_POLICY_ID)
        pack = self._pack(pack_id)
        return WorkspaceScope(kind=policy.workspace_scope_kind, pack_id=pack_id, shared=pack.workspace_scope == "pack")

    def _exam_scope(self, session_id: str | None, pack: PackDefinition | None = None) -> WorkspaceScope:
        policy = self._policy(EXAM_POLICY_ID)
        return WorkspaceScope(
            kind=policy.workspace_scope_kind,
            session_id=session_id or "_draft",
            shared=pack is not None and pack.workspace_scope == "pack",
        )


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
