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
  const { MIN_SEARCH_VISIBLE_MS, DONE_VISIBLE_MS, SHORTCUTS } = CONFIG.UI;
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
    const pair = CONFIG.THEME[mode];
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
  let currentInterfaceMode = 'listening';
  let activeTurnText = '';
  let turnHadToolCall = false;
  let searchingStartTime = 0;
  let modeRevertTimer = null;
  let pendingSpeakingTimer = null;

  // Audio Player Engine (24kHz Algenib Voice Output)
  const player = new AudioPlayer(speakingOrb, () => {
    if (currentInterfaceMode === 'speaking') {
      if (turnHadToolCall) {
        turnHadToolCall = false;
        setInterfaceMode('done');
        modeRevertTimer = setTimeout(() => {
          if (currentInterfaceMode === 'done') {
            setInterfaceMode('listening');
          }
        }, DONE_VISIBLE_MS);
      } else {
        setInterfaceMode('listening');
      }
    }
  });

  // Audio Recorder Engine (16kHz Echo-Cancelled Linear PCM Stream)
  const recorder = new AudioRecorder({
    onAudioChunk: (pcmBase64) => {
      gateway.sendLiveAudio(pcmBase64);
    },
    onLevel: (level) => {
      micLevel = Math.max(0, (level - 0.15) / 0.85);
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
        field?.pulse(1);
        if (!recorder.isActive) {
          recorder.start().then((active) => setMicUi(active === true));
        }
      } else {
        statusDot.className = 'status-dot connecting';
        gatewayLabel.textContent = label;
      }
    },
  });

  // Visual Mode Coordinator
  function setInterfaceMode(mode, detail = '') {
    if (modeRevertTimer) {
      clearTimeout(modeRevertTimer);
      modeRevertTimer = null;
    }
    if (pendingSpeakingTimer && mode !== 'speaking') {
      clearTimeout(pendingSpeakingTimer);
      pendingSpeakingTimer = null;
    }

    currentInterfaceMode = mode;
    if (modeLabel) {
      modeLabel.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
    }
    applyTheme(mode);
    field?.setMode(mode);

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
          feedbackLine.textContent = detail || 'Retrieving context or executing tool...';
        } else if (mode === 'done') {
          feedbackLine.textContent = detail || 'Action completed successfully.';
        }
      }
    }
  }

  // Handle Speaking Orb Completion
  if (speakingOrb) {
    speakingOrb.addEventListener('end', () => {
      if (!player.isPlaying()) {
        if (turnHadToolCall) {
          turnHadToolCall = false;
          setInterfaceMode('done');
          modeRevertTimer = setTimeout(() => {
            if (currentInterfaceMode === 'done') {
              setInterfaceMode('listening');
            }
          }, DONE_VISIBLE_MS);
        } else {
          setInterfaceMode('listening');
        }
      }
    });
  }

  // Gateway Event Listeners
  gateway.on('live.audio', (payload) => {
    if (payload.pcm) {
      player.playPcmChunk(payload.pcm, payload.rate || CONFIG.AUDIO.OUTPUT_SAMPLE_RATE);
      if (currentInterfaceMode !== 'speaking') {
        if (currentInterfaceMode === 'searching') {
          const elapsed = Date.now() - searchingStartTime;
          if (elapsed < MIN_SEARCH_VISIBLE_MS) {
            if (!pendingSpeakingTimer) {
              pendingSpeakingTimer = setTimeout(() => {
                pendingSpeakingTimer = null;
                if (player.isPlaying() || activeTurnText) {
                  setInterfaceMode('speaking');
                }
              }, MIN_SEARCH_VISIBLE_MS - elapsed);
            }
          } else {
            setInterfaceMode('speaking');
          }
        } else if (currentInterfaceMode === 'done') {
          if (!pendingSpeakingTimer) {
            pendingSpeakingTimer = setTimeout(() => {
              pendingSpeakingTimer = null;
              if (player.isPlaying() || activeTurnText) {
                setInterfaceMode('speaking');
              }
            }, 600);
          }
        } else {
          setInterfaceMode('speaking');
        }
      }
    }
  });

  gateway.on('live.text', (payload) => {
    if (payload.chunk) {
      activeTurnText += payload.chunk;
      if (currentInterfaceMode !== 'speaking') {
        if (currentInterfaceMode === 'searching') {
          const elapsed = Date.now() - searchingStartTime;
          if (elapsed < MIN_SEARCH_VISIBLE_MS) {
            if (!pendingSpeakingTimer) {
              pendingSpeakingTimer = setTimeout(() => {
                pendingSpeakingTimer = null;
                setInterfaceMode('speaking');
              }, MIN_SEARCH_VISIBLE_MS - elapsed);
            }
          } else {
            setInterfaceMode('speaking');
          }
        } else {
          setInterfaceMode('speaking');
        }
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
    turnHadToolCall = true;
    searchingStartTime = Date.now();
    const detail = formatToolDetail(payload.name, payload.args);
    setInterfaceMode('searching', detail);
  });

  gateway.on('live.tool_complete', (payload) => {
    const elapsed = Date.now() - searchingStartTime;
    const remaining = Math.max(0, MIN_SEARCH_VISIBLE_MS - elapsed);
    setTimeout(() => {
      if (currentInterfaceMode === 'searching') {
        setInterfaceMode('done', formatToolCompletion(payload.name));
        modeRevertTimer = setTimeout(() => {
          if (currentInterfaceMode === 'done' && !player.isPlaying()) {
            setInterfaceMode('listening');
          }
        }, DONE_VISIBLE_MS);
      }
    }, remaining);
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
    if (pendingSpeakingTimer) {
      clearTimeout(pendingSpeakingTimer);
      pendingSpeakingTimer = null;
    }
    if (modeRevertTimer) {
      clearTimeout(modeRevertTimer);
      modeRevertTimer = null;
    }
    activeTurnText = '';
    turnHadToolCall = false;
    setInterfaceMode('listening');
    if (feedbackLine) {
      feedbackLine.textContent = 'Interrupted. Listening...';
    }
  });

  gateway.on('live.transcription', (payload) => {
    if (payload.text && feedbackLine) {
      feedbackLine.textContent = payload.text;
      if (payload.finished && currentInterfaceMode === 'listening') {
        setInterfaceMode('thinking');
      }
    }
  });

  gateway.on('task.progress', (payload) => {
    const currentTaskState = payload.state;
    if (currentTaskState === 'EXECUTING' || currentTaskState === 'SEARCHING') {
      turnHadToolCall = true;
      searchingStartTime = Date.now();
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
    modeRevertTimer = setTimeout(() => {
      setInterfaceMode('listening');
    }, DONE_VISIBLE_MS);
  });

  // User Interaction & Mic Controls
  function setMicUi(active) {
    if (!btnMic) return;
    btnMic.classList.toggle('active', active);
    btnMic.setAttribute('aria-pressed', String(active));
  }

  async function handleMicToggle() {
    player.getAudioContext();
    const active = await recorder.toggle();
    setMicUi(active);
    if (feedbackLine && currentInterfaceMode === 'listening') {
      feedbackLine.textContent = active
        ? 'JARVIS is listening... Speak or type anytime.'
        : 'Microphone paused.';
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
      recorder.start().then((active) => setMicUi(active === true));
    }
  };

  window.addEventListener('pointerdown', unlockAudioOnGesture, { once: true });
  window.addEventListener('keydown', unlockAudioOnGesture, { once: true });

  // Prompt Submission
  function syncSendState() {
    if (btnSend && promptInput) btnSend.disabled = !promptInput.value.trim();
  }

  function submitQuery(rawText) {
    const text = rawText.trim();
    if (!text) return;

    player.getAudioContext();
    if (promptInput) promptInput.value = '';
    syncSendState();
    field?.pulse(1.2);

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
  promptInput?.addEventListener('input', syncSendState);

  // Keyboard shortcuts
  if (kbdHint) kbdHint.textContent = SHORTCUTS.FOCUS_INPUT;
  if (hintRow) {
    hintRow.innerHTML = `<kbd>${SHORTCUTS.FOCUS_INPUT}</kbd> type · <kbd>${modKey}+${SHORTCUTS.TOGGLE_MIC.toUpperCase()}</kbd> mic · <kbd>Esc</kbd> dismiss`;
  }

  window.addEventListener('keydown', (e) => {
    const typing = document.activeElement === promptInput;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === SHORTCUTS.TOGGLE_MIC) {
      e.preventDefault();
      handleMicToggle();
    } else if (!typing && e.key === SHORTCUTS.FOCUS_INPUT && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      promptInput?.focus();
    } else if (typing && e.key === 'Escape') {
      promptInput.blur();
    }
  });

  // Initialize
  setInterfaceMode('listening');
  if (field) {
    field.setAnchor(orbViewport).setLevelSource(currentVoiceLevel).start();
  }
  gateway.connect();

  // App visibility & focus synchronization to eliminate background freeze
  function syncVisibilityState() {
    if (document.hidden) return;
    if (!player.isPlaying() && currentInterfaceMode === 'speaking') {
      if (speakingOrb && typeof speakingOrb.finishCurrentLine === 'function') {
        speakingOrb.finishCurrentLine();
      }
      if (turnHadToolCall) {
        turnHadToolCall = false;
        setInterfaceMode('done');
        modeRevertTimer = setTimeout(() => {
          if (currentInterfaceMode === 'done') {
            setInterfaceMode('listening');
          }
        }, 800);
      } else {
        setInterfaceMode('listening');
      }
    }
    if (speakingOrb && typeof speakingOrb.resize === 'function') {
      speakingOrb.resize();
    }
    if (signalOrb && typeof signalOrb.resize === 'function') {
      signalOrb.resize();
    }
  }

  document.addEventListener('visibilitychange', syncVisibilityState);
  window.addEventListener('focus', syncVisibilityState);

  // Test Harnesses for automated verification
  window.__jarvis_handleFrame = (frame) => gateway.handleFrame(frame);
  window.__jarvis_setInterfaceMode = setInterfaceMode;
  window.__jarvis_player = player;
  window.__jarvis_recorder = recorder;
  window.__jarvis_gateway = gateway;
})();
