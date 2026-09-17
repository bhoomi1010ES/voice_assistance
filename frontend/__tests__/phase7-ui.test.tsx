import ReactTestRenderer, { act } from 'react-test-renderer';
import { AuthController } from '../src/auth/AuthController';
import { AuthProvider } from '../src/auth/AuthProvider';
import { AuthTokenStorage } from '../src/auth/secureStorage';
import { MainNavigator } from '../src/navigation/MainNavigator';
import { AssistantScreen } from '../src/screens/AssistantScreen';
import { TasksScreen } from '../src/screens/TasksScreen';
import { TestProviders } from '../src/testing/TestProviders';
import { VoiceSocket } from '../src/voice/VoiceSocket';
import { VoiceSocketProvider } from '../src/voice/VoiceSocketProvider';

class EmptyTokenStorage implements AuthTokenStorage {
  async read() {
    return null;
  }

  async save() {}

  async clear() {}
}

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

const profile = {
  id: 'user-1',
  email: 'user@example.com',
  name: 'Test User',
  status: 'active',
  timezone: 'Asia/Kolkata',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const tokenResponse = {
  token_type: 'bearer',
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  expires_in: 900,
  user: profile,
  device: {
    id: 'device-1',
    device_identifier: 'voice-assistant-android',
    platform: 'android',
    name: 'Voice Assistant',
    metadata: null,
    created_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-01-01T00:00:00Z',
    revoked_at: null,
  },
  session: {
    id: 'session-1',
    device_id: 'device-1',
    created_at: '2026-01-01T00:00:00Z',
    last_used_at: '2026-01-01T00:00:00Z',
    expires_at: '2026-01-02T00:00:00Z',
    revoked_at: null,
  },
};

const emptyTasks: unknown[] = [];
const emptyReminders: unknown[] = [];

function createVoiceSocket(
  conversationMessages: unknown[] = [],
  resolveConfirmation?: jest.Mock,
): VoiceSocket {
  const snapshot = {
    connection: 'disconnected' as const,
    session: 'idle' as const,
    turn: 'idle' as const,
    heartbeat: 'unknown' as const,
    sessionId: null,
    turnId: null,
    responseId: null,
    ttsPlaybackState: 'idle' as const,
    ttsResponseId: null,
    ttsError: null,
    confirmationAwaitingVoice: conversationMessages.some(
      (message: any) =>
        message.role === 'tool' && message.status === 'confirmation_required',
    ),
    transcriptMessages: [],
    conversationMessages,
    transcriptError: null,
    firstTextAtMs: null,
    conversationRenderCompletedAtMs: null,
    eventSequence: 0,
    lastEvent: null,
    lastEventAtMs: null,
    lastHeartbeatAtMs: null,
    reconnectAttempt: 0,
    droppedEventCount: 0,
    invalidEventCount: 0,
    error: null,
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: (value: typeof snapshot) => void) => {
      listener(snapshot);
      return () => undefined;
    },
    start: jest.fn(),
    connect: jest.fn().mockResolvedValue(undefined),
    stop: jest.fn().mockResolvedValue(undefined),
    dispose: jest.fn().mockResolvedValue(undefined),
    resolveConfirmation,
  } as unknown as VoiceSocket;
}

async function renderAuthenticated(
  fetchImpl: jest.Mock,
  child: React.ReactNode,
) {
  const controller = new AuthController({
    fetchImpl,
    storage: new EmptyTokenStorage(),
  });
  let renderer: ReactTestRenderer.ReactTestRenderer;

  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <AuthProvider controller={controller}>
          <VoiceSocketProvider socket={createVoiceSocket()}>
            {child}
          </VoiceSocketProvider>
        </AuthProvider>
      </TestProviders>,
    );
    await Promise.resolve();
  });

  await act(async () => {
    await controller.login('user@example.com', 'test-password');
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return renderer!;
}

function baseFetch() {
  return jest.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/login')) return response(200, tokenResponse);
    if (url.includes('/tasks')) {
      return response(init?.method === 'POST' ? 201 : 200, emptyTasks);
    }
    if (url.includes('/reminders')) return response(200, emptyReminders);
    return response(200, {});
  });
}

test('Tasks destination exposes upcoming, all, and completed views', async () => {
  const fetchImpl = baseFetch();
  const renderer = await renderAuthenticated(fetchImpl, <MainNavigator />);

  await act(async () => {
    renderer.root.findByProps({ testID: 'tab-tasks' }).props.onPress();
  });

  expect(renderer.root.findByProps({ testID: 'tasks-screen' })).toBeTruthy();
  expect(
    renderer.root.findByProps({ testID: 'tasks-filter-upcoming' }),
  ).toBeTruthy();
  expect(
    renderer.root.findByProps({ testID: 'tasks-filter-all' }),
  ).toBeTruthy();
  expect(
    renderer.root.findByProps({ testID: 'tasks-filter-completed' }),
  ).toBeTruthy();
  await act(async () => renderer.unmount());
});

