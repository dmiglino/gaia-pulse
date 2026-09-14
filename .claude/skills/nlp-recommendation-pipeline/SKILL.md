---
# Generated from .agents/ by scripts/agents/sync_agent_assets.py; do not edit.
name: nlp-recommendation-pipeline
description: Use when adding an NLP intent, adjusting the confidence threshold/OpenAI adapter, or changing a recommendation generator/filter/scorer.
---

# Change the NLP or recommendation pipeline

1. Read `README.md`'s "NLP Design" and "Recommendation Engine" sections.
2. For NLP: add the Pydantic intent to `app/nlp/intents.py`, the pattern to
   `app/nlp/rules.py`, and — only if genuinely ambiguous — the
   function-calling schema in `app/nlp/adapters/openai_adapter.py`.
3. Confirm the result still lands as `pending_confirmation` on an
   `NLPIngestionEvent`; never write the domain record directly from
   parsing.
4. For recommendations: add to exactly one of the four stages — candidate
   generation (`generators/`), hard-constraint filtering (`filters.py`),
   behavior-signal scoring (`scorer.py`), or ranking/persistence
   (`engine.py`) — don't blend stages. "Don't offer this subject right now"
   is stage 4, not stage 2 or 3: `engine.py` decides it against the rows it
   is about to write (`_still_suppressed`, `_without_duplicate_subjects`).
   A score penalty is never a suppression — `_DIVERSITY_PENALTY` lowered a
   0.95 candidate to 0.75 and it still came out first.
5. If the change is about *what the app learns* rather than one stage's
   behavior (a new `signal_type` or `subject_type`, decay, evidence,
   attribute or time-slot logic), it belongs in
   `app/recommendations/learning.py`, which both `filters.py` and
   `scorer.py` read — and it is written through `learning.record_signal()`,
   never through `BehaviorSignalRepository` from a service.
6. Add a deterministic fixture in `tests/test_nlp.py`,
   `tests/test_recommendations.py`, `tests/test_learning_signals.py` or
   `tests/test_household_learning.py` that doesn't call a live provider.
7. Run `.venv/bin/python -m pytest tests/test_nlp.py
   tests/test_recommendations.py tests/test_learning_signals.py
   tests/test_household_learning.py`. The last file is what keeps the
   household filter's union/intersection asymmetry from being "simplified"
   into one rule with the suite still green.
