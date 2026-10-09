import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import {
  VoiceSocket,
  VoiceSocketAdapter,
  VoiceServerEventType,
} from '../src/voice/VoiceSocket';

// Models the native resource gate; request/session ownership remains in the
// real VoiceSocket. PCM offsets and AudioTrack position are tested by JVM tests.
class VisibilityAdapter implements VoiceSocketAdapter {
  status: VoiceGatewayStatus = {
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
  };
  active = true;
  microphone = false;
  pendingMic = false;
  calls: string[] = [];
  durations: number[] = [];
  permission: Promise<string> | null = null;
  visibilityResult: Promise<VoiceGatewayStatus> | null = null;
  playbackStop: Promise<VoiceGatewayStatus> | null = null;
  events = new Set<(value: VoiceGatewayEvent) => void>();
  statuses = new Set<(value: VoiceGatewayStatus) => void>();
  vad = new Set<(value: unknown) => void>();
  bargeIn = new Set<(value: unknown) => void>();
  subscribeBargeIn(listener: (value: unknown) => void) {
    this.bargeIn.add(listener);
    return () => {
      this.bargeIn.delete(listener);
    };
  }
  async connect() {
    this.calls.push('connect');
    this.status = { ...this.status, connected: true, state: 'CONNECTED' };
    return this.status;
  }
  async disconnect() {
    this.calls.push('disconnect');
    this.status = {
      ...this.status,
      connected: false,
      sessionStarted: false,
      state: 'DISCONNECTED',
    };
    return this.status;
  }
  async startSession() {
    this.calls.push('session');
    return this.status;
  }
  async endSession() {
    this.calls.push('end');
    return this.status;
  }
  async startTurn() {
    this.calls.push('turn');
    this.status = {
      ...this.status,
      state: 'TURN_STARTING',
      turnActive: true,
      turnId: null,
      responseId: null,
    };
    return this.status;
  }
  async commitAudio(duration: number) {
    this.calls.push('commit');
    this.durations.push(duration);
    this.status = { ...this.status, state: 'SESSION_READY', turnActive: false };
    return this.status;
  }
  async cancelResponse() {
    this.calls.push('cancel');
    return this.status;
  }
  async stopPlayback() {
    this.calls.push('stopPlayback');
    return this.playbackStop ?? this.status;
  }
  async getStatus() {
    return this.status;
  }
  async startMicrophone() {
    this.calls.push('micStart');
    this.pendingMic = true;
    this.microphone = this.active;
  }
  async stopMicrophone() {
    this.calls.push('micStop');
    this.pendingMic = false;
    this.microphone = false;
  }
  async requestMicrophonePermission() {
    return this.permission ?? 'granted';
  }
  async setInteractionActive(active: boolean) {
    this.calls.push(`visible:${active}`);
    this.active = active;
    this.microphone = active && this.pendingMic;
    return this.visibilityResult ?? this.status;
  }
  subscribeEvent(listener: (value: VoiceGatewayEvent) => void) {
    this.events.add(listener);
    return () => {
      this.events.delete(listener);
    };
  }
  subscribeStatus(listener: (value: VoiceGatewayStatus) => void) {
    this.statuses.add(listener);
    return () => {
      this.statuses.delete(listener);
    };
  }
  subscribeVad(listener: (value: unknown) => void) {
    this.vad.add(listener);
    return () => {
      this.vad.delete(listener);
    };
  }
  emit(event: VoiceServerEventType, extra: Partial<VoiceGatewayEvent> = {}) {
    if (event === 'server.session.ready')
      this.status = {
        ...this.status,
        state: 'SESSION_READY',
        sessionStarted: true,
        sessionId: 'session',
      };
    if (event === 'server.turn.ready')
      this.status = {
        ...this.status,
        state: 'STREAMING_AUDIO',
        turnActive: true,
        turnId: extra.turnId ?? 'turn',
        responseId: extra.responseId ?? 'response',
      };
    // Native transport publishes its newly bound status before turn-ready.
    // This is required when a queued follow-up replaces old correlation IDs.
    if (event === 'server.turn.ready') {
      this.statuses.forEach(listener => listener(this.status));
    }
    const value = {
      event,
      sessionId: 'session',
      turnId: 'turn',
      responseId: 'response',
      eventId: `event-${++this.sequence}`,
      timestampMs: Date.now(),
      ...extra,
    } as VoiceGatewayEvent;
    this.events.forEach(listener => listener(value));
  }
  private sequence = 0;
}

const sockets: VoiceSocket[] = [];
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
const count = (adapter: VisibilityAdapter, call: string) =>
  adapter.calls.filter(value => value === call).length;

