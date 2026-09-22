import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import {
  SPEECH_END_COMMIT_GRACE_MS,
  VoiceSocket,
  VoiceSocketAdapter,
} from '../src/voice/VoiceSocket';
import {
  DEFAULT_VOICE_ROLLOUT_CONFIG,
  VoiceRolloutConfig,
} from '../src/config/voiceRollout';

const SESSION_ID = 'phase9-session';

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

class BargeInAdapter implements VoiceSocketAdapter {
  currentStatus = status();
  readonly calls: string[] = [];
  readonly turnIncludesPreRoll: boolean[] = [];
  readonly cancelReasons: Array<string | null | undefined> = [];
  cancelResponseDelayMs = 0;
  private readonly eventListeners = new Set<
    (event: VoiceGatewayEvent) => void
  >();
  private readonly vadListeners = new Set<(event: unknown) => void>();

  async connect() {
    this.currentStatus = status({ state: 'CONNECTED', connected: true });
    return this.currentStatus;
  }

  async disconnect() {
    this.currentStatus = status();
    return this.currentStatus;
  }

  async startSession() {
    this.currentStatus = status({
      state: 'SESSION_STARTING',
      connected: true,
    });
    return this.currentStatus;
  }

  async startTurn(_clientTurnId?: string | null, includePreRoll = false) {
    this.calls.push('startTurn');
    this.turnIncludesPreRoll.push(includePreRoll);
    this.currentStatus = status({
      state: 'TURN_STARTING',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
      turnActive: true,
    });
    return this.currentStatus;
  }