test('reminder form shows explicit local date, time, and timezone preview', async () => {
  const fetchImpl = baseFetch();
  const renderer = await renderAuthenticated(fetchImpl, <TasksScreen />);

  await act(async () => {
    renderer.root.findByProps({ testID: 'reminders-tab' }).props.onPress();
  });
  await act(async () => {
    renderer.root.findByProps({ testID: 'reminder-create' }).props.onPress();
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-date-input' })
      .props.onChangeText('2030-05-10');
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-time-input' })
      .props.onChangeText('09:00');
  });

  expect(
    renderer.root.findByProps({ testID: 'reminder-schedule-preview' }).props
      .children,
  ).toContain('2030-05-10 at 09:00 (Asia/Kolkata)');
  await act(async () => renderer.unmount());
});

test('double-tap create sends one mutation and renders only the committed reminder', async () => {
  const reminder = {
    id: 'reminder-1',
    task_id: null,
    title: 'Call Rahul',
    body: null,
    trigger_at: '2030-05-10T03:30:00Z',
    timezone: 'Asia/Kolkata',
    recurrence_rule: null,
    status: 'scheduled',
    delivery_channel: 'push',
    created_at: '2030-01-01T00:00:00Z',
    updated_at: '2030-01-01T00:00:00Z',
    sent_at: null,
    failure_code: null,
  };
  const fetchImpl = baseFetch();
  fetchImpl.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/login')) return response(200, tokenResponse);
    if (url.includes('/reminders') && init?.method === 'POST') {
      return response(201, reminder);
    }
    if (url.includes('/reminders')) return response(200, emptyReminders);
    if (url.includes('/tasks')) return response(200, emptyTasks);
    return response(200, {});
  });
  const renderer = await renderAuthenticated(fetchImpl, <TasksScreen />);

  await act(async () => {
    renderer.root.findByProps({ testID: 'reminders-tab' }).props.onPress();
  });
  await act(async () => {
    renderer.root.findByProps({ testID: 'reminder-create' }).props.onPress();
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-title-input' })
      .props.onChangeText('Call Rahul');
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-date-input' })
      .props.onChangeText('2030-05-10');
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-time-input' })
      .props.onChangeText('09:00');
  });

  await act(async () => {
    const save = renderer.root.findByProps({ testID: 'reminder-save' }).props
      .onPress;
    await Promise.all([save(), save()]);
  });

  const reminderPosts = fetchImpl.mock.calls.filter(
    ([url, init]) => url.includes('/reminders') && init?.method === 'POST',
  );
  expect(reminderPosts).toHaveLength(1);
  expect(
    renderer.root.findByProps({ testID: 'reminder-reminder-1' }).props,
  ).toBeDefined();
  await act(async () => renderer.unmount());
});

test('temporal clarification does not render a success acknowledgement', async () => {
  const fetchImpl = baseFetch();
  fetchImpl.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/login')) return response(200, tokenResponse);
    if (url.includes('/reminders') && init?.method === 'POST') {
      return response(422, {
        detail: { code: 'TEMPORAL_RESOLUTION_REQUIRED' },
      });
    }
    if (url.includes('/reminders')) return response(200, emptyReminders);
    if (url.includes('/tasks')) return response(200, emptyTasks);
    return response(200, {});
  });
  const renderer = await renderAuthenticated(fetchImpl, <TasksScreen />);

  await act(async () => {
    renderer.root.findByProps({ testID: 'reminders-tab' }).props.onPress();
  });
  await act(async () => {
    renderer.root.findByProps({ testID: 'reminder-create' }).props.onPress();
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-title-input' })
      .props.onChangeText('Ambiguous DST time');
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-date-input' })
      .props.onChangeText('2030-11-03');
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-time-input' })
      .props.onChangeText('01:30');
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'reminder-timezone-input' })
      .props.onChangeText('America/New_York');
  });
  await act(async () => {
    await renderer.root
      .findByProps({ testID: 'reminder-save' })
      .props.onPress();
  });

  expect(renderer.root.findAllByProps({ testID: 'reminder-1' })).toHaveLength(
    0,
  );
  expect(
    renderer.root.findByProps({ accessibilityRole: 'alert' }),
  ).toBeTruthy();
  await act(async () => renderer.unmount());
});

test('assistant confirmation is voice-only and renders no approval controls', async () => {
  const pendingTool = {
    id: 'tool-call-1',
    role: 'tool' as const,
    turnId: 'turn-1',
    responseId: 'response-1',
    toolCallId: 'call-1',
    name: 'create_reminder',
    status: 'confirmation_required' as const,
    result: null,
    confirmationId: 'confirmation-1',
    errorCode: null,
  };
  const fetchImpl = jest.fn().mockResolvedValue(response(200, tokenResponse));
  const controller = new AuthController({
    fetchImpl,
    storage: new EmptyTokenStorage(),
  });
  const socket = createVoiceSocket([pendingTool]);
  let renderer: ReactTestRenderer.ReactTestRenderer;

  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <AuthProvider controller={controller}>
          <VoiceSocketProvider socket={socket}>
            <AssistantScreen />
          </VoiceSocketProvider>
        </AuthProvider>
      </TestProviders>,
    );
    await controller.login('user@example.com', 'test-password');
  });

  expect(
    renderer!.root.findByProps({ testID: 'voice-confirmation-status' }),
  ).toBeTruthy();
  expect(
    renderer!.root.findAllByProps({ testID: 'tool-confirm-approve' }),
  ).toHaveLength(0);
  expect(
    renderer!.root.findAllByProps({ testID: 'tool-confirm-deny' }),
  ).toHaveLength(0);
  await act(async () => renderer!.unmount());
});
