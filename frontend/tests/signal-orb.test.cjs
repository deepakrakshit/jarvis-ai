// Dependency-free regressions using the actual component and application coordinator.
// Audio, Gateway and canvas are isolated; this never opens a microphone or socket.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(query = '') {
  let now = 1000, nextId = 0, Orb, gateway;
  const timers = new Map(), frames = new Map();
  const media = { matches: false, addEventListener(_, fn) { this.change = fn; }, removeEventListener() {} };
  class Element {
    constructor() { this.attrs = new Map(); this.classList = { add() {}, remove() {}, toggle() {} }; this.value = ''; }
    getAttribute(k) { return this.attrs.get(k) ?? null; }
    hasAttribute(k) { return this.attrs.has(k); }
    setAttribute(k, v) {
      const old = this.getAttribute(k); this.attrs.set(k, String(v));
      if (this.constructor.observedAttributes?.includes(k)) this.attributeChangedCallback(k, old, String(v));
    }
    addEventListener() {}
    getBoundingClientRect() { return { width: 100 }; }
  }
  class Player { isPlaying() { return !!this.playing; } stop() { this.playing = false; } playPcmChunk() { this.playing = true; } }
  class Recorder { constructor() { this.isActive = false; } }
  class Gateway {
    constructor() { gateway = this; this.events = new Map(); }
    on(name, fn) { this.events.set(name, fn); }
    connect() {}
    emit(name, value = {}) { this.events.get(name)(value); }
  }
  const elements = new Map();
  const sandbox = {
    HTMLElement: Element, customElements: { get() {}, define(_, cls) { Orb = cls; } },
    matchMedia: () => media, ResizeObserver: class { observe() {} disconnect() {} }, devicePixelRatio: 1,
    performance: { now: () => now }, Date: { now: () => now }, URLSearchParams,
    requestAnimationFrame: fn => { frames.set(++nextId, fn); return nextId; },
    cancelAnimationFrame: id => frames.delete(id),
    setTimeout: (fn, ms) => { timers.set(++nextId, { fn, due: now + ms }); return nextId; },
    clearTimeout: id => timers.delete(id),
    window: { location: { search: query, hostname: 'localhost' }, addEventListener() {} },
    navigator: { platform: 'test', userAgent: 'test' },
    document: { getElementById: id => elements.get(id) ?? null, addEventListener() {},
      documentElement: { style: { setProperty() {} } }, body: { dataset: {} } },
    AudioPlayer: Player, AudioRecorder: Recorder, GatewayClient: Gateway,
  };
  const context = vm.createContext(sandbox);
  const source = rel => fs.readFileSync(path.join(__dirname, '..', 'js', rel), 'utf8');
  vm.runInContext(source('components/signal-orb.js'), context);
  const orb = new Orb();
  orb.shadowRoot = {}; orb.canvas = {}; orb.dots = {}; orb.ctx = {};
  orb.paint = function(t, weights) { this.painted = { t, weights: Array.from(weights) }; };
  orb.connectedCallback();
  elements.set('signal-orb', orb);
  elements.set('mode-label', new Element());
  vm.runInContext(source('config.js').replaceAll('export ', ''), context);
  vm.runInContext(source('app.js').replace(/^import .*;\r?\n/gm, ''), context);
  const advance = ms => {
    const end = now + ms;
    while (true) {
      const pending = [...timers].filter(([, t]) => t.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
      if (!pending) break;
      const [id, t] = pending; now = t.due; timers.delete(id); t.fn();
    }
    now = end;
    const scheduled = [...frames];
    for (const [id, fn] of scheduled) { frames.delete(id); fn(now); }
  };
  const mode = () => elements.get('mode-label').textContent;
  const complete = () => { gateway.emit('live.tool_call', { name: 'web_search' }); advance(800); gateway.emit('live.tool_complete', { name: 'web_search' }); advance(0); };
  return { orb, gateway, media, advance, mode, complete, timers, frames };
}

