from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

PROJECT_CHECK_TYPES = (
    "file_exists",
    "module_imports",
    "callable_exists",
    "class_exists",
    "call_function",
    "raises",
)


@dataclass(frozen=True)
class ProjectCheck:
    """Declarative project check; values are data, never executable code."""

    type: str
    config: Mapping[str, object] = field(default_factory=dict)
