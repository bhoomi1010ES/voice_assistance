import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import { AppStateStatus } from 'react-native';
import { VoiceSocket, VoiceSocketAdapter } from '../src/voice/VoiceSocket';

const status = (
  state: VoiceGatewayStatus['state'],
  extra: Partial<VoiceGatewayStatus> = {},
): VoiceGatewayStatus => ({
  state,
  connected: state !== 'DISCONNECTED',
  sessionStarted: state === 'SESSION_READY' || state === 'TURN_STARTING',
  turnActive: state === 'TURN_STARTING',
  sessionId: state === 'DISCONNECTED' ? null : 'session-1',
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
  ...extra,
});

class WakeAdapter implements VoiceSocketAdapter {
  current = status('DISCONNECTED');
  calls: string[] = [];
  wakeOnly = true;
  permission = 'granted';
  events = new Set<(event: VoiceGatewayEvent) => void>();
  statuses = new Set<(status: VoiceGatewayStatus) => void>();
  wake = new Set<(event: unknown) => void>();
  vad = new Set<(event: unknown) => void>();
  sequence = 0;

  async connect() {
    this.calls.push('connect');
    this.current = status('CONNECTED');
    return this.current;
  }
  async disconnect() {
    this.calls.push('disconnect');
    this.current = status('DISCONNECTED');
    return this.current;
  }
  async startSession() {
    this.calls.push('session');
    this.current = status('SESSION_READY');
    return this.current;
  }
  async endSession() {
    this.calls.push('end');
    this.current = status('CONNECTED', { sessionStarted: false });
    return this.current;
  }
  async startTurn(_: string | null = null, includePreRoll = false) {
    this.calls.push(`turn:${includePreRoll}`);
    this.current = status('TURN_STARTING');
    return this.current;
  }
  async commitAudio() {
    this.calls.push('commit');
    this.current = status('SESSION_READY');
    return this.current;
  }
  async cancelResponse() {
    this.calls.push('cancel');
    return this.current;
  }
  async abortAll() {
    this.calls.push('abort');
    this.current = status('SESSION_READY');
    return this.current;
  }
  async stopPlayback() {
    this.calls.push('playbackStop');
    return this.current;
  }
  async getStatus() {
    return this.current;
  }
  async startMicrophone() {
    this.calls.push('micStart');
  }
  async stopMicrophone() {
    this.calls.push('micStop');
  }
  async getMicrophoneStatus() {
    return {
      isRecording:
        this.calls.includes('micStart') && this.calls.at(-1) !== 'micStop',
    };
  }
  async requestMicrophonePermission() {
    return this.permission;
  }
  async setWakeWordOnly(enabled: boolean) {
    this.wakeOnly = enabled;
    this.calls.push(`wakeOnly:${enabled}`);
  }
  async setInteractionActive(active: boolean) {
    this.calls.push(`visible:${active}`);
    return this.current;
  }
  subscribeStatus(listener: (status: VoiceGatewayStatus) => void) {
    this.statuses.add(listener);
    return () => this.statuses.delete(listener);
  }
  subscribeEvent(listener: (event: VoiceGatewayEvent) => void) {
    this.events.add(listener);
    return () => this.events.delete(listener);
  }
  subscribeWakeWord(listener: (event: unknown) => void) {
    this.wake.add(listener);
    return () => this.wake.delete(listener);
  }
  subscribeVad(listener: (event: unknown) => void) {
    this.vad.add(listener);
    return () => this.vad.delete(listener);
  }
  emit(type: string, extra: Record<string, unknown> = {}) {
    const event = {
      event: type,
      sessionId: 'session-1',
      eventId: `event-${++this.sequence}`,
      timestampMs: this.sequence,
      ...extra,
    } as VoiceGatewayEvent;
    this.events.forEach(listener => listener(event));
  }
  detect() {
    this.wake.forEach(listener =>
      listener({
        event: 'WAKE_WORD_DETECTED',
        modelName: 'hey_mycroft',
        confidence: 0.9,
      }),
    );
  }
  speech() {
    this.vad.forEach(listener =>
      listener({ event: 'SILERO_VAD_SPEECH_STARTED' }),
    );
  }
}

const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

