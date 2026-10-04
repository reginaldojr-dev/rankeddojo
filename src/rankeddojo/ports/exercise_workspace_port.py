from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rankeddojo.domain.exercise_definition import ExerciseDefinition


@dataclass(frozen=True)
class PreparedExerciseWorkspace:
    exercise_workspace_path: Path
    subject_path: Path
    submission_path: Path
    had_existing_submission: bool


@dataclass(frozen=True)
class WorkspaceScope:
    kind: str
    pack_id: str | None = None
    session_id: str | None = None
    owner_id: str | None = None
    shared: bool = False


class ExerciseWorkspacePort(Protocol):
    def root_for(self, workspace_root: Path, scope: WorkspaceScope) -> Path:
        raise NotImplementedError

    def prepare(
        self,
        definition: ExerciseDefinition,
        exercise_content_path: Path,
        workspace_root: Path,
        overwrite: bool = False,
    ) -> PreparedExerciseWorkspace:
        raise NotImplementedError

    def prepare_scoped(
        self,
        definition: ExerciseDefinition,
        exercise_content_path: Path,
        workspace_root: Path,
        scope: WorkspaceScope,
        overwrite: bool = False,
    ) -> PreparedExerciseWorkspace:
        raise NotImplementedError

    def move_directory(self, source: Path, target: Path) -> None:
        """Move `source` to `target` best-effort and never raise.

        Used to migrate old workspace layouts without deleting anything.
        """
        raise NotImplementedError

    def remove_directory(self, path: Path) -> None:
        """Remove `path` recursively if it exists.

        Used to clean up finished exam sessions.
        """
        raise NotImplementedError
