---
name: code-review
description: Use after each meaningful implementation unit and before accepting a branch; find correctness, security, compatibility, and test gaps.
---

# Code Review

Read the actual diff, surrounding callers, requirements, and tests. Review in
this order:

1. Does the implementation satisfy the observable requirement?
2. Can malformed, unauthorized, concurrent, cancelled, or platform-limited
   inputs bypass the intended boundary?
3. Does error handling preserve ownership, cleanup, and truthful state?
4. Do tests prove behavior rather than only implementation details?
5. Are documentation, configuration, lockfiles, and runtime assumptions aligned?

Report findings with severity, exact file and line, impact, and a concrete fix or
test. Do not report style preferences as defects. Do not change files during a
review unless the request explicitly includes remediation.

Source: `obra/superpowers`, `skills/requesting-code-review/SKILL.md`, commit
`bb92a77741419a4ab5f06e711a283343f1ada0c3`.
