/**
 * JARVIS Neural Interface - Master Application Orchestrator.
 * Coordinates Web Components, Web Audio Pipelines, and Gateway Client.
 */

import { CONFIG, getGatewayUrl, generateSessionId } from './config.js';
import './components/signal-orb.js';
import './components/speaking-orb.js';
import { ParticleField } from './components/particle-field.js';
import { AudioPlayer } from './audio/player.js';
import { AudioRecorder } from './audio/recorder.js';
import { GatewayClient } from './gateway/client.js';
import { OrbStateController, ORB_STATES } from './state/orb-state-controller.js';

(() => {
  // DOM References
  const signalOrb = document.getElementById('signal-orb');
  const speakingOrb = document.getElementById('speaking-orb');
  const statusDot = document.getElementById('status-dot');
  const gatewayLabel = document.getElementById('gateway-label');
  const modeLabel = document.getElementById('mode-label');
  const feedbackLine = document.getElementById('feedback-line');
  const btnMic = document.getElementById('btn-mic');
  const btnSend = document.getElementById('btn-send');
  const promptInput = document.getElementById('prompt-input');
  const promptForm = document.getElementById('prompt-form');
  const orbViewport = document.getElementById('orb-viewport');
  const kbdHint = document.getElementById('kbd-hint');
  const hintRow = document.getElementById('hint-row');
  const fieldCanvas = document.getElementById('particle-field');

  // Application Constants
  const { MIN_SEARCH_VISIBLE_MS, DONE_VISIBLE_MS, ERROR_VISIBLE_MS = 2500, SHORTCUTS } = CONFIG.UI;
  const isMac = /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent);
  const modKey = isMac ? '⌘' : 'Ctrl';

  // Ambient 3D particle field + live voice level plumbing
  let micLevel = 0;
  let lastCssLevel = -1;
  const field = fieldCanvas
    ? new ParticleField(fieldCanvas, { particles: CONFIG.PARTICLES, theme: CONFIG.THEME })
    : null;

  function currentVoiceLevel() {
    const lvl = currentInterfaceMode === 'speaking'
      ? Math.min(1, (speakingOrb?.env || 0) * 1.4)
      : (recorder.isActive ? micLevel : 0);
    if (Math.abs(lvl - lastCssLevel) > 0.02) {
      lastCssLevel = lvl;
      const v = lvl.toFixed(3);
      orbViewport?.style.setProperty('--level', v);
      btnMic?.style.setProperty('--level', v);
    }
    return lvl;
  }

  function applyTheme(mode) {
    const pair = CONFIG.THEME[mode] || CONFIG.THEME.idle;
    if (!pair) return;
    const root = document.documentElement.style;
    root.setProperty('--mode-rgb', pair[0].join(', '));
    root.setProperty('--mode-rgb-2', pair[1].join(', '));
    document.body.dataset.mode = mode;
  }

  function formatToolDetail(name, args) {
    if (!name) return 'Retrieving context or executing tool...';
    const a = args || {};
    switch (name) {
      case 'web_search':
        return a.query ? `Searching web: "${a.query}"...` : 'Searching the web...';
      case 'shell_execute':
        return a.command ? `Executing command: ${a.command}...` : 'Running shell command...';
      case 'app_launch':
        return a.target ? `Launching ${a.target}...` : 'Launching application...';
      case 'app_focus':
        return a.target ? `Focusing ${a.target}...` : 'Focusing window...';
      case 'app_close':
        return a.target ? `Closing ${a.target}...` : 'Closing application...';
      case 'system_volume_set':
        return a.level !== undefined ? `Setting volume to ${a.level}%...` : 'Adjusting volume...';
      case 'system_volume_get':
        return 'Checking system volume...';
      case 'system_screenshot':
        return 'Capturing desktop screenshot...';
      case 'system_info':
        return 'Inspecting system diagnostics...';
      case 'browser_navigate':
        return a.url ? `Navigating to ${a.url}...` : 'Navigating browser...';
      case 'browser_click':
        return a.selector ? `Clicking element: ${a.selector}...` : 'Clicking browser element...';
      case 'browser_type':
        return a.text ? `Typing "${a.text}"...` : 'Entering browser text...';
      case 'browser_snapshot':
        return 'Analyzing web page structure...';
      case 'whatsapp_send':
        return a.target ? `Sending WhatsApp message to ${a.target}...` : 'Sending WhatsApp message...';
      case 'whatsapp_call':
        return a.target ? `Initiating WhatsApp call to ${a.target}...` : 'Placing WhatsApp call...';
      case 'delegate_task':
        return a.target_model ? `Delegating to ${a.target_model}...` : 'Delegating to specialist model...';
      default:
        return `Executing ${name.replace(/_/g, ' ')}...`;
    }
  }

  function formatToolCompletion(name) {
    if (!name) return 'Action completed successfully.';
    switch (name) {
      case 'web_search': return 'Web search completed.';
      case 'shell_execute': return 'Command executed successfully.';
      case 'app_launch': return 'Application launched.';
      case 'app_focus': return 'Window focused.';
      case 'app_close': return 'Application closed.';
      case 'system_volume_set': return 'Volume adjusted.';
      case 'system_screenshot': return 'Screenshot captured.';
      case 'system_info': return 'System diagnostics retrieved.';
      case 'browser_navigate': return 'Page loaded.';
      case 'browser_click': return 'Element clicked.';
      case 'browser_type': return 'Input submitted.';
      case 'whatsapp_send': return 'WhatsApp message sent.';
      case 'whatsapp_call': return 'WhatsApp call placed.';
      default: return `${name.replace(/_/g, ' ')} completed successfully.`;
    }
  }

  // Application State
  const sessionId = generateSessionId();
  let currentInterfaceMode = ORB_STATES.IDLE;
  let activeTurnText = '';

  // Visual Mode Coordinator
  function setInterfaceMode(mode, detail = '') {
    currentInterfaceMode = mode;
    if (modeLabel) {
      modeLabel.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
    }
    applyTheme(mode);
    field?.setMode(mode);

    if (mode === ORB_STATES.SPEAKING) {
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
        switch (mode) {
          case ORB_STATES.IDLE:
            feedbackLine.textContent = detail || 'JARVIS standby. Click mic or type to begin.';
            break;
          case ORB_STATES.LISTENING:
            feedbackLine.textContent = detail || 'Listening... Ready for your command.';
            break;
          case ORB_STATES.THINKING:
            feedbackLine.textContent = detail || 'Synthesizing reasoning graph and formulating plan...';
            break;
          case ORB_STATES.SEARCHING:
            feedbackLine.textContent = detail || 'Retrieving context or executing tool...';
            break;
          case ORB_STATES.DONE:
            feedbackLine.textContent = detail || 'Action completed successfully.';
            break;
          case ORB_STATES.ERROR:
            feedbackLine.textContent = detail || 'An error occurred. Check connection or logs.';
            break;
          default:
            feedbackLine.textContent = detail || '';
        }
      }
    }
  }

  // State Controller Initialization
  const orbController = new OrbStateController({
    initialState: ORB_STATES.IDLE,
    minSearchMs: MIN_SEARCH_VISIBLE_MS,
    doneVisibleMs: DONE_VISIBLE_MS,
    errorVisibleMs: ERROR_VISIBLE_MS,
    onStateChange: (mode, { detail }) => {
      setInterfaceMode(mode, detail);
    },
  });

  // Audio Player Engine (24kHz Algenib Voice Output)
  const player = new AudioPlayer(speakingOrb, () => {
    orbController.onSpeechEnd(player.isPlaying());
  });

  // Audio Recorder Engine (16kHz Echo-Cancelled Linear PCM Stream)
  const recorder = new AudioRecorder({
    onAudioChunk: (pcmBase64) => {
      gateway.sendLiveAudio(pcmBase64);
    },
    onLevel: (level) => {
      micLevel = Math.max(0, (level - 0.15) / 0.85);
      if (currentInterfaceMode === ORB_STATES.LISTENING && signalOrb) {
        signalOrb.setAttribute('level', level.toFixed(2));
      }
    },
    isSpeakingCheck: () => currentInterfaceMode === ORB_STATES.SPEAKING || player.isPlaying(),
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
        field?.pulse(1);
        if (!recorder.isActive) {
          recorder.start().then((active) => {
            setMicUi(active === true);
            orbController.onConnect(active === true);
          });
        } else {
          orbController.onConnect(true);
        }
      } else {
        statusDot.className = 'status-dot connecting';
        gatewayLabel.textContent = label;
        orbController.onDisconnect();
      }
    },
  });

  // Handle Speaking Orb Completion
  if (speakingOrb) {
    speakingOrb.addEventListener('end', () => {
      orbController.onSpeechEnd(player.isPlaying());
    });
  }

  // Gateway Event Listeners
  gateway.on('live.audio', (payload) => {
    if (payload.pcm) {
      player.playPcmChunk(payload.pcm, payload.rate || CONFIG.AUDIO.OUTPUT_SAMPLE_RATE);
      orbController.onSpeechStart(player.isPlaying() || !!activeTurnText);
    }
  });

  gateway.on('live.text', (payload) => {
    if (payload.chunk) {
      activeTurnText += payload.chunk;
      orbController.onSpeechStart(true);
      if (speakingOrb && speakingOrb.streamWords) {
        speakingOrb.streamWords(payload.chunk);
      }
    }
  });

  gateway.on('live.turn_complete', (payload) => {
    const fullText = (payload.text || activeTurnText || '').trim();
    if (currentInterfaceMode === ORB_STATES.SPEAKING && speakingOrb && speakingOrb.finishStream) {
      speakingOrb.finishStream(fullText);
    }
    activeTurnText = '';
  });

  gateway.on('live.tool_call', (payload) => {
    const detail = formatToolDetail(payload.name, payload.args);
    orbController.onToolCall(detail);
  });

  gateway.on('live.tool_complete', (payload) => {
    orbController.onToolComplete(formatToolCompletion(payload.name));
  });

  gateway.on('live.interrupted', () => {
    player.stop();
    if (speakingOrb) {
      if (speakingOrb.finishCurrentLine) {
        speakingOrb.finishCurrentLine();
      } else if (speakingOrb.line) {
        speakingOrb.line.after = true;
        speakingOrb.line = null;
      }
    }
    activeTurnText = '';
    orbController.onInterrupted();
  });

  gateway.on('live.transcription', (payload) => {
    if (payload.text && feedbackLine) {
      feedbackLine.textContent = payload.text;
      if (payload.finished && currentInterfaceMode === ORB_STATES.LISTENING) {
        orbController.transitionTo(ORB_STATES.THINKING);
      }
    }
  });

  gateway.on('task.progress', (payload) => {
    const current