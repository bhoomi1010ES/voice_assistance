import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import {
  SPEECH_END_COMMIT_GRACE_MS,
  MAX_UTTERANCE_DURATION_MS,
  VoiceSocket,
  VoiceSocketAdapter,
} from '../src/voice/VoiceSocket';

const SESSION_ID = 'test-session-1';

function createStatus(
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

class TestVoiceAdapter implements VoiceSocketAdapter {
  currentStatus = createStatus();
  readonly calls: string[] = [];
  readonly commitDurations: number[] = [];
  private readonly eventListeners = new Set<(event: VoiceGatewayEvent) => void>();
  private readonly vadListeners = new Set<(event: unknown) => void>();

  async connect() {
    this.calls.push('connect');
    this.currentStatus = createStatus({ state: 'CONNECTED', connected: true });
    return this.currentStatus;
  }

  async disconnect() {
    this.calls.push('disconnect');
    this.currentStatus = createStatus();
    return this.currentStatus;
  }

  async startSession() {
    this.calls.push('startSession');
    this.currentStatus = createStatus({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    return this.currentStatus;
  }

  async startTurn() {
    this.calls.push('startTurn');
    this.currentStatus = createStatus({
      state: 'TURN_STARTING',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
      turnActive: true,
    });
    return this.currentStatus;
  }

  async commitAudio(durationMs = 0) {
    this.calls.push('commitAudio');
    this.commitDurations.push(durationMs);
    this.currentStatus = createStatus({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    return this.currentStatus;
  }

  async cancelResponse() {
    this.calls.push('cancelResponse');
    return this.currentStatus;
  }

  async abortAll() {
    this.calls.push('abortAll');
    return this.currentStatus;
  }

  async getStatus() {
    return this.currentStatus;
  }

  async startMicrophone() {
    this.calls.push('startMicrophone');
  }

  async stopMicrophone() {
    this.calls.push('stopMicrophone');
  }

  async requestMicrophonePermission() {
    return 'granted';
  }

  subscribeStatus() {
    return () => undefined;
  }

  subscribeEvent(listener: (event: VoiceGatewayEvent) => void) {
    this.eventListeners.add(listener);
    return () => {
      this.eventListeners.delete(listener);
    };
  }

  subscribeVad(listener: (event: unknown) => void) {
    this.vadListeners.add(listener);
    return () => {
      this.vadListeners.delete(listener);
    };
  }

  emitEvent(event: Partial<VoiceGatewayEvent> & { event: string }) {
    for (const listener of this.eventListeners) {
      listener(event as VoiceGatewayEvent);
    }
  }

  emitVad(event: unknown) {
    for (const listener of this.vadListeners) {
      listener(event);
    }
  }
}

async function prepareReadyTurn(
  options: {
    speechEndGraceMs?: number;
    maxUtteranceDurationMs?: number;
  } = {},
) {
  const adapter = new TestVoiceAdapter();
  const socket = new VoiceSocket({
    adapter,
    now: () => Date.now(),
    ...options,
  });

  await socket.connect();
  adapter.emitEvent({
    event: 'server.session.ready',
    sessionId: SESSION_ID,
    eventId: 'ready-1',
    timestampMs: 1,
  });
  adapter.emitEvent({
    event: 'server.pong',
    sessionId: SESSION_ID,
    eventId: 'pong-1',
    timestampMs: 2,
  });

  await socket.startTurn();
  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'resp-1',
    eventId: 'turn-ready-1',
    timestampMs: 3,
  });

  expect(socket.getSnapshot().turn).toBe('recording');

  return { socket, adapter };
}

describe('Voice Turn Completion Regression Tests (QA-VOICE-001)', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
  });

  test('normal microphone turn automatically commits after speech ends with grace period', async () => {
    const { socket, adapter } = await prepareReadyTurn();

    // User starts speaking "How can you help me?"
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 100,
    });
    expect(socket.getSnapshot().speechDetected).toBe(true);

    // User stops speaking
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 1500,
      timestampMs: 1600,
    });
    expect(socket.getSnapshot().speechDetected).toBe(false);

    // Before grace period expires: no commit yet
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);

    // Advance 600 ms (half of default 1200ms grace period)
    await jest.advanceTimersByTimeAsync(600);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);

    // Advance remaining 600 ms -> reaches 1200 ms grace period
    await jest.advanceTimersByTimeAsync(600);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(1);
    expect(socket.getSnapshot().turn).toBe('committing');
  });

  test('speech resumes during grace period, cancelling pending commit timer', async () => {
    const { socket, adapter } = await prepareReadyTurn();

    // User speaks first phrase: "Tell me a short..."
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 100,
    });
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 800,
      timestampMs: 900,
    });

    // 800ms natural pause (less than 1200ms grace period)
    await jest.advanceTimersByTimeAsync(800);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);

    // User resumes speaking "...joke."
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 1700,
    });

    // Even if another 1000ms passes, the first commit timer was cancelled:
    await jest.advanceTimersByTimeAsync(1000);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);

    // User finally stops speaking:
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 600,
      timestampMs: 2700,
    });

    // Now wait the full 1200ms grace period:
    await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(1);
  });

  test('manual Finish Turn and automatic commit occurring together produce only one commit', async () => {
    const { socket, adapter } = await prepareReadyTurn();

    // User speaks
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 100,
    });
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 1000,
      timestampMs: 1100,
    });

    // Right at the 1200ms mark, user simultaneously taps Finish Turn while timer fires:
    const manualCommitPromise = socket.commitTurn();
    await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);
    await manualCommitPromise;

    // Both calls must result in exactly ONE commitAudio call
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(1);
  });

  test('maximum-duration safety guard finalizes captured audio and prevents duplicate turns', async () => {
    const { socket, adapter } = await prepareReadyTurn({
      maxUtteranceDurationMs: 5000, // Configure 5s max duration for test
    });

    // User speaks continuously
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 100,
    });

    // Wait until max utterance duration (5s) is reached without speech stopped
    await jest.advanceTimersByTimeAsync(5000);

    // Max duration guard must trigger commit
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(1);
    expect(socket.getSnapshot().turn).toBe('committing');

    // Subsequent VAD stopped event must not duplicate commit
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 5100,
      timestampMs: 5200,
    });
    await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(1);
  });

  test('cancellation clears timers and prevents stale commit', async () => {
    const { socket, adapter } = await prepareReadyTurn();

    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 100,
    });
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 1000,
      timestampMs: 1100,
    });

    // User cancels the turn 500ms into grace period
    await jest.advanceTimersByTimeAsync(500);
    await socket.cancelTurn();

    // Advance past original grace period
    await jest.advanceTimersByTimeAsync(1000);

    // No commit must have been sent
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);
    expect(socket.getSnapshot().turn).toBe('idle');
  });

  test('assistant pause clears timers and resume re-arms remaining grace time', async () => {
    const { socket, adapter } = await prepareReadyTurn();

    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STARTED',
      speechDurationMs: 160,
      timestampMs: 100,
    });
    adapter.emitVad({
      event: 'SILERO_VAD_SPEECH_STOPPED',
      speechDurationMs: 1000,
      timestampMs: 1100,
    });

    // 400ms into the 1200ms grace period, app goes to background / visibility false
    await jest.advanceTimersByTimeAsync(400);
    await socket.setAssistantVisible(false);

    // Advance 2000ms while paused: timer must NOT fire while paused
    await jest.advanceTimersByTimeAsync(2000);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);

    // App resumes to foreground
    await socket.setAssistantVisible(true);

    // Still no commit immediately upon resume
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(0);

    // Advance remaining 800ms of grace period:
    await jest.advanceTimersByTimeAsync(800);
    expect(adapter.calls.filter(c => c === 'commitAudio')).toHaveLength(1);
  });
});
