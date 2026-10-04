# Progressive Project Packs

RankedDojo supports an opt-in shared workspace for declarative multi-file
projects. Existing packs keep the default isolated exercise workspace.

```json
{
  "schema_version": 3,
  "workspace": {"scope": "pack"}
}
```

The trusted `python_project` strategy evaluates only checks registered by the
core. Checks run against a temporary copy and return ordinary grading results
with an educational trace. Packs cannot ship executable graders or arbitrary
commands.

The first supported checks are `file_exists`, `module_imports`,
`callable_exists`, `class_exists`, `call_function`, and `raises`. Controlled
command execution is intentionally not part of this phase.
