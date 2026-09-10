import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import { VoiceSocket, VoiceSocketAdapter } from '../src/voice/VoiceSocket';

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
  connectCalls = 0;
  startTurnCalls = 0;
  stopMicrophoneCalls = 0;
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
      sessionId: SESSION_ID,
      turnActive: true,
    });
    return this.status;
  }

  async commitAudio() {
    this.status = status({
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
