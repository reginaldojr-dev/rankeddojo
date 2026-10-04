"""Preflight checks for activity content before grading.

This layer checks pack/runtime readiness without judging a user's submission.
It deliberately does not execute generated cases: a reference build failure is a
pack content problem, while reference smoke execution is deferred until grading's
existing CONTENT_ERROR fallback can record a trace.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rankeddojo.application.engine.execution import (
    ExecutionPlanError,
    ExecutionStrategy,
    default_execution_strategies,
    reference_spec,
)
from rankeddojo.application.engine.expectations import ExpectationRegistry, default_expectation_registry
from rankeddojo.application.engine.generators import TestCaseGeneratorRegistry, default_generator_registry
from rankeddojo.application.engine.runtime_registry import RuntimeRegistry, UnsupportedLanguageError
from rankeddojo.application.engine.test_case_service import TestCaseService
from rankeddojo.domain.exercise_definition import ExerciseDefinition, PYTHON_PROJECT
from rankeddojo.ports.runtime_port import LanguageRuntime, PreparedProgram


class ActivityPreflightStatus(str, Enum):
    READY = "ready"
    CONTENT_INVALID = "content_invalid"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    CONFIG_REQUIRED = "config_required"


@dataclass(frozen=True)
class ActivityPreflightResult:
    status: ActivityPreflightStatus
    issue_code: str = ""
    message: str = ""
    technical_detail: str = ""
    missing: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ActivityPreflightStatus.READY

    @classmethod
    def ready(cls) -> "ActivityPreflightResult":
        return cls(status=ActivityPreflightStatus.READY)

    @classmethod
    def runtime_unavailable(
        cls,
        message: str,
        technical_detail: str = "",
    ) -> "ActivityPreflightResult":
        return cls(
            status=ActivityPreflightStatus.RUNTIME_UNAVAILABLE,
            issue_code="runtime_unavailable",
            missing="Runtimes",
            message=message,
            technical_detail=technical_detail,
        )

    @classmethod
    def content_invalid(
        cls,
        message: str,
        technical_detail: str = "",
    ) -> "ActivityPreflightResult":
        return cls(
            status=ActivityPreflightStatus.CONTENT_INVALID,
            issue_code="content_invalid",
            missing="content",
            message=message,
            technical_detail=technical_detail,
        )


class ActivityContentPreflight:
    def __init__(
        self,
        runtimes: RuntimeRegistry,
        strategies: dict[str, ExecutionStrategy] | None = None,
        generator_registry: TestCaseGeneratorRegistry | None = None,
        expectation_registry: ExpectationRegistry | None = None,
    ) -> None:
        self._runtimes = runtimes
        self._strategies = strategies or default_execution_strategies()
        self._test_case_service = TestCaseService(
            generator_registry or default_generator_registry(),
            expectation_registry or default_expectation_registry(),
        )

    def check(
        self,
        definition: ExerciseDefinition,
        exercise_path: Path,
        *,
        probe_runtime: bool = True,
    ) -> ActivityPreflightResult:
        runtime_result = self._runtime_result(definition.language, probe_runtime=probe_runtime)
        if not runtime_result.ok:
            return runtime_result

        try:
            if definition.execution.type == PYTHON_PROJECT:
                if not definition.validation_plan or not definition.validation_plan.project_checks:
                    return ActivityPreflightResult.content_invalid("python_project requires declarative project checks.")
                return ActivityPreflightResult.ready()
            runtime = self._runtimes.get(definition.language)
            strategy = self._strategies.get(definition.execution.type)
            if strategy is None:
                raise ExecutionPlanError(
                    f"Unsupported execution type for activity preflight: {definition.execution.type}"
                )
            self._test_case_service.build_cases(definition, seed=1)
            source = exercise_path / definition.submission.filename
            strategy.submission(definition, exercise_path, source)
            if definition.reference is not None:
                with tempfile.TemporaryDirectory(prefix="rankeddojo-preflight-") as temp_dir:
                    build_dir = Path(temp_dir)
                    reference_program = prepare_reference_program(
                        definition,
                        exercise_path,
                        runtime,
                        build_dir,
                        f"{definition.id}_reference_preflight",
                    )
                if not reference_program.success:
                    return ActivityPreflightResult.content_invalid(
                        "A referência deste exercício não pôde ser preparada.",
                        reference_program.build.output,
                    )
        except (UnsupportedLanguageError, ExecutionPlanError, OSError, KeyError, ValueError) as error:
            return ActivityPreflightResult.content_invalid(
                "O conteúdo deste exercício está inválido.",
                str(error),
            )
        return ActivityPreflightResult.ready()

    def _runtime_result(self, language: str, *, probe_runtime: bool) -> ActivityPreflightResult:
        status = self._runtimes.status(language, probe=probe_runtime)
        if status.supported and status.available:
            return ActivityPreflightResult.ready()
        if not status.supported:
            return ActivityPreflightResult.runtime_unavailable(
                f"Este app não executa exercícios em '{status.language}'. Atualize o app ou use outro pack.",
                status.message,
            )
        return ActivityPreflightResult.runtime_unavailable(
            f"{status.display_name} não encontrado.\n"
            "Instale ou configure um runtime compatível para corrigir este exercício.",
            status.message,
        )


def prepare_reference_program(
    definition: ExerciseDefinition,
    exercise_path: Path,
    runtime: LanguageRuntime,
    build_dir: Path,
    name: str,
) -> PreparedProgram:
    reference = reference_spec(definition, exercise_path)
    return runtime.prepare(reference, build_dir, name)
