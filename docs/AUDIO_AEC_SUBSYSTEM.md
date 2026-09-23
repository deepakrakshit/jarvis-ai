# JARVIS Audio Pipeline & Acoustic Echo Cancellation (AEC)

**Status:** Technical Specification & Implementation Guide  
**Module:** `jarvis.voice.audio_aec`  
**Audio Architecture:** Hardware WASAPI Loopback Capture + DSP Echo Cancellation  

---

## 1. Problem Definition & Requirements

When operating a voice-interactive assistant with open speakers and an active physical microphone:
1. **Self-Echo:** Assistant speech output emitted through speakers travels through the room and enters the microphone, causing the cognitive model to hear its own voice and trigger conversational loops.
2. **System Audio Contamination:** Media playback (e.g. YouTube, Spotify, games, system notifications) plays through speakers and contaminates the microphone signal, triggering false conversational turns.
3. **Hardware Loopback Purity:** Naive loopback recording merges all audio, whereas clean voice capture requires pure microphone input with speaker audio subtracted.

JARVIS resolves this through a dual-stream architecture: capturing the physical microphone as the primary signal and Windows WASAPI render output as the reference signal, performing real-time adaptive filtering in the frequency domain.

---

## 2. DSP Architecture & Mathematical Pipeline

```
     [Physical Speakers / Sound Card Render Endpoint]
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
      Acoustic Wave                WASAPI Loopback
     (Room Reflection)             (Pure Reference x[n])
             │                           │
             ▼                           │
     [Microphone Input]                  │
     (Primary Signal d[n])               │
             │                           │
             └─────────────┬─────────────┘
                           ▼
            ┌─────────────────────────────┐
            │   ALIGNMENT & RING BUFFER   │
            │   Delay Estimation (xcorr)  │
            └──────────────┬──────────────┘
                           ▼
            ┌─────────────────────────────┐
            │     ADAPTIVE FILTERING      │
            │       (PBFDAF Engine)       │
            │   W(f) update via NLMS      │
            └──────────────┬──────────────┘
                           ▼
            ┌─────────────────────────────┐
            │   DOUBLE-TALK DETECTOR      │
            │  Geigel / Coherence Metric  │
            └──────────────┬──────────────┘
                           ▼
            ┌─────────────────────────────┐
            │ RESIDUAL SPECTRAL SUPPRESSION│
            │  Magnitude Squared Coherence│
            └──────────────┬──────────────┘
                           ▼
            ┌─────────────────────────────┐
            │   CLEAN MICROPHONE STREAM   │
            │  (PCM 16kHz mono to Gemini) │
            └─────────────────────────────┘
```

### 2.1 WASAPI Loopback Reference Worker
The `LoopbackCaptureWorker` attaches to the default Windows audio render endpoint via PortAudio/WASAPI with loopback flags enabled. It captures the exact digital audio samples emitted by any application on the machine into a thread-safe ring buffer with low latency.

### 2.2 Partitioned Block Frequency Domain Adaptive Filter (PBFDAF)
Rather than expensive time-domain convolution, JARVIS segments the reference signal into uniform blocks and transforms them using Fast Fourier Transforms (FFT).
- **Block Size ($N$):** Configurable frame size (typically 256 to 512 samples at 16kHz).
- **Filter Weight Update:** Normalized Least Mean Squares (NLMS) with regularization $\epsilon$ and step size $\mu$:
  $$\mathbf{W}_{k+1} = \mathbf{W}_k + \mu \frac{\mathbf{X}_k^* \mathbf{E}_k}{\|\mathbf{X}_k\|^2 + \epsilon}$$
- **Linear Convolution Constraint:** Zero-padding ensures circular convolution artifacts are eliminated.

### 2.3 Normalized Double-Talk Detection (DTD)
When the user speaks simultaneously while speaker audio is playing:
- If the filter adapts during double-talk, the user's voice corrupts the adaptive filter weights.
- The Double-Talk Detector calculates the energy ratio between primary microphone input $d[n]$ and estimated echo $\hat{y}[n]$:
  $$\xi = \frac{\|d[n]\|}{\max_{m} |x[n-m]|}$$
- When double-talk is detected ($\xi > \text{threshold}$), filter coefficient updates are immediately frozen to prevent divergence.

### 2.4 Residual Spectral Suppression via Magnitude Squared Coherence (MSC)
Adaptive filters typically achieve 15–25 dB Echo Return Loss Enhancement (ERLE). To suppress diffuse room reverberation and non-linear speaker distortion:
- Frequency-dependent coherence is computed across spectral bins:
  $$\gamma_{xd}^2(f) = \frac{|S_{xd}(f)|^2}{S_{xx}(f) S_{dd}(f)}$$
- An attenuation gain mask $G(f) = \max(1 - \alpha \gamma_{xd}^2(f), G_{\min})$ is applied to suppress residual echo bins while preserving user speech formants.

### 2.5 Playback Activity Tracking & Hardware Drain
The `PlaybackActivityTracker` class in `jarvis.voice.output_tracker` models audio hardware DMA buffers. Even after audio packet generation completes, physical DAC output continues for several hundred milliseconds. The tracker prevents false barge-in until physical speaker hardware has fully drained.
