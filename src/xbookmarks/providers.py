from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .classifier import OllamaClassifier, RuleBasedClassifier
from .config import CategoryDefinition
from .models import ClassificationResult


PROVIDER_NAMES = ("rules", "ollama")


class Classifier(Protocol):
    def classify(self, text: str) -> ClassificationResult:
        ...


@dataclass(frozen=True)
class ProviderOptions:
    name: str
    ollama_model: str
    ollama_url: str
    ollama_timeout: int


@dataclass(frozen=True)
class ClassificationProvider:
    name: str
    classifier: Classifier
    model_label: str
    show_progress: bool = False

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model_label}"


def build_provider(
    options: ProviderOptions,
    definitions: dict[str, CategoryDefinition],
) -> ClassificationProvider:
    if options.name == "rules":
        rules = {
            category: definition.keywords
            for category, definition in definitions.items()
        }
        return ClassificationProvider(
            name="rules",
            classifier=RuleBasedClassifier(rules),
            model_label="rules",
        )

    if options.name == "ollama":
        return ClassificationProvider(
            name="ollama",
            classifier=OllamaClassifier(
                categories=list(definitions.keys()),
                category_descriptions={
                    category: definition.description
                    for category, definition in definitions.items()
                },
                model=options.ollama_model,
                base_url=options.ollama_url,
                timeout_seconds=options.ollama_timeout,
            ),
            model_label=options.ollama_model,
            show_progress=True,
        )

    raise ValueError(f"Unsupported provider: {options.name}")


def build_ollama_healthcheck_provider(
    model: str,
    base_url: str,
    timeout_seconds: int,
) -> OllamaClassifier:
    return OllamaClassifier(
        categories=["General"],
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
