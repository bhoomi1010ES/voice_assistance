import {
  normalizeVoiceGatewayEvent,
  VoiceSocket,
  VoiceSocketAdapter,
} from '../src/voice/VoiceSocket';
import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import { parsePlanningState } from '../src/plans/types';

const state = (version = 1, mode = 'normal') => ({
  mode,
  state_version: version,
  available: true,
  automatic_actions_available: false,
  policy_version: 'plan-v1',
  active_plan_id: mode === 'plan' ? 'plan-1' : null,
  active_plan_name: mode === 'plan' ? 'XYZ' : null,
  plan_revision: mode === 'plan' ? 3 : null,
});

function harness() {
  let listener: (event: VoiceGatewayEvent) => void = () => {};
  const status = {
    state: 'CONNECTED',
    connected: true,
    sessionStarted: false,
    sessionId: null,
    turnId: null,
    responseId: null,
    lastServerEventTimestampMs: 0,
    turnActive: false,
    lastError: null,
  } as VoiceGatewayStatus;
  const adapter: VoiceSocketAdapter = {
    connect: async () => status,
    disconnect: async () => ({ ...status, connected: false }),
    startSession: async () => ({ ...status, state: 'SESSION_STARTING' }),
    startTurn: async () => status,
    commitAudio: async () => status,
    cancelResponse: async () => status,
    endSession: async () => status,
    getStatus: async () => status,
    subscribeStatus: () => () => {},
    subscribeEvent: callback => {
      listener = callback;
      return () => {};
    },
    setPlanningMode: jest.fn(async () => status),
    selectPlan: jest.fn(async () => status),
  };
  const socket = new VoiceSocket({
    adapter,
    now: () => 100,
    appState: {
      currentState: 'active',
      addEventListener: () => ({ remove: () => {} }),
    },
  });
  const emit = (event: string, extra: object = {}) =>
    listener({
      event,
      sessionId: 'session-1',
      turnId: null,
      responseId: null,
      eventId: `event-${Math.random()}`,
      timestampMs: 100,
      ...extra,
    });
  return { adapter, socket, emit };
}

let activeSocket: VoiceSocket | null = null;
beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(console, 'info').mockImplementation(() => {});
});
afterEach(async () => {
  await activeSocket?.dispose();
  activeSocket = null;
  jest.restoreAllMocks();
  jest.useRealTimers();
});

test('complete server and native envelopes retain planning state and request correlation', () => {
  const server = normalizeVoiceGatewayEvent({
    type: 'server.planning.state',
    session_id: 'session-1',
    request_event_id: 'request-1',
    planning: state(4, 'plan'),
  });
  const native = normalizeVoiceGatewayEvent({
    event: 'server.planning.state',
    sessionId: 'session-1',
    requestEventId: 'request-1',
    planningJson: JSON.stringify(state(4, 'plan')),
  });
  expect(native).toEqual(server);
  expect(native?.planning).toEqual({
    mode: 'plan',
    stateVersion: 4,
    activePlanId: 'plan-1',
    activePlanName: 'XYZ',
    planRevision: 3,
    policyVersion: 'plan-v1',
    available: true,
    automaticActionsAvailable: false,
  });
});

test.each([
  {},
  { ...state(), state_version: 0 },
  { ...state(), mode: 'auto' },
  { ...state(), state_version: '1' },
  'invalid',
  'x'.repeat(4097),
])('malformed snapshots are rejected: %p', input => {
  expect(parsePlanningState(input)).toBeNull();
  expect(
    normalizeVoiceGatewayEvent({
      type: 'server.planning.state',
      planning: input,
    }),
  ).toBeNull();
});

test('controls require capability and acknowledgement, ignore stale state, and preserve response identity', async () => {
  const { socket, adapter, emit } = harness();
  activeSocket = socket;
  socket.start();
  await socket.connect();
  emit('server.session.ready', { planningJson: JSON.stringify(state()) });
  await socket.setPlanningMode('plan');
  expect(socket.getSnapshot().planning?.mode).toBe('normal');
  expect(socket.getSnapshot().planningPending).toBe(true);
  expect(adapter.setPlanningMode).toHaveBeenCalledWith(
    'session-1',
    1,
    'plan',
    expect.any(String),
  );
  const requestId = (adapter.setPlanningMode as jest.Mock).mock.calls[0][3];
  emit('server.planning.state', {
    planningJson: JSON.stringify(state(2, 'plan')),
    requestEventId: requestId,
    turnId: 'old-turn',
    responseId: 'old-response',
  });
  expect(socket.getSnapshot().planning?.mode).toBe('plan');
  expect(socket.getSnapshot().planningPending).toBe(false);
  expect(socket.getSnapshot().responseId).toBeNull();
  emit('server.planning.state', { planningJson: JSON.stringify(state(1)) });
  expect(socket.getSnapshot().planning?.stateVersion).toBe(2);
  emit('server.planning.state', {
    sessionId: 'foreign-session',
    planningJson: JSON.stringify(state(99)),
  });
  expect(socket.getSnapshot().planning?.mode).toBe('plan');
  await socket.selectPlan(null);
  expect(adapter.selectPlan).toHaveBeenCalledWith(
    'session-1',
    2,
    null,
    expect.any(String),
  );
  const selectId = (adapter.selectPlan as jest.Mock).mock.calls[0][3];
  emit('server.planning.error', {
    code: 'planning_state_conflict',
    requestEventId: selectId,
  });
  expect(socket.getSnapshot().planningPending).toBe(false);
  expect(socket.getSnapshot().planningError).toContain('changed');
  emit('voice.connection.closed');
  expect(socket.getSnapshot().planning).toBeNull();
});

test('old servers cannot authorize controls and missing acknowledgements clear pending state', async () => {
  const { socket, adapter, emit } = harness();
  activeSocket = socket;
  socket.start();
  await socket.connect();
  emit('server.session.ready');
  await socket.setPlanningMode('plan');
  expect(adapter.setPlanningMode).not.toHaveBeenCalled();
  emit('server.session.ready', { planningJson: JSON.stringify(state()) });
  await socket.setPlanningMode('plan');
  jest.advanceTimersByTime(10000);
  expect(socket.getSnapshot().planningPending).toBe(false);
  expect(socket.getSnapshot().planning?.mode).toBe('normal');
  expect(socket.getSnapshot().planningError).toContain('acknowledgement');
});

test('a resumed ready snapshot restores mode and reset clears it', async () => {
  const { socket, emit } = harness();
  activeSocket = socket;
  socket.start();
  await socket.connect();
  emit('server.session.ready', {
    planningJson: JSON.stringify(state(7, 'plan')),
  });
  expect(socket.getSnapshot().planning?.activePlanName).toBe('XYZ');
  emit('server.conversation.reset', { sessionId: null });
  expect(socket.getSnapshot().planning).toBeNull();
});
