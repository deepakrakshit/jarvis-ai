/**
 * OrbStateController: Reusable visual state machine for JARVIS Orbs.
 * Manages transitions between: 'idle', 'listening', 'thinking', 'searching', 'speaking', 'done', 'error'.
 * Ensures transitions are synchronized with Gateway events and guarantees
 * terminal/error states clean up timers without leaking background animations.
 */

export const ORB_STATES = {
  IDLE: 'idle',
  LISTENING: 'listening',
  THINKING: 'thinking',
  SEARCHING: 'searching',
  SPEAKING: 'speaking',
  DONE: 'done',
  ERROR: 'error',
};

export class OrbStateController {
  constructor({
    initialState = ORB_STATES.IDLE,
    minSearchMs = 800,
    doneVisibleMs = 1200,
    errorVisibleMs = 2500,
    onStateChange = null,
  } = {}) {
    this.currentState = initialState;
    this.minSearchMs = minSearchMs;
    this.doneVisibleMs = doneVisibleMs;
    this.errorVisibleMs = errorVisibleMs;
    this.onStateChange = onStateChange;

    this.searchingStartTime = 0;
    this.turnHadToolCall = false;
    this.modeTimer = null;
    this.pendingSpeakingTimer = null;
  }

  getState() {
    return this.currentState;
  }

  clearTimers() {
    if (this.modeTimer) {
      clearTimeout(this.modeTimer);
      this.modeTimer = null;
    }
    if (this.pendingSpeakingTimer) {
      clearTimeout(this.pendingSpeakingTimer);
      this.pendingSpeakingTimer = null;
    }
  }

  transitionTo(nextState, detail = '') {
    const validStates = Object.values(ORB_STATES);
    if (!validStates.includes(nextState)) {
      console.warn(`[OrbStateController] Invalid state: "${nextState}"`);
      return;
    }

    this.clearTimers();
    const prevState = this.currentState;
    this.currentState = nextState;

    if (typeof this.onStateChange === 'function') {
      this.onStateChange(nextState, { prevState, detail });
    }
  }

  // --- Gateway & User Event Handlers ---

  onToolCall(detail = '') {
    this.turnHadToolCall = true;
    this.searchingStartTime = Date.now();
    this.transitionTo(ORB_STATES.SEARCHING, detail);
  }

  onToolComplete(detail = '') {
    const elapsed = Date.now() - this.searchingStartTime;
    const remaining = Math.max(0, this.minSearchMs - elapsed);

    this.modeTimer = setTimeout(() => {
      if (this.currentState === ORB_STATES.SEARCHING) {
        this.transitionTo(ORB_STATES.DONE, detail);
        this.modeTimer = setTimeout(() => {
          if (this.currentState === ORB_STATES.DONE) {
            this.transitionTo(ORB_STATES.LISTENING);
          }
        }, this.doneVisibleMs);
      }
    }, remaining);
  }

  onSpeechStart(hasActiveAudioOrText = true) {
    if (this.currentState === ORB_STATES.SPEAKING) return;

    if (this.currentState === ORB_STATES.SEARCHING) {
      const elapsed = Date.now() - this.searchingStartTime;
      if (elapsed < this.minSearchMs) {
        if (!this.pendingSpeakingTimer) {
          this.pendingSpeakingTimer = setTimeout(() => {
            this.pendingSpeakingTimer = null;
            if (hasActiveAudioOrText) {
              this.transitionTo(ORB_STATES.SPEAKING);
            }
          }, this.minSearchMs - elapsed);
        }
        return;
      }
    } else if (this.currentState === ORB_STATES.DONE) {
      if (!this.pendingSpeakingTimer) {
        this.pendingSpeakingTimer = setTimeout(() => {
          this.pendingSpeakingTimer = null;
          if (hasActiveAudioOrText) {
            this.transitionTo(ORB_STATES.SPEAKING);
          }
        }, 600);
      }
      return;
    }

    this.transitionTo(ORB_STATES.SPEAKING);
  }

  onSpeechEnd(isPlayingAudio = false) {
    if (isPlayingAudio) return;

    if (this.turnHadToolCall) {
      this.turnHadToolCall = false;
      this.transitionTo(ORB_STATES.DONE);
      this.modeTimer = setTimeout(() => {
        if (this.currentState === ORB_STATES.DONE) {
          this.transitionTo(ORB_STATES.LISTENING);
        }
      }, this.doneVisibleMs);
    } else {
      this.transitionTo(ORB_STATES.LISTENING);
    }
  }

  onInterrupted() {
    this.clearTimers();
    this.turnHadToolCall = false;
    this.transitionTo(ORB_STATES.LISTENING, 'Interrupted');
  }

  onError(errorMessage = 'Error occurred') {
    this.clearTimers();
    this.turnHadToolCall = false;
    this.transitionTo(ORB_STATES.ERROR, errorMessage);

    // Terminal cleanup: auto-revert to idle or listening after error interval
    this.modeTimer = setTimeout(() => {
      if (this.currentState === ORB_STATES.ERROR) {
        this.transitionTo(ORB_STATES.IDLE);
      }
    }, this.errorVisibleMs);
  }

  onDisconnect() {
    this.clearTimers();
    this.turnHadToolCall = false;
    this.transitionTo(ORB_STATES.IDLE, 'Disconnected');
  }

  onConnect(isMicActive = false) {
    this.clearTimers();
    this.transitionTo(isMicActive ? ORB_STATES.LISTENING : ORB_STATES.IDLE, 'Connected');
  }

  destroy() {
    this.clearTimers();
    this.onStateChange = null;
  }
}