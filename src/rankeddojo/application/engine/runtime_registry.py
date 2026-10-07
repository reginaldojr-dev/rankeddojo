from __future__ import annotations

from collections.abc import Iterable

from rankeddojo.ports.runtime_port import LanguageRuntime, RuntimeDescriptor, RuntimeStatus
from rankeddojo.domain.exercise_definition import PROGRAM_OUTPUT

AUTOMATIC_LANGUAGE = "automatic"


def is_automatic_language(value: str) -> bool:
    return value == AUTOMATIC_LANGUAGE


class UnsupportedLanguageError(ValueError):
    pass


class RuntimeRegistry:
    """language -> runtime. The only place that knows which languages the app runs."""

    def __init__(self, runtimes: Iterable[LanguageRuntime] = ()) -> None:
        self._runtimes: dict[str, LanguageRuntime] = {}
        for runtime in runtimes:
            self.register(runtime)

    def register(self, runtime: LanguageRuntime) -> None:
        self._runtimes[runtime.language] = runtime

    def get(self, language: str) -> LanguageRuntime:
        try:
            return self._runtimes[language]
        except KeyError as error:
            raise UnsupportedLanguageError(f"No runtime installed for language: {language}.") from error

    def has(self, language: str) -> bool:
        return language in self._runtimes

    def languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._runtimes))

    def descriptors(self) -> tuple[RuntimeDescriptor, ...]:
        return tuple(self._descriptor_for(self._runtimes[language]) for language in self.languages())

    def descriptor(self, language: str) -> RuntimeDescriptor:
        return self._descriptor_for(self.get(language))

    @staticmethod
    def _descriptor_for(runtime: LanguageRuntime) -> RuntimeDescriptor:
        descriptor = getattr(runtime, "descriptor", None)
        if isinstance(descriptor, RuntimeDescriptor):
            return descriptor
        return RuntimeDescriptor(
            language=runtime.language,
            display_name=runtime.display_name,
            file_extensions=(),
            execution_types=(PROGRAM_OUTPUT,),
            function_harness="pack",
        )

    def primary_language(self) -> str | None:
        languages = self.languages()
        return languages[0] if languages else None

    def statuses(self, probe: bool = False) -> tuple[RuntimeStatus, ...]:
        return tuple(self.status(language, probe=probe) for language in self.languages())

    def status(self, language: str, probe: bool = False) -> RuntimeStatus:
        runtime = self._runtimes.get(language)
        if runtime is None:
            return RuntimeStatus(
                language=language,
                display_name=language,
                supported=False,
                available=False,
                checked=True,
                message=f"Este app não executa exercícios em '{language}'.",
            )

        available = runtime.check_available() if probe else runtime.is_ready()
        tool = runtime.current_tool()
        if available:
            message = f"{runtime.display_name} disponível."
        elif probe:
            message = f"{runtime.display_name} não encontrado ou não configurado."
        else:
            message = f"{runtime.display_name} ainda não verificado."
        return RuntimeStatus(
            language=language,
            display_name=runtime.display_name,
            supported=True,
            available=available,
            checked=probe or available,
            tool=tool,
            message=message,
        )
