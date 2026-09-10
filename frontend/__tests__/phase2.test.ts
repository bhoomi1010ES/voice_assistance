import { AuthController } from '../src/auth/AuthController';
import { AuthTokenStorage } from '../src/auth/secureStorage';
import { AuthTokens } from '../src/auth/types';

const profile = {
  id: 'user-1',
  email: 'user@example.com',
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

class TokenStorage implements AuthTokenStorage {
  tokens: AuthTokens | null = null;
  clearCalls = 0;

  async read() {
    return this.tokens;
  }

  async save(tokens: AuthTokens) {
    this.tokens = tokens;
  }

  async clear() {
    this.tokens = null;
    this.clearCalls += 1;
  }
}

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

test('login stores only access and refresh tokens and authenticates the user', async () => {
  const storage = new TokenStorage();
  const fetchImpl = jest.fn().mockResolvedValue(response(200, tokenResponse));
  const controller = new AuthController({ storage, fetchImpl });

  await controller.login('user@example.com', 'not-persisted-password');

  expect(storage.tokens).toEqual({
    accessToken: 'access-1',
    refreshToken: 'refresh-1',
  });
  expect(controller.getState()).toMatchObject({
    status: 'authenticated',
    profile,
  });
  expect(JSON.stringify(storage.tokens)).not.toContain(
    'not-persisted-password',
  );
});

test('valid stored refresh session restores before protected UI is selected', async () => {
  const storage = new TokenStorage();
  storage.tokens = {
    accessToken: 'stored-access',
    refreshToken: 'stored-refresh',
  };
  const fetchImpl = jest.fn().mockResolvedValue(response(200, profile));
  const controller = new AuthController({ storage, fetchImpl });

  await controller.restore();

  expect(controller.getState()).toMatchObject({
    status: 'authenticated',
    profile,
  });
  expect(fetchImpl.mock.calls[0][0]).toMatch(/\/auth\/me$/);
  const requestInit = fetchImpl.mock.calls[0][1] as RequestInit;
  expect((requestInit.headers as Headers).get('Authorization')).toBe(
    'Bearer stored-access',
  );
});

test('concurrent 401 responses share one refresh and retry once', async () => {
  const storage = new TokenStorage();
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(response(401, { detail: { code: 'EXPIRED' } }))
    .mockResolvedValueOnce(response(401, { detail: { code: 'EXPIRED' } }))
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(response(200, { value: 'first' }))
    .mockResolvedValueOnce(response(200, { value: 'second' }));
  const controller = new AuthController({ storage, fetchImpl });
  await controller.login('user@example.com', 'password');

  const results = await Promise.all([
    controller.request<{ value: string }>('/safe-one'),
    controller.request<{ value: string }>('/safe-two'),
  ]);

  expect(results).toEqual([{ value: 'first' }, { value: 'second' }]);
  expect(
    fetchImpl.mock.calls.filter(([url]) =>
      String(url).endsWith('/auth/refresh'),
    ),
  ).toHaveLength(1);
  expect(storage.tokens).toEqual({
    accessToken: 'access-1',
    refreshToken: 'refresh-1',
  });
});

test('rejected refresh clears secure state and presents unauthenticated state once', async () => {
  const storage = new TokenStorage();
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(response(401, { detail: { code: 'EXPIRED' } }))
    .mockResolvedValueOnce(
      response(401, { detail: { code: 'AUTHENTICATION_FAILED' } }),
    );
  const controller = new AuthController({ storage, fetchImpl });
  await controller.login('user@example.com', 'password');

  await expect(controller.request('/protected')).rejects.toMatchObject({
    status: 401,
  });
  expect(storage.tokens).toBeNull();
  expect(storage.clearCalls).toBe(1);
  expect(controller.getState()).toMatchObject({
    status: 'unauthenticated',
    sessionExpired: true,
  });
});

test('logout stops active voice resources before clearing local auth state', async () => {
  const storage = new TokenStorage();
  const stopVoiceResources = jest.fn().mockResolvedValue(undefined);
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(response(200, tokenResponse))
    .mockResolvedValueOnce(response(200, { status: 'logged_out' }));
  const controller = new AuthController({
    storage,
    fetchImpl,
    stopVoiceResources,
  });
  await controller.login('user@example.com', 'password');

  await controller.logout();

  expect(stopVoiceResources).toHaveBeenCalledTimes(1);
  expect(storage.tokens).toBeNull();
  expect(controller.getState().status).toBe('unauthenticated');
});
