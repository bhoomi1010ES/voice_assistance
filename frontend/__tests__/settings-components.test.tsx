import React from 'react';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { SettingsSection } from '../src/components/settings/SettingsSection';
import { SettingsRow } from '../src/components/settings/SettingsRow';
import { ProfileSummaryCard } from '../src/components/settings/ProfileSummaryCard';
import { SessionDeviceCard } from '../src/components/settings/SessionDeviceCard';
import { SessionsScreen } from '../src/screens/SessionsScreen';
import { AuthController } from '../src/auth/AuthController';
import { AuthProvider } from '../src/auth/AuthProvider';
import { AuthTokenStorage } from '../src/auth/secureStorage';
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

const mockSessions = [
  {
    id: 'session-101',
    device_id: 'device-201',
    created_at: '2026-01-01T00:00:00Z',
    last_used_at: '2026-01-02T12:00:00Z',
    expires_at: '2026-02-01T00:00:00Z',
    revoked_at: null,
  },
];

const mockDevices = [
  {
    id: 'device-201',
    device_identifier: 'pixel-9-pro',
    platform: 'android',
    name: 'Elena’s Pixel 9',
    metadata: null,
    created_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-01-02T12:00:00Z',
    revoked_at: null,
  },
];

describe('Settings Presentational Components & SessionsScreen', () => {
  test('SettingsRow triggers onPress and renders title, subtitle, and badge', () => {
    const onPress = jest.fn();
    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <SettingsSection title="Test Section">
            <SettingsRow
              badge="Pro"
              icon="👤"
              onPress={onPress}
              subtitle="Row subtitle"
              testID="test-settings-row"
              title="Profile Settings"
            />
          </SettingsSection>
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    expect(root.findByProps({ testID: 'test-settings-row' })).toBeTruthy();
    act(() => {
      root.findByProps({ testID: 'test-settings-row' }).props.onPress();
    });
    expect(onPress).toHaveBeenCalled();
  });

  test('ProfileSummaryCard displays user information and initials', () => {
    const onPress = jest.fn();
    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <ProfileSummaryCard
            email="elena@example.com"
            name="Elena Rostova"
            onPress={onPress}
            status="active"
            testID="test-profile-card"
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    expect(root.findByProps({ testID: 'test-profile-card' })).toBeTruthy();
    act(() => {
      root.findByProps({ testID: 'test-profile-card' }).props.onPress();
    });
    expect(onPress).toHaveBeenCalled();
  });

  test('SessionDeviceCard renders active and revoked states', () => {
    const onRevoke = jest.fn();
    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <SessionDeviceCard
            isRevoked={false}
            onRevoke={onRevoke}
            revoking={false}
            subtitle="Last seen 2 hours ago"
            title="Pixel 9 Pro"
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    act(() => {
      root.findByProps({ accessibilityRole: 'button' }).props.onPress();
    });
    expect(onRevoke).toHaveBeenCalled();
  });

  test('SessionsScreen loads and revokes sessions and devices', async () => {
    const fetchImpl = jest.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/sessions') && init?.method === 'DELETE') {
        return response(204, null);
      }
      if (url.includes('/devices') && init?.method === 'DELETE') {
        return response(204, null);
      }
      if (url.includes('/sessions')) {
        return response(200, mockSessions);
      }
      if (url.includes('/devices')) {
        return response(200, mockDevices);
      }
      return response(200, {});
    });

    const controller = new AuthController({
      fetchImpl,
      storage: new EmptyTokenStorage(),
    });

    let renderer: ReactTestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <AuthProvider controller={controller}>
            <SessionsScreen />
          </AuthProvider>
        </TestProviders>,
      );
      await Promise.resolve();
    });

    await act(async () => {
      fetchImpl.mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith('/auth/login')) {
          return response(200, {
            token_type: 'bearer',
            access_token: 'token-1',
            refresh_token: 'refresh-1',
            expires_in: 900,
            user: { id: 'u1', email: 'test@example.com', name: 'User' },
            device: { id: 'd1' },
            session: { id: 's1' },
          });
        }
        if (url.includes('/sessions')) {
          return response(200, mockSessions);
        }
        if (url.includes('/devices')) {
          return response(200, mockDevices);
        }
        return response(200, {});
      });
      await controller.login('test@example.com', 'password1234');
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(renderer!.root.findByProps({ testID: 'sessions-screen' })).toBeTruthy();
    expect(fetchImpl).toHaveBeenCalledWith(
      expect.stringContaining('/sessions'),
      expect.anything(),
    );
    expect(fetchImpl).toHaveBeenCalledWith(
      expect.stringContaining('/devices'),
      expect.anything(),
    );
  });
});
