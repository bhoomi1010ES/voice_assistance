import {
  applyTranscriptEvent,
  createTranscriptPlaceholder,
  mapTranscriptError,
  VoiceTranscriptMessage,
} from '../src/voice/transcript';
import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import {
  normalizeVoiceGatewayEvent,
  VoiceSocket,
  VoiceSocketAdapter,
} from '../src/voice/VoiceSocket';

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

class FakeVoiceAdapter implements VoiceSocketAdapter {
  status = gatewayStatus();
  private readonly statusListeners = new Set<
    (status: VoiceGatewayStatus) => void
  >();
  private readonly eventListeners = new Set<
    (event: VoiceGatewayEvent) => void
  >();
  private readonly vadListeners = new Set<(event: unknown) => void>();

  async connect() {
    this.status = gatewayStatus({ state: 'CONNECTED', connected: true });
    return this.status;
  }

  async disconnect() {
    this.status = gatewayStatus();
    return this.status;
  }

  async startSession() {
    this.status = gatewayStatus({ state: 'SESSION_STARTING', connected: true });
    return this.status;
  }

  async startTurn() {
    this.status = gatewayStatus({
      state: 'TURN_STARTING',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
      turnActive: true,
    });
    return this.status;
  }

  async commitAudio() {
    this.status = gatewayStatus({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    return this.status;
  }

  async cancelResponse() {
    return this.commitAudio();
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

  subscribeVad(listener: (event: unknown) => void) {
    this.vadListeners.add(listener);
    return () => this.vadListeners.delete(listener);
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
    this.eventListeners.forEach(listener => listener(event));
  }

  emitVad(event: unknown) {
    this.vadListeners.forEach(listener => listener(event));
  }
}

const activeSockets: VoiceSocket[] = [];

afterEach(async () => {
  await Promise.all(activeSockets.splice(0).map(socket => socket.dispose()));
  jest.useRealTimers();
});

function createSocket() {
  const adapter = new FakeVoiceAdapter();
  const socket = new VoiceSocket({
    adapter,
    connectTimeoutMs: 30,
    heartbeatTimeoutMs: 100,
    heartbeatCheckIntervalMs: 10,
  });
  activeSockets.push(socket);
  return { socket, adapter };
}

async function prepareTurn() {
  const { socket, adapter } = createSocket();
  await socket.connect();
  await socket.startSession();
  adapter.emitEvent({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    turnId: null,
    responseId: null,
    eventId: 'session-ready',
    timestampMs: 1,
  });
  await socket.startTurn();
  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'turn-ready',
    timestampMs: 2,
  });
  return { socket, adapter };
}

function transcriptEvent(
  event: 'transcript.partial' | 'transcript.final',
  overrides: Partial<VoiceGatewayEvent> = {},
): VoiceGatewayEvent {
  return {
    event,
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: `${event}-1`,
    timestampMs: 10,
    text: event === 'transcript.final' ? 'final words' : 'partial words',
    final: event === 'transcript.final',
    transcriptSequence: event === 'transcript.final' ? 3 : 1,
    language: 'en',
    audioDurationMs: 900,
    metrics: {},
    ...overrides,
  };
}

test('normalizes genuine provider transcript payloads without provider branching', () => {
  expect(
    normalizeVoiceGatewayEvent(transcriptEvent('transcript.final')),
  ).toMatchObject({
    type: 'transcript.final',
    transcript: {
      kind: 'final',
      text: 'final words',
      sequence: 3,
      audioDurationMs: 900,
    },
  });
});

test('partial transcript updates in order and final replaces it atomically', () => {
  let messages: VoiceTranscriptMessage[] = createTranscriptPlaceholder(
    [],
    'turn-1',
    'response-1',
    0,
  );
  const partial = applyTranscriptEvent(
    messages,
    {
      kind: 'partial',
      sessionId: SESSION_ID,
      turnId: 'turn-1',
      responseId: 'response-1',
      text: 'hello',
      sequence: 1,
      timestampMs: 1,
      language: 'en',
      audioDurationMs: 300,
      metrics: {},
    },
    20,
  );
  messages = partial.messages;
  expect(messages[0]).toMatchObject({
    status: 'partial',
    text: 'hello',
    final: false,
  });

  const final = applyTranscriptEvent(
    messages,
    {
      kind: 'final',
      sessionId: SESSION_ID,
      turnId: 'turn-1',
      responseId: 'response-1',
      text: 'hello world',
      sequence: 2,
      timestampMs: 2,
      language: 'en',
      audioDurationMs: 700,
      metrics: { speech_end_to_final_transcript_ms: 125 },
    },
    145,
  );
  expect(final.accepted).toBe(true);
  expect(final.messages).toHaveLength(1);
  expect(final.messages[0]).toMatchObject({
    status: 'final',
    final: true,
    text: 'hello world',
    serverSpeechEndToFinalMs: 125,
  });
});

test('final-only endpoint stays transcribing and never fabricates a partial', async () => {
  const { socket, adapter } = await prepareTurn();
  await socket.commitTurn();

  expect(socket.getSnapshot().transcriptMessages[0]).toMatchObject({
    status: 'transcribing',
    text: '',
  });
  expect(socket.getSnapshot().transcriptMessages[0].status).not.toBe('partial');

  adapter.emitEvent(transcriptEvent('transcript.final'));
  expect(socket.getSnapshot().transcriptMessages[0]).toMatchObject({
    status: 'final',
    text: 'final words',
    final: true,
  });
});

test('blank final transcript becomes an actionable STT error without a final message', async () => {
  const { socket, adapter } = await prepareTurn();
  await socket.commitTurn();
  adapter.emitEvent(
    transcriptEvent('transcript.final', {
      eventId: 'blank-final',
      text: '',
    }),
  );

  expect(socket.getSnapshot()).toMatchObject({
    turn: 'failed',
    transcriptError: {
      code: 'stt_empty_transcript',
      retryable: true,
    },
  });
  expect(socket.getSnapshot().transcriptMessages[0]).toMatchObject({
    status: 'error',
    text: '',
    final: false,
  });
});

test('late final after cancellation is ignored and cannot enter transcript history', async () => {
  const { socket, adapter } = await prepareTurn();
  await socket.cancelTurn();
  adapter.emitEvent(
    transcriptEvent('transcript.final', { eventId: 'late-final' }),
  );

  expect(socket.getSnapshot().transcriptMessages[0]).toMatchObject({
    status: 'cancelled',
    text: '',
    final: false,
  });
  expect(socket.getSnapshot().droppedEventCount).toBeGreaterThan(0);
});

test('VAD transitions expose speech detected and return to listening', async () => {
  const { socket, adapter } = await prepareTurn();
  adapter.emitVad({ event: 'SILERO_VAD_SPEECH_STARTED', timestampMs: 20 });
  expect(socket.getSnapshot()).toMatchObject({
    turn: 'speech_detected',
    speechDetected: true,
  });

  adapter.emitVad({ event: 'SILERO_VAD_SPEECH_STOPPED', timestampMs: 30 });
  expect(socket.getSnapshot()).toMatchObject({
    turn: 'recording',
    speechDetected: false,
  });
  expect(
    socket.getSnapshot().transcriptMessages[0].speechEndedAtMs,
  ).not.toBeNull();
});

test('STT error codes have safe, distinct retry guidance', () => {
  expect(mapTranscriptError('stt_rate_limited')).toMatchObject({
    code: 'stt_rate_limited',
    retryable: true,
  });
  expect(mapTranscriptError('stt_empty_audio').message).toMatch(/didn't hear/i);
  expect(mapTranscriptError('stt_configuration_error').retryable).toBe(false);
});

test('turn failure maps the stable backend STT code and preserves retry state', async () => {
  const { socket, adapter } = await prepareTurn();
  adapter.emitEvent({
    event: 'server.turn.failed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'turn-failed',
    timestampMs: 20,
    code: 'stt_timeout',
  });

  expect(socket.getSnapshot()).toMatchObject({
    turn: 'failed',
    transcriptError: {
      code: 'stt_timeout',
      retryable: true,
    },
  });
  expect(socket.getSnapshot().transcriptMessages[0].status).toBe('error');
});
