from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from rankeddojo.domain.project_checks import PROJECT_CHECK_TYPES


class ProjectCheckError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectCheckResult:
    check_type: str
    passed: bool
    message: str = ""
    expected: str = ""
    received: str = ""
    details: str = ""


ProjectCheck = Callable[[str, dict[str, object]], ProjectCheckResult]


class ProjectCheckRegistry:
    """Trusted registry for declarative project checks.

    Packs select stable identifiers only. They cannot register Python code or
    replace a check implementation.
    """

    def __init__(self, checks: Iterable[tuple[str, ProjectCheck]] = ()) -> None:
        self._checks: dict[str, ProjectCheck] = {}
        for identifier, check in checks:
            self.register(identifier, check)

    def register(self, identifier: str, check: ProjectCheck) -> None:
        if identifier not in PROJECT_CHECK_TYPES:
            raise ProjectCheckError(f"Unknown project check: {identifier!r}")
        if identifier in self._checks:
            raise ProjectCheckError(f"Project check already registered: {identifier}")
        self._checks[identifier] = check

    def get(self, identifier: str) -> ProjectCheck:
        try:
            return self._checks[identifier]
        except KeyError as error:
            raise ProjectCheckError(f"Project check is not available: {identifier}") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._checks))
