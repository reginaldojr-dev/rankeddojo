from __future__ import annotations

import unittest

from rankeddojo.application.prompt_context import PromptContextRegistry
from rankeddojo.application.study_intent import PackPromptBuilder, StudyIntent
from rankeddojo.application.capabilities import default_exercise_capabilities


def intent(topic: str, language: str = "c", **overrides: str) -> StudyIntent:
    values = {
        "topic": topic,
        "goal": "practice",
        "format": "exercises",
        "programming_language": language,
        "content_language": "en",
        "size": "medium",
    }
    values.update(overrides)
    return StudyIntent(**values)


class PromptContextRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = PromptContextRegistry.default()

    def test_42_c_exam_requires_context_signals(self) -> None:
        self.assertEqual(self.registry.detect(intent("quero estudar para a prova de C da 42")).id, "42-c-exam")
        self.assertEqual(self.registry.detect(intent("42 C exam practice")).id, "42-c-exam")
        self.assertIsNone(self.registry.detect(intent("quero aprender C")))
        self.assertIsNone(self.registry.detect(intent("quero estudar ponteiros em C")))

    def test_profile_does_not_change_explicit_options(self) -> None:
        selected = intent(
            "quero estudar para a prova de C da 42",
            progression="uniform",
            levels="5",
            exercises_per_level="3",
            content_language="pt-BR",
        )
        prompt = PackPromptBuilder(
            capabilities=default_exercise_capabilities(),
            runtime_statuses=(),
            pack_contract="schema_version: 3",
            context_registry=self.registry,
        ).build(selected)

        self.assertIn("uniform", prompt)
        self.assertIn("exactly 5 levels", prompt)
        self.assertIn("exactly 3 exercises per level", prompt)
        self.assertIn("pt-BR", prompt)
        self.assertIn("CONTEXT GUIDANCE", prompt)

    def test_generic_intent_has_no_context_guidance(self) -> None:
        prompt = PackPromptBuilder(
            capabilities=default_exercise_capabilities(),
            runtime_statuses=(),
            pack_contract="schema_version: 3",
            context_registry=self.registry,
        ).build(intent("quero aprender C"))

        self.assertNotIn("CONTEXT GUIDANCE", prompt)
        self.assertIn("NO SOLUTIONS RULE", prompt)


if __name__ == "__main__":
    unittest.main()
