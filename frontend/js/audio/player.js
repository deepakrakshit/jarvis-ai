/**
 * Web Audio PCM Player for Real Gemini 3.8 Live Voice (Algenib 24kHz PCM).
 */

import { CONFIG } from '../config.js';

export class AudioPlayer {
  constructor(speakingOrb, onDrain) {
    this.speakingOrb = speakingOrb;
    this.onDrain = onDrain;
    this.audioCtx = null;
    this.audioAnalyser = null;
    this.nextPlayTime = 0;
    this.activeAudioSources = [];
    this.drainCheckTimer = null;
    this.lastAudioEndTime = 0;
  }

  getAudioContext() {
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      this.speakingOrb.actx = new AudioContextClass({ sampleRate: CONFIG.AUDIO.OUTPUT_SAMPLE_RATE });
      this.audioCtx = this.speakingOrb.actx;
      this.audioAnalyser = this.audioCtx.createAnalyser();
      this.audioAnalyser.fftSize = 512;
      this.audioAnalyser.connect(this.audioCtx.destination);
      this.speakingOrb.attachAudio(this.audioAnalyser);
    }
    if (this.audioCtx.state === 'suspended') {
      this.audioCtx.resume();
    }
    return this.audioCtx;
  }

  isPlaying() {
    return this.activeAudioSources.length > 0;
  }

  getLastAudioEndTime() {
    return this.lastAudioEndTime;
  }

  base64ToFloat32(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    const int16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768.0;
    }
    return float32;
  }

  playPcmChunk(base64Data, sampleRate = CONFIG.AUDIO.OUTPUT_SAMPLE_RATE) {
    const ctx = this.getAudioContext();
    const float32Data = this.base64ToFloat32(base64Data);
    if (float32Data.length === 0) return;

    const audioBuffer = ctx.createBuffer(1, float32Data.length, sampleRate);
    audioBuffer.getChannelData(0).set(float32Data);

    const sourceNode = ctx.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(this.audioAnalyser);

    const now = ctx.currentTime;
    if (this.nextPlayTime < now) {
      this.nextPlayTime = now + CONFIG.AUDIO.SMOOTHING_BUFFER_SEC;
    }
    sourceNode.start(this.nextPlayTime);
    this.nextPlayTime += audioBuffer.duration;
    this.lastAudioEndTime = Date.now() + Math.round(audioBuffer.duration * 1000);
    this.activeAudioSources.push(sourceNode);

    sourceNode.onended = () => {
      this.lastAudioEndTime = Date.now();
      const idx = this.activeAudioSources.indexOf(sourceNode);
      if (idx !== -1) this.activeAudioSources.splice(idx, 1);
      this.scheduleDrainCheck();
    };
  }

  scheduleDrainCheck() {
    if (this.drainCheckTimer) clearTimeout(this.drainCheckTimer);
    this.drainCheckTimer = setTimeout(() => {
      this.lastAudioEndTime = Date.now();
      if (
        this.audioCtx &&
        this.audioCtx.currentTime >= this.nextPlayTime - 0.05 &&
        this.activeAudioSources.length === 0
      ) {
        // Wait if speaking orb has an active unfinished typography line,
        // unless the app is hidden in the background or the line duration has elapsed
        if (this.speakingOrb.line && !this.speakingOrb.line.after) {
          const line = this.speakingOrb.line;
          const isBackgrounded = typeof document !== 'undefined' && document.hidden;
          const elapsedSec = line.wallClockStart ? (Date.now() - line.wallClockStart) / 1000 : Infinity;
          const expectedSec = line.expectedDuration || 2.0;
          const isExpired = elapsedSec >= expectedSec;

          if (isBackgrounded || isExpired) {
            line.after = true;
            if (typeof this.speakingOrb.finishCurrentLine === 'function') {
              this.speakingOrb.finishCurrentLine();
            } else {
              this.speakingOrb.state = this.speakingOrb.getAttribute('rest') || 'listening';
            }
          } else {
            this.scheduleDrainCheck();
            return;
          }
        }
        if (typeof this.onDrain === 'function') {
          this.onDrain();
        }
      }
    }, 300);
  }

  stop() {
    this.lastAudioEndTime = Date.now();
    for (const src of this.activeAudioSources) {
      try { src.stop(); } catch (e) {}
    }
    this.activeAudioSources = [];
    if (this.audioCtx) {
      this.nextPlayTime = this.audioCtx.currentTime;
    }
    if (this.drainCheckTimer) clearTimeout(this.drainCheckTimer);
  }
}
