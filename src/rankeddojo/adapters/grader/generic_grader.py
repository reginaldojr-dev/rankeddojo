"""Generic grader: language-independent.

GenericGrader -> ExecutionStrategy (which files) -> RuntimeRegistry (which runtime)
-> LanguageRuntime (prepare/run). Cases, expectations, comparison, fail-fast,
trace, seeds, and policy live here.
"""

from __future__ import annotations

import random
from dataclasses import replace

from rankeddojo.application.engine.activity_preflight import prepare_reference_program
from rankeddojo.application.engine.execution import (
    ExecutionPlanError,
    ExecutionStrategy,
    default_execution_strategies,
)
from rankeddojo.application.engine.expectations import ExpectationRegistry, default_expectation_registry
from rankeddojo.application.engine.generators import TestCaseGeneratorRegistry, default_generator_registry
from rankeddojo.application.engine.runtime_registry import RuntimeRegistry, UnsupportedLanguageError
from rankeddojo.application.engine.test_case_service import TestCaseService
from rankeddojo.application.engine.trace_builder import TraceBuilder
from rankeddojo.domain.grading import GradingOutcome, GradingResult, TestCase, TestResult
from rankeddojo.ports.grader_port import GradingRequest
from rankeddojo.ports.runtime_port import LanguageRuntime, PreparedProgram
from rankeddojo.adapters.grader.python_project_checks import run_project_checks
from rankeddojo.domain.exercise_definition import PYTHON_PROJECT


class ReferenceExecutionError(Exception):
    """The reference could not produce a trustworthy expected output.

    Raised when the reference program itself times out or exits non-zero
    while generating a test case's expected output. This is always a
    content problem (broken reference/harness/generated input), never
    something the submission did -- see `GenericGrader._content_error`.
    """


