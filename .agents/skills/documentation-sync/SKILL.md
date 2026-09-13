---
name: documentation-sync
description: Use after any change to the stack, schema, environment variables, or feature set to keep README.md and .env.example accurate.
---

# Sync documentation

1. Diff the change against README.md's stack table, schema table, project
   structure tree, env vars table, and "Known Tradeoffs & Roadmap".
2. Update only the section(s) the change actually affects.
3. Add or update `.env.example` for any new or changed environment
   variable — never a real secret value.
4. If the change resolves a documented tradeoff, move it out of "Current
   tradeoffs" and adjust the roadmap line.
5. If nothing needs updating, say so explicitly rather than skipping the
   check.
