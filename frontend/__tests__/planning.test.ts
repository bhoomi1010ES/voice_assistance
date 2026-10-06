import {
  normalizeVoiceGatewayEvent,
  VoiceSocket,
  VoiceSocketAdapter,
} from '../src/voice/VoiceSocket';
import {
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../src/native/VoiceModule';
import {
  parsePlanningState,
  parsePlanningAction,
  parsePlanningReceipt,
} from '../src/plans/types';

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

test('planning action receipt envelopes normalize across server and native formats', () => {
  const actionsPayload = {
    batch_id: 'batch-1',
    plan_id: 'plan-1',
    summary: 'Created tasks',
    text_summary: 'Created 2 tasks for moving',
    saved_actions: [
      {
        id: 'act-1',
        title: 'Pack boxes',
        action: 'CREATE_TASK',
        status: 'saved',
        scheduled_at: '2026-10-10T10:00:00Z',
      },
    ],
    duplicate_actions: [
      {
        id: 'act-2',
        title: 'Book truck',
        action: 'CREATE_TASK',
        status: 'duplicate',
      },
    ],
    failed_actions: [],
    timestamp_ms: 100,
  };

  const server = normalizeVoiceGatewayEvent({
    type: 'server.planning.actions',
    session_id: 'session-1',
    planning_receipt: actionsPayload,
  });

  const native = normalizeVoiceGatewayEvent({
    event: 'server.planning.actions',
    sessionId: 'session-1',
    planningActionsJson: JSON.stringify(actionsPayload),
  });

  expect(native).toEqual(server);
  expect(native?.planningReceipt?.savedActions).toHaveLength(1);
  expect(native?.planningReceipt?.savedActions[0].title).toBe('Pack boxes');
  expect(native?.planningReceipt?.duplicateActions).toHaveLength(1);
  expect(native?.planningReceipt?.textSummary).toBe('Created 2 tasks for moving');
});

test('VoiceSocket receives planning actions, updates receipt snapshots, and increments version', async () => {
  const { socket, emit } = harness();
  activeSocket = socket;
  socket.start();
  await socket.connect();

  emit('server.session.ready', {
    planningJson: JSON.stringify(state(1, 'plan')),
  });

  expect(socket.getSnapshot().planningReceipts).toEqual([]);
  expect(socket.getSnapshot().recentPlanningReceipt).toBeNull();
  expect(socket.getSnapshot().planningReceiptVersion).toBe(0);

  const receipt1 = {
    batch_id: 'batch-1',
    saved_actions: [
      { id: 'act-1', title: 'Task 1', status: 'saved' },
    ],
  };

  emit('server.planning.actions', {
    planningActionsJson: JSON.stringify(receipt1),
  });

  let snapshot = socket.getSnapshot();
  expect(snapshot.planningReceiptVersion).toBe(1);
  expect(snapshot.recentPlanningReceipt?.savedActions).toHaveLength(1);
  expect(snapshot.planningReceipts).toHaveLength(1);
  expect(snapshot.planningReceipts?.[0].title).toBe('Task 1');

  // Second receipt appends
  const receipt2 = {
    batch_id: 'batch-2',
    saved_actions: [
      { id: 'act-2', title: 'Task 2', status: 'saved' },
    ],
    duplicate_actions: [
      { id: 'act-3', title: 'Task 3 duplicate', status: 'duplicate' },
    ],
  };

  emit('server.planning.actions', {
    planningActionsJson: JSON.stringify(receipt2),
  });

  snapshot = socket.getSnapshot();
  expect(snapshot.planningReceiptVersion).toBe(2);
  expect(snapshot.recentPlanningReceipt?.savedActions).toHaveLength(1);
  expect(snapshot.recentPlanningReceipt?.duplicateActions).toHaveLength(1);
  expect(snapshot.planningReceipts).toHaveLength(3); // 1 from first + 2 from second

  // Foreign session planning actions are ignored
  emit('server.planning.actions', {
    sessionId: 'foreign-session',
    planningActionsJson: JSON.stringify({
      saved_actions: [{ id: 'act-foreign', title: 'Foreign' }],
    }),
  });
  expect(socket.getSnapshot().planningReceiptVersion).toBe(2);
  expect(socket.getSnapshot().planningReceipts).toHaveLength(3);

  // Connection closed clears recent receipt
  emit('voice.connection.closed');
  expect(socket.getSnapshot().recentPlanningReceipt).toBeNull();
});

test('planning receipt parser rejects oversized or malformed payloads', () => {
  expect(parsePlanningReceipt('x'.repeat(33000))).toBeNull();
  expect(parsePlanningReceipt('not-json')).toBeNull();
  expect(parsePlanningReceipt({})).toBeNull();
  expect(parsePlanningReceipt([])).toBeNull();

  // Array of actions is accepted as fallback
  const arrayPayload = [
    { title: 'Array task', status: 'saved' },
  ];
  const parsedArray = parsePlanningReceipt(arrayPayload);
  expect(parsedArray?.savedActions).toHaveLength(1);
  expect(parsedArray?.savedActions[0].title).toBe('Array task');

  // Action defaults and variations
  const actionWithoutTitle = parsePlanningAction({ id: '1' });
  expect(actionWithoutTitle?.title).toBe('Action');

  const actionWithAltName = parsePlanningAction({ name: 'Alt Name', action: 'UPDATE_TASK' });
  expect(actionWithAltName?.title).toBe('Alt Name');
  expect(actionWithAltName?.status).toBe('updated');
});

test('plan task grouping partitions tasks correctly into chronological buckets', () => {
  const baseDate = new Date('2026-10-06T12:00:00Z');
  const todayStr = '2026-10-06';
  const in7Days = new Date(baseDate.getTime() + 7 * 86400000).toISOString().slice(0, 10);

  const sampleTasks = [
    { id: '1', title: 'Due Today', due_at: '2026-10-06T18:00:00Z', status: 'pending' },
    { id: '2', title: 'Overdue', due_at: '2026-10-01T09:00:00Z', status: 'pending' },
    { id: '3', title: 'Up Next', due_at: '2026-10-10T12:00:00Z', status: 'pending' },
    { id: '4', title: 'Later', due_at: '2026-11-01T12:00:00Z', status: 'pending' },
    { id: '5', title: 'No Deadline', due_at: null, status: 'pending' },
    { id: '6', title: 'Completed Today', due_at: '2026-10-06T10:00:00Z', status: 'completed' },
    { id: '7', title: 'Completed Past', due_at: '2026-10-01T10:00:00Z', status: 'completed' },
  ];

  const completed = sampleTasks.filter(t => t.status === 'completed');
  const active = sampleTasks.filter(t => t.status !== 'completed');

  const todayTasks: any[] = [];
  const upNextTasks: any[] = [];
  const scheduledTasks: any[] = [];
  const undatedTasks: any[] = [];

  for (const t of active) {
    if (!t.due_at) {
      undatedTasks.push(t);
    } else {
      const dueDay = t.due_at.slice(0, 10);
      if (dueDay <= todayStr) {
        todayTasks.push(t);
      } else if (dueDay <= in7Days) {
        upNextTasks.push(t);
      } else {
        scheduledTasks.push(t);
      }
    }
  }

  expect(todayTasks).toHaveLength(2); // 'Due Today' and 'Overdue'
  expect(upNextTasks).toHaveLength(1); // 'Up Next'
  expect(scheduledTasks).toHaveLength(1); // 'Later'
  expect(undatedTasks).toHaveLength(1); // 'No Deadline'
  expect(completed).toHaveLength(2); // 'Completed Today', 'Completed Past'

  const progressPercent = Math.round((completed.length / sampleTasks.length) * 100);
  expect(progressPercent).toBe(29); // 2 / 7 = 28.57% -> 29%
});

