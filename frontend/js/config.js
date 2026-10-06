/**
 * Dynamic Runtime Configuration for JARVIS Web Interface.
 * Every tunable can be overridden through URL query parameters.
 */

const params = new URLSearchParams(window.location.search);
const numParam = (key, fallback) => {
  const n = Number(params.get(key));
  return params.has(key) && Number.isFinite(n) ? n : fallback;
};

export const CONFIG = {
  AUDIO: {
    INPUT_SAMPLE_RATE: 16000,
    OUTPUT_SAMPLE_RATE: 24000,
    INPUT_BUFFER_SIZE: 2048,
    SMOOTHING_BUFFER_SEC: 0.025,
    HANGOVER_GUARD_MS: 450,
  },
  GATEWAY: {
    DEFAULT_PORT: 8765,
    RECONNECT_INTERVAL_MS: 4000,
  },
  UI: {
    MIN_SEARCH_VISIBLE_MS: numParam('searchMs', 800),
    DONE_VISIBLE_MS: numParam('doneMs', 1200),
    SHORTCUTS: {
      FOCUS_INPUT: '/',
      TOGGLE_MIC: 'm', // combined with Ctrl / Cmd
    },
  },
  /** Per-mode palette: [primary rgb, secondary rgb]. Shared by CSS tokens and the particle field. */
  THEME: {
    listening: [[255, 185, 105], [255, 226, 190]],
    thinking: [[178, 151, 255], [120, 200, 255]],
    searching: [[97, 219, 249], [190, 245, 255]],
    speaking: [[255, 150, 214], [178, 151, 255]],
    done: [[136, 239, 194], [210, 255, 236]],
  },
  /** Ambient 3D particle field behaviour. */
  PARTICLES: {
    COUNT_DESKTOP: numParam('particles', 5200),
    COUNT_MOBILE: numParam('particles', 2400),
    MIN_COUNT: 900,
    MAX_DPR: numParam('dpr', 2),
    ORBIT_FRACTION: numParam('orbit', 0.22),
    NEAR: 1.2,
    FAR: 42,
    FOCAL: 1.6,
    MOUSE_PARALLAX: numParam('parallax', 1.6),
    TRANSITION_RATE: 2.6,
    PULSE_DECAY: 1.8,
    /** speed: forward drift (world units / s), swirl: vortex rad / s, orbit: halo orbit multiplier. */
    MODES: {
      listening: { speed: 0.9, swirl: 0.04, orbit: 1.0, glow: 0.85 },
      thinking: { speed: 2.4, swirl: 0.55, orbit: 2.2, glow: 1.0 },
      searching: { speed: 7.5, swirl: 0.12, orbit: 3.0, glow: 1.1 },
      speaking: { speed: 1.4, swirl: 0.08, orbit: 1.6, glow: 1.05 },
      done: { speed: 0.6, swirl: -0.06, orbit: 0.8, glow: 0.95 },
    },
  },
};

export function getGatewayUrl() {
  const explicitGwPort = params.get('gw') || String(CONFIG.GATEWAY.DEFAULT_PORT);
  return params.get('ws') || `ws://${window.location.hostname || '127.0.0.1'}:${explicitGwPort}`;
}

export function generateSessionId() {
  return 'SESSION-' + Math.random().toString(36).substring(2, 10).toUpperCase();
}
