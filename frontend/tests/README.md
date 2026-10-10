# Completion-boundary checks

Run `node --test frontend/tests/signal-orb.test.cjs` from the repository root.
The dependency-free harness evaluates the actual component, config and application
coordinator with synthetic Gateway events, a controlled clock, and isolated audio/canvas.
It covers configured tool completion, immediate speech, interruption, reduced motion,
short/zero settlement windows, repeated state assignment and disconnection.
It does not exercise real audio, WebSocket transport, GPU output or user comprehension.

For the real renderer only, serve `frontend` with a static server and open
`tests/orb-settlement.html`. This preview never loads `app.js`, requests microphone
access, or connects to a Gateway. Use system reduced motion for the static variant.
`settleMs` configures completion settlement (default 650 ms; zero is immediate).
The application clamps it to `doneMs` and leaves existing visibility/event timers
authoritative. New states, speech, reduced-motion changes and disconnection cancel
pending settlement. Other state transitions retain their existing blend.

Live voice, reconnect, timing on target devices, and the perceived clarity of the
completion moment still require maintainer review before merge.
