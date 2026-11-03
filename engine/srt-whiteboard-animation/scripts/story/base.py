"""Story Provider 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StoryProvider(ABC):
    """Generate provider-specific story output for a topic prompt.

    Providers return raw text that the generator will parse as JSON and
    validate against the canonical Story schema.
    """

    @property
    def name(self) -> str:
        """Provider identifier used by logs and routing."""
        return self.__class__.__name__

    @abstractmethod
    def generate(self, topic: str) -> str:
        """Return raw provider output for a topic prompt.

        Args:
            topic: Human-readable topic text.

        Returns:
            Raw text expected to contain a JSON object matching canonical Story schema.
        """
