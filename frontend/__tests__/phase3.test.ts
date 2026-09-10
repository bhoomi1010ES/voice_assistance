import {
  normalizeVoiceGatewayEvent,
  VoiceSocket,
  VoiceSocketAdapter,
  VoiceSocketSnapshot,
} from '../src/voice/VoiceSocket';
import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';

const SESSION_ID = 'session-1';

function gatewayStatus(
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

class FakeAppState {
  currentState: 'active' | 'background' = 'active';
  private readonly listeners = new Set<
    (state: 'active' | 'background') => void
  >();

  addEventListener(
    _type: 'change',
    listener: (state: 'active' | 'background') => void,
  ) {
    this.listeners.add(listener);
    return { remove: () => this.listeners.delete(listener) };
  }

  emit(state: 'active' | 'background') {
    this.currentState = state;
    this.listeners.forEach(listener => listener(state));
  }
}

class FakeVoiceAdapter implements VoiceSocketAdapter {
  status = gatewayStatus();
  connectCalls = 0;
  startSessionCalls = 0;
  startSessionResumeIds: Array<string | null> = [];
  startTurnCalls = 0;
  commitCalls = 0;
  disconnectCalls = 0;
  private readonly statusListeners = new Set<
    (status: VoiceGatewayStatus) => void
  >();
  private readonly eventListeners = new Set<
    (event: VoiceGatewayEvent) => void
  >();

  async connect() {
    this.connectCalls += 1;
    this.status = gatewayStatus({ state: 'CONNECTED', connected: true });
    return this.status;
  }

  async disconnect() {
    this.disconnectCalls += 1;
    this.status = gatewayStatus();
    return this.status;
  }

  async startSession(resumeSessionId?: string | null) {
    this.startSessionCalls += 1;
    this.startSessionResumeIds.push(resumeSessionId ?? null);
    this.status = gatewayStatus({
      state: 'SESSION_STARTING',
      connected: true,
    });
    return this.status;
  }

  async startTurn() {
    this.startTurnCalls += 1;
    this.status = gatewayStatus({
      state: 'TURN_STARTING',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
      turnActive: true,
    });
    return this.status;
  }

  async commitAudio(_durationMs: number) {
    this.commitCalls += 1;
    this.status = gatewayStatus({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    return this.status;
  }

  async cancelResponse() {
    return this.commitAudio(0);
  }

  async endSession() {
    this.status = gatewayStatus();
    return this.status;
  }

  async getStatus() {
    return this.status;
  }

  subscribeStatus(listener: (status: VoiceGatewayStatus) => void) {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  subscribeEvent(listener: (event: VoiceGatewayEvent) => void) {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  emitStatus(status: VoiceGatewayStatus) {
    this.status = status;
    this.statusListeners.forEach(listener => listener(status));
  }

  emitEvent(event: VoiceGatewayEvent) {
    if (event.event === 'server.session.ready') {
      this.status = gatewayStatus({
        state: 'SESSION_READY',
        connected: true,
        sessionStarted: true,
        sessionId: event.sessionId,
      });
    }
    if (event.event === 'server.turn.ready') {
      this.status = gatewayStatus({
        state: 'STREAMING_AUDIO',
        connected: true,
        sessionStarted: true,
        sessionId: event.sessionId,
        turnId: event.turnId,
        responseId: event.responseId,
        turnActive: true,
      });
    }
    if (
      event.event === 'server.turn.completed' ||
      event.event === 'response.cancelled'
    ) {
      this.status = gatewayStatus({
        state: 'SESSION_READY',
        connected: true,
        sessionStarted: true,
        sessionId: event.sessionId,
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

function createSocket(
  adapter = new FakeVoiceAdapter(),
  options: ConstructorParameters<typeof VoiceSocket>[0] = {},
) {
  const socket = new VoiceSocket({
    adapter,
    connectTimeoutMs: 30,
    heartbeatTimeoutMs: 100,
    heartbeatCheckIntervalMs: 10,
    reconnectDelaysMs: [5, 10],
    ...options,
  });
  activeSockets.push(socket);
  return { socket, adapter };
}

async function prepareSession(socket: VoiceSocket, adapter: FakeVoiceAdapter) {
  await socket.connect();
  await socket.startSession();
  adapter.emitEvent({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'session-ready-1',
    timestampMs: 1,
  });
}

test('normalizes only bounded known event metadata', () => {
  expect(
    normalizeVoiceGatewayEvent({
      event: 'server.pong',
      sessionId: null,
      turnId: null,
      responseId: null,
      eventId: 'pong-1',
      timestampMs: 10,
    }),
  ).toEqual({
    type: 'server.pong',
    eventId: 'pong-1',
    sessionId: null,
    turnId: null,
    responseId: null,
    timestampMs: 10,
  });
  expect(
    normalizeVoiceGatewayEvent({ event: 'unknown', payload: 'private' }),
  ).toBeNull();
  expect(
    normalizeVoiceGatewayEvent({
      event: 'server.pong',
      payload: 'x'.repeat(16 * 1024),
    }),
  ).toBeNull();
  expect(normalizeVoiceGatewayEvent('x'.repeat(16 * 1024 + 1))).toBeNull();
});

test('connects once and exposes the authenticated gateway state', async () => {
  const { socket, adapter } = createSocket();
  await Promise.all([socket.connect(), socket.connect()]);

  expect(adapter.connectCalls).toBe(1);
  expect(socket.getSnapshot()).toMatchObject<Partial<VoiceSocketSnapshot>>({
    connection: 'connected',
    heartbeat: 'healthy',
  });
});

test('rejects duplicate, stale-response, and out-of-order events', async () => {
  const { socket, adapter } = createSocket();
  await prepareSession(socket, adapter);
  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'turn-ready-1',
    timestampMs: 2,
  });
  const acceptedSequence = socket.getSnapshot().eventSequence;

  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'turn-ready-1',
    timestampMs: 2,
  });
  adapter.emitEvent({
    event: 'llm.response.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'old-response',
    eventId: 'old-response-1',
    timestampMs: 3,
  });
  adapter.emitEvent({
    event: 'server.pong',
    sessionId: null,
    turnId: null,
    responseId: null,
    eventId: 'out-of-order',
    timestampMs: 1,
  });

  expect(socket.getSnapshot().eventSequence).toBe(acceptedSequence);
  expect(socket.getSnapshot().droppedEventCount).toBe(3);
  expect(socket.getSnapshot().responseId).toBe('response-1');
});

test('does not accept gateway events while reconnecting or after a stale session', async () => {
  const { socket, adapter } = createSocket();
  await prepareSession(socket, adapter);
  adapter.emitStatus(
    gatewayStatus({
      state: 'DISCONNECTED',
      connected: false,
      sessionStarted: true,
      sessionId: SESSION_ID,
    }),
  );
  const droppedBeforeReconnectEvent = socket.getSnapshot().droppedEventCount;
  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'ghost-turn',
    responseId: 'ghost-response',
    eventId: 'ghost-event',
    timestampMs: 2,
  });
  expect(socket.getSnapshot().droppedEventCount).toBe(
    droppedBeforeReconnectEvent + 1,
  );
  expect(socket.getSnapshot().turn).toBe('idle');

  await socket.disconnect();
  await socket.connect();
  await socket.startSession();
  const newSessionId = 'session-2';
  adapter.emitEvent({
    event: 'server.session.ready',
    sessionId: newSessionId,
    turnId: null,
    responseId: null,
    eventId: 'session-ready-2',
    timestampMs: 3,
  });
  adapter.emitEvent({
    event: 'voice.session.stale.reaped',
    sessionId: newSessionId,
    turnId: null,
    responseId: null,
    eventId: 'stale-session-1',
    timestampMs: 4,
  });
  expect(socket.getSnapshot()).toMatchObject({
    connection: 'connected',
    session: 'idle',
    sessionId: null,
    turnId: null,
    responseId: null,
  });
});

test('returns to ready after ten consecutive manual turns', async () => {
  const { socket, adapter } = createSocket();
  await prepareSession(socket, adapter);

  for (let index = 1; index <= 10; index += 1) {
    await socket.startTurn();
    adapter.emitEvent({
      event: 'server.turn.ready',
      sessionId: SESSION_ID,
      turnId: `turn-${index}`,
      responseId: `response-${index}`,
      eventId: `turn-ready-${index}`,
      timestampMs: index * 3,
    });
    await socket.commitTurn();
    adapter.emitEvent({
      event: 'server.turn.completed',
      sessionId: SESSION_ID,
      turnId: `turn-${index}`,
      responseId: `response-${index}`,
      eventId: `turn-completed-${index}`,
      timestampMs: index * 3 + 1,
    });
    expect(socket.getSnapshot()).toMatchObject({
      connection: 'connected',
      session: 'ready',
      turn: 'idle',
      turnId: null,
      responseId: null,
    });
  }
  expect(adapter.startTurnCalls).toBe(10);
  expect(adapter.commitCalls).toBe(10);
});

test('reconciles foreground state without creating a duplicate session', async () => {
  const appState = new FakeAppState();
  const { socket, adapter } = createSocket(adapterForState(), { appState });
  await prepareSession(socket, adapter);
  const startSessionCalls = adapter.startSessionCalls;

  appState.emit('background');
  appState.emit('active');
  await Promise.resolve();

  expect(adapter.startSessionCalls).toBe(startSessionCalls);
  expect(socket.getSnapshot().session).toBe('ready');
});

test('retires an unavailable resumed session and reconnects without replaying it', async () => {
  jest.useFakeTimers();
  const appState = new FakeAppState();
  const { socket, adapter } = createSocket(new FakeVoiceAdapter(), {
    appState,
  });
  await prepareSession(socket, adapter);

  adapter.emitStatus(
    gatewayStatus({
      state: 'DISCONNECTED',
      sessionStarted: true,
      sessionId: SESSION_ID,
    }),
  );
  await jest.advanceTimersByTimeAsync(5);
  await Promise.resolve();

  expect(adapter.connectCalls).toBe(2);
  expect(adapter.startSessionResumeIds).toEqual([null, SESSION_ID]);

  // Native status is delivered before the correlated server event on Android.
  adapter.emitStatus(
    gatewayStatus({
      state: 'ERROR',
      sessionStarted: true,
      sessionId: SESSION_ID,
    }),
  );
  adapter.emitEvent({
    event: 'server.error',
    sessionId: null,
    turnId: null,
    responseId: null,
    eventId: 'resume-rejected-1',
    timestampMs: 2,
    code: 'session_not_available',
  });
  adapter.emitStatus(gatewayStatus({ state: 'CLOSING' }));

  expect(socket.getSnapshot()).toMatchObject({
    connection: 'reconnecting',
    session: 'idle',
    turn: 'idle',
    sessionId: null,
    turnId: null,
    responseId: null,
  });

  await jest.advanceTimersByTimeAsync(10);
  await Promise.resolve();

  expect(adapter.connectCalls).toBe(3);
  expect(adapter.startSessionResumeIds).toEqual([null, SESSION_ID]);
  expect(socket.getSnapshot()).toMatchObject({
    connection: 'connected',
    session: 'idle',
    sessionId: null,
  });

  adapter.emitEvent({
    event: 'server.pong',
    sessionId: null,
    turnId: null,
    responseId: null,
    eventId: 'stable-pong-1',
    timestampMs: 3,
  });
  expect(socket.getSnapshot().reconnectAttempt).toBe(0);
});

test('keeps the reconnect budget across sockets that open then immediately close', async () => {
  jest.useFakeTimers();
  const appState = new FakeAppState();
  const { socket, adapter } = createSocket(new FakeVoiceAdapter(), {
    appState,
  });
  await socket.connect();

  adapter.emitStatus(gatewayStatus());
  await jest.advanceTimersByTimeAsync(5);
  expect(adapter.connectCalls).toBe(2);
  expect(socket.getSnapshot().reconnectAttempt).toBe(1);

  adapter.emitStatus(gatewayStatus());
  await jest.advanceTimersByTimeAsync(10);
  expect(adapter.connectCalls).toBe(3);
  expect(socket.getSnapshot().reconnectAttempt).toBe(2);

  adapter.emitStatus(gatewayStatus());
  expect(socket.getSnapshot().connection).toBe('failed');
  await jest.advanceTimersByTimeAsync(100);
  expect(adapter.connectCalls).toBe(3);
});

test('bounds reconnect and stops it during logout', async () => {
  jest.useFakeTimers();
  const { socket, adapter } = createSocket();
  await socket.connect();
  adapter.emitStatus(gatewayStatus());
  expect(socket.getSnapshot().connection).toBe('reconnecting');

  await socket.stop('logout');
  jest.runOnlyPendingTimers();
  await Promise.resolve();

  expect(adapter.connectCalls).toBe(1);
  expect(socket.getSnapshot().connection).toBe('disconnected');
});

function adapterForState() {
  return new FakeVoiceAdapter();
}
