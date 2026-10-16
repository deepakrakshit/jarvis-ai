---
name: post-patch-validation
description: Use after a security patch exists and before describing it as fixed; validate the original failure, variants, preserved behavior, and regressions.
---

# Post-Patch Validation

Build evidence around the patch:

1. Reproduce the original unsafe behavior against the vulnerable baseline.
2. Run the same assertion against the patched code.
3. Exercise at least one distinct root-cause variant.
4. Check unaffected behavior, teardown, error paths, and the relevant project
   suite.
5. Preserve exact commands, outputs, evidence level, and unresolved gaps.

A nonzero exit is not proof that an exploit ran: distinguish a failed assertion
from an import error, missing dependency, timeout, or broken harness. Do not edit
the patch while validating it; return concrete failures for the next revision.

Source: Trail of Bits `plugins/post-patch-validation`, commit
`5c99c03fb8a9ea568432ecbcba5d86e9f4ac2bf0`.
