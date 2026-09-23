import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import {
  VoiceSocket,
  VoiceSocketAdapter,
  normalizeVoiceGatewayEvent,
} from '../src/voice/VoiceSocket';

const SESSION_ID = 'session-1';

function status(
  overrides: Partial<VoiceGatewayStatus> = {},
): VoiceGatewayStatus {
  return {
    state: 'DISCONNECTED',
    connected: false,
    sessionStarted: false,
    turnActive: false,
    sessionId: null,
    turnId: null,
    responseId: null,
    framesQueued: 0,
    queueHighWaterMark: 0,
    droppedFrames: 0,
    invalidFrames: 0,
    framesSent: 0,
    bytesSent: 0,
    websocketErrorCount: 0,
    lastServerEvent: null,
    lastServerEventTimestampMs: 0,
    lastError: null,
    ...overrides,
  };
}

class ContinuousChatAdapter implements VoiceSocketAdapter {
  status = status();
  private currentSessionId: string | null = SESSION_ID;
  connectCalls = 0;
  startTurnCalls = 0;
  abortAllCalls = 0;
  resetConversationCalls = 0;
  stopMicrophoneCalls = 0;
  stopPlaybackCalls = 0;
  private recording = false;
  private readonly eventListeners = new Set<
    (event: VoiceGatewayEvent) => void
  >();
  private readonly statusListeners = new Set<
    (next: VoiceGatewayStatus) => void
  >();

  async connect(_url: string): Promise<VoiceGatewayStatus> {
    this.connectCalls += 1;
    throw new Error(
      'E_VOICE_ALREADY_CONNECTED: Voice gateway is already connected.',
    );
  }

  async disconnect() {
    this.status = status();
    return this.status;
  }

  async startSession() {
    this.status = status({
      state: 'SESSION_STARTING',
      connected: true,
    });
    return this.status;
  }

  async startTurn() {
    this.startTurnCalls += 1;
    this.status = status({
      state: 'TURN_STARTING',
      connected: true,
      sessionStarted: true,
      sessionId: this.currentSessionId,
      turnActive: true,
    });
    return this.status;
  }

  async commitAudio() {
    this.status = status({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: this.currentSessionId,
    });
    return this.status;
  }

  async cancelResponse() {
    return this.commitAudio();
  }

  async abortAll() {
    this.abortAllCalls += 1;
    return this.commitAudio();
  }

  async resetConversation() {
    this.resetConversationCalls += 1;
    return this.commitAudio();
  }

  async endSession() {
    return this.disconnect();
  }

  async getStatus() {
    return this.status;
  }

  async startMicrophone() {
    if (this.recording) {
      throw new Error('E_AUDIO_ALREADY_RECORDING: microphone is still active');
    }
    this.recording = true;
  }

  async stopMicrophone() {
    this.stopMicrophoneCalls += 1;
    this.recording = false;
  }

  async stopPlayback() {
    this.stopPlaybackCalls += 1;
    return this.status;
  }