async function ready(listening = true, continuousListening = false) {
  const adapter = new VisibilityAdapter();
  const socket = new VoiceSocket({ adapter, continuousListening });
  sockets.push(socket);
  await socket.connect();
  await socket.startSession();
  adapter.emit('server.session.ready', { turnId: null, responseId: null });
  adapter.emit('server.pong', { turnId: null, responseId: null });
  if (listening) {
    await socket.startTurn();
    adapter.emit('server.turn.ready');
  }
  return { socket, adapter };
}

afterEach(async () => {
  await Promise.all(sockets.splice(0).map(socket => socket.dispose()));
  jest.useRealTimers();
});

test.each(['Memory', 'Tasks', 'Settings'])(
  'VIS-01 %s pauses listening and resumes the same turn without requests',
  async () => {
    const { socket, adapter } = await ready();
    const before = socket.getSnapshot();
    const requests = adapter.calls.filter(call => !call.startsWith('visible:'));
    await socket.setAssistantVisible(false);
    expect(adapter.microphone).toBe(false);
    expect(socket.getSnapshot().interactionPause).toBe('listening');
    adapter.vad.forEach(listener =>
      listener({ event: 'SILERO_VAD_SPEECH_STARTED' }),
    );
    await socket.setAssistantVisible(true);
    expect(adapter.microphone).toBe(true);
    expect(socket.getSnapshot()).toMatchObject({
      turn: 'recording',
      sessionId: before.sessionId,
      turnId: before.turnId,
      responseId: before.responseId,
      interactionPause: null,
    });
    expect(adapter.calls.filter(call => !call.startsWith('visible:'))).toEqual(
      requests,
    );
  },
);

test.each(['Memory', 'Tasks', 'Settings'])(
  'VIS-02 %s pauses speaking without cancellation or new generation',
  async () => {
    const { socket, adapter } = await ready();
    await socket.commitTurn();
    adapter.emit('tts.started');
    adapter.emit('tts.playback.started');
    const calls = [...adapter.calls];
    await socket.setAssistantVisible(false);
    adapter.emit('tts.playback.paused');
    expect(socket.getSnapshot()).toMatchObject({
      ttsPlaybackState: 'paused',
      interactionPause: 'speaking',
      turnId: 'turn',
      responseId: 'response',
    });
    await socket.setAssistantVisible(true);
    adapter.emit('tts.playback.resumed');
    expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');
    expect(adapter.calls.filter(call => !call.startsWith('visible:'))).toEqual(
      calls,
    );
  },
);

test('VIS-03 idle remains idle across repeated callbacks and does not capture', async () => {
  const { socket, adapter } = await ready(false, true);
  await socket.setAssistantVisible(true);
  for (let i = 0; i < 5; i++) {
    await socket.setAssistantVisible(false);
    await socket.setAssistantVisible(false);
    await socket.setAssistantVisible(true);
    await socket.setAssistantVisible(true);
  }
  expect(socket.getSnapshot()).toMatchObject({
    turn: 'idle',
    interactionPause: null,
  });
  expect(adapter.microphone).toBe(false);
  expect(count(adapter, 'turn')).toBe(0);
  expect(count(adapter, 'connect')).toBe(1);
  expect(count(adapter, 'visible:false')).toBe(5);
});

test('VIS-04 STT, LLM and pending TTS arrive hidden without resending or losing conversation', async () => {
  const { socket, adapter } = await ready();
  await socket.commitTurn();
  await socket.setAssistantVisible(false);
  adapter.emit('transcript.final', {
    text: 'Remind me to call John at six.',
    final: true,
    transcriptSequence: 1,
  });
  expect(socket.getSnapshot().transcriptMessages).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        text: 'Remind me to call John at six.',
        final: true,
      }),
    ]),
  );
  adapter.emit('assistant.response.started');
  adapter.emit('llm.response.completed', {
    text: 'Your reminder is scheduled.',
  });
  adapter.emit('tts.started');
  adapter.emit('server.turn.completed');
  expect(adapter.active).toBe(false);
  expect(adapter.microphone).toBe(false);
  expect(socket.getSnapshot().interactionPause).toBe('processing');
  expect(
    socket
      .getSnapshot()
      .conversationMessages.some(
        message =>
          'text' in message && message.text === 'Your reminder is scheduled.',
      ),
  ).toBe(true);
  await socket.setAssistantVisible(true);
  adapter.emit('tts.playback.started');
  adapter.emit('tts.playback.completed');
  expect(count(adapter, 'commit')).toBe(1);
  expect(count(adapter, 'turn')).toBe(1);
  expect(count(adapter, 'cancel')).toBe(0);
  expect(count(adapter, 'end')).toBe(0);
});

