/**
 * Dynamic Runtime Configuration for JARVIS Web Interface.
 */

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
};

export function getGatewayUrl() {
  const urlParams = new URLSearchParams(window.location.search);
  const explicitGwPort = urlParams.get('gw') || String(CONFIG.GATEWAY.DEFAULT_PORT);
  return urlParams.get('ws') || `ws://127.0.0.1:${explicitGwPort}`;
}

export function generateSessionId() {
  return 'SESSION-' + Math.random().toString(36).substring(2, 10).toUpperCase();
}
