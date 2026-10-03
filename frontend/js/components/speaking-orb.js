// Speaking Orb Web Component: 3D Procedural Sphere with Real-Time Particle Typography
(() => {
  if (customElements.get('speaking-orb')) return;
  const names = ['listening', 'thinking', 'searching', 'speaking', 'done'];
  const colors = [[255,185,105],[178,151,255],[97,219,249],[255,150,214],[136,239,194]];
  const SPK = 3;
  const tau = Math.PI * 2;
  const cache = new Map();
  const seedsFor = count => {
    const jitter = count > 1000 ? (1 - 1000 / count) * 1.6 / Math.sqrt(count) : 0;
    const rnd = n => { const x = Math.sin(n * 12.9898) * 43758.5453; return x - Math.floor(x); };
    if (!cache.has(count)) cache.set(count, Array.from({length:count}, (_,i) => {
      const y0 = 1 - 2 * (i + .5) / count, a = i * 2.399963229728653 + (rnd(i) - .5) * jitter * 6;
      const y = Math.max(-1, Math.min(1, y0 + (rnd(i + .37) - .5) * jitter));
      return {x:Math.cos(a)*Math.sqrt(1-y*y), y, z:Math.sin(a)*Math.sqrt(1-y*y), a, u:i/count, sa:Math.sin(a), ca:Math.cos(a)};
    }));
    return cache.get(count);
  };
  const bandTilt = [[.32,0],[-.72,1.02],[1.08,-.82]].map(([tilt,rot]) => [Math.cos(tilt+1),Math.sin(tilt+1),Math.cos(rot),Math.sin(rot)]);
  const autoCount = () => matchMedia('(pointer: coarse)').matches ? 6000 : 12000;
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const ease = p => p * p * (3 - 2 * p);

  const syl = w => Math.max(1, (w.toLowerCase().replace(/[^a-z]/g, '').replace(/e$/, '').match(/[aeiouy]+/g) || []).length);
  const pulseEnv = (words, t) => {
    let e = 0;
    for (const w of words) {
      if (t < w.start - .05 || t > w.end + .15) continue;
      const n = syl(w.w), len = Math.max(.08, w.end - w.start), k = clamp((t - w.start) / len) * n;
      const within = k - Math.floor(k);
      e = Math.max(e, (.55 + .45 * Math.sin(Math.PI * clamp(within * 1.15))) * clamp((t - w.start + .05) / .05) * clamp((w.end + .15 - t) / .15));
    }
    return e;
  };

  const bestVoice = () => {
    const vs = (speechSynthesis.getVoices() || []).filter(v => /^en/i.test(v.lang));
    const score = v => (/natural|neural|online/i.test(v.name) ? 4 : 0) + (/google/i.test(v.name) ? 3 : 0)
      + (/ava|aria|jenny|samantha|emma|sonia|libby|daniel|serena/i.test(v.name) ? 2 : 0) + (/en-US|en-GB/i.test(v.lang) ? 1 : 0) - (v.localService === false ? 0 : .5);
    return vs.sort((a, b) => score(b) - score(a))[0] || null;
  };
  if ('speechSynthesis' in window) speechSynthesis.getVoices();

  const groupsOf = words => {
    const out = []; let cur = [];
    words.forEach((w, i) => { cur.push(i); if (/[.!?]$/.test(w.w) || cur.length >= 4 && /[,;:]$/.test(w.w) || cur.length >= 8) { out.push(cur); cur = []; } });
    if (cur.length) out.push(cur);
    return out;
  };

  const VS = `attribute vec2 p;attribute vec3 d;uniform vec2 res;varying float a;varying float s;
    void main(){float size=max(d.x,2.0);float k=min(1.0,d.x*d.x/(size*size));a=d.y*k;s=d.z*k;
    gl_Position=vec4(p/res*2.0-1.0,0.0,1.0)*vec4(1,-1,1,1);gl_PointSize=size;}`;
  const FS = `precision mediump float;uniform vec3 col;varying float a;varying float s;
    void main(){float r=length(gl_PointCoord-.5)*2.0;if(r>1.0)discard;
    float dot=mix(1.0,.065,smoothstep(.17,.23,r))*(1.0-smoothstep(.9,1.0,r));
    float sp=mix(1.0,.065,smoothstep(.08,.12,r))*(1.0-smoothstep(.17,.2,r));
    gl_FragColor=vec4(col*dot*a+vec3(sp*s),min(1.0,dot*a+sp*s));}`;
  const setupGL = canvas => {
    const gl = canvas.getContext('webgl', {premultipliedAlpha:true, preserveDrawingBuffer:true, antialias:false});
    if (!gl) return null;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
    gl.useProgram(prog);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    const lp = gl.getAttribLocation(prog, 'p'), ld = gl.getAttribLocation(prog, 'd');
    gl.enableVertexAttribArray(lp); gl.enableVertexAttribArray(ld);
    gl.vertexAttribPointer(lp, 2, gl.FLOAT, false, 20, 0); gl.vertexAttribPointer(ld, 3, gl.FLOAT, false, 20, 8);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
    return {gl, res:gl.getUniformLocation(prog, 'res'), col:gl.getUniformLocation(prog, 'col')};
  };
  const sprite = (rgb, core) => {
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const g = c.getContext('2d'), grad = g.createRadialGradient(32,32,0,32,32,32);
    grad.addColorStop(0, `rgba(${rgb},1)`); grad.addColorStop(core, `rgba(${rgb},1)`);
    grad.addColorStop(core + .03, `rgba(${rgb},.065)`); grad.addColorStop(.94, `rgba(${rgb},.065)`); grad.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = grad; g.fillRect(0,0,64,64);
    return c;
  };

  const CSS = `:host{display:block;width:100%;position:relative;--cap:#fff;--cap-dim:rgba(255,255,255,.52)}
    .orb{position:relative;width:100%;aspect-ratio:1}
    canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
    .cap{position:relative;margin:-4% auto 0;width:var(--cap-w,92%);min-height:2.7em;text-align:center;font:600 var(--cap-size,24px)/1.3 var(--cap-font,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif);letter-spacing:-.01em;color:var(--cap);text-wrap:balance}
    .cap.user{--cap:#ffd6a8;font-weight:500}
    .cap span{display:inline-block;white-space:pre;will-change:opacity,transform}
    canvas.fx{pointer-events:none}`;

  class SpeakingOrb extends HTMLElement {
    static get observedAttributes() { return ['state','level','particles']; }
    connectedCallback() {
      if (!this.shadowRoot) {
        this.attachShadow({mode:'open'}).innerHTML = `<style>${CSS}</style><div class="orb"><canvas aria-hidden="true"></canvas><canvas aria-hidden="true"></canvas></div><div class="cap" aria-live="polite"></div><canvas class="fx" aria-hidden="true"></canvas>`;
        const [halo, dots] = this.shadowRoot.querySelectorAll('canvas');
        this.canvas=halo; this.ctx=halo.getContext('2d'); this.dots=dots; this.capEl=this.shadowRoot.querySelector('.cap');this.fx=this.shadowRoot.querySelector('.fx');this.fctx=this.fx.getContext('2d');
        this.gl=setupGL(dots); if(!this.gl) this.dctx=dots.getContext('2d');
      }
      this.media=matchMedia('(prefers-reduced-motion: reduce)');
      this.weights=names.map(n=>n===this.state?1:0); this.time=performance.now()/1000; this.last=performance.now(); this.slow=0; this.env=0; this.onsets=[]; this.line=null;
      this.resize=()=>{const w=this.canvas.parentNode.getBoundingClientRect().width,size=Math.max(1,Math.round(w*Math.min(devicePixelRatio||1,2)));
        this.canvas.width=this.canvas.height=this.dots.width=this.dots.height=size;this.style.setProperty('--cap-size',Math.round(clamp(w*.066,18,34))+'px');
        const hr=this.getBoundingClientRect(),k=Math.min(devicePixelRatio||1,2);this.fx.width=Math.round(hr.width*k);this.fx.height=Math.round(hr.height*k);this.glyphCache=new Map();this.draw(this.time);};
      this.observer=new ResizeObserver(this.resize);this.observer.observe(this);this.resize();
      this.onMotion=()=>{cancelAnimationFrame(this.frame);this.last=performance.now();this.start();};
      this.media.addEventListener('change',this.onMotion);
      this.setAttribute('role','img');this.setAttribute('aria-label',`Assistant ${this.state}`);
      this.start();
    }
    disconnectedCallback(){cancelAnimationFrame(this.frame);this.observer?.disconnect();this.media?.removeEventListener('change',this.onMotion);this.stopMic?.();}
    start(){if(!this.hasAttribute('recording'))this.frame=requestAnimationFrame(this.tick);}
    get state(){const v=this.getAttribute('state');return names.includes(v)?v:'listening';}
    set state(v){this.setAttribute('state',v);}
    get level(){const n=Number(this.getAttribute('level')??'.5');return Number.isFinite(n)?clamp(n):.5;}
    get particles(){const n=parseInt(this.getAttribute('particles'),10);return Number.isFinite(n)?clamp(n,200,20000):(this.auto??=this.gl?autoCount():1000);}
    attributeChangedCallback(){if(this.ctx)this.setAttribute('aria-label',`Assistant ${this.state}`);}

    say(text, {words, audio, voice} = {}) {
      const toks = String(text).trim().split(/\s+/).filter(Boolean);
      const line = {toks, words: [], t0: this.time, live: true, wallClockStart: Date.now(), expectedDuration: 0};
      if (words?.length) line.words = words.map((w, i) => ({w: toks[i] ?? w.w, start: w.start, end: w.end}));
      this.line = line; this.state = 'speaking';
      if (audio) { this.attachAudio(audio); line.audio = audio; audio.currentTime = 0; audio.play?.(); return; }
      if (line.words.length || !('speechSynthesis' in window)) { if (!line.words.length) line.words = this.estimate(toks, 0); return; }
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text), starts = [];
      u.voice = voice || bestVoice(); if (u.voice) u.lang = u.voice.lang;
      let pos = 0; toks.forEach(t => { pos = text.indexOf(t, pos); starts.push(pos); pos += t.length; });
      line.words = this.estimate(toks, 0); line.synth = true;
      line.t0 = this.time + 1e3;
      const go = () => { if (line.t0 > this.time) line.t0 = this.time; };
      u.onstart = go; setTimeout(go, 1500);
      u.onboundary = e => {
        if (e.name && e.name !== 'word') return;
        const i = starts.findIndex((s, k) => e.charIndex >= s && e.charIndex < s + toks[k].length);
        if (i < 0) return;
        const now = this.time - line.t0, shift = now - line.words[i].start;
        for (let k = i; k < line.words.length; k++) {
          const w = line.words[k];
          if (shift > 0 && w.start <= now) { if (k === i) w.end = Math.max(w.end, now + .1); continue; }
          w.start += shift; w.end += shift;
        }
      };
      u.onend = () => { const now = this.time - line.t0; line.words.forEach(w => { if (w.end > now) w.end = now; }); line.done = now; };
      speechSynthesis.speak(u);
    }
    estimate(toks, t) { return toks.map(w => { const d = .09 + .075 * syl(w) + (/[,.!?]$/.test(w) ? .18 : 0), o = {w, start: t, end: t + d - (/[,.!?]$/.test(w) ? .18 : 0)}; t += d; return o; }); }

    streamWords(text) {
      const toks = String(text).trim().split(/\s+/).filter(Boolean);
      if (!toks.length) return;
      if (!this.line || !this.line.live || this.line.after) {
        this.line = {toks: [], words: [], t0: this.time, live: true, wallClockStart: Date.now(), expectedDuration: 0};
        this.state = 'speaking';
      }
      const L = this.line;
      if (!L.wallClockStart) L.wallClockStart = Date.now();
      let lastEnd = L.words.length ? L.words[L.words.length - 1].end : 0;
      const currentLt = this.time - L.t0;
      if (currentLt > lastEnd && L.words.length === 0) {
        lastEnd = currentLt;
      }
      for (const tok of toks) {
        L.toks.push(tok);
        const d = .09 + .075 * syl(tok) + (/[,.!?]$/.test(tok) ? .18 : 0);
        L.words.push({w: tok, start: lastEnd, end: lastEnd + d - (/[,.!?]$/.test(tok) ? .18 : 0)});
        lastEnd += d;
      }
      L.expectedDuration = lastEnd + 1.2;
      delete L.groups;
    }

    finishStream(fullText) {
      // Guard against restarting completed speech
      if (!this.line || !this.line.live || this.line.after) {
        return;
      }
      if (fullText) {
        const toks = String(fullText).trim().split(/\s+/).filter(Boolean);
        if (this.line.words && toks.length > this.line.words.length) {
          const missing = toks.slice(this.line.words.length);
          this.streamWords(missing.join(' '));
        }
      }
      if (this.line && this.line.words.length) {
        this.line.done = this.line.words[this.line.words.length - 1].end;
        this.line.expectedDuration = this.line.done + 1.2;
      }
    }

    finishCurrentLine() {
      if (this.line) {
        this.line.after = true;
      }
      this.state = this.getAttribute('rest') || 'listening';
      if (this.capEl) {
        this.capEl.textContent = '';
        this.capEl.dataset.g = '';
      }
    }

    attachAudio(el) {
      if (this.srcEl === el) return;
      this.actx ??= new (window.AudioContext || window.webkitAudioContext)();
      this.actx.resume?.();
      const an = this.actx.createAnalyser(); an.fftSize = 1024;
      const src = el instanceof AudioNode ? el : this.actx.createMediaElementSource(el);
      src.connect(an); if (!(el instanceof AudioNode)) an.connect(this.actx.destination);
      this.srcEl = el; this.an = an; this.fbuf = new Float32Array(an.fftSize);
    }

    tick=(now)=>{
      if(this.hasAttribute('recording'))return;
      const dt=this.last?(now-this.last)/1000:0.016;
      this.last=now;
      this.time=now/1000;
      const pdt=Math.min(dt,.05);
      if(!this.hasAttribute('particles')&&dt){this.slow=pdt>.025?this.slow+1:0;if(this.slow>30&&this.auto>800){this.auto=Math.max(800,Math.round(this.auto*.6));this.slow=0;}}
      const k=1-Math.exp(-pdt*7);
      this.weights=this.weights.map((w,i)=>w+((names[i]===this.state?1:0)-w)*k);
      const L=this.line;let raw=0,lt=0;
      if(L){lt=L.audio?L.audio.currentTime:this.time-L.t0;
        if(this.an&&L.audio&&!L.audio.paused){this.an.getFloatTimeDomainData(this.fbuf);let s=0;for(const v of this.fbuf)s+=v*v;raw=clamp(Math.sqrt(s/this.fbuf.length)*5);}
        else raw=pulseEnv(L.words,lt);
        const end=L.done??(L.words.at(-1)?.end??0),over=L.audio?L.audio.ended:lt>end+1.2;
        if(over&&this.state==='speaking'&&!L.after){L.after=true;this.state=this.getAttribute('rest')||'listening';this.dispatchEvent(new CustomEvent('end'));}}
      const prev=this.env;this.env+=(raw-this.env)*(raw>this.env?.55:.18);
      if(this.env-prev>.06&&this.time-(this.onsets.at(-1)??-1)>.12)this.onsets.push(this.time);
      this.onsets=this.onsets.filter(o=>this.time-o<1.2);
      if(this.micLevel)this.setAttribute('level',this.micLevel().toFixed(3));
      this.paint(this.media.matches?0:this.time,this.weights,{env:this.media.matches?0:this.env,onsets:this.media.matches?[]:this.onsets});
      this.caption(L?lt:0,L);
      this.frame=requestAnimationFrame(this.tick);
    };
    draw(t){this.paint(t,this.weights||names.map(n=>n===this.state?1:0),{env:this.env||0,onsets:this.onsets||[]});}

    caption(t,L){
      const el=this.capEl;if(!el)return;
      if(!L||!L.words.length){if(el.childElementCount)el.textContent='';el.dataset.g='';return;}
      const W=L.words,G=L.groups??=groupsOf(W);
      let gi=-1;G.forEach((g,i)=>{if(t>=W[g[0]].start-.04)gi=i;});
      const last=W.at(-1).end,fade=clamp(1-(t-last-1.8)/.4);
      if(gi<0||fade<=0){if(el.childElementCount)el.textContent='';el.dataset.g='';return;}
      const key=gi+':'+W.length+':'+(L.who||'');el.classList.toggle('user',L.who==='user');
      if(el.dataset.g!==key){el.dataset.g=key;el.textContent='';
        for(const i of G[gi]){const s=document.createElement('span');s.textContent=W[i].w+' ';el.append(s);}}
      const reduce=this.media?.matches;
      [...el.children].forEach((s,k)=>{
        const w=W[G[gi][k]],fly=!reduce&&L.who!=='user';
        const a=reduce?(t>=w.start?1:0):fly?ease(clamp((t-w.start-.3)/.14)):ease(clamp((t-w.start+.04)/.14));
        const on=t>=w.start-.04&&t<w.end+.12;
        s.style.opacity=(a*fade*(on?1:.6)).toFixed(3);
        s.style.transform=fly?'':`translateY(${((1-a)*.25).toFixed(3)}em)`;
        s.style.color=on?'var(--cap)':'';
      });
      this.flight(t,L.who==='user'||reduce?[]:G[gi].map((i,k)=>[W[i],el.children[k]]));
    }

    glyphs(word,span){
      const cs=getComputedStyle(this.capEl),font=`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`,r=span.getBoundingClientRect();
      const key=word+'|'+font;if(this.glyphCache.has(key))return this.glyphCache.get(key);
      const w=Math.ceil(r.width)+4,h=Math.ceil(r.height);
      if(w<=0||h<=0)return [];
      const c=document.createElement('canvas');c.width=w;c.height=h;
      const g=c.getContext('2d');g.font=font;g.fillStyle='#fff';
      const fs=parseFloat(cs.fontSize)||24;
      const m=g.measureText(word);
      const A=Number.isFinite(m.fontBoundingBoxAscent)?m.fontBoundingBoxAscent:(Number.isFinite(m.actualBoundingBoxAscent)?m.actualBoundingBoxAscent:fs*.8);
      const D=Number.isFinite(m.fontBoundingBoxDescent)?m.fontBoundingBoxDescent:(Number.isFinite(m.actualBoundingBoxDescent)?m.actualBoundingBoxDescent:fs*.2);
      g.fillText(word,0,(h-(A+D))/2+A);
      const d=g.getImageData(0,0,w,h).data,pts=[],step=Math.max(1,fs/22);
      for(let y=0;y<h;y+=step)for(let x=0;x<w;x+=step){const i=((y|0)*w+(x|0))*4+3;if(d[i]>140)pts.push([x,y]);}
      this.glyphCache.set(key,pts);return pts;
    }

    flight(t,items){
      const g=this.fctx,W=this.fx.width,H=this.fx.height;if(!W)return;
      g.setTransform(1,0,0,1,0,0);g.clearRect(0,0,W,H);
      if(!items.length)return;
      const hr=this.getBoundingClientRect(),k=W/hr.width,orb=this.canvas.getBoundingClientRect();
      const cx=orb.left-hr.left+orb.width/2,cy=orb.top-hr.top+orb.height/2,R=orb.width*.25;
      const rnd=n=>{const x=Math.sin(n*12.9898)*43758.5453;return x-Math.floor(x);};
      g.setTransform(k,0,0,k,0,0);g.globalCompositeOperation='lighter';
      const [cr,cg,cb]=colors[SPK],dot=Math.max(1,parseFloat(getComputedStyle(this.capEl).fontSize)/24);
      items.forEach(([w,span],wi)=>{
        const age=t-w.start+.06;if(age<0||age>.72||!span)return;
        const pts=this.glyphs(w.w,span),sr=span.getBoundingClientRect(),ox=sr.left-hr.left,oy=sr.top-hr.top,seed=w.start*977+wi;
        for(let j=0;j<pts.length;j++){
          const r1=rnd(seed+j*1.7),r2=rnd(seed+j*3.1+.3),r3=rnd(seed+j*5.3+.7),del=r1*.12,p=clamp((age-del)/.36);
          if(p<=0)continue;
          const land=clamp((age-del-.34)/.1),al=(1-land)*Math.min(1,p*5);if(al<=0)continue;
          const e=1-Math.pow(1-p,3),a0=Math.PI/2+(r2-.5)*1.5;
          const sx=cx+Math.cos(a0)*R*(.85+.15*r3),sy=cy+Math.sin(a0)*R*(.92+.08*r3);
          const tx=ox+pts[j][0],ty=oy+pts[j][1],mx=(sx+tx)/2+(r3-.5)*R*.9,my=(sy+ty)/2+(r2-.5)*R*.25;
          const x=(1-e)*(1-e)*sx+2*(1-e)*e*mx+e*e*tx,y=(1-e)*(1-e)*sy+2*(1-e)*e*my+e*e*ty;
          const wm=e*e;g.fillStyle=`rgba(${cr+(255-cr)*wm|0},${cg+(255-cg)*wm|0},${cb+(255-cb)*wm|0},${al.toFixed(3)})`;
          const s=dot*(1.4-.5*e);g.fillRect(x-s/2,y-s/2,s,s);
        }
      });
      g.globalCompositeOperation='source-over';
    }

    paint(t,w,{env,onsets}){
      const ctx=this.ctx,size=this.canvas.width;if(!size)return;
      const unit=size/720;ctx.setTransform(unit,0,0,unit,0,0);ctx.clearRect(0,0,720,720);
      const rgb=[0,1,2].map(c=>Math.round(colors.reduce((s,col,i)=>s+col[c]*w[i],0))),color=rgb.join(',');
      const ws=w[SPK],glow=.075+.05*env*ws;
      const halo=ctx.createRadialGradient(360,360,10,360,360,305+25*env*ws);
      halo.addColorStop(0,`rgba(${color},${glow})`);halo.addColorStop(.57,`rgba(${color},${glow*.47})`);halo.addColorStop(1,`rgba(${color},0)`);
      ctx.fillStyle=halo;ctx.fillRect(0,0,720,720);
      const count=this.particles,seeds=seedsFor(count),ring=Math.round(count*.65);
      const fine=Math.max(.5,Math.pow(1000/count,.55)),light=Math.min(1,Math.pow(1000/(count*fine*fine),.35));
      if(!this.buf||this.buf.length<count*5)this.buf=new Float32Array(count*5);
      const buf=this.buf,[w0,w1,w2,w3,w4]=w,spin=t*.23,cs=Math.cos(spin),sn=Math.sin(spin),wave=8+this.level*22;
      const rings=onsets.map(o=>{const a=t-o;return {h:-1.15+a*2.1,g:Math.pow(1-a/1.2,2)};});
      for(let i=0;i<count;i++){
        const p=seeds[i];let vx=0,vy=0,vz=0,lit=0;
        if(w0>.001){
          const x=p.x*cs+p.z*sn,z=p.z*cs-p.x*sn;
          const r=(192+Math.sin(p.y*11-t*3+p.a*.03)*wave+Math.sin(p.a*.17+t*2)*5)*w0;
          vx+=x*r;vy+=p.y*r;vz+=z*r;
        }
        if(w1>.001){
          const theta=p.u*tau*3+t*.57,phi=p.u*tau*8-t*.48,rt=137+41*Math.cos(phi)+24*p.sa;
          const bx=rt*Math.cos(theta),bz=rt*Math.sin(theta),by=65*Math.sin(phi)+17*p.y;
          vx+=bx*w1;vy+=(by*.77-bz*.52)*w1;vz+=(by*.52+bz*.77)*w1;
        }
        if(w2>.001){
          const band=i%3,angle=p.u*tau*5+t*(.7+band*.2),radius=196+11*p.sa;
          const sx=Math.cos(angle)*radius,sy=Math.sin(angle)*radius;
          const sy2=sy*bandTilt[band][0],sz=sy*bandTilt[band][1],cr=bandTilt[band][2],sr=bandTilt[band][3];
          vx+=(sx*cr-sy2*sr)*w2;vy+=(sx*sr+sy2*cr)*w2;vz+=sz*w2;
        }
        if(w3>.001){
          const x=p.x*cs+p.z*sn,z=p.z*cs-p.x*sn;
          const lobe=Math.sin(x*2.3+t*1.4)*Math.sin(p.y*2.9-t*1.1)*Math.cos(z*2.1+t*.8);
          let r=184+16*env+lobe*(5+19*env)+Math.sin(p.y*7-t*2.2)*3*env;
          for(const R of rings){const d=(p.y-R.h)/.15,g=Math.exp(-d*d)*R.g;r+=7*g;lit+=g;}
          vx+=x*r*w3;vy+=p.y*r*w3;vz+=z*r*w3;lit*=w3;
        }
        if(w4>.001){
          if(i<ring){const q=i/ring*tau,rr=173+9*p.sa;vx+=Math.cos(q)*rr*w4;vy+=Math.sin(q)*rr*w4;vz+=8*p.sa*w4;}
          else{const q=(i-ring)/(count-ring),first=q<.38,v=first?q/.38:(q-.38)/.62;
            vx+=((first?-81+58*v:-23+116*v)+6*p.sa)*w4;vy+=((first?57*v:57-133*v)+6*p.ca)*w4;}
        }
        const depth=(vz+240)/480,persp=850/(850-vz),alpha=.22+.72*depth+.35*env*ws;
        const r=(.8+depth*1.35)*(.75+.25*Math.sin(p.a+t*1.6))*fine*(1+.5*Math.min(1,lit)),o=i*5;
        buf[o]=(360+vx*persp)*unit;buf[o+1]=(360+vy*persp)*unit;buf[o+2]=r*9*unit;
        buf[o+3]=Math.min(1,(alpha+1.2*lit)*light);buf[o+4]=vz>75||lit>.25?(Math.max(alpha-.5,0)+lit*.8)*.75*light:0;
      }
      if(this.gl)this.drawGL(buf,count,rgb,size);else this.draw2D(buf,count,color,size);
    }
    drawGL(buf,count,rgb,size){
      const {gl,res,col}=this.gl;
      gl.viewport(0,0,size,size);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(res,size,size);gl.uniform3f(col,rgb[0]/255,rgb[1]/255,rgb[2]/255);
      gl.bufferData(gl.ARRAY_BUFFER,buf.subarray(0,count*5),gl.DYNAMIC_DRAW);gl.drawArrays(gl.POINTS,0,count);
    }
    draw2D(buf,count,color,size){
      const ctx=this.dctx;
      if(this.spriteColor!==color){this.spriteColor=color;this.dot=sprite(color,.2);this.spark=sprite('255,255,255',.5);}
      ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,size,size);ctx.globalCompositeOperation='lighter';
      for(let i=0;i<count;i++){
        const o=i*5,x=buf[o],y=buf[o+1],s=buf[o+2];
        ctx.globalAlpha=buf[o+3];ctx.drawImage(this.dot,x-s/2,y-s/2,s,s);
        if(buf[o+4]>0){const h=s*.2;ctx.globalAlpha=buf[o+4];ctx.drawImage(this.spark,x-h/2,y-h/2,h,h);}
      }
      ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
    }
  }
  customElements.define('speaking-orb', SpeakingOrb);
})();
