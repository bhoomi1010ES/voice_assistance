import ReactTestRenderer, { act } from 'react-test-renderer';
import { AuthController } from '../src/auth/AuthController';
import { AuthProvider } from '../src/auth/AuthProvider';
import { AuthTokenStorage } from '../src/auth/secureStorage';
import { AccountScreen } from '../src/screens/AccountScreen';
import { MainNavigator } from '../src/navigation/MainNavigator';
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

function createVoiceSocket(): VoiceSocket {
  const snapshot = {
    connection: 'disconnected' as const,
    session: 'idle' as const,
    turn: 'idle' as const,
    heartbeat: 'unknown' as const,
    sessionId: null,
    turnId: null,
    responseId: null,
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
  } as unknown as VoiceSocket;
}

async function renderAuthenticated(
  fetchImpl: jest.Mock,
  child: React.ReactNode,
  socket?: VoiceSocket,
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
          <VoiceSocketProvider socket={socket}>{child}</VoiceSocketProvider>
        </AuthProvider>
      </TestProviders>,
    );
    await Promise.resolve();
  });

  await act(async () => {
    await controller.login('user@example.com', 'test-password');
  });

  return { controller, renderer: renderer! };
}

test('overflow menu opens settings, profile, and sign out actions', async () => {
  const fetchImpl = jest.fn().mockResolvedValue(response(200, tokenResponse));
  const { renderer } = await renderAuthenticated(
    fetchImpl,
    <MainNavigator />,
    createVoiceSocket(),
  );

  expect(
    renderer.root.findByProps({ testID: 'assistant-greeting' }).props
      .accessibilityLabel,
  ).toBe('Hello, Test User');

  await act(async () => {
    renderer.root.findByProps({ testID: 'main-menu-button' }).props.onPress();
  });

  expect(renderer.root.findByProps({ testID: 'main-menu' })).toBeTruthy();
  expect(renderer.root.findByProps({ testID: 'menu-settings' })).toBeTruthy();
  expect(renderer.root.findByProps({ testID: 'menu-profile' })).toBeTruthy();
  expect(renderer.root.findByProps({ testID: 'menu-sign-out' })).toBeTruthy();

  await act(async () => {
    renderer.root.findByProps({ testID: 'menu-settings' }).props.onPress();
  });
  expect(renderer.root.findByProps({ testID: 'settings-screen' })).toBeTruthy();

  await act(async () => {
    renderer.root.findByProps({ testID: 'main-menu-button' }).props.onPress();
  });
  await act(async () => {
    renderer.root.findByProps({ testID: 'menu-profile' }).props.onPress();
  });
  expect(renderer.root.findByProps({ testID: 'profile-screen' })).toBeTruthy();
  await act(async () => {
    renderer.unmount();
  });
});

test('profile name can be edited and saved', async () => {
  const updatedProfile = { ...profile, name: 'Updated User' };
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(response(200, updatedProfile));
  const { controller, renderer } = await renderAuthenticated(
    fetchImpl,
    <AccountScreen />,
    createVoiceSocket(),
  );

  await act(async () => {
    renderer.root.findByProps({ testID: 'profile-edit' }).props.onPress();
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'profile-name-input' })
      .props.onChangeText('Updated User');
  });
  await act(async () => {
    await renderer.root.findByProps({ testID: 'profile-save' }).props.onPress();
  });

  expect(controller.getState().profile?.name).toBe('Updated User');
  expect(renderer.root.findByProps({ testID: 'profile-edit' })).toBeTruthy();
  expect(fetchImpl).toHaveBeenLastCalledWith(
    expect.stringContaining('/auth/me'),
    expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ name: 'Updated User' }),
    }),
  );
  await act(async () => {
    renderer.unmount();
  });
});