  subscribeStatus(listener: (next: VoiceGatewayStatus) => void) {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  subscribeEvent(listener: (event: VoiceGatewayEvent) => void) {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  emit(event: VoiceGatewayEvent) {
    if (event.event === 'server.session.ready') {
      this.currentSessionId = event.sessionId;
      this.status = status({
        state: 'SESSION_READY',
        connected: true,
        sessionStarted: true,
        sessionId: event.sessionId,
      });
    }
    if (event.event === 'server.turn.ready') {
      this.status = status({
        state: 'STREAMING_AUDIO',
        connected: true,
        sessionStarted: true,
        sessionId: event.sessionId,
        turnId: event.turnId,
        responseId: event.responseId,
        turnActive: true,
      });
    }
    this.eventListeners.forEach(listener => listener(event));
  }
}

const activeSockets: VoiceSocket[] = [];

afterEach(async () => {
  await Promise.all(activeSockets.splice(0).map(socket => socket.dispose()));
  jest.useRealTimers();
});

test('reuses a native connected gateway when the JS snapshot is stale', async () => {
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({
    state: 'SESSION_READY',
    connected: true,
    sessionStarted: true,
    sessionId: SESSION_ID,
  });
  const socket = new VoiceSocket({ adapter });
  activeSockets.push(socket);

  await socket.connect();

  expect(adapter.connectCalls).toBe(0);
  expect(socket.getSnapshot()).toMatchObject({
    connection: 'connected',
    session: 'ready',
    sessionId: SESSION_ID,
  });
});

test('stops capture after a committed turn so the next turn can start', async () => {
  const adapter = new ContinuousChatAdapter();
  const socket = new VoiceSocket({ adapter });
  activeSockets.push(socket);

  adapter.status = status({ state: 'CONNECTED', connected: true });
  await socket.connect();
  adapter.emit({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'session-ready',
    timestampMs: 1,
  });

  await socket.startTurn();
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'turn-ready-1',
    timestampMs: 2,
  });
  await socket.commitTurn();
  adapter.emit({
    event: 'server.turn.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'turn-completed-1',
    timestampMs: 3,
  });

  await socket.startTurn();

  expect(adapter.startTurnCalls).toBe(2);
  expect(adapter.stopMicrophoneCalls).toBeGreaterThanOrEqual(2);
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('failed response suppresses auto-listening until an explicit retry turn', async () => {
  jest.useFakeTimers();
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({ state: 'CONNECTED', connected: true });
  const socket = new VoiceSocket({ adapter });
  activeSockets.push(socket);

  await socket.connect();
  adapter.emit({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'failure-session-ready',
    timestampMs: 1,
  });
  await jest.advanceTimersByTimeAsync(1);
  expect(adapter.startTurnCalls).toBe(1);

  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-failed',
    responseId: 'response-failed',
    eventId: 'failure-turn-ready',
    timestampMs: 2,
  });
  adapter.emit({
    event: 'llm.response.failed',
    sessionId: SESSION_ID,
    turnId: 'turn-failed',
    responseId: 'response-failed',
    eventId: 'failure-response',
    code: 'llm_provider_error',
    timestampMs: 3,
  });

  expect(socket.getSnapshot().turn).toBe('failed');
  expect(adapter.stopMicrophoneCalls).toBeGreaterThanOrEqual(1);

  // A late session-ready/status event must not create a zero-audio turn.
  adapter.emit({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'late-session-ready',
    timestampMs: 4,
  });
  await jest.advanceTimersByTimeAsync(10);
  expect(adapter.startTurnCalls).toBe(1);

  // The user can explicitly recover by starting a fresh turn.
  await socket.startTurn();
  expect(adapter.startTurnCalls).toBe(2);
});

test('starts listening after session ready and keeps capture for the next turn', async () => {
  jest.useFakeTimers();
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({ state: 'CONNECTED', connected: true });
  const socket = new VoiceSocket({ adapter });
  activeSockets.push(socket);

  await socket.connect();
  adapter.emit({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'auto-session-ready',
    timestampMs: 1,
  });
  await jest.advanceTimersByTimeAsync(1);

  expect(adapter.startTurnCalls).toBe(1);
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-auto-1',
    responseId: 'response-auto-1',
    eventId: 'auto-turn-ready-1',
    timestampMs: 2,
  });
  await socket.commitTurn();
  adapter.emit({
    event: 'server.turn.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-auto-1',
    responseId: 'response-auto-1',
    eventId: 'auto-turn-completed-1',
    timestampMs: 3,
  });
  await jest.advanceTimersByTimeAsync(1);

  expect(adapter.startTurnCalls).toBe(2);
  expect(adapter.stopMicrophoneCalls).toBe(1);
  expect(socket.getSnapshot().continuousListening).toBe(true);
});

test('shows the server wait phrase and clears it when real answer text arrives', async () => {
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({
    state: 'SESSION_READY',
    connected: true,
    sessionStarted: true,
    sessionId: SESSION_ID,
  });
  const socket = new VoiceSocket({ adapter, autoStartSession: false });
  activeSockets.push(socket);

  await socket.connect();
  await socket.startTurn();
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-wait-1',
    responseId: 'response-wait-1',
    eventId: 'wait-turn-ready',
    timestampMs: 1,
  });
  adapter.emit({
    event: 'assistant.thinking',
    sessionId: SESSION_ID,
    turnId: 'turn-wait-1',
    responseId: 'response-wait-1',
    eventId: 'wait-thinking',
    timestampMs: 2,
    text: "I'm on it.",
  });

  expect(socket.getSnapshot().waitPhrase).toBe("I'm on it.");
  adapter.emit({
    event: 'assistant.text.delta',
    sessionId: SESSION_ID,
    turnId: 'turn-wait-1',
    responseId: 'response-wait-1',
    eventId: 'wait-delta',
    timestampMs: 3,
    delta: 'Done.',
  });

  expect(socket.getSnapshot().waitPhrase).toBeNull();
});

