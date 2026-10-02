/**
 * JARVIS Neural Interface - Master Application Orchestrator.
 * Coordinates Web Components, Web Audio Pipelines, and Gateway Client.
 */

import { CONFIG, getGatewayUrl, generateSessionId } from './config.js';
import './components/signal-orb.js';
import './components/speaking-orb.js';
import { AudioPlayer } from './audio/player.js';
import { AudioRecorder } from './audio/recorder.js';
import { GatewayClient } from './gateway/client.js';

(() => {
  // DOM References
  const signalOrb = document.getElementById('signal-orb');
  const speakingOrb = document.getElementById('speaking-orb');
  const statusDot = document.getElementById('status-dot');
  const gatewayLabel = document.getElementById('gateway-label');
  const modeBadge = document.getElementById('mode-badge');
  const feedbackLine = document.getElementById('feedback-line');
  const btnMic = document.getElementById('btn-mic');
  const promptInput = document.getElementById('prompt-input');
  const promptForm = document.getElementById('prompt-form');

  // Application State
  const sessionId = generateSessionId();
  let currentInterfaceMode = 'listening';
  let activeTurnText = '';

  // Audio Player Engine (24kHz Algenib Voice Output)
  const player = new AudioPlayer(speakingOrb, () => {
    if (currentInterfaceMode === 'speaking') {
      setInterfaceMode('listening');
    }
  });

  // Audio Recorder Engine (16kHz Echo-Cancelled Linear PCM Stream)
  const recorder = new AudioRecorder({
    onAudioChunk: (pcmBase64) => {
      gateway.sendLiveAudio(pcmBase64);
    },
    onLevel: (level) => {
      if (currentInterfaceMode === 'listening' && signalOrb) {
        signalOrb.setAttribute('level', level.toFixed(2));
      }
    },
    isSpeakingCheck: () => currentInterfaceMode === 'speaking' || player.isPlaying(),
    getLastAudioEndTime: () => player.getLastAudioEndTime(),
  });

  // Gateway Client (WebSocket Pub/Sub)
  const gateway = new GatewayClient({
    url: getGatewayUrl(),
    sessionId: sessionId,
    onStatusChange: (status, label) => {
      if (status === 'connected') {
        statusDot.className = 'status-dot';
        gatewayLabel.textContent = label;
        if (!recorder.isActive) {
          recorder.start().then((active) => {
            if (active) btnMic.classList.add('active');
          });
        }
      } else {
        statusDot.className = 'status-dot connecting';
        gatewayLabel.textContent = label;
      }
    },
  });

  // Visual Mode Coordinator
  function setInterfaceMode(mode, detail = '') {
    currentInterfaceMode = mode;
    if (modeBadge) {
      modeBadge.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
    }

    if (mode === 'speaking') {
      if (signalOrb) {
        signalOrb.classList.remove('orb-visible');
        signalOrb.classList.add('orb-hidden');
      }

      if (speakingOrb) {
        speakingOrb.classList.remove('orb-hidden');
        speakingOrb.classList.add('orb-visible');
        speakingOrb.setAttribute('rest', 'listening');
        speakingOrb.state = 'speaking';
        speakingOrb.resize();
      }

      if (feedbackLine) {
        feedbackLine.textContent = '';
      }
    } else {
      if (speakingOrb) {
        speakingOrb.classList.remove('orb-visible');
        speakingOrb.classList.add('orb-hidden');
      }

      if (signalOrb) {
        signalOrb.classList.remove('orb-hidden');
        signalOrb.classList.add('orb-visible');
        signalOrb.setAttribute('state', mode);
      }

      if (feedbackLine) {
        if (mode === 'listening') {
          feedbackLine.textContent = 'Listening... Ready for your command.';
        } else if (mode === 'thinking') {
          feedbackLine.textContent = 'Synthesizing reasoning graph and formulating plan...';
        } else if (mode === 'searching') {
          feedbackLine.textContent = detail ? `Executing tool: ${detail}...` : 'Retrieving context or executing tool...';
        } else if (mode === 'done') {
          feedbackLine.textContent = 'Action completed successfully.';
        }
      }
    }
  }

  // Handle Speaking Orb Completion
  if (speakingOrb) {
    speakingOrb.addEventListener('end', () => {
      if (!player.isPlaying()) {
        setInterfaceMode('listening');
      }
    });
  }

  // Gateway Event Listeners
  gateway.on('live.audio', (payload) => {
    if (payload.pcm) {
      player.playPcmChunk(payload.pcm, payload.rate || CONFIG.AUDIO.OUTPUT_SAMPLE_RATE);
      if (currentInterfaceMode !== 'speaking') {
        setInterfaceMode('speaking');
      }
    }
  });

  gateway.on('live.text', (payload) => {
    if (payload.chunk) {
      activeTurnText += payload.chunk;
      if (currentInterfaceMode !== 'speaking') {
        setInterfaceMode('speaking');
      }
      if (speakingOrb && speakingOrb.streamWords) {
        speakingOrb.streamWords(payload.chunk);
      }
    }
  });

  gateway.on('live.turn_complete', (payload) => {
    const fullText = (payload.text || activeTurnText || '').trim();
    if (currentInterfaceMode === 'speaking' && speakingOrb && speakingOrb.finishStream) {
      speakingOrb.finishStream(fullText);
    }
    activeTurnText = '';
  });

  gateway.on('live.tool_call', (payload) => {
    setInterfaceMode('searching', payload.name || '');
  });

  gateway.on('live.interrupted', () => {
    player.stop();
    if (speakingOrb && speakingOrb.line) {
      speakingOrb.line.after = true;
      speakingOrb.line = null;
    }
    activeTurnText = '';
    setInterfaceMode('listening');
    if (feedbackLine) {
      feedbackLine.textContent = 'Interrupted. Listening...';
    }
  });

  gateway.on('live.transcription', (payload) => {
    if (payload.text && feedbackLine) {
      feedbackLine.textContent = payload.text;
      if (payload.finished) {
        setInterfaceMode('thinking');
      }
    }
  });

  gateway.on('task.progress', (payload) => {
    const currentState = payload.state;
    if (currentState === 'EXECUTING' || currentState === 'SEARCHING') {
      setInterfaceMode('searching', payload.message || '');
    } else {
      setInterfaceMode('thinking');
      if (feedbackLine) {
        feedbackLine.textContent = payload.message || 'Evaluating intent...';
      }
    }
  });

  gateway.on('task.completed', () => {
    setInterfaceMode('done');
    setTimeout(() => {
      setInterfaceMode('listening');
    }, 1200);
  });

  // User Interaction & Mic Controls
  async function handleMicToggle() {
    player.getAudioContext();
    const active = await recorder.toggle();
    if (btnMic) {
      if (active) {
        btnMic.classList.add('active');
        if (feedbackLine) feedbackLine.textContent = 'JARVIS is listening... Speak or type anytime.';
      } else {
        btnMic.classList.remove('active');
        if (feedbackLine) feedbackLine.textContent = 'Microphone paused.';
      }
    }
  }

  if (btnMic) {
    btnMic.addEventListener('click', (e) => {
      e.stopPropagation();
      handleMicToggle();
    });
  }

  // Unlock AudioContext & activate microphone on first interaction
  const unlockAudioOnGesture = (e) => {
    player.getAudioContext();
    if (!recorder.isActive && !e.target.closest('#btn-mic')) {
      recorder.start().then((active) => {
        if (active && btnMic) btnMic.classList.add('active');
      });
    }
  };

  window.addEventListener('pointerdown', unlockAudioOnGesture, { once: true });
  window.addEventListener('keydown', unlockAudioOnGesture, { once: true });

  // Prompt Submission
  function submitQuery(rawText) {
    const text = rawText.trim();
    if (!text) return;

    player.getAudioContext();
    if (promptInput) promptInput.value = '';

    setInterfaceMode('thinking');
    if (feedbackLine) {
      feedbackLine.textContent = `Processing: "${text}"`;
    }
    activeTurnText = '';

    const sent = gateway.sendLiveText(text);
    if (!sent) {
      setTimeout(() => {
        setInterfaceMode('listening');
        if (feedbackLine) feedbackLine.textContent = 'Gateway offline. Reconnecting...';
      }, 1500);
    }
  }

  if (promptForm) {
    promptForm.addEventListener('submit', (e) => {
      e.preventDefault();
      if (promptInput) {
        submitQuery(promptInput.value);
      }
    });
  }

  // Initialize
  setInterfaceMode('listening');
  gateway.connect();

  // Test Harnesses for automated verification
  window.__jarvis_handleFrame = (frame) => gateway.handleFrame(frame);
  window.__jarvis_setInterfaceMode = setInterfaceMode;
  window.__jarvis_player = player;
  window.__jarvis_recorder = recorder;
  window.__jarvis_gateway = gateway;
})();
