/**
 * Ambient 3D Particle Field.
 *
 * A full-viewport WebGL layer that renders two particle populations entirely on the GPU:
 *  - a deep volumetric "warp" tunnel drifting toward the camera with vortex swirl and parallax;
 *  - an orbital halo shell + accretion ring anchored to the orb, reacting to voice level.
 *
 * The CPU only integrates a handful of scalar uniforms per frame, so the field stays cheap
 * even at thousands of points. All behaviour is driven by CONFIG.PARTICLES / CONFIG.THEME.
 */

const VS = `
attribute vec4 a_seed;
attribute float a_kind;
uniform vec2 u_res;
uniform float u_time, u_travel, u_swirl, u_orbit, u_near, u_far, u_focal, u_dpr;
uniform vec2 u_mouse;
uniform float u_parallax;
uniform vec2 u_center;
uniform float u_orbR, u_level, u_pulse, u_glow;
varying float v_alpha;
varying float v_mix;

void main() {
  float rnd = a_seed.w;
  float aspect = u_res.x / u_res.y;
  if (a_kind < 0.5) {
    float span = u_far - u_near;
    float z = u_far - mod(a_seed.z * span + u_travel * (0.55 + 0.9 * rnd), span);
    float r = 0.5 + a_seed.x * 15.0;
    float ang = a_seed.y + u_swirl * (2.2 / (0.6 + r * 0.22));
    vec3 p = vec3(cos(ang) * r * aspect * 0.8, sin(ang) * r * 0.72, z);
    p.xy += vec2(sin(u_time * 0.31 + rnd * 21.0), cos(u_time * 0.27 + rnd * 13.0)) * 0.3;
    p.xy *= 1.0 + u_pulse * 0.45 * (1.0 - z / u_far);
    p.xy -= u_mouse * u_parallax;
    vec2 ndc = p.xy * u_focal / p.z;
    ndc.x /= aspect;
    gl_Position = vec4(ndc, 0.0, 1.0);
    float fadeFar = 1.0 - smoothstep(u_far * 0.55, u_far, z);
    float fadeNear = smoothstep(u_near, u_near + 2.2, z);
    float twinkle = 0.7 + 0.3 * sin(u_time * (1.2 + rnd * 3.4) + rnd * 40.0);
    v_alpha = fadeFar * fadeNear * twinkle * (0.55 + 0.95 * rnd) * u_glow;
    gl_PointSize = clamp(u_dpr * (4.0 + rnd * 8.0) * 2.4 / p.z, 1.0, 16.0 * u_dpr);
  } else {
    float y = 1.0 - 2.0 * a_seed.x;
    float ringK = step(0.55, a_seed.z);
    y *= mix(1.0, 0.06, ringK);
    float rr = sqrt(max(0.0, 1.0 - y * y));
    float th = a_seed.y + u_orbit * (0.5 + rnd * 0.9) * mix(1.0, 1.6, ringK);
    vec3 s = vec3(cos(th) * rr, y, sin(th) * rr);
    float tilt = mix(0.35, 1.18, ringK);
    s = vec3(s.x, s.y * cos(tilt) - s.z * sin(tilt), s.y * sin(tilt) + s.z * cos(tilt));
    float wob = sin(u_time * 5.0 + rnd * 31.0) * u_level * 0.22;
    float rad = mix(1.2 + rnd * 0.5, 1.45 + rnd * 0.35, ringK) + wob + u_pulse * 0.35;
    s *= rad;
    float persp = 3.4 / (3.4 - s.z);
    vec2 px = (s.xy * persp * u_orbR) - u_mouse * u_parallax * 6.0;
    gl_Position = vec4(u_center + vec2(px.x / u_res.x * 2.0, -px.y / u_res.y * 2.0), 0.0, 1.0);
    float depth = clamp((s.z + 1.9) / 3.8, 0.0, 1.0);
    v_alpha = (0.12 + 0.6 * depth) * (0.45 + 0.55 * rnd) * u_glow * (0.65 + u_level * 0.8);
    gl_PointSize = u_dpr * (0.9 + depth * 2.4) * (0.75 + rnd * 0.6);
  }
  v_mix = rnd;
}`;

const FS = `
precision mediump float;
uniform vec3 u_c1, u_c2;
varying float v_alpha;
varying float v_mix;
void main() {
  float r = length(gl_PointCoord - 0.5) * 2.0;
  if (r > 1.0) discard;
  float core = exp(-r * r * 7.0);
  float halo = (1.0 - r) * 0.28;
  float a = (core + halo) * v_alpha;
  vec3 col = mix(u_c1, u_c2, v_mix);
  gl_FragColor = vec4(col * a + vec3(core * core * 0.4 * v_alpha), a);
}`;