test('VIS-05 rapid navigation ignores out-of-order native status promises', async () => {
  const { socket, adapter } = await ready();
  let release!: (value: VoiceGatewayStatus) => void;
  adapter.visibilityResult = new Promise(resolve => {
    release = resolve;
  });
  const first = socket.setAssistantVisible(false);
  adapter.visibilityResult = null;
  await socket.setAssistantVisible(true);
  await socket.setAssistantVisible(false);
  release({ ...adapter.status, sessionId: 'obsolete', turnId: 'obsolete' });
  await first;
  expect(socket.getSnapshot()).toMatchObject({
    assistantVisible: false,
    sessionId: 'session',
    turnId: 'turn',
  });
  expect(adapter.microphone).toBe(false);
  await socket.setAssistantVisible(true);
  expect(count(adapter, 'turn')).toBe(1);
});

test('VIS-06 navigation during pending permission gates late capture without duplicating a turn', async () => {
  const { socket, adapter } = await ready(false);
  let release!: (value: string) => void;
  adapter.permission = new Promise(resolve => {
    release = resolve;
  });
  const starting = socket.startTurn();
  await flush();
  await socket.setAssistantVisible(false);
  release('granted');
  await starting;
  expect(adapter.microphone).toBe(false);
  expect(count(adapter, 'turn')).toBe(1);
  await socket.setAssistantVisible(true);
  expect(adapter.microphone).toBe(true);
  expect(count(adapter, 'turn')).toBe(1);
});

test('VIS-07 logout invalidates pending starts and paused responses', async () => {
  const { socket, adapter } = await ready(false);
  let release!: (value: string) => void;
  adapter.permission = new Promise(resolve => {
    release = resolve;
  });
  const starting = socket.startTurn();
  await flush();
  await socket.setAssistantVisible(false);
  await socket.stop('logout');
  release('granted');
  await starting;
  await socket.setAssistantVisible(true);
  adapter.emit('tts.started');
  adapter.emit('tts.playback.started');
  expect(adapter.microphone).toBe(false);
  expect(count(adapter, 'turn')).toBe(0);
  expect(socket.getSnapshot()).toMatchObject({
    sessionId: null,
    turnId: null,
    connection: 'disconnected',
  });
});

test('VIS-08 paused time is excluded from the recording commit duration', async () => {
  jest.useFakeTimers();
  jest.setSystemTime(100_000);
  const { socket, adapter } = await ready();
  jest.setSystemTime(102_000);
  await socket.setAssistantVisible(false);
  jest.setSystemTime(602_000);
  await socket.setAssistantVisible(true);
  jest.setSystemTime(603_000);
  await socket.commitTurn();
  expect(adapter.durations).toEqual([3000]);
});

test('VIS-09 stale TTS cannot replace the current response after navigation', async () => {
  const { socket, adapter } = await ready();
  await socket.cancelTurn();
  await socket.startTurn();
  adapter.emit('server.turn.ready', {
    turnId: 'new-turn',
    responseId: 'new-response',
  });
  await socket.setAssistantVisible(false);
  adapter.emit('tts.started');
  adapter.emit('tts.playback.started');
  await socket.setAssistantVisible(true);
  expect(socket.getSnapshot()).toMatchObject({
    turnId: 'new-turn',
    responseId: 'new-response',
    ttsPlaybackState: 'idle',
  });
});

test.each<VoiceServerEventType>([
  'server.turn.failed',
  'llm.response.failed',
  'tts.failed',
  'response.cancelled',
])(
  'VIS-10 %s while hidden never restarts capture or repeats commit',
  async event => {
    const { socket, adapter } = await ready();
    await socket.commitTurn();
    await socket.setAssistantVisible(false);
    adapter.emit(event, { code: 'test_failure' });
    await flush();
    expect(socket.getSnapshot().lastEvent).toBe(event);
    expect(adapter.microphone).toBe(false);
    expect(count(adapter, 'commit')).toBe(1);
    expect(count(adapter, 'turn')).toBe(1);
  },
);

test('VIS-11 hidden completion defers continuous listening until return', async () => {
  jest.useFakeTimers();
  const { socket, adapter } = await ready(true, true);
  await socket.commitTurn();
  await socket.setAssistantVisible(false);
  adapter.emit('assistant.response.started');
  adapter.emit('llm.response.completed', { text: 'Done.' });
  adapter.emit('server.turn.completed');
  await jest.advanceTimersByTimeAsync(1);
  expect(count(adapter, 'turn')).toBe(1);
  await socket.setAssistantVisible(true);
  await jest.advanceTimersByTimeAsync(1);
  expect(count(adapter, 'turn')).toBe(2);
  expect(adapter.microphone).toBe(true);
});

