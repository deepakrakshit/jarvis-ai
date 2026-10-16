---
name: tdd
description: Use for every feature, bug fix, or behavior change; prove the desired behavior with a failing test before implementation.
---

# Test-Driven Development

Use a tight red, green, refactor loop:

1. State one observable behavior and its boundary case.
2. Add the smallest test that exercises real code.
3. Run that test and confirm the failure is caused by the missing behavior.
4. Write the smallest production change that makes it pass.
5. Run the focused test, then the project-wide test command.
6. Refactor only while the tests remain green.

Prefer behavior assertions over call-count assertions. Mock only external systems
that cannot run deterministically offline. Keep credentials and live API calls out
of tests. For this repository, use `.venv/bin/python -m pytest` during local work
and `make test` once the reproducible project checks are configured.

Source: `obra/superpowers`, `skills/test-driven-development/SKILL.md`, commit
`bb92a77741419a4ab5f06e711a283343f1ada0c3`.