async function ready() {
  const adapter = new WakeAdapter();
  const appListeners = new Set<(state: AppStateStatus) => void>();
  const appState = {
    currentState: 'active' as AppStateStatus,
    addEventListener(
      _type: 'change',
      listener: (state: AppStateStatus) => void,
    ) {
      appListeners.add(listener);
      return { remove: () => appListeners.delete(listener) };
    },
    emit(state: AppStateStatus) {
      this.currentState = state;
      appListeners.forEach(listener => listener(state));
    },
  };
  const socket = new VoiceSocket({
    adapter,
    handsFreeWakeWordRestart: true,
    appState,
  });
  await socket.connect();
  adapter.emit('server.session.ready');
  adapter.emit('server.pong');
  await flush();
  return { adapter, socket, appState };
}

describe('wake word conversation lifecycle', () => {
  test('initial launch listens for the wake word without opening the gateway', async () => {
    const adapter = new WakeAdapter();
    const socket = new VoiceSocket({
      adapter,
      handsFreeWakeWordRestart: true,
      appState: {
        currentState: 'active',
        addEventListener: () => ({ remove: () => undefined }),
      },
    });
    socket.start();
    await flush();
    expect(socket.getSnapshot().connection).toBe('disconnected');
    expect(socket.getSnapshot().voiceLifecycle).toBe('WAKE_WORD_WAITING');
    expect(socket.getSnapshot().wakeWordStandby).toBe(true);
    expect(adapter.calls).not.toContain('connect');
    expect(adapter.calls).toContain('micStart');
    await socket.dispose();
  });

  test('launch waits locally and ordinary speech cannot start a turn', async () => {
    const { adapter, socket } = await ready();
    expect(socket.getSnapshot().voiceLifecycle).toBe('WAKE_WORD_WAITING');
    expect(socket.getSnapshot().wakeWordStandby).toBe(true);
    expect(adapter.wakeOnly).toBe(true);
    expect(adapter.calls).not.toContain('turn:false');
    expect(adapter.calls).not.toContain('turn:true');
    adapter.speech();
    expect(socket.getSnapshot().turn).toBe('idle');
    await socket.startTurn();
    expect(adapter.calls.filter(call => call.startsWith('turn:'))).toHaveLength(
      0,
    );
    await socket.dispose();
  });

  test('continuous follow-up starts without another wake phrase', async () => {
    const { adapter, socket } = await ready();
    adapter.detect();
    await flush();
    adapter.emit('server.turn.ready', {
      turnId: 'turn-1',
      responseId: 'response-1',
    });
    adapter.emit('server.turn.completed', {
      turnId: 'turn-1',
      responseId: 'response-1',
      suppressAutoListen: false,
    });
    await new Promise<void>(resolve => setTimeout(() => resolve(), 0));
    await flush();
    expect(adapter.calls.filter(call => call.startsWith('turn:'))).toEqual([
      'turn:true',
      'turn:false',
    ]);
    expect(socket.getSnapshot().voiceLifecycle).toBe('CONVERSATION_ACTIVE');
    await socket.dispose();
  });

  test('one wake starts one turn with local pre-roll; stop returns to wake waiting and stale events are ignored', async () => {
    const { adapter, socket } = await ready();
    adapter.detect();
    adapter.detect();
    await flush();
    expect(adapter.calls.filter(call => call === 'turn:true')).toHaveLength(1);
    expect(adapter.wakeOnly).toBe(false);
    expect(socket.getSnapshot().voiceLifecycle).toBe('CONVERSATION_ACTIVE');

    adapter.emit('server.turn.ready', {
      turnId: 'turn-1',
      responseId: 'response-1',
    });
    adapter.emit('assistant.text.final', {
      turnId: 'turn-1',
      responseId: 'response-1',
      route: 'CONTROL',
      text: 'Stopped listening.',
    });
    await flush();
    expect(adapter.calls).toContain('abort');
    expect(adapter.calls).toContain('playbackStop');
    expect(adapter.calls).toContain('disconnect');
    expect(adapter.wakeOnly).toBe(true);
    expect(socket.getSnapshot().voiceLifecycle).toBe('WAKE_WORD_WAITING');
    expect(socket.getSnapshot().wakeWordStandby).toBe(true);
    expect(socket.getSnapshot().connection).toBe('disconnected');

    adapter.current = status('CONNECTED');
    adapter.statuses.forEach(listener => listener(adapter.current));
    await flush();
    expect(socket.getSnapshot().connection).toBe('disconnected');
    expect(adapter.calls.filter(call => call === 'disconnect')).toHaveLength(2);

    adapter.emit('assistant.response.started', {
      turnId: 'turn-1',
      responseId: 'response-1',
    });
    expect(socket.getSnapshot().turn).toBe('idle');
    adapter.detect();
    await flush();
    adapter.emit('server.session.ready');
    adapter.emit('server.pong');
    await flush();
    expect(adapter.calls.filter(call => call === 'connect')).toHaveLength(2);
    expect(adapter.calls.filter(call => call === 'turn:true')).toHaveLength(2);
    await socket.dispose();
  });

  test('completion stop signal disconnects even when the control acknowledgement was missed', async () => {
    const { adapter, socket } = await ready();
    adapter.detect();
    await flush();
    adapter.emit('server.turn.ready', {
      turnId: 'turn-1',
      responseId: 'response-1',
    });
    adapter.emit('server.turn.completed', {
      turnId: 'turn-1',
      responseId: 'response-1',
      suppressAutoListen: true,
    });
    await flush();
    await new Promise<void>(resolve => setTimeout(() => resolve(), 0));
    expect(adapter.calls).toContain('playbackStop');
    expect(adapter.calls).toContain('abort');
    expect(adapter.calls.filter(call => call === 'disconnect')).toHaveLength(1);
    expect(adapter.calls.filter(call => call.startsWith('turn:'))).toHaveLength(1);
    expect(adapter.wakeOnly).toBe(true);
    expect(socket.getSnapshot()).toMatchObject({
      voiceLifecycle: 'WAKE_WORD_WAITING',
      connection: 'disconnected',
      session: 'idle',
      turn: 'idle',
      sessionId: null,
    });
    await socket.dispose();
  });

  test('Assistant navigation pauses and restores wake waiting', async () => {
    const { adapter, socket } = await ready();
    await socket.setAssistantVisible(false);
    expect(socket.getSnapshot().voiceLifecycle).toBe('PAUSED');
    expect(socket.getSnapshot().wakeWordStandby).toBe(false);
    await socket.setAssistantVisible(true);
    await flush();
    expect(socket.getSnapshot().voiceLifecycle).toBe('WAKE_WORD_WAITING');
    expect(adapter.wakeOnly).toBe(true);
    await socket.dispose();
  });

  test('app background pauses wake capture and foreground restores it', async () => {
    const { adapter, socket, appState } = await ready();
    appState.emit('background');
    await flush();
    expect(socket.getSnapshot().voiceLifecycle).toBe('PAUSED');
    expect(socket.getSnapshot().wakeWordStandby).toBe(false);
    expect(adapter.calls).toContain('visible:false');
    appState.emit('active');
    await flush();
    expect(socket.getSnapshot().voiceLifecycle).toBe('WAKE_WORD_WAITING');
    expect(socket.getSnapshot().wakeWordStandby).toBe(true);
    await socket.dispose();
  });

  test('capture failure during a conversation aborts the turn and restores wake waiting', async () => {
    const { adapter, socket } = await ready();
    adapter.detect();
    await flush();
    adapter.wake.forEach(listener =>
      listener({ event: 'AUDIO_ENGINE_ERROR' }),
    );
    await flush();
    expect(adapter.calls).toContain('abort');
    expect(adapter.wakeOnly).toBe(true);
    expect(socket.getSnapshot().voiceLifecycle).toBe('WAKE_WORD_WAITING');
    await socket.dispose();
  });

  test('permission denial does not start capture or conversation', async () => {
    const adapter = new WakeAdapter();
    adapter.permission = 'denied';
    const socket = new VoiceSocket({
      adapter,
      handsFreeWakeWordRestart: true,
      appState: {
        currentState: 'active',
        addEventListener: () => ({ remove: () => undefined }),
      },
    });
    await socket.connect();
    adapter.emit('server.session.ready');
    adapter.emit('server.pong');
    await flush();
    expect(adapter.calls).not.toContain('micStart');
    expect(adapter.calls.filter(call => call.startsWith('turn:'))).toHaveLength(
      0,
    );
    await socket.dispose();
  });
});
