## 📌 Description

<!-- Provide a clear and concise summary of the functional capabilities introduced, bugs resolved, or architectural improvements made in this pull request. -->

## 🎯 Motivation & Context

<!-- Why is this change required? What problem does it solve? If it fixes an open issue, link to it using `Fixes #...`. -->

## 🔀 Type of Change

- [ ] `feat`: New functional capability or subsystem enhancement
- [ ] `fix`: Bug fix or defect resolution
- [ ] `refactor`: Code refactoring without behavioral changes
- [ ] `perf`: Performance optimization
- [ ] `docs`: Documentation updates or additions
- [ ] `test`: New tests or test suite improvements
- [ ] `chore`: Maintenance, dependency updates, or build tooling

## 🛡️ Core Invariants & Quality Verification

All pull requests must strictly satisfy the project invariants:

- [ ] **Core Rule 1 (Top-Most Invariant - Avoid Hardcoding):**
  - Configurations, API endpoints, model identifiers, filesystem paths, and timeouts are parameterized, discoverable, or dynamically resolved.
  - OS-agnostic path handling (`pathlib.Path`) used throughout.
- [ ] **Core Rule 2 (Strict Communication Standard):**
  - Conventional Commits strictly adhered to (`feat(...)`, `fix(...)`, etc.).
  - Work documented by functional capability and architectural domain.
- [ ] **Core Rule 3 (Data Safety & Privacy):**
  - Private session logs, keys, `.env`, and database artifacts remain untracked in `.gitignore`.
  - Zero destructive modifications to operator data without explicit authorization.
- [ ] **Core Rule 4 (Engineering Quality Gates):**
  - `pytest` passes with zero failures.
  - `mypy src tests` passes with zero errors.
  - `ruff check .` and `ruff format --check .` pass.

## 🧪 Verification & Testing Performed

<!-- Describe the specific tests executed (unit tests, manual testing, live voice / app automation runs) and provide command outputs or evidence. -->

```bash
# Quality gate validation commands executed:
ruff check .
ruff format --check .
mypy src tests
pytest
```

## 📸 Screenshots / Telemetry (if applicable)

<!-- If this PR modifies the Neural Web UI, terminal console, or visual telemetry, include relevant screenshots or logs. -->
