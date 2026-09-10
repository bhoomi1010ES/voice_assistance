import React from 'react';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { AuthController } from '../src/auth/AuthController';
import { AuthProvider } from '../src/auth/AuthProvider';
import { AuthTokenStorage } from '../src/auth/secureStorage';
import { MemoryScreen } from '../src/screens/MemoryScreen';
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

const memory = {
  id: 'memory-1',
  content: 'I prefer green tea.',
  metadata: null,
  memory_type: 'preference' as const,
  subject: 'user',
  predicate: 'beverage',
  object_json: { value: 'green tea' },
  confidence: 1,
  salience: 0.8,
  supersedes_id: null,
  status: 'active' as const,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function createVoiceSocket(sessionId: string | null = null): VoiceSocket {
  const snapshot = {
    connection: 'connected' as const,
    session: sessionId ? ('ready' as const) : ('idle' as const),
    turn: 'idle' as const,
    heartbeat: 'healthy' as const,
    sessionId,
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

async function renderMemory(
  fetchImpl: jest.Mock,
  sessionId: string | null = null,
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
          <VoiceSocketProvider socket={createVoiceSocket(sessionId)}>
            <MemoryScreen />
          </VoiceSocketProvider>
        </AuthProvider>
      </TestProviders>,
    );
    await Promise.resolve();
  });

  await act(async () => {
    await controller.login('user@example.com', 'test-password');
    await Promise.resolve();
    await Promise.resolve();
  });

  return { controller, renderer: renderer! };
}

test('memory UI reviews, edits, deletes, and excludes the active voice session', async () => {
  const updated = { ...memory, content: 'I prefer jasmine tea.' };
  const session = { id: 'voice-session-1' };
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(
      response(200, {
        enabled: true,
        timezone: 'UTC',
        locale: 'en',
        version: 0,
      }),
    )
    .mockResolvedValueOnce(response(200, [memory]))
    .mockResolvedValueOnce(
      response(200, {
        id: session.id,
        client_metadata: { memory_excluded: false },
      }),
    )
    .mockResolvedValueOnce(response(200, updated))
    .mockResolvedValueOnce(response(204, null))
    .mockResolvedValueOnce(response(204, null));

  const { renderer } = await renderMemory(fetchImpl, session.id);
  expect(
    renderer.root.findByProps({ testID: 'memory-item-content' }).props.children,
  ).toBe(memory.content);

  await act(async () => {
    renderer.root.findByProps({ testID: 'memory-view' }).props.onPress();
  });
  await act(async () => {
    renderer.root.findByProps({ testID: 'memory-edit' }).props.onPress();
  });
  await act(async () => {
    renderer.root
      .findByProps({ testID: 'memory-edit-input' })
      .props.onChangeText(updated.content);
    await renderer.root.findByProps({ testID: 'memory-save' }).props.onPress();
  });
  expect(fetchImpl).toHaveBeenCalledWith(
    expect.stringContaining('/memories/memory-1'),
    expect.objectContaining({ method: 'PATCH' }),
  );

  await act(async () => {
    renderer.root
      .findByProps({ testID: 'memory-session-exclusion' })
      .props.onPress();
  });
  expect(fetchImpl).toHaveBeenCalledWith(
    expect.stringContaining('/sessions/voice-session-1/memory-exclusion'),
    expect.objectContaining({ method: 'PUT' }),
  );

  await act(async () => {
    renderer.root.findByProps({ testID: 'memory-delete' }).props.onPress();
  });
  expect(
    renderer.root.findByProps({ testID: 'memory-confirmation' }),
  ).toBeTruthy();
  await act(async () => {
    await renderer.root
      .findByProps({ testID: 'memory-confirm-delete' })
      .props.onPress();
  });
  expect(renderer.root.findByProps({ testID: 'memory-empty' })).toBeTruthy();
  await act(async () => {
    renderer.unmount();
  });
});

test('disabled memory keeps review/delete available and prevents search', async () => {
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(
      response(200, {
        enabled: false,
        timezone: 'UTC',
        locale: 'en',
        version: 1,
      }),
    )
    .mockResolvedValueOnce(response(200, [memory]));
  const { renderer } = await renderMemory(fetchImpl);

  expect(
    renderer.root.findByProps({ testID: 'memory-search' }).props.disabled,
  ).toBe(true);
  expect(
    renderer.root.findByProps({ testID: 'memory-toggle' }).props.disabled,
  ).toBe(false);
  expect(
    renderer.root.findByProps({ testID: 'memory-item-content' }),
  ).toBeTruthy();
  await act(async () => {
    renderer.unmount();
  });
});

test('enabled memory can be explicitly saved from the memory screen', async () => {
  const saved = { ...memory, id: 'memory-2', content: 'I live in Mumbai.' };
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(
      response(200, {
        enabled: true,
        timezone: 'UTC',
        locale: 'en',
        version: 0,
      }),
    )
    .mockResolvedValueOnce(response(200, []))
    .mockResolvedValueOnce(response(201, saved));
  const { renderer } = await renderMemory(fetchImpl);

  await act(async () => {
    renderer.root
      .findByProps({ testID: 'memory-create-input' })
      .props.onChangeText(saved.content);
  });
  await act(async () => {
    await renderer.root
      .findByProps({ testID: 'memory-create' })
      .props.onPress();
  });

  expect(fetchImpl).toHaveBeenCalledWith(
    expect.stringContaining('/memories'),
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ content: saved.content, memory_type: 'fact' }),
    }),
  );
  expect(
    renderer.root.findByProps({ testID: 'memory-item-content' }).props.children,
  ).toBe(saved.content);
  await act(async () => {
    renderer.unmount();
  });
});