test('VIS-12 native barge-in already confirmed before pause completes its replacement without hidden capture', async () => {
  const { socket, adapter } = await ready();
  await socket.commitTurn();
  adapter.emit('tts.started');
  adapter.emit('tts.playback.started');
  await socket.setAssistantVisible(false);
  // Native has already stopped this response before the bridge callback arrives.
  adapter.bargeIn.forEach(listener =>
    listener({
      event: 'BARGE_IN_CONFIRMED',
      responseId: 'response',
      localStopCompleted: true,
      reason: 'near_end_confirmed',
    }),
  );
  await flush();
  expect(count(adapter, 'cancel')).toBe(1);
  expect(count(adapter, 'turn')).toBe(2);
  expect(adapter.microphone).toBe(false);
  await socket.setAssistantVisible(true);
  expect(count(adapter, 'turn')).toBe(2);
  expect(adapter.microphone).toBe(true);
});

test('VIS-13 network failure/reconnect while hidden keeps the resource gate without replaying the request', async () => {
  jest.useFakeTimers();
  const { socket, adapter } = await ready();
  await socket.commitTurn();
  await socket.setAssistantVisible(false);
  adapter.status = {
    ...adapter.status,
    state: 'DISCONNECTED',
    connected: false,
    sessionStarted: false,
    sessionId: null,
    turnId: null,
    responseId: null,
  };
  adapter.statuses.forEach(listener => listener(adapter.status));
  await jest.advanceTimersByTimeAsync(10_000);
  expect(adapter.active).toBe(false);
  expect(adapter.microphone).toBe(false);
  await socket.setAssistantVisible(true);
  expect(count(adapter, 'turn')).toBe(1);
  expect(count(adapter, 'commit')).toBe(1);
  expect(adapter.microphone).toBe(false);
});

test('VIS-14 queued follow-up survives navigation without another commit or turn request', async () => {
  const { socket, adapter } = await ready();
  await socket.commitTurn();
  await socket.startTurn();
  const calls = [...adapter.calls];
  await socket.setAssistantVisible(false);
  expect(adapter.microphone).toBe(false);
  adapter.emit('server.turn.ready', {
    turnId: 'follow-up',
    responseId: 'follow-up-response',
  });
  await socket.setAssistantVisible(true);
  expect(socket.getSnapshot()).toMatchObject({
    turnId: 'follow-up',
    responseId: 'follow-up-response',
  });
  expect(adapter.calls.filter(call => !call.startsWith('visible:'))).toEqual(
    calls,
  );
});

test('VIS-15 hidden confirmation prompt completion defers its answer turn until return', async () => {
  const { socket, adapter } = await ready();
  await socket.commitTurn();
  adapter.emit('tts.started');
  adapter.emit('tts.playback.started');
  adapter.emit('confirmation.required', {
    confirmationId: 'confirm',
    toolCallId: 'call',
    toolName: 'create_task',
    status: 'PENDING',
  });
  await socket.setAssistantVisible(false);
  adapter.emit('server.turn.completed');
  adapter.emit('tts.playback.completed');
  await flush();
  expect(count(adapter, 'turn')).toBe(1);
  expect(adapter.microphone).toBe(false);
  await socket.setAssistantVisible(true);
  await flush();
  expect(count(adapter, 'turn')).toBe(2);
  expect(count(adapter, 'commit')).toBe(1);
  expect(count(adapter, 'cancel')).toBe(0);
});

test.each([false, true])(
  'VIS-16 pending speech-end delay resumes without hidden commit; new speech=%s',
  async newSpeech => {
    jest.useFakeTimers();
    const { socket, adapter } = await ready(true, true);
    adapter.vad.forEach(listener =>
      listener({
        event: 'SILERO_VAD_SPEECH_STOPPED',
        speechDurationMs: 500,
        timestampMs: Date.now(),
      }),
    );
    await jest.advanceTimersByTimeAsync(300);
    await socket.setAssistantVisible(false);
    await jest.advanceTimersByTimeAsync(5000);
    expect(count(adapter, 'commit')).toBe(0);
    await socket.setAssistantVisible(true);
    if (newSpeech) {
      adapter.vad.forEach(listener =>
        listener({
          event: 'SILERO_VAD_SPEECH_STARTED',
          speechDurationMs: 160,
          timestampMs: Date.now(),
        }),
      );
    }
    await jest.advanceTimersByTimeAsync(599);
    expect(count(adapter, 'commit')).toBe(0);
    await jest.advanceTimersByTimeAsync(1);
    expect(count(adapter, 'commit')).toBe(newSpeech ? 0 : 1);
    expect(count(adapter, 'turn')).toBe(1);
  },
);
