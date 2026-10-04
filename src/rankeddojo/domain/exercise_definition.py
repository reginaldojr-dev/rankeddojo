"""Neutral exercise model independent of JSON version and programming language.

The loader normalizes v1 and v2 contracts into these classes; grader/runtime
code only knows this model. See `resources/pack-contract.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

from rankeddojo.domain.activity_definition import (
    ActivityDefinition,
    ActivityIdentity,
    UsageConstraints,
    ValidationPlan,
    ValidationStep,
)
from rankeddojo.domain.test_contract import TestContract

# Neutral execution types: what the runtime must do with the submission.
PROGRAM_OUTPUT = "program_output"
FUNCTION_CALL = "function_call"
PYTHON_PROJECT = "python_project"
EXECUTION_KINDS = (PROGRAM_OUTPUT, FUNCTION_CALL, PYTHON_PROJECT)

# Contract v1 aliases to neutral types. They remain accepted for compatibility.
LEGACY_EXECUTION_ALIASES = {
    "function_with_main": FUNCTION_CALL,
    "reference_compare": None,  # decided by fixture presence; see loader
}


@dataclass(frozen=True)
class SubmissionDefinition:
    filename: str | PurePath
    extra_files: tuple[PurePath, ...] = ()


@dataclass(frozen=True)
class ExecutionDefinition:
    """How a submission is executed.

    - `type`: neutral type (`program_output` | `function_call`);
    - `fixture`: harness provided by the pack, such as a C `main.c` that calls
      the target function;
    - `entry`/`args_format`: for languages where the app provides the harness
      (Python), the called function name and each case's argument format;
    - `declared_type`: the type as declared in JSON, such as
      `function_with_main` in v1.
    """

    type: str
    fixture: PurePath | None = None
    reference: PurePath | None = None  # v1 compat: same value as ExerciseDefinition.reference.source
    entry: str | None = None
    args_format: str | None = None
    declared_type: str | None = None

    @property
    def harness(self) -> PurePath | None:
        return self.fixture


@dataclass(frozen=True)
class ReferenceDefinition:
    """Reference solution that generates expected output (`expectation: reference_output`)."""

    source: PurePath
    harness: PurePath | None = None
    extra_files: tuple[PurePath, ...] = ()


@dataclass(frozen=True)
class TestCaseDefinition:
    args: tuple[str, ...] = ()
    stdin: str = ""
    expected: str | None = None


@dataclass(frozen=True)
class TestDefinition:
    generator: str
    expectation: str
    cases: tuple[TestCaseDefinition, ...] = ()
    contract: TestContract | None = None


@dataclass(frozen=True)
class LimitsDefinition:
    timeout_seconds: int


@dataclass(frozen=True)
class ExerciseDefinition:
    id: str
    name: str
    subject: PurePath
    submission: SubmissionDefinition
    execution: ExecutionDefinition
    tests: TestDefinition
    limits: LimitsDefinition
    support_files: tuple[PurePath, ...] = ()
    reference: ReferenceDefinition | None = None
    topics: tuple[str, ...] = ()
    schema_version: int = 1
    language: str = "c"
    programming_language: str | None = None
    content_language: str = "pt-BR"
    activity_type: str = "exercise"
    validation_plan: ValidationPlan | None = None
    usage: UsageConstraints = field(default_factory=UsageConstraints)
    # Learning-track metadata (Fase 9): optional, technical, never used for
    # grading. `difficulty` is a free-form label for future consumption --
    # it does not control selection or level-up in v1. `prerequisites` names
    # other exercise ids from the SAME pack only; see `domain/learning.py`.
    difficulty: str | None = None
    prerequisites: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.programming_language is None:
            object.__setattr__(self, "programming_language", self.language)
        elif self.language != self.programming_language:
            object.__setattr__(self, "language", self.programming_language)

    @property
    def activity(self) -> ActivityDefinition:
        plan = self.validation_plan or ValidationPlan(
            steps=(
                ValidationStep(
                    id="grade",
                    validator="program",
                    strategy=self.execution.type,
                    config={
                        "tests": self.tests.generator,
                        "expectation": self.tests.expectation,
                    },
                ),
            )
        )
        return ActivityDefinition(
            identity=ActivityIdentity(id=self.id, type=self.activity_type),
            title=self.name,
            subject=self.subject,
            language=self.programming_language or self.language,
            validation=plan,
            topics=self.topics,
            usage=self.usage,
        )

    @property
    def executable_files(self) -> tuple[PurePath, ...]:
        """Pack files that will be compiled or executed during grading."""
        files: list[PurePath] = []
        if self.execution.fixture is not None:
            files.append(self.execution.fixture)
        if self.reference is not None:
            files.append(self.reference.source)
            files.extend(self.reference.extra_files)
            if self.reference.harness is not None and self.reference.harness not in files:
                files.append(self.reference.harness)
        return tuple(files)
