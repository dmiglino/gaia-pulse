# Claude Code project memory

`AGENTS.md` is canonical for this repository. This file only adds
Claude Code-specific pointers — it never duplicates `AGENTS.md` content.

## Claude Code-specific assets

- `.claude/agents/*.md` — Claude Code subagent definitions.
- `.claude/skills/*/SKILL.md` — Claude Code skill definitions.
- Both are generated from `.agents/agents/*.md` and `.agents/skills/*/SKILL.md`
  by `scripts/agents/sync_agent_assets.py`. Edit the `.agents/` source, run
  `python3 scripts/agents/sync_agent_assets.py --write`, and commit both the
  source and the generated `.claude/` files together.

## For any non-trivial change

1. Identify which architectural layer the change touches and route to the
   matching agent per the table in `AGENTS.md`.
2. If the change touches auth, sessions, personal health data, secrets, or
   file uploads, run `security-privacy` before calling the work done.
3. Run `code-health-qa` before calling any non-trivial change done.
4. If the change alters behavior `README.md` documents, run
   `documentation-steward`.
5. Once the change is finished, run `instruction-steward` — the instruction
   layer (this file, `AGENTS.md`, `docs/`, the skills, the agent definitions)
   goes stale silently, and a rule that no longer matches the code still gets
   obeyed.
6. If the change spans multiple agents' areas, use `integrator` to sequence
   the work and run the full quality gate.

## Quality gate

See "Required checks before completion" in `AGENTS.md` — it is the only copy,
including the `.venv/bin/python -m <tool>` invocation and the note that
`alembic check` needs PostgreSQL and does not run on this machine. A second
copy here drifts from the first.