class GenericGrader:
    def __init__(
        self,
        runtimes: RuntimeRegistry,
        generator_registry: TestCaseGeneratorRegistry | None = None,
        expectation_registry: ExpectationRegistry | None = None,
        strategies: dict[str, ExecutionStrategy] | None = None,
    ) -> None:
        self._runtimes = runtimes
        self._strategies = strategies or default_execution_strategies()
        self._test_case_service = TestCaseService(
            generator_registry or default_generator_registry(),
            expectation_registry or default_expectation_registry(),
        )

    def grade(self, request: GradingRequest) -> GradingResult:
        seed = request.seed if request.seed is not None else random.SystemRandom().randint(1, 2**31)
        definition = request.definition
        trace = TraceBuilder()
        trace.add_environment(definition, request.workspace_path)

        if definition.execution.type == PYTHON_PROJECT:
            return self._grade_python_project(request, trace, seed)

        source_file = request.workspace_path / definition.submission.filename
        trace.add_collected_file(source_file)
        if not source_file.is_file():
            return self._content_error(trace, seed, f"Expected submission file not found: {source_file}")

        try:
            runtime = self._runtimes.get(definition.language)
            strategy = self._strategies.get(definition.execution.type)
            if strategy is None:
                raise ExecutionPlanError(f"Unsupported execution type for grader: {definition.execution.type}")
            submission_spec = strategy.submission(definition, request.exercise_path, source_file)
            reference = definition.reference
        except (UnsupportedLanguageError, ExecutionPlanError) as error:
            # The pack's own execution plan is unusable (unknown strategy,
            # missing harness declaration, ...): a content problem, never
            # something the user's submission did.
            return self._content_error(trace, seed, str(error))

        build_dir = request.workspace_path / ".build"
        program = runtime.prepare(submission_spec, build_dir, definition.id)
        trace.add_compilation(program.build)
        if not program.success:
            # The submission itself failed to prepare (compile error, syntax
            # error, ...). Runtime/toolchain availability is checked by
            # preflight before grading is ever reached (see
            # `main_window._runtime_checked`), so reaching this point means
            # the problem is in the user's own code.
            return GradingResult(
                outcome=GradingOutcome.USER_FAILED,
                compile_output=program.build.output,
                seed=seed,
                trace_data=trace.build(),
            )

        test_cases = self._test_case_service.build_cases(definition, seed)
        timeout = definition.limits.timeout_seconds
        if reference is not None:
            try:
                reference_program = prepare_reference_program(
                    definition,
                    request.exercise_path,
                    runtime,
                    build_dir,
                    f"{definition.id}_reference",
                )
            except ExecutionPlanError as error:
                return self._content_error(trace, seed, str(error))
            trace.add_compilation(reference_program.build)
            if not reference_program.success:
                return self._content_error(
                    trace, seed, f"Reference failed to compile:\n{reference_program.build.output}"
                )
            try:
                test_cases = [
                    self._case_with_reference_output(runtime, reference_program, case, timeout)
                    for case in test_cases
                ]
            except ReferenceExecutionError as error:
                # The reference compiled but couldn't be trusted to produce
                # `expected` for at least one generated case (crashed, exited
                # non-zero, or timed out). Never let the user's submission be
                # judged against an expected value we don't actually trust.
                return self._content_error(trace, seed, str(error))

        test_results: list[TestResult] = []
        for index, test_case in enumerate(test_cases, start=1):
            test_result = self._run_test_case(runtime, program, test_case, timeout)
            test_results.append(test_result)
            trace.add_test_result(index, test_result)
            if request.policy.fail_fast and not test_result.passed:
                break

        all_passed = all(result.passed for result in test_results) and bool(test_results)
        grading_result = GradingResult(
            outcome=GradingOutcome.PASSED if all_passed else GradingOutcome.USER_FAILED,
            compile_output=program.build.output,
            test_results=tuple(test_results),
            stderr="\n".join(result.stderr for result in test_results if result.stderr),
            seed=seed,
            trace_data=trace.build(),
        )
        trace.add_final_result(grading_result)
        return replace(grading_result, trace_data=trace.build())

    def _grade_python_project(self, request: GradingRequest, trace: TraceBuilder, seed: int) -> GradingResult:
        definition = request.definition
        source_file = request.workspace_path / definition.submission.filename
        trace.add_collected_file(source_file)
        if not source_file.is_file():
            return self._content_error(trace, seed, f"Expected project entry file not found: {source_file}")
        try:
            checks = run_project_checks(
                request.workspace_path,
                definition.validation_plan.project_checks if definition.validation_plan else (),
            )
        except Exception as error:  # malformed declarations are content errors
            return self._content_error(trace, seed, str(error))
        results = tuple(
            TestResult(
                test_case=TestCase(args=(check.check_type,), expected=check.expected),
                passed=check.passed,
                stdout=check.received,
                stderr=check.details if not check.passed else "",
            )
            for check in checks
        )
        for index, check in enumerate(checks, 1):
            trace.add_project_check(index, check)
        result = GradingResult(
            outcome=GradingOutcome.PASSED if checks and all(check.passed for check in checks) else GradingOutcome.USER_FAILED,
            test_results=results,
            seed=seed,
            trace_data=trace.build(),
        )
        trace.add_final_result(result)
        return replace(result, trace_data=trace.build())

    @staticmethod
    def _run_test_case(
        runtime: LanguageRuntime,
        program: PreparedProgram,
        test_case: TestCase,
        timeout_seconds: int,
    ) -> TestResult:
        outcome = runtime.run(program, test_case.args, test_case.stdin, timeout_seconds)
        if outcome.timed_out:
            return TestResult(
                test_case=test_case,
                passed=False,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                timed_out=True,
            )
        return TestResult(
            test_case=test_case,
            passed=outcome.exit_code == 0 and outcome.stdout == test_case.expected,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=outcome.exit_code,
        )

    @classmethod
    def _case_with_reference_output(
        cls,
        runtime: LanguageRuntime,
        reference: PreparedProgram,
        test_case: TestCase,
        timeout_seconds: int,
    ) -> TestCase:
        result = cls._run_test_case(runtime, reference, replace(test_case, expected=""), timeout_seconds)
        if result.timed_out:
            raise ReferenceExecutionError("Reference execution timed out while generating expected output.")
        if result.exit_code != 0:
            raise ReferenceExecutionError(
                f"Reference exited with code {result.exit_code} while generating expected output:\n"
                f"{result.stdout}{result.stderr}"
            )
        expected = result.stdout
        if result.stderr:
            expected += result.stderr
        return replace(test_case, expected=expected)

    @staticmethod
    def _content_error(trace: TraceBuilder, seed: int, message: str) -> GradingResult:
        trace.add_content_error(message)
        return GradingResult(
            outcome=GradingOutcome.CONTENT_ERROR,
            compile_output=message,
            seed=seed,
            trace_data=trace.build(),
        )