const UNIFORMS = [
  'u_res', 'u_time', 'u_travel', 'u_swirl', 'u_orbit', 'u_near', 'u_far', 'u_focal', 'u_dpr',
  'u_mouse', 'u_parallax', 'u_center', 'u_orbR', 'u_level', 'u_pulse', 'u_glow', 'u_c1', 'u_c2',
];

const approach = (current, target, rate, dt) => current + (target - current) * (1 - Math.exp(-rate * dt));

export class ParticleField {
  constructor(canvas, { particles, theme }) {
    this.canvas = canvas;
    this.cfg = particles;
    this.theme = theme;
    this.mode = 'listening';
    this.levelSource = () => 0;
    this.anchor = null;
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)');
    this.coarse = matchMedia('(pointer: coarse)').matches;

    const m = this.cfg.MODES[this.mode];
    this.p = { speed: m.speed, swirl: m.swirl, orbit: m.orbit, glow: m.glow };
    const [c1, c2] = this.theme[this.mode];
    this.c1 = c1.map((v) => v / 255);
    this.c2 = c2.map((v) => v / 255);
    this.travel = 0;
    this.swirl = 0;
    this.orbit = 0;
    this.time = 0;
    this.level = 0;
    this.pulseAmt = 0;
    this.mouse = [0, 0];
    this.mouseTarget = [0, 0];
    this.center = [0, 0.1];
    this.orbR = 120;
    this.last = 0;
    this.slow = 0;
    this.frame = 0;

    this.capacity = this.coarse ? this.cfg.COUNT_MOBILE : this.cfg.COUNT_DESKTOP;
    this.drawCount = this.capacity;

