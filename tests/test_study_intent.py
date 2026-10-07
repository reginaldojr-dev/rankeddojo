from __future__ import annotations

import unittest

from rankeddojo.application.capabilities import default_exercise_capabilities
from rankeddojo.application.study_intent import PackPromptBuilder, StudyIntent
from rankeddojo.ports.runtime_port import RuntimeStatus


class PackPromptBuilderTest(unittest.TestCase):
    def test_prompt_contains_study_choices_contract_and_capabilities(self) -> None:
        prompt = PackPromptBuilder(
            capabilities=default_exercise_capabilities(),
            runtime_statuses=(
                RuntimeStatus(
                    language="python",
                    display_name="Python",
                    supported=True,
                    available=True,
                    tool="python3",
                ),
            ),
            pack_contract="schema_version: 3\nprogramming_language\ncontent_language",
        ).build(
            StudyIntent(
                topic="listas e dicionarios",
                goal="Praticar",
                format="Exercícios",
                programming_language="python",
                content_language="pt-BR",
                size="Médio",
                progression="progressive",
                levels="4",
                exercises_per_level="automatic",
            )
        )

        self.assertIn("listas e dicionarios", prompt)
        self.assertIn("Linguagem de programacao: python", prompt)
        self.assertIn("Idioma dos subjects/conteudo: pt-BR", prompt)
        self.assertIn("schema_version: 3", prompt)
        self.assertIn("program_output", prompt)
        self.assertIn("reference_output", prompt)
        self.assertIn("PROGRESSION", prompt)
        self.assertIn("NUMBER OF LEVELS", prompt)
        self.assertIn("exactly 4 levels", prompt)
        self.assertIn("NO SOLUTIONS RULE", prompt)
        self.assertIn("Do not include complete exercise solutions", prompt)
        self.assertIn("Python [python]: disponivel", prompt)
        for vendor in ("OpenAI", "Claude", "Codex"):
            self.assertNotIn(vendor, prompt)

    def test_empty_topic_gets_safe_default(self) -> None:
        prompt = PackPromptBuilder(
            capabilities=default_exercise_capabilities(),
            runtime_statuses=(),
            pack_contract="contrato",
        ).build(
            StudyIntent(
                topic=" ",
                goal="Revisar",
                format="Misto",
                programming_language="automatic",
                content_language="en",
                size="Curto",
                progression="uniform",
                levels="automatic",
                exercises_per_level="2",
            )
        )

        self.assertIn("fundamentos de programação", prompt)
        self.assertIn("Nenhum runtime registrado", prompt)


if __name__ == "__main__":
    unittest.main()
