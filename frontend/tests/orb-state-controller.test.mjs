import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { OrbStateController, ORB_STATES } from '../js/state/orb-state-controller.js';

describe('OrbStateController Regression Suite', () => {
  let controller;
  let transitions;

  beforeEach(() => {
    transitions = [];
    controller = new OrbStateController({
      initialState: ORB_STATES.IDLE,
      minSearchMs: 50,
      doneVisibleMs: 80,
      errorVisibleMs: 100,
      onStateChange: (state, info) => {
        transitions.push({ state, ...info });
      },
    });
  });

  test('starts at initial state (idle)', () => {
    assert.equal(controller.getState(), ORB_STATES.IDLE);
    assert.equal(transitions.length, 0);
  });

  test('valid transitions update state and notify listener', () => {
    controller.transitionTo(ORB_STATES.LISTENING, 'Ready');
    assert.equal(controller.getState(), ORB_STATES.LISTENING);
    assert.equal(transitions.length, 1);
    assert.equal(transitions[0].state, ORB_STATES.LISTENING);
    assert.equal(transitions[0].prevState, ORB_STATES.IDLE);
    assert.equal(transitions[0].detail, 'Ready');

    controller.transitionTo(ORB_STATES.THINKING);
    assert.equal(controller.getState(), ORB_STATES.THINKING);
    assert.equal(transitions[1].prevState, ORB_STATES.LISTENING);
  });

  test('rejects invalid state and preserves current state', () => {
    controller.transitionTo('invalid_state_xyz');
    assert.equal(controller.getState(), ORB_STATES.IDLE);
    assert.equal(transitions.length, 0);
  });

  test('handles tool call and tool complete lifecycle', async () => {
    controller.onToolCall('Searching web...');
    assert.equal(controller.getState(), ORB_STATES.SEARCHING);

    controller.onToolComplete('Search complete.');
    // Should still be searching until minSearchMs elapses
    await new Promise((resolve) => setTimeout(resolve, 60));
    assert.equal(controller.getState(), ORB_STATES.DONE);

    // After doneVisibleMs, should automatically settle to listening
    await new Promise((resolve) => setTimeout(resolve, 90));
    assert.equal(controller.getState(), ORB_STATES.LISTENING);
  });

  test('interruption immediately cancels pending operations and returns to listening', async () => {
    controller.onToolCall('Running long task...');
    assert.equal(controller.getState(), ORB_STATES.SEARCHING);

    controller.onInterrupted();
    assert.equal(controller.getState(), ORB_STATES.LISTENING);

    // Ensure no delayed timers kick in later to overwrite listening
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.equal(controller.getState(), ORB_STATES.LISTENING);
  });

  test('terminal error state cleans up and auto-reverts to idle without running indefinitely', async () => {
    controller.transitionTo(ORB_STATES.THINKING);
    controller.onError('Gateway connection dropped');

    assert.equal(controller.getState(), ORB_STATES.ERROR);

    // After errorVisibleMs, terminal cleanup transitions cleanly to idle
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.equal(controller.getState(), ORB_STATES.IDLE);
  });

  test('gateway disconnect cleans up timers and transitions to idle', async () => {
    controller.onToolCall('Active tool');
    controller.onDisconnect();

    assert.equal(controller.getState(), ORB_STATES.IDLE);
    // Ensure no pending tool timer fires
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(controller.getState(), ORB_STATES.IDLE);
  });

  test('gateway reconnect restores listening if mic is active', () => {
    controller.onConnect(true);
    assert.equal(controller.getState(), ORB_STATES.LISTENING);

    controller.onDisconnect();
    assert.equal(controller.getState(), ORB_STATES.IDLE);

    controller.onConnect(false);
    assert.equal(controller.getState(), ORB_STATES.IDLE);
  });

  test('speech start during search waits for minSearchMs before speaking', async () => {
    controller.onToolCall('Tool running');
    controller.onSpeechStart(true);

    // Should remain in SEARCHING during minimum window
    assert.equal(controller.getState(), ORB_STATES.SEARCHING);

    // After minSearchMs, should transition to SPEAKING
    await new Promise((resolve) => setTimeout(resolve, 70));
    assert.equal(controller.getState(), ORB_STATES.SPEAKING);
  });
});