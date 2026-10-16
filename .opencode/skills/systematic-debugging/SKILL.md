---
name: systematic-debugging
description: Use for any failing test, build, integration, or unexpected runtime behavior before proposing a fix.
---

# Systematic Debugging

Do not guess at fixes. First:

1. Capture the exact command, traceback, environment, and reproduction.
2. Trace the failing value through callers until its origin is known.
3. Compare the broken path with a working sibling path or reference contract.
4. State one falsifiable root-cause hypothesis.
5. Test that hypothesis with the smallest safe experiment.
6. Add a regression test, apply one focused fix, and rerun the original check.

Keep unrelated cleanup out of the repair. If repeated fixes expose shared-state
or ownership problems, stop and reassess the boundary instead of stacking more
patches. Report environmental limits separately from product failures.

Source: `obra/superpowers`, `skills/systematic-debugging/SKILL.md`, commit
`bb92a77741419a4ab5f06e711a283343f1ada0c3`.
