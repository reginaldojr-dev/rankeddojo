from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from rankeddojo.application.engine.project_checks import ProjectCheckError, ProjectCheckRegistry, ProjectCheckResult

_CHECK_SCRIPT = r'''
import importlib, json, pathlib, sys, traceback
root = pathlib.Path(sys.argv[1]).resolve()
config = json.loads(sys.argv[2])
sys.path.insert(0, str(root))

def target():
    value = config.get("target", "")
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("target must use module:attribute")
    module_name, attribute = value.split(":", 1)
    return getattr(importlib.import_module(module_name), attribute)

kind = config["type"]
if kind == "file_exists":
    path = (root / config["path"]).resolve()
    if root != path and root not in path.parents:
        raise ValueError("path escapes project workspace")
    ok = path.is_file()
    print(json.dumps({"passed": ok, "expected": "file exists", "received": str(path)}))
elif kind == "module_imports":
    importlib.import_module(config["module"])
    print(json.dumps({"passed": True}))
elif kind == "callable_exists":
    ok = callable(target())
    print(json.dumps({"passed": ok, "expected": "callable", "received": repr(target())}))
elif kind == "class_exists":
    value = target()
    ok = isinstance(value, type)
    print(json.dumps({"passed": ok, "expected": "class", "received": repr(value)}))
elif kind == "call_function":
    value = target()(*config.get("args", []), **config.get("kwargs", {}))
    expected = config.get("expected")
    ok = value == expected
    print(json.dumps({"passed": ok, "expected": repr(expected), "received": repr(value)}))
elif kind == "raises":
    try:
        target()(*config.get("args", []), **config.get("kwargs", {}))
    except Exception as error:
        expected = config["exception"]
        ok = type(error).__name__ == expected
        print(json.dumps({"passed": ok, "expected": expected, "received": type(error).__name__}))
    else:
        print(json.dumps({"passed": False, "expected": config["exception"], "received": "no exception"}))
else:
    raise ValueError("unsupported project check")
'''


def _python_command() -> str:
    if getattr(sys, "frozen", False):
        raise ProjectCheckError("A system Python interpreter is required for python_project.")
    return sys.executable


def _safe_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute():
        raise ProjectCheckError("Project check path must be relative.")
    path = (root / value).resolve()
    if path != root and root not in path.parents:
        raise ProjectCheckError("Project check path escapes the workspace.")
    return path


def _validate_config(root: Path, config: dict[str, object]) -> None:
    if config.get("type") == "file_exists":
        _safe_path(root, config.get("path"))
    if config.get("type") == "module_imports":
        module = config.get("module")
        if not isinstance(module, str) or not module or any(part == ".." for part in module.split(".")):
            raise ProjectCheckError("module_imports.module must be a safe module name.")


def run_project_checks(workspace: Path, checks: tuple[dict[str, object], ...]) -> tuple[ProjectCheckResult, ...]:
    results: list[ProjectCheckResult] = []
    registry = default_project_check_registry()
    for raw in checks:
        config = dict(raw)
        kind = config.get("type")
        if not isinstance(kind, str):
            raise ProjectCheckError("Project check type is required.")
        registry.get(kind)
        _validate_config(workspace, config)
        with tempfile.TemporaryDirectory(prefix="rankeddojo-project-check-") as temp_dir:
            isolated = Path(temp_dir) / "project"
            shutil.copytree(workspace, isolated, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".build", ".rankeddojo"))
            completed = subprocess.run(
                [_python_command(), "-I", "-c", _CHECK_SCRIPT, str(isolated), json.dumps(config)],
                capture_output=True, text=True, timeout=10, check=False,
            )
        details = (completed.stdout + completed.stderr).strip()
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            results.append(ProjectCheckResult(kind, bool(payload.get("passed")), f"{kind} completed", str(payload.get("expected", "")), str(payload.get("received", "")), details))
        except (ValueError, IndexError, json.JSONDecodeError):
            results.append(ProjectCheckResult(kind, False, f"{kind} failed", details=details))
    return tuple(results)


def default_project_check_registry() -> ProjectCheckRegistry:
    # The registry exposes the trusted implementation as one controlled runner;
    # the pack still selects only individual declarative check identifiers.
    return ProjectCheckRegistry((identifier, lambda workspace, config, _id=identifier: run_project_checks(Path(workspace), ({"type": _id, **config},))[0]) for identifier in (
        "file_exists", "module_imports", "callable_exists", "class_exists", "call_function", "raises"
    ))
