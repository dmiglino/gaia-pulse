---
# Generated from .agents/ by scripts/agents/sync_agent_assets.py; do not edit.
name: instruction-sync
description: Use after a finished implementation to check that AGENTS.md, CLAUDE.md, docs/, the skills and the agent definitions still match the code.
---

# Sync the instruction layer

1. Verify every path, module name, command and count that `AGENTS.md` and
   `CLAUDE.md` assert, against the tree. Correct or delete what no longer
   holds; a rule nobody can follow still gets obeyed.
2. Check each `.agents/skills/*/SKILL.md` for a step that would fail here —
   a moved file, or a command this environment cannot run.
3. Ask whether the change created an area no agent owns, or should have been
   caught by a reviewer's checklist that lacks the line. Propose the line.
4. Check `docs/` against what shipped: a phase marked done matches the code,
   and a decision the implementation reversed says so where the old promise
   was written.
5. Edit `.agents/`, never `.claude/`; then run
   `python3 scripts/agents/sync_agent_assets.py --write` and `--check`.
6. Propose — do not apply — anything that loosens an `AGENTS.md`
   non-negotiable or your own guardrails.
7. If nothing needs updating, say so explicitly rather than skipping the
   check.