  async commitAudio() {
    this.calls.push('commitAudio');
    this.currentStatus = status({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    return this.currentStatus;
  }

  async cancelResponse(reason?: string | null) {
    this.calls.push('cancelResponse');
    this.cancelReasons.push(reason);
    this.currentStatus = status({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    if (this.cancelResponseDelayMs > 0) {
      await new Promise<void>(resolve =>
        setTimeout(resolve, this.cancelResponseDelayMs),
      );
    }
    return this.currentStatus;
  }

  async stopPlayback() {
    this.calls.push('stopPlayback');
    return this.currentStatus;
  }

  async endSession() {
    return this.disconnect();
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
    return () => this.eventListeners.delete(listener);
  }

  subscribeVad(listener: (event: unknown) => void) {
    this.vadListeners.add(listener);
    return () => this.vadListeners.delete(listener);
  }

  emitEvent(event: VoiceGatewayEvent) {
    if (event.event === 'server.session.ready') {
      this.currentStatus = status({
        state: 'SESSION_READY',
        connected: true,
        sessionStarted: true,
        sessionId: SESSION_ID,
      });
    }
    if (event.event === 'server.turn.ready') {
      this.currentStatus = status({
        state: 'STREAMING_AUDIO',
        connected: true,
        sessionStarted: true,
        sessionId: SESSION_ID,
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

class NativeBargeInAdapter extends BargeInAdapter {
  private readonly nativeListeners = new Set<(event: unknown) => void>();

  subscribeBargeIn(listener: (event: unknown) => void) {
    this.nativeListeners.add(listener);
    return () => this.nativeListeners.delete(listener);
  }

  emitBargeIn(event: unknown) {
    this.nativeListeners.forEach(listener => listener(event));
  }
}

const activeSockets: VoiceSocket[] = [];

afterEach(async () => {
  await Promise.all(activeSockets.splice(0).map(socket => socket.dispose()));
  jest.useRealTimers();
});

async function prepareTurn(
  adapter: BargeInAdapter = new BargeInAdapter(),
  rollout: VoiceRolloutConfig = adapter instanceof NativeBargeInAdapter
    ? DEFAULT_VOICE_ROLLOUT_CONFIG
    : {
        ...DEFAULT_VOICE_ROLLOUT_CONFIG,
        nativeBargeInDetectorEnabled: false,
        legacyJsBargeInDetectorEnabled: true,
      },
) {
  const socket = new VoiceSocket({ adapter, rollout });
  activeSockets.push(socket);
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
  await socket.commitTurn();
  adapter.emitEvent({
    event: 'tts.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'tts-started',
    timestampMs: 3,
  });
  adapter.emitEvent({
    event: 'tts.playback.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'playback-started',
    timestampMs: 4,
  });
  return { socket, adapter };
}

test('native confirmation stops locally before cancellation and replacement turn', async () => {
  const adapter = new NativeBargeInAdapter();
  const { socket } = await prepareTurn(adapter);

  adapter.emitBargeIn({
    event: 'BARGE_IN_CONFIRMED',
    state: 'NEAR_END_CONFIRMED',
    reason: 'near_end_confirmed',
    responseId: 'response-1',
    monotonicNs: '1000000000',
    captureStartNs: '980000000',
    captureEndNs: '1000000000',
    segmentDurationMs: 480,
    probability: 0.98,
    playbackState: 'PLAYING',
    playbackActive: true,
    playbackPositionMs: 1000,
    referenceReady: true,
    referenceUsable: true,
    timestampConfidence: 'HIGH',
    aecHealthy: true,
    communicationModeActive: true,
    aecAvailable: true,
    aecEnabled: true,
    aecEffectiveness: 'ENABLED',
    sourceFrameSequenceStart: 10,
    sourceFrameSequenceEnd: 33,
    inferenceIndex: 4,
    discontinuous: false,
    localStopRequested: true,
    localStopCompleted: true,
    audioTrackStopped: true,
    audioTrackFlushed: true,
    audioTrackReleased: true,
    localStopReleasePending: false,
    stopReason: 'barge_in',
    stopRequestedMonotonicNs: '1001000000',
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls.indexOf('cancelResponse')).toBeGreaterThanOrEqual(0);
  expect(adapter.calls.lastIndexOf('startTurn')).toBeGreaterThan(
    adapter.calls.indexOf('cancelResponse'),
  );
  expect(adapter.turnIncludesPreRoll).toEqual([false, true]);
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('barge-in playback stop cannot finalize before native confirmation', async () => {
  const adapter = new NativeBargeInAdapter();
  const { socket } = await prepareTurn(adapter);

  adapter.emitEvent({
    event: 'server.turn.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'server-completed-before-native-stop',
    timestampMs: 5,
  });
  adapter.emitEvent({
    event: 'tts.playback.stopped',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'native-barge-in-stop',
    timestampMs: 6,
    stopReason: 'barge_in',
  });

  expect(socket.getSnapshot().turnId).toBe('turn-1');
  expect(socket.getSnapshot().responseId).toBe('response-1');

  adapter.emitBargeIn({
    event: 'BARGE_IN_CONFIRMED',
    responseId: 'response-1',
    reason: 'near_end_confirmed',
    localStopRequested: true,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.cancelReasons).toContain('barge_in');
  expect(adapter.turnIncludesPreRoll).toEqual([false, true]);
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('native confirmation interrupts when local stop reports inactive', async () => {
  const adapter = new NativeBargeInAdapter();
  const { socket } = await prepareTurn(adapter);

  adapter.emitBargeIn({
    event: 'BARGE_IN_CONFIRMED',
    responseId: 'response-1',
    reason: 'near_end_confirmed',
    localStopRequested: false,
    localStopCompleted: false,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.cancelReasons).toContain('barge_in');
  expect(adapter.turnIncludesPreRoll).toEqual([false, true]);
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('native confirmation interrupts after the old turn id was finalized', async () => {
  const adapter = new NativeBargeInAdapter();
  const { socket } = await prepareTurn(adapter);

  adapter.emitEvent({
    event: 'server.turn.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'server-completed-before-untagged-stop',
    timestampMs: 5,
  });
  adapter.emitEvent({
    event: 'tts.playback.stopped',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'untagged-stop',
    timestampMs: 6,
  });
  expect(socket.getSnapshot().turnId).toBeNull();
  expect(socket.getSnapshot().responseId).toBeNull();

  adapter.emitBargeIn({
    event: 'BARGE_IN_CONFIRMED',
    responseId: 'response-1',
    reason: 'near_end_confirmed',
    localStopRequested: false,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.cancelReasons).toContain('barge_in');
  expect(adapter.turnIncludesPreRoll).toEqual([false, true]);
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('natural playback completion still finalizes and resumes listening', async () => {
  const adapter = new NativeBargeInAdapter();
  const { socket } = await prepareTurn(adapter);

  adapter.emitEvent({
    event: 'server.turn.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'server-completed-before-natural-playback',
    timestampMs: 5,
  });
  adapter.emitEvent({
    event: 'tts.playback.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'natural-playback-completed',
    timestampMs: 6,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('cancelResponse');
  expect(adapter.turnIncludesPreRoll).toEqual([false, false]);
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('diagnostics-only mode keeps explicit playback stop but disables automatic interruption', async () => {
  const adapter = new BargeInAdapter();
  const { socket } = await prepareTurn(adapter, {
    ...DEFAULT_VOICE_ROLLOUT_CONFIG,
    nativeBargeInDetectorEnabled: false,
    legacyJsBargeInDetectorEnabled: false,
  });

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.99,
    timestampMs: 5000,
    playbackActive: true,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.99,
    timestampMs: 5600,
    playbackActive: true,
    speechDurationMs: 600,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(socket.getSnapshot().responseId).toBe('response-1');
});

test('native shadow mode does not let native metadata perform local stop', async () => {
  const adapter = new NativeBargeInAdapter();
  const { socket } = await prepareTurn(adapter, {
    ...DEFAULT_VOICE_ROLLOUT_CONFIG,
    legacyJsBargeInDetectorEnabled: true,
  });

  adapter.emitBargeIn({
    event: 'BARGE_IN_CONFIRMED',
    responseId: 'response-1',
    localStopRequested: true,
    reason: 'near_end_confirmed',
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(socket.getSnapshot().responseId).toBe('response-1');
});

test('spoken confirmation starts a hands-free auto-committing answer turn', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitEvent({
    event: 'confirmation.required',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'confirmation-required',
    confirmationId: 'confirmation-1',
    toolCallId: 'call-1',
    toolName: 'create_task',
    status: 'PENDING',
    timestampMs: 5,
  });
  expect(socket.getSnapshot().confirmationAwaitingVoice).toBe(true);

  adapter.emitEvent({
    event: 'server.turn.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'confirmation-turn-completed',
    timestampMs: 6,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(1);

  adapter.emitEvent({
    event: 'tts.playback.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'confirmation-playback-completed',
    timestampMs: 7,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(2);
  expect(socket.getSnapshot().turn).toBe('starting');

  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'confirmation-answer-ready',
    timestampMs: 8,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    speechDurationMs: 160,
    timestampMs: 9,
  });
  jest.useFakeTimers();
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STOPPED',
    speechDurationMs: 500,
    timestampMs: 10,
  });
  expect(adapter.calls.filter(call => call === 'commitAudio')).toHaveLength(1);
  await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);
  expect(adapter.calls.filter(call => call === 'commitAudio')).toHaveLength(2);
});

test('does not interrupt a pending confirmation prompt from playback VAD', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitEvent({
    event: 'confirmation.required',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'confirmation-required',
    confirmationId: 'confirmation-1',
    toolCallId: 'call-1',
    toolName: 'create_task',
    status: 'PENDING',
    timestampMs: 5,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.99,
    speechDurationMs: 160,
    timestampMs: 1_505,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.99,
    speechDurationMs: 2_000,
    timestampMs: 2_500,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(socket.getSnapshot().confirmationAwaitingVoice).toBe(true);
});

test('ignores Silero activity events as standalone playback interruptions', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.99,
    speechDurationMs: 2_000,
    timestampMs: 1_505,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');
});

test('keeps capture alive after commit for playback-time VAD', async () => {
  const { socket, adapter } = await prepareTurn();

  expect(adapter.calls.filter(call => call === 'stopMicrophone')).toHaveLength(
    1,
  );
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');

  await socket.dispose();
});

test('Silero confirmed speech stops playback, cancels the old response, and starts a new turn', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.9,
    speechDurationMs: 0,
    timestampMs: 1_505,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.9,
    speechDurationMs: 480,
    timestampMs: 1_985,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls.indexOf('stopPlayback')).toBeGreaterThanOrEqual(0);
  expect(adapter.calls.indexOf('cancelResponse')).toBeGreaterThan(
    adapter.calls.indexOf('stopPlayback'),
  );
  expect(adapter.calls.lastIndexOf('startTurn')).toBeGreaterThan(
    adapter.calls.indexOf('cancelResponse'),
  );
  expect(adapter.turnIncludesPreRoll).toEqual([false, true]);
  expect(socket.getSnapshot().turn).toBe('starting');

  adapter.emitEvent({
    event: 'tts.playback.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'stale-playback',
    timestampMs: 6,
  });
  expect(socket.getSnapshot().ttsPlaybackState).toBe('idle');
});

test('does not interrupt playback from a single confirmed VAD transition', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.99,
    speechDurationMs: 160,
    timestampMs: 1_505,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');
});

test('rejects a Silero candidate during the measured playback startup echo window', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.99,
    speechDurationMs: 160,
    timestampMs: 1_200,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(1);
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');
});

test('does not reclassify a continuous guard-started echo after the guard expires', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.98,
    speechDurationMs: 0,
    timestampMs: 500,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.99,
    speechDurationMs: 2_000,
    timestampMs: 2_500,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(1);
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');
});

test('does not interrupt when a high-confidence candidate matches rendered TTS', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.99,
    speechDurationMs: 160,
    timestampMs: 1_505,
    playbackActive: true,
    playbackState: 'TTS_PLAYING',
    playbackReferenceAvailable: true,
    echoLikely: true,
    echoSimilarity: 0.92,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.99,
    speechDurationMs: 2_000,
    timestampMs: 3_505,
    playbackActive: true,
    playbackState: 'TTS_PLAYING',
    playbackReferenceAvailable: true,
    echoLikely: true,
    echoSimilarity: 0.92,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls).not.toContain('cancelResponse');
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(1);
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');
});

test('keeps the playback guard anchored when playback-start is reported twice', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitEvent({
    event: 'tts.playback.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'duplicate-playback-start',
    timestampMs: 500,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.9,
    speechDurationMs: 160,
    timestampMs: 1_301,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.9,
    speechDurationMs: 480,
    timestampMs: 1_781,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).toContain('stopPlayback');
  expect(adapter.calls).toContain('cancelResponse');
  expect(socket.getSnapshot().turn).toBe('starting');
});

test('creates and commits the replacement turn before delayed cancellation acknowledgement', async () => {
  const { socket, adapter } = await prepareTurn();
  adapter.cancelResponseDelayMs = 250;

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.9,
    speechDurationMs: 160,
    timestampMs: 1_505,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.9,
    speechDurationMs: 480,
    timestampMs: 1_985,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).toEqual(
    expect.arrayContaining(['stopPlayback', 'cancelResponse', 'startTurn']),
  );
  expect(adapter.calls.filter(call => call === 'cancelResponse')).toHaveLength(
    1,
  );
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(2);
  expect(adapter.calls.lastIndexOf('startTurn')).toBeGreaterThan(
    adapter.calls.indexOf('cancelResponse'),
  );
  expect(adapter.turnIncludesPreRoll).toEqual([false, true]);

  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'new-turn-ready',
    timestampMs: 6,
  });
  jest.useFakeTimers();
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STOPPED',
    speechDurationMs: 700,
    timestampMs: 2_200,
  });
  expect(adapter.calls.filter(call => call === 'commitAudio')).toHaveLength(1);

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    speechDurationMs: 160,
    timestampMs: 2_500,
  });
  await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);
  expect(adapter.calls.filter(call => call === 'commitAudio')).toHaveLength(1);

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STOPPED',
    speechDurationMs: 900,
    timestampMs: 3_500,
  });
  await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);

  expect(adapter.calls).toContain('commitAudio');
  adapter.emitEvent({
    event: 'voice.audio.commit.received',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'new-commit-received',
    timestampMs: 8,
  });
  expect(socket.getSnapshot()).toMatchObject({
    turn: 'waiting',
    turnId: 'turn-2',
    responseId: 'response-2',
  });

  adapter.emitEvent({
    event: 'transcript.final',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'new-transcript-final',
    timestampMs: 9,
    text: 'What time is it right now?',
    final: true,
  });
  adapter.emitEvent({
    event: 'assistant.response.started',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'new-response-started',
    timestampMs: 10,
  });
  expect(socket.getSnapshot()).toMatchObject({
    turnId: 'turn-2',
    responseId: 'response-2',
  });
  expect(socket.getSnapshot().connection).toBe('connected');
  jest.useRealTimers();
  await new Promise<void>(resolve => setTimeout(resolve, 300));
});

test('late old-response cancellation does not reset the replacement turn', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.9,
    speechDurationMs: 160,
    timestampMs: 1_505,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.9,
    speechDurationMs: 480,
    timestampMs: 1_985,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'replacement-ready',
    timestampMs: 6,
  });
  expect(socket.getSnapshot()).toMatchObject({
    turnId: 'turn-2',
    responseId: 'response-2',
    turn: 'recording',
  });

  adapter.emitEvent({
    event: 'response.cancelled',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'late-old-cancel',
    timestampMs: 7,
  });
  expect(socket.getSnapshot()).toMatchObject({
    turnId: 'turn-2',
    responseId: 'response-2',
    turn: 'recording',
  });
});

test('holds pending speech until the replacement server turn is ready', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STARTED',
    probability: 0.9,
    speechDurationMs: 160,
    timestampMs: 1_505,
  });
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_ACTIVITY',
    probability: 0.9,
    speechDurationMs: 480,
    timestampMs: 1_985,
  });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(socket.getSnapshot().turnId).toBeNull();
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(2);

  jest.useFakeTimers();
  adapter.emitVad({
    event: 'SILERO_VAD_SPEECH_STOPPED',
    speechDurationMs: 700,
    timestampMs: 2_200,
  });
  expect(adapter.calls.filter(call => call === 'commitAudio')).toHaveLength(1);

  adapter.emitEvent({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'replacement-ready-early-end',
    timestampMs: 8,
  });
  await jest.advanceTimersByTimeAsync(SPEECH_END_COMMIT_GRACE_MS);
  expect(adapter.calls.filter(call => call === 'commitAudio')).toHaveLength(2);
});

test('energy VAD does not interrupt playback and cause a speaker echo restart', async () => {
  const { socket, adapter } = await prepareTurn();

  adapter.emitVad({ event: 'VAD_SPEECH_STARTED', timestampMs: 5 });
  await new Promise<void>(resolve => setTimeout(resolve, 0));

  expect(adapter.calls).not.toContain('stopPlayback');
  expect(adapter.calls.filter(call => call === 'startTurn')).toHaveLength(1);
  expect(socket.getSnapshot().turn).toBe('waiting');
});
