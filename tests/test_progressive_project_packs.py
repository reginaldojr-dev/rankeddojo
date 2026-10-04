from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rankeddojo.adapters.exercise_definition.json_loader import ExerciseDefinitionError, JsonExerciseDefinitionLoader
from rankeddojo.adapters.pack.json_pack_loader import JsonPackLoader, PackDefinitionError
from rankeddojo.adapters.workspace.local_exercise_workspace import LocalExerciseWorkspace
from rankeddojo.application.engine.project_checks import ProjectCheckError, ProjectCheckRegistry
from rankeddojo.domain.exercise_definition import ExerciseDefinition, ExecutionDefinition, LimitsDefinition, SubmissionDefinition, TestDefinition
from rankeddojo.domain.project_checks import PROJECT_CHECK_TYPES
from rankeddojo.ports.exercise_workspace_port import WorkspaceScope


class ProgressivePackContractTest(unittest.TestCase):
    def test_pack_scope_is_opt_in_and_legacy_defaults_to_exercise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "legacy.json").write_text(json.dumps({"schema_version": 3, "id": "legacy", "name": "Legacy", "version": "1", "language": "python", "levels": [{"id": "l0", "path": "l0"}]}), encoding="utf-8")
            (root / "project.json").write_text(json.dumps({"schema_version": 3, "id": "project", "name": "Project", "version": "1", "language": "python", "workspace": {"scope": "pack"}, "levels": [{"id": "l0", "path": "l0"}]}), encoding="utf-8")
            self.assertEqual(JsonPackLoader().load(root / "legacy.json").workspace_scope, "exercise")
            self.assertEqual(JsonPackLoader().load(root / "project.json").workspace_scope, "pack")

    def test_unknown_project_check_is_rejected(self) -> None:
        data = {"schema_version": 3, "id": "project", "name": "Project", "subject": "subject.md", "programming_language": "python", "submission": {"filename": "src/main.py"}, "validation": {"strategy": "python_project", "checks": [{"type": "run_pack_script"}]}}
        with self.assertRaises(ExerciseDefinitionError):
            JsonExerciseDefinitionLoader().load_data(data)

    def test_project_check_registry_rejects_duplicates_and_unknown_ids(self) -> None:
        registry = ProjectCheckRegistry()
        registry.register(PROJECT_CHECK_TYPES[0], lambda workspace, config: None)
        with self.assertRaises(ProjectCheckError):
            registry.register(PROJECT_CHECK_TYPES[0], lambda workspace, config: None)
        with self.assertRaises(ProjectCheckError):
            registry.register("arbitrary_script", lambda workspace, config: None)

    def test_shared_workspace_keeps_existing_project_files(self) -> None:
        definition = ExerciseDefinition(
            id="step_2", name="Step 2", subject=Path("subject.md"),
            submission=SubmissionDefinition("src/main.py"),
            execution=ExecutionDefinition(type="python_project"),
            tests=TestDefinition("fixed_cases", "literal"), limits=LimitsDefinition(2),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); content = root / "content"; content.mkdir()
            (content / "subject.md").write_text("step", encoding="utf-8")
            project = root / "workspace" / "training" / "pack" / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "main.py").write_text("user code", encoding="utf-8")
            prepared = LocalExerciseWorkspace().prepare_scoped(definition, content, root / "workspace", WorkspaceScope("training", pack_id="pack", shared=True))
            self.assertEqual(prepared.exercise_workspace_path, project)
            self.assertEqual((project / "src" / "main.py").read_text(encoding="utf-8"), "user code")

