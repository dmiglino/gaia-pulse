# Portable agent assets

`.agents/agents/` is the semantic source of truth for GaiaPulse's agent
team. `.claude/agents/` is a generated runtime adapter — Claude Code needs a
`tools:` allowlist that the source files intentionally don't carry, since
that allowlist is a Claude Code concept, not a description of the role. Do
not edit `.claude/agents/` by hand; run
`python3 scripts/agents/sync_agent_assets.py --write` and verify with
`python3 scripts/agents/sync_agent_assets.py --check`.

The same rule applies to skills: edit `.agents/skills/*/SKILL.md` and let
the sync script mirror it into `.claude/skills/`.

There is a single agent runtime in this repository today (Claude Code). The
source/adapter split is kept anyway so role and skill definitions stay
reviewable independent of any one tool's frontmatter quirks, and so a
second runtime could be added later without rewriting the team.

The sync script fails closed if a role under `.agents/agents/` has no
matching entry in `ROLE_POLICIES` (it needs to know that role's Claude tool
allowlist before it can generate an adapter). It removes only previously
generated adapters — files carrying its generated-marker comment; anything
else under `.claude/agents/` or `.claude/skills/` is left in place and
reported instead of touched.
