from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

LEGACY_EXAM_DURATION_SECONDS = 4 * 60 * 60
DEFAULT_LANGUAGE = "c"
SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3)
WORKSPACE_SCOPES = ("exercise", "pack")


@dataclass(frozen=True)
class PackLevelDefinition:
    id: str
    path: PurePath


@dataclass(frozen=True)
class PackDefinition:
    id: str
    name: str
    version: str
    levels: tuple[PackLevelDefinition, ...]
    # Exam duration declared by the pack (`exam.duration_minutes`).
    # None means the pack did not declare one; use `exam_duration_seconds_or_default`.
    exam_duration_seconds: int | None = None
    # Legacy contracts can declare a default language; v3 uses per-activity languages.
    schema_version: int = 1
    language: str = DEFAULT_LANGUAGE
    content_language: str = "pt-BR"
    topics: tuple[str, ...] = ()
    # Learning-track opt-in (Fase 9): a pack without this (or set to False)
    # keeps existing training/exam behavior unchanged and is simply invisible
    # to the learning track model -- see `adapters/learning/pack_content_provider.py`.
    learning_track: bool = False
    workspace_scope: str = "exercise"

    @property
    def exam_duration_seconds_or_default(self) -> int:
        return self.exam_duration_seconds or LEGACY_EXAM_DURATION_SECONDS

    @property
    def level_ids(self) -> tuple[str, ...]:
        return tuple(level.id for level in self.levels)
