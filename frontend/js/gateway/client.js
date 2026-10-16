/**
 * Gateway WebSocket Protocol Client for JARVIS.
 * Handles challenge handshake, continuous audio/text streaming, and event dispatching.
 */

import { CONFIG } from '../config.js';

export class GatewayClient {
  constructor({ url, sessionId, authToken, onStatusChange }) {
    this.url = url;
    this.sessionId = sessionId;
    this.authToken = authToken || null;
    this.onStatusChange = onStatusChange;
    this.ws = null;
    this.reconnectTimer = null;
    this.isConnected = false;
    this.isAuthenticated = false;
    this.handlers = new Map();
  }

  on(eventName, handler) {
    if (!this.handlers.has(eventName)) {
      this.handlers.set(eventName, []);
    }
    this.handlers.get(eventName).push(handler);
    return () => {
      const list = this.handlers.get(eventName) || [];
      const idx = list.indexOf(handler);
      if (idx !== -1) list.splice(idx, 1);
    };
  }

  emit(eventName, payload) {
    const list = this.handlers.get(eventName) || [];
    for (const h of list) {
      try {
        h(payload);
      } catch (err) {
        console.error(`Error in event handler for '${eventName}':`, err);
      }
    }
  }

  connect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) {
      try { this.ws.close(); } catch (e) {}
      this.ws = null;
    }

    if (typeof this.onStatusChange === 'function') {
      this.onStatusChange('connecting', 'CONNECTING...');
    }

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.isConnected = true;
        this.isAuthenticated = false;
        if (typeof this.onStatusChange === 'function') {
          this.onStatusChange('connecting', 'AUTHENTICATING...');
        }
        this.emit('open');
      };

      this.ws.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data);
          this.handleFrame(frame).catch((err) => {
            console.error('Gateway frame handling error:', err);
            if (typeof this.onStatusChange === 'function') {
              this.onStatusChange('error', 'AUTHENTICATION FAILED');
            }
            try { this.ws?.close(); } catch (e) {}
          });
        } catch (err) {
          console.error('Frame parse error:', err);
        }
      };

      this.ws.onclose = () => {
        this.isConnected = false;
        this.isAuthenticated = false;
        if (typeof this.onStatusChange === 'function') {
          this.onStatusChange('disconnected', 'STANDALONE');
        }
        this.emit('close');
        this.reconnectTimer = setTimeout(() => this.connect(), CONFIG.GATEWAY.RECONNECT_INTERVAL_MS);
      };

      this.ws.onerror = () => {
        if (this.ws) {
          try { this.ws.close(); } catch (e) {}
        }
      };
    } catch (err) {
      this.reconnectTimer = setTimeout(() => this.connect(), CONFIG.GATEWAY.RECONNECT_INTERVAL_MS);
    }
  }

  async handleFrame(frame) {
    if (!frame) return;

    // 1. Handshake Challenge Protocol
    if (frame.type === 'event' && frame.event === 'connect.challenge') {
      const clientId = 'jarvis-neural-ui';
      const sessionId = this.sessionId;
      const authToken = await this.resolveAuthToken();
      if (!authToken) {
        throw new Error('Gateway authentication token is unavailable');
      }
      const challengeResponse = await createChallengeResponse(
        frame.payload?.nonce || '',
        clientId,
        sessionId,
        authToken,
      );
      this.send({
        type: 'req',
        id: 'req-handshake',
        method: 'connect',
        params: {
          client_id: clientId,
          session_id: sessionId,
          challenge_response: challengeResponse,
        }
      });
      return;
    }

    // 2. Dispatch custom event handlers
    if (frame.type === 'event' && frame.event) {
      this.emit(frame.event, frame.payload || {});
      return;
    }

    if (frame.type === 'res' && frame.ok && frame.payload?.type === 'hello-ok') {
      this.isAuthenticated = true;
      if (typeof this.onStatusChange === 'function') {
        this.onStatusChange('connected', 'JARVIS OPERATIONAL');
      }
      this.emit('authenticated', frame.payload);
      return;
    }

    // 3. Fallback generic frame emit
    this.emit('frame', frame);
  }

  async resolveAuthToken() {
    if (this.authToken) return this.authToken;

    if (typeof window !== 'undefined') {
      try {
        const response = await fetch('/__jarvis_gateway_token', {
          cache: 'no-store',
          credentials: 'same-origin',
        });
        if (response.ok) {
          const payload = await response.json();
          if (typeof payload.token === 'string' && payload.token) {
            this.authToken = payload.token;
          }
        }
      } catch (err) {
        console.error('Gateway token bootstrap failed:', err);
      }
    }

    return this.authToken;
  }

  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  sendLiveAudio(pcmBase64) {
    return this.send({
      type: 'req',
      method: 'live.audio_chunk',
      params: { pcm: pcmBase64 }
    });
  }

  sendLiveText(text) {
    return this.send({
      type: 'req',
      id: 'live-' + Math.random().toString(36).substring(2, 9),
      method: 'live.send',
      params: {
        text,
        session_id: this.sessionId
      }
    });
  }
}

async function createChallengeResponse(nonce, clientId, sessionId, authToken) {
  if (!globalThis.crypto?.subtle) {
    throw new Error('Web Crypto API is unavailable');
  }

  const encoder = new TextEncoder();
  const key = await globalThis.crypto.subtle.importKey(
    'raw',
    encoder.encode(authToken),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const message = encoder.encode(`${nonce}\0${clientId}\0${sessionId}`);
  const signature = await globalThis.crypto.subtle.sign('HMAC', key, message);
  return Array.from(new Uint8Array(signature), (byte) => byte.toString(16).padStart(2, '0')).join('');
}
