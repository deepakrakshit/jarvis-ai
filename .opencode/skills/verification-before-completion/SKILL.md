---
name: verification-before-completion
description: Use before claiming a fix, test, build, or requirement is complete; require fresh command evidence.
---

# Verification Before Completion

For every claim, identify the command that proves it, run the full relevant
command now, read its exit status and output, and report exactly what it proves.

- A focused test proves only that behavior and its direct boundary.
- The full suite is required before claiming repository-wide safety.
- Lint, formatting, typing, syntax, build, and browser checks are separate
  claims; do not substitute one for another.
- Missing tools, unavailable hardware, skipped tests, and external services are
  gaps, not passes.
- Verify delegated work independently from the diff and fresh checks.

Always include the exact commands, working directory, pass/fail/skip counts, and
the remaining limitation in the handoff.

Source: `obra/superpowers`, `skills/verification-before-completion/SKILL.md`,
commit `bb92a77741419a4ab5f06e711a283343f1ada0c3`.
