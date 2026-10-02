/**
 * Gateway WebSocket Protocol Client for JARVIS.
 * Handles challenge handshake, continuous audio/text streaming, and event dispatching.
 */

import { CONFIG } from '../config.js';

export class GatewayClient {
  constructor({ url, sessionId, onStatusChange }) {
    this.url = url;
    this.sessionId = sessionId;
    this.onStatusChange = onStatusChange;
    this.ws = null;
    this.reconnectTimer = null;
    this.isConnected = false;
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
        if (typeof this.onStatusChange === 'function') {
          this.onStatusChange('connected', 'JARVIS OPERATIONAL');
        }
        this.emit('open');
      };

      this.ws.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data);
          this.handleFrame(frame);
        } catch (err) {
          console.error('Frame parse error:', err);
        }
      };

      this.ws.onclose = () => {
        this.isConnected = false;
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

  handleFrame(frame) {
    if (!frame) return;

    // 1. Handshake Challenge Protocol
    if (frame.type === 'event' && frame.event === 'connect.challenge') {
      this.send({
        type: 'req',
        id: 'req-handshake',
        method: 'connect',
        params: { client_id: 'jarvis-neural-ui', session_id: this.sessionId }
      });
      return;
    }

    // 2. Dispatch custom event handlers
    if (frame.type === 'event' && frame.event) {
      this.emit(frame.event, frame.payload || {});
      return;
    }

    // 3. Fallback generic frame emit
    this.emit('frame', frame);
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
