---
name: frontend-design-review
description: Use when reviewing or changing JARVIS frontend HTML, CSS, or JavaScript for accessibility, interaction quality, and responsive behavior.
---

# Frontend Design Review

Inspect the actual UI files and review observable behavior:

- Keyboard focus, labels, live-region announcements, contrast, reduced motion,
  and touch targets are usable without a mouse.
- Loading, empty, error, reconnecting, and disabled states are visible and
  actionable.
- Layout remains usable on narrow screens and high-DPI displays.
- Network authentication and tokens do not travel in URLs, logs, or markup that
  is not required by the same-origin UI.
- Event listeners, timers, media devices, and WebSockets are cleaned up on
  reconnect and teardown.
- JavaScript syntax and the real browser flow are checked, not inferred from
  static appearance.

For a formal review, fetch the current Vercel Web Interface Guidelines before
checking the files. Use Impeccable and Taste as design references, not as a
reason to replace JARVIS's existing visual language.

Sources:
- `vercel-labs/agent-skills`, `skills/web-design-guidelines/SKILL.md`, commit
  `063bee94c3f4df8453406c830b0a7df0f2860278`.
- `pbakaus/impeccable` and `Leonxlnx/taste-skill` official repositories.