test('tool completion settles exactly within the configured done visibility window', () => {
  const h = harness('?settleMs=650&doneMs=1200'); h.complete();
  assert.equal(h.mode(), 'Done'); assert.ok(h.orb.settlement);
  h.advance(325); assert.ok(h.orb.weights[3] > 0 && h.orb.weights[3] < 1);
  h.advance(325); assert.equal(h.orb.weights[3], 1); assert.equal(h.orb.settlement, null);
  h.advance(549); assert.equal(h.mode(), 'Done');
  h.advance(1); assert.equal(h.mode(), 'Listening');
});

test('immediate speech cancels settlement and the old done expiry', () => {
  const h = harness(); h.complete(); h.advance(100);
  h.gateway.emit('live.text', { chunk: 'Synthetic public response' });
  assert.equal(h.mode(), 'Speaking'); assert.equal(h.orb.settlement, null);
  h.advance(1500); assert.equal(h.mode(), 'Speaking');
});

test('interruption during settlement returns to listening without delayed completion', () => {
  const h = harness(); h.complete(); h.advance(100); h.gateway.emit('live.interrupted');
  assert.equal(h.mode(), 'Listening'); assert.equal(h.orb.settlement, null);
  h.advance(1500); assert.equal(h.mode(), 'Listening'); assert.ok(h.orb.weights[3] < 1);
});

test('audio completion preserves the existing speech delay while cancelling pending settlement', () => {
  const h = harness(); h.complete(); h.gateway.emit('live.audio', { pcm: 'synthetic' });
  assert.equal(h.mode(), 'Done'); h.advance(599); assert.equal(h.mode(), 'Done');
  h.advance(1); assert.equal(h.mode(), 'Speaking'); assert.equal(h.orb.settlement, null);
  h.advance(1500); assert.equal(h.mode(), 'Speaking');
});

test('reduced motion cancels active settlement and paints the static result immediately', () => {
  const h = harness(); h.complete(); h.advance(100);
  h.media.matches = true; h.media.change();
  assert.equal(h.orb.settlement, null); assert.equal(h.orb.weights[3], 1);
  assert.equal(h.orb.painted.t, 0); assert.equal(h.frames.size, 0);
  h.gateway.emit('live.interrupted'); assert.equal(h.orb.painted.weights[0], 1);
});

test('short visibility clamps settlement; zero duration completes without waiting', () => {
  const h = harness('?settleMs=900&doneMs=200'); h.complete();
  assert.equal(h.orb.settleMs, 200); h.advance(199); assert.equal(h.mode(), 'Done');
  h.advance(1); assert.equal(h.mode(), 'Listening'); assert.equal(h.orb.settlement, null);
  const instant = harness('?settleMs=0'); instant.complete();
  assert.equal(instant.orb.weights[3], 1); assert.equal(instant.orb.settlement, null);
});

test('new state, repeated done, and disconnect do not leave a pending settlement', () => {
  const h = harness(); h.complete(); h.advance(100);
  const initial = h.orb.settlement;
  h.orb.setAttribute('state', 'done'); assert.equal(h.orb.settlement, initial);
  h.orb.setAttribute('state', 'thinking'); assert.equal(h.orb.settlement, null);
  h.orb.setAttribute('state', 'done'); assert.ok(h.orb.settlement);
  h.orb.disconnectedCallback(); assert.equal(h.orb.settlement, null);
});

test('completion under existing reduced motion is static; standalone default keeps legacy blending', () => {
  const h = harness(); h.media.matches = true; h.media.change(); h.complete();
  assert.equal(h.orb.settlement, null); assert.equal(h.orb.weights[3], 1);
  assert.equal(h.orb.painted.t, 0);
  const standalone = harness(); standalone.orb.attrs.delete('settle-ms'); standalone.complete();
  assert.equal(standalone.orb.settlement, null); assert.ok(standalone.orb.weights[3] < 1);
});
