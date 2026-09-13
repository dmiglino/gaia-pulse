"""Sync .agents/ (source of truth) into .claude/ (Claude Code runtime adapters).

.agents/agents/*.md and .agents/skills/*/SKILL.md are the portable, tool-
neutral definitions of GaiaPulse's agent team. This script mirrors them into
.claude/agents/*.md and .claude/skills/*/SKILL.md, adding the Claude-specific
``tools:`` allowlist (from ROLE_POLICIES) and a generated-file marker.

Usage:
    python3 scripts/agents/sync_agent_assets.py --write   # regenerate .claude/
    python3 scripts/agents/sync_agent_assets.py --check   # verify .claude/ is up to date
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_SRC = REPO_ROOT / ".agents" / "agents"
SKILLS_SRC = REPO_ROOT / ".agents" / "skills"
AGENTS_OUT = REPO_ROOT / ".claude" / "agents"
SKILLS_OUT = REPO_ROOT / ".claude" / "skills"

GENERATED_MARKER = "# Generated from .agents/ by scripts/agents/sync_agent_assets.py; do not edit."
GENERATED_HEADER_PREFIX = f"---\n{GENERATED_MARKER}\n"

# Claude Code tool allowlist per agent role. Every role under .agents/agents/
# must have an entry here, or the sync fails closed.
ROLE_POLICIES: dict[str, str] = {
    "backend": "Read,Edit,Write,Bash,Glob,Grep",
    "nlp-recommendations": "Read,Edit,Write,Bash,Glob,Grep",
    "frontend": "Read,Edit,Write,Bash,Glob,Grep",
    "data-persistence": "Read,Edit,Write,Bash,Glob,Grep",
    "security-privacy": "Read,Bash,Glob,Grep",
    "code-health-qa": "Read,Edit,Write,Bash,Glob,Grep",
    "documentation-steward": "Read,Edit,Write,Bash,Glob,Grep",
    # No Write: the instruction steward edits existing rulebooks and skills, and a new
    # agent or skill file is a change to the team itself — that goes through the user.
    "instruction-steward": "Read,Edit,Bash,Glob,Grep",
    "integrator": "Read,Edit,Write,Bash,Glob,Grep",
}

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class SyncError(Exception):
    pass


def parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SyncError(f"{path.relative_to(REPO_ROOT)}: missing frontmatter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, text[match.end() :]


def build_agent_adapter(name: str, fields: dict[str, str], body: str, path: Path) -> str:
    if name not in ROLE_POLICIES:
        raise SyncError(
            f"{path.relative_to(REPO_ROOT)}: no ROLE_POLICIES entry for agent "
            f"'{name}'. Add one to scripts/agents/sync_agent_assets.py before syncing."
        )
    if "description" not in fields:
        raise SyncError(f"{path.relative_to(REPO_ROOT)}: frontmatter missing 'description'")
    header = (
        f"{GENERATED_HEADER_PREFIX}"
        f"name: {name}\n"
        f"description: {fields['description']}\n"
        f"tools: {ROLE_POLICIES[name]}\n---\n"
    )
    return header + body


def build_skill_adapter(fields: dict[str, str], body: str, path: Path) -> str:
    if "name" not in fields or "description" not in fields:
        raise SyncError(f"{path.relative_to(REPO_ROOT)}: frontmatter missing 'name'/'description'")
    header = (
        f"{GENERATED_HEADER_PREFIX}"
        f"name: {fields['name']}\n"
        f"description: {fields['description']}\n---\n"
    )
    return header + body


def collect_agent_outputs() -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    for src in sorted(AGENTS_SRC.glob("*.md")):
        name = src.stem
        fields, body = parse_frontmatter(src.read_text(), src)
        outputs[AGENTS_OUT / f"{name}.md"] = build_agent_adapter(name, fields, body, src)
    return outputs


def collect_skill_outputs() -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    for src in sorted(SKILLS_SRC.glob("*/SKILL.md")):
        fields, body = parse_frontmatter(src.read_text(), src)
        outputs[SKILLS_OUT / src.parent.name / "SKILL.md"] = build_skill_adapter(fields, body, src)
    return outputs


def is_generated(path: Path) -> bool:
    try:
        return path.read_text().startswith(GENERATED_HEADER_PREFIX)
    except OSError:
        return False


def existing_generated_files(out_dir: Path, pattern: str) -> set[Path]:
    if not out_dir.exists():
        return set()
    return {path for path in out_dir.glob(pattern) if path.is_file() and is_generated(path)}


def run(write: bool) -> int:
    try:
        outputs = {**collect_agent_outputs(), **collect_skill_outputs()}
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    generated = existing_generated_files(AGENTS_OUT, "*.md") | existing_generated_files(
        SKILLS_OUT, "*/SKILL.md"
    )
    stale = sorted(generated - set(outputs))

    mismatches = []
    for path, content in sorted(outputs.items()):
        if path.exists() and path.read_text() == content:
            continue
        mismatches.append(path)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    if write:
        for path in stale:
            path.unlink()
        for path in mismatches:
            print(f"wrote {path.relative_to(REPO_ROOT)}")
        for path in stale:
            print(f"removed stale {path.relative_to(REPO_ROOT)}")
        return 0

    if mismatches or stale:
        for path in mismatches:
            print(f"out of date: {path.relative_to(REPO_ROOT)}")
        for path in stale:
            print(f"stale (would remove): {path.relative_to(REPO_ROOT)}")
        print(
            "\nRun `python3 scripts/agents/sync_agent_assets.py --write` to fix.",
            file=sys.stderr,
        )
        return 1

    print("agents/skills adapters are up to date.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="Write adapters into .claude/")
    group.add_argument("--check", action="store_true", help="Verify .claude/ is up to date")
    args = parser.parse_args()
    return run(write=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
