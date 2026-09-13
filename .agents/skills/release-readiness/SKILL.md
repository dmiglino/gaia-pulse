---
name: release-readiness
description: Use as the final gate before calling a change done — runs the full quality suite and confirms cross-agent evidence exists.
---

# Confirm release readiness

1. Run the full gate through the venv: `.venv/bin/python -m pytest tests/`,
   `.venv/bin/python -m ruff check .`,
   `.venv/bin/python -m black --check .`, `.venv/bin/python -m mypy app`.
   `alembic check` needs PostgreSQL and does not run on this machine — say so
   explicitly in the verdict instead of omitting it or calling the gate green
   without it.
2. Run `python3 scripts/agents/sync_agent_assets.py --check` if any
   `.agents/` file changed.
3. Confirm `security-privacy` and `code-health-qa` evidence exists for the
   change; confirm `documentation-steward` ran if the documented surface
   changed.
4. Confirm no gate was lowered and no finding was silently waived.
5. Report a final verdict: `APPROVE`, `APPROVE WITH FOLLOW-UP`, or
   `BLOCK`.
