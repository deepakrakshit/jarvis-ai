/**
 * Audio diagnostics, format conversions, and test signal generation.
 *
 * WhatsApp VoIP uses:
 * - 16,000 Hz
 * - 1 channel (mono)
 * - 32-bit float (Float32Array)
 * - 320 samples per frame (20ms)
 */

export interface AudioStats {
  samples: number;
  rms: number;
  peak: number;
  nonZeroCount: number;
  durationMs: number;
}

/**
 * Computes RMS, peak amplitude, and non-zero counts for a Float32Array frame.
 */
export function computeAudioStats(pcm: Float32Array, sampleRate = 16000): AudioStats {
  if (!pcm || pcm.length === 0) {
    return { samples: 0, rms: 0, peak: 0, nonZeroCount: 0, durationMs: 0 };
  }

  let sumSquares = 0;
  let peak = 0;
  let nonZeroCount = 0;

  for (let i = 0; i < pcm.length; i++) {
    const s = pcm[i];
    const abs = Math.abs(s);
    if (abs > 0.0001) {
      nonZeroCount++;
    }
    if (abs > peak) {
      peak = abs;
    }
    sumSquares += s * s;
  }

  const rms = Math.sqrt(sumSquares / pcm.length);
  const durationMs = (pcm.length / sampleRate) * 1000;

  return {
    samples: pcm.length,
    rms,
    peak,
    nonZeroCount,
    durationMs,
  };
}

/**
 * Generate a synthetic sine wave tone (e.g. 440 Hz) as 16kHz mono Float32Array.
 */
export function generateTone(
  frequencyHz = 440,
  durationSeconds = 1.0,
  sampleRate = 16000,
  amplitude = 0.5
): Float32Array {
  const totalSamples = Math.floor(durationSeconds * sampleRate);
  const buffer = new Float32Array(totalSamples);
  const angularSpeed = (2 * Math.PI * frequencyHz) / sampleRate;

  for (let i = 0; i < totalSamples; i++) {
    buffer[i] = Math.sin(i * angularSpeed) * amplitude;
  }

  return buffer;
}

/**
 * Generate silence as 16kHz mono Float32Array.
 */
export function generateSilence(durationSeconds = 1.0, sampleRate = 16000): Float32Array {
  const totalSamples = Math.floor(durationSeconds * sampleRate);
  return new Float32Array(totalSamples);
}

/**
 * Convert Float32Array [-1.0, 1.0] to 16-bit signed PCM (Int16Array).
 */
export function float32ToInt16(f32: Float32Array): Int16Array {
  const i16 = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1.0, Math.min(1.0, f32[i]));
    i16[i] = s < 0 ? s * 32768 : s * 32767;
  }
  return i16;
}

/**
 * Convert 16-bit signed PCM (Int16Array) to Float32Array [-1.0, 1.0].
 */
export function int16ToFloat32(i16: Int16Array): Float32Array {
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) {
    const s = i16[i];
    f32[i] = s < 0 ? s / 32768.0 : s / 32767.0;
  }
  return f32;
}

/**
 * High-quality linear interpolation resampler from 24,000 Hz to 16,000 Hz (ratio 3:2).
 * Converts 3 input samples to 2 output samples.
 */
export function resample24kTo16k(input: Float32Array): Float32Array {
  if (!input || input.length === 0) {
    return new Float32Array(0);
  }

  const srcRate = 24000;
  const dstRate = 16000;
  const outputLength = Math.floor((input.length * dstRate) / srcRate);
  const output = new Float32Array(outputLength);
  const ratio = srcRate / dstRate; // 1.5

  for (let i = 0; i < outputLength; i++) {
    const srcPos = i * ratio;
    const srcIndex = Math.floor(srcPos);
    const fraction = srcPos - srcIndex;

    const s0 = input[srcIndex];
    const s1 = srcIndex + 1 < input.length ? input[srcIndex + 1] : s0;

    output[i] = s0 + fraction * (s1 - s0);
  }

  return output;
}
