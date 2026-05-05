"""Main NLP orchestrator.

Layer 1 (rule-based) always runs.
Layer 2 (LLM) runs only when:
  - use_llm=True
  - settings.nlp_enabled is True (i.e. OPENAI_API_KEY is set)
  - Layer 1 overall_confidence < 0.7

Usage::

    from app.nlp.parser import NLPParser

    parser = NLPParser()
    result = await parser.parse("We bought 6 bananas", speaking_user="diego")
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.nlp.adapters.openai_adapter import OpenAIAdapter
from app.nlp.intents import ParseResult
from app.nlp.rules import parse as rules_parse

logger = logging.getLogger(__name__)

_LLM_CONFIDENCE_THRESHOLD = 0.7


class NLPParser:
    """Two-layer NLP parser for GaiaPulse wellness log entries.

    Instantiate once and reuse; the OpenAI client is created lazily and
    held on the adapter instance.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm_adapter = OpenAIAdapter()

    async def parse(
        self,
        text: str,
        speaking_user: str = "diego",
        use_llm: bool = True,
    ) -> ParseResult:
        """Parse a natural-language wellness input.

        Args:
            text: Raw text from the user.
            speaking_user: Key of the user who sent this message
                           ("diego" or "rocio").
            use_llm: Whether Layer 2 (LLM) parsing is allowed for this call.
                     Even when True, the LLM is only invoked if the Layer 1
                     confidence is below the threshold AND the API key is set.

        Returns:
            ParseResult (parser_layer will be "rules", "llm", or "combined").
        """
        # ── Layer 1 — always ────────────────────────────────────────────────
        layer1 = rules_parse(text, speaking_user=speaking_user)
        logger.debug(
            "Layer 1 result: confidence=%.3f intents=%d",
            layer1.overall_confidence,
            len(layer1.intents),
        )

        # ── Layer 2 — conditional ────────────────────────────────────────────
        should_use_llm = (
            use_llm
            and self._settings.nlp_enabled
            and layer1.overall_confidence < _LLM_CONFIDENCE_THRESHOLD
        )

        if not should_use_llm:
            return layer1

        logger.debug(
            "Confidence %.3f below %.1f — invoking LLM adapter.",
            layer1.overall_confidence,
            _LLM_CONFIDENCE_THRESHOLD,
        )

        layer2 = await self._llm_adapter.parse(
            text=text,
            speaking_user=speaking_user,
            layer1_result=layer1,
        )

        # If LLM improved or matched confidence, use it; otherwise keep Layer 1
        if layer2.overall_confidence >= layer1.overall_confidence:
            return layer2

        logger.debug("LLM result not better than Layer 1; keeping Layer 1.")
        return layer1
