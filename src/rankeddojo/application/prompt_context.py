from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from rankeddojo.application.study_intent import StudyIntent


def _terms(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return set(re.findall(r"[a-z0-9]+", plain))


@dataclass(frozen=True)
class PromptContextProfile:
    id: str
    description: str
    guidance: tuple[str, ...]
    required_terms: tuple[frozenset[str], ...]
    programming_languages: frozenset[str] = frozenset()

    def matches(self, intent: StudyIntent) -> bool:
        terms = _terms(intent.topic)
        language = intent.programming_language.casefold()
        if self.programming_languages:
            if language == "automatic":
                if not self.programming_languages.intersection(terms):
                    return False
            elif language not in self.programming_languages:
                return False
        return all(group & terms for group in self.required_terms)


class PromptContextRegistry:
    """Small internal registry for optional, non-authoritative prompt guidance."""

    def __init__(self, profiles: tuple[PromptContextProfile, ...] = ()) -> None:
        self._profiles: dict[str, PromptContextProfile] = {}
        for profile in profiles:
            self.register(profile)

    @classmethod
    def default(cls) -> "PromptContextRegistry":
        return cls(
            (
                PromptContextProfile(
                    id="42-c-exam",
                    description="42 School-style C exam preparation guidance",
                    guidance=(
                        "Use short, objective contracts suitable for timed practice.",
                        "Prefer small C functions involving strings, arrays, pointers and focused algorithms.",
                        "Consider linked lists when they fit the requested scope.",
                        "Use strict C compilation and realistic exam-sized exercises.",
                        "Treat this as style guidance only; do not claim exercises are official without an authoritative source.",
                    ),
                    required_terms=(
                        frozenset({"42"}),
                        frozenset({"exam", "exame", "prova", "assessment", "avaliacao"}),
                    ),
                    programming_languages=frozenset({"c"}),
                ),
            )
        )

    def register(self, profile: PromptContextProfile) -> None:
        if profile.id in self._profiles:
            raise ValueError(f"Duplicate prompt context profile id: {profile.id}")
        self._profiles[profile.id] = profile

    def get(self, profile_id: str) -> PromptContextProfile | None:
        return self._profiles.get(profile_id)

    def available(self) -> tuple[PromptContextProfile, ...]:
        return tuple(self._profiles.values())

    def detect(self, intent: StudyIntent) -> PromptContextProfile | None:
        for profile in self._profiles.values():
            if profile.matches(intent):
                return profile
        return None
