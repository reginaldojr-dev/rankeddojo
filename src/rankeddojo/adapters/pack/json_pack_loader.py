from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import Any

from rankeddojo.adapters.contract_fields import read_schema_version, read_topics
from rankeddojo.application.capabilities import default_exercise_capabilities
from rankeddojo.domain.identifiers import UnsafeValueError, parse_relative_path, validate_identifier
from rankeddojo.domain.pack_definition import DEFAULT_LANGUAGE, PackDefinition, PackLevelDefinition, WORKSPACE_SCOPES

MAX_EXAM_DURATION_MINUTES = 24 * 60
V2_PACK_KEYS = frozenset(
    (
        "schema_version",
        "id",
        "name",
        "version",
        "language",
        "languages",
        "content_language",
        "topics",
        # Learning-track opt-in (Fase 9), optional. See domain/pack_definition.py.
        "learning_track",
        "description",
        "exam",
        "levels",
        "workspace",
    )
)


class PackDefinitionError(ValueError):
    pass


class JsonPackLoader:
    def __init__(self, supported_languages: frozenset[str] | None = None) -> None:
        # None means use the app's default capabilities.
        if supported_languages is None:
            supported_languages = frozenset(default_exercise_capabilities().languages)
        self._languages = supported_languages

    def load(self, pack_json_path: Path | str) -> PackDefinition:
        path = Path(pack_json_path)
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise PackDefinitionError(f"Invalid JSON in pack definition: {error.msg}.") from error
        except OSError as error:
            raise PackDefinitionError(f"Could not read pack definition: {error}.") from error

        data = self._require_object(raw_data, "pack definition")
        schema_version = read_schema_version(data, PackDefinitionError)
        if schema_version >= 3:
            unknown = sorted(set(data) - V2_PACK_KEYS)
            if unknown:
                raise PackDefinitionError(f"Unknown field(s) in pack.json v3: {', '.join(unknown)}.")
            language = self._read_optional_pack_language(data)
            content_language = self._read_locale(data.get("content_language", "pt-BR"), "content_language")
            topics = read_topics(data.get("topics", []), PackDefinitionError)
            self._read_languages_metadata(data.get("languages", []))
            learning_track = self._read_bool(data.get("learning_track", False), "learning_track")
        elif schema_version >= 2:
            unknown = sorted(set(data) - V2_PACK_KEYS)
            if unknown:
                raise PackDefinitionError(f"Unknown field(s) in pack.json v2: {', '.join(unknown)}.")
            language = self._require_identifier(data, "language") if "language" in data else DEFAULT_LANGUAGE
            content_language = self._read_locale(data.get("content_language", "pt-BR"), "content_language")
            topics = read_topics(data.get("topics", []), PackDefinitionError)
            self._read_languages_metadata(data.get("languages", []))
            learning_track = self._read_bool(data.get("learning_track", False), "learning_track")
        else:
            # v1: no language/topics/learning_track. Extra fields remain ignored as before.
            language, content_language, topics = DEFAULT_LANGUAGE, "pt-BR", ()
            learning_track = False
        if language not in self._languages:
            supported = ", ".join(sorted(self._languages))
            raise PackDefinitionError(f"Unsupported language: {language} (supported: {supported}).")
        pack_id = self._require_identifier(data, "id")
        name = self._require_non_empty_string(data, "name")
        version = self._require_non_empty_string(data, "version")
        levels = self._read_levels(data)
        exam_duration_seconds = self._read_exam_duration(data)
        workspace_scope = self._read_workspace_scope(data.get("workspace", {}))
        return PackDefinition(
            id=pack_id,
            name=name,
            version=version,
            levels=levels,
            exam_duration_seconds=exam_duration_seconds,
            schema_version=schema_version,
            language=language,
            content_language=content_language,
            topics=topics,
            learning_track=learning_track,
            workspace_scope=workspace_scope,
        )

    @staticmethod
    def _read_workspace_scope(raw: Any) -> str:
        if raw in (None, {}):
            return "exercise"
        if not isinstance(raw, dict) or set(raw) - {"scope"}:
            raise PackDefinitionError("workspace must contain only the scope field.")
        scope = raw.get("scope", "exercise")
        if not isinstance(scope, str) or scope not in WORKSPACE_SCOPES:
            raise PackDefinitionError("workspace.scope must be 'exercise' or 'pack'.")
        return scope

    def _read_optional_pack_language(self, data: dict[str, Any]) -> str:
        if "language" not in data:
            languages = data.get("languages")
            if isinstance(languages, list) and languages:
                first = languages[0]
                if not isinstance(first, str):
                    raise PackDefinitionError("languages[0] must be a string.")
                try:
                    return validate_identifier(first, "languages[0]")
                except UnsafeValueError as error:
                    raise PackDefinitionError(str(error)) from error
            return DEFAULT_LANGUAGE
        return self._require_identifier(data, "language")

    def _read_languages_metadata(self, raw: Any) -> tuple[str, ...]:
        if raw in (None, []):
            return ()
        if not isinstance(raw, list):
            raise PackDefinitionError("languages must be a list.")
        values: list[str] = []
        for index, item in enumerate(raw):
            if not isinstance(item, str):
                raise PackDefinitionError(f"languages[{index}] must be a string.")
            try:
                language = validate_identifier(item, f"languages[{index}]")
            except UnsafeValueError as error:
                raise PackDefinitionError(str(error)) from error
            if language not in self._languages:
                supported = ", ".join(sorted(self._languages))
                raise PackDefinitionError(f"Unsupported language: {language} (supported: {supported}).")
            values.append(language)
        return tuple(values)

    @staticmethod
    def _read_locale(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PackDefinitionError(f"{field_name} must be a non-empty locale string.")
        locale = value.strip()
        parts = locale.replace("_", "-").split("-")
        if not 1 <= len(parts) <= 3 or not all(part.isalnum() and 2 <= len(part) <= 8 for part in parts):
            raise PackDefinitionError(f"{field_name} must be a locale like pt-BR or en.")
        return locale

    @staticmethod
    def _read_bool(value: Any, field_name: str) -> bool:
        if not isinstance(value, bool):
            raise PackDefinitionError(f"{field_name} must be a boolean.")
        return value

    def _read_exam_duration(self, data: dict[str, Any]) -> int | None:
        if "exam" not in data:
            return None
        exam = self._require_object(data["exam"], "exam")
        if "duration_minutes" not in exam:
            return None
        minutes = exam["duration_minutes"]
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            raise PackDefinitionError("exam.duration_minutes must be an integer.")
        if not 1 <= minutes <= MAX_EXAM_DURATION_MINUTES:
            raise PackDefinitionError(
                f"exam.duration_minutes must be between 1 and {MAX_EXAM_DURATION_MINUTES}."
            )
        return minutes * 60

    def _read_levels(self, data: dict[str, Any]) -> tuple[PackLevelDefinition, ...]:
        if "levels" not in data:
            raise PackDefinitionError("Missing required field: levels.")
        raw_levels = data["levels"]
        if not isinstance(raw_levels, list) or not raw_levels:
            raise PackDefinitionError("levels must be a non-empty list.")

        levels: list[PackLevelDefinition] = []
        seen: set[str] = set()
        for index, raw_level in enumerate(raw_levels):
            level_data = self._require_object(raw_level, f"levels[{index}]")
            level_id = self._require_identifier(level_data, "id")
            level_path = self._require_relative_path(level_data, "path")
            if level_id in seen:
                raise PackDefinitionError(f"Duplicated level id: {level_id}.")
            seen.add(level_id)
            levels.append(PackLevelDefinition(id=level_id, path=level_path))
        return tuple(levels)

    @staticmethod
    def _require_object(raw_data: Any, field_name: str) -> dict[str, Any]:
        if not isinstance(raw_data, dict):
            raise PackDefinitionError(f"{field_name} must be an object.")
        return raw_data

    def _require_identifier(self, data: dict[str, Any], field_name: str) -> str:
        value = self._require_non_empty_string(data, field_name)
        try:
            return validate_identifier(value, field_name)
        except UnsafeValueError as error:
            raise PackDefinitionError(str(error)) from error

    @staticmethod
    def _require_non_empty_string(data: dict[str, Any], field_name: str) -> str:
        if field_name not in data:
            raise PackDefinitionError(f"Missing required field: {field_name}.")
        value = data[field_name]
        if not isinstance(value, str):
            raise PackDefinitionError(f"{field_name} must be a string.")
        if not value.strip():
            raise PackDefinitionError(f"{field_name} cannot be empty.")
        return value.strip()

    def _require_relative_path(self, data: dict[str, Any], field_name: str) -> PurePath:
        value = self._require_non_empty_string(data, field_name)
        try:
            return parse_relative_path(value, field_name)
        except UnsafeValueError as error:
            raise PackDefinitionError(str(error)) from error
