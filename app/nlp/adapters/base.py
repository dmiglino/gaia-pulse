"""Abstract base class for LLM parsing adapters (Layer 2)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.nlp.intents import ParseResult


class BaseLLMAdapter(ABC):
    """Contract that every LLM adapter must satisfy.

    Layer 2 adapters receive the raw text plus the Layer 1 ParseResult so they
    can correct, extend, or override whatever the rule engine produced.
    """

    @abstractmethod
    async def parse(
        self,
        text: str,
        speaking_user: str,
        layer1_result: ParseResult,
    ) -> ParseResult:
        """Return an improved ParseResult.

        Must always return a valid ParseResult.  If the LLM call fails the
        implementation should fall back to *layer1_result*.

        Args:
            text: Original natural-language input.
            speaking_user: Key of the speaking user ("diego" | "rocio").
            layer1_result: Best-effort parse from the rule engine.

        Returns:
            A ParseResult with parser_layer set to "llm" or "combined".
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the adapter can reach the backend service."""
        ...
