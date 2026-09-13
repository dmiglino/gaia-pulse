---
name: code-health-qa-review
description: Use after a meaningful change and before calling work done — checks simplicity, duplication, and functional correctness together.
---

# Run the code-health/QA gate

1. Run `.venv/bin/python -m ruff check .`,
   `.venv/bin/python -m black --check .`, `.venv/bin/python -m mypy app` —
   the venv form, since bare `ruff` is not on `PATH`. Compare the counts
   against the baseline table under "Verificación" in `docs/v3-plan.md`
   instead of treating pre-existing debt as new, and don't auto-fix or
   reformat files the change didn't touch.
2. Run `.venv/bin/python -m pytest tests/ --cov=app --cov-report=term-missing`.
3. Scan the diff for duplication, dead code, and unused
   imports/dependencies.
4. Ask of every new abstraction/config knob: does GaiaPulse need this
   today, or only hypothetically? If hypothetically, cut it.
5. Derive test cases from the change: happy path, boundary, invalid input,
   cross-user isolation, provider-failure fallback.
6. Report a verdict: `APPROVE`, `APPROVE WITH FOLLOW-UP`, or `BLOCK`.
