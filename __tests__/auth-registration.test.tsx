import ReactTestRenderer, { act } from 'react-test-renderer';
import { AuthController } from '../src/auth/AuthController';
import { registerRequest } from '../src/auth/authApi';
import { AuthProvider } from '../src/auth/AuthProvider';
import { AuthTokenStorage } from '../src/auth/secureStorage';
import { AuthNavigator } from '../src/navigation/AuthNavigator';
import { TestProviders } from '../src/testing/TestProviders';

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

test('registration request includes the display name and credentials', async () => {
  const fetchImpl = jest
    .fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    .mockResolvedValue(
      response(201, {
        id: 'user-1',
        name: 'Test User',
        email: 'test@example.com',
        status: 'active',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      }),
    );

  await registerRequest(
    {
      name: 'Test User',
      email: 'test@example.com',
      password: 'a-strong-test-password',
    },
    fetchImpl,
  );

  expect(fetchImpl).toHaveBeenCalledWith(
    expect.stringContaining('/auth/register'),
    expect.objectContaining({
      body: JSON.stringify({
        name: 'Test User',
        email: 'test@example.com',
        password: 'a-strong-test-password',
      }),
    }),
  );
});

test('auth navigator switches between sign-in and registration screens', async () => {
  const controller = new AuthController({
    storage: new EmptyTokenStorage(),
    fetchImpl: jest.fn(),
  });
  let renderer: ReactTestRenderer.ReactTestRenderer;

  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <AuthProvider controller={controller}>
          <AuthNavigator
            sessionExpired={false}
            onDismissSessionExpired={() => undefined}
          />
        </AuthProvider>
      </TestProviders>,
    );
  });

  expect(renderer!.root.findByProps({ testID: 'auth-screen' })).toBeTruthy();

  await act(async () => {
    renderer!.root
      .findByProps({ testID: 'create-account-link' })
      .props.onPress();
  });
  expect(
    renderer!.root.findByProps({ testID: 'register-screen' }),
  ).toBeTruthy();

  await act(async () => {
    renderer!.root.findByProps({ testID: 'sign-in-link' }).props.onPress();
  });
  expect(renderer!.root.findByProps({ testID: 'auth-screen' })).toBeTruthy();
});
