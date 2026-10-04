from __future__ import annotations

import shutil
from pathlib import Path

from rankeddojo.domain.exercise_definition import ExerciseDefinition
from rankeddojo.ports.exercise_workspace_port import PreparedExerciseWorkspace, WorkspaceScope


class ExerciseWorkspaceError(RuntimeError):
    pass


class LocalExerciseWorkspace:
    def root_for(self, workspace_root: Path, scope: WorkspaceScope) -> Path:
        if scope.kind == "training":
            if not scope.pack_id:
                raise ExerciseWorkspaceError("Training workspace scope requires pack_id.")
            root = workspace_root / "training" / scope.pack_id
            return root / "project" if scope.shared else root
        if scope.kind == "exams":
            if not scope.session_id:
                raise ExerciseWorkspaceError("Exam workspace scope requires session_id.")
            root = workspace_root / "exams" / scope.session_id
            return root / "project" if scope.shared else root
        if scope.kind == "projects":
            if not scope.pack_id or not scope.owner_id:
                raise ExerciseWorkspaceError("Project workspace scope requires pack_id and owner_id.")
            return workspace_root / "projects" / scope.pack_id / scope.owner_id
        raise ExerciseWorkspaceError(f"Unknown workspace scope: {scope.kind!r}")

    def prepare(
        self,
        definition: ExerciseDefinition,
        exercise_content_path: Path,
        workspace_root: Path,
        overwrite: bool = False,
    ) -> PreparedExerciseWorkspace:
        exercise_workspace = workspace_root / definition.id
        exercise_workspace.mkdir(parents=True, exist_ok=True)

        source_subject = exercise_content_path / definition.subject
        if not source_subject.is_file():
            raise ExerciseWorkspaceError(f"Subject file not found: {source_subject}")

        subject_path = exercise_workspace / "subject.txt"
        subject_path.write_text(source_subject.read_text(encoding="utf-8"), encoding="utf-8")

        submission_path = exercise_workspace / definition.submission.filename
        had_existing_submission = submission_path.exists()
        if overwrite or not had_existing_submission:
            starter = exercise_content_path / definition.submission.filename
            if starter.is_file():
                submission_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(starter, submission_path)
            else:
                submission_path.write_text("", encoding="utf-8")

        for extra_file in definition.submission.extra_files:
            target = exercise_workspace / extra_file
            target.parent.mkdir(parents=True, exist_ok=True)
            if overwrite or not target.exists():
                starter = exercise_content_path / extra_file
                if starter.is_file():
                    shutil.copy2(starter, target)
                else:
                    target.write_text("", encoding="utf-8")

        for support_file in definition.support_files:
            source_support_file = exercise_content_path / support_file
            if not source_support_file.is_file():
                raise ExerciseWorkspaceError(
                    f"Support file not found: {source_support_file}"
                )
            target_support_file = exercise_workspace / support_file.name
            shutil.copy2(source_support_file, target_support_file)

        return PreparedExerciseWorkspace(
            exercise_workspace_path=exercise_workspace,
            subject_path=subject_path,
            submission_path=submission_path,
            had_existing_submission=had_existing_submission,
        )

    def prepare_scoped(
        self,
        definition: ExerciseDefinition,
        exercise_content_path: Path,
        workspace_root: Path,
        scope: WorkspaceScope,
        overwrite: bool = False,
    ) -> PreparedExerciseWorkspace:
        root = self.root_for(workspace_root, scope)
        if not scope.shared:
            return self.prepare(definition, exercise_content_path, root, overwrite)
        root.mkdir(parents=True, exist_ok=True)
        source_subject = exercise_content_path / definition.subject
        if not source_subject.is_file():
            raise ExerciseWorkspaceError(f"Subject file not found: {source_subject}")
        subject_path = root / ".rankeddojo" / "subjects" / f"{definition.id}.txt"
        subject_path.parent.mkdir(parents=True, exist_ok=True)
        subject_path.write_text(source_subject.read_text(encoding="utf-8"), encoding="utf-8")
        submission_path = root / definition.submission.filename
        submission_path.parent.mkdir(parents=True, exist_ok=True)
        had_existing_submission = submission_path.exists()
        if overwrite or not had_existing_submission:
            starter = exercise_content_path / definition.submission.filename
            if starter.is_file():
                shutil.copy2(starter, submission_path)
            else:
                submission_path.write_text("", encoding="utf-8")
        for extra_file in definition.submission.extra_files:
            target = root / extra_file
            target.parent.mkdir(parents=True, exist_ok=True)
            if overwrite or not target.exists():
                starter = exercise_content_path / extra_file
                if starter.is_file():
                    shutil.copy2(starter, target)
                else:
                    target.write_text("", encoding="utf-8")
        return PreparedExerciseWorkspace(root, subject_path, submission_path, had_existing_submission)

    def move_directory(self, source: Path, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        except OSError:
            pass

    def remove_directory(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)
