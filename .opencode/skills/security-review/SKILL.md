---
name: security-review
description: Use when reviewing or changing authentication, authorization, policy, file access, IPC, integrations, or other trust boundaries.
---

# Security Review

Review the complete data flow, not only the named function. Check that:

- Authentication proves possession of the intended secret and rejects replay,
  expiry, malformed identifiers, and untrusted origins.
- Authorization is bound to the authenticated user, session, task, capability,
  and resource; never rely on a client-supplied identity alone.
- Sensitive reads, writes, delivery, logs, and error paths apply the same policy.
- Shared mutable policy is isolated per session and restored on teardown.
- Wildcard origins, fail-open defaults, implicit verification, and unbounded input
  are rejected.
- Cancellation, timeout, disconnect, and retry paths cannot continue side effects
  after ownership has ended.
- Platform-specific operations fail clearly when the host cannot provide them.

For every finding, name the invariant, the reachable caller, the impact, and a
regression test that would fail if the boundary is weakened.

Source: Trail of Bits `skills` marketplace, especially `insecure-defaults` and
`audit-context-building`, commit `5c99c03fb8a9ea568432ecbcba5d86e9f4ac2bf0`.
