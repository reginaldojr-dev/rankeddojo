from __future__ import annotations

from dataclasses import dataclass

from rankeddojo.application.capabilities import ExerciseCapabilities
from rankeddojo.ports.runtime_port import RuntimeStatus


@dataclass(frozen=True)
class StudyIntent:
    topic: str
    goal: str
    format: str
    programming_language: str
    content_language: str
    size: str
    progression: str = "progressive"
    levels: str = "automatic"
    exercises_per_level: str = "automatic"


class PackPromptBuilder:
    """Turns a study intent into a vendor-neutral pack generation prompt."""

    def __init__(
        self,
        *,
        capabilities: ExerciseCapabilities,
        runtime_statuses: tuple[RuntimeStatus, ...],
        pack_contract: str,
        context_registry=None,
    ) -> None:
        self._capabilities = capabilities
        self._runtime_statuses = runtime_statuses
        self._pack_contract = pack_contract
        if context_registry is None:
            from rankeddojo.application.prompt_context import PromptContextRegistry

            context_registry = PromptContextRegistry.default()
        self._context_registry = context_registry

    def build(self, intent: StudyIntent) -> str:
        topic = intent.topic.strip() or "fundamentos de programação"
        runtime_lines = [self._runtime_line(status) for status in self._runtime_statuses]
        if not runtime_lines:
            runtime_lines = ["- Nenhum runtime registrado nesta instalação."]

        languages = ", ".join(sorted(self._capabilities.languages)) or "nenhuma"
        executions = ", ".join(sorted(self._capabilities.executions.supported)) or "nenhuma"
        generators = ", ".join(sorted(self._capabilities.generators.supported)) or "nenhum"
        expectations = ", ".join(sorted(self._capabilities.expectations.supported)) or "nenhuma"
        reference_required = ", ".join(sorted(self._capabilities.expectations.reference_required)) or "nenhuma"

        profile = self._context_registry.detect(intent)
        progression = intent.progression or "progressive"
        levels = intent.levels or "automatic"
        exercises_per_level = intent.exercises_per_level or "automatic"
        level_rule = (
            f"Organize the pack into exactly {levels} levels."
            if levels != "automatic"
            else "Choose the number of levels from the topic, goal, format and size."
        )
        exercises_rule = (
            f"Use exactly {exercises_per_level} exercises per level."
            if exercises_per_level != "automatic"
            else "Choose exercises per level coherently with the requested size."
        )
        progression_rule = (
            "Difficulty must increase with level number. Level 1 is introductory and the final level is the hardest material in scope."
            if progression == "progressive"
            else "Use levels to organize content, but keep difficulty within approximately the same range across levels."
        )
        lines = [
            "Create a pack for RankedDojo.",
            "",
            "USER GOAL",
            f"{topic}",
            "",
            "PROGRAMMING LANGUAGE",
            f"{intent.programming_language}",
            "",
            "CONTENT LANGUAGE",
            f"{intent.content_language}",
            "",
            "FORMAT",
            f"{intent.format}",
            "",
            "SIZE",
            f"{intent.size}",
            "",
            "PROGRESSION",
            f"{progression}",
            progression_rule,
            "",
            "NUMBER OF LEVELS",
            f"{levels}",
            level_rule,
            "",
            "EXERCISES PER LEVEL",
            f"{exercises_per_level}",
            exercises_rule,
            "",
            "PACK CONTRACT",
            "Contrato atual do pack:",
            "Use schema_version 3 and only fields supported by the contract below.",
            "The real structure is pack.json plus level directories containing exercise.json and subject.md.",
            "Use declarative fixtures, expected values and supported validation data where applicable.",
            "",
            "VALIDATION RULES",
            "Use only supported strategies, generators, expectations and declarative project checks.",
            "Do not add arbitrary graders, shell commands, scripts, eval or exec payloads.",
            "",
            "NO SOLUTIONS RULE",
            "Do not include complete exercise solutions, solution directories or correct implementations in the distributed pack.",
            "Validation must rely on declarative test cases, fixtures, expectations and supported RankedDojo strategies.",
            "",
            "USER OPTIONS (legacy-readable labels)",
            f"- Linguagem de programacao: {intent.programming_language}",
            f"- Idioma dos subjects/conteudo: {intent.content_language}",
            f"- Objetivo: {intent.goal}",
            f"- Formato: {intent.format}",
            f"- Tamanho: {intent.size}",
            "",
            "CAPABILITIES",
            f"- Languages: {languages}",
            f"- Execution strategies: {executions}",
            f"- Generators: {generators}",
            f"- Expectations/validators: {expectations}",
            f"- Expectations that require a reference (legacy/internal): {reference_required}",
            "",
            "RUNTIMES AVAILABLE ON THIS MACHINE",
            *runtime_lines,
        ]
        if profile is not None:
            lines.extend(("", "CONTEXT GUIDANCE", f"Profile: {profile.id}", profile.description, *[f"- {item}" for item in profile.guidance]))
        lines.extend(("", "PACK CONTRACT DETAILS", "```markdown", self._pack_contract.strip(), "```"))
        return "\n".join(lines)

    @staticmethod
    def _runtime_line(status: RuntimeStatus) -> str:
        availability = "disponivel" if status.available else "indisponivel/nao verificado"
        tool = f" ({status.tool})" if status.tool else ""
        return f"- {status.display_name} [{status.language}]: {availability}{tool}"
