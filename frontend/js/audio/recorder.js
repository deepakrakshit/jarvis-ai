/**
 * WebRTC Hardware Echo-Cancelled Microphone Capture Engine.
 * Streams continuous 16kHz 16-bit linear PCM with duplex suppression and acoustic hangover protection.
 */

import { CONFIG } from '../config.js';

export class AudioRecorder {
  constructor({ onAudioChunk, onLevel, isSpeakingCheck, getLastAudioEndTime }) {
    this.onAudioChunk = onAudioChunk;
    this.onLevel = onLevel;
    this.isSpeakingCheck = isSpeakingCheck;
    this.getLastAudioEndTime = getLastAudioEndTime;

    this.micStream = null;
    this.micSource = null;
    this.micProcessor = null;
    this.micAudioContext = null;
    this.isActive = false;
    this.lastToggleTime = 0;
  }

  downsampleTo16k(inputBuffer, inputSampleRate) {
    if (inputSampleRate === CONFIG.AUDIO.INPUT_SAMPLE_RATE) return inputBuffer;
    const ratio = inputSampleRate / CONFIG.AUDIO.INPUT_SAMPLE_RATE;
    const newLength = Math.round(inputBuffer.length / ratio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      let accum = 0, count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < inputBuffer.length; i++) {
        accum += inputBuffer[i];
        count++;
      }
      result[offsetResult] = count > 0 ? (accum / count) : 0;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  floatTo16BitPCM(float32Array) {
    const buffer = new ArrayBuffer(float32Array.length * 2);
    const view = new DataView(buffer);
    for (let i = 0; i < float32Array.length; i++) {
      let s = Math.max(-1, Math.min(1, float32Array[i]));
      view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Uint8Array(buffer);
  }

  uint8ToBase64(bytes) {
    let binary = '';
    const len = bytes.byteLength;
    for (let i = 0; i < len; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary);
  }

  async start() {
    if (this.isActive) return;
    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        }
      });

      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      this.micAudioContext = new AudioContextClass();
      if (this.micAudioContext.state === 'suspended') {
        await this.micAudioContext.resume();
      }

      this.micSource = this.micAudioContext.createMediaStreamSource(this.micStream);
      this.micProcessor = this.micAudioContext.createScriptProcessor(CONFIG.AUDIO.INPUT_BUFFER_SIZE, 1, 1);

      const dummyGain = this.micAudioContext.createGain();
      dummyGain.gain.value = 0;

      this.micProcessor.onaudioprocess = (e) => {
        if (!this.isActive) return;

        // Strict Duplex Echo Suppression + Acoustic Hangover Guard:
        // When JARVIS is speaking or audio recently finished, never transmit mic frames!
        const lastAudioEndTime = typeof this.getLastAudioEndTime === 'function' ? this.getLastAudioEndTime() : 0;
        const isSpeaking = typeof this.isSpeakingCheck === 'function' ? this.isSpeakingCheck() : false;
        if (isSpeaking || (Date.now() - lastAudioEndTime < CONFIG.AUDIO.HANGOVER_GUARD_MS)) {
          return;
        }

        const inputChannel = e.inputBuffer.getChannelData(0);
        let sumSquares = 0;
        for (let i = 0; i < inputChannel.length; i++) {
          sumSquares += inputChannel[i] * inputChannel[i];
        }
        const rms = Math.sqrt(sumSquares / inputChannel.length);
        if (typeof this.onLevel === 'function') {
          const level = Math.min(1.0, Math.max(0.15, rms * 15));
          this.onLevel(level);
        }

        // Stream continuous 16kHz linear PCM frames to Gemini Live for accurate cloud neural VAD
        const pcm16k = this.downsampleTo16k(inputChannel, this.micAudioContext.sampleRate);
        const pcmBytes = this.floatTo16BitPCM(pcm16k);
        const b64 = this.uint8ToBase64(pcmBytes);

        if (typeof this.onAudioChunk === 'function') {
          this.onAudioChunk(b64);
        }
      };

      this.micSource.connect(this.micProcessor);
      this.micProcessor.connect(dummyGain);
      dummyGain.connect(this.micAudioContext.destination);

      this.isActive = true;
      return true;
    } catch (err) {
      console.warn('Microphone activation notice:', err);
      this.stop();
      return false;
    }
  }

  stop() {
    this.isActive = false;
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => t.stop());
      this.micStream = null;
    }
    if (this.micSource) {
      try { this.micSource.disconnect(); } catch (e) {}
      this.micSource = null;
    }
    if (this.micProcessor) {
      try { this.micProcessor.disconnect(); } catch (e) {}
      this.micProcessor = null;
    }
    if (this.micAudioContext) {
      try { this.micAudioContext.close(); } catch (e) {}
      this.micAudioContext = null;
    }
    if (typeof this.onLevel === 'function') {
      this.onLevel(0.4);
    }
  }

  async toggle() {
    const now = Date.now();
    if (now - this.lastToggleTime < 350) return this.isActive;
    this.lastToggleTime = now;
    if (this.isActive) {
      this.stop();
      return false;
    }
    return (await this.start()) === true;
  }
}
