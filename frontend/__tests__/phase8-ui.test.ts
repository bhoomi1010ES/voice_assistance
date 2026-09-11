import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import { VoiceSocket, VoiceSocketAdapter } from '../src/voice/VoiceSocket';

const SESSION_ID = 'phase8-session';

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

class TtsAdapter implements VoiceSocketAdapter {
  status = gatewayStatus();
  stopPlaybackCalls = 0;
  private readonly events = new Set<(event: VoiceGatewayEvent) => void>();
  private readonly statuses = new Set<(status: VoiceGatewayStatus) => void>();

  async connect() {
    this.status = gatewayStatus({ state: 'CONNECTED', connected: true });
    return this.status;
  }

  async disconnect() {
    this.status = gatewayStatus();
    return this.status;
  }

  async startSession() {
    this.status = gatewayStatus({
      state: 'SESSION_STARTING',
      connected: true,
    });
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
    return this.status;
  }

  async cancelResponse() {
    this.status = gatewayStatus({
      state: 'SESSION_READY',
      connected: true,
      sessionStarted: true,
      sessionId: SESSION_ID,
    });
    return this.status;
  }

  async stopPlayback() {
    this.stopPlaybackCalls += 1;
    return this.status;
  }

  async endSession() {
    return this.disconnect();
  }

  async getStatus() {
    return this.status;
  }

  async startMicrophone() {}
  async stopMicrophone() {}
  async requestMicrophonePermission() {
    return 'granted';
  }

  subscribeStatus(listener: (status: VoiceGatewayStatus) => void) {
    this.statuses.add(listener);
    return () => this.statuses.delete(listener);
  }

  subscribeEvent(listener: (event: VoiceGatewayEvent) => void) {
    this.events.add(listener);
    return () => this.events.delete(listener);
  }

  emit(event: VoiceGatewayEvent) {
    if (event.event === 'server.session.ready') {
      this.status = gatewayStatus({
        state: 'SESSION_READY',
        connected: true,
        sessionStarted: true,
        sessionId: SESSION_ID,
      });
    }
    if (event.event === 'server.turn.ready') {
      this.status = gatewayStatus({
        state: 'STREAMING_AUDIO',
        connected: true,
        sessionStarted: true,
        turnActive: true,
        sessionId: SESSION_ID,
        turnId: event.turnId,
        responseId: event.responseId,
      });
    }
    this.events.forEach(listener => listener(event));
  }
}

async function prepare(socket: VoiceSocket, adapter: TtsAdapter) {
  await socket.connect();
  await socket.startSession();
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
    eventId: 'turn-ready',
    timestampMs: 2,
  });
}

test('speaking begins only after native playback start and stop clears local audio', async () => {
  const adapter = new TtsAdapter();
  const socket = new VoiceSocket({ adapter });
  await prepare(socket, adapter);

  adapter.emit({
    event: 'tts.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'tts-started',
    timestampMs: 3,
  });
  expect(socket.getSnapshot().ttsPlaybackState).toBe('buffering');

  adapter.emit({
    event: 'tts.playback.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'playback-started',
    timestampMs: 4,
  });
  expect(socket.getSnapshot().ttsPlaybackState).toBe('speaking');

  await socket.stopPlayback();
  expect(adapter.stopPlaybackCalls).toBe(1);
  expect(socket.getSnapshot().ttsPlaybackState).toBe('idle');
  await socket.dispose();
});

test('tts failure does not turn a completed text response into a failed response', async () => {
  const adapter = new TtsAdapter();
  const socket = new VoiceSocket({ adapter });
  await prepare(socket, adapter);

  adapter.emit({
    event: 'assistant.response.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'assistant-started',
    timestampMs: 3,
  });
  adapter.emit({
    event: 'llm.response.completed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'text-completed',
    timestampMs: 4,
    text: 'The text remains available.',
  });
  adapter.emit({
    event: 'tts.failed',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'tts-failed',
    timestampMs: 5,
    code: 'provider_unavailable',
  });

  expect(socket.getSnapshot().ttsPlaybackState).toBe('failed');
  expect(
    socket
      .getSnapshot()
      .conversationMessages.some(
        message =>
          message.role === 'assistant' && message.status === 'completed',
      ),
  ).toBe(true);
  await socket.dispose();
});

test('a stale playback event is discarded after response supersession', async () => {
  const adapter = new TtsAdapter();
  const socket = new VoiceSocket({ adapter });
  await prepare(socket, adapter);
  adapter.emit({
    event: 'tts.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'old-tts-started',
    timestampMs: 3,
  });
  await socket.cancelTurn();
  await socket.startTurn();
  adapter.emit({
    event: 'server.turn.ready',
    sessionId: SESSION_ID,
    turnId: 'turn-2',
    responseId: 'response-2',
    eventId: 'turn-ready-2',
    timestampMs: 4,
  });
  const before = socket.getSnapshot().eventSequence;
  adapter.emit({
    event: 'tts.playback.started',
    sessionId: SESSION_ID,
    turnId: 'turn-1',
    responseId: 'response-1',
    eventId: 'late-old-playback',
    timestampMs: 5,
  });

  expect(socket.getSnapshot().eventSequence).toBe(before);
  expect(socket.getSnapshot().ttsPlaybackState).toBe('idle');
  await socket.dispose();
});
