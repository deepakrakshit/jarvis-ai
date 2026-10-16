# Local Engineering Skills

These project-local skills are intentionally small, source-attributed adapters
for OmniRush/OpenCode. They are discoverable from `.opencode/skills/` and do not
require Cloud sign-in or a global install.

| Skill | Local path | Source focus |
| --- | --- | --- |
| TDD | `.opencode/skills/tdd/SKILL.md` | Superpowers test-first workflow |
| Systematic debugging | `.opencode/skills/systematic-debugging/SKILL.md` | Superpowers root-cause workflow |
| Security review | `.opencode/skills/security-review/SKILL.md` | Trail of Bits boundary review |
| Post-patch validation | `.opencode/skills/post-patch-validation/SKILL.md` | Trail of Bits adversarial evidence |
| Code review | `.opencode/skills/code-review/SKILL.md` | Superpowers review checklist |
| Verification before completion | `.opencode/skills/verification-before-completion/SKILL.md` | Superpowers evidence rules |
| Frontend design review | `.opencode/skills/frontend-design-review/SKILL.md` | Vercel, Impeccable, and Taste UI guidance |

The source repositories and immutable commits are recorded in each skill file.
The complete upstream plugins are not copied into this repository, so these
local adapters remain portable and do not silently depend on missing upstream
scripts or private credentials.

To verify discovery in OmniRush, open **Settings → Library** (the app's
Extensions view) and inspect the project-local skills, or ask the native skill
tool to load one by name.