test('normalizes assistant.thinking text into thinkingText', () => {
  expect(
    normalizeVoiceGatewayEvent({
      event: 'assistant.thinking',
      session_id: 'session-1',
      turn_id: 'turn-1',
      response_id: 'response-1',
      text: "I'm on it.",
    }),
  ).toMatchObject({
    type: 'assistant.thinking',
    thinkingText: "I'm on it.",
  });
});

test('normalizes confirmation.resolved status from the native status field', () => {
  expect(
    normalizeVoiceGatewayEvent({
      event: 'confirmation.resolved',
      session_id: 'session-1',
      turn_id: 'turn-1',
      response_id: 'response-1',
      confirmationId: 'confirmation-1',
      status: 'APPROVED',
    }),
  ).toMatchObject({
    type: 'confirmation.resolved',
    confirmationStatus: 'APPROVED',
  });
});

test('queues a follow-up while waiting and abort prevents automatic restart', async () => {
  jest.useFakeTimers();
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({
    state: 'SESSION_READY',
    connected: true,
    sessionStarted: true,
    sessionId: SESSION_ID,
  });
  const socket = new VoiceSocket({ adapter, autoStartSession: false });
  activeSockets.push(socket);

  await socket.connect();
  await socket.startTurn();
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-queue-1',
    responseId: 'response-queue-1',
    eventId: 'queue-turn-ready-1',
    timestampMs: 1,
  });
  adapter.emit({
    event: 'assistant.thinking',
    sessionId: SESSION_ID,
    turnId: 'turn-queue-1',
    responseId: 'response-queue-1',
    eventId: 'queue-thinking-1',
    timestampMs: 2,
    text: 'Give me a moment.',
  });

  await socket.startTurn({ preserveMicrophone: true });
  expect(socket.getSnapshot().followUpQueued).toBe(true);
  expect(adapter.startTurnCalls).toBe(2);

  await socket.cancelTurn('user_stopped_response');
  jest.runOnlyPendingTimers();
  await Promise.resolve();

  expect(adapter.abortAllCalls).toBe(1);
  expect(socket.getSnapshot().turn).toBe('idle');
  expect(adapter.startTurnCalls).toBe(2);
});

test('resets the server conversation and auto-listens on the replacement session', async () => {
  jest.useFakeTimers();
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({
    state: 'SESSION_READY',
    connected: true,
    sessionStarted: true,
    sessionId: SESSION_ID,
  });
  const socket = new VoiceSocket({ adapter, autoStartSession: false });
  activeSockets.push(socket);

  await socket.connect();
  await socket.startTurn();
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-reset-1',
    responseId: 'response-reset-1',
    eventId: 'reset-turn-ready',
    timestampMs: 1,
  });
  expect(socket.getSnapshot().conversationMessages.length).toBeGreaterThan(0);

  await socket.resetConversation();
  expect(adapter.resetConversationCalls).toBe(1);
  expect(socket.getSnapshot().conversationMessages).toEqual([]);

  adapter.emit({
    event: 'server.conversation.reset',
    sessionId: null,
    turnId: null,
    responseId: null,
    eventId: 'conversation-reset',
    timestampMs: 2,
  });
  adapter.emit({
    event: 'server.session.ready',
    sessionId: 'session-2',
    turnId: null,
    responseId: null,
    eventId: 'replacement-session-ready',
    timestampMs: 3,
  });
  expect(socket.getSnapshot().sessionId).toBe('session-2');
  await jest.advanceTimersByTimeAsync(1);

  expect(socket.getSnapshot().sessionId).toBe('session-2');
  expect(socket.getSnapshot().conversationMessages).toEqual([]);
  expect(adapter.startTurnCalls).toBe(2);
});

test('stop after an auto-listen turn does not schedule another turn', async () => {
  jest.useFakeTimers();
  const adapter = new ContinuousChatAdapter();
  adapter.status = status({ state: 'CONNECTED', connected: true });
  const socket = new VoiceSocket({ adapter });
  activeSockets.push(socket);

  await socket.connect();
  adapter.emit({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'stop-session-ready',
    timestampMs: 1,
  });
  await jest.advanceTimersByTimeAsync(1);

  expect(adapter.startTurnCalls).toBe(1);
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-stop-1',
    responseId: 'response-stop-1',
    eventId: 'stop-turn-ready-1',
    timestampMs: 2,
  });

  await socket.stop('user_stopped');
  await jest.advanceTimersByTimeAsync(20);

  expect(adapter.startTurnCalls).toBe(1);
  expect(adapter.stopMicrophoneCalls).toBeGreaterThanOrEqual(1);
  expect(socket.getSnapshot()).toMatchObject({
    connection: 'disconnected',
    session: 'idle',
    turn: 'idle',
  });
});