    if (!this.init()) {
      canvas.hidden = true;
      return;
    }
    this.bind();
  }

  init() {
    const gl = this.canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false });
    if (!gl) return false;
    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn('ParticleField shader:', gl.getShaderInfoLog(s));
      return s;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    gl.useProgram(prog);

    const n = this.capacity;
    const data = new Float32Array(n * 5);
    const golden = 0.6180339887;
    for (let i = 0; i < n; i++) {
      const o = i * 5;
      const orbit = ((i * golden) % 1) < this.cfg.ORBIT_FRACTION;
      data[o] = orbit ? Math.random() : Math.pow(Math.random(), 0.75);
      data[o + 1] = Math.random() * Math.PI * 2;
      data[o + 2] = Math.random();
      data[o + 3] = Math.random();
      data[o + 4] = orbit ? 1 : 0;
    }
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    const aSeed = gl.getAttribLocation(prog, 'a_seed');
    const aKind = gl.getAttribLocation(prog, 'a_kind');
    gl.enableVertexAttribArray(aSeed);
    gl.enableVertexAttribArray(aKind);
    gl.vertexAttribPointer(aSeed, 4, gl.FLOAT, false, 20, 0);
    gl.vertexAttribPointer(aKind, 1, gl.FLOAT, false, 20, 16);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);

    this.gl = gl;
    this.u = Object.fromEntries(UNIFORMS.map((k) => [k, gl.getUniformLocation(prog, k)]));
    gl.uniform1f(this.u.u_near, this.cfg.NEAR);
    gl.uniform1f(this.u.u_far, this.cfg.FAR);
    gl.uniform1f(this.u.u_focal, this.cfg.FOCAL);
    return true;
  }

  bind() {
    this.onResize = () => {
      this.dpr = Math.min(window.devicePixelRatio || 1, this.cfg.MAX_DPR);
      const w = Math.max(1, Math.round(window.innerWidth * this.dpr));
      const h = Math.max(1, Math.round(window.innerHeight * this.dpr));
      if (this.canvas.width !== w || this.canvas.height !== h) {
        this.canvas.width = w;
        this.canvas.height = h;
      }
      this.measureAnchor();
      if (this.reduce.matches) this.render();
    };
    this.onPointer = (e) => {
      this.mouseTarget[0] = (e.clientX / window.innerWidth) * 2 - 1;
      this.mouseTarget[1] = -((e.clientY / window.innerHeight) * 2 - 1);
    };
    this.onMotion = () => {
      cancelAnimationFrame(this.frame);
      this.last = 0;
      if (this.reduce.matches) this.render();
      else this.frame = requestAnimationFrame(this.tick);
    };
    this.canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      cancelAnimationFrame(this.frame);
    });
    this.canvas.addEventListener('webglcontextrestored', () => {
      if (this.init()) this.onMotion();
    });
    window.addEventListener('resize', this.onResize);
    window.addEventListener('pointermove', this.onPointer, { passive: true });
    this.reduce.addEventListener('change', this.onMotion);
  }

  start() {
    if (!this.gl) return this;
    this.onResize();
    this.onMotion();
    return this;
  }

  setAnchor(el) {
    this.anchor = el;
    if (this.anchorObserver) this.anchorObserver.disconnect();
    if (el && 'ResizeObserver' in window) {
      this.anchorObserver = new ResizeObserver(() => this.measureAnchor());
      this.anchorObserver.observe(el);
    }
    this.measureAnchor();
    return this;
  }

  measureAnchor() {
    if (!this.anchor) return;
    const r = this.anchor.getBoundingClientRect();
    if (!r.width) return;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.width / 2; // orb is the square at the top of the viewport
    this.center = [(cx / window.innerWidth) * 2 - 1, -((cy / window.innerHeight) * 2 - 1)];
    this.orbR = r.width * 0.27 * (this.dpr || 1);
  }

  setLevelSource(fn) {
    this.levelSource = typeof fn === 'function' ? fn : () => 0;
    return this;
  }

  setMode(mode) {
    if (!this.cfg.MODES[mode] || mode === this.mode) return;
    this.mode = mode;
    this.pulse(0.6);
    this.measureAnchor();
    if (this.reduce.matches) {
      const [c1, c2] = this.theme[mode];
      this.c1 = c1.map((v) => v / 255);
      this.c2 = c2.map((v) => v / 255);
      this.render();
    }
  }

  pulse(strength = 1) {
    this.pulseAmt = Math.min(1.4, this.pulseAmt + strength);
  }

  tick = (now) => {
    const dt = this.last ? Math.min((now - this.last) / 1000, 0.05) : 0;
    this.last = now;
    this.step(dt);
    this.adapt(dt);
    this.render();
    this.frame = requestAnimationFrame(this.tick);
  };

  step(dt) {
    const rate = this.cfg.TRANSITION_RATE;
    const target = this.cfg.MODES[this.mode];
    for (const k of Object.keys(this.p)) this.p[k] = approach(this.p[k], target[k], rate, dt);
    const [t1, t2] = this.theme[this.mode];
    for (let i = 0; i < 3; i++) {
      this.c1[i] = approach(this.c1[i], t1[i] / 255, rate, dt);
      this.c2[i] = approach(this.c2[i], t2[i] / 255, rate, dt);
    }
    const lvl = Math.max(0, Math.min(1, Number(this.levelSource()) || 0));
    this.level = approach(this.level, lvl, lvl > this.level ? 14 : 4, dt);
    this.pulseAmt = Math.max(0, this.pulseAmt - this.cfg.PULSE_DECAY * dt * this.pulseAmt - 0.05 * dt);
    this.mouse[0] = approach(this.mouse[0], this.mouseTarget[0], 3, dt);
    this.mouse[1] = approach(this.mouse[1], this.mouseTarget[1], 3, dt);
    const boost = 1 + this.level * 1.2 + this.pulseAmt * 2.5;
    this.time += dt;
    this.travel += this.p.speed * boost * dt;
    this.swirl += this.p.swirl * dt;
    this.orbit += 0.32 * this.p.orbit * (1 + this.level) * dt;
  }

  adapt(dt) {
    if (!dt || this.drawCount <= this.cfg.MIN_COUNT) return;
    this.slow = dt > 0.028 ? this.slow + 1 : Math.max(0, this.slow - 1);
    if (this.slow > 45) {
      this.drawCount = Math.max(this.cfg.MIN_COUNT, Math.round(this.drawCount * 0.7));
      this.slow = 0;
    }
  }

  render() {
    const gl = this.gl;
    if (!gl || gl.isContextLost()) return;
    const { width, height } = this.canvas;
    const u = this.u;
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(u.u_res, width, height);
    gl.uniform1f(u.u_time, this.time);
    gl.uniform1f(u.u_travel, this.travel);
    gl.uniform1f(u.u_swirl, this.swirl);
    gl.uniform1f(u.u_orbit, this.orbit);
    gl.uniform1f(u.u_dpr, this.dpr || 1);
    gl.uniform2f(u.u_mouse, this.mouse[0], this.mouse[1]);
    gl.uniform1f(u.u_parallax, this.reduce.matches ? 0 : this.cfg.MOUSE_PARALLAX);
    gl.uniform2f(u.u_center, this.center[0], this.center[1]);
    gl.uniform1f(u.u_orbR, this.orbR);
    gl.uniform1f(u.u_level, this.level);
    gl.uniform1f(u.u_pulse, this.pulseAmt);
    gl.uniform1f(u.u_glow, this.p.glow);
    gl.uniform3fv(u.u_c1, this.c1);
    gl.uniform3fv(u.u_c2, this.c2);
    gl.drawArrays(gl.POINTS, 0, this.drawCount);
  }
}
